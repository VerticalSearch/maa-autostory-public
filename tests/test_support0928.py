"""0.9.28: support unit, free formal stages, resume info, stale status, GUI cache, waits."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from autostory import common
from autostory.campaign import execution_blockers, formal_without_practice
from autostory.controller import candidate_priority
from autostory.dashboard import resume_info, training_info
from autostory.device import Device
from autostory.jobs import assigned_document, match_job, support_allowed
from autostory.runtime_state import persist_interrupted


def own(oid, name, elite=2, level=90):
    return {'own': True, 'id': oid, 'name': name, 'elite': elite, 'level': level, 'potential': 1}


def need(name, skill=1, **req):
    return {'name': name, 'skill': skill, 'requirements': {'elite': 2, 'level': 1, **req}}


ROSTER = {'own_opers': [own('char_a', 'A'), own('char_b', 'B')]}


class SupportMatchTests(unittest.TestCase):
    def test_exactly_one_missing_operator_borrows_a_support(self):
        doc = {'opers': [need('A'), need('B'), need('C')]}
        self.assertEqual(match_job(doc, ROSTER, chars={})['status'], 'blocked')
        result = match_job(doc, ROSTER, allow_support=True, chars={})
        self.assertEqual(result['status'], 'trial_required')
        self.assertTrue(result['needs_support'])
        self.assertEqual(result['support_slot'], 2)
        slot = result['assignment'][2]
        self.assertTrue(slot['support'])
        self.assertIsNone(slot['id'])
        self.assertIn('C', result['reasons'][0])
        # The borrowed slot keeps its original requirement so MaaCore sees it missing.
        self.assertEqual(assigned_document(doc, result)['opers'][2], need('C'))

    def test_two_missing_operators_stay_blocked(self):
        doc = {'opers': [need('A'), need('C'), need('D')]}
        self.assertEqual(match_job(doc, ROSTER, allow_support=True, chars={})['status'], 'blocked')

    def test_mastery_requirement_cannot_be_borrowed(self):
        doc = {'opers': [need('A'), need('C', skill=3, skill_level=8)]}
        self.assertEqual(match_job(doc, ROSTER, allow_support=True, chars={})['status'], 'blocked')
        doc = {'opers': [need('A'), need('C', skill=3, skill_level=7)]}
        self.assertTrue(match_job(doc, ROSTER, allow_support=True, chars={}).get('needs_support'))

    def test_group_alternatives_are_all_offered_for_the_support_slot(self):
        doc = {'opers': [need('A')], 'groups': [{'name': '奶', 'opers': [need('X'), need('Y')]}]}
        result = match_job(doc, ROSTER, allow_support=True, chars={})
        self.assertEqual([o['name'] for o in result['assignment'][1]['support_opers']], ['X', 'Y'])
        self.assertEqual([o['name'] for o in assigned_document(doc, result)['groups'][0]['opers']], ['X', 'Y'])

    def test_setting_defaults_on_and_rejects_non_boolean(self):
        self.assertTrue(support_allowed({}))
        self.assertFalse(support_allowed({'support_unit_when_missing_one': False}))
        with self.assertRaises(ValueError):
            support_allowed({'support_unit_when_missing_one': 'yes'})

    def test_own_roster_job_ranks_before_support_job(self):
        own_job = {'id': 2, 'like': 0, 'match': {'status': 'trial_required', 'assignment': [{'unknown': []}] * 3}}
        support_job = {'id': 1, 'like': 999, 'match': {'status': 'trial_required', 'needs_support': True,
                                                       'assignment': [{'unknown': []}] * 2}}
        self.assertLess(candidate_priority(own_job), candidate_priority(support_job))


class FreeFormalTests(unittest.TestCase):
    STAGE = {'can_practice': False, 'predefined': False, 'maa_map': True, 'mode': 'normal', 'cost': 0}

    def test_only_confirmed_zero_cost_normal_stage_skips_practice(self):
        self.assertTrue(formal_without_practice(self.STAGE))
        self.assertEqual(execution_blockers(self.STAGE), [])
        for change in ({'cost': 6}, {'cost': None}, {'mode': 'raid'}, {'predefined': True},
                       {'maa_map': False}, {'can_practice': True}):
            stage = {**self.STAGE, **change}
            with self.subTest(change=change):
                self.assertFalse(formal_without_practice(stage))
        self.assertIn('无演习入口，尚无验证途径', execution_blockers({**self.STAGE, 'cost': 6}))


class ResumeInfoTests(unittest.TestCase):
    BUDGET = {'sanity': 40, 'count': 1, 'limit': 5, 'practice_count': 0, 'practice_limit': None}

    def test_resumable_checkpoint_with_budget_left(self):
        info = resume_info({'state': 'error', 'budget': self.BUDGET}, False, {})
        self.assertTrue(info['available'])
        self.assertIn('40', info['reason'])

    def test_unavailable_cases_explain_why(self):
        cases = [
            (({'state': 'error', 'budget': self.BUDGET}, True), '运行中'),
            (({}, False), '还没有'),
            (({'state': 'finished', 'budget': self.BUDGET}, False), '已完成'),
            (({'state': 'stopped', 'budget': {**self.BUDGET, 'sanity': 0}}, False), '预算'),
            (({'state': 'stopped', 'budget': {**self.BUDGET, 'count': 5}}, False), '预算'),
            (({'state': 'stopped', 'budget': {**self.BUDGET, 'practice_count': 3, 'practice_limit': 3}}, False), '演习'),
            (({'state': 'interrupted', 'budget': {'sanity': 'x'}}, False), '无效'),
        ]
        for (campaign, running), text in cases:
            with self.subTest(campaign=campaign, running=running):
                info = resume_info(campaign, running, {})
                self.assertFalse(info['available'])
                self.assertIn(text, info['reason'])

    def test_training_report_failure_never_breaks_dashboard(self):
        with patch('autostory.training_report.cached', side_effect=RuntimeError('boom')):
            self.assertEqual(training_info(), {'state': 'error', 'error': 'boom'})


class StaleStatusTests(unittest.TestCase):
    RUNNING = {'state': 'running', 'run_id': 'r1', 'pid': 999999, 'message': '推图中'}

    def write(self, root, value):
        (Path(root) / 'status.json').write_text(json.dumps(value), encoding='utf-8')

    def read(self, root):
        return json.loads((Path(root) / 'status.json').read_text(encoding='utf-8'))

    def test_dead_running_status_is_written_back_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.write(tmp, self.RUNNING)
            self.assertTrue(persist_interrupted(tmp, self.RUNNING))
            status = self.read(tmp)
            self.assertEqual(status['state'], 'interrupted')
            self.assertEqual(status['run_id'], 'r1')
            self.assertIn('interrupted_detected', status)
            self.assertFalse(persist_interrupted(tmp, self.RUNNING))

    def test_a_newer_run_or_finished_status_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.write(tmp, {**self.RUNNING, 'run_id': 'r2'})
            self.assertFalse(persist_interrupted(tmp, self.RUNNING))
            self.assertEqual(self.read(tmp)['run_id'], 'r2')
            self.write(tmp, {**self.RUNNING, 'state': 'done'})
            self.assertFalse(persist_interrupted(tmp, self.RUNNING))
            self.assertFalse(persist_interrupted(tmp, {**self.RUNNING, 'state': 'done'}))

    def test_live_claim_blocks_the_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.write(tmp, self.RUNNING)
            with patch('autostory.runtime_state.read_claim', return_value={'run_id': 'r9'}), \
                 patch('autostory.runtime_state.identity_alive', return_value=True):
                self.assertFalse(persist_interrupted(tmp, self.RUNNING))
            self.assertEqual(self.read(tmp)['state'], 'running')


class GuiLogCacheTests(unittest.TestCase):
    RUN = b'[INF] [RunningState] Idle: to false\n'
    IDLE = b'[INF] [RunningState] Idle: to true\n'

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'gui.log'
        cache = patch.dict(common._GUI_CACHE, {'log': None, 'offset': 0, 'running': False, 'mark': -1})
        cache.start()
        self.addCleanup(cache.stop)
        self.addCleanup(self.tmp.cleanup)

    def append(self, data):
        with self.path.open('ab') as stream:
            stream.write(data)

    def test_incremental_reads_follow_the_latest_state(self):
        self.assertFalse(common._gui_running(self.path))
        self.append(b'start\n' + self.RUN)
        self.assertTrue(common._gui_running(self.path))
        self.append(b'noise\n')
        self.assertTrue(common._gui_running(self.path))
        self.append(self.IDLE)
        self.assertFalse(common._gui_running(self.path))
        self.append(self.RUN[:20])
        self.assertFalse(common._gui_running(self.path))
        self.append(self.RUN[20:])
        self.assertTrue(common._gui_running(self.path))

    def test_replaced_log_is_read_afresh(self):
        self.append(self.RUN + b'x' * 50 + b'\n')
        self.assertTrue(common._gui_running(self.path))
        self.path.write_bytes(self.IDLE)
        self.assertFalse(common._gui_running(self.path))
        self.path.unlink()
        self.assertFalse(common._gui_running(self.path))

    def test_state_line_that_leaves_the_tail_no_longer_counts(self):
        with patch.object(common, '_GUI_TAIL', 64):
            self.append(self.RUN)
            self.assertTrue(common._gui_running(self.path))
            self.append(b'y' * 15 + b'\n')  # 36 + 16 bytes: still inside the tail
            self.assertTrue(common._gui_running(self.path))
            self.append(b'z' * 70 + b'\n')
            self.assertFalse(common._gui_running(self.path))


class InputWaitTests(unittest.TestCase):
    def test_blind_waits_are_shorter_and_overridable(self):
        device = object.__new__(Device)
        device.size = (1280, 720)
        device.serial = 'test'
        device.raw = Mock()
        device.invalidate_frame = Mock()
        with patch('autostory.device.check_stop'), patch('autostory.device.assert_gui_idle'), \
             patch('autostory.device.STOP_WAIT', return_value=False) as wait:
            device.tap(10, 10)
            device.tap(10, 10, wait=0)
            device.swipe(1, 1, 2, 2)
            device.back()
        self.assertEqual([c.args[0] for c in wait.call_args_list], [.45, 0, .55, .45])
        self.assertEqual(device.raw.call_count, 4)


if __name__ == '__main__':
    unittest.main()
