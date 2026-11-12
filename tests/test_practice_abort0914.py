import copy,json,tempfile,unittest
from pathlib import Path
from unittest.mock import Mock,patch
from autostory.practice_abort import exercise_abort,reject_aborted_practice
from autostory.recovery import PracticeFailure

def events():
    return [{'msg':20002,'taskid':11,'taskchain':'Copilot','details':{'task':'BattleStartExercise'}},
            {'msg':20003,'taskid':11,'taskchain':'Copilot','what':'ExceededLimit','details':{'task':'BattlePauseCancel','exec_times':10,'max_times':10}},
            {'msg':10002,'taskid':11,'taskchain':'Copilot'}]

class PracticeAbortTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        target=patch('autostory.practice_abort.DATA',Path(temporary.name))
        target.start();self.addCleanup(target.stop)
        for path,value in [('autostory.recovery.DATA',Path(temporary.name)),
                           ('autostory.recovery.check_stop',Mock()),('autostory.recovery.time.sleep',Mock())]:
            target=patch(path,value);target.start();self.addCleanup(target.stop)

    def test_exact_exercise_limit_and_completion_order_required(self):
        self.assertIsNotNone(exercise_abort(events()))
        for variant in ('paid','mixed','task','id','order','incomplete','limit'):
            rows=copy.deepcopy(events())
            if variant=='paid':rows[0]['details']['task']='BattleStartRaid'
            elif variant=='mixed':rows.insert(1,{**rows[0],'details':{'task':'BattleStartNormal'}})
            elif variant=='task':rows[1]['details']['task']='BattleQuickFormationSkillPage'
            elif variant=='id':rows[1]['taskid']=12
            elif variant=='order':rows=[rows[1],rows[0],rows[2]]
            elif variant=='incomplete':rows.pop()
            else:rows[1]['details']['exec_times']=9
            self.assertIsNone(exercise_abort(rows),variant)
    def test_confirmed_map_and_target_reject_candidate_with_unverified_game_result(self):
        nav=Mock();nav.observe.return_value=(None,[])
        nav.find.return_value={'code':'M8-8','mode':'raid','state':'uncleared'}
        with tempfile.TemporaryDirectory() as tmp,patch('autostory.practice_abort.DATA',Path(tmp)), \
             patch('autostory.practice_abort.navigation_regions',return_value=('map',(1,2))):
            with self.assertRaises(PracticeFailure) as caught:reject_aborted_practice(nav,'8','M8-8','raid',events())
            proof=json.loads((Path(caught.exception.evidence)/'result.json').read_text('utf-8'))
        self.assertEqual(proof['game_result'],'unverified');self.assertFalse(proof['may_start_formal'])
        nav.find.assert_called_once_with('M8-8',mode='raid');nav.d.tap.assert_not_called()
    def test_unknown_page_and_wrong_target_stop_without_candidate_recovery(self):
        nav=Mock();nav.observe.return_value=(None,[])
        nav.visible_stages.return_value=[]
        with patch('autostory.practice_abort.navigation_regions',return_value=(None,())):
            with self.assertRaises(RuntimeError) as caught:reject_aborted_practice(nav,'8','M8-8','raid',events())
        self.assertNotIsInstance(caught.exception,PracticeFailure);nav.find.assert_not_called()
        self.assertEqual(nav.observe.call_count,49)
        nav.find.return_value={'code':'M8-7','mode':'raid','state':'uncleared'}
        nav.visible_stages.return_value=[1]
        with patch('autostory.practice_abort.navigation_regions',return_value=('map',(1,2))):
            with self.assertRaises(RuntimeError) as caught:reject_aborted_practice(nav,'8','M8-8','raid',events())
        self.assertNotIsInstance(caught.exception,PracticeFailure)
    def test_other_errors_do_not_inject_input_or_observe(self):
        nav=Mock();reject_aborted_practice(nav,'8','M8-8','raid',[])
        nav.observe.assert_not_called();nav.find.assert_not_called()

if __name__=='__main__':unittest.main()
