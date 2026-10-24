#!/usr/bin/env python
"""
scripts/phase1_evidence.py — what our seats actually did in a recorded session.

Reads the exchange's recording (including the audit-only `aux` records:
order acks, rejects, cancels, connects/disconnects) and prints, per seat:
uptime over the block, orders, fills, rejects by code, the reaction to every
SHOCK / CALENDAR_EVENT, and the IPO lifecycle lines.

Usage:
    python scripts/phase1_evidence.py sessions/<session>.jsonl --team 6_rings
"""

from __future__ import annotations

import argparse
import collections
import json


def uptime(events: list[tuple[int, str]], t0: int, t1: int) -> float:
    """Fraction of [t0, t1] a seat was connected, from participant events."""
    up, state, last = 0, False, t0
    for tick, ev in sorted(events):
        tick = min(max(tick, t0), t1)
        if state:
            up += tick - last
        state, last = ev == "connected", tick
    if state:
        up += t1 - last
    return up / max(1, t1 - t0)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("file")
    ap.add_argument("--team", default="6_rings")
    ap.add_argument("--window", type=int, default=60, help="reaction window in ticks")
    a = ap.parse_args()

    t_open = t_close = None
    total_trades = None
    has_aux = False
    shocks: list[tuple[int, str, str]] = []
    ipo: list[tuple[int, str, str]] = []
    acts = collections.defaultdict(list)                 # seat → ticks of orders/cancels
    first_ack: dict[str, int] = {}
    rejects = collections.defaultdict(collections.Counter)
    fills = collections.defaultdict(list)                # seat → ticks of our fills
    conn = collections.defaultdict(list)
    clock: tuple[int, float] | None = None              # (last known tick, its ts)
    t_first: int | None = None                           # first tick in the file

    with open(a.file) as f:
        for line in f:
            if (a.team not in line and '"session_event"' not in line
                    and ('"tick"' in line[:40] or '"leaderboard"' not in line)):
                continue                                  # skip everyone else's traffic cheaply
            rec = json.loads(line)
            m, ts = rec["msg"], float(rec.get("ts", 0.0))
            typ = m.get("type")
            # Hosted recordings stamp every record with its tick. Local ones don't:
            # take the tick carried by leaderboards / session events, advanced by
            # the wall clock in between (the venue ticks once a second).
            known = rec.get("tick") or m.get("tick") or (m.get("data") or {}).get("tick")
            if known:
                clock = (int(known), ts)
            tick = rec.get("tick") or (clock[0] + round(ts - clock[1]) if clock else 0)
            if t_first is None and tick:
                t_first = tick
            if typ == "leaderboard":
                continue
            has_aux = has_aux or bool(rec.get("aux"))
            if typ == "session_event":
                ev = m["event"]
                if ev == "SESSION_OPEN" and t_open is None:
                    t_open = tick
                elif ev == "SESSION_CLOSED":
                    t_close = tick
                    total_trades = (m.get("data") or {}).get("total_trades")
                elif ev in ("SHOCK", "CALENDAR_EVENT"):
                    shocks.append((tick, ev, m.get("message", "")))
                elif ev.startswith("IPO_"):
                    ipo.append((tick, ev, m.get("message", "")))
                continue
            seat = m.get("team_id", "")
            if typ == "trade_execution":
                for k in ("buyer_id", "seller_id"):
                    if m[k].startswith(a.team):
                        fills[m[k]].append(tick)
            elif not seat.startswith(a.team):
                continue
            elif typ == "participant":
                conn[seat].append((tick, m["event"]))
            elif typ in ("order_ack", "order_cancel"):
                acts[seat].append(tick)
                if typ == "order_ack" and t_open is not None and seat not in first_ack:
                    first_ack[seat] = tick - t_open
            elif typ == "order_reject":
                rejects[seat][m.get("code", "?")] += 1

    if t_open is None:                                    # recording began after the open
        t_open = t_first or 0
        print("NOTE: no SESSION_OPEN in this file — block measured from its first record.")
    t_close = t_close or t_open
    print(f"Block t{t_open} → t{t_close} ({t_close - t_open} ticks)")
    if not has_aux:
        # A "_market" export carries only the public stream (books, a sample of
        # trades, session events): no acks, rejects, cancels or connects.
        print("NOTE: no aux records — this is a market-only recording. Uptime, orders and\n"
              "      rejects are unknown; reactions below are measured from our FILLS only.\n"
              "      Ask for the full recording (or use the dashboard REPLAY tab) for those.")
    print()
    for t, ev, text in shocks:
        print(f"EVENT t{t} (+{t - t_open}) {ev}: {text}")
    print()
    for seat in sorted(set(conn) | set(acts) | set(rejects) | set(fills)):
        activity = acts[seat] if has_aux else fills[seat]
        print(f"── {seat}")
        if has_aux:
            print(f"   connected {uptime(conn[seat], t_open, t_close):.0%} of block"
                  f" · {len(acts[seat])} orders/cancels · {len(fills[seat])} fills"
                  f" · first order t_open+{first_ack.get(seat, '—')}")
            print(f"   rejects {sum(rejects[seat].values())} {dict(rejects[seat])}")
        else:
            first = min(fills[seat]) - t_open if fills[seat] else "—"
            print(f"   {len(fills[seat])} fills in this recording · first fill t_open+{first}")
        for t, ev, text in shocks:
            hit = sorted(x - t for x in activity if 0 <= x - t <= a.window)
            what = "reacted" if has_aux else "filled"
            print(f"   {ev:<14} t{t} {text[:40]:<40} → "
                  + (f"{what} in {hit[0]} ticks" if hit else f"no {what[:-2]} within {a.window}"))
    if total_trades and not has_aux:
        seen = sum(len(v) for v in fills.values())
        print(f"\n(SESSION_CLOSED reports {total_trades:,} trades venue-wide; this file "
              f"is a sample — our {seen} fills here are a lower bound.)")
    for t, ev, text in ipo:
        print(f"IPO  t{t} {ev}: {text}")


if __name__ == "__main__":
    main()
