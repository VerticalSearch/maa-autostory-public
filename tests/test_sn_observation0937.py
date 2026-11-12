"""Unit tests for the bounded SN small-map OCR fallback (candidate)."""
from __future__ import annotations

import copy
import json
import pathlib
import unittest

import numpy as np

from autostory import sn_observation as sn

HERE = pathlib.Path(__file__).resolve().parent
FIXTURES = HERE.parent / 'release/evidence/flow0937'

# Authored escapes keep this test file ASCII while matching retained OCR text.
FREE = '\u81ea\u7531\u63a2\u7d22'
REGION = '\u683c\u5170\u6cd5\u6d1b'
OTHER_REGION = '\u8499\u5c18\u56fe\u518c'
START = '\u5f00\u59cb\u884c\u52a8'
START_EXERCISE = '\u5f00\u59cb\u6f14\u4e60'
DETAIL = '\u8be6\u7ec6\u60c5\u62a5'
SETTLE = '\u884c\u52a8\u7ed3\u675f'
CONFIRM = '\u786e\u8ba4'
PURCHASE = '\u662f\u5426\u82b1\u8d39'
EXCHANGE = '\u6e90\u77f3\u5151\u6362'
PAUSE_A = '\u653e\u5f03\u884c\u52a8'
PAUSE_B = '\u7ee7\u7eed\u884c\u52a8'

KNOWN = frozenset({'SN-1', 'SN-2', 'SN-3', 'SN-4', 'SN-5',
                   'SN-TR-1', 'SN-6'})


def load_json(name):
    return json.loads((FIXTURES / name).read_text(encoding='utf-8'))


def item(text, x=640.0, y=300.0, box=(630, 290, 650, 310), score=0.99):
    return {'text': text, 'score': score, 'x': float(x), 'y': float(y),
            'box': [float(value) for value in box]}


class ZoomMap:
    def __init__(self, mapping=None):
        self.mapping = {tuple(roi): copy.deepcopy(rows)
                        for roi, rows in (mapping or {}).items()}
        self.calls = []
        self.scales = []

    def __call__(self, image, roi, scale=2):
        self.calls.append(tuple(roi))
        self.scales.append(scale)
        return copy.deepcopy(self.mapping.get(tuple(roi), []))


def fixture_zoom():
    rows = load_json('sn-zoom-ocr.json')
    return ZoomMap({tuple(row['roi']): row['texts'] for row in rows})


class FrameTests(unittest.TestCase):
    def test_retained_frame_is_1280x720_png(self):
        data = (FIXTURES / '1793538140716021400.png').read_bytes()
        self.assertEqual(data[:8], b'\x89PNG\r\n\x1a\n')
        self.assertEqual(int.from_bytes(data[16:20], 'big'), 1280)
        self.assertEqual(int.from_bytes(data[20:24], 'big'), 720)


class PositiveRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.image = np.zeros((720, 1280, 3), dtype=np.uint8)
        self.full = load_json('sn-full-ocr.json')

    def test_truncated_label_recovered_once(self):
        zoom = fixture_zoom()
        out = sn.enrich(self.image, self.full, zoom, lambda: None, KNOWN)
        labels = [entry['text'] for entry in out]
        self.assertEqual(labels.count('SN-TR-1'), 1)
        self.assertEqual(labels.count('SN-3'), 1)
        self.assertEqual(zoom.calls, [(0, 90, 480, 300)])
        self.assertEqual(zoom.scales, [2])

    def test_original_labels_are_preserved(self):
        out = sn.enrich(self.image, self.full, fixture_zoom(), lambda: None,
                        KNOWN)
        labels = [entry['text'] for entry in out]
        for entry in self.full:
            self.assertIn(entry['text'], labels)
        self.assertIn('SN-TR-', labels)
        self.assertIn(FREE, labels)
        self.assertIn(REGION, labels)
        self.assertEqual(len(out), len(self.full) + 1)

    def test_recovered_codes_are_anchor_eligible(self):
        out = sn.enrich(self.image, self.full, fixture_zoom(), lambda: None,
                        KNOWN)
        candidates = {}
        for entry in out:
            code = str(entry.get('text', '')).replace(' ', '').upper()
            box = entry.get('box', ())
            if code not in KNOWN or entry.get('score', 0) < 0.9:
                continue
            if len(box) != 4:
                continue
            x1, y1, x2, y2 = box
            if 5 <= x1 < x2 <= 1274 and 90 <= y1 < y2 <= 610:
                candidates.setdefault(code, []).append(entry)
        self.assertEqual(set(candidates), {'SN-3', 'SN-TR-1'})
        self.assertTrue(all(len(rows) == 1 for rows in candidates.values()))

    def test_two_codes_do_not_zoom(self):
        texts = self.full + [item('SN-1', 900, 500, (880, 492, 920, 508))]
        zoom = fixture_zoom()
        out = sn.enrich(self.image, texts, zoom, lambda: None, KNOWN)
        self.assertEqual(zoom.calls, [])
        self.assertEqual(out, texts)

    def test_low_confidence_second_code_still_zooms(self):
        texts = self.full + [item('SN-1', 900, 500, (880, 492, 920, 508),
                                  score=0.89)]
        zoom = fixture_zoom()
        out = sn.enrich(self.image, texts, zoom, lambda: None, KNOWN)
        self.assertEqual(len(zoom.calls), 1)
        self.assertIn('SN-TR-1', [entry['text'] for entry in out])

    def test_code_must_be_in_known_codes(self):
        zoom = ZoomMap()
        out = sn.enrich(self.image, self.full, zoom, lambda: None,
                        {'SN-3'})
        self.assertEqual(len(zoom.calls), 6)
        self.assertEqual(out, self.full)

    def test_truncated_text_is_never_repaired(self):
        mapping = {roi: [item('SN-TR-', 453.75, 259.25,
                              (428.5, 252.0, 479.0, 266.5))]
                   for roi in sn.TILES}
        zoom = ZoomMap(mapping)
        # Even a malformed known-code collection must not turn a fragment into
        # a battle code.
        out = sn.enrich(self.image, self.full, zoom, lambda: None,
                        KNOWN | {'SN-TR-'})
        self.assertNotIn('SN-TR-1', [entry['text'] for entry in out])
        self.assertEqual(len(zoom.calls), 6)


class NegativeGateTests(unittest.TestCase):
    def setUp(self):
        self.image = np.zeros((720, 1280, 3), dtype=np.uint8)
        self.full = load_json('sn-full-ocr.json')

    def test_missing_free_explore_does_not_trigger(self):
        texts = [entry for entry in self.full if entry['text'] != FREE]
        zoom = fixture_zoom()
        out = sn.enrich(self.image, texts, zoom, lambda: None, KNOWN)
        self.assertEqual(zoom.calls, [])
        self.assertEqual(out, texts)

    def test_missing_region_does_not_trigger(self):
        texts = [entry for entry in self.full if entry['text'] != REGION]
        zoom = fixture_zoom()
        out = sn.enrich(self.image, texts, zoom, lambda: None, KNOWN)
        self.assertEqual(zoom.calls, [])
        self.assertEqual(out, texts)

    def test_wrong_region_does_not_trigger(self):
        texts = [entry if entry['text'] != REGION else item(OTHER_REGION,
                 1098.5, 661.5, (1061, 650, 1136, 673))
                 for entry in self.full]
        zoom = fixture_zoom()
        out = sn.enrich(self.image, texts, zoom, lambda: None, KNOWN)
        self.assertEqual(zoom.calls, [])
        self.assertEqual(out, texts)

    def test_home_page_does_not_trigger(self):
        texts = [item('\u91c7\u8d2d\u4e2d\u5fc3'),
                 item('\u516c\u5f00\u62db\u52df')]
        zoom = fixture_zoom()
        self.assertEqual(sn.enrich(self.image, texts, zoom, lambda: None,
                                   KNOWN), texts)
        self.assertEqual(zoom.calls, [])

    def test_full_frame_miss_can_recover_only_exact_codes_from_bounded_tiles(self):
        texts = [entry for entry in self.full if entry['text'] != 'SN-3']
        zoom = fixture_zoom()
        out = sn.enrich(self.image, texts, zoom, lambda: None, KNOWN)
        self.assertLessEqual(len(zoom.calls),6)
        self.assertGreater(len(zoom.calls),0)
        self.assertIn('SN-3',[row['text'] for row in out])
        self.assertIn('SN-TR-1',[row['text'] for row in out])

    def test_empty_known_codes_does_not_trigger(self):
        zoom = fixture_zoom()
        out = sn.enrich(self.image, self.full, zoom, lambda: None, set())
        self.assertEqual(zoom.calls, [])
        self.assertEqual(out, self.full)

    def test_wrong_image_does_not_trigger(self):
        zoom = fixture_zoom()
        for image in (np.zeros((720, 1280, 3), dtype=np.float32),
                      np.zeros((720, 1280), dtype=np.uint8),
                      np.zeros((1080, 1920, 3), dtype=np.uint8),
                      None):
            with self.subTest(shape=getattr(image, 'shape', None)):
                out = sn.enrich(image, self.full, zoom, lambda: None, KNOWN)
                self.assertEqual(out, self.full)
        self.assertEqual(zoom.calls, [])

    def test_overlays_do_not_trigger(self):
        markers = (START, START_EXERCISE, DETAIL, SETTLE, CONFIRM,
                   PURCHASE, EXCHANGE, PAUSE_A, PAUSE_B, 'MISSIONRESULTS')
        for marker in markers:
            with self.subTest(marker=marker):
                texts = self.full + [item(marker)]
                zoom = fixture_zoom()
                out = sn.enrich(self.image, texts, zoom, lambda: None, KNOWN)
                self.assertEqual(zoom.calls, [])
                self.assertEqual(out, texts)


