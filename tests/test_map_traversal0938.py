"""Offline tests for the AutoStory 0.9.38 PanBoundary candidate.

Scope and evidence boundary
---------------------------
These tests are OFFLINE.  They load four retained SN screenshots and the
retained OCR JSON rows from a local fixture copy; no emulator, game, ADB, UI,
network, OCR backend, account, or runtime state is touched.  Shifted-label and
translated-pixel cases are synthetic/offline negatives.  They are not live
gameplay evidence and do not claim that the candidate is integrated or deployed.
"""

import ast
import copy
import json
import os
import sys
import unittest
from pathlib import Path

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent
FIXTURES = HERE.parent / 'release/evidence/flow0937'
ACTUAL_ROWS = HERE.parent / 'release/evidence/algorithm0938/real-ocr-rows.json'
from autostory.motion import map_motion, map_signature
from autostory import map_traversal


SN_CODES = ("SN-3", "SN-TR-1")


def load_json(name):
    return json.loads((FIXTURES / name).read_text("utf-8"))


def real_rows():
    """Merge retained full OCR rows with the zoom-recovered SN-TR-1 row."""
    full = load_json("sn-full-ocr.json")
    zoom = load_json("sn-zoom-ocr.json")
    recovered = []
    for tile in zoom:
        for row in tile.get("texts", []):
            if row.get("text") == "SN-TR-1":
                recovered.append(copy.deepcopy(row))
    if not recovered:
        raise AssertionError("fixture must recover SN-TR-1 from the zoom rows")
    # The retained zoom pass uses overlapping tiles, so the same recovered
    # code appears twice.  The full pass only saw the truncated SN-TR- row.
    # Use one recovered row; the detector must still reject runtime duplicates.
    rows = [copy.deepcopy(row) for row in full if row.get("text") != "SN-TR-"]
    rows.append(recovered[0])
    return rows


def frames():
    paths = sorted(FIXTURES.glob("179*.png"))[-4:]
    images = [
        cv2.imdecode(np.fromfile(str(path), np.uint8), cv2.IMREAD_COLOR)
        for path in paths
    ]
    if len(images) != 4 or any(image is None for image in images):
        raise AssertionError("four retained SN frames are required")
    return images


def frames_by_name():
    paths = sorted(FIXTURES.glob("179*.png"))[-4:]
    return {
        path.name: cv2.imdecode(np.fromfile(str(path), np.uint8), cv2.IMREAD_COLOR)
        for path in paths
    }


def real_ocr_rows():
    """Actual per-frame OCR rows retained by Codex in algorithm0938."""
    return json.loads(ACTUAL_ROWS.read_text("utf-8"))


def code_row(code, x1, y1, x2, y2, score=0.99, text=None):
    return {
        "text": code if text is None else text,
        "score": score,
        "x": (x1 + x2) / 2.0,
        "y": (y1 + y2) / 2.0,
        "box": [float(x1), float(y1), float(x2), float(y2)],
    }


def shifted(row, dx, dy):
    result = copy.deepcopy(row)
    box = list(result["box"])
    box[0] += dx
    box[1] += dy
    box[2] += dx
    box[3] += dy
    result["box"] = box
    result["x"] += dx
    result["y"] += dy
    return result


def extra_rows(*texts):
    return [
        {
            "text": text,
            "score": 0.99,
            "x": 640.0,
            "y": 300.0,
            "box": [600.0, 280.0, 680.0, 320.0],
        }
        for text in texts
    ]


def textured_frame(boxes):
    """Deterministic synthetic offline map with textured label regions."""
    rng = np.random.default_rng(938)
    image = np.full((720, 1280, 3), 13, np.uint8)
    for x1, y1, x2, y2 in boxes:
        image[y1:y2, x1:x2] = rng.integers(
            0, 256, (y2 - y1, x2 - x1, 3), dtype=np.uint8
        )
    return image


