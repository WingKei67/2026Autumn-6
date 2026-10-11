# UML class diagram — where each box lives

Diagram: `class_diagram.png` (source: `class_diagram.mmd`, Mermaid).

- `Trader` (abstract) — `arena/trader.py`; `Broker` — `arena/broker.py`; `Exchange` — `arena/exchange.py`
- `MyTrader` (our subclass) — `team/trader.py`; `MyBroker` (our subclass) — `team/broker.py`
- `MarketData`, `Portfolio`, `TraderBot` — `engine/trader/trader.py`
- `Signal`, `Handshake`, `PlaceOrder`, `CancelOrder`, `OrderAck`, `BookSnapshot` — `engine/shared/messages.py`
- `ExchangeServer` (hosted arena) — `engine/exchange/server.py`

Only `Trader` is a real ABC; `Broker` and `Exchange` are plain classes with default hooks.
