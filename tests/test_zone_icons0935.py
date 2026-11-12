"""Focused candidate tests for zone_icons.match_zone_icon (flow0935)."""
import hashlib
import json
import unittest
from pathlib import Path

import cv2
import numpy as np

from autostory import zone_icons

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "release/evidence/flow0935/icons"
FIXTURES = BASE / "fixtures"
ASSETS = ROOT / "assets"
BAD_ASSETS = FIXTURES / "bad_assets"

_TARGET_ASSETS = {
    "NL-EX": "zone-nl-ex.png",
    "NL-S": "zone-nl-s.png",
    "MN-EX": "zone-mn-ex.png",
}
_TARGET_SOURCES = {
    "NL-EX": {"NL"},
    "NL-S": {"NL"},
    "MN-EX": {"MN"},
}
_EXPECTED_CENTERS = {
    "NL-EX": (1079.0, 641.0),
    "NL-S": (1190.5, 641.0),
    "MN-EX": (1207.5, 628.5),
}
_CROP_ORIGINS = {
    "NL-EX": (1034, 610),
    "NL-S": (1153, 604),
    "MN-EX": (1165, 583),
}
_FRAME_CACHE = {}


def frame(name):
    if name not in _FRAME_CACHE:
        path = FIXTURES / name
        image = cv2.imdecode(np.fromfile(str(path), np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            raise AssertionError(f"cannot decode fixture {path}")
        _FRAME_CACHE[name] = image
    return _FRAME_CACHE[name]


def template(name):
    path = ASSETS / name
    image = cv2.imdecode(np.fromfile(str(path), np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise AssertionError(f"cannot decode asset {path}")
    return image


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


class PositiveTests(unittest.TestCase):
    def assert_contract(self, result, target, delta=0.75):
        self.assertIsInstance(result, dict)
        self.assertEqual(set(result), {"x", "y", "text", "score"})
        self.assertGreaterEqual(result["score"], zone_icons.THRESHOLD)
        self.assertLessEqual(result["score"], 1.0001)
        expected_x, expected_y = _EXPECTED_CENTERS[target]
        self.assertAlmostEqual(result["x"], expected_x, delta=delta)
        self.assertAlmostEqual(result["y"], expected_y, delta=delta)

    def test_nl_ex_icon_on_normal_nl_map(self):
        result = zone_icons.match_zone_icon(
            frame("zone-NL-EX.png"), "NL-EX", {"NL"}, ASSETS
        )
        self.assertIsNotNone(result)
        self.assertEqual(result["text"], "NL EX 图标")
        self.assert_contract(result, "NL-EX")

    def test_nl_s_icon_on_normal_nl_map(self):
        result = zone_icons.match_zone_icon(
            frame("zone-NL-EX.png"), "NL-S", {"NL"}, ASSETS
        )
        self.assertIsNotNone(result)
        self.assertEqual(result["text"], "NL S 图标")
        self.assert_contract(result, "NL-S")

    def test_mn_ex_icon_on_normal_mn_map(self):
        result = zone_icons.match_zone_icon(
            frame("zone-MN-TR.png"), "MN-EX", {"MN"}, ASSETS
        )
        self.assertIsNotNone(result)
        self.assertEqual(result["text"], "MN EX 图标")
        self.assert_contract(result, "MN-EX")


class SourceGroupTests(unittest.TestCase):
    def test_all_nl_source_groups_match_nl(self):
        nl = frame("zone-NL-EX.png")
        for target in ("NL-EX", "NL-S"):
            sources = ({"NL"}, {"NL-TR"}, {"NL-EX"}, {"NL-S"})
            for observed in sources:
                if target in observed:
                    continue
                with self.subTest(target=target, observed=observed):
                    self.assertIsNotNone(
                        zone_icons.match_zone_icon(nl, target, observed, ASSETS)
                    )

    def test_mn_source_groups_match_mn(self):
        mn = frame("zone-MN-TR.png")
        for observed in ({"MN"}, {"MN-TR"}):
            with self.subTest(observed=observed):
                self.assertIsNotNone(
                    zone_icons.match_zone_icon(mn, "MN-EX", observed, ASSETS)
                )

    def test_target_already_observed_is_rejected(self):
        cases = (
            (frame("zone-NL-EX.png"), "NL-EX", {"NL", "NL-EX"}),
            (frame("zone-NL-EX.png"), "NL-EX", {"NL-EX"}),
            (frame("zone-NL-EX.png"), "NL-S", {"NL", "NL-S"}),
            (frame("zone-NL-EX.png"), "NL-S", {"NL-S"}),
            (frame("zone-MN-TR.png"), "MN-EX", {"MN", "MN-EX"}),
            (frame("zone-MN-TR.png"), "MN-EX", {"MN-EX"}),
        )
        for image, target, observed in cases:
            with self.subTest(target=target, observed=observed):
                self.assertIsNone(
                    zone_icons.match_zone_icon(image, target, observed, ASSETS)
                )


class ContextTests(unittest.TestCase):
    def test_other_story_frames_are_rejected_even_with_matching_base_claim(self):
        for name in ("PV-EX.png", "HE-EX.png", "entry-IS.png"):
            image = frame(name)
            for target, observed in _TARGET_SOURCES.items():
                if target == "MN-EX" and name == "entry-IS.png":
                    observed = {"MN"}
                with self.subTest(frame=name, target=target):
                    self.assertIsNone(
                        zone_icons.match_zone_icon(image, target, observed, ASSETS)
                    )

    def test_normal_mn_mixed_context_is_rejected(self):
        # Base claim and pixels disagree, or the observed set mixes bases.
        cases = (
            (frame("zone-NL-EX.png"), "MN-EX", {"MN"}),
            (frame("zone-MN-TR.png"), "NL-EX", {"NL"}),
            (frame("zone-MN-TR.png"), "NL-S", {"NL"}),
            (frame("zone-NL-EX.png"), "NL-EX", {"NL", "MN"}),
            (frame("zone-NL-EX.png"), "NL-EX", {"NL-EX", "MN"}),
            (frame("zone-MN-TR.png"), "MN-EX", {"MN", "NL"}),
        )
        for image, target, observed in cases:
            with self.subTest(target=target, observed=observed):
                self.assertIsNone(
                    zone_icons.match_zone_icon(image, target, observed, ASSETS)
                )

    def test_wrong_or_foreign_base_is_rejected(self):
        nl = frame("zone-NL-EX.png")
        mn = frame("zone-MN-TR.png")
        cases = (
            (nl, "NL-EX", {"MN"}),
            (nl, "NL-EX", {"PV"}),
            (nl, "NL-EX", {"HE"}),
            (nl, "NL-S", {"MN-TR"}),
            (mn, "MN-EX", {"NL"}),
            (mn, "MN-EX", {"NL-TR"}),
            (mn, "MN-EX", {"PV"}),
        )
        for image, target, observed in cases:
            with self.subTest(target=target, observed=observed):
                self.assertIsNone(
                    zone_icons.match_zone_icon(image, target, observed, ASSETS)
                )

    def test_empty_or_malformed_observed_is_rejected(self):
        nl = frame("zone-NL-EX.png")
        cases = (
            None,
            set(),
            frozenset(),
            ["NL"],
            "NL",
            {"NL", 1},
            {"NL", None},
            {"nl"},
            {"NL-EX "},
            {"NL_EXTRA"},
        )
        for observed in cases:
            with self.subTest(observed=repr(observed)):
                self.assertIsNone(
                    zone_icons.match_zone_icon(nl, "NL-EX", observed, ASSETS)
                )

    def test_unsupported_targets_are_rejected(self):
        nl = frame("zone-NL-EX.png")
        for target in ("HE-EX", "PV-EX", "NL", "MN", "NL-EX ", "nl-ex", "", None):
            with self.subTest(target=repr(target)):
                self.assertIsNone(
                    zone_icons.match_zone_icon(nl, target, {"NL"}, ASSETS)
                )


class BadInputTests(unittest.TestCase):
    def test_blank_and_unrelated_images_are_rejected(self):
        nl = frame("zone-NL-EX.png")
        rng = np.random.default_rng(935)
        images = (
            np.zeros_like(nl),
            np.full_like(nl, 127),
            rng.integers(0, 256, size=nl.shape, dtype=np.uint8),
        )
        for index, image in enumerate(images):
            for target, observed in _TARGET_SOURCES.items():
                with self.subTest(image=index, target=target):
                    self.assertIsNone(
                        zone_icons.match_zone_icon(image, target, observed, ASSETS)
                    )

    def test_malformed_images_are_rejected(self):
        nl = frame("zone-NL-EX.png")
        images = (
            None,
            nl.astype(np.uint16),
            nl.astype(np.float32),
            cv2.cvtColor(nl, cv2.COLOR_BGR2GRAY),
            nl[:, :, :2],
            np.zeros((720, 1280, 4), np.uint8),
            np.zeros((640, 360, 3), np.uint8),
            np.zeros((721, 1280, 3), np.uint8),
            "not an image",
        )
        for image in images:
            with self.subTest(kind=type(image).__name__):
                self.assertIsNone(
                    zone_icons.match_zone_icon(image, "NL-EX", {"NL"}, ASSETS)
                )


class AssetTests(unittest.TestCase):
    def test_assets_have_expected_geometry_and_are_not_flat(self):
        expected = {
            "zone-nl-ex.png": (62, 90),
            "zone-nl-s.png": (74, 75),
            "zone-mn-ex.png": (91, 85),
        }
        for name, shape in expected.items():
            with self.subTest(asset=name):
                asset = template(name)
                self.assertEqual(asset.shape, (shape[0], shape[1], 3))
                self.assertEqual(asset.dtype, np.uint8)
                self.assertGreater(float(asset.std()), 40.0)

    def test_asset_hashes_match_generated_manifest(self):
        manifest = json.loads((BASE / "ASSETS_MANIFEST.json").read_text("utf-8"))
        for name in _TARGET_ASSETS.values():
            with self.subTest(asset=name):
                self.assertEqual(sha256(ASSETS / name), manifest["templates"][name]["sha256"])

    def test_fixture_hashes_match_retained_evidence_manifest(self):
        manifest = json.loads((BASE / "FIXTURES_MANIFEST.json").read_text("utf-8"))
        for name in ("zone-NL-EX.png", "zone-MN-TR.png", "PV-EX.png", "HE-EX.png", "entry-IS.png"):
            with self.subTest(fixture=name):
                self.assertEqual(sha256(FIXTURES / name), manifest[name]["sha256"])

    def test_missing_assets_are_rejected(self):
        absent = BASE / "absent-assets-for-test"
        self.assertFalse(absent.exists())
        for target, observed in _TARGET_SOURCES.items():
            image = frame("zone-MN-TR.png" if target == "MN-EX" else "zone-NL-EX.png")
            with self.subTest(target=target):
                self.assertIsNone(
                    zone_icons.match_zone_icon(image, target, observed, absent)
                )
        self.assertIsNone(
            zone_icons.match_zone_icon(
                frame("zone-NL-EX.png"), "NL-EX", {"NL"}, None
            )
        )

    def test_corrupt_asset_is_rejected(self):
        directory = BAD_ASSETS / "corrupt"
        self.assertTrue((directory / "zone-nl-ex.png").is_file())
        self.assertIsNone(
            zone_icons.match_zone_icon(
                frame("zone-NL-EX.png"), "NL-EX", {"NL"}, directory
            )
        )

    def test_flat_or_tiny_asset_is_rejected(self):
        flat = BAD_ASSETS / "flat"
        self.assertTrue((flat / "zone-nl-ex.png").is_file())
        self.assertIsNone(
            zone_icons.match_zone_icon(
                frame("zone-NL-EX.png"), "NL-EX", {"NL"}, flat
            )
        )
        tiny = BAD_ASSETS / "tiny"
        self.assertTrue((tiny / "zone-nl-s.png").is_file())
        self.assertIsNone(
            zone_icons.match_zone_icon(
                frame("zone-NL-EX.png"), "NL-S", {"NL"}, tiny
            )
        )


class AmbiguityAndTranslationTests(unittest.TestCase):
    def _duplicate(self, image, asset_name, x, y):
        asset = template(asset_name)
        height, width = asset.shape[:2]
        x0, y0, x1, y1 = zone_icons.ROI
        self.assertGreaterEqual(x, x0)
        self.assertGreaterEqual(y, y0)
        self.assertLessEqual(x + width, x1)
        self.assertLessEqual(y + height, y1)
        duplicated = image.copy()
        duplicated[y:y + height, x:x + width] = asset
        return duplicated

    def test_duplicated_icon_at_distinct_location_is_ambiguous(self):
        cases = (
            ("NL-EX", frame("zone-NL-EX.png"), {"NL"}, "zone-nl-ex.png", 1140, 610),
            ("NL-S", frame("zone-NL-EX.png"), {"NL"}, "zone-nl-s.png", 1030, 604),
            ("MN-EX", frame("zone-MN-TR.png"), {"MN"}, "zone-mn-ex.png", 1040, 583),
        )
        for target, image, observed, asset_name, x, y in cases:
            with self.subTest(target=target):
                self.assertIsNotNone(
                    zone_icons.match_zone_icon(image, target, observed, ASSETS)
                )
                duplicated = self._duplicate(image, asset_name, x, y)
                self.assertIsNone(
                    zone_icons.match_zone_icon(
                        duplicated, target, observed, ASSETS
                    )
                )

    def test_translation_within_documented_roi_is_located(self):
        cases = (
            ("NL-EX", frame("zone-NL-EX.png"), {"NL"}, 16, 10),
            ("NL-EX", frame("zone-NL-EX.png"), {"NL"}, -10, 6),
            ("NL-EX", frame("zone-NL-EX.png"), {"NL"}, 1, 0),
            ("NL-S", frame("zone-NL-EX.png"), {"NL"}, -14, 8),
            ("NL-S", frame("zone-NL-EX.png"), {"NL"}, 12, -6),
            ("NL-S", frame("zone-NL-EX.png"), {"NL"}, 0, 1),
            ("MN-EX", frame("zone-MN-TR.png"), {"MN"}, 8, 8),
            ("MN-EX", frame("zone-MN-TR.png"), {"MN"}, -8, 10),
        )
        x0, y0, x1, y1 = zone_icons.ROI
        for target, image, observed, dx, dy in cases:
            with self.subTest(target=target, dx=dx, dy=dy):
                asset = template(_TARGET_ASSETS[target])
                height, width = asset.shape[:2]
                left = _CROP_ORIGINS[target][0] + dx
                top = _CROP_ORIGINS[target][1] + dy
                self.assertGreaterEqual(left, x0)
                self.assertGreaterEqual(top, y0)
                self.assertLessEqual(left + width, x1)
                self.assertLessEqual(top + height, y1)
                shifted = np.roll(np.roll(image, dy, axis=0), dx, axis=1)
                result = zone_icons.match_zone_icon(
                    shifted, target, observed, ASSETS
                )
                self.assertIsNotNone(result)
                base_x, base_y = _EXPECTED_CENTERS[target]
                self.assertAlmostEqual(result["x"], base_x + dx, delta=1.0)
                self.assertAlmostEqual(result["y"], base_y + dy, delta=1.0)
                self.assertGreaterEqual(result["score"], zone_icons.THRESHOLD)


if __name__ == "__main__":
    unittest.main()
