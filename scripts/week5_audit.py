"""Read-only evidence audit. Unknown/missing evidence is reported explicitly."""
import argparse
import json
import re
from datetime import datetime
from pathlib import Path

SEATS = ('6_rings_trader_1', '6_rings_trader_2', '6_rings_broker')


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--logs', default='logs')
    ap.add_argument('--date', default=datetime.now().strftime('%Y%m%d'))
    args = ap.parse_args()
    for seat in SEATS:
        files = sorted(Path(args.logs).glob(f'{seat}_{args.date}_*.log'))
        if not files:
            print(f'{seat}: UNKNOWN — no matching logs')
            continue
        body = '\n'.join(f.read_text(errors='replace') for f in files)
        def count(pattern, text=body): return len(re.findall(pattern, text, re.IGNORECASE))
        maxpos = {}; maxteam = {}; gross = []; teamgross = []
        for line in body.splitlines():
            if 'RISK_STATE ' not in line: continue
            try: state = json.loads(line.split('RISK_STATE ', 1)[1])
            except ValueError: continue
            for key, maxima in [('positions', maxpos), ('team_positions', maxteam)]:
                for sym, qty in state.get(key, {}).items():
                    maxima[sym] = max(maxima.get(sym, 0), abs(qty))
            if state.get('gross') is not None: gross.append(state['gross'])
            marks = state.get('marks', {}); team = state.get('team_positions', {})
            if team and all(marks.get(s, 0) > 0 for s, q in team.items() if q):
                teamgross.append(sum(abs(q) * marks.get(s, 0) for s, q in team.items()))
        print(f'{seat}: connect={count(r"CONNECT #|Connected to .* as ")} '
              f'week5={count(r"RISK WEEK 05|SESSION WEEK 05|scenario switched: week=5")} '
              f'halts={count(r"RISK (?:broker )?HALT|RISK MARKET HALT")} '
              f'resumes={count(r"RISK (?:broker )?RESUMED|RISK MARKET RESUMED")} '
              f'post_resume_ack={count(r"RESUME CONFIRMED")}')
        print(f'  max_abs_positions={maxpos or "UNKNOWN"} max_team_positions={maxteam or "UNKNOWN"}')
        print(f'  max_gross={max(gross) if gross else "UNKNOWN"} '
              f'max_team_net_positions_gross={max(teamgross) if teamgross else "UNKNOWN"}')
        risk_rejects = count(r'\[(?:POSITION_LIMIT|LIQUIDATED|LEVERAGE_LIMIT|INSUFFICIENT_CASH|INSUFFICIENT_MARGIN)\]')
        print(f'  risk_rejects={risk_rejects} '
              f'liquidation_notices={count(r"LIQUIDATION notice|Session event: LIQUIDATION|OUR account liquidated|LIQUIDATED")} '
              f'disconnects={count(r"DISCONNECT|Exchange connection closed|Exchange closed the connection")} '
              f'crashes={count(r"Traceback|Unexpected error|unexpected error")}')
        if not count(r'RESUME CONFIRMED'):
            print('  No accepted post-resume order proven in these logs.')
    print('Positions are observed snapshots; pending-order bounds use independent seat caps: 150+150+20=320 per symbol.')
    print('Missing logs or samples cannot prove absence of violations. Cross-venue/extra-seat activity is outside these bounds.')


if __name__ == '__main__':
    main()
