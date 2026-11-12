"""Keep game tasks idle while independent isolated release checks run."""
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from autostory.runtime_state import TaskLock
lock=TaskLock(ROOT/'runtime/alltasks.lock')
if not lock.acquire():raise RuntimeError('A live task is running; finish/stop it normally before validation')
children=[]
try:
    commands=[('source',[sys.executable,'packaging/verify_startup0933.py']),
              ('frozen',[sys.executable,'packaging/verify_startup0933.py','--frozen']),
              ('package',[sys.executable,'-X','utf8','packaging/verify_bundle.py','--app-name','App-0.9.33']),
              ('frames',[sys.executable,'packaging/replay_startup0931.py'])]
    for name,command in commands:
        with (ROOT/f'runtime/validation0933-{name}.log').open('wb') as output:
            child=subprocess.Popen(command,cwd=ROOT,stdout=output,stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        children.append((name,child))
    results={name:child.wait(timeout=180) for name,child in children}
    print(results)
    if any(results.values()):raise RuntimeError('Release validation failed; inspect individual logs')
finally:
    for _,child in children:
        if child.poll() is None:child.terminate();child.wait(timeout=5)
    lock.release()
