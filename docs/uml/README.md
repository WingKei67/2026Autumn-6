# UML class diagram — where each box lives

Diagram: `class_diagram.png` (source: `class_diagram.mmd`, Mermaid).

- `Trader` (abstract), `_AdaptedTraderBot`, `_AdapterStrategy` — `arena/trader.py`
- `Broker` — `arena/broker.py`
- `Exchange` — `arena/exchange.py`
- `MyTrader` (our concrete subclass) — `team/trader.py`
- `MarketData`, `Portfolio`, `Strategy`, `TraderBot` — `engine/trader/trader.py`
- `Signal` — `engine/shared/messages.py`

Only `Trader` is a real ABC; `Broker` and `Exchange` are plain classes with default hooks.
