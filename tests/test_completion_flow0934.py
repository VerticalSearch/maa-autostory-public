import copy,json,os,subprocess,unittest
from contextlib import ExitStack
from unittest.mock import Mock,patch
from autostory.controller import Runner
from autostory.dashboard import resume_info
from autostory.recovery import PracticeBudgetExhausted
import runtime_support as support

BOOK={'profiles':{'1':{},'2':{}},'stages':{
    '1-1:normal':{'code':'1-1','mode':'normal','group':'1'},
    '2-1:normal':{'code':'2-1','mode':'normal','group':'2'}}}
LEDGER={'sanity':20,'count':0,'limit':None,'practice_count':0,'practice_limit':None}

class CompletionIntegrationTests(unittest.TestCase):
    def campaign(self,progress,previous=None,side_effect=None):
        runner=Runner(Mock(),Mock(),history=Mock());runner.run=Mock(side_effect=side_effect,return_value=[])
        runner.core.refresh_box.return_value={'syncTime':'fresh'}
        writes=[]
        def read(path,default=None):return previous or {} if path.name=='campaign-results.json' else progress
        with ExitStack() as stack:
            for name,value in dict(catalogue=Mock(return_value=BOOK),read_json=Mock(side_effect=read),
                write_json=Mock(side_effect=lambda p,r:writes.append(copy.deepcopy(r))),
                settings=Mock(return_value={'max_sanity_per_run':20}),export_coverage=Mock(),
                box_is_stale=Mock(return_value=False),log=Mock(),check_stop=Mock(),assert_execution_unlocked=Mock()).items():
                stack.enter_context(patch('autostory.controller.'+name,value))
            runner.run_all(resume=previous is not None)
        return runner,writes[-1]

    def test_traversal_finished_with_unseen_records_is_partial(self):
        runner,report=self.campaign({})
        self.assertEqual(runner.run.call_count,2)
        self.assertEqual(report['state'],'partial')
        self.assertEqual(report['completion']['summary']['unknown'],2)

    def test_spent_budget_is_not_success_and_does_not_refresh(self):
        previous={'budget':{**LEDGER,'sanity':0},'groups':[]}
        runner,report=self.campaign({},previous)
        self.assertEqual(report['state'],'budget_exhausted')
        self.assertEqual(report['budget'],previous['budget'])
        runner.core.refresh_box.assert_not_called();runner.run.assert_not_called()

    def test_resume_revisits_previous_scan_with_missing_evidence(self):
        previous={'budget':LEDGER,'groups':[{'group':'1','outcomes':[]}]}
        runner,report=self.campaign({},previous)
        self.assertEqual([c.args[0] for c in runner.run.call_args_list],['1','2'])
        self.assertEqual(report['state'],'partial')

    def test_verified_whole_scope_is_done(self):
        progress={key:{'state':'cleared','code':stage['code'],'mode':stage['mode']} for key,stage in BOOK['stages'].items()}
        _,report=self.campaign(progress)
        self.assertEqual(report['state'],'done')
        self.assertEqual(report['completion']['summary']['cleared'],2)

    def test_practice_allowance_ends_campaign_without_failure(self):
        def exhausted(prefix,box,budget_state):
            budget_state.update(practice_limit=1,practice_count=1)
            raise PracticeBudgetExhausted('test limit')
        _,report=self.campaign({},side_effect=exhausted)
        self.assertEqual(report['state'],'budget_exhausted')
        self.assertEqual(report['budget']['practice_count'],1)

    def test_partial_can_resume_but_spent_budget_requires_new_round(self):
        self.assertTrue(resume_info({'state':'partial','budget':LEDGER},False,{})['available'])
        self.assertFalse(resume_info({'state':'budget_exhausted','budget':{**LEDGER,'sanity':0}},False,{})['available'])

    def test_worker_preserves_all_three_result_states(self):
        root=support.make_project()
        self.addCleanup(support.remove_project,root)
        for state in ('done','partial','budget_exhausted'):
            support.clean_runtime(root)
            result=dict(state=state,message='fixture '+state,summary={'total':2,'cleared':1})
            proc=subprocess.run([support.PYTHON,str(root/'worker.py'),'run-all','--chapter','ALL'],cwd=root,
                env={**os.environ,'FAKE_RUN_RESULT':json.dumps(result)},capture_output=True,timeout=12,
                creationflags=support.CREATE_NO_WINDOW)
            self.assertEqual(proc.returncode,0,proc.stderr.decode('utf-8','replace'))
            status=support.read_json(root/'runtime/status.json')
            workflow=support.read_json(root/'runtime/workflow.json')
            self.assertEqual(status['state'],state)
            self.assertEqual(status['message'],result['message'])
            self.assertEqual(status['summary'],result['summary'])
            self.assertEqual(workflow['phase'],'finished' if state=='done' else state)

if __name__=='__main__':unittest.main()
