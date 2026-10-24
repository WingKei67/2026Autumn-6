"""The bot survives a dropped socket: it handshakes again on its own."""

import asyncio
import contextlib

import websockets

import trader.config as cfg
from shared.messages import Handshake, Leaderboard
from team.trader import MyTrader, _Bot


def test_bot_reconnects_after_drop(monkeypatch, tmp_path):
    seen: list[Handshake] = []

    async def exchange(ws):
        seen.append(Handshake.model_validate_json(await ws.recv()))
        await ws.close()                     # the exchange drops us

    async def scenario():
        async with websockets.serve(exchange, "localhost", 0) as srv:
            port = srv.sockets[0].getsockname()[1]
            monkeypatch.setattr(cfg, "EXCHANGE_URL", f"ws://localhost:{port}")
            bot = _Bot(MyTrader())
            bot._state_path = tmp_path / "state.json"
            task = asyncio.create_task(bot.run())
            for _ in range(100):
                if len(seen) >= 2:
                    break
                await asyncio.sleep(0.1)
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    asyncio.run(scenario())
    assert len(seen) >= 2 and all(h.role == "trader" for h in seen)


def test_books_resync_from_leaderboard_row(monkeypatch, tmp_path):
    """An existing seat gets no PortfolioUpdate on reconnect — only the leaderboard."""
    row = {"team_id": cfg.TEAM_ID, "cash": 210_000.0, "positions": {"NVDA": 40},
           "avg_cost": {"NVDA": 162.43}, "realized_pnl": -4_109.98, "net_worth": 216_500.0}
    board = Leaderboard(traders=[row], brokers=[], exchange_fees=0.0, tick=1)

    async def exchange(ws):
        await ws.recv()                      # the handshake
        await ws.send(board.model_dump_json())
        await asyncio.sleep(0.2)
        await ws.close()

    async def scenario() -> _Bot:
        async with websockets.serve(exchange, "localhost", 0) as srv:
            port = srv.sockets[0].getsockname()[1]
            monkeypatch.setattr(cfg, "EXCHANGE_URL", f"ws://localhost:{port}")
            bot = _Bot(MyTrader())
            bot._state_path = tmp_path / "state.json"
            task = asyncio.create_task(bot.run())
            for _ in range(50):
                if bot.ledger.positions:
                    break
                await asyncio.sleep(0.05)
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
            return bot

    bot = asyncio.run(scenario())
    assert bot.ledger.positions == {"NVDA": 40}
    assert bot.ledger.avg_entry == {"NVDA": 162.43}
    assert bot.ledger.cash == 210_000.0 and bot.ledger.realized_pnl == -4_109.98
