"""Record the approved hash of intentionally modified production files in tests/production_pins.json.
Run only after tests + rehearsal pass:  python -m research.update_pins "reason text"
The test (tests/test_alpha.py) requires the current file hash to equal the recorded pin, so ANY later edit
without re-pinning (and a documented reason) fails the suite."""
import hashlib, json, sys, time
from pathlib import Path
root = Path(__file__).resolve().parents[1]
pins_path = root / 'tests/production_pins.json'
pins = json.loads(pins_path.read_text()) if pins_path.exists() else {}
reason = sys.argv[1]
for f in ('team/trader.py', 'team/event2.py'):
    sha = hashlib.sha256((root / f).read_bytes()).hexdigest()
    hist = pins.setdefault(f, {'history': []})['history']
    if not hist or hist[-1]['sha256'] != sha:
        hist.append({'sha256': sha, 'date': time.strftime('%Y-%m-%d'), 'base_commit': 'e4d8c54', 'reason': reason,
                     'approval': 'User approved Trader 1 production-code changes in the isolated research branch (2026-11-02); no deployment.'})
pins_path.write_text(json.dumps(pins, indent=1))
print(json.dumps({k: v['history'][-1]['sha256'] for k, v in pins.items()}, indent=1))
