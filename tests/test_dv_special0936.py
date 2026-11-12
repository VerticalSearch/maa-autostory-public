"""Candidate unittest suite for dv_special.py.

ASCII-only source and output.  Fixtures are local copies of retained OCR rows
from release/evidence/flow0936; no device, browser, network or game input is
used.  The tests are evidence for the candidate module only, not live proof.
"""

import copy
import json
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
FIXTURES = HERE.parent / "release/evidence/flow0936"
sys.path.insert(0, str(HERE))

from autostory import dv_special


CODE_1 = "DV-S-1"
CODE_2 = "DV-S-2"
NAME_1 = "\u56f0\u5fc3"
NAME_2 = "\u8861\u8651"
ACCESS = "SPECIAL ACCESS CONTENT"
EXIT = "EXIT"
START = "\u5f00\u59cb\u884c\u52a8"
CONDITION = "\u67e5\u770b\u6761\u4ef6"
SETTLE = "\u884c\u52a8\u7ed3\u675f"
CONFIRM = "\u786e\u8ba4"
CANCEL = "\u53d6\u6d88"
WHETHER = "\u662f\u5426"
SPEND = "\u82b1\u8d39"
ORIGINIUM = "\u6e90\u77f3"
EXCHANGE = "\u5151\u6362"


def load(name):
    payload = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    if isinstance(payload, dict) and isinstance(payload.get("texts"), list):
        return payload["texts"]
    return payload


def scene(name="dv-special.json"):
    return copy.deepcopy(load(name))


def norm(value):
    return "".join(value.split()).upper()


def find(rows, text):
    wanted = norm(text)
    for row in rows:
        value = row.get("text", "")
        if isinstance(value, str) and norm(value) == wanted:
            return row
    raise AssertionError("fixture row not found: " + text)


def remove(rows, text):
    rows.remove(find(rows, text))


class PositiveTests(unittest.TestCase):
    def test_identify_dv_s_1(self):
        self.assertEqual(dv_special.identify(scene()), CODE_1)

    def test_identify_expected_code(self):
        self.assertEqual(dv_special.identify(scene(), CODE_1), CODE_1)
        self.assertIsNone(dv_special.identify(scene(), CODE_2))

    def test_panel_regions_exact(self):
        regions = dv_special.panel_regions(scene())
        self.assertEqual(regions, (
            (781, 170, 129, 42),
            (1090, 166, 80, 51),
            (63, 158, 175, 24),
            (1127, 63, 52, 28),
            (919, 578, 112, 32),
            (67, 580, 93, 34),
        ))
        self.assertIsInstance(regions, tuple)
        self.assertEqual(len(regions), 6)
        for region in regions:
            self.assertIsInstance(region, tuple)
            self.assertEqual(len(region), 4)
            for value in region:
                self.assertIsInstance(value, int)

    def test_regions_contain_their_anchor_points(self):
        rows = scene()
        points = [
            (float(find(rows, CODE_1)["x"]), float(find(rows, CODE_1)["y"])),
            (float(find(rows, NAME_1)["x"]), float(find(rows, NAME_1)["y"])),
            (float(find(rows, ACCESS)["x"]), float(find(rows, ACCESS)["y"])),
            (float(find(rows, EXIT)["x"]), float(find(rows, EXIT)["y"])),
            (float(find(rows, START)["x"]), float(find(rows, START)["y"])),
            (float(find(rows, CONDITION)["x"]), float(find(rows, CONDITION)["y"])),
        ]
        for region, (x, y) in zip(dv_special.panel_regions(rows), points):
            left, top, width, height = region
            self.assertTrue(left <= x <= left + width)
            self.assertTrue(top <= y <= top + height)

    def test_exit_target_is_fixed_cross(self):
        target = dv_special.exit_target(scene())
        self.assertEqual(target, {"x": 1208, "y": 76})
        self.assertIsInstance(target["x"], int)
        self.assertIsInstance(target["y"], int)

    def test_exit_target_is_not_start_action(self):
        target = dv_special.exit_target(scene())
        self.assertNotEqual(target, {"x": 975, "y": 594})
        self.assertNotEqual(target, {"x": 923, "y": 582})

    def test_dv_s_2_synthetic(self):
        rows = scene()
        find(rows, CODE_1)["text"] = CODE_2
        find(rows, NAME_1)["text"] = NAME_2
        self.assertEqual(dv_special.identify(rows), CODE_2)
        self.assertEqual(dv_special.identify(rows, CODE_2), CODE_2)
        self.assertIsNone(dv_special.identify(rows, CODE_1))
        self.assertEqual(len(dv_special.panel_regions(rows)), 6)
        self.assertEqual(dv_special.exit_target(rows), {"x": 1208, "y": 76})

    def test_missing_each_anchor_rejects(self):
        for label in (CODE_1, NAME_1, ACCESS, EXIT, START, CONDITION):
            rows = scene()
            remove(rows, label)
            self.assertIsNone(dv_special.identify(rows), label)
            self.assertEqual(dv_special.panel_regions(rows), (), label)
            self.assertIsNone(dv_special.exit_target(rows), label)

    def test_missing_boxes_only_blocks_regions(self):
        rows = scene()
        for row in rows:
            row.pop("box", None)
        self.assertEqual(dv_special.identify(rows), CODE_1)
        self.assertEqual(dv_special.panel_regions(rows), ())
        self.assertEqual(dv_special.exit_target(rows), {"x": 1208, "y": 76})

    def test_invalid_boxes_only_blocks_regions(self):
        rows = scene()
        find(rows, CODE_1)["box"] = [906, 174, 785, 208]
        self.assertEqual(dv_special.identify(rows), CODE_1)
        self.assertEqual(dv_special.panel_regions(rows), ())
        rows = scene()
        find(rows, CODE_1)["box"] = [900, 100, 2000, 300]
        self.assertEqual(dv_special.identify(rows), CODE_1)
        self.assertEqual(dv_special.panel_regions(rows), ())

    def test_score_boundary(self):
        rows = scene()
        find(rows, CODE_1)["score"] = 0.899999
        self.assertIsNone(dv_special.identify(rows))
        rows = scene()
        find(rows, CODE_1)["score"] = 0.90
        self.assertEqual(dv_special.identify(rows), CODE_1)

    def test_whitespace_and_ascii_case(self):
        rows = scene()
        find(rows, CODE_1)["text"] = "  dv-s-1  "
        find(rows, ACCESS)["text"] = "special access   content"
        find(rows, EXIT)["text"] = "exit"
        self.assertEqual(dv_special.identify(rows), CODE_1)
        self.assertEqual(dv_special.identify(rows, CODE_1), CODE_1)

    def test_body_variation_is_irrelevant(self):
        rows = [row for row in scene() if not (240 < float(row["y"]) < 330)]
        rows.append({"text": "arbitrary body", "score": 0.99,
                     "x": 900.0, "y": 280.0, "box": [800, 270, 1000, 290]})
        self.assertEqual(dv_special.identify(rows), CODE_1)
        self.assertEqual(len(dv_special.panel_regions(rows)), 6)


