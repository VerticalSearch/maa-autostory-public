"""One-pass index of the side-story collection list (「乐章收录」).

Walking that list once per chapter is the dominant navigation cost of a full
scan: every chapter restarts at the top. A single OCR pass yields
``title -> page``, which lets a round order its chapters by list position and
use a bounded blind scroll before the normal title check.

The index is advisory only. A page number is used as a blind scroll distance and
the chapter title is still verified before anything is tapped; a stale index
costs speed, never accuracy.

``complete`` records that the walk reached the end of the list. It does **not**
mean every decorative card title was recognised: OCR misses are expected, so a
title absent from the index is never evidence that the chapter is absent.
"""
from __future__ import annotations

import math
import re
import time

from . import common

FILE = 'collection-index.json'
TTL = 12 * 3600
MAX_PAGES = 24
SCHEMA = 1
# A decorative OCR label may carry a short logo prefix/suffix around the real
# title ("+揭幕者们", "LIEBI尘影余音"). Bound that tolerance and allow only
# punctuation/Latin/digit decoration: extra Han text means a different label.
MAX_EXTRA_GLYPHS = 6


def key(title):
    """Same folding as the navigation title matcher, kept local to avoid a cycle."""
    value = re.sub(r'\s+', '', str(title or ''))
    return (value.replace('—', '-').replace('–', '-')
            .replace('：', ':').replace('・', '·'))


def _decoration_only(text):
    """True when ``text`` has no letters other than Latin (Han is rejected)."""
    for char in text:
        if char.isalpha() and not ('a' <= char.lower() <= 'z'):
            return False
    return True


def matches(recognized, title):
    """Exact title identity, with a bounded decorative-label tolerance.

    Multi-glyph titles must appear in full. A few non-Han decoration glyphs may
    surround them, so "+揭幕者们" and "LIEBI尘影余音" match while "新生路途"
    does not match "生路". Single-glyph titles are exact only.
    """
    folded = key(recognized)
    wanted = key(title)
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


def _path():
    return common.DATA / FILE


def _age(at, max_age):
    """Seconds since ``at``, or None for stale/malformed/future timestamps."""
    try:
        stamp = float(at)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(stamp):
        return None
    age = time.time() - stamp
    if age < -60:
        # A clock jump or a hand-edited cache must not look fresh forever.
        return None
    return max(0.0, age) if age <= max_age else None


def load(max_age=TTL):
    """Return the cached index, or None when absent, stale or malformed."""
    try:
        data = common.read_json(_path(), None)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get('schema') != SCHEMA:
        return None
    entries = data.get('pages_by_title')
    if not isinstance(entries, dict) or not entries:
        return None
    age = _age(data.get('at'), max_age)
    if age is None:
        return None
    try:
        pages = int(data.get('pages') or 0)
    except (TypeError, ValueError, OverflowError):
        return None
    clean = {}
    for title, page in entries.items():
        try:
            page = int(page)
        except (TypeError, ValueError, OverflowError):
            continue
        if 0 <= page <= MAX_PAGES and isinstance(title, str) and key(title):
            clean[key(title)] = page
    if not clean:
        return None
    # ``complete`` only records that the list walk ended. It says nothing about
    # whether every decorative title was recognised; callers must not infer
    # absence from it.
    return {'at': float(data['at']), 'pages': pages, 'pages_by_title': clean,
            'complete': bool(data.get('complete'))}


def save(pages_by_title, pages, complete=False):
    """Persist the index; a read-only runtime directory simply skips caching."""
    clean = {}
    for title, page in (pages_by_title or {}).items():
        folded = key(title)
        if not folded:
            continue
        try:
            page = int(page)
        except (TypeError, ValueError, OverflowError):
            continue
        if 0 <= page <= MAX_PAGES:
            clean[folded] = page
    if not clean:
        return None
    try:
        count = max(0, min(int(pages or 0), MAX_PAGES))
    except (TypeError, ValueError, OverflowError):
        count = 0
    payload = {'schema': SCHEMA, 'at': time.time(), 'pages': count,
               'complete': bool(complete), 'pages_by_title': clean}
    try:
        common.write_json(_path(), payload)
    except OSError:
        return None
    return load()


def page_of(index, title):
    """Page number for one chapter, or None when the index does not contain it."""
    if not isinstance(index, dict):
        return None
    entries = index.get('pages_by_title') or {}
    if not isinstance(entries, dict):
        return None
    wanted = key(title)
    if not wanted:
        return None
    for name, page in entries.items():
        if matches(name, title):
            try:
                return int(page)
            except (TypeError, ValueError, OverflowError):
                return None
    return None


def titles(index):
    return sorted(str(name) for name in ((index or {}).get('pages_by_title') or {}))


def summary(index=None):
    data = index if isinstance(index, dict) else load()
    if not data:
        return {'indexed': 0, 'pages': 0, 'complete': False}
    return {'indexed': len(data['pages_by_title']), 'pages': data['pages'],
            'complete': bool(data.get('complete'))}
