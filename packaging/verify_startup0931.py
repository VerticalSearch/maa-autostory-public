"""Run source or exact shipped regression suite, guarding live account files."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import unittest

ROOT=Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser();parser.add_argument('--frozen',action='store_true');args=parser.parse_args()
sys.path[:0]=[str(ROOT),str(ROOT/'tests')]
if args.frozen:
    from frozen_logic_loader import install
    install('App-0.9.31',extra_modules=('worker',))
import autostory
from autostory import startup, startup_screens
if args.frozen:
    for module in (autostory,startup,startup_screens):
        assert module.__spec__.loader.__class__.__name__=='FrozenLoader'
    assert autostory.__version__=='0.9.31'
FILES=('status.json','worker.json','STOP','workflow.json','startup.json','plan.json','progress.json',
       'campaign-results.json','operators.json','attempt-history.json','run-results.json',
       'scan-report.json','nav-hints.json','collection-index.json','../settings.json')
def snapshot():
    return {name:hashlib.sha256((ROOT/'runtime'/name).read_bytes()).hexdigest()
            if (ROOT/'runtime'/name).exists() else None for name in FILES}
before=snapshot()
excluded={'test_server_state'} if args.frozen else set()
names=sorted(p.stem for p in (ROOT/'tests').glob('test_*.py') if p.stem not in excluded)
suite=unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromName(name) for name in names)
kind='frozen' if args.frozen else 'source'
with (ROOT/f'runtime/tests-startup0931-{kind}.log').open('w',encoding='utf-8') as stream:
    result=unittest.TextTestRunner(stream=stream,verbosity=2).run(suite)
after=snapshot();touched=[name for name in before if before[name]!=after[name]]
report=dict(ok=result.wasSuccessful() and not touched,version=autostory.__version__,
            tests=result.testsRun,failures=len(result.failures),errors=len(result.errors),
            excluded=sorted(excluded),live_records_touched=touched)
(ROOT/f'runtime/startup0931-{kind}-check.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
print(json.dumps(report));raise SystemExit(0 if report['ok'] else 1)
