import copy,json,tempfile,unittest
from pathlib import Path
from unittest.mock import Mock,patch
from autostory.core import CoreTaskError
from autostory.controller import Runner
from autostory.formation_recovery import formation_rejection,reject_formation
from autostory.recovery import PracticeFailure,RecoveryFailure
import test_retry_flow as support
from test_retry_flow import stage


def events():
    common={'taskid':34,'taskchain':'Copilot'}
    return [{**common,'msg':10001},{**common,'msg':20001,'subtask':'BattleFormationTask'},
            {**common,'msg':20000,'subtask':'BattleFormationTask','why':'OperatorMissing',
             'details':{'opers':{'医疗':[{'name':'华法琳','reason':'Unavailable'}]}}},
            {**common,'msg':10000}]


def labels():
    return [{'text':word,'score':.99,'x':(left+right)/2,'y':(top+bottom)/2,'box':[left,top,right,bottom]}
            for word,(left,top,right,bottom) in [('清空选择',(453,666,544,694)),('确认',(1032,666,1082,696)),('稀有度',(926,29,977,50))]]


class FormationProofTests(unittest.TestCase):
    def test_only_exact_same_task_prebattle_missing_operator_proof_accepted(self):
        self.assertIsNotNone(formation_rejection(CoreTaskError('Copilot',34,events())))
        for kind in ('exercise','paid','id','chain','incomplete','generic','order','disconnect','completed'):
            rows=events()
            if kind in ('exercise','paid'):rows.insert(2,{'msg':20002,'taskchain':'Copilot','taskid':34,'details':{'task':'BattleStartExercise' if kind=='exercise' else 'BattleStartRaid'}})
            elif kind=='id':rows[2]['taskid']=35
            elif kind=='chain':rows[2]['taskchain']='Custom'
            elif kind=='incomplete':rows.pop()
            elif kind=='generic':rows[2].pop('why')
            elif kind=='order':rows[1],rows[2]=rows[2],rows[1]
            elif kind=='disconnect':rows.insert(2,{'msg':0,'taskchain':'Copilot','taskid':34})
            else:rows.insert(2,{'msg':10002,'taskchain':'Copilot','taskid':34})
            with self.subTest(kind=kind):self.assertIsNone(formation_rejection(CoreTaskError('Copilot',34,rows)))

    def test_error_freezes_its_own_callback_copy(self):
        rows=events();error=CoreTaskError('Copilot',34,rows);rows[2]['details'].clear()
        self.assertIsNotNone(formation_rejection(error))

    def test_real_core_internal_scroll_and_all_tasks_envelope_are_not_a_battle_start(self):
        rows=events()
        rows.insert(2,{'msg':20002,'taskid':0,'taskchain':'Copilot','subtask':'ProcessTask','details':{'task':'BattleQuickFormationSkill-SwipeToTheDown'}})
        rows.append({'msg':3,'taskid':34,'taskchain':'Copilot','finished_tasks':[34]})
        self.assertIsNotNone(formation_rejection(CoreTaskError('Copilot',34,rows)))
        rows[2]['details']['task']='BattleStartRaid'
        self.assertIsNone(formation_rejection(CoreTaskError('Copilot',34,rows)))

    def test_exact_selection_returns_to_target_and_only_rejects_candidate(self):
        with tempfile.TemporaryDirectory() as tmp,patch('autostory.formation_recovery.DATA',Path(tmp)),patch('autostory.formation_recovery.check_stop'):
            nav=Mock();nav.observe.return_value=(None,labels());nav.find.return_value={'code':'R8-9','mode':'raid','state':'uncleared'}
            with self.assertRaises(PracticeFailure) as caught:reject_formation(nav,'8','R8-9','raid',CoreTaskError('Copilot',34,events()))
            self.assertTrue(caught.exception.recovered);self.assertEqual(caught.exception.game_result,'not_started')
            proof=json.loads((Path(caught.exception.evidence)/'result.json').read_text('utf-8'))
            self.assertFalse(proof['may_start_formal'])
            nav.home.assert_called_once();nav.enter.assert_called_once_with('8');nav.find.assert_called_once_with('R8-9',mode='raid')

    def test_unknown_or_paused_frame_never_backs_or_navigates(self):
        with tempfile.TemporaryDirectory() as tmp,patch('autostory.formation_recovery.DATA',Path(tmp)):
            for texts in ([],labels()+[{'text':'放弃行动 继续行动'}]):
                nav=Mock();nav.observe.return_value=(None,texts)
                with self.assertRaises(RecoveryFailure):reject_formation(nav,'8','R8-9','raid',CoreTaskError('Copilot',34,events()))
                nav.home.assert_not_called();nav.enter.assert_not_called();nav.find.assert_not_called()

    def test_wrong_target_or_mode_does_not_confirm_recovery(self):
        with tempfile.TemporaryDirectory() as tmp,patch('autostory.formation_recovery.DATA',Path(tmp)),patch('autostory.formation_recovery.check_stop'):
            nav=Mock();nav.observe.return_value=(None,labels());nav.find.return_value={'code':'R8-9','mode':'normal','state':'cleared'}
            with self.assertRaises(RecoveryFailure):reject_formation(nav,'8','R8-9','raid',CoreTaskError('Copilot',34,events()))

    def test_formal_error_never_enters_exercise_formation_recovery(self):
        runner=Runner(Mock(),Mock(),history=Mock());runner.nav.find.return_value={'state':'uncleared'}
        runner.nav.observe.return_value=(None,[{'text':'开始突袭'}]);runner.core.run.side_effect=CoreTaskError('Copilot',34,events())
        with patch('autostory.controller.assert_execution_unlocked'),patch('autostory.controller.settings',return_value={}),patch('autostory.formation_recovery.reject_formation') as recovery:
            with self.assertRaises(CoreTaskError):runner.battle('R8-9',Path('job.json'),False,'raid')
            recovery.assert_not_called()


