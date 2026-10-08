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

ORDER_QTY = 1  # Level 1: one tiny order is enough


class MyTrader(Trader):
    """Level 1: place a single small buy, then sit out."""

    def __init__(self) -> None:
        self._order_sent = False

    def on_tick(self, market, portfolio):
        if self._order_sent:
            return None

        for sym in sorted(market.symbols()):
            ask = market.best_ask(sym)
            if ask is None:  # empty book, nothing to buy from
                continue
            if not portfolio.can_buy(sym, ORDER_QTY, ask):
                continue
            self._order_sent = True
            return Signal(symbol=sym, side="buy", quantity=ORDER_QTY, price=ask)

        return None

    def on_fill(self, side, symbol, quantity, price):
        logger.info("FILL: %s %d %s @ %.2f", side, quantity, symbol, price)


if __name__ == "__main__":
    MyTrader().run()
