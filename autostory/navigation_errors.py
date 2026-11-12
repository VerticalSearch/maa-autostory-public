"""Navigation errors that are local to one chapter entry.

``ChapterEntryUnavailable`` is deliberately distinct from a device or global
navigation failure.  ``Navigator.enter`` raises it only after it has either
walked the collection list for real (``cached=False``) or reused a recent
negative hint written by such a real walk (``cached=True``).  It therefore
means "this chapter entry is unavailable/unconfirmed for now", not "the
device or the navigation layer is broken".

The controller must keep the chapter pending and continue with other groups;
it must never feed this expected, chapter-local outcome into the global
consecutive-navigation circuit breaker.
"""
from __future__ import annotations

import math


def _minutes(age_seconds):
    """Whole minutes for the cached message; never below one."""
    try:
        age = float(age_seconds)
    except (TypeError, ValueError, OverflowError):
        return 1
    if not math.isfinite(age):
        return 1
    return max(1, int(max(0.0, age) // 60))


class ChapterEntryUnavailable(RuntimeError):
    """A chapter entry could not be confirmed without blaming navigation.

    ``base`` is the group whose entry lookup was attempted (for a nested group
    such as ``AD-EX`` this is normally ``AD``), ``title`` is the displayed
    chapter title, and ``cached`` records whether this failure came from a
    real walk in this call or from a recent negative hint.
    """

    def __init__(self, base, title, *, cached=False, age_seconds=None):
        self.base = base
        self.title = title
        self.cached = bool(cached)
        self.age_seconds = age_seconds
        if self.cached:
            message = (f'未找到章节入口：{title}'
                       f'（{_minutes(age_seconds)} 分钟前翻查未找到，本次不重复翻查）')
        else:
            message = f'未找到章节入口：{title}（本次已实际翻查，仍保持待确认）'
        super().__init__(message)
