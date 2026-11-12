"""Strict DV-S panel identity, fixed observation regions and a verified exit.

Requires six independent OCR anchors; never infers clearance or unlock state.
Only the 1280x720 DV-S-1/2 layout is supported.
"""

from __future__ import annotations

import math

__all__ = ("identify", "panel_regions", "exit_target")

FRAME_WIDTH = 1280.0
FRAME_HEIGHT = 720.0
MIN_SCORE = 0.90

CODE_1 = "DV-S-1"
CODE_2 = "DV-S-2"
NAMES = {
    CODE_1: "\u56f0\u5fc3",  # KUNXIN
    CODE_2: "\u8861\u8651",  # HENGLU
}

ACCESS = "SPECIAL ACCESS CONTENT"
EXIT = "EXIT"
START = "\u5f00\u59cb\u884c\u52a8"  # START ACTION
CONDITION = "\u67e5\u770b\u6761\u4ef6"  # VIEW CONDITIONS

# Inclusive centre windows.  They are deliberately much tighter than the
# 1280x720 frame: a correctly spelled code at a map node or in an ordinary
# detail header must not satisfy the corresponding anchor.
CODE_WINDOW = (810.0, 168.0, 880.0, 215.0)
NAME_WINDOW = (1090.0, 166.0, 1170.0, 217.0)
ACCESS_WINDOW = (110.0, 150.0, 190.0, 190.0)
EXIT_WINDOW = (1118.0, 58.0, 1188.0, 96.0)
START_WINDOW = (915.0, 570.0, 1035.0, 618.0)
CONDITION_WINDOW = (70.0, 575.0, 158.0, 620.0)

# Verified in release/evidence/flow0936/dv-special.png: the X close control is
# to the right of the EXIT text, at the top-right corner.  This is a fixed tap
# target, never an OCR box corner and never the START ACTION button.
CLOSE_CROSS = (1208, 76)

_ANCHOR_ORDER = ("code", "name", "access", "exit", "start", "condition")

_SETTLEMENT_MARKERS = (
    "\u884c\u52a8\u7ed3\u675f",  # action end
    "\u6f14\u4e60\u7ed3\u675f",  # practice end
    "\u4f5c\u6218\u7ed3\u675f",  # operation end
    "\u6218\u6597\u7ed3\u675f",  # battle end
    "\u4efb\u52a1\u5931\u8d25",  # mission failed
    "\u884c\u52a8\u5931\u8d25",  # action failed
    "\u6f14\u4e60\u5931\u8d25",  # practice failed
    "MISSIONRESULTS",
    "MISSIONFAILED",
    "MISSIONACCOMPLISHED",
)

_PURCHASE_STRONG = (
    "\u662f\u5426\u82b1\u8d39",  # whether to spend
    "\u786e\u8ba4\u8d2d\u4e70",  # confirm purchase
    "\u8d2d\u4e70\u6210\u529f",  # purchase succeeded
    "\u8d2d\u4e70\u5931\u8d25",  # purchase failed
    "\u7acb\u5373\u8d2d\u4e70",  # buy now
    "\u652f\u4ed8\u6210\u529f",  # payment succeeded
    "\u652f\u4ed8\u5931\u8d25",  # payment failed
    "\u4fe1\u7528\u4ea4\u6613\u6240",  # credit store
    "\u5145\u503c",  # top up
)


def identify(texts, expected_code=None):
    """Return "DV-S-1" or "DV-S-2" for a fully proven panel, else None.

    expected_code is compared literally against the proven code.  A wrong,
    lowercase, prefixed, or suffixed expectation returns None.  Malformed OCR
    rows are ignored rather than raising.
    """
    identity = _identity(texts)
    if identity is None:
        return None
    code = identity["code"]
    if expected_code is not None and expected_code != code:
        return None
    return code


