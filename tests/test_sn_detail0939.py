"""Focused regressions for the evidenced SN lower detail panel (0.9.38).

The four retained frames are the only SN detail evidence in this session.
These tests lock in the narrow recognizer and the motion contract:

* only a coherent lower-left OPERATION+SN-<digits> header plus bottom-right
  开始行动 / 剧情 / 代理指挥 is accepted;
* ordinary stage details, other maps, a lone start button, SN-TR, duplicates,
  low confidence, settlements, purchase/confirmation dialogs and paused
  battles fail closed; SN raid/practice-only are not evidenced and fail closed;
* the selected ROIs are the exact text/control boxes, not the animated panel
  or a broad backdrop;
* every selected textured anchor is stable across the four retained frames and
  rejects real 1/2/5 px horizontal/vertical motion, opposite motion and drift;
* the unchanged ``motion.settle(kind='map')`` gate needs two consecutive stable
  pairs and never accepts one.

Run with ``AUTOSTORY_FIXTURE_DIR`` pointing at the live
``runtime/screens/unstable`` directory; the retained-frame tests skip if the
images are unavailable.
"""
import copy
import inspect
import os
import re
import unittest
from itertools import combinations
from pathlib import Path

import cv2
import numpy as np

from autostory import motion, sn_detail

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = Path(os.environ.get(
    "AUTOSTORY_FIXTURE_DIR", ROOT / "runtime/screens/unstable"))
FRAME_IDS = (
    "1793686390590290400",
    "1793686394701358700",
    "1793686398695663800",
    "1793686402898714000",
)

# Exact full-frame RapidOCR rows measured from the retained frames.  Only the
# header segmentation and a few 1 px box edges vary between frames.
_BASE = (
    ("OPERATION.SN-1", .964, (94, 540, 215, 566)),
    ("中心广场", 1.000, (123, 565, 239, 599)),
    ("LV.20", .997, (478, 563, 526, 584)),
    ("详细情报", .999, (352, 570, 423, 594)),
    ("+", .925, (446, 574, 460, 589)),
    ("剧情", 1.000, (1008, 569, 1050, 596)),
    ("代理指挥", .998, (1149, 568, 1228, 595)),
    ("推荐平均等级", .998, (481, 581, 552, 601)),
    ("Q报酬", .971, (345, 627, 404, 654)),
    ("开始演习", .999, (972, 627, 1048, 651)),
    ("开始行动", .996, (1146, 625, 1225, 652)),
)


def _row(text, score, box):
    return {
        "text": text,
        "score": score,
        "x": (box[0] + box[2]) / 2,
        "y": (box[1] + box[3]) / 2,
        "box": list(box),
    }


def make_rows(header="OPERATION.SN-1", header_score=.964,
              header_box=(94, 540, 215, 566)):
    rows = [_row(text, score, box) for text, score, box in _BASE]
    rows[0] = _row(header, header_score, header_box)
    return rows


def split_rows():
    rows = make_rows()
    rows[0] = _row("OPERATION", .97, (94, 540, 180, 566))
    rows.insert(1, _row("SN-1", .98, (180, 540, 215, 566)))
    return rows


def _without(rows, label):
    return [row for row in rows if row["text"] != label]


def _replace(rows, label, text):
    return [{**row, "text": text} if row["text"] == label else row for row in rows]


def _settle(images, rois, timeout=3.5, per_frame=.34):
    state = {"t": 0.0}

    def clock():
        return state["t"]

    def capture():
        state["t"] += per_frame
        return images[int(state["t"] / per_frame - 1) % len(images)]

    return motion.settle(capture, lambda: None, lambda _seconds: None,
                         timeout=timeout, interval=.15, confirmations=2,
                         clock=clock, rois=rois, kind="map")


