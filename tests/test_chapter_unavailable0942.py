"""0.9.42: a chapter entry that cannot be confirmed is chapter-local.

A targeted collection walk that does not find one chapter, or a recent negative
hint from such a walk, must not look like a device/global navigation failure.
``run_all`` keeps that chapter pending and continues with the finite catalogue,
while genuine repeated navigation errors still trip the global breaker.
"""
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from autostory.common import DeviceUnavailableError
from autostory.controller import Runner
from autostory.navigation_errors import ChapterEntryUnavailable

TITLE = '红丝绒'


class ErrorContractTests(unittest.TestCase):
    def test_actual_lookup_fields_and_message(self):
        error = ChapterEntryUnavailable('AD', TITLE)
        self.assertIsInstance(error, RuntimeError)
        self.assertEqual((error.base, error.title, error.cached, error.age_seconds),
                         ('AD', TITLE, False, None))
        self.assertEqual(str(error), f'未找到章节入口：{TITLE}（本次已实际翻查，仍保持待确认）')
        self.assertEqual(error.args, (str(error),))

    def test_cached_message_uses_whole_minutes_with_a_one_minute_floor(self):
        for age, minutes in ((125, 2), (59, 1), (0, 1), (None, 1)):
            error = ChapterEntryUnavailable('AD', TITLE, cached=True, age_seconds=age)
            self.assertTrue(error.cached)
            self.assertEqual(error.age_seconds, age)
            self.assertEqual(
                str(error),
                f'未找到章节入口：{TITLE}（{minutes} 分钟前翻查未找到，本次不重复翻查）')

    def test_cached_flag_is_keyword_only(self):
        with self.assertRaises(TypeError):
            ChapterEntryUnavailable('AD', TITLE, True)


class CampaignCase(unittest.TestCase):
    def prepare(self, groups, *, previous=None, progress=None, config=None):
        runner = Runner.__new__(Runner)
        runner.core = Mock()
        runner.core.refresh_box.return_value = {'syncTime': '2999-01-01T00:00:00+00:00'}
        runner.history = Mock()
        runner._phase = 'navigation'
        book = {'profiles': {group: {} for group in groups}, 'stages': {}}
        writes = []
        self.enterContext(patch('autostory.controller.catalogue', return_value=book))
        self.enterContext(patch('autostory.controller.settings', return_value=config or {}))

        def read_json(path, default=None):
            name = Path(str(path)).name
            if name == 'campaign-results.json' and previous is not None:
                return previous
            if name == 'progress.json' and progress is not None:
                return progress
            return {} if default is None else default

        def write_json(path, payload):
            writes.append((Path(str(path)).name, payload))

        self.enterContext(patch('autostory.controller.read_json', side_effect=read_json))
        self.enterContext(patch('autostory.controller.write_json', side_effect=write_json))
        self.enterContext(patch('autostory.controller.check_stop'))
        self.enterContext(patch('autostory.controller.log'))
        self.enterContext(patch('autostory.controller.workflow.update'))
        self.enterContext(patch('autostory.controller.export_coverage'))
        self.enterContext(patch('autostory.controller.box_is_stale', return_value=False))
        return runner, book, writes

    @staticmethod
    def calls(runner):
        return [call.args[0] for call in runner.run.call_args_list]

    @staticmethod
    def campaign_checkpoints(writes):
        return [payload for name, payload in writes if name == 'campaign-results.json']


