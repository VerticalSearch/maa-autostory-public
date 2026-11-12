"""Pure OCR parser for the activity map's lower-left completion counter.

The activity map shows a ``current/total`` counter in the lower-left corner
(for example ``62/62`` or ``81/81``).  A full counter means the whole activity
has already been cleared, so the caller may skip it.  This module only turns
already-recognised OCR rows into that ratio; it deliberately does not capture
frames, call OCR, click, navigate, read files, or inspect project state.

Contract
--------
``ratio_from_rows(rows, width=1280, height=720)`` accepts the rows returned by
``autostory.vision.Vision.texts`` / ``texts_zoom``.  Every row is a mapping with
``text``, ``score`` and ``box=[x0, y0, x1, y1]`` in original-image pixels.
``x``/``y`` are accepted when present, but boxes are the geometry authority.

The only accepted region is the fixed lower-left ROI, whose 1280x720 reference
is ``(78, 630, 225, 704)``; it is scaled independently by ``width/1280`` and
``height/720``.  A box must lie entirely inside that ROI.  A digit token must
have an OCR score of at least 0.88, and the same floor is applied to every
token consumed by a split reading (including its slash).

A result is a plain ``dict`` with integer ``current``/``total``, ``full`` only
when ``current == total``, the minimum score of the consumed tokens, and the
canonical text ``"<current>/<total>"``.  Everything else returns ``None``.
The parser accepts a single ``62/62`` token, ``62`` + ``/62``, ``62/`` + ``62``,
or ``62`` + ``/`` + ``62``.  Tokens must be adjacent on one OCR line (their
boxes must vertically overlap; equal centre ``y`` values are neither required
nor sufficient) and separated by at most 18 reference pixels.

Spacing and the full-width slash (``U+FF0F``) are allowed.  There is no
guesswork: a missing slash, ``O``/``I``/``l`` substitutions, percent signs,
signs, decimals, control text, low-confidence readings, conflicts and
multi-line readings all fail closed.  The function never mutates its input and
uses only the standard library.
"""
from __future__ import annotations

import math
import numbers
import re
from collections.abc import Mapping

__all__ = ["ratio_from_rows"]

REFERENCE_WIDTH = 1280.0
REFERENCE_HEIGHT = 720.0
# Lower-left completion counter, 1280x720 reference pixels.
REFERENCE_ROI = (78.0, 630.0, 225.0, 704.0)
MIN_SCORE = 0.88
MAX_VALUE = 9999
MAX_GAP = 18.0
FULLWIDTH_SLASH = "\uff0f"

_FULL = re.compile(r"^([0-9]+)/([0-9]+)$")
_LEFT = re.compile(r"^([0-9]+)/$")
_RIGHT = re.compile(r"^/([0-9]+)$")
_INTEGER = re.compile(r"^([0-9]+)$")
_DIGITS = frozenset("0123456789")


def _number(value):
    """Return a finite real as float, or ``None`` for bool/NaN/invalid."""
    if isinstance(value, bool) or not isinstance(value, numbers.Real):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _dimension(value):
    number = _number(value)
    return number if number is not None and number > 0.0 else None


def _parse_box(value):
    """Return a validated box tuple, or ``None`` for malformed geometry."""
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    values = tuple(_number(item) for item in value)
    if any(item is None for item in values):
        return None
    x0, y0, x1, y1 = values
    if not (x0 < x1 and y0 < y1):
        return None
    return (x0, y0, x1, y1)


def _compact(text):
    """Fold OCR spacing and the full-width slash; preserve everything else."""
    return "".join(ch for ch in text.replace(FULLWIDTH_SLASH, "/")
                   if not ch.isspace())


def _inside(box, roi):
    x0, y0, x1, y1 = box
    rx0, ry0, rx1, ry1 = roi
    return rx0 <= x0 and ry0 <= y0 and x1 <= rx1 and y1 <= ry1


def _vertical_overlap(first, second):
    """True when two boxes share vertical extent (not merely a centre)."""
    return min(first[3], second[3]) > max(first[1], second[1])


