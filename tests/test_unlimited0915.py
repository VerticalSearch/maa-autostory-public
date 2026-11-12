import copy
import unittest
from contextlib import ExitStack
from unittest.mock import Mock,patch

from autostory.controller import Runner,formal_limit,formal_limit_reached,resume_budget
from autostory.recovery import PracticeBudgetExhausted
import test_retry_flow as retry_support
from test_retry_flow import stage


class UnlimitedBudgetTests(unittest.TestCase):
    def test_missing_and_null_setting_mean_unlimited(self):
        self.assertIsNone(formal_limit({}))
        self.assertIsNone(formal_limit({'max_battles_per_run':None}))
        self.assertFalse(formal_limit_reached({'limit':None,'count':1000000}))

    def test_finite_and_zero_caps_remain_explicit(self):
        for limit in (0,3):
            with self.subTest(limit=limit):
                self.assertEqual(formal_limit({'max_battles_per_run':limit}),limit)
                self.assertTrue(formal_limit_reached({'limit':limit,'count':limit}))
        self.assertFalse(formal_limit_reached({'limit':3,'count':2}))

    def test_invalid_setting_is_not_silently_unlimited(self):
        for value in (-1,False,True,'null','3',3.5,[],{}):
            with self.subTest(value=value),self.assertRaises(ValueError):
                formal_limit({'max_battles_per_run':value})

    def test_old_cap_is_lifted_without_resetting_budget_or_attempts(self):
        old={'sanity':146,'limit':3,'count':3,'practice_count':6,'practice_limit':12,
             'stages':{'R8-7:raid':{'practice_count':1,'candidate_limit':3,'attempts':[]}}}
        original=copy.deepcopy(old)
        restored=resume_budget(old,{'max_battles_per_run':None,'max_sanity_per_run':200})
        self.assertEqual(restored,{**old,'limit':None})
        self.assertEqual(old,original)

    def test_unlimited_checkpoint_resumes_above_old_three_cap(self):
        old={'sanity':74,'limit':None,'count':7,'practice_count':10,'practice_limit':12}
        self.assertEqual(resume_budget(old,{'max_battles_per_run':None}),old)

    def test_finite_setting_or_missing_setting_preserves_old_cap(self):
        old={'sanity':146,'limit':3,'count':3}
        for config in ({},{'max_battles_per_run':3}):
            self.assertEqual(resume_budget(old,config),old)

    def test_removing_limit_does_not_accept_corrupt_checkpoint(self):
        good={'sanity':146,'limit':3,'count':3}
        invalid=[None,[],{}, {'sanity':146,'count':3}]
        invalid += [{**good,**change} for change in (
            {'limit':False},{'limit':-1},{'limit':'3'},{'count':4},
            {'count':-1},{'count':True},{'sanity':-1},{'sanity':'146'})]
        for old in invalid:
            with self.subTest(old=old),self.assertRaisesRegex(RuntimeError,'检查点'):
                resume_budget(old,{'max_battles_per_run':None})


class UnlimitedBattleFlowTests(unittest.TestCase):
    setUp=retry_support.RetryFlowTests.setUp
    execute=retry_support.RetryFlowTests.execute

    def test_more_than_three_formals_still_practice_first_and_obey_sanity(self):
        ledger={'sanity':200,'limit':None,'count':0}
        result=self.execute([stage(f'DV-{i}',[i],cost=35) for i in range(1,7)],ledger=ledger)
        self.assertIsNone(result['error'])
        self.assertEqual([(call[1],call[2]) for call in result['calls']],
                         [(i,trial) for i in range(1,6) for trial in (True,False)])
        self.assertEqual((ledger['count'],ledger['sanity'],ledger['practice_count']),(5,25,5))
        self.assertTrue(any(r['code']=='DV-6' and r['result']=='skipped' for r in result['outcomes']))

    def test_formal_unlimited_does_not_remove_practice_guard(self):
        ledger={'sanity':146,'limit':None,'count':3,'practice_count':12,'practice_limit':12}
        result=self.execute([stage('DV-1',[1])],ledger=ledger)
        self.assertIsInstance(result['error'],PracticeBudgetExhausted)
        self.assertEqual(result['calls'],[])
        self.assertEqual((ledger['count'],ledger['sanity'],ledger['practice_count']),(3,146,12))

    def test_existing_finite_formal_cap_still_stops(self):
        result=self.execute([stage(f'DV-{i}',[i]) for i in range(1,5)],
                            ledger={'sanity':200,'limit':3,'count':0})
        self.assertIsNone(result['error'])
        self.assertEqual(result['ledger']['count'],3)
        self.assertEqual(len(result['calls']),6)


class UnlimitedCampaignTests(unittest.TestCase):
    def execute(self,previous=None,pending=False):
        runner=Runner(Mock(),Mock(),history=Mock());runner.recover_previous_failures=Mock()
        runner.core.refresh_box.return_value={'syncTime':'fresh'}
        book={'profiles':{str(i):{} for i in range(8,14)},
              'stages':{'R8-9:raid':{'group':'8'}}}
        progress={'R8-9:raid':{'state':'uncleared'}} if pending else {}
        calls=[];writes=[]
        def read(path,default=None):
            return previous or {} if path.name=='campaign-results.json' else progress
        def run(prefix,box,budget_state):
            calls.append((prefix,copy.deepcopy(budget_state)))
            budget_state['count']+=1;budget_state['sanity']-=min(50,budget_state['sanity'])
            return []
        runner.run=run
        with ExitStack() as stack:
            for name,value in {
                'assert_execution_unlocked':Mock(),'settings':Mock(return_value={
                    'max_battles_per_run':None,'max_sanity_per_run':200}),
                'catalogue':Mock(return_value=book),'read_json':Mock(side_effect=read),
                'write_json':Mock(side_effect=lambda p,d:writes.append((p,copy.deepcopy(d)))),
                'box_is_stale':Mock(return_value=False),'export_coverage':Mock(),
                'log':Mock(),'check_stop':Mock(),
            }.items():stack.enter_context(patch('autostory.controller.'+name,value))
            runner.run_all(resume=previous is not None)
        return calls,writes,runner

    def test_new_campaign_crosses_three_groups_and_stops_at_sanity_budget(self):
        calls,writes,_=self.execute()
        self.assertEqual([p for p,_ in calls],['8','9','10','11'])
        self.assertEqual(writes[-1][1]['budget']['count'],4)
        self.assertEqual(writes[-1][1]['budget']['sanity'],0)
        self.assertIsNone(writes[-1][1]['budget']['limit'])

    def test_old_limit_stop_revisits_pending_chapter_and_preserves_counters(self):
        previous={'budget':{'sanity':146,'limit':3,'count':3,'practice_count':6,'practice_limit':12},
                  'groups':[{'group':'8','outcomes':[]}],'active_group':'8','state':'finished'}
        calls,_,_=self.execute(previous,pending=True)
        self.assertEqual(calls[0],('8',{'sanity':146,'limit':None,'count':3,
                                      'practice_count':6,'practice_limit':12}))
        self.assertEqual(previous['budget']['limit'],3)

    def test_unlimited_restart_does_not_replenish_spent_sanity(self):
        previous={'budget':{'sanity':0,'limit':None,'count':8,'practice_count':10,'practice_limit':12},'groups':[]}
        calls,_,runner=self.execute(previous)
        self.assertEqual(calls,[])
        runner.core.refresh_box.assert_not_called()


if __name__=='__main__':unittest.main()