class ChapterLocalUnavailableTests(CampaignCase):
    def test_actual_and_two_cached_nested_groups_continue_to_next_group(self):
        runner, _, writes = self.prepare(['AD', 'AD-EX', 'AD-TR', 'ZZ'])

        def run(prefix, box=None, budget_state=None):
            runner.phase = 'navigation'
            if prefix == 'AD':
                raise ChapterEntryUnavailable('AD', TITLE)
            if prefix == 'AD-EX':
                raise ChapterEntryUnavailable('AD', TITLE, cached=True, age_seconds=125)
            if prefix == 'AD-TR':
                raise ChapterEntryUnavailable('AD', TITLE, cached=True, age_seconds=60)
            return []

        runner.run = Mock(side_effect=run)
        report = runner.run_all()

        self.assertEqual(self.calls(runner), ['AD', 'AD-EX', 'AD-TR', 'ZZ'])
        rows = {row['group']: row for row in report}
        self.assertEqual(rows['AD']['error_kind'], 'chapter_entry_unavailable')
        self.assertEqual(rows['AD']['base_group'], 'AD')
        self.assertFalse(rows['AD']['cached'])
        for group, age in (('AD-EX', 125), ('AD-TR', 60)):
            self.assertEqual(rows[group]['error_kind'], 'chapter_entry_unavailable')
            self.assertEqual(rows[group]['base_group'], 'AD')
            self.assertTrue(rows[group]['cached'])
            self.assertIn(TITLE, rows[group]['blocked'])
            self.assertIn(f'{max(1, age // 60)} 分钟前翻查未找到', rows[group]['blocked'])
        self.assertIn('本次已实际翻查', rows['AD']['blocked'])
        # The missing chapters stay pending: no success outcomes were invented.
        for group in ('AD', 'AD-EX', 'AD-TR'):
            self.assertNotIn('outcomes', rows[group])
        self.assertEqual(rows['ZZ']['outcomes'], [])
        self.assertNotIn('error_kind', rows['ZZ'])
        self.assertEqual(runner.last_completion['summary']['cleared'], 0)
        # The durable checkpoint carries the classification, not a generic error.
        saved = self.campaign_checkpoints(writes)
        self.assertTrue(saved)
        self.assertNotIn('error', {payload.get('state') for payload in saved})
        self.assertTrue(any(
            row.get('error_kind') == 'chapter_entry_unavailable'
            for payload in saved for row in payload['groups']))

    def test_three_actual_absences_stay_blocked_and_later_group_still_runs(self):
        runner, _, _ = self.prepare(['AA', 'BB', 'CC', 'ZZ'])

        def run(prefix, box=None, budget_state=None):
            runner.phase = 'navigation'
            if prefix == 'ZZ':
                return []
            raise ChapterEntryUnavailable(prefix, f'章节{prefix}')

        runner.run = Mock(side_effect=run)
        report = runner.run_all()

        # The finite catalogue continues past all three real absences.
        self.assertEqual(self.calls(runner), ['AA', 'BB', 'CC', 'ZZ'])
        rows = {row['group']: row for row in report}
        blocked = [rows[group] for group in ('AA', 'BB', 'CC')]
        self.assertTrue(all(row['error_kind'] == 'chapter_entry_unavailable' for row in blocked))
        self.assertTrue(all(row['cached'] is False for row in blocked))
        self.assertTrue(all('outcomes' not in row for row in blocked))
        self.assertEqual(rows['ZZ']['outcomes'], [])
        self.assertEqual(runner.last_completion['summary']['cleared'], 0)

    def test_generic_same_failure_still_trips_breaker_when_absence_intervenes(self):
        runner, _, writes = self.prepare(['A', 'B', 'C', 'D'])
        message = '画面仍在运动或加载，停止导航并保留截图'

        def run(prefix, box=None, budget_state=None):
            runner.phase = 'navigation'
            if prefix == 'C':
                raise ChapterEntryUnavailable('C', '章节C')
            raise RuntimeError(message)

        runner.run = Mock(side_effect=run)
        with self.assertRaisesRegex(RuntimeError, '连续 3 个分区'):
            runner.run_all()

        self.assertEqual(self.calls(runner), ['A', 'B', 'C', 'D'])
        checkpoint = self.campaign_checkpoints(writes)[-1]
        self.assertEqual(checkpoint['state'], 'error')
        rows = {row['group']: row for row in checkpoint['groups']}
        self.assertEqual(rows['C']['error_kind'], 'chapter_entry_unavailable')
        self.assertNotIn('error_kind', rows['A'])
        self.assertNotIn('error_kind', rows['B'])
        self.assertNotIn('error_kind', rows['D'])

    def test_cached_absence_also_preserves_a_pending_generic_streak(self):
        runner, _, writes = self.prepare(['A', 'B', 'C', 'D'])
        message = '画面仍在运动或加载，停止导航并保留截图'

        def run(prefix, box=None, budget_state=None):
            runner.phase = 'navigation'
            if prefix == 'C':
                raise ChapterEntryUnavailable('C', '章节C', cached=True, age_seconds=120)
            raise RuntimeError(message)

        runner.run = Mock(side_effect=run)
        with self.assertRaisesRegex(RuntimeError, '连续 3 个分区'):
            runner.run_all()

        self.assertEqual(self.calls(runner), ['A', 'B', 'C', 'D'])
        checkpoint = self.campaign_checkpoints(writes)[-1]
        rows = {row['group']: row for row in checkpoint['groups']}
        self.assertTrue(rows['C']['cached'])
        self.assertEqual(rows['C']['error_kind'], 'chapter_entry_unavailable')

    def test_plain_runtime_error_with_the_same_text_is_not_reclassified(self):
        runner, _, writes = self.prepare(['A', 'B', 'C', 'D'])
        message = f'未找到章节入口：{TITLE}（本次已实际翻查，仍保持待确认）'

        def run(prefix, box=None, budget_state=None):
            runner.phase = 'navigation'
            raise RuntimeError(message)

        runner.run = Mock(side_effect=run)
        with self.assertRaisesRegex(RuntimeError, '连续 3 个分区'):
            runner.run_all()

        # Only the concrete type is classified; the message is not matched.
        self.assertEqual(self.calls(runner), ['A', 'B', 'C'])
        checkpoint = self.campaign_checkpoints(writes)[-1]
        self.assertTrue(all('error_kind' not in row for row in checkpoint['groups']))


