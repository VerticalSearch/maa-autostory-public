import json,tempfile,unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import Mock,patch
from autostory.practice_abort import reject_aborted_practice
from autostory.recovery import PracticeFailure,RecoveryFailure
from test_practice_abort0914 import events


def texts(*words):
    return [{'text':word,'x':80+i*120,'y':100} for i,word in enumerate(words)]


class AbortSettlementTests(unittest.TestCase):
    def setUp(self):
        self.stack=ExitStack();self.addCleanup(self.stack.close)
        self.tmp=self.stack.enter_context(tempfile.TemporaryDirectory());self.root=Path(self.tmp)
        for module in ('practice_abort','recovery'):
            self.stack.enter_context(patch('autostory.'+module+'.DATA',self.root))
        self.stack.enter_context(patch('autostory.recovery.check_stop'))
        self.stack.enter_context(patch('autostory.recovery.time.sleep'))
        self.stack.enter_context(patch('autostory.practice_abort.navigation_regions',return_value=(None,())))

    def navigator(self,frames):
        nav=Mock();nav.observe.side_effect=[(None,frame) for frame in frames]
        nav.visible_stages.side_effect=lambda rows,prefix:[1] if any(t['text']=='map' for t in rows) else []
        nav.find.return_value={'code':'R8-9','mode':'raid','state':'uncleared'}
        return nav

    def test_exact_result_waits_through_loading_then_rejects_job_without_formal(self):
        settlement=texts('OPERATION R8-9','行动结束','MISSION RESULTS')
        nav=self.navigator([settlement,settlement]+[[]]*12+[texts('map')])
        with self.assertRaises(PracticeFailure) as caught:
            reject_aborted_practice(nav,'8','R8-9','raid',events())
        proof=json.loads((Path(caught.exception.evidence)/'result.json').read_text('utf-8'))
        self.assertEqual(proof['game_result'],'unverified');self.assertFalse(proof['may_start_formal'])
        self.assertEqual(proof['recovery']['settlement_clicks'],1)
        self.assertEqual(nav.find.call_count,2)
        nav.d.tap.assert_called_once_with(1255,180,.8)
        nav.d.back.assert_not_called()

    def test_wrong_stage_settlement_is_not_acknowledged(self):
        nav=self.navigator([texts('R8-8','行动结束')])
        with self.assertRaises(RuntimeError) as caught:
            reject_aborted_practice(nav,'8','R8-9','raid',events())
        self.assertNotIsInstance(caught.exception,PracticeFailure)
        nav.d.tap.assert_not_called();nav.find.assert_not_called()

    def test_paused_battle_never_acknowledged_even_with_target_text(self):
        nav=self.navigator([texts('R8-9','行动结束','继续行动','放弃行动')])
        with self.assertRaisesRegex(RuntimeError,'仍在战斗'):
            reject_aborted_practice(nav,'8','R8-9','raid',events())
        nav.d.tap.assert_not_called();nav.find.assert_not_called()

    def test_first_settlement_does_not_authorize_click_on_changed_wrong_stage(self):
        nav=self.navigator([texts('R8-9','行动结束'),texts('R8-8','行动结束')])
        with self.assertRaises(RecoveryFailure):
            reject_aborted_practice(nav,'8','R8-9','raid',events())
        nav.d.tap.assert_not_called();nav.find.assert_not_called()

    def test_unknown_after_target_settlement_is_bounded_and_never_clicked(self):
        nav=self.navigator([texts('R8-9','行动结束')]+[[]]*48)
        with self.assertRaises(RecoveryFailure):
            reject_aborted_practice(nav,'8','R8-9','raid',events())
        self.assertEqual(nav.observe.call_count,49)
        nav.d.tap.assert_not_called();nav.find.assert_not_called()

    def test_recovery_still_requires_exact_mode_and_uncleared_state(self):
        for row in ({'code':'R8-9','mode':'normal','state':'uncleared'},
                    {'code':'R8-9','mode':'raid','state':'cleared'}):
            with self.subTest(row=row):
                nav=self.navigator([texts('R8-9','行动结束'),texts('map')]);nav.find.return_value=row
                with self.assertRaises(RuntimeError) as caught:
                    reject_aborted_practice(nav,'8','R8-9','raid',events())
                self.assertNotIsInstance(caught.exception,PracticeFailure)
                nav.d.tap.assert_not_called()

    def test_formal_or_missing_start_proof_never_enters_settlement_recovery(self):
        for kind in ('BattleStartRaid','BattleStartNormal',None):
            rows=events();rows[0]['details']['task']=kind
            nav=self.navigator([texts('R8-9','行动结束')])
            with self.assertRaisesRegex(RuntimeError,'回调不完整'):
                reject_aborted_practice(nav,'8','R8-9','raid',rows)
            nav.observe.assert_not_called();nav.d.tap.assert_not_called();nav.find.assert_not_called()


if __name__=='__main__':unittest.main()
