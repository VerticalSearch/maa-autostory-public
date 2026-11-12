"""Start the loopback service without a console or opening any desktop window."""
import pathlib
import subprocess
import sys
import urllib.request
import json

root=pathlib.Path(__file__).resolve().parent
cfg=json.loads((root/'settings.json').read_text('utf-8'))
url=f'http://127.0.0.1:{cfg.get("port",18741)}/api/state'
try:
    with urllib.request.urlopen(url,timeout=2) as r:state=json.load(r)
    if 'operators' in state:raise SystemExit(0)
except (OSError,ValueError):pass
(root/'runtime').mkdir(exist_ok=True)
with (root/'runtime/server.log').open('ab') as out:
    subprocess.Popen([sys.executable,str(root/'server.py')],cwd=root,stdout=out,stderr=out,
                     creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
