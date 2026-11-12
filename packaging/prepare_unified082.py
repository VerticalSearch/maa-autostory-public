"""Prepare the final unified release from the verified backend and UI sources."""
import hashlib,json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
base=json.loads((ROOT/'runtime/package-unified080-manifest.json').read_text('utf-8'))
ui=json.loads((ROOT/'runtime/package-ui-081-manifest.json').read_text('utf-8'))
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
assert base['ok'] and ui['ok']
for name,digest in base['source_sha256'].items():
    if name=='desktop_ui.py':digest=ui['source_sha256']
    if name.replace('\\','/')=='autostory/__init__.py':continue
    if sha(ROOT/name)!=digest:raise RuntimeError('Unreviewed source drift: '+name)
source=(ROOT/'packaging/build_unified080.py').read_text('utf-8')
source=source.replace('unified080','unified082').replace('0.8.0','0.8.2').replace('(0,8,0,0)','(0,8,2,0)')
source=source.replace("for path in sorted((SNAP/'autostory').glob('*.py')):",
    "for path in sorted((SNAP/'autostory').glob('*.py'))+[(SNAP/name) for name in ('desktop_ui.py','server.py','worker.py')]:")
source=source.replace("name='autostory' if path.stem=='__init__' else 'autostory.'+path.stem",
    "name=path.stem if path.parent==SNAP else ('autostory' if path.stem=='__init__' else 'autostory.'+path.stem)")
(ROOT/'packaging/build_unified082.py').write_text(source,encoding='utf-8')
print('Verified source union: 0.8.0 backend + 0.8.1 UI, version 0.8.2')
