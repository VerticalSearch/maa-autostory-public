"""Independent regressions from actual OCR and counterexamples to the candidate."""
import copy
import json
from pathlib import Path
import unittest

import cv2
import numpy as np

from autostory.home_observation import map_codes
from autostory.map_traversal import PanBoundary

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / 'release/evidence/flow0937'
ROWS = ROOT / 'release/evidence/algorithm0938/real-ocr-rows.json'


class PanReviewTests(unittest.TestCase):
    def setUp(self):
        self.rows = json.loads(ROWS.read_text('utf-8'))
        self.frame = cv2.imdecode(np.fromfile(FIX / self.rows[-1]['frame'], np.uint8), 1)
        self.labels = self.rows[-1]['texts']

    def test_actual_ocr_box_edge_jitter_does_not_reset_stationary_pixels(self):
        tracker = PanBoundary(map_codes())
        observed = []
        for row in self.rows:
            image = cv2.imdecode(np.fromfile(FIX / row['frame'], np.uint8), 1)
            observed.append(tracker.observe(image, row['texts']))
        self.assertEqual(observed, [False, False, True, True])

    def test_adjacent_and_total_label_displacement_both_need_to_stay_small(self):
        for axis in ('x', 'y'):
            for offsets in ((.7, -.7), (.6, 1.2)):
                tracker = PanBoundary(map_codes())
                self.assertFalse(tracker.observe(self.frame, self.labels))
                answers = []
                for delta in offsets:
                    labels = copy.deepcopy(self.labels)
                    for row in labels:
                        if row['text'] in map_codes():
                            row[axis] += delta
                            for index in ((0, 2) if axis == 'x' else (1, 3)):
                                row['box'][index] += delta
                    answers.append(tracker.observe(self.frame, labels))
                self.assertEqual(answers, [False, False], (axis, offsets))

    def test_one_moving_label_cannot_be_outvoted_by_two_stationary_labels(self):
        image = self.frame.copy()
        roi = image[459:481, 444:482].copy()
        image[339:361, 766:804] = roi
        labels = copy.deepcopy(self.labels) + [
            {'text': 'SN-4', 'score': .99, 'x': 785, 'y': 350,
             'box': [770, 343, 800, 357]}]
        tracker = PanBoundary(map_codes())
        self.assertFalse(tracker.observe(image, labels))
        self.assertFalse(tracker.observe(image, labels))
        self.assertTrue(tracker.observe(image, labels))
        tracker.reset()
        tracker.observe(image, labels)
        for pixels in (1, 2):
            moved = image.copy()
            moved[339:361, 766:804] = np.roll(roi, pixels, axis=1)
            self.assertFalse(tracker.observe(moved, labels))

    def test_practice_start_control_clears_boundary_evidence(self):
        tracker = PanBoundary(map_codes())
        tracker.observe(self.frame, self.labels)
        tracker.observe(self.frame, self.labels)
        overlay = self.labels + [{'text': '开始演习'}]
        for _ in range(3):
            self.assertFalse(tracker.observe(self.frame, overlay))
        self.assertFalse(tracker.observe(self.frame, self.labels))
        self.assertFalse(tracker.observe(self.frame, self.labels))
        self.assertTrue(tracker.observe(self.frame, self.labels))


if __name__ == '__main__':
    unittest.main()