class GeometryTests(unittest.TestCase):
    def setUp(self):
        self.image = np.zeros((720, 1280, 3), dtype=np.uint8)
        self.full = load_json('sn-full-ocr.json')
        self.without_code = [entry for entry in self.full
                             if entry['text'] != 'SN-3']

    def assert_no_trigger(self, bad):
        texts = self.without_code + [bad]
        zoom = fixture_zoom()
        out = sn.enrich(self.image, texts, zoom, lambda: None, KNOWN)
        self.assertEqual(zoom.calls, [])
        self.assertEqual(out, texts)

    def test_nonfinite_box(self):
        self.assert_no_trigger(item('SN-3', 463, 470,
                                    (float('nan'), 463, 478, 477)))

    def test_nonfinite_centre(self):
        self.assert_no_trigger(item('SN-3', float('inf'), 470,
                                    (448, 463, 478, 477)))

    def test_box_outside_frame(self):
        self.assert_no_trigger(item('SN-3', 1272, 470,
                                    (1250, 463, 1300, 477)))

    def test_box_outside_map_area(self):
        self.assert_no_trigger(item('SN-3', 463, 470,
                                    (448, 60, 478, 80)))
        self.assert_no_trigger(item('SN-3', 463, 470,
                                    (448, 590, 478, 620)))

    def test_centre_does_not_match_box(self):
        self.assert_no_trigger(item('SN-3', 1000, 470,
                                    (448, 463, 478, 477)))

    def test_low_confidence_code(self):
        self.assert_no_trigger(item('SN-3', 463, 470,
                                    (448, 463, 478, 477), score=0.89))

    def test_bad_zoom_boxes_are_not_merged(self):
        bad_rows = [
            item('SN-TR-1', 453.75, 259.25,
                 (428.5, 252.0, 1300.0, 266.5)),
            item('SN-TR-1', float('nan'), 259.25,
                 (428.5, 252.0, 479.0, 266.5)),
            item('SN-TR-1', 453.75, 259.25,
                 (428.5, 50.0, 479.0, 70.0)),
        ]
        mapping = {sn.TILES[0]: bad_rows}
        zoom = ZoomMap(mapping)
        out = sn.enrich(self.image, self.full, zoom, lambda: None, KNOWN)
        self.assertEqual(len(zoom.calls), 6)
        self.assertNotIn('SN-TR-1', [entry['text'] for entry in out])


