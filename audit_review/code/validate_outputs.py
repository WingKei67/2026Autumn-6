"""Final output completeness and provenance checks; does not run trades."""
import json,hashlib,socket
from pathlib import Path
from collections import Counter
from network_guard import install
ROOT=Path(__file__).resolve().parents[2];out=ROOT/'audit_review/results'
install();s=socket.socket();blocked=False
try:s.connect(('203.0.113.1',1))
except RuntimeError:blocked=True
finally:s.close()
assert blocked  # connect implementation rejects before any kernel/network call
allrows=[];counts={};code_hash=hashlib.sha256((ROOT/'audit_review/code/audit_study.py').read_bytes()).hexdigest()
for stage,expected in [('reproduce',900),('fresh',900),('stress',2000),('paired_corrections',500)]:
 rows=[json.loads(x) for x in (out/(stage+'.jsonl')).open()]
 assert len(rows)==expected
 cells=Counter((r['week'],r['candidate'],r['stress']) for r in rows);assert all(n==50 for n in cells.values())
 protocol=json.loads((out/(stage+'_protocol.json')).read_text());assert protocol.get('code_sha256',protocol.get('study_code_sha256'))==code_hash
 counts[stage]=len(rows);allrows+=rows
checks={key:sum(r[key] for r in allrows) for key in ['liquidations','position_violations','team_position_violations','same_tick_fills','missing_fill_provenance']}
assert not any(checks.values())
result=dict(trials=counts,total_trials=len(allrows),unique_seed_ids=len({r['seed'] for r in allrows}),fresh_seed_ids=list(range(3000,3050)),performance_diagnostics=checks,max_per_symbol_shares=max(r['max_abs_position'] for r in allrows),max_reserved_team_symbol_shares=max(r['max_team_position'] for r in allrows),nonloopback_connect_guard_verified=blocked,study_code_unchanged=True)
(out/'final_validation.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))