class FatalAndStopTests(CampaignCase):
    def test_fatal_device_error_propagates(self):
        runner, _, writes = self.prepare(['A'])
        error = DeviceUnavailableError('device offline')
        runner.run = Mock(side_effect=error)
        with self.assertRaises(DeviceUnavailableError) as caught:
            runner.run_all()
        self.assertIs(caught.exception, error)
        self.assertEqual(self.campaign_checkpoints(writes)[-1]['state'], 'error')

    def test_user_stop_propagates_and_checkpoints_stopped(self):
        runner, _, writes = self.prepare(['A'])
        error = InterruptedError('user stopped')
        runner.run = Mock(side_effect=error)
        with self.assertRaises(InterruptedError) as caught:
            runner.run_all()
        self.assertIs(caught.exception, error)
        self.assertEqual(self.campaign_checkpoints(writes)[-1]['state'], 'stopped')

    def test_unavailable_during_formal_remains_fatal(self):
        runner, _, writes = self.prepare(['AD'])
        error = ChapterEntryUnavailable('AD', TITLE)

        def run(prefix, box=None, budget_state=None):
            runner.phase = 'formal'
            raise error

        runner.run = Mock(side_effect=run)
        with self.assertRaises(ChapterEntryUnavailable) as caught:
            runner.run_all()
        self.assertIs(caught.exception, error)
        checkpoint = self.campaign_checkpoints(writes)[-1]
        self.assertEqual(checkpoint['state'], 'error')
        self.assertNotIn('error_kind', checkpoint['groups'][0])


class ResumeTests(CampaignCase):
    BUDGET = {'sanity': 17, 'limit': 10, 'count': 7, 'practice_count': 3,
              'practice_limit': 31}

    def test_resume_preserves_budget_history_and_never_clears_missing_chapter(self):
        previous = {
            'at': '2026-11-03T10:00:00Z',
            'budget': dict(self.BUDGET),
            'groups': [{'group': 'OLD', 'phase': 'navigation', 'blocked': 'previous',
                        'at': '2026-11-03T10:00:00Z'}],
        }
        progress = {'AD-7': {'code': 'AD-7', 'mode': 'normal', 'state': 'uncleared'}}
        before = {key: dict(value) for key, value in progress.items()}
        runner, book, writes = self.prepare(['AD', 'ZZ'], previous=previous, progress=progress)
        book['stages']['AD-7'] = {'code': 'AD-7', 'group': 'AD', 'mode': 'normal'}
        seen = {}

        def run(prefix, box=None, budget_state=None):
            runner.phase = 'navigation'
            seen[prefix] = dict(budget_state)
            if prefix == 'AD':
                raise ChapterEntryUnavailable('AD', TITLE)
            return []

        runner.run = Mock(side_effect=run)
        report = runner.run_all(resume=True)

        self.assertEqual(seen['AD'], self.BUDGET)
        self.assertEqual(seen['ZZ'], self.BUDGET)
        self.assertEqual(runner.last_ledger['sanity'], 17)
        self.assertEqual(runner.last_ledger['limit'], 10)
        self.assertEqual(runner.last_ledger['count'], 7)
        self.assertEqual(runner.last_ledger['practice_count'], 3)
        self.assertEqual(runner.last_ledger['practice_limit'], 31)
        # Incoming history is retained ahead of the new rows.
        self.assertEqual(report[0]['group'], 'OLD')
        rows = {row['group']: row for row in report}
        self.assertEqual(rows['AD']['error_kind'], 'chapter_entry_unavailable')
        self.assertNotIn('outcomes', rows['AD'])
        self.assertEqual(rows['ZZ']['outcomes'], [])
        # The pending chapter record is untouched and never rewritten as cleared.
        self.assertEqual(progress, before)
        self.assertNotIn('progress.json', [name for name, _ in writes])
        self.assertEqual(runner.last_completion['summary']['cleared'], 0)
        self.assertEqual(runner.last_completion['summary']['pending'], 1)


if __name__ == '__main__':
    unittest.main()