def panel_regions(texts):
    """Return six padded ROIs for the proven panel, else an empty tuple.

    Order: code, name, header, exit, action, condition.  Every box must be
    valid; one malformed box makes the whole result empty rather than a
    partially usable tuple.
    """
    identity = _identity(texts)
    if identity is None:
        return ()
    regions = []
    for key in _ANCHOR_ORDER:
        _, row, point = identity["anchors"][key]
        region = _region(row, point)
        if region is None:
            return ()
        regions.append(region)
    return tuple(regions)


def exit_target(texts):
    """Return the fixed close-cross tap only for a fully proven panel.

    The frame must pass the same six-anchor identity gate as identify(), and
    the EXIT occurrence must still be exact and inside its anchor window.  The
    result is a fresh dict; no OCR box and no action button is reused.
    """
    identity = _identity(texts)
    if identity is None:
        return None
    text, row, point = identity["anchors"]["exit"]
    if text != _label(EXIT) or _norm(row) != _label(EXIT):
        return None
    if not _inside(point, EXIT_WINDOW):
        return None
    return {"x": CLOSE_CROSS[0], "y": CLOSE_CROSS[1]}


def _identity(texts):
    rows = _rows(texts)
    if not rows or _blocked(rows):
        return None

    code_hits = _hits(rows, frozenset((CODE_1, CODE_2)))
    if len(code_hits) != 1:
        return None
    code, code_row, code_point = code_hits[0]
    if not _inside(code_point, CODE_WINDOW):
        return None

    name_hits = _hits(rows, frozenset(NAMES.values()))
    if len(name_hits) != 1:
        return None
    name, name_row, name_point = name_hits[0]
    if name != _label(NAMES[code]) or not _inside(name_point, NAME_WINDOW):
        return None

    access_hits = _hits(rows, frozenset((ACCESS,)))
    if len(access_hits) != 1 or not _inside(access_hits[0][2], ACCESS_WINDOW):
        return None

    exit_hits = _hits(rows, frozenset((EXIT,)))
    if len(exit_hits) != 1 or not _inside(exit_hits[0][2], EXIT_WINDOW):
        return None

    start_hits = _hits(rows, frozenset((START,)))
    if len(start_hits) != 1 or not _inside(start_hits[0][2], START_WINDOW):
        return None

    condition_hits = _hits(rows, frozenset((CONDITION,)))
    if len(condition_hits) != 1 or not _inside(condition_hits[0][2], CONDITION_WINDOW):
        return None

    access_point = access_hits[0][2]
    exit_point = exit_hits[0][2]
    start_point = start_hits[0][2]
    condition_point = condition_hits[0][2]

    # Row/column sanity checks make a scrambled collection of six correct
    # strings insufficient.  The header is above the title row, EXIT is the
    # top-right control, and the action/condition controls share the bottom.
    if abs(code_point[1] - name_point[1]) > 26.0:
        return None
    if code_point[0] >= name_point[0]:
        return None
    if access_point[1] >= code_point[1]:
        return None
    if access_point[0] >= code_point[0]:
        return None
    if exit_point[1] >= access_point[1]:
        return None
    if exit_point[0] <= name_point[0]:
        return None
    if start_point[1] <= code_point[1]:
        return None
    if condition_point[1] <= code_point[1]:
        return None
    if start_point[0] <= code_point[0]:
        return None
    if condition_point[0] >= code_point[0]:
        return None
    if abs(start_point[1] - condition_point[1]) > 30.0:
        return None

    return {
        "code": code,
        "anchors": {
            "code": (code, code_row, code_point),
            "name": (name, name_row, name_point),
            "access": access_hits[0],
            "exit": exit_hits[0],
            "start": start_hits[0],
            "condition": condition_hits[0],
        },
    }


def _rows(texts):
    """Return a list of dict rows without mutating or rejecting the caller."""
    if isinstance(texts, dict):
        return [texts]
    if texts is None:
        return []
    try:
        values = list(texts)
    except TypeError:
        return []
    return [value for value in values if isinstance(value, dict)]


