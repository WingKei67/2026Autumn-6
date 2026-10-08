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