class RecognitionTests(unittest.TestCase):
    def assertRejected(self, rows, reason=None, code=None):
        result = sn_detail.classify(rows, code=code)
        self.assertFalse(result.ok, "expected rejection, got %r" % (result,))
        if reason:
            self.assertIn(reason, result.reason)
        self.assertEqual(result.rois, ())
        return result

    def test_retained_merged_header_forms_are_recognized(self):
        for header in ("OPERATION.SN-1", "OPERATIONSN-1", "OPERATION SN-1",
                       "OPERATION-SN-1"):
            result = sn_detail.classify(make_rows(header=header), code="SN-1")
            self.assertTrue(result.ok, (header, result.reason))
            self.assertEqual(result.code, "SN-1")
            self.assertEqual(result.mode, "normal")
            self.assertEqual(result.settle_kind, "map")
            self.assertEqual([a.name for a in result.anchors],
                             ["header", "action", "plot", "auto_deploy", "practice"])
            self.assertEqual(tuple(a.box for a in result.anchors),
                             ((94, 540, 215, 566), (1146, 625, 1225, 652),
                              (1008, 569, 1050, 596), (1149, 568, 1228, 595),
                              (972, 627, 1048, 651)))

    def test_split_header_is_coherent_and_recognized(self):
        result = sn_detail.classify(split_rows(), code="SN-1")
        self.assertTrue(result.ok, result.reason)
        self.assertEqual(result.anchors[0].box, (94, 540, 215, 566))

    def test_incoherent_split_header_is_rejected(self):
        rows = split_rows()
        rows[1] = _row("SN-1", .98, (180, 590, 215, 616))
        self.assertRejected(rows, "header")

    def test_split_code_outside_header_bounds_is_rejected(self):
        rows = split_rows()
        rows[1] = _row("SN-1", .98, (180, 480, 215, 506))
        self.assertRejected(rows, "header")

    def test_ordinary_5_2_top_header_is_rejected(self):
        rows = make_rows(header="OPERATION 5-2", header_box=(820, 70, 970, 96))
        self.assertRejected(rows, "header")

    def test_ordinary_5_2_lower_left_is_rejected(self):
        rows = make_rows(header="OPERATION 5-2")
        self.assertRejected(rows, "header")

    def test_sn_tr_header_is_rejected(self):
        rows = make_rows(header="OPERATION SN-TR-1")
        self.assertRejected(rows, "header")

    def test_header_outside_lower_left_is_rejected(self):
        rows = make_rows(header_box=(94, 300, 215, 326))
        self.assertRejected(rows, "header")

    def test_lone_start_action_is_rejected(self):
        rows = [row for row in make_rows() if row["text"] != "OPERATION.SN-1"]
        self.assertRejected(rows)

    def test_missing_action_is_rejected(self):
        self.assertRejected(_without(make_rows(), "开始行动"), "missing")

    def test_missing_plot_control_is_rejected(self):
        self.assertRejected(_without(make_rows(), "剧情"), "missing")

    def test_missing_auto_control_is_rejected(self):
        self.assertRejected(_without(make_rows(), "代理指挥"), "missing")

    def test_settlement_overlay_is_rejected(self):
        rows = make_rows() + [_row("行动结束", .99, (620, 300, 700, 330))]
        self.assertRejected(rows, "settlement")

    def test_purchase_and_exchange_overlays_are_rejected(self):
        for label in ("购买", "采购", "兑换", "源石", "合成玉", "花费"):
            rows = make_rows() + [_row(label, .99, (620, 300, 700, 330))]
            self.assertRejected(rows, "overlay")

    def test_confirmation_overlay_is_rejected(self):
        for label in ("确认", "确定", "取消"):
            rows = make_rows() + [_row(label, .99, (620, 300, 700, 330))]
            self.assertRejected(rows, "overlay")

    def test_paused_battle_overlay_is_rejected(self):
        rows = make_rows() + [
            _row("放弃行动", .99, (520, 620, 600, 650)),
            _row("继续行动", .99, (680, 620, 760, 650)),
        ]
        self.assertRejected(rows, "overlay")

    def test_raid_detail_is_not_evidenced_and_is_rejected(self):
        rows = _replace(make_rows(), "开始行动", "开始突袭")
        rows.append(_row("普通模式", .99, (1080, 680, 1160, 710)))
        self.assertRejected(rows, "raid")

    def test_alternate_start_action_is_rejected(self):
        rows = _replace(make_rows(), "开始行动", "开始作战")
        self.assertRejected(rows, "alternate")

    def test_practice_only_panel_is_rejected(self):
        self.assertRejected(_without(make_rows(), "开始行动"), "missing")

    def test_code_mismatch_is_rejected(self):
        self.assertRejected(make_rows(), "does not match", code="SN-2")

    def test_expected_code_must_be_a_strict_sn_code(self):
        self.assertRejected(make_rows(), "strict", code="5-2")
        self.assertRejected(make_rows(), "strict", code="SN-TR-1")
        self.assertRejected(make_rows(), "string", code=1)

    def test_lowercase_expected_code_is_normalised(self):
        result = sn_detail.classify(make_rows(), code="sn-1")
        self.assertTrue(result.ok, result.reason)
        self.assertEqual(result.code, "SN-1")

    def test_duplicate_header_is_rejected(self):
        rows = make_rows() + [_row("OPERATIONSN-1", .97, (94, 570, 215, 596))]
        self.assertRejected(rows, "header")

    def test_duplicate_action_is_rejected(self):
        rows = make_rows() + [_row("开始行动", .99, (1146, 658, 1225, 685))]
        self.assertRejected(rows, "not unique")

    def test_duplicate_control_is_rejected(self):
        rows = make_rows() + [_row("剧情", .99, (1008, 580, 1050, 604))]
        self.assertRejected(rows, "not unique")

    def test_low_confidence_header_is_rejected(self):
        self.assertRejected(make_rows(header_score=.89), "header")

    def test_invalid_header_box_is_rejected(self):
        self.assertRejected(make_rows(header_box=(94, 540, 94, 566)), "header")

    def test_generic_labels_only_are_rejected(self):
        rows = [_row("中心广场", 1.0, (123, 565, 239, 599)),
                _row("开始行动", .99, (1146, 625, 1225, 652)),
                _row("剧情", 1.0, (1008, 569, 1050, 596)),
                _row("代理指挥", .99, (1149, 568, 1228, 595))]
        self.assertRejected(rows, "header")

    def test_empty_or_malformed_input_is_rejected(self):
        for rows in (None, "", [], [{"text": "开始行动", "score": "high",
                                     "box": [1146, 625, 1225, 652]}]):
            self.assertFalse(sn_detail.classify(rows).ok)

    def test_classification_does_not_mutate_input(self):
        rows = make_rows()
        before = copy.deepcopy(rows)
        sn_detail.classify(rows, code="SN-1")
        self.assertEqual(rows, before)

    def test_selected_rois_are_small_text_boxes_not_the_panel_or_backdrop(self):
        result = sn_detail.classify(make_rows(), code="SN-1")
        self.assertTrue(result.ok, result.reason)
        self.assertEqual(len(result.rois), 5)
        for x, y, w, h in result.rois:
            self.assertGreater(w, 0)
            self.assertGreater(h, 0)
            self.assertLessEqual(w, 200)
            self.assertLessEqual(h, 60)
            self.assertLessEqual(x + w, 1280)
            self.assertLessEqual(y + h, 720)
        self.assertNotIn((5, 90, 1269, 420), result.rois)       # old broad backdrop
        self.assertNotIn((70, 525, 260, 150), result.rois)      # old animated panel
        self.assertNotIn((1000, 600, 270, 115), result.rois)    # old animated panel

    def test_no_click_api_and_no_image_imports(self):
        source = inspect.getsource(sn_detail)
        self.assertNotRegex(source, r"(?m)^\s*(import|from)\s+(cv2|numpy|autostory)")
        for name in ("tap", "click", "act", "start"):
            self.assertFalse(hasattr(sn_detail, name), name)

    def test_settle_kind_is_documented_map(self):
        result = sn_detail.classify(make_rows(), code="SN-1")
        self.assertEqual(result.settle_kind, "map")
        self.assertEqual(sn_detail.settle_rois(result), result.rois)
        self.assertEqual(sn_detail.settle_rois(sn_detail.Recognition(False, "x")), ())

    def test_revalidate_accepts_same_page_and_returns_fresh(self):
        first = sn_detail.classify(make_rows(), code="SN-1")
        fresh = sn_detail.revalidate(first, make_rows(header="OPERATIONSN-1"), code="SN-1")
        self.assertTrue(fresh.ok, fresh.reason)
        self.assertEqual(fresh.anchors[0].text, "OPERATIONSN-1")

    def test_revalidate_rejects_changed_code(self):
        first = sn_detail.classify(make_rows(), code="SN-1")
        fresh = sn_detail.revalidate(first, make_rows(header="OPERATION SN-2"), code=None)
        self.assertFalse(fresh.ok)
        self.assertIn("changed", fresh.reason)

    def test_revalidate_rejects_non_detail_page(self):
        first = sn_detail.classify(make_rows(), code="SN-1")
        fresh = sn_detail.revalidate(first, [_row("加载中", .99, (600, 340, 680, 380))])
        self.assertFalse(fresh.ok)

    def test_revalidate_rejects_invalid_prior_evidence(self):
        self.assertFalse(sn_detail.revalidate(sn_detail.Recognition(False, "x"),
                                               make_rows()).ok)


