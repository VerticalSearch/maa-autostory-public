"""Persistent hints for the side-story collection list (「乐章收录」).

Walking that list from the top costs roughly 2.6 s per screen, so a real
targeted lookup that exhausted both directions is remembered for a bounded
time.

A negative hint is **not** proof that a chapter does not exist. It only records
that one actual targeted search failed recently. Index-based absence is not a
lookup at all, so legacy entries written without this schema are ignored (left
on disk, never wiped) and a positive index observation clears any negative.
"""
from __future__ import annotations

import math
import time

from . import common

FILE = 'nav-hints.json'
PAGE_TTL = 24 * 3600
MISS_TTL = 6 * 3600
MAX_PAGES = 40
SCHEMA = 2
SOURCE_SEARCH = 'targeted_search'
SAFE_SOURCES = frozenset({SOURCE_SEARCH})
MAX_ATTEMPTS = 1000
# Tolerate a small clock skew, but a timestamp far in the future is malformed
# and must not make an entry look fresh forever.
FUTURE_TOLERANCE = 60


def _path():
    return common.DATA / FILE


def _load():
    try:
        data = common.read_json(_path(), {})
    except (OSError, ValueError):
        return {'pages': {}, 'missing': {}}
    if not isinstance(data, dict):
        return {'pages': {}, 'missing': {}}
    for key in ('pages', 'missing'):
        if not isinstance(data.get(key), dict):
            data[key] = {}
    return data


def _save(data):
    # Hints are an optimisation: a read-only runtime directory must never break
    # navigation, so a failed write is simply forgotten.
    try:
        common.write_json(_path(), data)
    except OSError:
        pass


def _age(entry):
    """Seconds since the entry was written, or None when unusable."""
    if not isinstance(entry, dict):
        return None
    try:
        at = float(entry.get('at'))
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(at):
        return None
    age = time.time() - at
    if age < -FUTURE_TOLERANCE:
        return None
    return max(0.0, age)


def _integer(value, default=None):
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return default


def _page_from(data, prefix, max_age):
    entry = data['pages'].get(prefix)
    age = _age(entry)
    if age is None or age > max_age:
        return None
    pages = _integer(entry.get('pages'), None)
    direction = _integer(entry.get('direction'), None)
    if pages is None or direction not in (1, -1) or not 0 <= pages <= MAX_PAGES:
        return None
    return {'direction': direction, 'pages': pages}


def _missing_from(data, prefix, max_age):
    entry = data['missing'].get(prefix)
    if not isinstance(entry, dict):
        return None
    # Only a failure of a real targeted lookup is actionable. Older entries
    # (no schema/source) were written from index-based absence and are ignored.
    if not isinstance(entry.get('source'), str) or entry['source'] not in SAFE_SOURCES:
        return None
    if _integer(entry.get('schema'), 0) < SCHEMA:
        return None
    age = _age(entry)
    if age is None or age > max_age:
        return None
    return age


def page_hint(prefix, max_age=PAGE_TTL):
    """Remembered list position for one chapter, or None when stale/unknown."""
    return _page_from(_load(), prefix, max_age)


def remember_page(prefix, direction, pages):
    """Record where the chapter was found; a hit also clears a missing mark."""
    if not isinstance(prefix, str) or not prefix:
        return
    pages = _integer(pages, None)
    if pages is None:
        return
    try:
        direction = 1 if int(direction) >= 0 else -1
    except (TypeError, ValueError, OverflowError):
        return
    data = _load()
    data['pages'][prefix] = {'direction': direction, 'pages': max(0, min(pages, MAX_PAGES)),
                             'at': time.time(), 'schema': SCHEMA}
    data['missing'].pop(prefix, None)
    _save(data)


def missing(prefix, max_age=MISS_TTL):
    """Seconds since a real targeted lookup failed, or None.

    ``None`` also covers legacy index-derived entries: they are not evidence.
    """
    return _missing_from(_load(), prefix, max_age)


def mark_missing(prefix, source=SOURCE_SEARCH):
    """Remember a failed *targeted* lookup. Never call this for index absence."""
    if not isinstance(source, str) or source not in SAFE_SOURCES:
        raise ValueError('only a real targeted lookup may record a negative hint')
    if not isinstance(prefix, str) or not prefix:
        return
    data = _load()
    previous = data['missing'].get(prefix)
    attempts = _integer(previous.get('attempts'), 0) if isinstance(previous, dict) else 0
    attempts = min(max(0, attempts) + 1, MAX_ATTEMPTS)
    data['missing'][prefix] = {'at': time.time(), 'attempts': attempts,
                               'schema': SCHEMA, 'source': source}
    # A chapter that is not in the list cannot have a usable page hint.
    data['pages'].pop(prefix, None)
    _save(data)


def clear_missing(prefix):
    data = _load()
    if data['missing'].pop(prefix, None) is not None:
        _save(data)


def summary():
    """Counts of entries that are still effective; the UI never needs timestamps."""
    data = _load()
    pages = sum(1 for prefix in data['pages'] if _page_from(data, prefix, PAGE_TTL) is not None)
    absent = sum(1 for prefix in data['missing']
                 if _missing_from(data, prefix, MISS_TTL) is not None)
    return {'pages': pages, 'missing': absent}