class DedupAmbiguityTests(unittest.TestCase):
    def setUp(self):
        self.image = np.zeros((720, 1280, 3), dtype=np.uint8)
        self.full = load_json('sn-full-ocr.json')

    def test_duplicate_zoom_detection_added_once(self):
        rows = [item('SN-TR-1', 453.75, 259.25,
                     (428.5, 252.0, 479.0, 266.5)),
                item('SN-TR-1', 453.75, 259.0,
                     (428.0, 251.5, 479.5, 266.5))]
        zoom = ZoomMap({sn.TILES[0]: rows})
        out = sn.enrich(self.image, self.full, zoom, lambda: None, KNOWN)
        self.assertEqual(len(zoom.calls), 1)
        labels = [entry['text'] for entry in out]
        self.assertEqual(labels.count('SN-TR-1'), 1)

    def test_duplicate_of_existing_code_not_added(self):
        rows = [item('SN-3', 463.0, 470.0, (448.0, 463.0, 478.0, 477.0))]
        zoom = ZoomMap({sn.TILES[0]: rows})
        out = sn.enrich(self.image, self.full, zoom, lambda: None, KNOWN)
        self.assertEqual(len(zoom.calls), 6)
        labels = [entry['text'] for entry in out]
        self.assertEqual(labels.count('SN-3'), 1)
        self.assertEqual(len(out), len(self.full))

    def test_same_code_at_two_locations_is_ambiguous(self):
        rows = [item('SN-TR-1', 200.0, 200.0, (185, 193, 215, 207)),
                item('SN-TR-1', 1000.0, 500.0, (985, 493, 1015, 507))]
        zoom = ZoomMap({sn.TILES[0]: rows})
        out = sn.enrich(self.image, self.full, zoom, lambda: None, KNOWN)
        self.assertEqual(len(zoom.calls), 6)
        self.assertNotIn('SN-TR-1', [entry['text'] for entry in out])

    def test_two_codes_at_one_box_are_ambiguous(self):
        rows = [item('SN-TR-1', 700.0, 300.0, (686, 293, 714, 307)),
                item('SN-1', 700.0, 300.0, (686, 293, 714, 307))]
        zoom = ZoomMap({sn.TILES[0]: rows})
        out = sn.enrich(self.image, self.full, zoom, lambda: None, KNOWN)
        self.assertEqual(len(zoom.calls), 6)
        labels = [entry['text'] for entry in out]
        self.assertNotIn('SN-TR-1', labels)
        self.assertNotIn('SN-1', labels)

    def test_ambiguous_initial_codes_do_not_trigger(self):
        texts = self.full + [item('SN-1', 463.0, 470.0,
                                  (448.0, 463.0, 478.0, 477.0))]
        zoom = fixture_zoom()
        out = sn.enrich(self.image, texts, zoom, lambda: None, KNOWN)
        self.assertEqual(zoom.calls, [])
        self.assertEqual(out, texts)


class TileAndStopTests(unittest.TestCase):
    def setUp(self):
        self.image = np.zeros((720, 1280, 3), dtype=np.uint8)
        self.full = load_json('sn-full-ocr.json')

    def test_six_bounded_tiles(self):
        zoom = ZoomMap()
        sn.enrich(self.image, self.full, zoom, lambda: None, KNOWN)
        self.assertEqual(len(zoom.calls), 6)
        self.assertEqual(zoom.calls, list(sn.TILES))
        self.assertEqual(zoom.scales, [2] * 6)
        self.assertEqual(sn.TILES, ((0, 90, 480, 300), (400, 90, 480, 300),
                                    (800, 90, 480, 300), (0, 310, 480, 300),
                                    (400, 310, 480, 300),
                                    (800, 310, 480, 300)))
        for left, top, width, height in zoom.calls:
            self.assertEqual((width, height), (480, 300))
            self.assertLessEqual(left + width, 1280)
            self.assertLessEqual(top + height, 720)
        self.assertEqual(480 - (400 - 0), 80)
        self.assertEqual(480 - (800 - 400), 80)
        self.assertEqual(300 - (310 - 90), 80)

    def test_stop_before_first_zoom_propagates(self):
        def stop():
            raise InterruptedError('stopped')

        zoom = ZoomMap()
        with self.assertRaises(InterruptedError):
            sn.enrich(self.image, self.full, zoom, stop, KNOWN)
        self.assertEqual(zoom.calls, [])

    def test_truthy_stop_return_propagates(self):
        zoom = ZoomMap()
        with self.assertRaises(InterruptedError):
            sn.enrich(self.image, self.full, zoom, lambda: True, KNOWN)
        self.assertEqual(zoom.calls, [])

    def test_stop_mid_scan_propagates(self):
        calls = {'count': 0}

        def stop():
            calls['count'] += 1
            if calls['count'] == 3:
                raise InterruptedError('stopped')

        zoom = ZoomMap()
        with self.assertRaises(InterruptedError):
            sn.enrich(self.image, self.full, zoom, stop, KNOWN)
        self.assertEqual(len(zoom.calls), 2)

    def test_stop_not_called_on_nonmatching_page(self):
        texts = [entry for entry in self.full if entry['text'] != FREE]

        def stop():
            raise AssertionError('check_stop must not run off-page')

        zoom = ZoomMap()
        out = sn.enrich(self.image, texts, zoom, stop, KNOWN)
        self.assertEqual(out, texts)
        self.assertEqual(zoom.calls, [])


if __name__ == '__main__':
    unittest.main()
