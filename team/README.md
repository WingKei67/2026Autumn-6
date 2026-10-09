# 6 Rings

Budget invested: $1,000,000 (revised 2026-10-08: no exchange license)

- 🏦 `6_rings_broker` ($400,000):

      TEAM_ID=6_rings_broker python -m team.broker
      # or: make broker BOT=6_rings_broker

- 🤖 `6_rings_trader_1` ($150,000):

      TEAM_ID=6_rings_trader_1 python -m team.trader

- 🤖 `6_rings_trader_2` ($150,000):

      TEAM_ID=6_rings_trader_2 python -m team.trader

- 🤖 `6_rings_trader_3` ($150,000):

      TEAM_ID=6_rings_trader_3 python -m team.trader
      # or: make trader BOT=6_rings_trader_3

- 🤖 `6_rings_trader_4` ($150,000):

      TEAM_ID=6_rings_trader_4 python -m team.trader

## Where to write code

- `trader.py` → `MyTrader.on_tick()` — your edge
- `broker.py` → `MyBroker.spread()/skew()` — quoting and inventory
- `exchange.py` → unused (we hold no exchange license)
- `config.py` → your tunables

Each class derives an `arena` base that handles all plumbing —
see `from arena import Trader, Broker, Exchange`.
