"""
6 Rings — trader bot.

Implement on_tick(): decide a trade (or None) each tick. Everything else —
connection, auth, portfolio tracking — is handled by the base class.

Run one seat:
    TEAM_ID=6_rings_trader_1 python -m team.trader
"""

from __future__ import annotations

import logging

from arena import Signal, Trader

logger = logging.getLogger(__name__)

SYMBOL = "AAPL"
ORDER_QTY = 1  # Level 1: one tiny order is enough


class MyTrader(Trader):
    """Level 1: place a single small buy of AAPL, then sit out."""

    def __init__(self) -> None:
        self._done = False

    def on_tick(self, market, portfolio):
        if self._done:
            return None
        ask = market.best_ask(SYMBOL)
        if ask is None or not portfolio.can_buy(SYMBOL, ORDER_QTY, ask):
            return None
        self._done = True
        return Signal(symbol=SYMBOL, side="buy", quantity=ORDER_QTY, price=ask)

    def on_fill(self, side, symbol, quantity, price):
        logger.info("FILL: %s %d %s @ %.2f", side, quantity, symbol, price)


if __name__ == "__main__":
    MyTrader().run()
