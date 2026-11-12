"""Deterministic offline tests for the candidate ``autostory/vision.py``.

No real OCR model, MAA DLL/resource, ADB device, screenshot, account data or
network access is used.  OCR is injected as a callable and all template files
are generated under a temporary directory.

Run either form (project path is auto-detected; override with
``AUTOSTORY_PROJECT``)::

    python -m unittest test_vision -v
    python test_vision.py -v
"""
from __future__ import annotations

import importlib.util
import os
import shutil
import sys
import unittest
import uuid
from pathlib import Path
from unittest import mock

import cv2
import numpy as np

HERE = Path(__file__).resolve().parents[1]
TMP_ROOT = HERE / '_tmp'
TMP_ROOT.mkdir(exist_ok=True)

def new_temp_dir(prefix):
    path=TMP_ROOT/(prefix+uuid.uuid4().hex)
    path.mkdir()
    return path

from autostory import vision


class FakeOCR:
    """Minimal RapidOCR-compatible callable that records the exact crop."""

    def __init__(self, result=None):
        self.result = [] if result is None else result
        self.calls = []
        self.error = None

    def __call__(self, image):
        self.calls.append(np.array(image, copy=True))
        if self.error is not None:
            raise self.error
        if callable(self.result):
            return self.result(image)
        return (self.result, None)


def ocr_item(text, x, y, w=10, h=8, confidence=.9):
    return ([[x, y], [x + w, y], [x + w, y + h], [x, y + h]],
            text, confidence)


def random_image(seed=0, height=180, width=320):
    rng = np.random.default_rng(seed)
    return rng.integers(0, 256, (height, width, 3), dtype=np.uint8)


def place(image, template, x, y):
    result = image.copy()
    result[y:y + template.shape[0], x:x + template.shape[1]] = template
    return result


class VisionTestCase(unittest.TestCase):
    def setUp(self):
        self.root = new_temp_dir('autostory-vision-test-')
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self._maa = mock.patch.object(vision, 'MAA', self.root)
        self._maa.start()
        self.addCleanup(self._maa.stop)

    def make_vision(self, result=None, **kwargs):
        fake = FakeOCR(result)
        return vision.Vision(ocr=fake, **kwargs), fake

    def write_template(self, relative, image):
        if not relative.endswith('.png'):
            relative += '.png'
        path = self.root / 'resource' / 'template' / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        ok, encoded = cv2.imencode('.png', image)
        self.assertTrue(ok)
        path.write_bytes(encoded.tobytes())
        return path


