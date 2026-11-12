"""Bounded SN (Stultifera Navis) small map-label OCR fallback.

``enrich`` is read-only: it never taps, swipes, waits
for settling, starts a process, or certifies stability.  The caller must still
use the unchanged ``map_motion``/``settle`` path with two actual known labels
and re-check a fresh final page identity.

A frame is treated as the verified SN map only when all of these hold:

* the exact free-explore label is a unique high-confidence upper-right anchor;
* the exact current-region label is a unique high-confidence lower-right anchor;
* local OCR must recover an exact known ``SN-`` battle code; a full-frame
  miss may start a bounded lookup after the two independent map controls;
* no detail/start/settlement/confirmation/purchase/pause overlay is visible.

When two distinct exact known codes are already present, ``zoom`` is not called.
Otherwise at most six 480x300 tiles are scanned (left 0/400/800, top 90/310,
80 px overlap), stopping as soon as two distinct codes exist.  Only exact
high-confidence known codes with finite in-frame boxes inside the map area are
merged.  Truncated labels such as ``SN-TR-`` are never repaired by guessing.
Other full-frame labels are preserved.
"""
from __future__ import annotations

import math

IMAGE_SHAPE = (720, 1280, 3)
IMAGE_DTYPE = 'uint8'
MIN_SCORE = 0.90

# Map-label area used by navigation.visible_stages().
MAP_BOUNDS = (5, 90, 1274, 610)
# Unique screen anchors.  Escaped so this authored file stays ASCII.
FREE_EXPLORE = '\u81ea\u7531\u63a2\u7d22'
CURRENT_REGION = '\u683c\u5170\u6cd5\u6d1b'
FREE_EXPLORE_BOUNDS = (1000, 0, 1280, 120)
CURRENT_REGION_BOUNDS = (900, 560, 1280, 720)

TILE_LEFT = (0, 400, 800)
TILE_TOP = (90, 310)
TILE_WIDTH = 480
TILE_HEIGHT = 300
TILES = tuple((left, top, TILE_WIDTH, TILE_HEIGHT)
              for top in TILE_TOP for left in TILE_LEFT)
MAX_ZOOM_TILES = 6

# Overlay evidence.  Exact tokens plus a few unambiguous substrings.
_OVERLAY_EXACT = frozenset({
    '\u5f00\u59cb\u884c\u52a8',              # start action
    '\u5f00\u59cb\u4f5c\u6218',              # start battle
    '\u5f00\u59cb\u7a81\u88ad',              # start raid
    '\u5f00\u59cb\u6f14\u4e60',              # start exercise
    '\u884c\u52a8\u7ed3\u675f',              # action finished
    '\u6f14\u4e60\u7ed3\u675f',              # exercise finished
    '\u884c\u52a8\u5931\u8d25',              # action failed
    '\u6f14\u4e60\u5931\u8d25',              # exercise failed
    '\u4efb\u52a1\u5931\u8d25',              # mission failed
    '\u653e\u5f03\u884c\u52a8',              # abandon action
    '\u7ee7\u7eed\u884c\u52a8',              # continue action
    '\u6682\u505c',                          # pause
    '\u786e\u8ba4',                          # confirm
    '\u786e\u5b9a',                          # OK
    '\u53d6\u6d88',                          # cancel
    '\u8d2d\u4e70',                          # buy
    '\u91c7\u8d2d',                          # purchase
    '\u662f\u5426\u82b1\u8d39',              # spend confirmation
    '\u82b1\u8d39',                          # spend
    '\u5151\u6362',                          # exchange
    '\u6e90\u77f3',                          # originite
    '\u5408\u6210\u7389',                    # orundum
    '\u8be6\u7ec6\u60c5\u62a5',              # details
    '\u4ee3\u7406\u6307\u6325',              # auto deploy
    '\u63a8\u8350\u5e73\u5747\u7b49\u7ea7',  # recommended level
    '\u62a5\u916c',                          # rewards
    '\u89e3\u9501',                          # unlock
    '\u5173\u5361\u8be6\u60c5',              # stage details
    'MISSIONRESULTS',
    'MISSIONFAILED',
})

# Same-place tests.  Same-code detections from overlapping tiles are close.
_SAME_IOU = 0.20
_SAME_CENTRE = 20.0
# Different exact codes at essentially one position are ambiguous.
_CLASH_IOU = 0.50
_CLASH_CENTRE = 14.0


