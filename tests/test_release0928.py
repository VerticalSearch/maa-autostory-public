"""0.9.28: training report, bounded core-events log and screenshot budget."""
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from autostory import housekeeping, training_report
from autostory.training_report import trained_row

TESTS = Path(__file__).resolve().parent


def need(name, **req):
    return {'name': name, 'skill': 1, 'requirements': {'elite': 2, 'level': 1, **req}}


class TrainedRowTests(unittest.TestCase):
    def test_promotion_resets_level_and_is_costed(self):
        row = {'id': 'b', 'name': 'B', 'elite': 1, 'level': 50, 'potential': 1}
        out, steps, effort = trained_row(row, need('B'))
        self.assertEqual((out['elite'], out['level']), (2, 1))
        self.assertEqual(steps, ['精一→精二'])
        self.assertGreaterEqual(effort, 6)
        self.assertEqual(row['elite'], 1)  # the saved box is never modified

    def test_level_module_potential_and_known_skill_shortfall(self):
        row = {'elite': 2, 'level': 30, 'potential': 1, 'mainSkillLevel': 5}
        entry = need('B', level=60, module=1, module_level=2, potential=3, skill_level=7)
        out, steps, _ = trained_row(row, entry)
        self.assertEqual(steps, ['等级30→60', '潜能1→3（需信物）', '1技能5级→7级', 'X模组解锁→2级'])
        self.assertEqual(out['equips'], [{'type': 'X', 'level': 2}])

    def test_unread_skill_rank_is_not_a_training_step(self):
        _, steps, effort = trained_row({'elite': 2, 'level': 90}, need('B', skill_level=7))
        self.assertEqual((steps, effort), ([], 0))


class TrainingReportTests(unittest.TestCase):
    BOX = {'syncTime': '2026-11-01T05:00:00+08:00', 'own_opers': [
        {'own': True, 'id': 'char_a', 'name': 'A', 'elite': 2, 'level': 90, 'potential': 1, 'rarity': 5},
        {'own': True, 'id': 'char_b', 'name': 'B', 'elite': 1, 'level': 50, 'potential': 1, 'rarity': 4}]}
    PROGRESS = {'1-1:normal': {'code': '1-1', 'mode': 'normal', 'state': 'uncleared', 'cost': 6},
                '1-2:normal': {'code': '1-2', 'mode': 'normal', 'state': 'complete', 'cost': 6},
                '1-3:normal': {'code': '1-3', 'mode': 'normal', 'state': 'uncleared', 'cost': 6}}

    def report(self, config):
        doc = {'opers': [need('A'), need('B'), need('C')]}
        with tempfile.TemporaryDirectory() as tmp, \
             patch('autostory.campaign.stage_info', return_value={'code': 'x'}), \
             patch('autostory.campaign.execution_blockers', return_value=[]):
            (Path(tmp) / '1-1-normal-search.json').write_text(
                json.dumps([{'id': 101, 'preview': doc}]), encoding='utf-8')
            return training_report.build(self.BOX, config, self.PROGRESS, Path(tmp))

    def test_operator_whose_training_unlocks_a_stage_is_ranked(self):
        report = self.report({})
        self.assertTrue(report['support'])
        self.assertEqual(report['summary']['pending'], 2)
        self.assertEqual(report['summary']['unsearched'], ['1-3'])
        self.assertEqual((report['summary']['blocked'], report['summary']['jobs_single']), (1, 1))
        top = report['ranking'][0]
        self.assertEqual((top['name'], top['stages']), ('B', ['1-1']))
        self.assertEqual(top['cheapest']['job'], 101)
        self.assertEqual(top['cheapest']['steps'], ['精一→精二'])

    def test_without_support_one_operator_is_not_enough(self):
        report = self.report({'support_unit_when_missing_one': False})
        self.assertFalse(report['support'])
        self.assertEqual(report['ranking'], [])
        self.assertEqual(report['summary']['jobs_multi'], 1)


class DashboardPageTests(unittest.TestCase):
    def test_page_renders_training_and_resume_sections(self):
        page = (TESTS.parent / 'web' / 'index.html').read_text(encoding='utf-8')
        for marker in ('trainingSummary', 'trainingList', 'updateTraining', 'resume'):
            self.assertIn(marker, page)


