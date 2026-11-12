import copy
import json
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import Mock,patch

from autostory.attempts import AttemptHistory
from autostory.controller import Runner,candidate_priority
from autostory.recovery import (PracticeFailure,FormalFailure,RecoveryFailure,PracticeBudgetExhausted,
                               StateIntegrityError,recover_practice)


def stage(code,ids,cost=6):
    return {'code':code,'mode':'normal','state':'uncleared','cost':cost,
            'candidates':[{'id':ident,'like':0,'match':{'status':'trial_required','assignment':[]}} for ident in ids]}


class RetryFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        target=patch('autostory.controller.DATA',self.root);target.start();self.addCleanup(target.stop)

    def execute(self,stages,failures=None,ledger=None,cap=3,recovery_error=None,history=None,returns=None,dispatch=None,extra=None,downloader=None):
        failures=failures or {}
        ledger=ledger if ledger is not None else {'sanity':200,'limit':1,'count':0}
        history=history or AttemptHistory(self.root/'history.json')
        core=Mock();core.refresh_box.return_value={'syncTime':'2026-10-30T22:00:00+08:00','own_opers':[]}
        nav=Mock();nav.find.return_value={'state':'cleared','code':'DV-1','mode':'normal'}
        store=Mock();store.catalog.costs={}
        store.search.return_value=extra or [];store.last_search={'pages':4,'exhausted':True,'stop_reason':'exhausted'}
        def download(item,code,mode):
            path=self.root/f'{item["id"]}.json';path.write_text('{}')
            return path,{'stage_name':code,'opers':[],'actions':[{'type':'SkillDaemon','pre_delay':item['id']}]},''
        store.download.side_effect=downloader or download
        calls=[];checkpoints=[]
        with ExitStack() as stack:
            for name,value in [('DATA',self.root),('JobStore',Mock(return_value=store)),
                ('build_plan',Mock(return_value=copy.deepcopy(stages))),('box_is_stale',Mock(return_value=False)),
                ('settings',Mock(return_value={'max_candidates_per_stage':cap,'max_practice_attempts_per_run':12})),
                ('match_job',Mock(return_value={'status':'trial_required','assignment':[]})),
                ('check_stop',Mock()),('log',Mock()),('export_coverage',Mock())]:
                stack.enter_context(patch('autostory.controller.'+name,value))
            recover=stack.enter_context(patch('autostory.controller.recover_practice',side_effect=recovery_error))
            runner=Runner(core,nav,history=history)
            runner.campaign_checkpoint=lambda:checkpoints.append(dict(ledger))
            def battle(code,path,trial,mode,prepared=None):
                job=int(path.stem);calls.append((code,job,trial,dict(ledger)))
                if not trial:runner.formal_dispatched=dispatch
                failure=failures.get((job,trial))
                if failure:raise failure
                return (returns or {}).get((job,trial),'three_star')
            runner.battle=battle
            error=None
            try:outcomes=runner.run('DV',budget_state=ledger)
            except Exception as exc:error=exc;outcomes=json.loads((self.root/'run-results.json').read_text('utf-8'))
        return {'calls':calls,'ledger':ledger,'outcomes':outcomes,'error':error,
                'recover':recover,'history':history,'checkpoints':checkpoints,'store':store}

    def test_failed_checkpoint_blocks_exact_job_when_history_write_was_lost(self):
        first=self.execute([stage('DV-1',[1])],{(1,True):PracticeFailure('loss')},recovery_error=RecoveryFailure('lost'))
        ledger=json.loads(json.dumps(first['ledger']))
        result=self.execute([stage('DV-1',[1,2])],ledger=ledger,history=AttemptHistory(self.root/'empty-history.json'))
        self.assertIsNone(result['error'])
        self.assertEqual([(x[1],x[2]) for x in result['calls']],[(2,True),(2,False)])

    def test_newer_history_pass_cannot_be_overridden_by_old_ledger_failure(self):
        # Ledger and history writes can straddle a real-clock second. Give the
        # old failure, later success and resumed execution explicit ordering.
        with patch('autostory.attempts.now',return_value='2026-10-30T16:00:00+08:00'),patch(
                'autostory.run_ledger.now',return_value='2026-10-30T16:00:00+08:00'):
            first=self.execute([stage('DV-1',[1])],{(1,True):PracticeFailure('loss')})
        history=first['history'];entry=history._load()[-1]
        with patch('autostory.attempts.now',return_value='2026-10-30T16:01:00+08:00'):
            history.record(entry['code'],entry['mode'],entry['job_id'],entry['job_sha'],entry['roster_sha'],'passed')
        with patch('autostory.attempts.now',return_value='2026-10-30T16:02:00+08:00'),patch(
                'autostory.run_ledger.now',return_value='2026-10-30T16:02:00+08:00'):
            result=self.execute([stage('DV-1',[1])],ledger=first['ledger'],history=history)
        self.assertIsNone(result['error'])
        self.assertEqual([(x[1],x[2]) for x in result['calls']],[(1,True),(1,False)])

    def test_excluded_first_pages_refill_then_practice_new_job_before_formal(self):
        current=stage('DV-1',[1,2,3]);current['search']={'pages':3,'exhausted':False,'stop_reason':'eligible'}
        history=Mock();history.blocked.side_effect=lambda code,mode,job,*args: {'reason':'known loss'} if job<4 else None
        history.blocked_equivalent.return_value=None
        result=self.execute([current],history=history,extra=[{'id':4,'preview':{'opers':[],'actions':[{'type':'SkillDaemon'}]}}])
        self.assertIsNone(result['error'])
        self.assertEqual([(x[1],x[2]) for x in result['calls']],[(4,True),(4,False)])
        self.assertEqual(result['store'].search.call_count,1)

    def test_invalid_timeout_skips_candidate_then_valid_job_practices_before_formal(self):
        from autostory.jobs import JobStore
        store=object.__new__(JobStore);store.cache=self.root
        store.catalog=Mock();store.catalog.code.return_value='DV-1'
        def detail(url):
            bad={'1':None,'2':[],'3':{}}
            action={'type':'SkillDaemon'}
            if url.rsplit('/',1)[-1] in bad:action['timeout']=bad[url.rsplit('/',1)[-1]]
            return {'content':json.dumps({'stage_name':'DV-1','difficulty':1,'actions':[action],'opers':[]})}
        store.get=detail
        result=self.execute([stage('DV-1',[1,2,3,4])],downloader=store.download)
        self.assertIsNone(result['error'])
        self.assertEqual([(x[1],x[2]) for x in result['calls']],[(4,True),(4,False)])
        self.assertEqual(result['ledger']['practice_count'],1)
        self.assertEqual(len([r for r in result['outcomes'] if r['result']=='candidate_rejected']),3)

    def test_failed_practice_recovers_then_next_candidate_must_practice_before_paid(self):
        result=self.execute([stage('DV-1',[1,2])],{(1,True):PracticeFailure('loss','evidence')})
        self.assertIsNone(result['error'])
        self.assertEqual([(x[1],x[2]) for x in result['calls']],[(1,True),(2,True),(2,False)])
        result['recover'].assert_called_once()
        self.assertEqual(result['ledger']['sanity'],194)
        self.assertEqual(result['ledger']['practice_count'],2)
        self.assertEqual(result['calls'][-1][3]['count'],1)
        self.assertEqual(result['calls'][-1][3]['sanity'],194)

    def test_exhausted_candidates_skip_stage_and_continue_another_stage(self):
        result=self.execute([stage('DV-1',[1,2,3]),stage('DV-2',[4])],
            {(1,True):PracticeFailure('loss'),(2,True):PracticeFailure('loss')},cap=2)
        self.assertIsNone(result['error'])
        self.assertEqual([(c[0],c[1],c[2]) for c in result['calls']],
                         [('DV-1',1,True),('DV-1',2,True),('DV-2',4,True),('DV-2',4,False)])
        self.assertTrue(any(r['code']=='DV-1' and r['result']=='skipped' for r in result['outcomes']))

    def test_new_worker_excludes_same_failed_job_without_another_practice(self):
        first=self.execute([stage('DV-1',[1])],{(1,True):PracticeFailure('loss')})
        self.assertEqual(len(first['calls']),1)
        second=self.execute([stage('DV-1',[1])])
        self.assertEqual(second['calls'],[])
        self.assertEqual(second['ledger']['practice_count'],0)
        self.assertEqual(second['outcomes'][0]['result'],'candidate_excluded')

    def test_recovery_uncertainty_stops_before_next_candidate(self):
        result=self.execute([stage('DV-1',[1,2])],{(1,True):PracticeFailure('loss')},recovery_error=RecoveryFailure('unknown screen'))
        self.assertIsInstance(result['error'],RecoveryFailure)
        self.assertEqual(len(result['calls']),1)
        self.assertEqual(result['ledger']['sanity'],200)

    def test_formal_failure_stops_and_keeps_reserved_budget(self):
        result=self.execute([stage('DV-1',[1,2])],{(1,False):FormalFailure('paid loss')})
        self.assertIsInstance(result['error'],FormalFailure)
        self.assertEqual([(c[1],c[2]) for c in result['calls']],[(1,True),(1,False)])
        self.assertEqual(result['ledger']['sanity'],194)
        data=json.loads(result['history'].path.read_text('utf-8'))
        self.assertEqual(data['attempts'][-1]['outcome'],'formal_failed')

    def test_already_cleared_without_paid_start_releases_reservation(self):
        result=self.execute([stage('DV-1',[1])],returns={(1,False):'already_cleared'})
        self.assertIsNone(result['error'])
        self.assertEqual(result['ledger']['sanity'],200)
        self.assertEqual(result['ledger']['count'],0)

    def test_only_proven_pre_dispatch_error_releases_formal_reservation(self):
        for dispatched,expected in ((False,200),(True,194),(None,194)):
            result=self.execute([stage('DV-1',[1])],failures={(1,False):RuntimeError('preflight or connection failed')},dispatch=dispatched)
            self.assertIsInstance(result['error'],RuntimeError)
            self.assertEqual(result['ledger']['sanity'],expected)
            self.assertEqual(result['ledger']['count'],0 if dispatched is False else 1)
            self.assertEqual(result['ledger']['practice_count'],1)
            if dispatched is False:self.assertEqual(result['ledger']['stages']['DV-1:normal']['attempts'][-1]['phase'],'formal_not_started')

    def test_unknown_error_and_user_stop_never_trigger_automatic_recovery(self):
        for error in (RuntimeError('no settlement proof'),InterruptedError('stop')):
            with self.subTest(error=type(error).__name__):
                result=self.execute([stage('DV-1',[1,2])],{(1,True):error})
                self.assertIs(result['error'],error)
                self.assertEqual(len(result['calls']),1)
                result['recover'].assert_not_called()

    def test_shared_practice_budget_is_not_reset(self):
        ledger={'sanity':179,'limit':3,'count':1,'practice_count':12,'practice_limit':12}
        result=self.execute([stage('DV-1',[1])],ledger=ledger)
        self.assertIsInstance(result['error'],PracticeBudgetExhausted)
        self.assertEqual(result['calls'],[])
        self.assertEqual(result['ledger'],ledger)

    def test_corrupt_history_stops_instead_of_silently_retrying(self):
        (self.root/'history.json').write_text('corrupt')
        result=self.execute([stage('DV-1',[1,2])])
        self.assertIsInstance(result['error'],StateIntegrityError)
        self.assertEqual(result['calls'],[])

    def test_known_requirements_rank_before_fewer_unknown_operators(self):
        unknown={'id':1,'like':999,'match':{'status':'trial_required','assignment':[{'unknown':['module unknown']}]}}
        known={'id':2,'like':0,'match':{'status':'trial_required','assignment':[{'unknown':[]} for _ in range(3)]}}
        self.assertEqual(sorted([unknown,known],key=candidate_priority)[0]['id'],2)

    def test_wrong_start_mode_never_becomes_a_retryable_practice_failure(self):
        nav=Mock();nav.find.return_value={'state':'uncleared'}
        nav.observe.return_value=(None,[{'text':'演习'}])
        core=Mock();core.run.return_value=[{'msg':20002,'details':{'task':'BattleStartNormal'}}]
        runner=Runner(core,nav,history=Mock());runner.result=Mock(return_value=False)
        with self.assertRaisesRegex(RuntimeError,'开战模式不符'):
            runner.battle('DV-1',self.root/'job.json',True)
        runner.result.assert_not_called()

    def test_mixed_exercise_and_paid_start_is_not_accepted_as_practice(self):
        nav=Mock();nav.find.return_value={'state':'uncleared'}
        nav.observe.return_value=(None,[{'text':'演习'}])
        core=Mock();core.run.return_value=[{'msg':20002,'details':{'task':name}} for name in ('BattleStartExercise','BattleStartNormal')]
        runner=Runner(core,nav,history=Mock());runner.result=Mock(return_value=True)
        with self.assertRaisesRegex(RuntimeError,'开战模式不符'):runner.battle('DV-1',self.root/'job.json',True)
        runner.result.assert_not_called()