def _same_line(boxes):
    return min(box[3] for box in boxes) > max(box[1] for box in boxes)


def _value(digits):
    """Parse explicit ASCII digits; values above 9999 become a sentinel."""
    significant = digits.lstrip("0") or "0"
    if len(significant) > 4:
        return MAX_VALUE + 1
    return int(significant)


def _token(text):
    """Classify a compacted OCR token, or ``None`` when it is not numeric."""
    if not text or any(ch not in _DIGITS and ch != "/" for ch in text):
        return None
    match = _FULL.fullmatch(text)
    if match:
        return {"kind": "full", "current": _value(match.group(1)),
                "total": _value(match.group(2))}
    match = _LEFT.fullmatch(text)
    if match:
        return {"kind": "left", "current": _value(match.group(1))}
    match = _RIGHT.fullmatch(text)
    if match:
        return {"kind": "right", "total": _value(match.group(1))}
    match = _INTEGER.fullmatch(text)
    if match:
        return {"kind": "integer", "value": _value(match.group(1))}
    if text == "/":
        return {"kind": "slash"}
    return None


def _valid_ratio(current, total):
    return (0 <= current <= MAX_VALUE and 1 <= total <= MAX_VALUE
            and current <= total)


def _lines(items):
    """Group ROI items into OCR lines by vertical box overlap."""
    if not items:
        return []
    parent = list(range(len(items)))

    def find(index):
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(first, second):
        root_first, root_second = find(first), find(second)
        if root_first != root_second:
            parent[root_second] = root_first

    for second in range(len(items)):
        for first in range(second):
            if _vertical_overlap(items[first]["box"], items[second]["box"]):
                union(first, second)

    groups = {}
    for index, item in enumerate(items):
        groups.setdefault(find(index), []).append(item)
    return list(groups.values())


def _candidate(items, indices, current, total, max_gap):
    """Build one candidate from contiguous tokens, or fail closed.

    Returns ``None`` when the geometry is not one coherent adjacent line,
    ``"invalid"`` when an explicit numeric reading violates the value
    contract, and a candidate dict otherwise.
    """
    boxes = [items[index]["box"] for index in indices]
    if not _same_line(boxes):
        return None
    for first, second in zip(boxes, boxes[1:]):
        if second[0] - first[2] > max_gap:
            return None
    if not _valid_ratio(current, total):
        return "invalid"
    union = (
        min(box[0] for box in boxes),
        min(box[1] for box in boxes),
        max(box[2] for box in boxes),
        max(box[3] for box in boxes),
    )
    return {
        "current": current,
        "total": total,
        "full": current == total,
        "score": min(items[index]["score"] for index in indices),
        "text": f"{current}/{total}",
        "box": union,
    }