class _Detection:
    __slots__ = ('code', 'entry', 'box', 'score')

    def __init__(self, code, entry, box, score):
        self.code = code
        self.entry = entry
        self.box = box
        self.score = score


def enrich(image, texts, zoom, check_stop, known_codes):
    """Return ``texts`` plus recovered exact SN codes, or equivalent contents.

    ``image`` must be a 1280x720 BGR uint8 frame.  ``zoom(image, roi, scale=2)``
    must return full-frame OCR coordinates.  ``check_stop`` is called before
    every zoom tile and its exception is allowed to propagate.  ``known_codes``
    is the caller's exact battle-code collection; this module never invents one.
    """
    original = _as_list(texts)
    if not _valid_image(image):
        return original
    known = _known(known_codes)
    if not known:
        return original
    if _overlay_seen(original):
        return original
    if _anchor(original, FREE_EXPLORE, FREE_EXPLORE_BOUNDS) is None:
        return original
    if _anchor(original, CURRENT_REGION, CURRENT_REGION_BOUNDS) is None:
        return original

    initial = _detections(original, known)
    if not initial and any(_norm(_value(row,'text','')).upper() in known for row in original):
        # A present but corrupt/low-confidence exact label is contradictory
        # evidence; do not silently turn it into a missing-label recovery.
        return original
    seen_codes = {det.code for det in initial}
    if len(seen_codes) >= 2:
        return original
    initial_codes = set(_resolve(initial))
    if initial and not initial_codes:
        return original

    pool = list(initial)
    resolved = _resolve(pool)
    for roi in TILES[:MAX_ZOOM_TILES]:
        _stop(check_stop)
        rows = zoom(image, roi, scale=2)
        try:
            rows = list(rows or ())
        except TypeError:
            rows = []
        if _overlay_seen(rows):
            return original
        pool.extend(_detections(rows, known))
        resolved = _resolve(pool)
        # A crop that contradicts an existing label invalidates this fallback.
        # Keeping that old label alongside a different new one would otherwise
        # make the caller see two codes despite the unresolved contradiction.
        if not initial_codes.issubset(resolved):
            return original
        if len(resolved) >= 2:
            break

    if not resolved:
        return original
    additions = [det.entry for code, det in resolved.items()
                 if code not in initial_codes]
    if not additions:
        return original
    return original + [_copy_entry(entry) for entry in additions]


# --------------------------------------------------------------------- basics
def _as_list(texts):
    if texts is None or isinstance(texts, dict):
        return []
    try:
        return list(texts)
    except TypeError:
        return []


def _valid_image(image):
    shape = getattr(image, 'shape', None)
    dtype = getattr(image, 'dtype', None)
    return (isinstance(shape, tuple) and shape == IMAGE_SHAPE
            and str(dtype) == IMAGE_DTYPE)


def _value(entry, key, default=None):
    try:
        return entry.get(key, default)
    except AttributeError:
        return default


def _norm(value):
    text = ''.join(str(value).split())
    for dash in ('\u2014', '\u2013', '\u2212', '\uff0d'):
        text = text.replace(dash, '-')
    return text


