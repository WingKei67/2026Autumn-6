"""Risk telemetry and fixed independent seat allocations for Week 5.

No shared process memory is needed: 150 + 150 + 20 = 320 < 800.
This assumes these are the team's only active trading seats and that its
starting/reconciled holdings already satisfy the exchange limit.
"""
import json
import logging

logger = logging.getLogger(__name__)

SEAT_CAPS = {"6_rings_trader_1": 150, "6_rings_trader_2": 150, "6_rings_broker": 20}


def log_state(seat, positions, marks, cash, equity, rows=()):
    team = {}
    for row in rows:
        if row.get("team_id") in SEAT_CAPS:
            for sym, qty in (row.get("positions") or {}).items():
                team[sym] = team.get(sym, 0) + qty
    known = all(marks.get(s, 0) > 0 for s, q in positions.items() if q)
    gross = sum(abs(q) * marks.get(s, 0) for s, q in positions.items()) if known else None
    logger.info("RISK_STATE %s", json.dumps({"seat": seat, "positions": positions,
        "team_positions": team, "marks": marks, "gross": gross, "cash": cash, "equity": equity}, sort_keys=True))


def acquire_seat_lock(seat):
    """Hold one macOS/POSIX advisory lock per local seat for process lifetime."""
    import fcntl
    from pathlib import Path
    root = Path(__file__).resolve().parents[1] / 'logs'
    root.mkdir(exist_ok=True)
    handle = (root / (seat + '.lock')).open('a')
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        raise RuntimeError('Another local process already owns seat ' + seat) from None
    return handle
