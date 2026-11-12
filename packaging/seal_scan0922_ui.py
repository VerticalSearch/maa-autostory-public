"""Attach the independently audited external control page to the release manifest."""
import hashlib,json,shutil
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
path=ROOT/'runtime/package-scan0922-manifest.json'
manifest=json.loads(path.read_text('utf-8-sig'))
audit_path=ROOT/'runtime/web0922-browser-audit.json'
audit=json.loads(audit_path.read_text('utf-8-sig'))
page=ROOT/'web/index.html'
assert audit['ok'] and audit['passed']==72 and audit_path.stat().st_mtime_ns>=page.stat().st_mtime_ns
assert manifest['exe_sha256']==hashlib.sha256((ROOT/'App-0.9.22/MAA自动推图助手.exe').read_bytes()).hexdigest()
shutil.copy2(page,Path(manifest['snapshot'])/'index.html')
manifest['ui_sha256']=hashlib.sha256(page.read_bytes()).hexdigest()
manifest['ui_audit']='runtime/web0922-browser-audit.json'
path.write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'ui_sha256':manifest['ui_sha256'],'browser_checks':audit['passed']}))
