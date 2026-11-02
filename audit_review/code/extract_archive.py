"""Extract audit inputs without overwriting an existing baseline."""
import argparse,zipfile,hashlib,json
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--archive',type=Path,default=Path('../alpha_review_light.zip'));p.add_argument('--output',type=Path,default=Path('audit_review/extracted'));a=p.parse_args()
if a.output.exists():raise SystemExit('Output already exists; use another --output to preserve baseline')
digest=hashlib.sha256(a.archive.read_bytes()).hexdigest()
if digest!='1fe1d222c67447ace8f14ba5bcc2a9a8e1ce91e3b0303a5d344ba84f23f7cf76':raise SystemExit('Archive checksum differs from the audited input')
root=a.output.resolve()
with zipfile.ZipFile(a.archive) as z:
 for member in z.infolist():
  target=(root/member.filename).resolve()
  if root not in target.parents and target!=root:raise ValueError('Unsafe archive path')
  if (member.external_attr>>16)&0o170000==0o120000:raise ValueError('Archive symlink is not supported')
 members=[dict(path=m.filename,bytes=m.file_size) for m in z.infolist()]
 z.extractall(root)
(root.parent/'archive_manifest.json').write_text(json.dumps(dict(archive=str(a.archive),sha256=digest,files=members),indent=2))
print('Archive SHA256:',hashlib.sha256(a.archive.read_bytes()).hexdigest())
