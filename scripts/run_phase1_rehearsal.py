"""Run an accelerated LOCAL Week 4 rehearsal, not a Friday live session.

The student bot uses a real WebSocket and the real exchange matching and
settlement code. A controlled local counterparty supplies liquidity. The
rehearsal advances 2,400 open-market venue ticks (40 minutes at the normal
venue rate). Only this test process replaces the strategy's wall clock with
exchange tick time. Default runtime is normally under two minutes; --realtime uses the ordinary
one-second venue clock for about 40 minutes of wall-clock survival evidence.
"""

import argparse
import asyncio
import contextlib
import logging
import os
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
RUNTIME = pathlib.Path(tempfile.mkdtemp(prefix="phase1-rehearsal-"))
# Set these before project imports so .env cannot route this test to a live venue.
os.environ.update(
    EXCHANGE_URL="ws://127.0.0.1:1",
    TEAM_ID="6_rings_trader_2",
    ARENA_TOKEN="",
    GAME_WEEK="4",
    SCENARIO_PATH=str(ROOT / "season/week04.json"),
    SEASON_DIR=str(ROOT / "season"),
    SEASON_PERSIST="false",
    ROSTER_PATH=str(RUNTIME / "absent-roster.json"),
    SEASON_PATH=str(RUNTIME / "season.json"),
    AUTH_REQUIRED="false",
    BASE_PRICES_PATH=str(RUNTIME / "absent-base.json"),
    SEAT_POSITION_CAP="150",
)
sys.path[:0] = [str(ROOT), str(ROOT / "engine")]

import websockets  # noqa: E402
import exchange.config as exchange_config  # noqa: E402
from exchange.server import ExchangeServer, Portfolio  # noqa: E402
from shared.messages import PlaceOrder  # noqa: E402
import team.trader as student  # noqa: E402
from team.ledger import Ledger  # noqa: E402


class CounterpartySink:
    """Receive acknowledgements for the controlled local liquidity counterparty."""

    async def send(self, raw: str) -> None:
        pass


async def run(real_time: bool = False) -> None:
    """Exercise the submitted bot through a complete accelerated scenario."""
    (ROOT / "evidence").mkdir(exist_ok=True)
    logfile = ROOT / "evidence/phase1_final_rehearsal.log"
    logging.basicConfig(
        level=logging.INFO, force=True, filename=logfile, filemode="w",
        format="%(asctime)s %(levelname)s %(message)s",
    )
    mode = "REAL-TIME" if real_time else "ACCELERATED"
    logging.info(
        "LOCAL %s REHEARSAL: Week 4, 2400 open-market venue ticks; "
        "not an original Friday live recording", mode,
    )
    student._config.ARENA_TOKEN = ""
    student._config.TICK_INTERVAL_SEC = 0.1 if real_time else 0.001
    student.DROP_AFTER_S = 120.0 if real_time else 1.0
    student.STATUS_EVERY_S = 30.0 if real_time else 10.0
    venue = ExchangeServer()
    exchange_config.SESSIONS_DIR = str(RUNTIME / "recordings")
    maker = "rehearsal_mm"
    venue.portfolios[maker] = Portfolio(
        team_id=maker, role="broker", level=1, cash=10_000_000.0,
    )
    sink = CounterpartySink()

    async with websockets.serve(venue.handle_client, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        student._config.EXCHANGE_URL = f"ws://127.0.0.1:{port}"
        owner = student.MyTrader()
        if not real_time:
            owner.now = lambda: float(venue.tick)
        bot = student._Bot(owner)
        # Keep this rehearsal's persistent books separate from the live seat.
        bot._state_path = RUNTIME / "bot.json"
        bot.ledger = Ledger()
        task = asyncio.create_task(bot.run())
        try:
            for _ in range(100):
                if bot._connects and not bot._resync:
                    break
                await asyncio.sleep(0.01)
            assert bot._connects and not bot._resync, "Initial connection/resync failed"
            await venue.open_session()

            # Week 4 begins with a 12-tick pre-open, then 2,400 open-market ticks.
            for _ in range(2412):
                await venue.advance_tick()
                if venue.session_open:
                    for symbol, book in list(venue.books.items()):
                        book.cancel_team_orders(maker)
                        mark = venue.ref_prices[symbol]
                        for side, price in (
                            ("buy", round(mark * 0.9995, 2)),
                            ("sell", round(mark * 1.0005, 2)),
                        ):
                            await venue._handle_place_order(
                                sink,
                                PlaceOrder(
                                    team_id=maker, symbol=symbol, side=side,
                                    quantity=100, price=price, order_type="limit",
                                ),
                                maker,
                            )
                for symbol, book in list(venue.books.items()):
                    await venue._broadcast(venue._make_snapshot(symbol, book))
                await venue._broadcast(venue._build_leaderboard())
                await asyncio.sleep(1.0 if real_time else 0.01)

            await venue.close_session(persist=False)
            for _ in range(100):
                if not bot._closing and "RECONCILE at close" in logfile.read_text():
                    break
                await asyncio.sleep(0.01)

            data = logfile.read_text()
            assert bot._connects >= 2, "Reconnect not demonstrated"
            assert bot.rejects == 0, "Trader rejects occurred; inspect the log"
            assert data.count("FILL ") > 0, "No fills occurred"
            assert venue.ipo_allocations.get(student._config.TEAM_ID, 0) > 0, "No IPO allocation"
            assert (
                "OK: accounting and unrealized P&L match at venue valuation marks" in data
            ), "Final reconciliation did not pass"
            row = next(
                row for row in venue._build_leaderboard().traders
                if row["team_id"] == student._config.TEAM_ID
            )
            assert not bot.ledger.reconcile(**bot._row_state(row)), "Final accounting mismatch"
            assert all(
                abs(qty) <= student.SEAT_POSITION_CAP
                for qty in bot.ledger.positions.values()
            ), "Seat cap exceeded"
            logging.info(
                "LOCAL_VALIDATION PASSED: reconnect, fills, IPO, zero trader rejects, "
                "final accounting and P&L reconciliation"
            )
            data = logfile.read_text()
            print(f"LOCAL {mode} REHEARSAL — not original Friday participation")
            print("LOG", logfile)
            print("RAW RECORDINGS", RUNTIME / "recordings")
            print(
                "connects", bot._connects, "rejects", bot.rejects,
                "fills", data.count("FILL "), "IPO allocations", venue.ipo_allocations,
            )
            for line in data.splitlines():
                if any(tag in line for tag in (
                    "CASH_AUDIT", "RECONCILE at close", "SESSION SUMMARY",
                    "IPO ALLOCATED", "DISCONNECT", "RECONNECT",
                )):
                    print(line)
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--realtime", action="store_true", help="Use the normal venue clock (about 40 minutes)")
    args = parser.parse_args()
    asyncio.run(run(real_time=args.realtime))
