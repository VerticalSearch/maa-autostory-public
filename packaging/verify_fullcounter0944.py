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
    install('App-0.9.44',extra_modules=('worker',))
import autostory
from autostory import startup, startup_screens, completion, story_zones, navigation, controller, siracusa, zone_icons, home_observation, stage_header, dv_special, cw_morse, sn_observation, map_traversal, navigation_recovery, sn_detail, startup_controls, resource_preflight, core, sn_sparse_map, skill_training, navigation_errors, activity_counter, activity_completion, collection_progress, collection_completion
if args.frozen:
    for module in (autostory,startup,startup_screens,completion,story_zones,navigation,controller,siracusa,zone_icons,home_observation,stage_header,dv_special,cw_morse,sn_observation,map_traversal,navigation_recovery,sn_detail,startup_controls,resource_preflight,core,sn_sparse_map,skill_training,navigation_errors,activity_counter,activity_completion,collection_progress,collection_completion):
        assert module.__spec__.loader.__class__.__name__=='FrozenLoader'
    assert autostory.__version__=='0.9.44'
FILES=('activity.log','status.json','worker.json','STOP','workflow.json','startup.json','plan.json','progress.json',
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
with (ROOT/f'runtime/tests-fullcounter0944-{kind}.log').open('w',encoding='utf-8') as stream:
    result=unittest.TextTestRunner(stream=stream,verbosity=2).run(suite)
after=snapshot();touched=[name for name in before if before[name]!=after[name]]
report=dict(ok=result.wasSuccessful() and not touched,version=autostory.__version__,
            tests=result.testsRun,failures=len(result.failures),errors=len(result.errors),
            skipped=len(result.skipped),excluded=sorted(excluded),live_records_touched=touched)
(ROOT/f'runtime/fullcounter0944-{kind}-check.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
print(json.dumps(report));raise SystemExit(0 if report['ok'] else 1)
