"""A missing training capability is a candidate rejection before any input."""
import copy
import unittest
from unittest.mock import Mock,patch

import test_retry_flow as support
from test_training0920 import POLICY,box,plan
from autostory.skill_training import SkillTrainer,TrainingUnavailableError


class CapabilityTests(unittest.TestCase):
    def test_unverified_version_rejects_without_input_receipt_or_roster_change(self):
        core=Mock();core.asst.get_version.return_value='v6.21.1'
        nav=Mock();trainer=SkillTrainer(core,nav,lambda:POLICY)
        roster=box();before=copy.deepcopy(roster)
        with patch('autostory.skill_training.write_json') as write:
            with self.assertRaises(TrainingUnavailableError):trainer.apply(roster,plan())
        core.run.assert_not_called();nav.home.assert_not_called();write.assert_not_called()
        self.assertEqual(roster,before);self.assertEqual(trainer.attempted,set())


class CandidateFlowTests(unittest.TestCase):
    setUp=support.RetryFlowTests.setUp
    execute=support.RetryFlowTests.execute

    def run_training(self,error,next_candidate=False):
        training={'status':'training_required','assignment':[],**plan()}
        ready={'status':'trial_required','assignment':[]}
        trainer=Mock();trainer.apply.side_effect=error
        matches=[training,training,ready,ready] if next_candidate else None
        with patch('autostory.controller.SkillTrainer',return_value=trainer),patch(
                'autostory.controller.candidate_match',side_effect=matches,return_value=training):
            result=self.execute([support.stage('DV-1',[1,2] if next_candidate else [1])])
        return result,trainer

    def test_unsupported_training_skips_to_next_practice_then_formal(self):
        result,trainer=self.run_training(TrainingUnavailableError('not supported; no input'),True)
        self.assertIsNone(result['error']);trainer.apply.assert_called_once()
        self.assertEqual([(c[1],c[2]) for c in result['calls']],[(2,True),(2,False)])
        self.assertEqual((result['ledger']['sanity'],result['ledger']['practice_count']),(194,1))
        self.assertTrue(any(r['job']==1 and r['result']=='candidate_rejected' for r in result['outcomes'] if 'job' in r))

    def test_only_unsupported_candidates_preserve_budget_without_battle(self):
        result,_=self.run_training(TrainingUnavailableError('not supported; no input'))
        self.assertIsNone(result['error']);self.assertEqual(result['calls'],[])
        self.assertEqual((result['ledger']['sanity'],result['ledger']['count'],result['ledger']['practice_count']),(200,0,0))
        self.assertTrue(any(r['result']=='skipped' for r in result['outcomes']))

    def test_real_upgrade_error_is_not_downgraded_to_candidate_rejection(self):
        error=RuntimeError('upgrade already submitted; result unknown')
        result,_=self.run_training(error,True)
        self.assertIs(result['error'],error);self.assertEqual(result['calls'],[])

    def test_user_stop_is_not_downgraded_to_candidate_rejection(self):
        error=InterruptedError('user stopped')
        result,_=self.run_training(error,True)
        self.assertIs(result['error'],error);self.assertEqual(result['calls'],[])


if __name__=='__main__':unittest.main()