def ratio_from_rows(rows, width=1280, height=720):
    """Return the one unambiguous lower-left completion ratio, or ``None``.

    ``rows`` may be any iterable of mappings.  The function does not mutate
    the iterable, its mappings, or their boxes.  Malformed mappings, missing
    boxes, non-finite/bool coordinates, invalid dimensions and invalid scores
    fail closed.  Rows whose complete box lies outside the fixed ROI are
    ignored, because callers may pass a full-screen OCR result.
    """
    width = _dimension(width)
    height = _dimension(height)
    if width is None or height is None:
        return None
    if rows is None or isinstance(rows, (str, bytes, bytearray, Mapping)):
        return None
    try:
        iterator = iter(rows)
    except TypeError:
        return None

    scale_x = width / REFERENCE_WIDTH
    scale_y = height / REFERENCE_HEIGHT
    roi = (
        REFERENCE_ROI[0] * scale_x,
        REFERENCE_ROI[1] * scale_y,
        REFERENCE_ROI[2] * scale_x,
        REFERENCE_ROI[3] * scale_y,
    )
    if not (roi[0] < roi[2] and roi[1] < roi[3]):
        return None
    max_gap = MAX_GAP * scale_x
    if not math.isfinite(max_gap) or max_gap < 0.0:
        return None

    items = []
    for row in iterator:
        if not isinstance(row, Mapping):
            return None
        try:
            text = row["text"]
            score = row["score"]
            box = row["box"]
        except (KeyError, TypeError):
            return None
        if not isinstance(text, str):
            return None
        score = _number(score)
        if score is None or score < 0.0 or score > 1.0:
            return None
        box = _parse_box(box)
        if box is None:
            return None
        # Optional vision centre coordinates: validate them when present, but
        # never require them because the box is the authoritative geometry.
        if "x" in row:
            x = _number(row["x"])
            if x is None or not (0.0 <= x <= width):
                return None
        if "y" in row:
            y = _number(row["y"])
            if y is None or not (0.0 <= y <= height):
                return None
        if not _inside(box, roi):
            continue
        compact = _compact(text)
        if not compact:
            continue
        token = _token(compact)
        # Low-confidence numeric tokens are never accepted, but they remain
        # in the line as barriers so they cannot disappear between two pieces.
        if token is not None and score < MIN_SCORE:
            token = None
        items.append({
            "box": box,
            "score": score,
            "token": token,
        })

    if not items:
        return None

    candidates = []
    invalid = False
    for line in _lines(items):
        ordered = sorted(line, key=lambda item: (
            item["box"][0], item["box"][2], item["box"][1]))
        for index, item in enumerate(ordered):
            token = item["token"]
            if token is None:
                continue
            if token["kind"] == "full":
                candidate = _candidate(
                    ordered, [index], token["current"], token["total"], max_gap)
                if candidate == "invalid":
                    invalid = True
                elif candidate is not None:
                    candidates.append(candidate)

            # Two-token split: ``62`` + ``/62`` or ``62/`` + ``62``.
            if index + 1 < len(ordered):
                second = ordered[index + 1]["token"]
                if second is not None:
                    current = total = None
                    if token["kind"] == "integer" and second["kind"] == "right":
                        current, total = token["value"], second["total"]
                    elif token["kind"] == "left" and second["kind"] == "integer":
                        current, total = token["current"], second["value"]
                    if current is not None:
                        candidate = _candidate(
                            ordered, [index, index + 1], current, total, max_gap)
                        if candidate == "invalid":
                            invalid = True
                        elif candidate is not None:
                            candidates.append(candidate)

            # Three-token split: ``62`` + ``/`` + ``62``.
            if index + 2 < len(ordered):
                second = ordered[index + 1]["token"]
                third = ordered[index + 2]["token"]
                if (token["kind"] == "integer"
                        and second is not None and second["kind"] == "slash"
                        and third is not None and third["kind"] == "integer"):
                    candidate = _candidate(
                        ordered, [index, index + 1, index + 2],
                        token["value"], third["value"], max_gap)
                    if candidate == "invalid":
                        invalid = True
                    elif candidate is not None:
                        candidates.append(candidate)

    if invalid or not candidates:
        return None

    # A candidate's union box is used to distinguish one counter line from
    # multiple rows.  Identical duplicate readings on one line are harmless;
    # conflicting values or more than one line are ambiguous and fail closed.
    candidate_lines = []
    for candidate in candidates:
        matched = [line for line in candidate_lines
                   if any(_vertical_overlap(candidate["box"], other["box"])
                          for other in line)]
        if matched:
            target = matched[0]
            target.append(candidate)
            for extra in matched[1:]:
                target.extend(extra)
                candidate_lines.remove(extra)
        else:
            candidate_lines.append([candidate])
    if len(candidate_lines) != 1:
        return None

    values = {(candidate["current"], candidate["total"])
              for candidate in candidates}
    if len(values) != 1:
        return None
    best = max(candidates, key=lambda candidate: (
        candidate["score"], -candidate["box"][0]))
    return {
        "current": best["current"],
        "total": best["total"],
        "full": best["full"],
        "score": float(best["score"]),
        "text": best["text"],
    }
