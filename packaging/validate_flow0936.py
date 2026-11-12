"""Hold the task lock across isolated source, frozen and package checks."""
from pathlib import Path
import subprocess,sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from autostory.runtime_state import TaskLock
lock=TaskLock(ROOT/'runtime/alltasks.lock')
if not lock.acquire():raise RuntimeError('A task is running')
children=[]
try:
    commands=[('source',[sys.executable,'packaging/verify_flow0936.py']),
              ('frozen',[sys.executable,'packaging/verify_flow0936.py','--frozen']),
              ('package',[sys.executable,'-X','utf8','packaging/verify_bundle.py','--app-name','App-0.9.36'])]
    for name,command in commands:
        with (ROOT/f'runtime/validation0936-{name}.log').open('wb') as output:
            child=subprocess.Popen(command,cwd=ROOT,stdout=output,stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        children.append((name,child))
    results={name:child.wait(timeout=180) for name,child in children}
    print(results)
    if any(results.values()):raise RuntimeError('Validation failed; inspect logs')
finally:
    for _,child in children:
        if child.poll() is None:child.terminate();child.wait(timeout=5)
    lock.release()
