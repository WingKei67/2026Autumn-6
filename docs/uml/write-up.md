## Design justification

`Trader` is an abstract base class with a single `@abstractmethod` (`on_tick`) rather than a bare function you register, because the ABC pattern lets the engine own the entire event loop (`run()` / `asyncio.run(...)`) while enforcing the strategy contract at class-definition time — Python raises `TypeError` the moment you instantiate a subclass that forgot `on_tick`, not at the first tick, which makes bugs cheap to catch. The trade-off versus a callback registry is slightly more boilerplate (one subclass instead of one function), but the gain is full access to `self` across hooks, so state like `_done`, rolling averages, or a trained model can live naturally on the instance without external closures or module-level globals.

## `on_fill` log snippet

`MyTrader` overrides the optional `on_fill` hook to log each execution:

```python
def on_fill(self, side, symbol, quantity, price):
    print(f"FILL {side} {quantity} {symbol} @ {price:.2f}")
```

Output from running `TEAM_ID=6_rings_trader_1 python -m team.trader`:

```
17:30:07  INFO      Connected to wss://feed.algoarena-st.duckdns.org as 6_rings_trader_1 (trader)
Tick 745091  rank=#?  net_worth=$535,143.64  realized_pnl=+0.00
17:30:07  INFO      Session event: SESSION_OPEN — Session already open — you joined mid-session.
17:30:08  INFO      Fill: bought 1 AAPL @ 205.1300 (fee=0.3077)
FILL buy 1 AAPL @ 205.13
Tick 745092  rank=#?  net_worth=$535,143.76  realized_pnl=+0.00
```

The `FILL buy 1 AAPL @ 205.13` line is printed by our `on_fill` override; the preceding `INFO Fill: ...` line is the SDK's own log. The bot placed one size-limited order (guarded by `portfolio.can_buy`) and then sat out via `return None`. The 0.3077 fee is 30 bps taker on 205.13.
