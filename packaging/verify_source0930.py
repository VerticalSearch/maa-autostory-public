"""Source regression suite with guards against modifying live account records."""
import hashlib
import json
from pathlib import Path
import sys
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'tests')]
FILES=('runtime/status.json','runtime/worker.json','runtime/STOP','runtime/workflow.json',
       'runtime/plan.json','runtime/progress.json','runtime/campaign-results.json',
       'runtime/operators.json','runtime/attempt-history.json','runtime/run-results.json',
       'runtime/scan-report.json','runtime/nav-hints.json','runtime/collection-index.json','settings.json')
def snapshot():
    return {name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() if (ROOT/name).exists() else None for name in FILES}

before=snapshot()
suite=unittest.defaultTestLoader.discover(str(ROOT/'tests'))
with (ROOT/'runtime/tests-recovery0930-source.log').open('w',encoding='utf-8') as stream:
    result=unittest.TextTestRunner(stream=stream,verbosity=2).run(suite)
after=snapshot()
touched=[name for name in FILES if before[name]!=after[name]]
report={'ok':result.wasSuccessful() and not touched,'tests':result.testsRun,'failures':len(result.failures),
        'errors':len(result.errors),'skipped':len(result.skipped),'live_records_touched':touched}
(ROOT/'runtime/recovery0930-source-check.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
print(json.dumps(report))
raise SystemExit(0 if report['ok'] else 1)
