#!/usr/bin/env python
"""
scripts/phase2_session_evidence.py — Part A checks, from the recording and the live log.

For a recorded block (the venue's own tape) and our seat's live log it prints,
for every TRADING_HALT / TRADING_RESUMED on a symbol:
  * fills by any of our seats in that symbol while it was halted (must be 0)
  * our first order after the resume (live log: "Order sent") and first fill (tape)
and, for the seat:
  * the largest |position| it held in each symbol (live log, every FILL line
    carries the exchange-reconciled position) against the seat cap and the
    scenario's position limit
  * any LIQUIDATION event or log line

    python scripts/phase2_session_evidence.py sessions/session_week05_….jsonl \\
        logs/6_rings_trader_2_20261029_163731.log --utc-offset -5
"""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import pathlib
import re
import sys

POS_RE = re.compile(r"^(\d\d:\d\d:\d\d) INFO\s+FILL (?:buy|sell) \d+ (\S+) @ [\d.]+ \(fee [\d.]+\) → \S+ (flat|[+-]\d+)")
SENT_RE = re.compile(r"^(\d\d:\d\d:\d\d) INFO\s+Order sent: (buy|sell) (\d+) (\S+) @ ([\d.]+)")
LIQ_RE = re.compile(r"LIQUIDAT", re.I)
TS_RE = re.compile(r"^(\d\d:\d\d:\d\d) ")
ROW_RE = re.compile(r"^    ([A-Z][A-Z0-9]{1,6})\s+([+-]\d+)\s+[\d.]+\s+[\d.]+")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("tape")
    ap.add_argument("log")
    ap.add_argument("--seat", default="6_rings_trader_2")
    ap.add_argument("--team", default="6_rings_")
    ap.add_argument("--utc-offset", type=int, required=True, help="hours, the live log's clock")
    ap.add_argument("--seat-cap", type=int, default=150)
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)

    # ── the tape ──────────────────────────────────────────────────────
    scen, open_ts, close_ts = {}, None, None
    halts: list[tuple[float, str, str, str, int]] = []      # ts, event, symbol, message, tick
    fills: list[tuple[float, str, str, str, int, float, int]] = []   # ts, seat, symbol, side, qty, px, tick
    liq = []
    last_ts = 0.0
    with open(a.tape) as fh:
        for line in fh:
            if '"session_event"' not in line and a.team not in line:
                continue
            r = json.loads(line)
            m, ts, tick = r.get("msg") or {}, float(r["ts"]), int(r.get("tick") or 0)
            last_ts = max(last_ts, ts)
            if m.get("type") == "session_event":
                ev = m.get("event")
                if ev == "SESSION_OPEN":
                    open_ts = ts
                    scen = (m.get("data") or {}).get("scenario") or {}
                elif ev == "SESSION_CLOSED":
                    close_ts = ts
                elif ev in ("TRADING_HALT", "TRADING_RESUMED", "MARKET_HALT", "MARKET_RESUMED"):
                    halts.append((ts, ev, (m.get("data") or {}).get("symbol", "*"), m.get("message", ""), tick))
                elif ev and "LIQUIDAT" in ev:
                    liq.append((ts, m.get("message", "")))
            elif m.get("type") == "trade_execution":
                for side, who in (("buy", m.get("buyer_id", "")), ("sell", m.get("seller_id", ""))):
                    if who.startswith(a.team):
                        fills.append((ts, who, m["symbol"], side, int(m["quantity"]), float(m["price"]), tick))
    limit = scen.get("position_limit")
    close_ts = close_ts or last_ts          # a closing-auction block ends on SESSION_PRECLOSE + the cross
    t0 = open_ts or (halts[0][0] if halts else 0.0)

    # ── the live log ──────────────────────────────────────────────────
    log = pathlib.Path(a.log)
    day = re.search(r"_(\d{8})_", log.name).group(1)
    tz = dt.timezone(dt.timedelta(hours=a.utc_offset))

    def epoch(hms: str) -> float:
        return dt.datetime.strptime(day + hms, "%Y%m%d%H:%M:%S").replace(tzinfo=tz).timestamp()

    max_pos: dict[str, int] = collections.defaultdict(int)
    sent: list[tuple[float, str]] = []
    log_liq = []
    in_block = False                      # the last timestamped line fell inside the block
    for line in log.read_text(errors="replace").splitlines():
        if (ts_m := TS_RE.match(line)):
            in_block = bool(open_ts and open_ts <= epoch(ts_m[1]) <= close_ts)
        elif in_block and (m := ROW_RE.match(line)):   # a BOOKS table row: carried positions too
            max_pos[m[1]] = max(max_pos[m[1]], abs(int(m[2])))
            continue
        if m := POS_RE.match(line):
            if in_block:
                q = 0 if m[3] == "flat" else int(m[3])
                max_pos[m[2]] = max(max_pos[m[2]], abs(q))
        elif m := SENT_RE.match(line):
            sent.append((epoch(m[1]), m[4]))
        elif LIQ_RE.search(line) and "LIQUIDATED" in line:
            log_liq.append(line[:120])

    out = [f"# Part A evidence — {pathlib.Path(a.tape).name}",
           f"scenario: week {scen.get('week')} · {scen.get('label', '')} · position_limit {limit} · "
           f"seat cap {a.seat_cap} · {a.seat} live log {log.name}", ""]

    # halts
    out.append("## Halts on the venue and what our seats did")
    open_halt: dict[str, int] = {}          # symbol → tick of its halt
    my_syms = {s for _, who, s, *_ in fills if who == a.seat} | set(max_pos)
    for ts, ev, sym, msg, tick in halts:
        rel = f"t+{ts - t0:6.0f}s"
        if ev == "TRADING_HALT":
            open_halt[sym] = tick
            out.append(f"{rel} HALT    {sym:5s} {msg[len('Trading halted on '):][:70]}")
            continue
        start = open_halt.pop(sym, None)
        # strictly after the halt's tick: the print that TRIPPED the halt shares its tick
        during = [f for f in fills if f[2] == sym and start is not None and start < f[6] < tick]
        nxt_fill = next((f for f in fills if f[1] == a.seat and f[2] == sym and f[0] >= ts), None)
        nxt_sent = next((t for t, s in sent if s == sym and t >= ts - 1), None)
        trades = "we trade it" if sym in my_syms else "not one of ours"
        out.append(f"{rel} RESUMED {sym:5s} ({trades}) · our fills during the halt: {len(during)} · "
                   f"first order after: {f'+{max(0.0, nxt_sent - ts):.0f} s' if nxt_sent is not None else '—'} · "
                   f"first fill after: {f'+{nxt_fill[0] - ts:.1f} s' if nxt_fill else '—'}")
    out.append("")

    # positions
    out.append(f"## Largest |position| of {a.seat} per symbol during the block (live log, exchange-reconciled)")
    worst = max(max_pos.values(), default=0)
    out.append("  " + "  ".join(f"{s} {q}" for s, q in sorted(max_pos.items())))
    out.append(f"  max {worst} · seat cap {a.seat_cap} · scenario limit {limit} → "
               f"{'within' if worst <= a.seat_cap and (limit is None or worst <= limit) else 'EXCEEDED'}")
    team_flow: dict[str, int] = collections.defaultdict(int)
    for _, who, s, side, q, *_ in fills:
        team_flow[s] += q if side == "buy" else -q
    out.append("  team net share flow in the block (all 6_rings_ seats, tape): " +
               "  ".join(f"{s} {v:+d}" for s, v in sorted(team_flow.items())))
    out.append("")
    out.append("## Liquidation")
    out.append(f"  LIQUIDATION events on the tape: {len(liq)} · LIQUIDATED lines in our log: {len(log_liq)}")
    seats = collections.Counter(who for _, who, *_ in fills)
    out.append(f"  our fills on the tape: {dict(seats)}")
    text = "\n".join(out)
    print(text)
    if a.out:
        pathlib.Path(a.out).write_text(text + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
