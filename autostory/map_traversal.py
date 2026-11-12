"""Bounded, pure map-boundary detector for AutoStory 0.9.38 (candidate).

This module only decides whether two freshly observed map frames are a reliable
stationary pair.  It never opens a device, performs OCR, sleeps, logs, writes
files, produces click coordinates, or marks a stage complete.

Integration model
-----------------
The caller creates one ``PanBoundary`` per directional sweep and calls
``observe(image, texts)`` after each fresh frame/OCR observation.  The first
usable observation is a baseline and returns ``False``.  ``True`` is returned
only after ``confirmations`` consecutive reliable pairs.  Each pair must keep
every exact-label OCR centre within 0.85 px of both the immediately previous
observation and the fixed sweep anchor, and every tracked textured label region
must pass the original ``autostory.motion.map_signature`` / ``map_motion`` gate
against both previous and anchor.  OCR box edges are validated for geometry but
are not treated as motion evidence.

State stays bounded: the anchor labels/signatures, the last accepted
labels/signatures, fixed label ROIs, and a pair counter capped at
``confirmations``.  If a frame is invalid, ambiguous, covered by a blocking
overlay, has fewer than two exact known labels, changes its label set, or moves
a centre beyond the unchanged 0.85 px tolerance, the pair streak is reset and
``False`` is returned.  A changed-but-valid map is installed as a fresh
anchor/last frame while an invalid/overlay frame clears the evidence entirely;
no stale anchors survive.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Mapping

import cv2
import numpy as np

from autostory.motion import (
    TEXTURE_MIN_STD,
    map_motion,
    map_signature,
    region_texture,
)

# Map-label bounds from home_observation.navigation_regions; inclusive.
MAP_LEFT = 5
MAP_TOP = 90
MAP_RIGHT = 1274
MAP_BOTTOM = 610

# The original map_motion gate uses this same unchanged subpixel tolerance.
POSITION_TOLERANCE = 0.85

# At least two independently valid, textured label anchors are required.
MIN_LABELS = 2
MIN_TEXTURED_REGIONS = 2

# home_observation pads every accepted label box by four pixels.
LABEL_PAD = 4

# Existing sn_observation geometry allows OCR's centre to sit just outside its
# own box because polygon/box rounding differs by a pixel or two.
OCR_CENTRE_TOLERANCE = 2.0

# Exact catalogue matches only; this is the same OCR confidence floor used by
# home_observation.navigation_regions.
EXACT_SCORE = 0.9

_IMAGE_SHAPE = (720, 1280, 3)


def _norm_code(value):
    """Normalise whitespace/dashes only; never fuzzy-match a catalogue code."""
    if not isinstance(value, str):
        return None
    return re.sub(r"\s+", "", value).replace("\u2014", "-").replace("\u2013", "-").upper()


def _number(value):
    """Return a finite float for a real scalar, else ``None``."""
    if isinstance(value, bool) or not isinstance(
        value, (int, float, np.integer, np.floating)
    ):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _image_ok(image):
    """The candidate contract is one actual 1280x720 uint8 BGR frame."""
    return (
        isinstance(image, np.ndarray)
        and image.dtype == np.uint8
        and image.shape == _IMAGE_SHAPE
        and image.size > 0
    )


def _overlay(texts):
    """Reject detail/settlement/confirmation/purchase/pause overlays.

    This is a local, conservative copy of the words already used by the main
    navigation/home observation gates.  No image- or OCR-derived guess is
    made beyond the caller's fresh text rows.
    """
    parts = []
    for row in texts:
        if isinstance(row, Mapping):
            text = row.get("text", "")
            if isinstance(text, str):
                parts.append(_norm_code(text) or "")
    words = "".join(parts).upper()
    if not words:
        return False
    # Purchase/exchange dialog (home_observation.blocked_overlay) and common
    # single-button OCR variants.  These words are not map-label content.
    if any(word in words for word in ("购买", "兑换", "合成玉", "源石")):
        return True
    # Paused battle (home_observation.blocked_overlay).
    if "放弃行动" in words and "继续行动" in words:
        return True
    if any(word in words for word in ("暂停", "继续行动", "放弃行动")):
        return True
    # Settlement screens.
    if any(
        word in words
        for word in (
            "行动结束",
            "演习结束",
            "任务失败",
            "行动失败",
            "演习失败",
            "MISSIONFAILED",
            "MISSIONRESULTS",
        )
    ):
        return True
    # Stage detail panels.  Practice stages use 开始演习, the counterpart of
    # 开始行动, and must clear boundary evidence just like a normal detail panel.
    if any(
        word in words
        for word in ("开始行动", "开始作战", "开始突袭", "开始演习", "关卡详情")
    ):
        return True
    # Confirmation/modal controls.  A map label never consists of these words,
    # so rejecting them is the conservative choice when OCR misses the rest of
    # the dialog.
    if any(word in words for word in ("确认", "确定", "取消", "是否")):
        return True
    return False


class _Label:
    __slots__ = ("code", "box", "x", "y")

    def __init__(self, code, box, x, y):
        self.code = code
        self.box = box
        self.x = x
        self.y = y


def _overlaps(first, second):
    ax1, ay1, ax2, ay2 = first
    bx1, by1, bx2, by2 = second
    return min(ax2, bx2) > max(ax1, bx1) and min(ay2, by2) > max(ay1, by1)


class PanBoundary:
    """Bounded stationary-pair evidence for one directional map sweep.

    Parameters
    ----------
    known_codes:
        Exact catalogue codes supplied by the caller.  Codes outside this set
        are ignored; there is no global catalogue lookup or tracking.
    confirmations:
        Number of consecutive stationary pairs required.  The default of two
        therefore needs three observations (one baseline plus two pairs).
    """

    __slots__ = (
        "_known",
        "_confirmations",
        "_baseline",
        "_last",
        "_rois",
        "_signatures",
        "_last_signatures",
        "_pairs",
    )

    def __init__(self, known_codes, confirmations=2):
        if isinstance(known_codes, str) or not isinstance(known_codes, Iterable):
            raise TypeError("known_codes must be an iterable of exact code strings")
        known = set()
        for code in known_codes:
            if not isinstance(code, str):
                raise TypeError("known_codes must contain strings")
            normalised = _norm_code(code)
            if normalised:
                known.add(normalised)
        if isinstance(confirmations, bool):
            raise TypeError("confirmations must be an integer")
        try:
            confirmations = int(confirmations)
        except (TypeError, ValueError, OverflowError) as exc:
            raise TypeError("confirmations must be an integer") from exc
        if confirmations < 1:
            raise ValueError("confirmations must be at least 1")
        self._known = frozenset(known)
        self._confirmations = confirmations
        self._baseline = None
        self._last = None
        self._rois = {}
        self._signatures = {}
        self._last_signatures = {}
        self._pairs = 0

    def reset(self):
        """Drop all evidence so the next valid observation is a new baseline."""
        self._baseline = None
        self._last = None
        self._rois = {}
        self._signatures = {}
        self._last_signatures = {}
        self._pairs = 0

    def observe(self, image, texts):
        """Return ``True`` only for confirmed stationary map-pair evidence."""
        observation = self._parse(image, texts)
        if observation is None:
            self.reset()
            return False
        if self._baseline is None:
            self._store(image, observation)
            return False
        if not self._same_page(observation):
            self._store(image, observation)
            return False
        current_signatures = self._pixel_stationary(image)
        if current_signatures is None:
            self._store(image, observation)
            return False
        self._last = {label.code: label for label in observation}
        self._last_signatures = current_signatures
        self._pairs += 1
        if self._pairs >= self._confirmations:
            self._pairs = self._confirmations
            return True
        return False

    def _parse(self, image, texts):
        """Extract an exact, unambiguous label set or reject the observation."""
        if not _image_ok(image) or not isinstance(texts, (list, tuple)):
            return None
        if _overlay(texts):
            return None
        found = {}
        for row in texts:
            if not isinstance(row, Mapping):
                continue
            code = _norm_code(row.get("text", ""))
            if code is None or code not in self._known:
                continue
            score = _number(row.get("score"))
            if score is None:
                # A malformed confidence for an exact code is ambiguous.
                return None
            if score < EXACT_SCORE:
                continue
            box = row.get("box")
            if not isinstance(box, (list, tuple)) or len(box) != 4:
                return None
            coords = tuple(_number(value) for value in box)
            if any(value is None for value in coords):
                return None
            x1, y1, x2, y2 = coords
            if not (x1 < x2 and y1 < y2):
                return None
            if not (
                MAP_LEFT <= x1
                and x2 <= MAP_RIGHT
                and MAP_TOP <= y1
                and y2 <= MAP_BOTTOM
            ):
                return None
            x = _number(row.get("x"))
            y = _number(row.get("y"))
            if x is None or y is None:
                return None
            # OCR's centre must describe the same box.  Match the existing
            # sn_observation antialias allowance rather than a stricter rule.
            if not (
                x1 - OCR_CENTRE_TOLERANCE <= x <= x2 + OCR_CENTRE_TOLERANCE
                and y1 - OCR_CENTRE_TOLERANCE <= y <= y2 + OCR_CENTRE_TOLERANCE
            ):
                return None
            if code in found:
                # Two exact matches for one code are ambiguous even if equal.
                return None
            found[code] = _Label(code, (x1, y1, x2, y2), x, y)
        if len(found) < MIN_LABELS:
            return None
        labels = tuple(found[code] for code in sorted(found))
        for index, first in enumerate(labels):
            for second in labels[index + 1 :]:
                if _overlaps(first.box, second.box):
                    return None
        return labels

    def _roi(self, label):
        """Padded native-resolution ROI, entirely inside the input frame."""
        x1, y1, x2, y2 = label.box
        left = math.floor(x1) - LABEL_PAD
        top = math.floor(y1) - LABEL_PAD
        right = math.ceil(x2) + LABEL_PAD
        bottom = math.ceil(y2) + LABEL_PAD
        if left < 0 or top < 0 or right > _IMAGE_SHAPE[1] or bottom > _IMAGE_SHAPE[0]:
            return None
        if right <= left or bottom <= top:
            return None
        return (left, top, right - left, bottom - top)

    def _store(self, image, observation):
        """Install one valid observation as the anchor and current pair frame."""
        rois = {}
        signatures = {}
        for label in observation:
            roi = self._roi(label)
            if roi is None:
                self.reset()
                return False
            try:
                texture = region_texture(image, roi)
                signature = map_signature(image, roi)
            except (cv2.error, TypeError, ValueError):
                self.reset()
                return False
            if (
                texture is not None
                and math.isfinite(texture)
                and texture >= TEXTURE_MIN_STD
            ):
                rois[label.code] = roi
                signatures[label.code] = signature
        if len(rois) < MIN_TEXTURED_REGIONS:
            self.reset()
            return False
        self._baseline = {label.code: label for label in observation}
        self._last = {label.code: label for label in observation}
        self._rois = rois
        self._signatures = signatures
        self._last_signatures = dict(signatures)
        self._pairs = 0
        return True

    def _same_page(self, observation):
        """Require the identical label set and stable OCR centres.

        Box edges are deliberately not compared: OCR polygon/box rounding can
        move an edge by a pixel while the centre and pixels are stationary.
        Current boxes are still validated for geometry, bounds, and overlap in
        ``_parse``.
        """
        if self._baseline is None or self._last is None:
            return False
        codes = {label.code for label in observation}
        if set(self._baseline) != codes or set(self._last) != codes:
            return False
        for label in observation:
            for reference in (self._baseline[label.code], self._last[label.code]):
                if abs(label.x - reference.x) > POSITION_TOLERANCE:
                    return False
                if abs(label.y - reference.y) > POSITION_TOLERANCE:
                    return False
        return True

    def _pixel_stationary(self, image):
        """Run the original map gate on every tracked textured label ROI.

        The comparison mirrors ``motion.settle(kind='map')``: each current
        region must be stable both against the immediately previous frame and
        against the fixed anchor.  No moving tracked label may be outvoted by
        other stationary labels.  Returns the current signatures on success and
        ``None`` on any failure.
        """
        if len(self._rois) < MIN_TEXTURED_REGIONS:
            return None
        current = {}
        try:
            for code, roi in self._rois.items():
                current[code] = map_signature(image, roi)
            for code in self._rois:
                previous = self._last_signatures.get(code)
                anchor = self._signatures.get(code)
                if previous is None or anchor is None:
                    return None
                local = map_motion(previous, current[code])
                drift = map_motion(anchor, current[code])
                if not (local.get("textured") and local.get("stable")):
                    return None
                if not (drift.get("textured") and drift.get("stable")):
                    return None
        except (cv2.error, TypeError, ValueError):
            return None
        return current