def _label(value):
    """Whitespace-free, ASCII-case-folded exact label text."""
    if not isinstance(value, str):
        return ""
    return "".join(value.split()).upper()


def _norm(row):
    """Exact label text of one OCR row."""
    return _label(row.get("text", ""))


def _score(row):
    try:
        value = float(row.get("score", 0.0))
    except (TypeError, ValueError):
        return None
    if not math.isfinite(value):
        return None
    return value


def _confident(row):
    score = _score(row)
    return score is not None and score >= MIN_SCORE


def _point(row):
    try:
        x = float(row.get("x"))
        y = float(row.get("y"))
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(x) and math.isfinite(y)):
        return None
    if not (0.0 <= x <= FRAME_WIDTH and 0.0 <= y <= FRAME_HEIGHT):
        return None
    return (x, y)


def _inside(point, window):
    left, top, right, bottom = window
    return left <= point[0] <= right and top <= point[1] <= bottom


def _hits(rows, labels):
    """All exact, confident, finite-coordinate occurrences of labels."""
    wanted = frozenset(_label(label) for label in labels)
    found = []
    for row in rows:
        text = _norm(row)
        if text not in wanted:
            continue
        if not _confident(row):
            continue
        point = _point(row)
        if point is None:
            continue
        found.append((text, row, point))
    return found


def _blocked(rows):
    """Reject settlement screens and purchase/confirmation overlays first.

    Rejection text is scanned regardless of score: a battle result or a live
    purchase dialog must never be promoted to a panel just because one of its
    labels was read below the anchor threshold.
    """
    words = "".join(_norm(row) for row in rows)
    if not words:
        return False
    if any(marker in words for marker in _SETTLEMENT_MARKERS):
        return True
    if any(marker in words for marker in _PURCHASE_STRONG):
        return True
    if '\u653e\u5f03\u884c\u52a8' in words or '\u7ee7\u7eed\u884c\u52a8' in words:
        return True
    if "\u662f\u5426" in words and (
            "\u786e\u8ba4" in words or "\u53d6\u6d88" in words):
        return True
    if ("\u786e\u8ba4" in words or "\u786e\u5b9a" in words) and (
            "\u53d6\u6d88" in words or "\u5173\u95ed" in words):
        return True
    if "\u8d2d\u4e70" in words and (
            "\u786e\u8ba4" in words or "\u53d6\u6d88" in words or "\u6210\u529f" in words):
        return True
    if "\u6e90\u77f3" in words and (
            "\u5151\u6362" in words or "\u82b1\u8d39" in words):
        return True
    if "\u5408\u6210\u7389" in words and "\u5151\u6362" in words:
        return True
    return False


def _region(row, point):
    """Padded (left, top, width, height) ROI, or None if any part is invalid."""
    box = row.get("box")
    if not isinstance(box, (list, tuple)) or len(box) != 4:
        return None
    values = []
    for value in box:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(number):
            return None
        values.append(number)
    x1, y1, x2, y2 = values
    if not (0.0 <= x1 < x2 <= FRAME_WIDTH):
        return None
    if not (0.0 <= y1 < y2 <= FRAME_HEIGHT):
        return None
    cx, cy = point
    if not (x1 - 0.5 <= cx <= x2 + 0.5 and y1 - 0.5 <= cy <= y2 + 0.5):
        return None
    left = int(math.floor(x1)) - 4
    top = int(math.floor(y1)) - 4
    right = int(math.ceil(x2)) + 4
    bottom = int(math.ceil(y2)) + 4
    if left < 0 or top < 0 or right > int(FRAME_WIDTH) or bottom > int(FRAME_HEIGHT):
        return None
    width = right - left
    height = bottom - top
    if width <= 0 or height <= 0:
        return None
    return (left, top, width, height)
