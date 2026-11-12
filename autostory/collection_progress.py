"""Pure parser for collection-list completion cards (「完成进度」 tab).

The collection list draws each side-activity as a card: an ``current/total``
counter on the left side, then the activity title below it.  This module turns
already-recognised OCR rows into JSON-safe cards, binding each counter to at
most one title in the same fixed column.  It deliberately performs no OCR,
image, navigation, tab, colour, device or filesystem work; the caller confirms
the active tab, cyan badge, two independent OCR passes and fresh frames.

Contract
--------
``cards_from_rows(rows, profiles)`` accepts vision-shaped rows with ``text``,
``score`` and ``box=[x0, y0, x1, y1]`` in canonical 1280x720 coordinates, plus
a profile mapping/list built from ``data/catalogue.json``.  It returns a list
of plain ``dict`` objects sorted in reading order::

    {'base_group', 'title', 'current', 'total', 'full', 'score',
     'counter_box', 'title_box'}

Only unique base side-activity profiles are eligible: ``kind == 'side'`` and
``base == key`` (a missing ``base`` means the key).  Titles must match the whole
normalised Chinese title, or carry at most six non-Han decoration glyphs as in
``collection_index.matches``; no fuzzy matching is used.  A title that maps to
two different bases is rejected.

A ratio must be one complete OCR token (for example ``39/39``).  Whitespace and
the full-width slash are accepted, but missing digits/slash, split fragments,
extra text, zero denominators, over-total numerators and values above 9999 are
rejected.  Ratio confidence must be at least 0.88 and title confidence at least
0.90; ``score`` is the conservative minimum of the two.

Spatial rules
-------------
Cards occupy four non-overlapping columns ``(50,328)``, ``(328,600)``,
``(600,875)`` and ``(875,1155)`` with text in ``y=75..695``.  The counter must
sit in the left half of its column and its centre must be 45..175 px above the
title centre.  A counter and title in different columns, a title above a
counter, or a row outside the column never bind.  A counter with more than one
eligible title and a title with more than one counter are ambiguous: those
affected cards are dropped while unrelated cards remain.  Identical overlapping
OCR duplicates are collapsed before binding.

The function never mutates its inputs, uses only the standard library, and
returns ``[]`` for unusable input instead of raising.
"""
from __future__ import annotations

import math
import numbers
import re
from collections.abc import Mapping

__all__ = ["cards_from_rows"]

# Four non-overlapping card columns, canonical 1280x720 x coordinates.
COLUMNS = ((50.0, 328.0), (328.0, 600.0), (600.0, 875.0), (875.0, 1155.0))
Y_MIN = 75.0
Y_MAX = 695.0
MIN_COUNTER_SCORE = 0.88
MIN_TITLE_SCORE = 0.90
MIN_TITLE_GAP = 45.0
MAX_TITLE_GAP = 175.0
ROW_TOLERANCE = 20.0
MAX_VALUE = 9999
MAX_EXTRA_GLYPHS = 6
FULLWIDTH_SLASH = "\uff0f"

_FRACTION = re.compile(r"^([0-9]+)/([0-9]+)$")


def _number(value):
    """Return a finite real as float, or ``None`` for bool/NaN/invalid."""
    if isinstance(value, bool) or not isinstance(value, numbers.Real):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _box(value):
    """Return a validated ``(x0, y0, x1, y1)`` tuple or ``None``."""
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    values = tuple(_number(item) for item in value)
    if any(item is None for item in values):
        return None
    x0, y0, x1, y1 = values
    if not (0.0 <= x0 < x1 and 0.0 <= y0 < y1):
        return None
    return (x0, y0, x1, y1)


def _in_band(box):
    return Y_MIN <= box[1] and box[3] <= Y_MAX


def _column(box):
    """Return the single column index containing the whole box, or ``None``."""
    hits = [index for index, (left, right) in enumerate(COLUMNS)
            if left <= box[0] and box[2] <= right]
    return hits[0] if len(hits) == 1 else None


def _compact(text):
    return "".join(ch for ch in text.replace(FULLWIDTH_SLASH, "/")
                   if not ch.isspace())


def _key(title):
    """Mirror ``collection_index.key`` without importing project state."""
    value = re.sub(r"\s+", "", str(title or ""))
    return (value.replace("\u2014", "-").replace("\u2013", "-")
            .replace("\uff1a", ":").replace("\u30fb", "\u00b7"))


