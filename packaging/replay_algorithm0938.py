"""Offline real OCR replay and controlled traversal counterexamples; no Device."""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import statistics
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument('--frozen', action='store_true')
args = parser.parse_args()
if args.frozen:
    from frozen_logic_loader import install
    install('App-0.9.38', extra_modules=('worker',))
sys.path.insert(0, str(ROOT))
import cv2
import numpy as np
from autostory.vision import Vision
from autostory.home_observation import navigation_regions, map_codes
from autostory.sn_observation import enrich
from autostory.map_traversal import PanBoundary
from autostory.runtime_state import TaskLock
import autostory.map_traversal as module

FILES = ('activity.log', 'status.json', 'worker.json', 'STOP', 'workflow.json',
         'progress.json', 'campaign-results.json', 'attempt-history.json',
         'operators.json', 'scan-report.json', 'nav-hints.json', '../settings.json')


def hashes():
    return {n: hashlib.sha256((ROOT / 'runtime' / n).read_bytes()).hexdigest()
            if (ROOT / 'runtime' / n).exists() else None for n in FILES}


def legacy_difference(a, b):
    return float(np.mean(cv2.absdiff(cv2.resize(a[150:580], (128, 43)),
                                    cv2.resize(b[150:580], (128, 43)))))


def translated(image, rows, pixels, axis=1, move_rows=True):
    rows = copy.deepcopy(rows)
    if move_rows:
        for row in rows:
            if row['text'] in map_codes():
                row['x' if axis == 1 else 'y'] += pixels
                for edge in ((0, 2) if axis == 1 else (1, 3)):
                    row['box'][edge] += pixels
    return np.roll(image, pixels, axis=axis), rows


lock = TaskLock(ROOT / 'runtime/alltasks.lock')
if not lock.acquire():
    raise RuntimeError('Another task is using the runtime')
before = hashes()
report = {'kind': 'offline_replay', 'frozen': args.frozen,
          'loader': type(module.__spec__.loader).__name__, 'no_device_input': True}
try:
    fixture = ROOT / 'release/evidence/flow0937'
    vision = Vision()
    observed = []
    tracker = PanBoundary(map_codes())
    recorded = []
    for path in sorted(fixture.glob('179*.png'))[-4:]:
        image = cv2.imdecode(np.fromfile(path, np.uint8), 1)
        full = vision.texts(image)
        rows = enrich(image, full, vision.texts_zoom, lambda: None, map_codes())
        observed.append((image, rows))
        recorded.append({'file': path.name, 'boundary': tracker.observe(image, rows)})
    assert [r['boundary'] for r in recorded] == [False, False, True, True], recorded
    report['retained_frame_sequence'] = recorded

    image, rows = observed[-1]
    tracker = PanBoundary(map_codes())
    moving = []
    for offset in (0, 30, 60, 90):
        result = tracker.observe(*translated(image, rows, offset))
        moving.append({'horizontal_pixels': offset, 'boundary': result})
    assert not any(r['boundary'] for r in moving)
    report['same_identifiers_moving_positions'] = moving

    negatives = []
    for axis in (0, 1):
        for pixels in (1, 2, 5):
            for move_rows in (False, True):
                tracker = PanBoundary(map_codes())
                tracker.observe(image, rows)
                answers = [tracker.observe(*translated(image, rows, pixels * step, axis, move_rows))
                           for step in (1, 2)]
                assert answers == [False, False], (axis, pixels, move_rows, answers)
                negatives.append({'axis': axis, 'pixels_per_step': pixels,
                                  'ocr_positions_move': move_rows, 'boundary_rejected': True})
    report['controlled_motion_sequences'] = negatives

    # Deliberately change only non-anchor pixels. This demonstrates the
    # algorithm's background independence, not a newly captured live scenario.
    _, rois = navigation_regions(rows, image)
    mask = np.ones(image.shape[:2], dtype=bool)
    for x, y, w, h in rois:
        mask[max(0, y-6):min(720, y+h+6), max(0, x-6):min(1280, x+w+6)] = False
    altered = image.copy()
    altered[mask] = 255 - altered[mask]
    tracker = PanBoundary(map_codes())
    answers = [tracker.observe(frame, rows) for frame in (image, altered, image)]
    difference = legacy_difference(image, altered)
    assert difference >= 2.5 and answers == [False, False, True], (difference, answers)
    report['synthetic_background_change'] = {'legacy_difference': difference,
        'legacy_repeated_input_would_continue': True, 'boundary_sequence': answers,
        'evidence': 'controlled pixels outside actual label regions; not live footage'}

    timings = []
    for _ in range(10):
        tracker = PanBoundary(map_codes())
        for frame, labels in observed[:3]:
            start = time.perf_counter()
            tracker.observe(frame, labels)
            timings.append((time.perf_counter()-start)*1000)
    report['observation_median_ms'] = round(statistics.median(timings), 3)
    report['extra_ocr_or_capture_calls_from_detector'] = 0
    report['ok'] = True
except Exception as error:
    report.update(ok=False, error=repr(error))
finally:
    report['protected_files_unchanged'] = before == hashes()
    report['ok'] = report.get('ok', False) and report['protected_files_unchanged']
    kind = 'frozen' if args.frozen else 'source'
    (ROOT / f'runtime/algorithm0938-{kind}-replay.json').write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    lock.release()
print(json.dumps(report, ensure_ascii=False))
raise SystemExit(0 if report['ok'] else 1)
