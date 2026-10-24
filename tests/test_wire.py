"""The wire is typed: what we send is a model, what we read goes through the union."""

import asyncio
import json

from pydantic import TypeAdapter

import trader.config as cfg
from arena import Signal
from shared.messages import AnyClientMessage, PlaceOrder, TradeExecution
from team.trader import WIRE, MyTrader, _Bot

CLIENT = TypeAdapter(AnyClientMessage)


class _FakeWS:
    """Stands in for the websocket: records what we send, replays given frames."""

    def __init__(self, frames: list[str] = ()) -> None:
        self.sent: list[str] = []
        self._frames = list(frames)

    async def send(self, text: str) -> None:
        self.sent.append(text)

    def __aiter__(self):
        return self._gen()

    async def _gen(self):
        for f in self._frames:
            yield f


def _bot(tmp_path, ws: _FakeWS) -> _Bot:
    bot = _Bot(MyTrader())
    bot._state_path = tmp_path / "state.json"
    bot._ws = ws
    return bot


def test_orders_leave_as_a_typed_place_order(tmp_path):
    ws = _FakeWS()
    bot = _bot(tmp_path, ws)
    asyncio.run(bot._place_order(Signal(symbol="NVDA", side="buy", quantity=5, price=137.5)))
    (frame,) = ws.sent
    msg = CLIENT.validate_json(frame)                  # the exchange's own view of our frame
    assert isinstance(msg, PlaceOrder)
    assert (msg.team_id, msg.order_type, msg.quantity) == (cfg.TEAM_ID, "ioc", 5)


def test_union_picks_the_model_from_the_type_tag():
    fill = TradeExecution(trade_id="t1", symbol="NVDA", price=137.5, quantity=3, buyer_id="a",
                          seller_id="b", aggressor="buy", fee=1.24)
    assert isinstance(WIRE.validate_json(fill.model_dump_json()), TradeExecution)


def test_bad_frames_are_dropped_and_good_ones_still_land(tmp_path):
    ours = TradeExecution(trade_id="t1", symbol="NVDA", price=100.0, quantity=4,
                          buyer_id=cfg.TEAM_ID, seller_id="x", aggressor="buy", fee=1.2,
                          taker_id=cfg.TEAM_ID).model_dump_json()
    malformed = json.dumps({**json.loads(ours), "quantity": "ten"})
    unknown = json.dumps({"type": "teacher_only_thing", "x": 1})
    bot = _bot(tmp_path, _FakeWS([malformed, unknown, ours]))
    asyncio.run(bot.listen())                          # must not raise
    assert bot.ledger.positions == {"NVDA": 4}         # only the valid fill was booked
    assert bot._unknown_tags == {"teacher_only_thing"}
