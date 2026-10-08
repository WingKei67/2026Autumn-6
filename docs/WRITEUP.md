# Week 1 Write-up — 6 Rings

## Design justification

`Trader` is an abstract base class with a single `@abstractmethod`, `on_tick`, rather than a function we register, because the framework has to guarantee exactly one thing: that every bot can answer "what do you do this tick?". Python enforces this at instantiation, so a subclass that forgets `on_tick` fails immediately with a `TypeError` instead of silently doing nothing in the middle of a live session. The class also lets a bot keep state across ticks (my `_order_sent` flag) and bundle the optional hooks (`on_fill`, `on_event`, `on_ipo`) with sensible default implementations, so we override only what we need while the plumbing (connection, auth, order placement) stays in the base class (inversion of control: the engine calls us, we never call the engine). The cost is heavier ceremony than a bare function: students must learn inheritance, and the contract is only checked at runtime rather than by a static type checker unless we add annotations; a registered function would be simpler for a one-line strategy and easier to unit-test in isolation, but would force state into globals and make the optional hooks awkward to express.

## Offline check (`SimSession`, no network)

`MyTrader` wrapped with `as_signal_fn` ran 1000 simulated ticks without errors and
made exactly one trade: a buy of 1 AAPL at 220.10 (fee 0.22). It then sat out.

## Log excerpt: connect and first order

Run: `TEAM_ID=6_rings_trader_1 python -m team.trader` against the class arena.

```
14:14:04  INFO      Connected to wss://feed.algoarena-st.duckdns.org as 6_rings_trader_1 (trader)
14:14:04  INFO      Session event: FEE_SCHEDULE — Venue fee schedule: taker 30.0 bps, maker rebate 5.0 bps
14:14:04  INFO      Session event: SESSION_OPEN — Session already open — you joined mid-session.
14:14:05  INFO      Fill: bought 1 AAPL @ 208.7000 (fee=0.3130)
```

The handshake succeeded, and our single limit buy of 1 AAPL at the best ask was
accepted and filled. No further orders were sent.

## Log excerpt: `on_fill`

Our `on_fill` override logs each execution of our own orders:

```
14:14:05  INFO      FILL: buy 1 AAPL @ 208.70
```
