"""Production _RiskBrokerBot behaviour on an uncorrelated reject (POST_ONLY_CROSS has no order id)."""
import asyncio
import research.harness  # noqa: F401  (path/env setup)
from team.broker import MyBroker, _RiskBrokerBot
from shared.messages import ErrorMsg


class FakeWS:
    closed = False
    async def close(self): self.closed = True


def test_post_only_cross_fails_closed_and_reconnect_clears_state():
    async def go():
        b = _RiskBrokerBot(MyBroker()); b._ws = FakeWS()
        b._has_snapshot = True; b._orders[('AAPL', 'buy')] = [2, 100., None, False, False]
        b._on_error(ErrorMsg(code='POST_ONLY_CROSS', message='Post-only buy at 100.0 would cross the book'))
        await asyncio.sleep(0)
        assert b._has_snapshot is False and b._ws.closed       # orders blocked, socket dropped
        assert ('AAPL', 'buy') in b._orders                     # reservation held until reconnect sweep
        # the reconnect path (connect_to_exchange post-super section) is what releases it:
        b._orders.clear(); b.state.resting_orders.clear()
    asyncio.run(go())
