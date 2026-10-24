"""
team/ledger.py — our own books, built only from what the exchange tells us.

Same signed average-cost rule as the exchange (engine/exchange/server.py,
Portfolio._book_signed_fill), so the two reconcile line by line:
- a fill in the same direction extends the position at a volume-weighted entry
- a fill in the opposite direction realises (price − entry) × closed × sign(old)
  on the part that closes; a flip through zero re-opens the rest at the fill price

Two kinds of change reach the books:
- trade-driven (fills): position, entry, realized P&L, fees, cash — booked by us
  and reconciled strictly against the exchange
- exchange-side, with no trade print: starting-share grants, IPO allocations,
  dividends, and the per-tick interest on idle cash / margin carry that the
  exchange accrues without any message — booked as they show up, and the cash
  part kept in `accruals` so it is reported, not hidden
"""

from __future__ import annotations

import json
import pathlib
from dataclasses import asdict, dataclass, field

CASH_TOL = 1.00     # cash / realized P&L / fees may differ by $1 (rounding)
AVG_TOL = 0.01      # average entry may differ by 1 cent (the leaderboard rounds it)


@dataclass
class Ledger:
    """Position, average entry, realized P&L, fees and non-trade cash flows."""

    cash: float = 0.0
    positions: dict[str, int] = field(default_factory=dict)
    avg_entry: dict[str, float] = field(default_factory=dict)
    realized_pnl: float = 0.0
    fees: float = 0.0           # fees paid minus rebates earned
    accruals: float = 0.0       # non-trade cash: interest − carry + dividends

    # ── Trade-driven ─────────────────────────────────────────────────────

    def apply_fill(self, side: str, symbol: str, qty: int, price: float,
                   fee: float = 0.0) -> None:
        """One TradeExecution of ours. fee > 0 = paid, < 0 = rebate."""
        signed = qty if side == "buy" else -qty
        old = self.positions.get(symbol, 0)
        entry = self.avg_entry.get(symbol, price)
        if old == 0 or (old > 0) == (signed > 0):
            self.avg_entry[symbol] = (abs(old) * entry + qty * price) / (abs(old) + qty)
        else:
            closed = min(abs(old), qty)
            self.realized_pnl += (price - entry) * closed * (1 if old > 0 else -1)
            if qty > abs(old):
                self.avg_entry[symbol] = price          # flipped through zero
        self._set(symbol, old + signed)
        self.cash -= signed * price + fee
        self.fees += fee

    # ── Exchange-side (no trade print) ───────────────────────────────────

    def apply_allocation(self, symbol: str, qty: int, price: float) -> None:
        """IPO allocation: the exchange books it at the offer."""
        self._set(symbol, self.positions.get(symbol, 0) + qty)
        self.avg_entry[symbol] = price
        self.cash -= qty * price

    def apply_grant(self, symbol: str, qty: int, mark: float) -> None:
        """Starting-share grant: free shares; an existing position keeps its entry,
        a new one enters at the mark (exactly the exchange's rule)."""
        if not self.positions.get(symbol):
            self.avg_entry[symbol] = mark
        self._set(symbol, self.positions.get(symbol, 0) + qty)

    def apply_dividend(self, symbol: str, per_share: float) -> None:
        """Cash to holders; shorts pay it."""
        self.accrue(per_share * self.positions.get(symbol, 0))

    def accrue(self, amount: float) -> None:
        """A non-trade cash flow (interest on idle cash, margin carry, dividend)."""
        self.cash += amount
        self.accruals += amount

    # ── Exchange snapshots ───────────────────────────────────────────────

    def sync_from(self, cash: float, positions: dict[str, int], realized_pnl: float,
                  marks: dict[str, float], avg_cost: dict[str, float] | None = None,
                  fees: float | None = None) -> list[str]:
        """Adopt the exchange's numbers after a (re)connect. Returns what differed.

        Entry prices come from the exchange when it sends them (the leaderboard
        does, a PortfolioUpdate does not). Without them, a position on the same
        side keeps our entry and a new or flipped one is approximated by the mark.
        """
        diffs = self.reconcile(cash, positions, realized_pnl, avg_cost, fees)
        self.cash, self.realized_pnl = cash, realized_pnl
        if fees is not None:
            self.fees = fees
        for s in set(self.positions) | set(positions):
            q, old = positions.get(s, 0), self.positions.get(s, 0)
            if q and avg_cost and avg_cost.get(s):
                self.avg_entry[s] = avg_cost[s]
            elif q and (old * q <= 0 or s not in self.avg_entry):
                self.avg_entry[s] = marks.get(s, 0.0)
            self._set(s, q)
        return diffs

    def seed_missing_entries(self, marks: dict[str, float]) -> None:
        """Positions adopted before any book arrived get the first mark as entry."""
        for s in self.positions:
            if not self.avg_entry.get(s) and marks.get(s):
                self.avg_entry[s] = marks[s]

    # ── Queries ──────────────────────────────────────────────────────────

    def unrealized_pnl(self, marks: dict[str, float]) -> float:
        """Marked to the exchange's ref prices, exactly as the exchange does."""
        return sum((marks.get(s, self.avg_entry[s]) - self.avg_entry[s]) * q
                   for s, q in self.positions.items() if s in self.avg_entry)

    def reconcile(self, cash: float, positions: dict[str, int], realized_pnl: float,
                  avg_cost: dict[str, float] | None = None,
                  fees: float | None = None) -> list[str]:
        """Differences vs the exchange's numbers. Empty list = the books agree."""
        out = []
        held = {s for s, q in positions.items() if q}
        for s in sorted(set(self.positions) | held):
            mine, theirs = self.positions.get(s, 0), positions.get(s, 0)
            if mine != theirs:
                out.append(f"{s}: ours {mine} vs exchange {theirs}")
        for s, theirs in sorted((avg_cost or {}).items()):
            mine = self.avg_entry.get(s, 0.0)
            if s in self.positions and abs(mine - theirs) > AVG_TOL:
                out.append(f"{s} entry: ours {mine:.4f} vs exchange {theirs:.4f}")
        if abs(self.cash - cash) > CASH_TOL:
            out.append(f"cash: ours {self.cash:,.2f} vs exchange {cash:,.2f}")
        if abs(self.realized_pnl - realized_pnl) > CASH_TOL:
            out.append(f"realized: ours {self.realized_pnl:+,.2f} "
                       f"vs exchange {realized_pnl:+,.2f}")
        if fees is not None and abs(self.fees - fees) > CASH_TOL:
            out.append(f"fees: ours {self.fees:,.2f} vs exchange {fees:,.2f}")
        return out

    def summary(self, marks: dict[str, float]) -> str:
        """One line: cash and the P&L totals."""
        return (f"cash {self.cash:,.2f} · realized {self.realized_pnl:+,.2f} · "
                f"unrealized {self.unrealized_pnl(marks):+,.2f} · fees {self.fees:,.2f} · "
                f"interest/carry/dividends {self.accruals:+,.2f}")

    def position_line(self, symbol: str, marks: dict[str, float]) -> str:
        """One symbol: quantity, entry, mark and its unrealized P&L."""
        q = self.positions.get(symbol, 0)
        if not q:
            return f"{symbol} flat"
        entry = self.avg_entry.get(symbol, 0.0)
        mark = marks.get(symbol, entry)
        return f"{symbol} {q:+d} @ {entry:.2f} (mark {mark:.2f}, unrealized {(mark - entry) * q:+,.2f})"

    def table(self, marks: dict[str, float]) -> str:
        """Multi-line books: one aligned row per position, then the totals."""
        lines = [f"    {'SYMBOL':<7}{'QTY':>7}{'ENTRY':>11}{'MARK':>11}{'UNREAL':>12}"]
        for s, q in sorted(self.positions.items()):
            entry = self.avg_entry.get(s, 0.0)
            mark = marks.get(s, entry)
            lines.append(f"    {s:<7}{q:>+7d}{entry:>11.2f}{mark:>11.2f}{(mark - entry) * q:>+12,.2f}")
        if not self.positions:
            lines.append("    (flat)")
        lines.append("    " + self.summary(marks))
        return "\n".join(lines)

    def _set(self, symbol: str, qty: int) -> None:
        if qty:
            self.positions[symbol] = qty
        else:
            self.positions.pop(symbol, None)
            self.avg_entry.pop(symbol, None)

    # ── Persistence: entry prices survive a process restart ──────────────

    def save(self, path: pathlib.Path) -> None:
        """Write the books to JSON (team/state/<seat>.json)."""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self)))

    @classmethod
    def load(cls, path: pathlib.Path) -> "Ledger":
        """Books from a previous run, or empty books if there are none."""
        try:
            return cls(**json.loads(path.read_text()))
        except (OSError, ValueError, TypeError):
            return cls()