def _number(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _score(entry):
    return _number(_value(entry, 'score'))


def _box(entry):
    """Return ``(x1, y1, x2, y2, cx, cy)`` or ``None`` for invalid geometry."""
    box = _value(entry, 'box')
    if not isinstance(box, (list, tuple)) or len(box) != 4:
        return None
    values = []
    for value in box:
        number = _number(value)
        if number is None:
            return None
        values.append(number)
    x1, y1, x2, y2 = values
    if not (0 <= x1 < x2 <= IMAGE_SHAPE[1]
            and 0 <= y1 < y2 <= IMAGE_SHAPE[0]):
        return None
    cx = _number(_value(entry, 'x'))
    cy = _number(_value(entry, 'y'))
    if cx is None or cy is None:
        return None
    # OCR's x/y must be the same box, with a small antialias tolerance.
    if not (x1 - 2 <= cx <= x2 + 2 and y1 - 2 <= cy <= y2 + 2):
        return None
    return x1, y1, x2, y2, cx, cy


def _inside(box, bounds):
    left, top, right, bottom = bounds
    x1, y1, x2, y2, _cx, _cy = box
    return left <= x1 and x2 <= right and top <= y1 and y2 <= bottom


def _map_box(box):
    return _inside(box, MAP_BOUNDS)


def _anchor(texts, label, bounds):
    matches = []
    for entry in texts:
        if _norm(_value(entry, 'text', '')) != label:
            continue
        score = _score(entry)
        if score is None or score < MIN_SCORE:
            continue
        box = _box(entry)
        if box is None or not _inside(box, bounds):
            continue
        matches.append(entry)
    return matches[0] if len(matches) == 1 else None


def _known(known_codes):
    if isinstance(known_codes, str):
        known_codes = (known_codes,)
    try:
        values = list(known_codes)
    except TypeError:
        return frozenset()
    codes = set()
    for value in values:
        if isinstance(value, str):
            code = _norm(value).upper()
            if code:
                codes.add(code)
    return frozenset(codes)


def _overlay_seen(texts):
    for entry in texts:
        word = _norm(_value(entry, 'text', '')).upper()
        if not word:
            continue
        if word in _OVERLAY_EXACT:
            return True
        for marker in _OVERLAY_EXACT:
            if len(marker) > 1 and marker in word:
                return True
    return False


def _detections(texts, known):
    found = []
    for entry in texts:
        code = _norm(_value(entry, 'text', '')).upper()
        if not code.startswith('SN-') or code not in known:
            continue
        # A truncated fragment is never a battle code, even if a caller's
        # known-code collection is malformed.
        if code.endswith('-') or not any(char.isdigit() for char in code):
            continue
        score = _score(entry)
        if score is None or score < MIN_SCORE:
            continue
        box = _box(entry)
        if box is None or not _map_box(box):
            continue
        found.append(_Detection(code, entry, box, score))
    return found


# ------------------------------------------------------------------ resolution
def _overlap(a, b):
    ax1, ay1, ax2, ay2 = a[:4]
    bx1, by1, bx2, by2 = b[:4]
    width = min(ax2, bx2) - max(ax1, bx1)
    height = min(ay2, by2) - max(ay1, by1)
    if width <= 0 or height <= 0:
        return 0.0
    inter = width * height
    union = ((ax2 - ax1) * (ay2 - ay1) + (bx2 - bx1) * (by2 - by1)
             - inter)
    return inter / union if union > 0 else 0.0


def _same(a, b):
    if _overlap(a, b) >= _SAME_IOU:
        return True
    dx = a[4] - b[4]
    dy = a[5] - b[5]
    return dx * dx + dy * dy <= _SAME_CENTRE * _SAME_CENTRE


def _clash(a, b):
    if _overlap(a, b) >= _CLASH_IOU:
        return True
    dx = a[4] - b[4]
    dy = a[5] - b[5]
    return dx * dx + dy * dy <= _CLASH_CENTRE * _CLASH_CENTRE


def _clusters(rows):
    parent = list(range(len(rows)))

    def find(index):
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    for i in range(len(rows)):
        for j in range(i):
            if _same(rows[i].box, rows[j].box):
                left, right = find(i), find(j)
                if left != right:
                    parent[right] = left
    groups = {}
    for index in range(len(rows)):
        groups.setdefault(find(index), []).append(index)
    return list(groups.values())


def _resolve(detections):
    """One unambiguous location per exact code; ambiguous codes are dropped."""
    grouped = {}
    for det in detections:
        grouped.setdefault(det.code, []).append(det)

    representatives = {}
    for code, rows in grouped.items():
        clusters = _clusters(rows)
        if len(clusters) != 1:
            continue  # the same code was read at two distinct positions
        best = max(clusters[0], key=lambda index: (rows[index].score, -index))
        representatives[code] = rows[best]

    rejected = set()
    codes = list(representatives)
    for i in range(len(codes)):
        for j in range(i + 1, len(codes)):
            if _clash(representatives[codes[i]].box,
                      representatives[codes[j]].box):
                rejected.add(codes[i])
                rejected.add(codes[j])
    for code in rejected:
        representatives.pop(code, None)
    return representatives


# ---------------------------------------------------------------------- output
def _stop(check_stop):
    if callable(check_stop):
        result = check_stop()
        if result:
            # AutoStory's check_stop raises; accept a truthy return as well.
            raise InterruptedError('stopped')


def _copy_entry(entry):
    try:
        copied = dict(entry)
    except (TypeError, ValueError):
        copied = {}
    if not copied:
        copied = {'text': _value(entry, 'text', ''),
                  'score': _value(entry, 'score', 0.0),
                  'x': _value(entry, 'x', 0.0),
                  'y': _value(entry, 'y', 0.0),
                  'box': list(_value(entry, 'box', []))}
    if isinstance(copied.get('box'), (list, tuple)):
        copied['box'] = list(copied['box'])
    return copied
