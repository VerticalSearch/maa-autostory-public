"""Offline traversal integration; all input, records and workflow are mocked."""
import copy
import json
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import Mock, patch

import cv2
import numpy as np

from autostory.navigation import Navigator

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / 'release/evidence/flow0937'


def retained():
    image = cv2.imdecode(np.fromfile(FIX / '1793538140716021400.png', np.uint8), 1)
    rows = json.loads((FIX / 'sn-full-ocr.json').read_text('utf-8'))
    zoom = json.loads((FIX / 'sn-zoom-ocr.json').read_text('utf-8'))[0]['texts']
    rows += [r for r in zoom if r['text'] == 'SN-TR-1']
    return image, rows


def moved(frame, pixels):
    image, rows = frame
    rows = copy.deepcopy(rows)
    for row in rows:
        if row['text'].startswith('SN-'):
            row['x'] += pixels
            row['box'][0] += pixels
            row['box'][2] += pixels
    return np.roll(image, pixels, axis=1), rows


class PanNavigationTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stop = self.stack.enter_context(patch('autostory.navigation.check_stop'))
        self.stack.enter_context(patch('autostory.navigation.log'))
        self.stack.enter_context(patch('autostory.workflow.update'))
        self.read = self.stack.enter_context(patch('autostory.navigation.read_json', return_value={}))
        self.write = self.stack.enter_context(patch('autostory.navigation.write_json'))
        self.nav = Navigator.__new__(Navigator)
        self.nav.catalog = Mock()
        self.nav.catalog.stages.return_value = ['SN-3', 'SN-4', 'SN-5']
        self.nav.catalog.code.side_effect = lambda code: code
        self.nav.d = Mock()
        self.nav.v = Mock()
        self.nav.close_detail = Mock()
        self.nav.set_mode = Mock()
        self.nav.detail = Mock(return_value={'code': 'SN-4', 'mode': 'normal', 'state': 'cleared'})
        self.nav.tap_stage = Mock()
        self.frame = retained()

    def test_find_uses_fresh_boundary_evidence_for_each_direction(self):
        self.nav.observe = Mock(return_value=self.frame)
        self.nav.stage_candidates = Mock(return_value=[])
        with self.assertRaisesRegex(RuntimeError, '未找到 SN-5'):
            self.nav.find('SN-5', max_swipes=8)
        self.assertEqual(self.nav.observe.call_count, 6)
        self.assertEqual(self.nav.d.swipe.call_count, 4)
        self.nav.tap_stage.assert_not_called()
        self.nav.v.texts_zoom.assert_not_called()
        self.write.assert_not_called()

    def test_find_without_reliable_labels_obeys_existing_swipe_bound(self):
        self.nav.observe = Mock(return_value=(self.frame[0], []))
        self.nav.stage_candidates = Mock(return_value=[])
        with self.assertRaisesRegex(RuntimeError, '未找到 SN-5'):
            self.nav.find('SN-5', max_swipes=3)
        self.assertEqual(self.nav.d.swipe.call_count, 6)
        self.nav.tap_stage.assert_not_called()
        self.write.assert_not_called()

    def test_stop_during_search_does_not_repeat_or_reverse_input(self):
        self.nav.observe = Mock(return_value=self.frame)
        self.nav.stage_candidates = Mock(return_value=[])
        self.stop.side_effect = [None, InterruptedError('stop')]
        with self.assertRaises(InterruptedError):
            self.nav.find('SN-5')
        self.assertEqual(self.nav.d.swipe.call_count, 1)
        self.nav.tap_stage.assert_not_called()

    def test_scan_reaches_new_node_after_same_codes_move_across_pages(self):
        self.read.return_value = {'SN-3:normal': {'code': 'SN-3', 'mode': 'normal', 'state': 'cleared'}}
        pages = [moved(self.frame, offset) for offset in (0, 30, 60, 90)]
        pages[-1][1].append({'text': 'SN-4', 'score': .99, 'x': 760, 'y': 380,
                              'box': [735, 370, 785, 390]})
        # Three observations reach the initial left boundary. The forward pass
        # then moves four times, followed by a fresh pre-click observation.
        self.nav.observe = Mock(side_effect=[self.frame] * 3 + pages + [pages[-1]])
        self.nav.find = Mock(return_value={'code': 'SN-5', 'mode': 'normal', 'state': 'unknown'})
        results = self.nav.scan('SN', max_pages=4, include_raid=False, incremental=True)
        self.nav.tap_stage.assert_called_once()
        self.assertEqual(self.nav.tap_stage.call_args.args[1], 'SN-4')
        self.assertEqual(self.nav.tap_stage.call_args.args[0]['x'], 760)
        self.assertEqual([c.args[0] for c in self.nav.find.call_args_list], ['SN-5'])
        self.assertTrue(any(r['code'] == 'SN-4' for r in results))

    def test_detail_transition_resets_forward_boundary_observations(self):
        # SN-3 is newly read on the first forward page. That transition breaks
        # the boundary sequence, even when the retained screenshot looks equal.
        self.nav.catalog.stages.return_value = ['SN-3', 'SN-5']
        self.nav.observe = Mock(return_value=self.frame)
        self.nav.detail.return_value = {'code': 'SN-3', 'mode': 'normal', 'state': 'cleared'}
        self.nav.find = Mock(return_value={'code': 'SN-5', 'mode': 'normal', 'state': 'unknown'})
        self.nav.scan('SN', max_pages=8, include_raid=False)
        pages = [c.args[0] for c in self.nav.observe.call_args_list if c.args]
        self.assertEqual(pages, ['SN-map-0', 'SN-map-1', 'SN-map-2', 'SN-map-3'])
        self.nav.tap_stage.assert_called_once()

    def test_initial_scan_sweep_honors_stop_before_first_input(self):
        self.nav.observe = Mock(return_value=self.frame)
        self.stop.side_effect = InterruptedError('stop')
        with self.assertRaises(InterruptedError):
            self.nav.scan('SN', include_raid=False)
        self.nav.d.swipe.assert_not_called()
        self.nav.tap_stage.assert_not_called()
        self.write.assert_not_called()


if __name__ == '__main__':
    unittest.main()
