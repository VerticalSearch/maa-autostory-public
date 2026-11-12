"""Run the regression suite against the exact frozen 0.9.30 bytecode.

test_server_state is excluded (as in every earlier frozen check): it imports a
stub project in-process, and the frozen meta-path loader would substitute the
shipped modules, pointing that test server at the live runtime. It runs in the
source suite. A guard fails the check if any live record changes during the run.
"""
import hashlib,json,sys,unittest
from frozen_logic_loader import ROOT,install

GUARDED=('status.json','worker.json','STOP','workflow.json','plan.json','progress.json','campaign-results.json',
         'operators.json','attempt-history.json','run-results.json','scan-report.json','nav-hints.json','collection-index.json')
EXCLUDED={'test_server_state'}

def snapshot():
    out={}
    for name in GUARDED+('../settings.json',):
        path=ROOT/'runtime'/name
        out[name]=hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None
    return out

def flatten(suite):
    for item in suite:
        if isinstance(item,unittest.TestSuite):yield from flatten(item)
        else:yield item

install('App-0.9.30',extra_modules=('worker',))
import autostory
assert autostory.__version__=='0.9.30'
import autostory.scanning, autostory.scan_progress, worker
for module in (autostory.scanning,autostory.scan_progress,worker):
    assert module.__spec__.loader.__class__.__name__=='FrozenLoader',module
from autostory.terminal_navigation import TerminalNavigationError
assert issubclass(TerminalNavigationError,RuntimeError)
sys.path.insert(0,str(ROOT/'tests'))
before=snapshot()
names=sorted(p.stem for p in (ROOT/'tests').glob('test_*.py') if p.stem not in EXCLUDED)
suite=unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromName(name) for name in names)
assert not any(type(t).__module__ in EXCLUDED for t in flatten(suite))
with (ROOT/'runtime/tests-frozen0930.log').open('w',encoding='utf-8') as stream:
    result=unittest.TextTestRunner(stream=stream,verbosity=2).run(suite)
after=snapshot()
touched=sorted(name for name in before if before[name]!=after[name])
frozen=sorted(name for name,module in sys.modules.items()
              if (name=='worker' or name.startswith('autostory'))
              and getattr(getattr(module,'__spec__',None),'loader',None).__class__.__name__=='FrozenLoader')
ok=result.wasSuccessful() and not touched
report={'ok':ok,'tests':result.testsRun,'modules':len(names),'excluded':sorted(EXCLUDED),
        'version':autostory.__version__,'failures':len(result.failures),'errors':len(result.errors),
        'frozen_modules':len(frozen),'live_records_touched':touched}
(ROOT/'runtime/recovery0930-frozen-check.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
print(json.dumps(report));raise SystemExit(0 if ok else 1)
