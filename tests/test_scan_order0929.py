"""0.9.29 regressions: scan order resumes the interrupted group and defers dead ends.

Ordering only: no key is ever dropped, confirmed or marked cleared by it.
"""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from autostory import scanning
from autostory import scan_progress as sp
from autostory.common import read_json, write_json


def rec(code, state='uncleared', mode='normal'):
    return {'code': code, 'mode': mode, 'state': state, 'at': '2026-11-01T00:00:00+08:00', 'evidence': 'screen.png'}


def unseen(code):
    # Exactly what Navigator.scan writes after a finished map pass.
    return {'code': code, 'mode': 'normal', 'state': 'unseen', 'at': '2026-11-01T00:00:00+08:00'}


def book(*groups, raid=()):
    stages = {}
    for g in groups:
        stages[f'{g}-1:normal'] = {'group': g, 'code': f'{g}-1', 'mode': 'normal'}
        if g in raid:
            stages[f'{g}-1:raid'] = {'group': g, 'code': f'{g}-1', 'mode': 'raid'}
    return {'profiles': {g: {} for g in groups}, 'stages': stages}


class ArrangeTests(unittest.TestCase):
    def test_unseen_only_group_goes_last_and_never_counts_as_confirmed(self):
        b = book('A', 'B', 'C')
        progress = {'A-1:normal': unseen('A-1')}
        ordered, deferred = sp.arrange_groups(['A', 'B', 'C'], b, progress)
        self.assertEqual(ordered, ['B', 'C', 'A'])
        self.assertEqual(deferred, ['A'])
        self.assertEqual(sp.summarize_group(b, progress, 'A')['missing_keys'], ['A-1:normal'])

    def test_never_scanned_key_keeps_the_group_fresh(self):
        b = book('A', 'B')
        b['stages']['A-2:normal'] = {'group': 'A', 'code': 'A-2', 'mode': 'normal'}
        progress = {'A-1:normal': unseen('A-1')}          # A-2 has no record at all
        self.assertEqual(sp.arrange_groups(['A', 'B'], b, progress), (['A', 'B'], []))

    def test_raid_is_stalled_only_while_its_normal_node_cannot_open_it(self):
        b = book('A', 'B', 'C', raid=('A', 'B', 'C'))
        progress = {'A-1:normal': rec('A-1', 'partial'),   # raid not observable yet
                    'B-1:normal': rec('B-1', 'cleared'),   # raid should be observable
                    'C-1:normal': unseen('C-1')}
        ordered, deferred = sp.arrange_groups(['A', 'B', 'C'], b, progress)
        self.assertEqual(ordered, ['B', 'A', 'C'])
        self.assertEqual(deferred, ['A', 'C'])

    def test_unseen_record_for_another_code_is_not_a_reason(self):
        b = book('A', 'B')
        progress = {'A-1:normal': {**unseen('A-1'), 'code': 'Z-9'}}
        self.assertEqual(sp.arrange_groups(['A', 'B'], b, progress), (['A', 'B'], []))

    def test_interrupted_group_resumes_first_unless_it_is_a_dead_end(self):
        b = book('A', 'B', 'C')
        self.assertEqual(sp.arrange_groups(['A', 'B', 'C'], b, {}, {'active_group': 'C'})[0], ['C', 'A', 'B'])
        stalled = {'C-1:normal': unseen('C-1')}
        self.assertEqual(sp.arrange_groups(['A', 'B', 'C'], b, stalled, {'active_group': 'C'})[0], ['A', 'B', 'C'])
        self.assertEqual(sp.arrange_groups(['A', 'B', 'C'], b, {}, {'active_group': 'B'}, {'B'})[0], ['A', 'C', 'B'])

    def test_failed_entries_are_deferred_in_their_planned_order(self):
        b = book('A', 'B', 'C', 'D')
        ordered, deferred = sp.arrange_groups(['A', 'B', 'C', 'D'], b, {}, None, {'C', 'A', 7})
        self.assertEqual(ordered, ['B', 'D', 'A', 'C'])
        self.assertEqual(deferred, ['A', 'C'])

    def test_malformed_inputs_never_drop_a_group(self):
        b = book('A', 'B')
        for previous in (None, [], 'x', {'active_group': 'GONE'}, {'active_group': ['A']}):
            with self.subTest(previous=previous):
                self.assertEqual(sorted(sp.arrange_groups(['B', 'A'], b, None, previous)[0]), ['A', 'B'])
        self.assertEqual(sp.arrange_groups([], b, {}), ([], []))


class ScanOrderTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.book = book('A', 'B', 'C')
        for name, value in [('DATA', self.root), ('catalogue', Mock(side_effect=lambda: self.book)),
                            ('check_stop', Mock()), ('log', Mock()), ('export_coverage', Mock()),
                            ('settings', Mock(return_value={}))]:
            target = patch.object(scanning, name, value)
            target.start()
            self.addCleanup(target.stop)
        target = patch.object(scanning.workflow, 'update')
        target.start()
        self.addCleanup(target.stop)
        self.nav = Mock()
        self.nav.on_scan_stage = None
        self.nav.scan.side_effect = lambda group, **kw: self.confirm(group)

    def confirm(self, group):
        db = read_json(self.root / 'progress.json', {})
        row = rec(group + '-1')
        db[group + '-1:normal'] = row
        write_json(self.root / 'progress.json', db)
        self.nav.on_scan_stage(row)

    def entered(self):
        return [call.args[0] for call in self.nav.enter.call_args_list]

    def logged(self):
        return ' '.join(str(call) for call in scanning.log.call_args_list)

    def test_resume_survives_the_collection_index_reorder(self):
        write_json(self.root / 'scan-report.json', {'active_group': 'C', 'state': 'stopped'})
        index = {'pages_by_title': {'x': 1}, 'pages': 2, 'at': 0.0}
        with patch.object(scanning, 'ensure_collection_index', return_value=index), \
             patch.object(scanning, 'order_by_index', side_effect=lambda groups, _index: sorted(groups)):
            result = scanning.scan_all(self.nav)
        self.assertEqual(result['state'], 'done')
        self.assertEqual(self.entered(), ['C', 'A', 'B'])
        self.assertIn('从上次中断的分区 C 继续扫描', self.logged())
        self.assertEqual(read_json(self.root / 'scan-report.json')['planned_groups'], ['C', 'A', 'B'])

    def test_group_left_with_unseen_nodes_is_scanned_last(self):
        write_json(self.root / 'progress.json', {'A-1:normal': unseen('A-1')})
        write_json(self.root / 'scan-report.json', {'active_group': 'A', 'state': 'stopped'})
        scanning.scan_all(self.nav)
        self.assertEqual(self.entered(), ['B', 'C', 'A'])
        report = read_json(self.root / 'scan-report.json')
        self.assertEqual(report['deferred_groups'], ['A'])
        self.assertNotIn('从上次中断的分区', self.logged())
        self.assertIn('放到本轮最后', self.logged())

    def test_failed_entry_is_remembered_deferred_and_cleared_on_success(self):
        self.nav.enter.side_effect = lambda group: (_ for _ in ()).throw(RuntimeError('分区入口未确认')) if group == 'A' else None
        first = scanning.scan_all(self.nav)
        self.assertEqual(first['state'], 'partial')
        self.assertEqual(self.entered(), ['A', 'B', 'C'])
        self.assertEqual(list(read_json(self.root / 'scan-report.json')['failed_groups']), ['A'])
        # Second round: B/C are complete, so add new work to show A now goes last.
        self.book = book('A', 'B', 'C', 'D')
        self.nav.enter.reset_mock(side_effect=True)
        scanning.scan_all(self.nav)
        self.assertEqual(self.entered(), ['D', 'A'])
        report = read_json(self.root / 'scan-report.json')
        self.assertEqual(report['failed_groups'], {})
        self.assertEqual(report['state'], 'finished')

    def test_pre_0929_report_errors_are_carried_over(self):
        write_json(self.root / 'scan-report.json',
                   {'state': 'partial', 'active_group': 'C',
                    'chapters': [{'chapter': 'A', 'error': '未找到章节入口：x'}, {'chapter': 'B'}]})
        scanning.scan_all(self.nav)
        self.assertEqual(self.entered(), ['C', 'B', 'A'])

    def test_absent_base_chapter_defers_every_group_sharing_its_entry(self):
        self.book = book('GA-TR', 'GA-EX', 'B')
        write_json(self.root / 'nav-hints.json', {'pages': {}, 'missing': {'GA': {'at': 1.0, 'attempts': 1}}})
        scanning.scan_all(self.nav)
        self.assertEqual(self.entered(), ['B', 'GA-TR', 'GA-EX'])

    def test_corrupt_hint_file_does_not_block_scanning(self):
        (self.root / 'nav-hints.json').write_text('{broken', encoding='utf-8')
        self.assertEqual(scanning.absent_chapters(), set())
        write_json(self.root / 'nav-hints.json', {'missing': ['GA']})
        self.assertEqual(scanning.absent_chapters(), set())
        self.assertEqual(scanning.scan_all(self.nav)['state'], 'done')
        self.assertEqual(self.entered(), ['A', 'B', 'C'])


if __name__ == '__main__':
    unittest.main()
