# AlgoArena — student team Makefile
# The arena address and your token live in .env (created by make register).

-include .env
PYTHON ?= $(if $(wildcard .venv310/bin/python),.venv310/bin/python,python3)
export

ARENA_URL ?= https://arena.example.edu
# The engine lives under engine/ — every target needs it importable.
export PYTHONPATH := engine:.
# The season's week scenarios (the rules and event calendar of each Friday
# block) ship under season/ so the season simulator runs offline.
export PYTHONPATH := $(CURDIR)/engine:$(CURDIR)
export SEASON_DIR := season
export SEASON_PERSIST := false

install:
	$(PYTHON) -m pip install -r requirements.txt

register:
	$(PYTHON) scripts/create_team.py --remote $(ARENA_URL)

# Prove your connection end-to-end (registration code needed: CODE=...)
test-remote:
	$(PYTHON) scripts/test_remote.py --arena $(ARENA_URL) --code $(CODE)

# Run a bot seat: make trader BOT=<id>  — TEAM_ID=<id> works too.
TEAM_ID ?= $(BOT)

trader:
	TEAM_ID=$(TEAM_ID) $(PYTHON) -m team.trader

broker:
	TEAM_ID=$(TEAM_ID) $(PYTHON) -m team.broker

exchange:
	$(PYTHON) -m exchange.server

sim:
	$(PYTHON) tests/sim_session.py

# Simulate a graded week offline against the house bots: make season WEEK=3
WEEK ?= 1
season:
	$(PYTHON) scripts/season_sim.py --weeks $(WEEK)

# Replay a recorded block into a local dashboard: make replay FILE=sessions/<file>
replay:
	$(PYTHON) scripts/replay_session.py $(FILE) --speed 5

test:
	$(PYTHON) -m pytest tests/ -q

.PHONY: install register trader broker exchange sim season replay test