def _decoration_only(text):
    """True when the extra glyphs are punctuation/Latin/digits (not Han)."""
    for char in text:
        if char.isalpha() and not ("a" <= char.lower() <= "z"):
            return False
    return True


def _matches(recognized, title):
    """Bounded decoration match, copied from ``collection_index.matches``."""
    folded = _key(recognized)
    wanted = _key(title)
    if not folded or not wanted:
        return False
    if folded == wanted:
        return True
    if len(wanted) < 2:
        return False
    start = folded.find(wanted)
    if start < 0:
        return False
    decoration = folded[:start] + folded[start + len(wanted):]
    if len(decoration) > MAX_EXTRA_GLYPHS:
        return False
    return _decoration_only(decoration)


def _profile_items(profiles):
    """Normalise a profile mapping/list to ``(key, value)`` pairs."""
    if isinstance(profiles, Mapping):
        inner = profiles.get("profiles")
        if isinstance(inner, (Mapping, list, tuple)):
            profiles = inner
    if isinstance(profiles, Mapping):
        return list(profiles.items())
    if isinstance(profiles, (list, tuple)):
        return [(None, item) for item in profiles]
    return []


def _title_index(profiles):
    """Return ``normalised title -> (base, canonical title)``.

    Only unique base side-activity profiles survive.  A title seen under two
    distinct bases is removed entirely, so no caller can pick one arbitrarily.
    """
    index = {}
    ambiguous = set()
    for key, item in _profile_items(profiles):
        if not isinstance(item, Mapping):
            continue
        if item.get("kind") != "side":
            continue
        title = item.get("title")
        if not isinstance(title, str):
            continue
        title_key = _key(title)
        if not title_key:
            continue
        base = item.get("base")
        if key is None:
            key = item.get("key")
        if base is None:
            if key is None:
                continue
            base = key
        if key is not None and str(base) != str(key):
            continue
        base = str(base)
        if not base:
            continue
        if title_key in index and index[title_key][0] != base:
            ambiguous.add(title_key)
        else:
            index[title_key] = (base, title)
    for title_key in ambiguous:
        index.pop(title_key, None)
    return index


def _match_title(text, titles):
    """Return exactly one ``(base, canonical title)`` or ``None``."""
    matches = set()
    for title in titles.values():
        if _matches(text, title[1]):
            matches.add(title)
    return matches.pop() if len(matches) == 1 else None


def _fraction(text):
    """Parse one explicit whole fraction, or return ``None``."""
    match = _FRACTION.fullmatch(_compact(text))
    if not match:
        return None
    values = []
    for digits in match.groups():
        significant = digits.lstrip("0") or "0"
        if len(significant) > 4:
            return None
        values.append(int(significant))
    current, total = values
    if not (0 <= current <= total and 0 < total <= MAX_VALUE):
        return None
    return current, total