class TextsTests(VisionTestCase):
    def test_contract_confidence_cutoff_and_roi_translation(self):
        image = np.zeros((720, 1280, 3), dtype=np.uint8)
        raw = [
            ocr_item('low', 5, 4, confidence=.6499),
            ocr_item('ok', 5, 4, confidence=.65),
            ocr_item('high', 30, 20, confidence=.99),
            None,
            ([[1], [2]], 'bad-points', .9),
        ]
        v, fake = self.make_vision(raw)
        out = v.texts(image, (100, 50, 200, 100))

        self.assertEqual([item['text'] for item in out], ['ok', 'high'])
        self.assertEqual(set(out[0]),
                         {'text', 'score', 'x', 'y', 'box'})
        self.assertAlmostEqual(out[0]['x'], 110.0)   # 5 + 10/2 + 100
        self.assertAlmostEqual(out[0]['y'], 58.0)    # 4 + 8/2 + 50
        self.assertEqual(out[0]['box'], [105.0, 54.0, 115.0, 62.0])
        self.assertAlmostEqual(out[1]['x'], 135.0)
        self.assertAlmostEqual(out[1]['y'], 74.0)
        self.assertEqual(len(fake.calls), 1)
        self.assertEqual(fake.calls[0].shape, (100, 200, 3))
        self.assertTrue(np.array_equal(fake.calls[0], image[50:150, 100:300]))
        stats = v.stats()
        self.assertEqual(stats['texts_cache_misses'], 1)
        self.assertEqual(stats['ocr_calls'], 1)
        self.assertEqual(stats['texts_malformed_entries'], 2)

    def test_exact_cache_translates_same_pixels_and_copies(self):
        image = np.zeros((720, 1280, 3), dtype=np.uint8)
        rng = np.random.default_rng(11)
        tile = rng.integers(0, 256, (30, 40, 3), dtype=np.uint8)
        for x, y in ((10, 10), (500, 300), (900, 400)):
            image[y:y + 30, x:x + 40] = tile
        raw = [ocr_item('A', 2, 3, w=10, h=6)]
        v, fake = self.make_vision(raw)

        first = v.texts(image, (10, 10, 40, 30))
        second = v.texts(image, (500, 300, 40, 30))
        self.assertEqual(len(fake.calls), 1)
        self.assertAlmostEqual(second[0]['x'] - first[0]['x'], 490.0)
        self.assertAlmostEqual(second[0]['y'] - first[0]['y'], 290.0)
        self.assertAlmostEqual(second[0]['box'][0], 502.0)
        self.assertAlmostEqual(second[0]['box'][1], 303.0)

        # Caller mutation must not reach the cached private entry.
        first[0]['text'] = 'mutated'
        first[0]['box'][0] = -999.0
        first[0]['box'].append(123.0)
        third = v.texts(image, (900, 400, 40, 30))
        self.assertEqual(third[0]['text'], 'A')
        self.assertEqual(third[0]['box'], [902.0, 403.0, 912.0, 409.0])
        self.assertIsNot(third[0], second[0])
        self.assertIsNot(third[0]['box'], second[0]['box'])

        stats = v.stats()
        self.assertEqual(stats['texts_cache_misses'], 1)
        self.assertEqual(stats['texts_cache_hits'], 2)
        self.assertEqual(stats['ocr_calls'], 1)

    def test_exact_content_does_not_reuse_approximate_pixels(self):
        image = np.zeros((80, 120, 3), dtype=np.uint8)
        rng = np.random.default_rng(12)
        tile_a = rng.integers(0, 256, (20, 20, 3), dtype=np.uint8)
        tile_b = tile_a.copy()
        tile_b[0, 0, 0] = (int(tile_b[0, 0, 0]) + 1) % 256
        image[10:30, 10:30] = tile_a
        image[10:30, 50:70] = tile_b
        raw = [ocr_item('A', 1, 1)]
        v, fake = self.make_vision(raw)

        v.texts(image, (10, 10, 20, 20))
        v.texts(image, (50, 10, 20, 20))
        self.assertEqual(len(fake.calls), 2)
        v.texts(image, (10, 10, 20, 20))
        self.assertEqual(len(fake.calls), 2)
        stats = v.stats()
        self.assertEqual(stats['texts_cache_misses'], 2)
        self.assertEqual(stats['texts_cache_hits'], 1)

    def test_cache_identity_includes_shape(self):
        image_a = np.zeros((10, 10, 3), dtype=np.uint8)
        image_b = np.zeros((20, 5, 3), dtype=np.uint8)
        v, fake = self.make_vision([ocr_item('A', 1, 1)])
        v.texts(image_a)
        v.texts(image_b)
        self.assertEqual(len(fake.calls), 2)
        self.assertEqual(v.stats()['texts_cache_misses'], 2)

    def test_bounded_lru_evicts_oldest_entry(self):
        image = np.zeros((100, 160, 3), dtype=np.uint8)
        rng = np.random.default_rng(13)
        for index, x in enumerate((10, 50, 90)):
            image[10:30, x:x + 20] = rng.integers(
                0, 256, (20, 20, 3), dtype=np.uint8)
        v, fake = self.make_vision([ocr_item('A', 1, 1)],
                                   ocr_cache_size=2)

        v.texts(image, (10, 10, 20, 20))
        v.texts(image, (50, 10, 20, 20))
        v.texts(image, (90, 10, 20, 20))   # evicts the first
        self.assertEqual(len(fake.calls), 3)
        v.texts(image, (10, 10, 20, 20))   # miss again
        self.assertEqual(len(fake.calls), 4)
        stats = v.stats()
        self.assertEqual(stats['ocr_cache_capacity'], 2)
        self.assertEqual(stats['ocr_cache_size'], 2)
        self.assertEqual(stats['ocr_cache_evictions'], 2)

    def test_invalid_and_clipped_roi(self):
        image = np.zeros((50, 60, 3), dtype=np.uint8)
        raw = [ocr_item('A', 1, 2, w=10, h=6)]
        v, fake = self.make_vision(raw)

        invalid = [
            (0, 0, 0, 10),
            (0, 0, -1, 10),
            (0, 0, 10, 0),
            (-100, -100, 10, 10),
            (0, 0, float('nan'), 10),
            (0, 0, float('inf'), 10),
            ('a', 0, 10, 10),
            (0, 0, 10),
        ]
        for roi in invalid:
            self.assertEqual(v.texts(image, roi), [], roi)
        self.assertEqual(fake.calls, [])
        self.assertEqual(v.stats()['texts_invalid_roi'], len(invalid))

        clipped = v.texts(image, (-10, -5, 30, 20))
        self.assertEqual(fake.calls[-1].shape, (15, 20, 3))
        self.assertAlmostEqual(clipped[0]['x'], 6.0)
        self.assertAlmostEqual(clipped[0]['y'], 5.0)

        right = v.texts(image, (50, 40, 20, 20))
        self.assertEqual(fake.calls[-1].shape, (10, 10, 3))
        self.assertAlmostEqual(right[0]['x'], 56.0)
        self.assertAlmostEqual(right[0]['y'], 45.0)

        full = v.texts(image, None)
        self.assertEqual(fake.calls[-1].shape, (50, 60, 3))
        self.assertAlmostEqual(full[0]['x'], 6.0)
        self.assertAlmostEqual(full[0]['y'], 5.0)

        self.assertEqual(v.texts(np.zeros((0, 0, 3), np.uint8)), [])
        self.assertEqual(v.texts(None), [])

    def test_ocr_error_is_not_cached(self):
        image = np.zeros((20, 20, 3), dtype=np.uint8)

        class BoomOCR(FakeOCR):
            def __call__(self, image):
                self.calls.append(np.array(image, copy=True))
                raise RuntimeError('ocr unavailable')

        fake = BoomOCR()
        v = vision.Vision(ocr=fake)
        with self.assertRaises(RuntimeError):
            v.texts(image)
        with self.assertRaises(RuntimeError):
            v.texts(image)
        self.assertEqual(len(fake.calls), 2)
        stats = v.stats()
        self.assertEqual(stats['ocr_calls'], 2)
        self.assertEqual(stats['texts_cache_hits'], 0)
        self.assertEqual(stats['ocr_cache_size'], 0)


