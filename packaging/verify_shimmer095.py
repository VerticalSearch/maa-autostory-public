"""Run device/error/state regressions against the exact frozen bytecode."""
import json,sys,unittest
from frozen_logic_loader import ROOT,install

install('App-0.9.5')
import autostory
assert autostory.__version__=='0.9.5'
sys.path.insert(0,str(ROOT/'tests'))
suite=unittest.TestSuite()
for name in ('test_atomic_json','test_capture_helpers','test_capture_device','test_disconnect_resume','test_motion',
             'test_stop093_regressions','test_settle094','test_shimmer095','test_home_observation','test_postconditions','test_recovery_integration','test_resilient_ocr','test_ocr_backend',
             'test_attempt_history','test_logic070','test_retry_flow','test_runner','test_jobs_review',
             'test_stage_fallback','test_safety','test_vision'):
    suite.addTests(unittest.defaultTestLoader.loadTestsFromName(name))
with (ROOT/'runtime/tests-frozen095.log').open('w',encoding='utf-8') as stream:
    result=unittest.TextTestRunner(stream=stream,verbosity=2).run(suite)
report={'ok':result.wasSuccessful(),'tests':result.testsRun,'version':autostory.__version__}
(ROOT/'runtime/shimmer095-frozen-check.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
print(json.dumps(report));raise SystemExit(0 if result.wasSuccessful() else 1)
