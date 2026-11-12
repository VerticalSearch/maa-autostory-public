"""Run the release regressions against the exact frozen 0.9.26 bytecode."""
import json,sys,unittest
from frozen_logic_loader import ROOT,install

install('App-0.9.26',extra_modules=('worker',))
import autostory
assert autostory.__version__=='0.9.26'
import autostory.nav_hints, autostory.collection_index
sys.path.insert(0,str(ROOT/'tests'))
suite=unittest.TestSuite()
for name in ('test_collection_index0926','test_nav_hints0925','test_recovery0924','test_safety','test_motion','test_settle094','test_shimmer095',
             'test_scan0922','test_scan_progress','test_capture_helpers','test_capture_device','test_capture0923',
             'test_disconnect_resume','test_home_observation','test_home096','test_postconditions',
             'test_recovery_integration','test_checkpoint_recovery','test_recovery_wait0913','test_stop093_regressions',
             'test_atomic_json','test_network098','test_worker_lock','test_stage_fallback','test_raid097',
             'test_logic070','test_flow','test_ui_events'):
    suite.addTests(unittest.defaultTestLoader.loadTestsFromName(name))
with (ROOT/'runtime/tests-frozen0926.log').open('w',encoding='utf-8') as stream:
    result=unittest.TextTestRunner(stream=stream,verbosity=2).run(suite)
report={'ok':result.wasSuccessful(),'tests':result.testsRun,'version':autostory.__version__}
(ROOT/'runtime/recovery0926-frozen-check.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
print(json.dumps(report));raise SystemExit(0 if result.wasSuccessful() else 1)

