# UML Diagram — AlgoArena Class Hierarchy

`algoarena_uml.png` shows the full SDK class hierarchy.

| Box in diagram | Source file |
|----------------|-------------|
| `Trader` (abstract, blue) | `arena/trader.py` — the ABC your bot must subclass |
| `Broker` (base, grey) | `arena/broker.py` — base class for market-maker bots |
| `Exchange` (base, grey) | `arena/exchange.py` — base class for venue implementations |
| `MyTrader` (concrete, green) | `team/trader.py` — **your implementation** |
| `MarketData` | `engine/trader/trader.py` — live order-book and price-history |
| `Portfolio` | `engine/trader/trader.py` — local cash/position mirror, synced from exchange |
| `Signal` | `engine/shared/messages.py` — Pydantic order intent returned by `on_tick()` |

**Design justification.** `Trader` is an abstract base class with a single `@abstractmethod` (`on_tick`) rather than a bare function you register, because the ABC pattern lets the engine own the entire event loop (`run()` / `asyncio.run(...)`) while enforcing the strategy contract at class-definition time — Python raises `TypeError` the moment you instantiate a subclass that forgot `on_tick`, not at the first tick, which makes bugs cheap to catch. The trade-off versus a callback registry is slightly more boilerplate (one subclass instead of one function), but the gain is full access to `self` across hooks, so state like `_done`, rolling averages, or a trained model can live naturally on the instance without external closures or module-level globals.