class NegativeSceneTests(unittest.TestCase):
    def test_cw_morse_is_rejected(self):
        rows = load("cw-morse.json")
        self.assertIsNone(dv_special.identify(rows))
        self.assertIsNone(dv_special.identify(rows, "CW-S"))
        self.assertEqual(dv_special.panel_regions(rows), ())
        self.assertIsNone(dv_special.exit_target(rows))

    def test_map_is_rejected(self):
        rows = load("dv-map.json")
        self.assertTrue(any(norm(row["text"]) == CODE_1 and row["x"] < 700
                            for row in rows))
        self.assertIsNone(dv_special.identify(rows))
        self.assertEqual(dv_special.panel_regions(rows), ())
        self.assertIsNone(dv_special.exit_target(rows))

    def test_condition_subpage_back_is_not_exit(self):
        rows = load("dv-condition.json")
        back = [row for row in rows if norm(row["text"]) == "BACK"]
        self.assertEqual(len(back), 1)
        self.assertLess(abs(float(back[0]["x"]) - 1153.0), 2.0)
        self.assertLess(abs(float(back[0]["y"]) - 77.0), 2.0)
        self.assertIsNone(dv_special.identify(rows))
        self.assertEqual(dv_special.panel_regions(rows), ())
        self.assertIsNone(dv_special.exit_target(rows))

    def test_back_failed_ocr_is_a_panel_but_not_state_evidence(self):
        rows = load("dv-back-failed.json")
        self.assertEqual(dv_special.identify(rows), CODE_1)
        self.assertEqual(dv_special.exit_target(rows), {"x": 1208, "y": 76})
        self.assertFalse(hasattr(dv_special, "cleared"))
        self.assertFalse(hasattr(dv_special, "state"))