class RecoveryTests(unittest.TestCase):
    def execute(self,frames,find=None,click=True,attempts=8):
        nav=Mock();nav.observe.side_effect=[(None,t) for t in frames]
        nav.click_text.return_value=click
        nav.visible_stages.side_effect=lambda texts,prefix:[1] if any(x['text']=='DV-1' for x in texts) else []
        nav.find.return_value=find or {'code':'DV-1','mode':'raid','state':'uncleared'}
        with tempfile.TemporaryDirectory() as tmp,patch('autostory.recovery.DATA',Path(tmp)),patch('autostory.recovery.check_stop'),patch('autostory.recovery.time.sleep'):
            try:return recover_practice(nav,'DV-1','raid','DV',attempts),nav,None
            except Exception as error:return None,nav,error

    def test_only_known_continue_prompt_is_clicked_then_exact_mode_verified(self):
        result,nav,error=self.execute([[{'text':'任务失败'},{'text':'点击画面以继续'}],[{'text':'DV-1'}]])
        self.assertIsNone(error);self.assertEqual(result['result'],'recovered')
        nav.click_text.assert_called_once()
        nav.find.assert_called_once_with('DV-1',mode='raid')

    def test_no_continue_prompt_or_active_battle_never_gets_blind_taps(self):
        for frame in ([{'text':'任务失败'}],[{'text':'继续行动'},{'text':'放弃行动'}]):
            result,nav,error=self.execute([frame],click=False)
            self.assertIsInstance(error,RecoveryFailure)
            nav.d.tap.assert_not_called();nav.d.back.assert_not_called()
            nav.find.assert_not_called()

    def test_wrong_difficulty_cannot_be_reported_recovered(self):
        _,_,error=self.execute([[{'text':'DV-1'}]],find={'code':'DV-1','mode':'normal','state':'cleared'})
        self.assertIsInstance(error,RecoveryFailure)

    def test_unknown_transition_wait_is_bounded_without_clicks(self):
        _,nav,error=self.execute([[],[],[]],attempts=3)
        self.assertIsInstance(error,RecoveryFailure)
        self.assertEqual(nav.observe.call_count,3);nav.click_text.assert_not_called()

    def result_frame(self,code='DV-1'):
        return [{'text':code,'x':180,'y':90},{'text':'行动结束','x':180,'y':220},
                {'text':'MISSIONRESULTS','x':150,'y':275}]

    def test_failed_exercise_result_page_is_acknowledged_before_map_lookup(self):
        result,nav,error=self.execute([[{'text':'任务失败'},{'text':'点击画面以继续'}],
            [],self.result_frame(),[{'text':'DV-1'}]])
        self.assertIsNone(error)
        self.assertEqual(result['settlement_clicks'],1)
        nav.d.tap.assert_called_once_with(1255,180,.8)
        nav.find.assert_called_once_with('DV-1',mode='raid')

    def test_recovery_can_resume_on_same_stage_result_page(self):
        result,nav,error=self.execute([self.result_frame(),[{'text':'DV-1'}]])
        self.assertIsNone(error);self.assertEqual(result['continue_clicks'],0)
        nav.d.tap.assert_called_once()

    def test_wrong_stage_result_never_gets_acknowledged(self):
        _,nav,error=self.execute([self.result_frame('DV-11')])
        self.assertIsInstance(error,RecoveryFailure)
        nav.d.tap.assert_not_called();nav.find.assert_not_called()

    def test_stuck_result_page_stops_after_two_acknowledgements(self):
        _,nav,error=self.execute([self.result_frame()]*3)
        self.assertIsInstance(error,RecoveryFailure)
        self.assertEqual(nav.d.tap.call_count,2);nav.find.assert_not_called()

if __name__=='__main__':unittest.main()