class RetainedFrameTests(unittest.TestCase):
    def frames(self):
        if not FIXTURE_DIR.is_dir():
            self.skipTest("retained SN detail frames unavailable")
        images = [cv2.imread(str(FIXTURE_DIR / (name + ".png"))) for name in FRAME_IDS]
        if any(image is None for image in images):
            self.skipTest("retained SN detail frames unavailable")
        return images

    def recognition(self):
        result = sn_detail.classify(make_rows(), code="SN-1")
        self.assertTrue(result.ok, result.reason)
        return result

    def test_all_anchors_are_textured(self):
        images = self.frames()
        result = self.recognition()
        for x, y, w, h in result.rois:
            for image in images:
                self.assertGreaterEqual(motion.region_texture(image, (x, y, w, h)), 8.0)

    def test_all_anchors_stable_across_all_four_frames(self):
        images = self.frames()
        result = self.recognition()
        for x, y, w, h in result.rois:
            signatures = [motion.map_signature(image, (x, y, w, h)) for image in images]
            for i, j in combinations(range(4), 2):
                report = motion.map_motion(signatures[i], signatures[j])
                self.assertTrue(report["stable"], ((x, y, w, h), i, j, report))
                self.assertGreaterEqual(report["correlation"], .92)
                self.assertGreaterEqual(report["response"], .8)
                self.assertLessEqual(max(abs(v) for v in report["translation"]), .85)

    def test_all_anchors_reject_1_2_5_pixel_motion(self):
        images = self.frames()
        result = self.recognition()
        for image in images:
            for x, y, w, h in result.rois:
                signature = motion.map_signature(image, (x, y, w, h))
                for axis in (0, 1):
                    for pixels in (1, 2, 5):
                        for sign in (1, -1):
                            shifted = np.roll(image, sign * pixels, axis=axis)
                            report = motion.map_motion(
                                signature, motion.map_signature(shifted, (x, y, w, h)))
                            self.assertFalse(
                                report["stable"],
                                ((x, y, w, h), axis, sign, pixels, report))

    def test_opposite_motion_and_one_pixel_drift_are_rejected(self):
        images = self.frames()
        result = self.recognition()
        image = images[0]
        for x, y, w, h in result.rois:
            roi = (x, y, w, h)
            plus = np.roll(image, 1, axis=1)
            minus = np.roll(image, -1, axis=1)
            self.assertFalse(motion.map_motion(
                motion.map_signature(plus, roi),
                motion.map_signature(minus, roi))["stable"], roi)
            one = np.roll(image, 1, axis=1)
            two = np.roll(image, 2, axis=1)
            self.assertFalse(motion.map_motion(
                motion.map_signature(image, roi),
                motion.map_signature(one, roi))["stable"], roi)
            self.assertFalse(motion.map_motion(
                motion.map_signature(one, roi),
                motion.map_signature(two, roi))["stable"], roi)

    def test_old_fixed_panel_rois_are_not_stable_across_the_frames(self):
        images = self.frames()
        rois = ((70, 525, 260, 150), (1000, 600, 270, 115))
        for roi in rois:
            signatures = [motion.signature(image, roi) for image in images]
            stable = all(motion.frame_motion(signatures[i], signatures[j])["stable"]
                         for i, j in combinations(range(4), 2))
            self.assertFalse(stable, roi)

    def test_settle_map_gate_confirms_two_consecutive_pairs(self):
        images = self.frames()
        result = self.recognition()
        _, report = _settle(images, result.rois)
        self.assertTrue(report["confirmed"], report)
        self.assertEqual(report["frames"], 3)
        self.assertEqual(report["roi_count"], 5)
        self.assertTrue(report["stable"])
        self.assertTrue(report["anchor_stable"])
        self.assertEqual(report["blank_streak"], 0)

    def test_settle_map_gate_rejects_one_stable_pair(self):
        images = self.frames()
        result = self.recognition()
        shifted = np.roll(images[0], 1, axis=1)
        _, report = _settle([images[0], shifted, shifted], result.rois, timeout=.8)
        self.assertTrue(report["stable"])
        self.assertFalse(report["confirmed"], report)
        self.assertEqual(report["frames"], 3)

    def test_settle_map_gate_rejects_one_pixel_full_sequence(self):
        images = self.frames()
        result = self.recognition()
        shifted = np.roll(images[0], 1, axis=1)
        _, report = _settle([images[0], shifted], result.rois, timeout=.8)
        self.assertFalse(report["confirmed"], report)

    def test_settle_map_gate_rejects_blackout(self):
        images = self.frames()
        result = self.recognition()
        black = np.zeros_like(images[0])
        _, report = _settle([images[0], black, black], result.rois)
        self.assertFalse(report["confirmed"], report)
        self.assertFalse(report["textured"])


if __name__ == "__main__":
    unittest.main()