class StrictnessTests(unittest.TestCase):
    def test_code_suffix_collisions(self):
        for bad in ("DV-S-10", "DV-S-1A", "XDV-S-1", "OPERATIONDV-S-1",
                    "DV-S-1-2", "DVS-1", "DV-S-1.0", "DV-S-1A "):
            rows = scene()
            find(rows, CODE_1)["text"] = bad
            self.assertIsNone(dv_special.identify(rows), bad)

    def test_access_suffix_collision(self):
        rows = scene()
        find(rows, ACCESS)["text"] = ACCESS + "X"
        self.assertIsNone(dv_special.identify(rows))

    def test_wrong_official_name(self):
        rows = scene()
        find(rows, NAME_1)["text"] = NAME_2
        self.assertIsNone(dv_special.identify(rows))
        rows = scene()
        find(rows, CODE_1)["text"] = CODE_2
        self.assertIsNone(dv_special.identify(rows))

    def test_duplicate_code_is_ambiguous(self):
        rows = scene()
        duplicate = copy.deepcopy(find(rows, CODE_1))
        duplicate["x"] = 600.0
        duplicate["y"] = 400.0
        duplicate["box"] = [560, 380, 640, 420]
        rows.append(duplicate)
        self.assertIsNone(dv_special.identify(rows))
        self.assertIsNone(dv_special.exit_target(rows))

    def test_conflicting_code_is_ambiguous(self):
        rows = scene()
        duplicate = copy.deepcopy(find(rows, CODE_1))
        duplicate["text"] = CODE_2
        rows.append(duplicate)
        self.assertIsNone(dv_special.identify(rows))

    def test_duplicate_name_is_ambiguous(self):
        rows = scene()
        duplicate = copy.deepcopy(find(rows, NAME_1))
        duplicate["x"] = 600.0
        duplicate["y"] = 400.0
        duplicate["box"] = [580, 380, 620, 420]
        rows.append(duplicate)
        self.assertIsNone(dv_special.identify(rows))

    def test_duplicate_access_is_ambiguous(self):
        rows = scene()
        duplicate = copy.deepcopy(find(rows, ACCESS))
        duplicate["x"] = 600.0
        duplicate["y"] = 400.0
        duplicate["box"] = [500, 380, 700, 420]
        rows.append(duplicate)
        self.assertIsNone(dv_special.identify(rows))

    def test_duplicate_exit_is_ambiguous(self):
        rows = scene()
        duplicate = copy.deepcopy(find(rows, EXIT))
        duplicate["x"] = 600.0
        duplicate["y"] = 400.0
        duplicate["box"] = [580, 380, 620, 420]
        rows.append(duplicate)
        self.assertIsNone(dv_special.identify(rows))
        self.assertIsNone(dv_special.exit_target(rows))

    def test_non_finite_or_out_of_frame_coordinates(self):
        for bad in (float("nan"), float("inf"), float("-inf"), -5.0, 2000.0,
                    None, "x"):
            rows = scene()
            find(rows, CODE_1)["x"] = bad
            self.assertIsNone(dv_special.identify(rows), repr(bad))
        rows = scene()
        del find(rows, CODE_1)["x"]
        self.assertIsNone(dv_special.identify(rows))
        rows = scene()
        find(rows, CODE_1)["y"] = float("nan")
        self.assertIsNone(dv_special.identify(rows))

    def test_low_confidence_anchor_rejects(self):
        for label in (CODE_1, NAME_1, ACCESS, EXIT, START, CONDITION):
            rows = scene()
            find(rows, label)["score"] = 0.89
            self.assertIsNone(dv_special.identify(rows), label)
        rows = scene()
        find(rows, CODE_1)["score"] = float("nan")
        self.assertIsNone(dv_special.identify(rows))

    def test_settlement_text_rejects(self):
        for marker in (SETTLE, "MISSIONRESULTS", "MISSIONFAILED",
                       "MISSIONACCOMPLISHED"):
            rows = scene()
            rows.append({"text": marker, "score": 0.99})
            self.assertIsNone(dv_special.identify(rows), marker)
            self.assertIsNone(dv_special.exit_target(rows), marker)
            rows = scene()
            rows.append({"text": marker})
            self.assertIsNone(dv_special.identify(rows), marker + ":no-score")

    def test_purchase_overlay_rejects(self):
        rows = scene()
        rows.append({"text": "\u662f\u5426\u82b1\u8d39", "score": 0.99})
        self.assertIsNone(dv_special.identify(rows))
        rows = scene()
        rows.append({"text": "\u662f\u5426\u82b1\u8d39"})
        self.assertIsNone(dv_special.identify(rows))
        rows = scene()
        rows.append({"text": ORIGINIUM, "score": 0.99})
        rows.append({"text": EXCHANGE, "score": 0.99})
        self.assertIsNone(dv_special.identify(rows))

    def test_confirmation_overlay_rejects(self):
        rows = scene()
        rows.append({"text": CONFIRM, "score": 0.99})
        rows.append({"text": CANCEL, "score": 0.99})
        self.assertIsNone(dv_special.identify(rows))
        rows = scene()
        rows.append({"text": CONFIRM})
        rows.append({"text": CANCEL})
        self.assertIsNone(dv_special.identify(rows))
        rows = scene()
        rows.append({"text": WHETHER, "score": 0.99})
        rows.append({"text": SPEND, "score": 0.99})
        self.assertIsNone(dv_special.identify(rows))

    def test_wrong_or_invalid_expected_code(self):
        for bad in ("dv-s-1", "DV-S-3", "DV-S-10", "DV-S-1A", "", 123):
            self.assertIsNone(dv_special.identify(scene(), bad), repr(bad))

    def test_empty_and_malformed_inputs(self):
        for value in ([], (), None, [None, 1, "x"], [{}], [{"text": ""}],
                      [{"text": CODE_1, "score": 0.99}]):
            self.assertIsNone(dv_special.identify(value), repr(value))
            self.assertEqual(dv_special.panel_regions(value), (), repr(value))
            self.assertIsNone(dv_special.exit_target(value), repr(value))

    def test_public_api_surface(self):
        public = {name for name in dir(dv_special)
                  if not name.startswith("_") and callable(getattr(dv_special, name))}
        self.assertEqual(public, {"identify", "panel_regions", "exit_target"})
        self.assertEqual(set(dv_special.__all__),
                         {"identify", "panel_regions", "exit_target"})



if __name__ == "__main__":
    unittest.main(verbosity=2)
