import json
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np

from autostory import navigation_recovery as recovery
from autostory.navigation import StageNotFound
from autostory.motion import UnsettledFrameError
from autostory.controller import Runner


class NavigationRecoveryTests(unittest.TestCase):
    def setUp(self):
        stack=ExitStack();self.addCleanup(stack.close)
        self.root=Path(stack.enter_context(tempfile.TemporaryDirectory()))
        stack.enter_context(patch.object(recovery,'DATA',self.root))
        self.stop=stack.enter_context(patch.object(recovery,'check_stop'))
        stack.enter_context(patch.object(recovery,'log'))
        stack.enter_context(patch.object(recovery.workflow,'update'))
        self.page=stack.enter_context(patch.object(recovery,'navigation_regions',return_value=('map',())))
        self.nav=Mock()
        self.nav.observe.return_value=(np.zeros((720,1280,3),np.uint8),[])
        self.row={'code':'5-8','mode':'raid','state':'uncleared'}

    def test_reenters_once_then_uses_new_lookup(self):
        self.nav.find.side_effect=[StageNotFound('missing'),self.row]
        self.assertEqual(recovery.locate(self.nav,'5-8','raid'),self.row)
        self.nav.enter.assert_called_once_with('5')
        self.assertEqual(self.nav.find.call_count,2)
        self.nav.d.tap.assert_not_called()
        report=json.loads(next((self.root/'navigation-recovery').glob('*.json')).read_text('utf-8'))
        self.assertEqual(report['status'],'recovered')
        self.assertTrue(Path(report['observations'][0]['screenshot']).exists())

    def test_repeat_failure_is_bounded(self):
        self.nav.find.side_effect=StageNotFound('missing')
        with self.assertRaises(StageNotFound):recovery.locate(self.nav,'5-8','raid')
        self.assertEqual(self.nav.find.call_count,2)
        self.nav.enter.assert_called_once()

    def test_failed_fresh_motion_gate_never_reenters(self):
        self.nav.find.side_effect=UnsettledFrameError('moving')
        self.nav.observe.side_effect=UnsettledFrameError('still moving')
        with self.assertRaises(UnsettledFrameError):recovery.locate(self.nav,'5-8','raid')
        self.nav.enter.assert_not_called()

    def test_unknown_page_or_settlement_never_receives_recovery_input(self):
        self.nav.find.side_effect=StageNotFound('missing')
        for page,texts in ((None,[]),('map',[{'text':'行动结束'}]),('map',[{'text':'是否花费源石兑换合成玉'}])):
            self.page.return_value=(page,())
            self.nav.observe.return_value=(np.zeros((720,1280,3),np.uint8),texts)
            with self.assertRaises(RuntimeError):recovery.locate(self.nav,'5-8','raid')
        self.nav.enter.assert_not_called()

    def test_stop_connection_and_generic_error_are_not_retried(self):
        for error in (InterruptedError('stop'),RuntimeError('input uncertain'),OSError('connection')):
            self.nav.find.reset_mock();self.nav.find.side_effect=error
            with self.assertRaises(type(error)):recovery.locate(self.nav,'5-8','raid')
            self.nav.find.assert_called_once();self.nav.enter.assert_not_called()
        self.nav.observe.assert_not_called()

    def test_stop_before_reentry_preserves_inputs(self):
        self.nav.find.side_effect=StageNotFound('missing')
        self.stop.side_effect=InterruptedError('stop')
        with self.assertRaises(InterruptedError):recovery.locate(self.nav,'5-8','raid')
        self.nav.enter.assert_not_called();self.nav.observe.assert_not_called()


class NavigationBudgetTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.root=Path(temp.name)

    def test_initial_navigation_failure_does_not_reserve_attempt_or_sanity(self):
        from test_retry_flow import RetryFlowTests,stage
        ledger={'sanity':53,'limit':None,'count':8,'practice_count':23,'practice_limit':None}
        with patch('autostory.controller.locate',side_effect=StageNotFound('missing')):
            result=RetryFlowTests.execute(self,[stage('DV-1',[1])],ledger=ledger)
        self.assertIsInstance(result['error'],StageNotFound)
        self.assertEqual(result['calls'],[])
        self.assertEqual((ledger['sanity'],ledger['count'],ledger['practice_count']),(53,8,23))
        self.assertEqual(ledger['stages']['DV-1:normal']['attempts'],[])

    def test_formal_navigation_failure_preserves_spent_practice_without_paid_reservation(self):
        from test_retry_flow import RetryFlowTests,stage
        ledger={'sanity':53,'limit':None,'count':8,'practice_count':23,'practice_limit':None}
        with patch('autostory.controller.locate',side_effect=[{'state':'uncleared'},StageNotFound('missing')]):
            result=RetryFlowTests.execute(self,[stage('DV-1',[1])],ledger=ledger)
        self.assertIsInstance(result['error'],StageNotFound)
        self.assertEqual([call[2] for call in result['calls']],[True])
        self.assertEqual((ledger['sanity'],ledger['count'],ledger['practice_count']),(53,8,24))

    def test_prepared_page_mismatch_never_dispatches(self):
        runner=Runner(Mock(),Mock(),history=Mock())
        runner.nav.observe.return_value=(None,[{'text':'开始行动'}])
        runner.nav._detail_identity.return_value=False
        with patch('autostory.controller.assert_execution_unlocked'):
            with self.assertRaisesRegex(RuntimeError,'关卡或难度'):
                runner.battle('5-8',self.root/'job.json',False,'raid',prepared={'state':'uncleared'})
        self.assertIs(runner.formal_dispatched,False)
        runner.core.run.assert_not_called();runner.nav.click_text.assert_not_called()


if __name__=='__main__':unittest.main()
