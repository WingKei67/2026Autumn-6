"""
6 Rings — team configuration.

Your bot IDs and capital allocation were set at registration.
The exchange grants each bot its allocated capital when it connects.

Environment variables:
    TEAM_ID         which bot this process is
    ARENA_TOKEN     your team's secret token (from registration — see .env)
    EXCHANGE_URL    full exchange URL (wss://… for a TLS-hosted arena;
                    set at registration — see .env)
    EXCHANGE_HOST   exchange hostname (default: localhost)
    EXCHANGE_PORT   exchange port     (default: 8765)
"""

import os

try:                       # credentials from `make register` (.env);
    from shared.envfile import load_env   # shell variables still win
    load_env()
except ImportError:        # standalone import without the engine on sys.path
    pass

EXCHANGE_URL = os.environ.get("EXCHANGE_URL") or (
    f"ws://{os.environ.get('EXCHANGE_HOST', 'localhost')}"
    f":{os.environ.get('EXCHANGE_PORT', '8765')}"
)
ARENA_TOKEN = os.environ.get("ARENA_TOKEN", "")

TEAM_NAME  = '6 Rings'
BROKER_IDS = ['6_rings_broker']
TRADER_IDS = ['6_rings_trader_1', '6_rings_trader_2',
              '6_rings_trader_3', '6_rings_trader_4']

# Capital you allocated per bot (informational — the exchange enforces it).
# Revised 2026-10-08 with the instructor: no exchange license.
CAPITAL = {
    '6_rings_broker': 400000,
    '6_rings_trader_1': 150000,
    '6_rings_trader_2': 150000,
    '6_rings_trader_3': 150000,
    '6_rings_trader_4': 150000,
}
