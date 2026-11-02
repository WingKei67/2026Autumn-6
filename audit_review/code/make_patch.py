"""Create a review patch containing only this audit's source/report additions."""
from pathlib import Path
import subprocess,tempfile,json
ROOT=Path(__file__).resolve().parents[2];folder=ROOT/'audit_review'
files=[folder/'conftest.py',folder/'.gitignore',folder/'FILES.md',folder/'REPORT.md',folder/'issues.json']+sorted((folder/'code').glob('*.py'))
chunks=[]
for file in files:
 r=subprocess.run(['git','diff','--no-index','--','/dev/null',str(file.relative_to(ROOT))],cwd=ROOT,capture_output=True,text=True)
 if r.returncode not in (0,1):raise RuntimeError(r.stderr)
 chunks.append(r.stdout)
patch=folder/'changes.patch';patch.write_text(''.join(chunks))
with tempfile.TemporaryDirectory(prefix='alpha_audit_patch_') as tmp:
 r=subprocess.run(['git','apply','--check','--whitespace=error',str(patch)],cwd=tmp,capture_output=True,text=True)
 if r.returncode:raise RuntimeError(r.stderr)
(folder/'results/patch_validation.json').write_text(json.dumps(dict(files=[str(f.relative_to(ROOT)) for f in files],patch_bytes=patch.stat().st_size,git_apply_check_exit_code=r.returncode),indent=2))
print(len(files),'files;',patch.stat().st_size,'bytes; git apply --check passed')
