"""Event2: public-direction long/short event trader.

FROZEN defaults (selected on dev2 seeds 3001-3060 only, see research/PROGRESS.md E7/E8): gross_cap 105000,
cash_reserve 0.35, ev_notional 0.30, ev_maxsyms 6, other parameters as in the earlier validated research run.
These relax two conservative capital limits of the deployed baseline (70000 / 0.60) and are a risk-appetite decision.

Selected with TRADER_STRATEGY=event2 (default: the corrected baseline event
strategy). Uses only public information: the fired CALENDAR_EVENT already
carries the signed move (pct), ramp length and overshoot; entries are
marketable IOCs capped by displayed depth, so nothing here reads simulator
internals. Order-level safety (price band, caps, cash, SSR, shorts gate,
reconciliation) is enforced again at the final outbound boundary in
`team.trader._Bot._place_order`.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, fields, replace

from arena import Signal
from week2.week02_alpha_bot import _depth

NON_SHARE_SYMBOLS = frozenset(
    x.strip() for x in os.environ.get("FUTURES", "ARENA10").split(",") if x.strip())


@dataclass(frozen=True)
class Event2Config:
    cap_shares: int = 150       # per-symbol cap enforced at the outbound boundary (seat cap)
    gross_cap: float = 105000.  # gross notional cap at the outbound boundary (deployed baseline: 70000; includes ~59k endowed inventory)
    cash_reserve: float = 0.35  # fraction of starting equity kept as cash on the buy path (deployed baseline: 0.60)
    exposure: float = 1.0       # scales ev_notional only (caps are absolute)
    enable_short: bool = True
    ev_notional: float = 0.30   # fraction of net worth per single-name event (split on market-wide)
    ev_entry: float = 0.5       # entry limit pre*(1+sign*ev_entry*|pct|)
    ev_take: float = 0.9        # exit at pre*(1+sign*ev_take*overshoot*|pct|)
    ev_time: float = 3.0        # time stop, multiples of ramp length
    ev_stop: float = 0.5        # stop at entry*(1-sign*ev_stop*|pct|)
    ev_window: float = 0.5      # stop adding after ev_window*ramp
    ev_chunk: int = 60          # max shares per IOC
    ev_kill: float = 0.03       # no new entries after this drawdown from equity peak
    ev_min_edge: float = 1.0    # designed edge must exceed this x round-trip fees
    ev_delay: float = 0.0
    ev_maxsyms: int = 6

    @classmethod
    def from_env(cls) -> "Event2Config":
        """EVENT2_<FIELD>=value overrides, e.g. EVENT2_CAP_SHARES=150."""
        kw = {}
        for f in fields(cls):
            raw = os.environ.get("EVENT2_" + f.name.upper())
            if raw is not None:
                kw[f.name] = (raw.lower() in ("1", "true", "yes")) if f.type in (bool, "bool") else type(getattr(cls, f.name))(raw)
        return cls(**kw)


class EvPlan:
    __slots__ = ("sym", "sign", "pre", "pct", "ov", "t0", "ramp_s", "pos0", "target",
                 "state", "entry_px")

    def __init__(self, sym, sign, pre, pct, ov, t0, ramp_s, pos0):
        self.sym, self.sign, self.pre, self.pct, self.ov = sym, sign, pre, pct, ov
        self.t0, self.ramp_s, self.pos0 = t0, ramp_s, pos0
        self.target = 0
        self.state = "enter"
        self.entry_px = 0.0


class Event2Engine:
    """Strategy logic. `owner` supplies: now(), taker, _mids, _halted_symbols,
    _shorts_allowed, _ssr, _bot (optional), ramp_ticks, overshoot."""

    def __init__(self, owner, cfg: Event2Config):
        self.owner, self.cfg = owner, cfg
        self.ev: dict[str, EvPlan] = {}
        self.peak = 0.0
        self.rel_spread: dict[str, float] = {}
        self.stats = {"events": 0, "skipped_edge": 0, "plans": 0}

    # -- lifecycle ----------------------------------------------------------
    def reset(self):
        self.ev.clear()

    # -- events -------------------------------------------------------------
    def on_calendar(self, data: dict, portfolio, market) -> None:
        cfg, o = self.cfg, self.owner
        pct = float(data.get("pct") or 0.0)
        if not pct or abs(pct) > 0.25:
            return
        ramp_s = max(1, int(data.get("ramp_ticks") or 15)) * 1.0
        ov = float(data.get("overshoot") or 1.0)
        syms = [x for x in (data.get("affected") or []) if x not in NON_SHARE_SYMBOLS and o._mids.get(x)]
        if not syms:
            return
        self.stats["events"] += 1
        cost = 2 * max(o.taker, 0.0015) * cfg.ev_min_edge
        if (cfg.ev_take * ov - cfg.ev_entry) * abs(pct) <= cost:
            self.stats["skipped_edge"] += 1
            return
        if len(syms) > cfg.ev_maxsyms:
            syms.sort(key=lambda x: self.rel_spread.get(x, 1.0))
            syms = syms[:cfg.ev_maxsyms]
        nw = portfolio.net_worth(market) if (portfolio is not None and market is not None) else 175000.0
        budget = cfg.ev_notional * nw * cfg.exposure / max(1, len(syms)) * (len(syms) if len(syms) == 1 else 1)
        for sym in syms:
            pre = o._mids[sym]
            pos0 = int(portfolio.positions.get(sym, 0)) if portfolio is not None else 0
            plan = EvPlan(sym, 1 if pct > 0 else -1, pre, pct, ov, o.now(), ramp_s, pos0)
            plan.target = int(budget / pre)
            self.ev[sym] = plan
            self.stats["plans"] += 1

    def on_shock(self, data: dict, portfolio, market) -> None:
        o = self.owner
        targets = data.get("prices") or {}
        for sym in data.get("affected") or []:
            pre = o._refs.get(sym) or o._mids.get(sym)
            target = targets.get(sym)
            if pre and target:
                self.on_calendar({"pct": float(target) / pre - 1.0, "affected": [sym],
                                  "ramp_ticks": o.ramp_ticks, "overshoot": o.overshoot}, portfolio, market)

    def note_quotes(self, market) -> None:
        o = self.owner
        for sym in market.symbols():
            bid, ask = market.best_bid(sym), market.best_ask(sym)
            if bid and ask:
                o._mids[sym] = (bid + ask) / 2
                self.rel_spread[sym] = (ask - bid) / ((ask + bid) / 2)

    # -- decisions ----------------------------------------------------------
    def signal(self, market, portfolio):
        cfg = self.cfg
        nw = portfolio.net_worth(market)
        self.peak = max(self.peak, nw)
        halted = nw < self.peak * (1 - cfg.ev_kill)
        best = None
        for sym, p in list(self.ev.items()):
            sig, urg = self._step(p, market, portfolio, halted)
            if p.state == "done":
                self.ev.pop(sym, None)
            if sig is not None and (best is None or urg > best[0]):
                best = (urg, sig)
        return best[1] if best else None

    def _step(self, p: EvPlan, market, portfolio, halted):
        cfg, o = self.cfg, self.owner
        sym = p.sym
        bot = getattr(o, "_bot", None)
        if sym in o._halted_symbols or (bot is not None and sym in bot._entry_from_board):
            return None, 0
        bid, ask = market.best_bid(sym), market.best_ask(sym)
        mid = (bid + ask) / 2 if (bid and ask) else o._mids.get(sym)
        if not mid:
            return None, 0
        age = o.now() - p.t0
        pos = int(portfolio.positions.get(sym, 0))
        held = p.sign * (pos - p.pos0)
        sign = p.sign
        if p.state == "enter":
            if age < cfg.ev_delay:
                return None, 0
            if sign < 0 and not (cfg.enable_short and o._shorts_allowed and sym not in o._ssr):
                p.state = "done"
                return None, 0
            limit = p.pre * (1 + sign * cfg.ev_entry * abs(p.pct))
            want = p.target - max(held, 0)
            if halted or want <= 0 or age > p.ramp_s * cfg.ev_window:
                p.state = "hold" if held > 0 else "done"
                return None, 0
            if sign > 0:
                if not ask or ask > limit:
                    return None, 0
                qty = min(want, cfg.ev_chunk, _depth(market, sym, "asks", limit))
                if qty <= 0 or not portfolio.can_buy(sym, qty, limit):
                    return None, 0
                return Signal(symbol=sym, side="buy", quantity=qty, price=round(limit, 2)), 3.0
            if not bid or bid < limit:
                return None, 0
            qty = min(want, cfg.ev_chunk, _depth(market, sym, "bids", limit))
            if qty <= 0:
                return None, 0
            return Signal(symbol=sym, side="sell", quantity=qty, price=round(limit, 2)), 3.0
        if p.state == "hold":
            if held <= 0:
                p.state = "done"
                return None, 0
            if not p.entry_px:
                p.entry_px = mid
            take = p.pre * (1 + sign * cfg.ev_take * p.ov * abs(p.pct))
            stop = p.entry_px * (1 - sign * cfg.ev_stop * abs(p.pct))
            hit = (mid >= take or mid <= stop) if sign > 0 else (mid <= take or mid >= stop)
            if hit or age > p.ramp_s * cfg.ev_time:
                qty = min(held, cfg.ev_chunk * 2)
                if sign > 0 and bid:
                    return Signal(symbol=sym, side="sell", quantity=qty, price=round(bid * 0.995, 2)), 5.0
                if sign < 0 and ask:
                    return Signal(symbol=sym, side="buy", quantity=qty, price=round(ask * 1.005, 2)), 5.0
        return None, 0

    def on_fill(self, side: str, symbol: str, quantity: int, price: float) -> None:
        p = self.ev.get(symbol)
        if p is not None and not p.entry_px and ((side == "buy") == (p.sign > 0)):
            p.entry_px = price
