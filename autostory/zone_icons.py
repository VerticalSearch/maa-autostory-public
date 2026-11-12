"""Verified icon-only zone selectors for the NL and MN story maps.

Candidate module for flow0935. It is deliberately small and side-effect free:
it imports no ``autostory`` code, performs no device/vision calls and never
writes to disk. The caller must already have proved the current map base from
two exact stage codes; this module only locates one of three known selector
icons and fails closed (returns ``None``) on any doubt.

The target is never inferred from an icon alone. A caller-supplied ``observed``
set describes the map groups that were positively read through exact two-code
verification. For NL targets the accepted source groups are NL, NL-TR, NL-EX
and NL-S (all share base NL); for MN-EX the accepted source groups are MN and
MN-TR. A target already present in ``observed`` is rejected, as are mixed or
foreign bases, unsupported targets, malformed images, missing/corrupt assets,
weak matches and two distinct icon peaks. There is no guessed fallback
coordinate.

Templates are extracted from the retained 1280x720 evidence frames by
``extract_zone_icon_assets.py``. Matching is normalized correlation over a
restricted lower-right ROI, with a fixed >= 0.92 threshold.
"""
from pathlib import Path

import cv2
import numpy as np

THRESHOLD = 0.92
# Lower-right selector strip. Kept narrow so stage labels and map art cannot
# contribute; wide enough for the leftmost NL EX button (starts at x=1034).
ROI = (1010, 580, 1260, 690)
FRAME_SHAPE = (720, 1280, 3)

_SPECS = {
    # Crops from the retained frames, in (x, y, width, height) order:
    #   zone-nl-ex.png (1034, 610, 90, 62)
    #   zone-nl-s.png  (1153, 604, 75, 74)
    #   zone-mn-ex.png (1165, 583, 85, 91)
    "NL-EX": {
        "asset": "zone-nl-ex.png",
        "text": "NL EX 图标",
        "sources": frozenset({"NL", "NL-TR", "NL-EX", "NL-S"}),
    },
    "NL-S": {
        "asset": "zone-nl-s.png",
        "text": "NL S 图标",
        "sources": frozenset({"NL", "NL-TR", "NL-EX", "NL-S"}),
    },
    "MN-EX": {
        "asset": "zone-mn-ex.png",
        "text": "MN EX 图标",
        "sources": frozenset({"MN", "MN-TR"}),
    },
    "DV-EX": {
        "asset": "zone-dv-ex.png",
        "text": "DV EX 图标",
        "sources": frozenset({"DV", "DV-TR", "DV-S"}),
    },
}
SUPPORTED_TARGETS = tuple(_SPECS)


def _valid_image(image):
    return (
        isinstance(image, np.ndarray)
        and image.dtype == np.uint8
        and image.ndim == 3
        and image.shape == FRAME_SHAPE
    )


def _load_template(asset_root, asset_name):
    """Return a BGR template or None for any missing/malformed/low-detail asset."""
    try:
        path = Path(asset_root) / asset_name
        if not path.is_file():
            return None
        raw = np.fromfile(str(path), dtype=np.uint8)
    except (TypeError, ValueError, OSError):
        return None
    if raw.size == 0:
        return None
    try:
        template = cv2.imdecode(raw, cv2.IMREAD_COLOR)
    except cv2.error:
        return None
    if (
        template is None
        or template.dtype != np.uint8
        or template.ndim != 3
        or template.shape[2] != 3
    ):
        return None
    height, width = template.shape[:2]
    if height < 8 or width < 8:
        return None
    if height > ROI[3] - ROI[1] or width > ROI[2] - ROI[0]:
        return None
    gray = cv2.cvtColor(template, cv2.COLOR_BGR2GRAY)
    if not np.isfinite(gray).all() or float(gray.std()) < 2.0:
        return None
    return template


def _distinct_peaks(scores, template_height, template_width):
    """Thresholded correlation peaks, merging adjacent pixels of one match.

    A response blob is one candidate regardless of how many pixels exceed the
    threshold. Peaks closer than half the smaller template dimension are merged
    as the same button; genuine different button locations stay independent.
    """
    scores = np.nan_to_num(scores, nan=-1.0, posinf=-1.0, neginf=-1.0)
    mask = (scores >= THRESHOLD).astype(np.uint8)
    if not mask.any():
        return []
    count, labels, _, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    raw = []
    for label in range(1, count):
        ys, xs = np.nonzero(labels == label)
        if xs.size == 0:
            continue
        best = int(np.argmax(scores[ys, xs]))
        raw.append((float(scores[ys[best], xs[best]]), int(xs[best]), int(ys[best])))
    raw.sort(key=lambda peak: (-peak[0], peak[2], peak[1]))

    min_separation = max(8.0, 0.5 * min(template_height, template_width))
    peaks = []
    for candidate in raw:
        if all(
            (candidate[1] - kept[1]) ** 2 + (candidate[2] - kept[2]) ** 2
            >= min_separation ** 2
            for kept in peaks
        ):
            peaks.append(candidate)
    return peaks


def match_zone_icon(image, prefix, observed, asset_root):
    """Locate one supported icon selector in a retained-layout frame.

    Parameters
    ----------
    image : numpy.ndarray
        BGR uint8 frame, exactly 1280x720.
    prefix : str
        One of ``NL-EX``, ``NL-S``, ``MN-EX`` or ``DV-EX``.
    observed : set[str]
        Non-empty set of map groups already confirmed by two exact stage codes.
    asset_root : str | os.PathLike
        Directory containing the selector PNG templates.

    Returns
    -------
    dict | None
        ``{'x', 'y', 'text', 'score'}`` for exactly one strong, unique match;
        ``None`` for every rejected or ambiguous case.
    """
    spec = _SPECS.get(prefix)
    if spec is None:
        return None
    if not _valid_image(image):
        return None
    if not isinstance(observed, (set, frozenset)) or not observed:
        return None
    if any(not isinstance(group, str) for group in observed):
        return None
    if not observed.issubset(spec["sources"]):
        return None
    # Never re-select a zone the caller has already confirmed on this map.
    if prefix in observed:
        return None

    template = _load_template(asset_root, spec["asset"])
    if template is None:
        return None

    x0, y0, x1, y1 = ROI
    roi = image[y0:y1, x0:x1]
    if roi.shape[0] < template.shape[0] or roi.shape[1] < template.shape[1]:
        return None
    try:
        gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
        template_gray = cv2.cvtColor(template, cv2.COLOR_BGR2GRAY)
        scores = cv2.matchTemplate(gray, template_gray, cv2.TM_CCOEFF_NORMED)
    except cv2.error:
        return None
    peaks = _distinct_peaks(scores, template.shape[0], template.shape[1])
    if len(peaks) != 1:
        return None
    score, px, py = peaks[0]
    if not np.isfinite(score) or score < THRESHOLD:
        return None
    return {
        "x": float(x0 + px + template.shape[1] / 2.0),
        "y": float(y0 + py + template.shape[0] / 2.0),
        "text": spec["text"],
        "score": float(score),
    }
