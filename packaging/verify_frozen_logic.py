import sys
import unittest
from frozen_logic_loader import ROOT,install

if __name__=='__main__':
    install()
    sys.path.insert(0,str(ROOT/'tests'))
    tests=unittest.TestSuite()
    for name in ('test_attempt_history','test_retry_flow','test_runner','test_disconnect_resume'):
        tests.addTests(unittest.defaultTestLoader.loadTestsFromName(name))
    with (ROOT/'runtime/tests-frozen-logic.log').open('w',encoding='utf-8') as stream:
        result=unittest.TextTestRunner(stream=stream,verbosity=2).run(tests)
    print(f'Frozen logic: {result.testsRun} tests; success={result.wasSuccessful()}')
    raise SystemExit(0 if result.wasSuccessful() else 1)
