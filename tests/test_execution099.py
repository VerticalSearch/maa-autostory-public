import copy,json,tempfile,unittest
from pathlib import Path
from unittest.mock import Mock,patch
from autostory.controller import Runner,assert_execution_unlocked,ledger_failure
from autostory.recovery import StateIntegrityError
from autostory.core import Core
from autostory.run_ledger import validate_stages

class ExecutionInterlockTests(unittest.TestCase):
    def test_wrong_mode_hook_persists_lock_and_stops_core_immediately(self):
        with tempfile.TemporaryDirectory() as tmp,patch('autostory.controller.DATA',Path(tmp)), \
             patch('autostory.controller.check_stop'),patch('autostory.core.check_stop'),patch('autostory.core.assert_gui_idle'):
            nav=Mock();nav.find.return_value={'state':'uncleared'};nav.observe.return_value=(None,[{'text':'演习'}])
            core=object.__new__(Core);core.asst=Mock();core.asst.get_version.return_value='6.20.1'
            core.events=[]
            def start():
                core.events.append({'msg':20002,'taskchain':'Copilot','details':{'task':'BattleStartRaid'}});return True
            core.asst.start.side_effect=start;core.asst.running.return_value=True
            runner=Runner(core,nav,history=Mock());runner.active_attempt={'phase':'practice_reserved','job_id':1}
            snapshots=[];runner.persist_checkpoint=lambda:snapshots.append(copy.deepcopy(runner.active_attempt))
            with self.assertRaisesRegex(StateIntegrityError,'开战模式不符'):runner.battle('DV-1',Path(tmp)/'job.json',True)
            self.assertEqual(runner.active_attempt['phase'],'mode_mismatch')
            self.assertEqual(len(snapshots),1);self.assertEqual(core.asst.stop.call_count,2)
            lock=json.loads((Path(tmp)/'execution-lock.json').read_text('utf-8'))
            self.assertEqual(lock['actual_event']['details']['task'],'BattleStartRaid')
            for resume in (False,True):
                other=Runner(Mock(),Mock())
                with self.assertRaises(StateIntegrityError):other.run_all(resume=resume)
                other.core.refresh_box.assert_not_called()
            with self.assertRaises(StateIntegrityError):assert_execution_unlocked()

    def test_persisted_failure_survives_missing_history_and_requires_exact_fingerprints(self):
        for phase in ('practice_failed','formal_failed'):
            item={'attempts':[{'phase':phase,'job_id':1,'job_sha':'j','team_sha':'t','at':'2026-10-31T10:00:00+08:00'}]}
            restored=json.loads(json.dumps(item))
            self.assertTrue(ledger_failure(restored,1,'j','t'))
            for job,j,t in ((2,'j','t'),(1,'new','t'),(1,'j','new'),('1','j','t')):
                self.assertIsNone(ledger_failure(restored,job,j,t))
    def test_failed_checkpoint_without_fingerprints_is_not_retried(self):
        ledger={'practice_count':1,'stages':{'DV-1:normal':{'practice_count':1,'candidate_limit':3,
                'attempts':[{'phase':'formal_failed','job_id':1}]}}}
        with self.assertRaises(StateIntegrityError):validate_stages(ledger)

if __name__=='__main__':unittest.main()
