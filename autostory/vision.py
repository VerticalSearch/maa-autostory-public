"""GPU/CPU OCR with exact ROI caches and guarded template matching."""
from __future__ import annotations

import hashlib
import math
import threading
import time
from collections import OrderedDict

import cv2
import numpy as np

from .common import MAA
from .resilient_ocr import ResilientOCR, OCRBackendChanged

__all__ = ['Vision', 'OCR_MIN_CONFIDENCE']

# Must stay identical to the historical AutoStory threshold.
OCR_MIN_CONFIDENCE = .65


class Vision:
    """OCR and template matching with bounded, exact caches.

    Parameters are deliberately keyword-only so the historical constructor
    (``Vision()``) and both public method signatures remain compatible.
    ``ocr``/cache sizes exist for deterministic tests and offline evaluation.
    """

    def __init__(self, ocr=None, *, ocr_cache_size=16, template_cache_size=128, backend=None, device_id=None):
        self.backend = {'active':'injected'}
        if ocr is None:
            from .common import settings, DATA, write_json
            from .ocr_backend import create_ocr
            cfg=settings()
            ocr,self.backend=create_ocr(backend or cfg.get('ocr_backend','cpu'),
                device_id if device_id is not None else cfg.get('ocr_device_id',0))
            self._record_backend(self.backend)
            ocr=ResilientOCR(ocr,self.backend,factory=create_ocr,callback=self._record_backend)
        self.ocr = ocr
        self._ocr_cache_size = max(0, int(ocr_cache_size))
        self._template_cache_size = max(0, int(template_cache_size))
        # Public for compatibility: name -> decoded template, or None when the
        # lookup was negative (missing/ambiguous/invalid).  It is an LRU map.
        self.templates = OrderedDict()
        self._ocr_cache = OrderedDict()
        self._lock = threading.RLock()
        self._stats = self._new_stats()

    def _record_backend(self, report):
        import os,sys
        from .common import DATA,now,write_json
        self.backend={**report,'pid':os.getpid(),'executable':sys.executable,'at':now()}
        write_json(DATA/'ocr-backend.json',self.backend)

    # ------------------------------------------------------------------ stats
    @staticmethod
    def _new_stats():
        return {
            'texts_calls': 0,
            'texts_cache_hits': 0,
            'texts_cache_misses': 0,
            'texts_invalid_image': 0,
            'texts_invalid_roi': 0,
            'texts_malformed_entries': 0,
            'texts_seconds': 0.0,
            'ocr_calls': 0,
            'ocr_seconds': 0.0,
            'ocr_cache_evictions': 0,
            'match_calls': 0,
            'match_hits': 0,
            'match_misses': 0,
            'match_invalid_image': 0,
            'match_invalid_roi': 0,
            'match_invalid_threshold': 0,
            'match_invalid_scales': 0,
            'match_incompatible': 0,
            'match_errors': 0,
            'match_nonfinite_scores': 0,
            'match_scale_evaluations': 0,
            'match_seconds': 0.0,
            'template_lookups': 0,
            'template_loads': 0,
            'template_positive_hits': 0,
            'template_negative_hits': 0,
            'template_missing': 0,
            'template_rejects': 0,
            'template_evictions': 0,
            'last_texts_count': None,
            'last_match_score': None,
            'last_match_scale': None,
        }

    def stats(self):
        """Return a JSON-safe snapshot of timing/cache counters.

        Callers may mutate the returned dict; it is not the live state.
        """
        with self._lock:
            data = dict(self._stats)
            hits = data['texts_cache_hits']
            misses = data['texts_cache_misses']
            data['ocr_cache_size'] = len(self._ocr_cache)
            data['ocr_cache_capacity'] = self._ocr_cache_size
            data['template_cache_size'] = len(self.templates)
            data['template_cache_capacity'] = self._template_cache_size
            data['texts_cache_hit_rate'] = (
                float(hits) / (hits + misses) if hits + misses else None)
        return data

    def reset_stats(self):
        with self._lock:
            self._stats = self._new_stats()

    def clear_caches(self):
        """Drop OCR/template entries; returns how many entries were dropped."""
        with self._lock:
            dropped = {'ocr_entries': len(self._ocr_cache),
                       'templates': len(self.templates)}
            self._ocr_cache.clear()
            self.templates.clear()
        return dropped

    # ------------------------------------------------------------------- ROI
    @staticmethod
    def _valid_image(image):
        return (isinstance(image, np.ndarray)
                and image.ndim in (2, 3)
                and image.size > 0
                and image.shape[0] > 0
                and image.shape[1] > 0)

    @classmethod
    def _roi_box(cls, image, roi):
        """Return clipped ``(x0, y0, x1, y1)`` or ``None``.

        ROI values are rounded to ints.  Negative origins and overhangs are
        clipped to the image; a non-positive area or malformed/non-finite ROI
        is invalid.  This also handles the historical full-frame default.
        """
        if not cls._valid_image(image):
            return None
        height, width = image.shape[:2]
        if roi is None:
            return (0, 0, int(width), int(height))
        try:
            values = tuple(roi)
        except TypeError:
            return None
        if len(values) != 4:
            return None
        try:
            coords = [float(value) for value in values]
        except (TypeError, ValueError):
            return None
        if not all(math.isfinite(value) for value in coords):
            return None
        x, y, w, h = (int(round(value)) for value in coords)
        if w <= 0 or h <= 0:
            return None
        x0 = max(0, min(int(width), x))
        y0 = max(0, min(int(height), y))
        x1 = max(0, min(int(width), x + w))
        y1 = max(0, min(int(height), y + h))
        if x1 <= x0 or y1 <= y0:
            return None
        return (x0, y0, x1, y1)

    # -------------------------------------------------------------- OCR cache
    @staticmethod
    def _crop_key(crop):
        """Exact ROI identity: dtype + shape + every byte of the crop."""
        digest = hashlib.blake2b(digest_size=20)
        digest.update(crop.dtype.str.encode('ascii'))
        digest.update(repr(crop.shape).encode('ascii'))
        digest.update(np.ascontiguousarray(crop).tobytes())
        return digest.digest()

    @staticmethod
    def _copy_with_offset(entries, dx, dy):
        # Always build new dicts/lists: cached entries must never be handed to
        # a caller who could mutate them.
        return [
            {
                'text': entry['text'],
                'score': float(entry['score']),
                'x': entry['x'] + dx,
                'y': entry['y'] + dy,
                'box': [entry['box'][0] + dx, entry['box'][1] + dy,
                        entry['box'][2] + dx, entry['box'][3] + dy],
            }
            for entry in entries
        ]

    def _normalize_ocr_entries(self, result):
        """Convert OCR output to crop-relative dicts, filtering low confidence."""
        if result is None:
            return []
        try:
            iterator = iter(result)
        except TypeError:
            self._bump('texts_malformed_entries')
            return []
        entries = []
        for item in iterator:
            try:
                points, text, confidence = item
            except (TypeError, ValueError):
                self._bump('texts_malformed_entries')
                continue
            try:
                score = float(confidence)
            except (TypeError, ValueError):
                self._bump('texts_malformed_entries')
                continue
            if not math.isfinite(score) or score < OCR_MIN_CONFIDENCE:
                continue
            try:
                array = np.asarray(points, dtype=np.float64)
            except (TypeError, ValueError):
                self._bump('texts_malformed_entries')
                continue
            if array.ndim != 2 or array.shape[0] < 1 or array.shape[1] < 2:
                self._bump('texts_malformed_entries')
                continue
            array = array[:, :2]
            if not np.isfinite(array).all():
                self._bump('texts_malformed_entries')
                continue
            xs = array[:, 0]
            ys = array[:, 1]
            entries.append({
                'text': text,
                'score': score,
                'x': float(xs.mean()),
                'y': float(ys.mean()),
                'box': [float(xs.min()), float(ys.min()),
                        float(xs.max()), float(ys.max())],
            })
        return entries

    # ------------------------------------------------------------------ texts
    def texts(self, image, roi=None):
        started = time.perf_counter()
        try:
            self._bump('texts_calls')
            if not self._valid_image(image):
                self._bump('texts_invalid_image')
                return []
            box = self._roi_box(image, roi)
            if box is None:
                self._bump('texts_invalid_roi')
                return []
            x0, y0, x1, y1 = box
            crop = image[y0:y1, x0:x1]
            key = self._crop_key(crop)

            with self._lock:
                if key in self._ocr_cache:
                    entries = self._ocr_cache[key]
                    self._ocr_cache.move_to_end(key)
                    self._stats['texts_cache_hits'] += 1
                    self._stats['last_texts_count'] = len(entries)
                    return self._copy_with_offset(entries, x0, y0)
                self._stats['texts_cache_misses'] += 1

            ocr_started = time.perf_counter()
            try:
                raw = self.ocr(crop)
            except Exception as error:
                if isinstance(error,OCRBackendChanged):self.clear_caches()
                with self._lock:
                    self._stats['ocr_calls'] += 1
                    self._stats['ocr_seconds'] += time.perf_counter() - ocr_started
                raise
            ocr_seconds = time.perf_counter() - ocr_started

            # RapidOCR returns ``(result, elapsed)``; test doubles may return
            # the result list directly.
            if isinstance(raw, tuple) and len(raw) == 2:
                result = raw[0]
            else:
                result = raw
            entries = self._normalize_ocr_entries(result)

            with self._lock:
                self._stats['ocr_calls'] += 1
                self._stats['ocr_seconds'] += ocr_seconds
                if self._ocr_cache_size:
                    self._ocr_cache[key] = entries
                    self._ocr_cache.move_to_end(key)
                    while len(self._ocr_cache) > self._ocr_cache_size:
                        self._ocr_cache.popitem(last=False)
                        self._stats['ocr_cache_evictions'] += 1
                self._stats['last_texts_count'] = len(entries)
            return self._copy_with_offset(entries, x0, y0)
        finally:
            self._add_seconds('texts_seconds', time.perf_counter() - started)

    # -------------------------------------------------------------- templates
    def texts_zoom(self, image, roi, scale=2):
        """Re-detect small text and return source-image coordinates."""
        box=self._roi_box(image,roi)
        if box is None:return []
        if not math.isfinite(scale) or not 1<=scale<=3:raise ValueError('OCR zoom must be 1..3')
        x0,y0,x1,y1=box
        width=max(1,round((x1-x0)*scale));height=max(1,round((y1-y0)*scale))
        crop=cv2.resize(image[y0:y1,x0:x1],(width,height),interpolation=cv2.INTER_CUBIC)
        rows=self.texts(crop)
        sx=width/(x1-x0);sy=height/(y1-y0)
        for row in rows:
            row['x']=row['x']/sx+x0;row['y']=row['y']/sy+y0
            row['box']=[row['box'][0]/sx+x0,row['box'][1]/sy+y0,
                        row['box'][2]/sx+x0,row['box'][3]/sy+y0]
        return rows

    @staticmethod
    def _template_problem(template):
        """Return a short reason an image is unusable as a match template."""
        if template is None:
            return 'decode'
        if not isinstance(template, np.ndarray):
            return 'type'
        if template.size == 0 or template.ndim not in (2, 3):
            return 'shape'
        if template.ndim == 3 and template.shape[2] not in (1, 3, 4):
            return 'channels'
        if template.dtype not in (np.dtype('uint8'), np.dtype('float32')):
            return 'dtype'
        if template.dtype == np.float32 and not np.isfinite(template).all():
            return 'nonfinite'
        # Color CCOEFF degenerates only when ALL channels have zero variance.
        if template.ndim == 3:
            values = template.reshape(-1, template.shape[2]).astype(np.float64)
        else:
            values = template.reshape(-1, 1).astype(np.float64)
        if np.all(values.std(axis=0) <= 1e-9):
            return 'constant'
        return None

    def _load_template(self, name):
        if not isinstance(name, str) or not name:
            self._bump('template_rejects')
            return None
        with self._lock:
            if name in self.templates:
                template = self.templates[name]
                self.templates.move_to_end(name)
                if template is None:
                    self._stats['template_negative_hits'] += 1
                else:
                    self._stats['template_positive_hits'] += 1
                return template
            self._stats['template_lookups'] += 1

        pattern = name if name.endswith('.png') else name + '.png'
        try:
            files = list((MAA / 'resource/template').rglob(pattern))
        except OSError:
            files = []
        template = None
        if len(files) == 1:
            try:
                template = cv2.imdecode(
                    np.fromfile(str(files[0]), np.uint8), cv2.IMREAD_COLOR)
            except (OSError, ValueError, cv2.error):
                template = None
            problem = self._template_problem(template)
            if problem is not None:
                self._bump('template_rejects')
                template = None
        else:
            self._bump('template_missing')
        if template is not None:
            self._bump('template_loads')

        with self._lock:
            if self._template_cache_size:
                self.templates[name] = template
                self.templates.move_to_end(name)
                while len(self.templates) > self._template_cache_size:
                    self.templates.popitem(last=False)
                    self._stats['template_evictions'] += 1
        return template

    @staticmethod
    def _normalize_scales(scales):
        if isinstance(scales, (str, bytes)):
            return None
        try:
            values = list(scales)
        except TypeError:
            return None
        normalized = []
        for value in values:
            try:
                scale = float(value)
            except (TypeError, ValueError):
                return None
            if not math.isfinite(scale) or scale <= 0:
                return None
            if scale not in normalized:
                normalized.append(scale)
        return tuple(normalized) if normalized else None

    # ------------------------------------------------------------------ match
    def match(self, image, name, roi=None, threshold=.9, *, scales=None):
        """Match a MAA template; default is one scale (1.0).

        ``scales`` is opt-in and must be explicit; callers that need settlement
        safety keep the historical single-scale behavior.  The returned dict is
        always ``{'score', 'x', 'y'}`` and its center is in image coordinates.
        """
        started = time.perf_counter()
        try:
            self._bump('match_calls')
            try:
                threshold = float(threshold)
            except (TypeError, ValueError):
                self._bump('match_invalid_threshold')
                return None
            if not math.isfinite(threshold):
                self._bump('match_invalid_threshold')
                return None
            if not self._valid_image(image):
                self._bump('match_invalid_image')
                return None
            box = self._roi_box(image, roi)
            if box is None:
                self._bump('match_invalid_roi')
                return None

            template = self._load_template(name)
            if template is None:
                self._bump('match_misses')
                return None

            x0, y0, x1, y1 = box
            crop = image[y0:y1, x0:x1]
            if crop.ndim != template.ndim:
                self._bump('match_incompatible')
                self._bump('match_misses')
                return None
            if template.ndim == 3 and crop.shape[2] != template.shape[2]:
                self._bump('match_incompatible')
                self._bump('match_misses')
                return None
            if crop.dtype != template.dtype:
                self._bump('match_incompatible')
                self._bump('match_misses')
                return None

            if scales is None:
                scale_values = (1.0,)
            else:
                scale_values = self._normalize_scales(scales)
                if scale_values is None:
                    self._bump('match_invalid_scales')
                    return None

            best = None
            for scale in scale_values:
                if scale == 1.0:
                    candidate = template
                else:
                    width = max(1, int(round(template.shape[1] * scale)))
                    height = max(1, int(round(template.shape[0] * scale)))
                    # Check fits before allocating a possibly huge resize.
                    if width > crop.shape[1] or height > crop.shape[0]:
                        continue
                    interpolation = (cv2.INTER_AREA if scale < 1
                                     else cv2.INTER_LINEAR)
                    candidate = cv2.resize(template, (width, height),
                                           interpolation=interpolation)
                    if self._template_problem(candidate) is not None:
                        continue
                if (candidate.shape[0] > crop.shape[0]
                        or candidate.shape[1] > crop.shape[1]):
                    continue

                try:
                    scores = cv2.matchTemplate(
                        crop, candidate, cv2.TM_CCOEFF_NORMED)
                except cv2.error:
                    self._bump('match_errors')
                    continue
                if scores.size == 0:
                    continue
                if not np.isfinite(scores).all():
                    self._bump('match_nonfinite_scores')
                    continue
                _, score, _, location = cv2.minMaxLoc(scores)
                score = float(score)
                if not math.isfinite(score):
                    self._bump('match_nonfinite_scores')
                    continue
                self._bump('match_scale_evaluations')
                if best is None or score > best[0]:
                    best = (score, location, candidate.shape, scale)

            if best is None:
                self._bump('match_misses')
                return None
            score, location, shape, scale = best
            with self._lock:
                self._stats['last_match_score'] = score
                self._stats['last_match_scale'] = scale
            if score < threshold:
                self._bump('match_misses')
                return None
            self._bump('match_hits')
            return {
                'score': score,
                'x': float(x0 + location[0] + shape[1] / 2),
                'y': float(y0 + location[1] + shape[0] / 2),
            }
        finally:
            self._add_seconds('match_seconds', time.perf_counter() - started)

    # ------------------------------------------------------------- statistics
    def _bump(self, key, amount=1):
        with self._lock:
            self._stats[key] += amount

    def _add_seconds(self, key, seconds):
        with self._lock:
            self._stats[key] += max(0.0, float(seconds))