class MatchTests(VisionTestCase):
    def template(self, seed=21, height=20, width=24):
        rng = np.random.default_rng(seed)
        return rng.integers(0, 256, (height, width, 3), dtype=np.uint8)

    def test_correct_match_and_wrong_image(self):
        template = self.template()
        image = random_image(22)
        image = place(image, template, 200, 100)
        self.write_template('Solid', template)
        v, _ = self.make_vision()

        hit = v.match(image, 'Solid', threshold=.95)
        self.assertIsNotNone(hit)
        self.assertEqual(set(hit), {'score', 'x', 'y'})
        self.assertGreater(hit['score'], .99)
        self.assertAlmostEqual(hit['x'], 212.0)
        self.assertAlmostEqual(hit['y'], 110.0)

        hit['x'] = -1
        again = v.match(image, 'Solid', threshold=.95)
        self.assertAlmostEqual(again['x'], 212.0)

        wrong = random_image(23)
        self.assertIsNone(v.match(wrong, 'Solid', threshold=.9))

        stats = v.stats()
        self.assertEqual(stats['template_lookups'], 1)
        self.assertEqual(stats['template_loads'], 1)
        self.assertEqual(stats['template_positive_hits'], 2)
        self.assertEqual(stats['match_hits'], 2)
        self.assertEqual(stats['match_misses'], 1)
        self.assertGreater(stats['match_seconds'], 0.0)

    def test_roi_clipping_translation_and_bounds(self):
        template = self.template(seed=24, height=12, width=16)
        image = random_image(25)
        image = place(image, template, 100, 40)
        self.write_template('Small', template)
        v, _ = self.make_vision()

        hit = v.match(image, 'Small', (90, 30, 50, 40), .95)
        self.assertIsNotNone(hit)
        self.assertAlmostEqual(hit['x'], 108.0)
        self.assertAlmostEqual(hit['y'], 46.0)

        # Negative/overhanging ROI is clipped; the center is still image-based.
        clipped = v.match(image, 'Small', (-5, -5, 130, 80), .95)
        self.assertIsNotNone(clipped)
        self.assertAlmostEqual(clipped['x'], 108.0)
        self.assertAlmostEqual(clipped['y'], 46.0)

        # Exact visible size.
        exact = v.match(image, 'Small', (100, 40, 16, 12), .95)
        self.assertIsNotNone(exact)
        self.assertAlmostEqual(exact['x'], 108.0)
        self.assertAlmostEqual(exact['y'], 46.0)

        self.assertIsNone(v.match(image, 'Small', (100, 40, 8, 8), .95))
        self.assertIsNone(v.match(image, 'Small', (0, 0, 0, 0), .95))
        self.assertIsNone(v.match(np.zeros((0, 0, 3), np.uint8),
                                  'Small', None, .95))
        stats = v.stats()
        self.assertGreaterEqual(stats['match_invalid_roi'], 1)
        self.assertGreaterEqual(stats['match_invalid_image'], 1)

    def test_negative_and_ambiguous_template_lookups_are_cached(self):
        image = random_image(26)
        v, _ = self.make_vision()
        real_rglob = Path.rglob
        calls = []

        def counted(path, pattern):
            calls.append(pattern)
            return real_rglob(path, pattern)

        with mock.patch.object(Path, 'rglob', counted):
            self.assertIsNone(v.match(image, 'NoSuchThing'))
            self.assertIsNone(v.match(image, 'NoSuchThing'))
        self.assertEqual(calls, ['NoSuchThing.png'])
        stats = v.stats()
        self.assertEqual(stats['template_lookups'], 1)
        self.assertEqual(stats['template_missing'], 1)
        self.assertEqual(stats['template_negative_hits'], 1)

        self.write_template('A/Dup', self.template(27, 10, 10))
        self.write_template('B/Dup', self.template(28, 10, 10))
        with mock.patch.object(Path, 'rglob', counted):
            self.assertIsNone(v.match(image, 'Dup'))
            self.assertIsNone(v.match(image, 'Dup'))
        self.assertEqual(len(calls), 2)
        stats = v.stats()
        self.assertEqual(stats['template_lookups'], 2)
        self.assertEqual(stats['template_negative_hits'], 2)

    def test_constant_and_invalid_templates_are_rejected(self):
        image = random_image(29)
        constant = np.full((20, 20, 3), 128, dtype=np.uint8)
        self.write_template('Constant', constant)
        v, _ = self.make_vision()

        with mock.patch.object(vision.cv2, 'matchTemplate') as matcher:
            self.assertIsNone(v.match(image, 'Constant'))
            self.assertIsNone(v.match(image, 'Constant'))
            matcher.assert_not_called()

        (self.root / 'resource' / 'template' /
         'NotImage.png').write_bytes(b'not a png')
        self.assertIsNone(v.match(image, 'NotImage'))

        stats = v.stats()
        self.assertEqual(stats['template_lookups'], 2)
        self.assertEqual(stats['template_rejects'], 2)
        self.assertEqual(stats['template_negative_hits'], 1)

    def test_channel_and_dtype_incompatibility_returns_none(self):
        template = self.template(30, 10, 12)
        self.write_template('Color', template)
        v, _ = self.make_vision()
        gray = np.zeros((40, 60), dtype=np.uint8)
        self.assertIsNone(v.match(gray, 'Color'))
        self.assertEqual(v.stats()['match_incompatible'], 1)

        float_image = np.zeros((40, 60, 3), dtype=np.float32)
        self.assertIsNone(v.match(float_image, 'Color'))
        self.assertEqual(v.stats()['match_incompatible'], 2)

    def test_nonfinite_scores_are_rejected(self):
        template = self.template(31, 10, 12)
        self.write_template('Finite', template)
        image = random_image(32)
        v, _ = self.make_vision()
        for scores in (np.array([[np.nan, .99]], dtype=np.float32),
                       np.array([[np.inf, .99]], dtype=np.float32)):
            with mock.patch.object(vision.cv2, 'matchTemplate',
                                   return_value=scores):
                self.assertIsNone(v.match(image, 'Finite', threshold=.1))
        stats = v.stats()
        self.assertEqual(stats['match_nonfinite_scores'], 2)
        self.assertEqual(stats['match_scale_evaluations'], 0)

    def test_match_engine_error_is_handled(self):
        template = self.template(40, 10, 12)
        self.write_template('EngineError', template)
        image = random_image(41)
        v, _ = self.make_vision()
        with mock.patch.object(vision.cv2, 'matchTemplate',
                               side_effect=vision.cv2.error('boom')):
            self.assertIsNone(v.match(image, 'EngineError', threshold=.1))
        self.assertEqual(v.stats()['match_errors'], 1)
        self.assertEqual(v.stats()['match_misses'], 1)

    def test_decode_error_is_cached_as_negative(self):
        template = self.template(42, 10, 12)
        self.write_template('DecodeError', template)
        image = random_image(43)
        v, _ = self.make_vision()
        with mock.patch.object(vision.cv2, 'imdecode',
                               side_effect=vision.cv2.error('bad png')) as decode:
            self.assertIsNone(v.match(image, 'DecodeError'))
            self.assertIsNone(v.match(image, 'DecodeError'))
            self.assertEqual(decode.call_count, 1)
        stats = v.stats()
        self.assertEqual(stats['template_rejects'], 1)
        self.assertEqual(stats['template_negative_hits'], 1)

    def test_default_is_single_scale_and_multiscale_is_opt_in(self):
        template = self.template(33, height=24, width=30)
        small = cv2.resize(template, (15, 12), interpolation=cv2.INTER_AREA)
        image = random_image(34)
        image = place(image, small, 100, 100)
        self.write_template('Scaled', template)
        v, _ = self.make_vision()

        self.assertIsNone(v.match(image, 'Scaled', threshold=.95))
        stats = v.stats()
        self.assertEqual(stats['match_scale_evaluations'], 1)
        self.assertEqual(stats['last_match_scale'], 1.0)

        hit = v.match(image, 'Scaled', threshold=.95,
                      scales=(.5, 1.0))
        self.assertIsNotNone(hit)
        self.assertGreater(hit['score'], .99)
        self.assertAlmostEqual(hit['x'], 107.5)
        self.assertAlmostEqual(hit['y'], 106.0)
        stats = v.stats()
        self.assertEqual(stats['match_scale_evaluations'], 3)
        self.assertEqual(stats['last_match_scale'], .5)

        self.assertIsNone(v.match(image, 'Scaled', scales=()))
        self.assertIsNone(v.match(image, 'Scaled', scales=(0, 1)))
        self.assertIsNone(v.match(image, 'Scaled', scales='0.5'))
        self.assertEqual(v.stats()['match_invalid_scales'], 3)

    def test_invalid_threshold_avoids_template_lookup(self):
        image = random_image(35)
        v, _ = self.make_vision()
        self.assertIsNone(v.match(image, 'Any', threshold=float('nan')))
        self.assertIsNone(v.match(image, 'Any', threshold='bad'))
        stats = v.stats()
        self.assertEqual(stats['match_invalid_threshold'], 2)
        self.assertEqual(stats['template_lookups'], 0)