class RotateTests(unittest.TestCase):
    def test_rotation_keeps_a_bounded_number_of_generations(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'core-events.ndjson'
            path.write_text('small', encoding='utf-8')
            self.assertFalse(housekeeping.rotate(path, limit=10, keep=2))
            for text in ('first-generation', 'second-generation', 'third-generation'):
                path.write_text(text, encoding='utf-8')
                self.assertTrue(housekeeping.rotate(path, limit=10, keep=2))
                self.assertFalse(path.exists())
            names = sorted(p.name for p in Path(tmp).iterdir())
            self.assertEqual(names, ['core-events.ndjson.1', 'core-events.ndjson.2'])
            self.assertEqual((Path(tmp) / 'core-events.ndjson.1').read_text(encoding='utf-8'), 'third-generation')
            self.assertEqual((Path(tmp) / 'core-events.ndjson.2').read_text(encoding='utf-8'), 'second-generation')
            self.assertFalse(housekeeping.rotate(Path(tmp) / 'missing.ndjson', limit=1))


class ScreensBudgetTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.data = self.root / 'runtime'
        self.screens = self.data / 'screens'
        self.docs = self.root / 'docs'
        self.docs.mkdir()
        self.now = time.time()

    def shot(self, relative, age_hours, size=1000):
        path = self.screens / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'x' * size)
        stamp = self.now - age_hours * 3600
        os.utime(path, (stamp, stamp))
        return path

    def prune(self, **extra):
        options = dict(screens=self.screens, budget_mb=2500 / 1048576, data=self.data, now=self.now,
                       fixtures=('fixture',), docs=self.docs)
        options.update(extra)
        return housekeeping.prune_screens(**options)

    def test_oldest_unprotected_files_go_first_until_under_budget(self):
        old = [self.shot(f'unstable/{i}.png', 100 - i) for i in range(4)]
        ledger = self.shot('settlements/run-1/01.png', 200)
        relative = self.shot('formation/kept.json', 200)
        cited = self.shot('report.png', 300)
        fixture = self.shot('fixture/0.png', 300)
        recent = self.shot('fresh.png', 1)
        (self.data / 'progress.json').write_text(json.dumps(
            {'1-1:normal': {'evidence': str(self.screens / 'settlements' / 'run-1')}}), encoding='utf-8')
        (self.data / 'attempt-history.json').write_text(json.dumps(
            [{'evidence': 'runtime/screens/formation/kept.json'}]), encoding='utf-8')
        (self.docs / 'NOTES.md').write_text('见 `screens/report.png`，类别 screens/unstable/ 只是泛指', encoding='utf-8')

        preview = self.prune(dry_run=True)
        self.assertEqual(preview['deleted'], 4)
        self.assertTrue(all(p.exists() for p in old))

        report = self.prune()
        self.assertEqual((report['deleted'], report['protected']), (4, 5))
        self.assertEqual(report['before_mb'], round(9000 / 1048576, 1))
        self.assertFalse(any(p.exists() for p in old))
        self.assertFalse((self.screens / 'unstable').exists())  # emptied folder removed
        for path in (ledger, relative, cited, fixture, recent):
            self.assertTrue(path.exists(), path)
        self.assertTrue(self.screens.is_dir())

    def test_stops_as_soon_as_the_budget_fits(self):
        files = [self.shot(f'{i}.png', 50 - i) for i in range(5)]
        report = self.prune()
        self.assertEqual(report['deleted'], 3)
        self.assertEqual([p.exists() for p in files], [False, False, False, True, True])

    def test_disabled_or_missing_folder_does_nothing(self):
        self.shot('a.png', 100, size=5000)
        self.assertEqual(self.prune(budget_mb=0)['deleted'], 0)
        self.assertEqual(self.prune(screens=self.root / 'none')['deleted'], 0)


class WorkerHousekeepingTests(unittest.TestCase):
    def test_run_rotates_and_never_raises(self):
        with tempfile.TemporaryDirectory() as tmp, patch('autostory.housekeeping.log') as log, \
             patch('autostory.housekeeping.CORE_EVENTS_LIMIT', 4), \
             patch('autostory.housekeeping.prune_screens', side_effect=RuntimeError('disk')):
            (Path(tmp) / 'core-events.ndjson').write_text('0123456789', encoding='utf-8')
            result = housekeeping.run(Path(tmp))
            self.assertTrue((Path(tmp) / 'core-events.ndjson.1').exists())
        self.assertTrue(result['core_events_rotated'])
        self.assertEqual(result['screens_error'], 'disk')
        log.assert_called_once()

    def test_worker_main_runs_housekeeping_inside_the_task(self):
        import worker
        from autostory.common import read_json
        with tempfile.TemporaryDirectory() as tmp, patch('worker.DATA', Path(tmp)), \
             patch('worker.TaskLock'), patch('worker.arm_stop_threshold'), patch('worker.write_claim'), \
             patch('worker.clear_claim'), patch('worker.check_stop'), patch('worker.workflow'), \
             patch('worker.make_identity', return_value={'run_id': 'test', 'action': 'scan-all'}), \
             patch('worker.execute', return_value={'state': 'done', 'message': 'ok'}), \
             patch('autostory.housekeeping.run', side_effect=lambda data: calls.append(data)), \
             patch('sys.argv', ['worker', 'scan-all']):
            calls = []
            worker.main()
            self.assertEqual(calls, [Path(tmp)])
            self.assertEqual(read_json(Path(tmp) / 'status.json')['state'], 'done')

    def test_worker_calls_housekeeping_after_the_lock(self):
        source = (TESTS.parent / 'worker.py').read_text(encoding='utf-8')
        lock = source.index('TaskLock(')
        call = source.index('housekeeping.run(DATA)')
        self.assertLess(lock, call)
        self.assertLess(call, source.index('result=execute(args)'))


if __name__ == '__main__':
    unittest.main()
