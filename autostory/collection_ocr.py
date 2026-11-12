"""Bounded OCR of the 「乐章收录」 card grid.

The list is a four-column grid of decorative cards. Full-frame OCR loses many
card titles because the artwork and logo font are much larger than UI text, and
one crop per chapter title is far too expensive when a round knows ~40 titles.

This module OCRs four overlapping, resized column crops per screen. Every title
sharing a row is recognised by the same call, so the cost is bounded by the
grid (``MAX_CROPS``) rather than by the number of wanted titles.
"""
from __future__ import annotations

import math

CARD_X0 = 40
CARD_X1 = 1155
CARD_TOP = 70
CARD_BOTTOM = 712
# Both the one-pass index and chapter_candidates use the same geometry.
COLUMNS = ((40, 330), (315, 605), (590, 880), (865, 1155))
SCALE = 2
MAX_CROPS = len(COLUMNS)


def in_card(entry):
    """True when an OCR entry's centre lies inside the card area, not the tabs."""
    if not isinstance(entry, dict):
        return False
    try:
        x = float(entry.get('x'))
        y = float(entry.get('y'))
    except (TypeError, ValueError, OverflowError):
        return False
    if not (math.isfinite(x) and math.isfinite(y)):
        return False
    return CARD_X0 <= x <= CARD_X1 and CARD_TOP <= y <= CARD_BOTTOM


def columns(image, texts_zoom, check=None):
    """Yield OCR entries from exactly ``MAX_CROPS`` overlapping card columns.

    ``check`` is an optional zero-argument callable (normally ``check_stop``) run
    before each crop so a user stop propagates during this bounded pass.
    """
    rows = []
    for x0, x1 in COLUMNS:
        if check is not None:
            check()
        for entry in texts_zoom(image, (x0, CARD_TOP, x1 - x0, CARD_BOTTOM - CARD_TOP),
                                scale=SCALE):
            if in_card(entry):
                rows.append(entry)
    return rows