class StatsAndCacheTests(VisionTestCase):
    def test_stats_snapshot_clear_and_reset(self):
        image = np.zeros((40, 60, 3), dtype=np.uint8)
        template = random_image(36, 10, 12)
        self.write_template('Stats', template)
        v, _ = self.make_vision([ocr_item('A', 1, 1)])
        v.texts(image)
        v.match(place(image, template, 20, 20), 'Stats', threshold=.95)
        before = v.stats()
        self.assertEqual(before['ocr_cache_size'], 1)
        self.assertEqual(before['template_cache_size'], 1)
        self.assertGreaterEqual(before['texts_seconds'], 0.0)
        self.assertGreaterEqual(before['match_seconds'], 0.0)
        self.assertIsNotNone(before['texts_cache_hit_rate'])

        before['texts_calls'] = -1
        self.assertNotEqual(v.stats()['texts_calls'], -1)

        dropped = v.clear_caches()
        self.assertEqual(dropped, {'ocr_entries': 1, 'templates': 1})
        after = v.stats()
        self.assertEqual(after['ocr_cache_size'], 0)
        self.assertEqual(after['template_cache_size'], 0)

        v.reset_stats()
        reset = v.stats()
        self.assertEqual(reset['texts_calls'], 0)
        self.assertEqual(reset['match_calls'], 0)
        self.assertEqual(reset['template_lookups'], 0)

    def test_template_cache_is_bounded(self):
        image = random_image(37)
        first = random_image(38, 10, 10)
        second = random_image(39, 10, 10)
        self.write_template('One', first)
        self.write_template('Two', second)
        v, _ = self.make_vision(template_cache_size=1)
        v.match(image, 'One')
        v.match(image, 'Two')       # evicts One
        v.match(image, 'One')       # reloads One
        stats = v.stats()
        self.assertEqual(stats['template_cache_capacity'], 1)
        self.assertEqual(stats['template_cache_size'], 1)
        self.assertEqual(stats['template_lookups'], 3)
        self.assertEqual(stats['template_loads'], 3)
        self.assertEqual(stats['template_evictions'], 2)


if __name__ == '__main__':
    unittest.main(verbosity=2)