class PanBoundaryTests(unittest.TestCase):
    def test_first_observation_stores_and_returns_false(self):
        tracker = map_traversal.PanBoundary(SN_CODES)
        self.assertFalse(tracker.observe(frames()[-1], real_rows()))

    def test_two_stationary_pairs_confirm_on_real_sn_animation(self):
        images = frames()
        rows = real_rows()
        self.assertEqual({row["text"] for row in rows if row["text"].startswith("SN-")},
                         {"SN-3", "SN-TR-1"})
        tracker = map_traversal.PanBoundary(SN_CODES)
        results = [tracker.observe(image, rows) for image in images]
        self.assertEqual(results, [False, False, True, True])

    def test_actual_per_frame_ocr_box_edge_jitter_confirms(self):
        per_frame = real_ocr_rows()
        images = frames_by_name()
        self.assertEqual(len(per_frame), 4)
        sn3 = [
            next(row for row in item["texts"] if row["text"] == "SN-3")
            for item in per_frame
        ]
        self.assertEqual([row["y"] for row in sn3], [470.0, 470.0, 469.5, 470.0])
        self.assertEqual([row["box"][3] for row in sn3], [477.0, 477.0, 476.0, 477.0])
        tracker = map_traversal.PanBoundary(SN_CODES)
        observed = [
            tracker.observe(images[item["frame"]], item["texts"])
            for item in per_frame
        ]
        self.assertEqual(observed, [False, False, True, True])

    def test_adjacent_and_anchor_centre_displacement_are_both_required(self):
        image = frames()[-1]
        rows = real_rows()
        for axis in ("x", "y"):
            for offsets in ((0.7, -0.7), (0.6, 1.2)):
                with self.subTest(axis=axis, offsets=offsets):
                    tracker = map_traversal.PanBoundary(SN_CODES)
                    self.assertFalse(tracker.observe(image, rows))
                    answers = []
                    for delta in offsets:
                        dx = delta if axis == "x" else 0.0
                        dy = delta if axis == "y" else 0.0
                        shifted_rows = [shifted(row, dx, dy) for row in rows]
                        answers.append(tracker.observe(image, shifted_rows))
                    self.assertEqual(answers, [False, False])

    def test_same_codes_moving_one_two_five_pixels_both_axes_do_not_confirm(self):
        image = frames()[-1]
        rows = real_rows()
        for pixels in (1, 2, 5):
            with self.subTest(pixels=pixels):
                tracker = map_traversal.PanBoundary(SN_CODES)
                self.assertFalse(tracker.observe(image, rows))
                self.assertFalse(tracker.observe(image, rows))  # one stable pair
                moved = [shifted(row, pixels, -pixels) for row in rows]
                self.assertFalse(tracker.observe(image, moved))
                # The moved page is a fresh baseline, not a stale anchor.
                self.assertFalse(tracker.observe(image, moved))
                self.assertTrue(tracker.observe(image, moved))

    def test_motion_within_point_eight_five_tolerance_can_confirm(self):
        image = frames()[-1]
        rows = [shifted(row, 0.5, -0.5) for row in real_rows()]
        tracker = map_traversal.PanBoundary(SN_CODES)
        self.assertFalse(tracker.observe(image, rows))
        self.assertFalse(tracker.observe(image, rows))
        self.assertTrue(tracker.observe(image, rows))

    def test_translated_pixels_with_unchanged_ocr_are_rejected_by_map_gate(self):
        image = frames()[-1]
        rows = real_rows()
        # The two retained label ROIs, padded by four pixels.
        rois = ((424, 248, 59, 23), (444, 459, 39, 23))
        for dy, dx in ((2, 3), (5, 5)):
            with self.subTest(dy=dy, dx=dx):
                moved = np.roll(image, shift=(dy, dx), axis=(0, 1))
                for roi in rois:
                    self.assertFalse(
                        map_motion(
                            map_signature(image, roi), map_signature(moved, roi)
                        )["stable"]
                    )
                tracker = map_traversal.PanBoundary(SN_CODES)
                self.assertFalse(tracker.observe(image, rows))
                self.assertFalse(tracker.observe(moved, rows))
                # A rebaseline is required; the shifted frame must not become
                # the second confirmation pair of the original baseline.
                self.assertFalse(tracker.observe(moved, rows))
                self.assertTrue(tracker.observe(moved, rows))

    def test_appearing_label_set_resets(self):
        image = frames()[-1]
        rows = real_rows()
        extra = code_row("3-3", 200, 200, 260, 224)
        tracker = map_traversal.PanBoundary(SN_CODES + ("3-3",))
        self.assertFalse(tracker.observe(image, rows))
        self.assertFalse(tracker.observe(image, rows))
        self.assertFalse(tracker.observe(image, rows + [extra]))
        self.assertFalse(tracker.observe(image, rows + [extra]))
        self.assertTrue(tracker.observe(image, rows + [extra]))

    def test_disappearing_label_set_resets(self):
        image = frames()[-1]
        rows = real_rows()
        only_one = [row for row in rows if row["text"] == "SN-TR-1"]
        tracker = map_traversal.PanBoundary(SN_CODES)
        self.assertFalse(tracker.observe(image, rows))
        self.assertFalse(tracker.observe(image, rows))
        self.assertFalse(tracker.observe(image, only_one))
        self.assertFalse(tracker.observe(image, []))
        self.assertFalse(tracker.observe(image, rows))  # fresh baseline
        self.assertFalse(tracker.observe(image, rows))
        self.assertTrue(tracker.observe(image, rows))

    def test_replaced_label_set_resets(self):
        image = frames()[-1]
        rows = real_rows()
        replaced = []
        for row in rows:
            if row["text"] == "SN-3":
                replaced.append({**row, "text": "3-3"})
            elif row["text"] == "SN-TR-1":
                replaced.append({**row, "text": "4-4"})
            else:
                replaced.append(row)
        tracker = map_traversal.PanBoundary(SN_CODES + ("3-3", "4-4"))
        self.assertFalse(tracker.observe(image, rows))
        self.assertFalse(tracker.observe(image, rows))
        self.assertFalse(tracker.observe(image, replaced))
        self.assertFalse(tracker.observe(image, replaced))
        self.assertTrue(tracker.observe(image, replaced))

    def test_duplicate_code_is_rejected(self):
        image = frames()[-1]
        rows = real_rows()
        duplicate = copy.deepcopy(next(row for row in rows if row["text"] == "SN-3"))
        tracker = map_traversal.PanBoundary(SN_CODES)
        for _ in range(3):
            self.assertFalse(tracker.observe(image, rows + [duplicate]))

    def test_overlapping_boxes_are_rejected(self):
        image = frames()[-1]
        rows = real_rows()
        overlap = code_row("3-3", 450, 255, 510, 275)
        tracker = map_traversal.PanBoundary(SN_CODES + ("3-3",))
        for _ in range(3):
            self.assertFalse(tracker.observe(image, rows + [overlap]))

    def test_low_confidence_label_is_not_extracted(self):
        image = frames()[-1]
        rows = real_rows()
        for row in rows:
            if row["text"] == "SN-3":
                row["score"] = 0.89
        tracker = map_traversal.PanBoundary(SN_CODES)
        for _ in range(3):
            self.assertFalse(tracker.observe(image, rows))

    def test_score_threshold_is_inclusive(self):
        image = frames()[-1]
        rows = real_rows()
        for row in rows:
            if row["text"] == "SN-3":
                row["score"] = 0.9
        tracker = map_traversal.PanBoundary(SN_CODES)
        self.assertEqual(
            [tracker.observe(image, rows) for _ in range(3)],
            [False, False, True],
        )

    def test_single_valid_label_is_insufficient(self):
        image = frames()[-1]
        only_one = [row for row in real_rows() if row["text"] == "SN-TR-1"]
        tracker = map_traversal.PanBoundary(SN_CODES)
        for _ in range(3):
            self.assertFalse(tracker.observe(image, only_one))

    def test_nonfinite_coordinates_are_rejected(self):
        image = frames()[-1]
        rows = real_rows()
        base = code_row("3-3", 200, 200, 260, 224)
        cases = {
            "nan_x": {**copy.deepcopy(base), "x": float("nan")},
            "inf_y": {**copy.deepcopy(base), "y": float("inf")},
            "inf_box": {
                **copy.deepcopy(base),
                "box": [200.0, float("inf"), 260.0, 224.0],
            },
            "nan_score": {**copy.deepcopy(base), "score": float("nan")},
        }
        for name, extra in cases.items():
            with self.subTest(name=name):
                tracker = map_traversal.PanBoundary(SN_CODES + ("3-3",))
                for _ in range(3):
                    self.assertFalse(tracker.observe(image, rows + [extra]))

    def test_malformed_geometry_is_rejected(self):
        image = frames()[-1]
        rows = real_rows()
        valid = code_row("3-3", 200, 200, 260, 224)
        cases = {
            "short_box": {**copy.deepcopy(valid), "box": [200.0, 200.0, 260.0]},
            "reversed_box": {**copy.deepcopy(valid), "box": [260.0, 224.0, 200.0, 200.0]},
            "outside_right": code_row("3-3", 1200, 200, 1275, 224),
            "outside_top": code_row("3-3", 200, 89, 260, 224),
            "centre_outside_box": {**copy.deepcopy(valid), "x": 999.0},
            "string_coordinates": {
                **copy.deepcopy(valid),
                "box": ["200", "200", "260", "224"],
            },
            "missing_box": {key: value for key, value in valid.items() if key != "box"},
        }
        for name, extra in cases.items():
            with self.subTest(name=name):
                tracker = map_traversal.PanBoundary(SN_CODES + ("3-3",))
                for _ in range(3):
                    self.assertFalse(tracker.observe(image, rows + [extra]))

    def test_ocr_centre_antialias_tolerance_matches_existing_geometry(self):
        image = frames()[-1]
        base = real_rows()
        sn3 = next(row for row in base if row["text"] == "SN-3")
        near_rows = []
        for row in base:
            if row is sn3:
                near = copy.deepcopy(row)
                near["x"] = near["box"][0] - 1.5
                near["y"] = near["box"][1] - 1.5
                near_rows.append(near)
            else:
                near_rows.append(row)
        tracker = map_traversal.PanBoundary(SN_CODES)
        self.assertEqual(
            [tracker.observe(image, near_rows) for _ in range(3)],
            [False, False, True],
        )
        far_rows = []
        for row in base:
            if row is sn3:
                far = copy.deepcopy(row)
                far["x"] = far["box"][0] - 3.0
                far_rows.append(far)
            else:
                far_rows.append(row)
        tracker = map_traversal.PanBoundary(SN_CODES)
        for _ in range(3):
            self.assertFalse(tracker.observe(image, far_rows))

    def test_wrong_image_shape_dtype_and_none_are_rejected(self):
        rows = real_rows()
        bad_images = (
            None,
            [],
            np.zeros((360, 640, 3), np.uint8),
            np.zeros((720, 1280), np.uint8),
            np.zeros((720, 1280, 4), np.uint8),
            np.zeros((720, 1280, 3), np.float32),
        )
        for index, image in enumerate(bad_images):
            with self.subTest(index=index):
                tracker = map_traversal.PanBoundary(SN_CODES)
                self.assertFalse(tracker.observe(image, rows))
                self.assertFalse(tracker.observe(image, rows))

    def test_blank_frame_is_rejected_and_not_stored(self):
        image = frames()[-1]
        rows = real_rows()
        blank = np.zeros((720, 1280, 3), np.uint8)
        tracker = map_traversal.PanBoundary(SN_CODES)
        self.assertEqual(
            [tracker.observe(blank, rows) for _ in range(3)],
            [False, False, False],
        )
        # A real frame still needs a fresh baseline and two new pairs.
        self.assertFalse(tracker.observe(image, rows))
        self.assertFalse(tracker.observe(image, rows))
        self.assertTrue(tracker.observe(image, rows))

    def test_overlays_reset_and_block_boundary(self):
        image = frames()[-1]
        rows = real_rows()
        overlays = {
            "detail": extra_rows("开始行动"),
            "practice_detail": extra_rows("开始演习"),
            "settlement": extra_rows("行动结束", "MISSION RESULTS"),
            "confirmation": extra_rows("确认", "取消"),
            "confirmation_single": extra_rows("确认"),
            "purchase": extra_rows("是否花费源石兑换"),
            "purchase_single": extra_rows("购买"),
            "pause": extra_rows("放弃行动", "继续行动"),
            "pause_single": extra_rows("暂停"),
        }
        for name, overlay in overlays.items():
            with self.subTest(name=name):
                tracker = map_traversal.PanBoundary(SN_CODES)
                self.assertFalse(tracker.observe(image, rows))
                self.assertFalse(tracker.observe(image, rows))  # one stable pair
                self.assertFalse(tracker.observe(image, rows + overlay))
                # The overlay cleared evidence; a full new baseline is needed.
                self.assertFalse(tracker.observe(image, rows))
                self.assertFalse(tracker.observe(image, rows))
                self.assertTrue(tracker.observe(image, rows))

    def test_at_least_two_textured_label_regions_required(self):
        boxes = ((100, 150, 160, 174), (700, 400, 760, 424))
        rows = [
            code_row("3-3", *boxes[0]),
            code_row("4-4", *boxes[1]),
        ]
        image = textured_frame(boxes)
        flattened = image.copy()
        flattened[396:428, 696:764] = 0
        tracker = map_traversal.PanBoundary(("3-3", "4-4"))
        for _ in range(3):
            self.assertFalse(tracker.observe(flattened, rows))
        # The same labels with two textured regions are accepted.
        tracker = map_traversal.PanBoundary(("3-3", "4-4"))
        self.assertEqual(
            [tracker.observe(image, rows) for _ in range(3)],
            [False, False, True],
        )

    def test_non_sn_exact_codes_are_not_special_cased(self):
        images = frames()
        rows = []
        for row in real_rows():
            if row["text"] == "SN-3":
                rows.append({**row, "text": "3-3"})
            elif row["text"] == "SN-TR-1":
                rows.append({**row, "text": "4-4"})
            else:
                rows.append(row)
        tracker = map_traversal.PanBoundary(("3-3", "4-4"))
        self.assertEqual(
            [tracker.observe(image, rows) for image in images],
            [False, False, True, True],
        )

    def test_reset_clears_evidence_and_baseline(self):
        image = frames()[-1]
        rows = real_rows()
        tracker = map_traversal.PanBoundary(SN_CODES)
        self.assertFalse(tracker.observe(image, rows))
        self.assertFalse(tracker.observe(image, rows))  # one stable pair
        tracker.reset()
        self.assertFalse(tracker.observe(image, rows))  # new baseline
        self.assertFalse(tracker.observe(image, rows))
        self.assertTrue(tracker.observe(image, rows))

    def test_independent_instances_do_not_share_state(self):
        images = frames()
        rows = real_rows()
        first = map_traversal.PanBoundary(SN_CODES)
        second = map_traversal.PanBoundary(SN_CODES)
        self.assertFalse(first.observe(images[0], rows))
        self.assertFalse(second.observe(images[1], rows))
        self.assertFalse(first.observe(images[0], rows))
        self.assertFalse(second.observe(images[1], rows))
        self.assertTrue(first.observe(images[0], rows))
        self.assertTrue(second.observe(images[1], rows))
        first.reset()
        self.assertTrue(second.observe(images[1], rows))

    def test_confirmations_one_requires_one_pair(self):
        image = frames()[-1]
        rows = real_rows()
        tracker = map_traversal.PanBoundary(SN_CODES, confirmations=1)
        self.assertFalse(tracker.observe(image, rows))
        self.assertTrue(tracker.observe(image, rows))

    def test_candidate_source_is_pure_and_does_not_import_navigation_device_or_runtime(self):
        source = Path(map_traversal.__file__).read_text("utf-8")
        tree = ast.parse(source)
        imported = set()
        called = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.add(node.module or "")
            elif isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name):
                    called.add(node.func.id)
                elif isinstance(node.func, ast.Attribute):
                    called.add(node.func.attr)
        self.assertIn("autostory.motion", imported)
        self.assertTrue(
            imported
            <= {
                "__future__",
                "math",
                "re",
                "collections.abc",
                "cv2",
                "numpy",
                "autostory.motion",
            },
            imported,
        )
        for forbidden in (
            "open",
            "print",
            "input",
            "sleep",
            "tap",
            "swipe",
            "click",
            "screencap",
            "exec",
            "eval",
            "frame_motion",
            "signature",
        ):
            self.assertNotIn(forbidden, called)
        self.assertIn("map_signature", called)
        self.assertIn("map_motion", called)


if __name__ == "__main__":
    unittest.main()
