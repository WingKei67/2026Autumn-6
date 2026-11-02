import json,hashlib
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];archive=ROOT/'audit_review/extracted/2026Autumn-6-fresh'
out=ROOT/'audit_review/results';out.mkdir(parents=True,exist_ok=True)
protocol=json.loads((archive/'alpha_results/study/protocol.json').read_text())
rows=[]
for p,expected in protocol['source_sha256'].items():
 actual=hashlib.sha256((archive/p).read_bytes()).hexdigest()
 rows.append(dict(path=p,expected=expected,actual=actual,match=actual==expected))
result={'files':rows,'all_match':all(x['match'] for x in rows)}
(ROOT/'audit_review/results/source_verification.json').write_text(json.dumps(result,indent=2))
print(len(rows),'files; all match:',result['all_match'])
assert result['all_match']

inputs=[]
for item in json.loads((archive/'alpha_results/study/data_inventory.json').read_text()):
 p=ROOT/'sessions'/Path(item['file']).name
 actual=hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None
 inputs.append(dict(file=str(p),archive_input_present=(archive/'sessions'/p.name).exists(),local_present=p.exists(),expected_sha256=item['sha256'],actual_sha256=actual,matches=actual==item['sha256']))
(out/'historical_inputs.json').write_text(json.dumps(inputs,indent=2))
print(sum(x['matches'] for x in inputs),'of',len(inputs),'external historical inputs checksum matched')
