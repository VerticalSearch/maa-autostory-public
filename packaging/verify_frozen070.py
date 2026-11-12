import sys
import unittest
from frozen_logic_loader import ROOT,install

if __name__=='__main__':
    install('App-0.7.0')
    sys.path.insert(0,str(ROOT/'tests'))
    suite=unittest.TestSuite()
    for name in ('test_attempt_history','test_retry_flow','test_runner','test_disconnect_resume','test_logic070','test_jobs_review'):
        suite.addTests(unittest.defaultTestLoader.loadTestsFromName(name))
    with (ROOT/'runtime/tests-frozen070.log').open('w',encoding='utf-8') as stream:
        result=unittest.TextTestRunner(stream=stream,verbosity=2).run(suite)
    print(f'Frozen 0.7.0: {result.testsRun} tests; success={result.wasSuccessful()}')
    raise SystemExit(0 if result.wasSuccessful() else 1)
