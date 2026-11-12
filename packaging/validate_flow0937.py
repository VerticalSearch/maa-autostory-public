"""Offline source and candidate EXE bytecode checks; never launch the EXE."""
from pathlib import Path
import subprocess,sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from autostory.runtime_state import TaskLock
lock=TaskLock(ROOT/'runtime/alltasks.lock')
if not lock.acquire():raise RuntimeError('A task is running')
children=[]
try:
    commands=(('source',[]),) if '--source-only' in sys.argv else (('source',[]),('frozen',['--frozen']))
    for name,extra in commands:
        with (ROOT/f'runtime/validation0937-{name}.log').open('wb') as output:
            child=subprocess.Popen([sys.executable,'packaging/verify_flow0937.py',*extra],cwd=ROOT,
                stdout=output,stderr=subprocess.STDOUT,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        children.append((name,child))
    results={name:child.wait(timeout=180) for name,child in children}
    print(results)
    if any(results.values()):raise RuntimeError('Offline validation failed; inspect logs')
finally:
    for _,child in children:
        if child.poll() is None:child.terminate();child.wait(timeout=5)
    lock.release()
