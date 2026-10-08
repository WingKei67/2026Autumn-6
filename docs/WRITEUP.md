# Week 1 Write-up — 6 Rings

## Design justification

`Trader` is an abstract base class with a single `@abstractmethod`, `on_tick`, rather than a function we register, because the framework has to guarantee exactly one thing: that every bot can answer "what do you do this tick?". Python enforces this at instantiation, so a subclass that forgets `on_tick` fails immediately with a `TypeError` instead of silently doing nothing in the middle of a live session. The class also lets a bot keep state across ticks (my `_order_sent` flag) and bundle the optional hooks (`on_fill`, `on_event`, `on_ipo`) with sensible default implementations, so we override only what we need while the plumbing (connection, auth, order placement) stays in the base class (inversion of control: the engine calls us, we never call the engine). The cost is heavier ceremony than a bare function: students must learn inheritance, and the contract is only checked at runtime rather than by a static type checker unless we add annotations; a registered function would be simpler for a one-line strategy and easier to unit-test in isolation, but would force state into globals and make the optional hooks awkward to express.

## Offline check (`SimSession`, no network)

`MyTrader` wrapped with `as_signal_fn` ran 1000 simulated ticks without errors and
made exactly one trade: a buy of 1 AAPL at 220.10 (fee 0.22). It then sat out.

## Log excerpt: connect and first order

TODO: paste the live-arena log (handshake + accepted order).

## Log excerpt: `on_fill`

TODO: paste the `FILL: ...` line from the live run.
