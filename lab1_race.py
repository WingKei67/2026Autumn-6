from sim.session import SimSession               # sim/ is a real package
from arena import Signal, Trader, as_signal_fn


class Momentum(Trader):
    """Buy strength: last price above its N-tick average."""
    def __init__(self, lookback=20, qty=5):
        self.lookback, self.qty = lookback, qty

    def on_tick(self, market, portfolio):
        for sym in market.symbols():
            hist = market.prices(sym)
            if len(hist) < self.lookback:
                continue
            avg = sum(hist[-self.lookback:]) / self.lookback
            ask, bid = market.best_ask(sym), market.best_bid(sym)
            if ask and hist[-1] > avg * 1.001 and portfolio.can_buy(sym, self.qty, ask):
                return Signal(symbol=sym, side="buy", quantity=self.qty, price=ask)
            if bid and hist[-1] < avg * 0.999 and portfolio.can_sell(sym, self.qty):
                return Signal(symbol=sym, side="sell", quantity=self.qty, price=bid)
        return None


class MeanReversion(Trader):
    """Buy weakness: last price below its N-tick average."""
    def __init__(self, lookback=20, qty=5, band=0.003):
        self.lookback, self.qty, self.band = lookback, qty, band

    def on_tick(self, market, portfolio):
        for sym in market.symbols():
            hist = market.prices(sym)
            if len(hist) < self.lookback:
                continue
            avg = sum(hist[-self.lookback:]) / self.lookback
            ask, bid = market.best_ask(sym), market.best_bid(sym)
            if ask and hist[-1] < avg * (1 - self.band) and portfolio.can_buy(sym, self.qty, ask):
                return Signal(symbol=sym, side="buy", quantity=self.qty, price=ask)
            if bid and hist[-1] > avg * (1 + self.band) and portfolio.can_sell(sym, self.qty):
                return Signal(symbol=sym, side="sell", quantity=self.qty, price=bid)
        return None


SimSession().run(
    n_ticks=400,
    strategies=[("momentum", as_signal_fn(Momentum())),
                ("reversion", as_signal_fn(MeanReversion(band=0.001)))],
    symbols=["AAPL", "TSLA"], seed=7, verbose=False,
).print_summary()