# 6 Rings

Budget invested: $1,000,000

- 🏛 Exchange license — your venue is assigned port 8772

- 🏦 `6_rings_broker` ($350,000):

      TEAM_ID=6_rings_broker python -m team.broker

- 🤖 `6_rings_trader_1` ($175,000):

      TEAM_ID=6_rings_trader_1 python -m team.trader

- 🤖 `6_rings_trader_2` ($175,000):

      TEAM_ID=6_rings_trader_2 python -m team.trader

## Where to write code

- `trader.py` → `MyTrader.on_tick()` — your edge
- `broker.py` → `MyBroker.spread()/skew()` — quoting and inventory
- `exchange.py` → `MyExchange` — your fee schedule and policy
- `config.py` → your tunables

Each class derives an `arena` base that handles all plumbing —
see `from arena import Trader, Broker, Exchange`.