class ReplacementTests(unittest.TestCase):
    def assignment(self,name):return {'status':'trial_required','assignment':[{'slot':'医疗','id':name,'operator':{'skill':1}}]}
    def test_replacement_selected_after_profile_is_inspected_before_dispatch(self):
        runner=Runner(Mock(),Mock(),history=Mock());runner.inspector=Mock();runner.inspector.inspect.side_effect=[({'read':'a'},True),({'read':'b'},True)]
        with patch('autostory.controller.match_job',side_effect=[self.assignment('b'),self.assignment('b')]):
            box,match=runner.inspect_assignment({}, {},self.assignment('a'),'8')
        self.assertEqual([call.args[1]['assignment'][0]['id'] for call in runner.inspector.inspect.call_args_list],['a','b'])
        self.assertEqual(box,{'read':'b'});self.assertEqual(match['assignment'][0]['id'],'b')

    def test_replacement_cycle_terminates_without_repeated_profile_reads(self):
        runner=Runner(Mock(),Mock(),history=Mock());runner.inspector=Mock();runner.inspector.inspect.return_value=({},False)
        with patch('autostory.controller.match_job',side_effect=[self.assignment('b'),self.assignment('a')]):runner.inspect_assignment({}, {},self.assignment('a'),'8')
        self.assertEqual(runner.inspector.inspect.call_count,2)


class FormationContinuationTests(unittest.TestCase):
    setUp=support.RetryFlowTests.setUp
    execute=support.RetryFlowTests.execute
    def test_recovered_formation_failure_continues_without_double_recovery_or_formal(self):
        failure=PracticeFailure('formation rejected');failure.recovered=True;failure.game_result='not_started';failure.failure_kind='formation_rejected'
        ledger={'sanity':125,'limit':None,'count':4,'practice_count':13,'practice_limit':None}
        result=self.execute([stage('DV-1',[1,2])],failures={(1,True):failure},ledger=ledger,cap=None)
        self.assertIsNone(result['error']);result['recover'].assert_not_called()
        self.assertEqual([(c[1],c[2]) for c in result['calls']],[(1,True),(2,True),(2,False)])
        self.assertEqual(ledger['stages']['DV-1:normal']['attempts'][0]['game_result'],'not_started')
        self.assertEqual((ledger['sanity'],ledger['count']),(119,5))


if __name__=='__main__':unittest.main()
