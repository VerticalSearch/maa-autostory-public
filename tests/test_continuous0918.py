import copy
import unittest
from unittest.mock import Mock,patch

from autostory.controller import resume_budget
from autostory.run_ledger import optional_limit,limit_reached,reserve_practice,validate_stages
from autostory.recovery import PracticeFailure,StateIntegrityError,RecoveryFailure
from autostory.practice_abort import reject_aborted_practice
from autostory.navigation import Navigator
import test_retry_flow as support
from test_retry_flow import stage
import test_abort_settlement0916 as abort_support
from test_abort_settlement0916 import texts
from test_practice_abort0914 import events


class ContinuousBudgetTests(unittest.TestCase):
    def test_migrate_all_old_caps_without_reset_or_mutating_source(self):
        attempts=[{'job_id':i,'phase':'practice_failed','job_sha':'job','team_sha':'team'} for i in range(3)]
        old={'sanity':125,'limit':None,'count':4,'practice_count':12,'practice_limit':12,
             'stages':{'R8-9:raid':{'practice_count':3,'candidate_limit':3,'attempts':attempts}}}
        untouched=copy.deepcopy(old)
        restored=resume_budget(old,{'max_battles_per_run':None,'max_practice_attempts_per_run':None,'max_candidates_per_stage':None})
        self.assertEqual(old,untouched)
        self.assertEqual((restored['sanity'],restored['count'],restored['practice_count']),(125,4,12))
        self.assertIsNone(restored['practice_limit']);self.assertIsNone(restored['stages']['R8-9:raid']['candidate_limit'])
        reserve_practice(restored,'R8-9:raid',None,4,'new-job','same-team')
        validate_stages(restored)
        self.assertEqual(restored['practice_count'],13)
        self.assertEqual(restored['stages']['R8-9:raid']['practice_count'],4)
        self.assertEqual(old,untouched)

    def test_invalid_old_caps_cannot_be_erased_by_unlimited_setting(self):
        base={'sanity':125,'limit':None,'count':4,'practice_count':12,'practice_limit':12,
              'stages':{'R8-9:raid':{'practice_count':0,'candidate_limit':3,'attempts':[]}}}
        for field,value in [('practice_limit','12'),('practice_limit',False),('practice_count',-1),('candidate_limit','3')]:
            old=copy.deepcopy(base)
            if field=='candidate_limit':old['stages']['R8-9:raid'][field]=value
            else:old[field]=value
            with self.subTest(field=field,value=value),self.assertRaises(StateIntegrityError):
                resume_budget(old,{'max_practice_attempts_per_run':None,'max_candidates_per_stage':None})

    def test_unlimited_is_null_not_large_number_and_finite_caps_still_work(self):
        self.assertIsNone(optional_limit(None));self.assertFalse(limit_reached(10**12,None))
        self.assertEqual(optional_limit(40,1),40);self.assertTrue(limit_reached(40,40))
        for invalid in (-1,False,'unlimited',1.5):
            with self.subTest(invalid=invalid),self.assertRaises(StateIntegrityError):optional_limit(invalid)


class ContinuousFlowTests(unittest.TestCase):
    setUp=support.RetryFlowTests.setUp
    execute=support.RetryFlowTests.execute

    def test_fourteen_candidates_cross_both_old_caps_then_practice_before_formal(self):
        ledger={'sanity':125,'limit':None,'count':4,'practice_count':12,'practice_limit':None}
        result=self.execute([stage('DV-1',list(range(1,15)))],
            failures={(i,True):PracticeFailure('confirmed loss') for i in range(1,14)},ledger=ledger,cap=None)
        self.assertIsNone(result['error'])
        self.assertEqual([(c[1],c[2]) for c in result['calls']],[(i,True) for i in range(1,15)]+[(14,False)])
        self.assertEqual((ledger['count'],ledger['sanity'],ledger['practice_count']),(5,119,26))
        self.assertEqual(result['recover'].call_count,13)
        self.assertIsNone(ledger['stages']['DV-1:normal']['candidate_limit'])

    def test_exhausted_stage_continues_next_and_resume_does_not_repeat_failed_jobs(self):
        ledger={'sanity':125,'limit':None,'count':4,'practice_count':12,'practice_limit':None}
        first=self.execute([stage('DV-1',[1,2,3,4])],
            failures={(i,True):PracticeFailure('loss') for i in range(1,5)},ledger=ledger,cap=None)
        result=self.execute([stage('DV-1',[1,2,3,4]),stage('DV-2',[5])],ledger=ledger,cap=None,history=first['history'])
        self.assertIsNone(result['error'])
        self.assertEqual([(c[0],c[1],c[2]) for c in result['calls']],[('DV-2',5,True),('DV-2',5,False)])
        self.assertEqual((ledger['count'],ledger['sanity'],ledger['practice_count']),(5,119,17))

    def test_unlimited_does_not_bypass_user_stop_or_sanity_budget(self):
        ledger={'sanity':5,'limit':None,'count':4,'practice_count':50,'practice_limit':None}
        result=self.execute([stage('DV-1',[1],cost=6)],ledger=ledger,cap=None)
        self.assertEqual(result['calls'],[])
        ledger['sanity']=125
        result=self.execute([stage('DV-1',[1,2])],failures={(1,True):InterruptedError('stop')},ledger=ledger,cap=None)
        self.assertIsInstance(result['error'],InterruptedError)
        self.assertEqual(len(result['calls']),1);result['recover'].assert_not_called()
        self.assertEqual(ledger['sanity'],125)


class ContinuousRecoveryTests(unittest.TestCase):
    setUp=abort_support.AbortSettlementTests.setUp
    navigator=abort_support.AbortSettlementTests.navigator

    def test_initial_loading_waits_for_exact_map_and_records_failure(self):
        nav=self.navigator([[]]*4+[texts('map')])
        with self.assertRaises(PracticeFailure):reject_aborted_practice(nav,'8','R8-9','raid',events())
        nav.d.tap.assert_not_called();nav.d.back.assert_not_called()

    def test_initial_unsettled_frame_enters_observation_only_recovery(self):
        from autostory.motion import UnsettledFrameError
        nav=self.navigator([])
        nav.observe.side_effect=[UnsettledFrameError('loading'),(None,texts('map'))]
        with self.assertRaises(PracticeFailure):reject_aborted_practice(nav,'8','R8-9','raid',events())
        nav.d.tap.assert_not_called();nav.d.back.assert_not_called()

    def test_initial_unknown_is_bounded_no_input_or_success(self):
        nav=self.navigator([[]]*49)
        with self.assertRaises(RecoveryFailure):reject_aborted_practice(nav,'8','R8-9','raid',events())
        self.assertEqual(nav.observe.call_count,49)
        nav.d.tap.assert_not_called();nav.d.back.assert_not_called();nav.find.assert_not_called()

    def test_home_checks_result_of_last_back_without_extra_keypress(self):
        nav=object.__new__(Navigator);nav.d=Mock()
        nav.observe=Mock(side_effect=[(None,[])]*7+[(None,texts('采购中心','公开招募'))])
        nav.home();self.assertEqual(nav.d.back.call_count,7)


if __name__=='__main__':unittest.main()
