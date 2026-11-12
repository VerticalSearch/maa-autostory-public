import json,tempfile,unittest
from pathlib import Path
from unittest.mock import Mock,patch
from autostory.attempts import AttemptHistory,AttemptHistoryError
from autostory.controller import Runner,ledger_failure

class CheckpointRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.history=AttemptHistory(self.root/'history.json')
    def budget(self,phase='practice_failed',at='2026-10-31T10:00:00+08:00'):
        return {'sanity':194,'count':1,'limit':3,'practice_count':1,'practice_limit':12,
                'stages':{'DV-1:normal':{'practice_count':1,'candidate_limit':3,'attempts':[
                    {'phase':phase,'job_id':1,'job_sha':'j','team_sha':'t','at':at,'updated_at':at}]}}}
    def test_missing_failure_restores_once_and_keeps_original_time(self):
        for phase in ('practice_failed','formal_failed'):
            history=AttemptHistory(self.root/(phase+'.json'));budget=self.budget(phase)
            self.assertEqual(history.recover_checkpoints([budget]),1)
            self.assertEqual(history.recover_checkpoints([budget,budget]),0)
            self.assertEqual(history.blocked('DV-1','normal',1,'j','t')['at'],'2026-10-31T10:00:00+08:00')
            self.assertIsNone(history.blocked('DV-1','normal',1,'new-job','t'))
            self.assertIsNone(history.blocked('DV-1','normal',1,'j','new-team'))
    def test_later_pass_in_history_or_checkpoint_wins(self):
        self.history.record('DV-1','normal',1,'j','t','passed')
        self.assertEqual(self.history.recover_checkpoints([self.budget()]),0)
        self.assertIsNone(self.history.blocked('DV-1','normal',1,'j','t'))
        fresh=AttemptHistory(self.root/'new.json')
        self.assertEqual(fresh.recover_checkpoints([self.budget(),self.budget('verified','2026-10-31T11:00:00+08:00')]),1)
        self.assertIsNone(fresh.blocked('DV-1','normal',1,'j','t'))
    def test_newer_verified_persists_after_checkpoint_replaced_then_later_failure_blocks(self):
        self.history.recover_checkpoints([self.budget()])
        verified=self.budget('verified','2026-10-31T11:00:00+08:00')
        self.assertEqual(self.history.recover_checkpoints([verified]),1)
        self.assertIsNone(self.history.blocked('DV-1','normal',1,'j','t'))
        self.assertEqual(self.history.recover_checkpoints([self.budget(),{}]),0)
        self.assertIsNone(self.history.blocked('DV-1','normal',1,'j','t'))
        self.assertEqual(self.history.recover_checkpoints([self.budget('formal_failed','2026-10-31T12:00:00+08:00')]),1)
        self.assertEqual(self.history.blocked('DV-1','normal',1,'j','t')['outcome'],'formal_failed')
    def test_reversed_inputs_identical_and_idempotent(self):
        old=self.budget();new=self.budget('verified','2026-10-31T11:00:00+08:00')
        other=AttemptHistory(self.root/'other.json')
        self.assertEqual(self.history.recover_checkpoints([old,new]),1)
        self.assertEqual(other.recover_checkpoints([new,old]),1)
        self.assertEqual(self.history._load(),other._load())
        self.assertEqual(self.history.recover_checkpoints([new,old]),0)
    def test_same_second_conflict_stops_without_writing(self):
        with self.assertRaisesRegex(AttemptHistoryError,'同秒'):
            self.history.recover_checkpoints([self.budget(),self.budget('verified')])
        self.assertFalse(self.history.path.exists())
        self.history.recover_checkpoints([self.budget()]);before=self.history.path.read_bytes()
        with self.assertRaisesRegex(AttemptHistoryError,'同秒'):self.history.recover_checkpoints([self.budget('verified')])
        self.assertEqual(self.history.path.read_bytes(),before)
    def test_latest_ledger_terminal_result_controls_exclusion(self):
        failed=self.budget()['stages']['DV-1:normal']['attempts'][0]
        verified=self.budget('verified','2026-10-31T11:00:00+08:00')['stages']['DV-1:normal']['attempts'][0]
        for entries in ([failed,verified],[verified,failed]):
            self.assertIsNone(ledger_failure({'attempts':entries},1,'j','t'))
        later=self.budget('formal_failed','2026-10-31T12:00:00+08:00')['stages']['DV-1:normal']['attempts'][0]
        self.assertTrue(ledger_failure({'attempts':[verified,later]},1,'j','t'))
    def test_direct_new_global_round_restores_before_budget_replaced(self):
        (self.root/'campaign-results.json').write_text(json.dumps({'budget':self.budget(),'groups':[]}),encoding='utf-8')
        with patch('autostory.controller.DATA',self.root),patch('autostory.controller.check_stop'), \
             patch('autostory.controller.workflow.update'),patch('autostory.controller.log'), \
             patch('autostory.controller.catalogue',return_value={'profiles':{},'stages':{}}), \
             patch('autostory.controller.export_coverage'):
            core=Mock();core.refresh_box.return_value={'own_opers':[]}
            Runner(core,Mock(),history=self.history).run_all(resume=False)
        self.assertTrue(self.history.blocked('DV-1','normal',1,'j','t'))
        self.assertEqual(json.loads((self.root/'campaign-results.json').read_text('utf-8'))['budget']['count'],0)
    def test_direct_single_chapter_restores_before_refresh(self):
        (self.root/'chapter-checkpoint.json').write_text(json.dumps({'budget':self.budget('formal_failed')}),encoding='utf-8')
        core=Mock();core.refresh_box.side_effect=RuntimeError('end fixture before device')
        with patch('autostory.controller.DATA',self.root),patch('autostory.controller.workflow.update'):
            with self.assertRaisesRegex(RuntimeError,'end fixture'):Runner(core,Mock(),history=self.history).run('DV')
        self.assertTrue(self.history.blocked('DV-1','normal',1,'j','t'))
    def test_failed_history_write_stops_before_any_new_round_action(self):
        (self.root/'campaign-results.json').write_text(json.dumps({'budget':self.budget()}),encoding='utf-8')
        original=(self.root/'campaign-results.json').read_bytes()
        with patch('autostory.controller.DATA',self.root),patch('autostory.attempts.write_json',side_effect=OSError('disk full')):
            runner=Runner(Mock(),Mock(),history=self.history)
            with self.assertRaisesRegex(RuntimeError,'记录不可用'):runner.run_all()
            runner.core.refresh_box.assert_not_called()
        self.assertEqual((self.root/'campaign-results.json').read_bytes(),original)

if __name__=='__main__':unittest.main()