def _center(box):
    return ((box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0)


def _row_candidates(rows, titles):
    """Extract valid counter/title candidates without mutating the input."""
    counters = []
    title_rows = []
    if rows is None or isinstance(rows, (str, bytes, bytearray, Mapping)):
        return counters, title_rows
    try:
        iterator = iter(rows)
    except TypeError:
        return counters, title_rows
    for item in iterator:
        if not isinstance(item, Mapping):
            continue
        try:
            text = item["text"]
            score = item["score"]
            box = item["box"]
        except (KeyError, TypeError):
            continue
        if not isinstance(text, str):
            continue
        score = _number(score)
        if score is None or not (0.0 <= score <= 1.0):
            continue
        box = _box(box)
        if box is None or not _in_band(box):
            continue
        if "x" in item:
            x = _number(item["x"])
            if x is None or not (0.0 <= x <= 1280.0):
                continue
        if "y" in item:
            y = _number(item["y"])
            if y is None or not (0.0 <= y <= 720.0):
                continue
        column = _column(box)
        if column is None:
            continue
        center_x, center_y = _center(box)
        fraction = _fraction(text)
        if fraction is not None and score >= MIN_COUNTER_SCORE:
            left, right = COLUMNS[column]
            if center_x < (left + right) / 2.0:
                counters.append({
                    "kind": "counter",
                    "current": fraction[0],
                    "total": fraction[1],
                    "score": score,
                    "box": box,
                    "column": column,
                    "center_y": center_y,
                })
        title = _match_title(text, titles)
        if title is not None and score >= MIN_TITLE_SCORE:
            title_rows.append({
                "kind": "title",
                "base": title[0],
                "title": title[1],
                "score": score,
                "box": box,
                "column": column,
                "center_y": center_y,
            })
    return counters, title_rows


def _overlap(first, second):
    """True when two OCR boxes cover at least half of the smaller box."""
    width = max(0.0, min(first[2], second[2]) - max(first[0], second[0]))
    height = max(0.0, min(first[3], second[3]) - max(first[1], second[1]))
    intersection = width * height
    area_first = (first[2] - first[0]) * (first[3] - first[1])
    area_second = (second[2] - second[0]) * (second[3] - second[1])
    smaller = min(area_first, area_second)
    return smaller > 0.0 and intersection >= 0.5 * smaller


def _dedup(items, same):
    """Collapse identical overlapping OCR rows, keeping the best score."""
    kept = []
    for item in sorted(items, key=lambda value: -value["score"]):
        if any(same(item, other) and _overlap(item["box"], other["box"])
               for other in kept):
            continue
        kept.append(item)
    return kept


def _same_counter(first, second):
    return (first["column"] == second["column"]
            and first["current"] == second["current"]
            and first["total"] == second["total"])


def _same_title(first, second):
    return (first["column"] == second["column"]
            and first["base"] == second["base"]
            and first["title"] == second["title"])


def _window(counter, title):
    """Counter is above the title in one column, 45..175 px apart."""
    if counter["column"] != title["column"]:
        return False
    gap = title["center_y"] - counter["center_y"]
    return MIN_TITLE_GAP <= gap <= MAX_TITLE_GAP


def _ordered(cards):
    """Sort cards by visual row, then left-to-right within the row."""
    rows = []
    for card in sorted(cards, key=lambda value: (
            value["counter_box"][1], value["counter_box"][0])):
        placed = False
        for row in rows:
            representative = row[0]
            if (abs(card["counter_center_y"] - representative["counter_center_y"])
                    <= ROW_TOLERANCE
                    or _vertical_overlap(card["counter_box"],
                                         representative["counter_box"])):
                row.append(card)
                placed = True
                break
        if not placed:
            rows.append([card])
    rows.sort(key=lambda row: min(item["counter_center_y"] for item in row))
    ordered = []
    for row in rows:
        row.sort(key=lambda item: item["counter_box"][0])
        ordered.extend(row)
    return ordered


def _vertical_overlap(first, second):
    return min(first[3], second[3]) > max(first[1], second[1])


def cards_from_rows(rows, profiles):
    """Return unambiguous completion cards, or ``[]`` when none are valid."""
    titles = _title_index(profiles)
    if not titles:
        return []
    counters, title_rows = _row_candidates(rows, titles)
    counters = _dedup(counters, _same_counter)
    title_rows = _dedup(title_rows, _same_title)
    if not counters or not title_rows:
        return []

    counter_titles = [
        [index for index, title in enumerate(title_rows)
         if _window(counter, title)]
        for counter in counters
    ]
    title_counters = [
        [index for index, counter in enumerate(counters)
         if _window(counter, title)]
        for title in title_rows
    ]

    cards = []
    for counter, hits in zip(counters, counter_titles):
        if len(hits) != 1:
            continue
        title_index = hits[0]
        if len(title_counters[title_index]) != 1:
            continue
        title = title_rows[title_index]
        cards.append({
            "base_group": title["base"],
            "title": title["title"],
            "current": counter["current"],
            "total": counter["total"],
            "full": counter["current"] == counter["total"],
            "score": float(min(counter["score"], title["score"])),
            "counter_box": [float(value) for value in counter["box"]],
            "title_box": [float(value) for value in title["box"]],
            "counter_center_y": counter["center_y"],
        })

    ordered = _ordered(cards)
    return [{
        "base_group": card["base_group"],
        "title": card["title"],
        "current": card["current"],
        "total": card["total"],
        "full": card["full"],
        "score": card["score"],
        "counter_box": card["counter_box"],
        "title_box": card["title_box"],
    } for card in ordered]
