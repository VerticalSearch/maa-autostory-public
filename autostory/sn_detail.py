"""Pure recognizer and motion-anchor selector for the evidenced SN detail panel.

Scope
-----
This module recognizes only the *normal* Stultifera Navis (SN) lower stage
detail panel retained at 2026-11-03 14:13 in the live 0.9.38 run:

* a coherent exact ``OPERATION`` + ``SN-<digits>`` header entirely inside the
  lower-left header box;
* the exact bottom-right action ``开始行动``;
* the exact bottom-right controls ``剧情`` and ``代理指挥``;
* optional ``开始演习`` (present on the retained normal panel).

It rejects ordinary stage details, other maps, a lone start button, SN-TR
headers, duplicate/ambiguous targets, low confidence, invalid geometry,
settlement screens, purchase/confirmation dialogs and paused battles.  There is
no retained SN raid detail frame in this session, so ``开始突袭`` and a
practice-only panel (only ``开始演习``) fail closed rather than being guessed.

Purity and no clicks
--------------------
This module is pure: it imports only the standard library, performs no OCR,
device, image, filesystem or process I/O, holds no mutable state, and exposes
no tap/click point.  ``classify`` returns text/control boxes only.

Why tight text/control boxes, not a panel or whole backdrop
-----------------------------------------------------------
The retained four frames show the lower panel's decorative border/glow moving
while the exact text/control glyph boxes are stationary (six-pair
``map_motion`` translations <= 0.11 px).  A broad map backdrop would average
small motion away; these tight boxes reject real 1/2/5 px shifts, opposite
motion and 1 px drift under the unchanged ``autostory.motion`` thresholds.

Integration contract
--------------------
Use the returned ROIs with the unchanged motion gate::

    evidence = classify(first_texts, code=code)
    if evidence.ok:
        device.invalidate_frame(evidence.rois, kind=evidence.settle_kind)
        image = device.stable_shot(...)          # settle(kind='map')
        fresh = navigation_texts(vision, image)
        evidence = revalidate(evidence, fresh, code=code)
        if not evidence.ok:
            raise RuntimeError(evidence.reason)

``settle_kind`` is always ``"map"``: every selected ROI is a textured text or
control box that must pass ``map_motion`` against both the previous frame and
the fixed anchor.  The caller must not fall back to ``kind="fixed"`` or a
whole-frame settle for this page, and must not accept one stable pair.
"""

from __future__ import annotations

import math
import numbers
import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass

__all__ = [
    "Anchor",
    "Recognition",
    "classify",
    "revalidate",
    "settle_rois",
    "HEADER_BOUNDS",
    "CONTROL_BOUNDS",
    "ACTION_BOUNDS",
    "PRACTICE_BOUNDS",
    "MIN_SCORE",
]

SCREEN_WIDTH = 1280.0
SCREEN_HEIGHT = 720.0

# Exact-match OCR confidence floor.  Retained header/action/control scores are
# >= 0.954; a single low-confidence copy is never enough to select a page.
MIN_SCORE = 0.90

# Evidenced geometry at 1280x720.  A box must lie entirely inside its bound.
HEADER_BOUNDS = (60.0, 515.0, 360.0, 620.0)      # lower-left OPERATION + code
CONTROL_BOUNDS = (990.0, 550.0, 1260.0, 610.0)   # 剧情 / 代理指挥
ACTION_BOUNDS = (1120.0, 610.0, 1260.0, 690.0)   # 开始行动
PRACTICE_BOUNDS = (950.0, 610.0, 1110.0, 690.0)  # 开始演习

HEADER_PREFIX = "OPERATION"
ACTION_LABEL = "\u5f00\u59cb\u884c\u52a8"        # 开始行动
PRACTICE_LABEL = "\u5f00\u59cb\u6f14\u4e60"      # 开始演习
RAID_LABEL = "\u5f00\u59cb\u7a81\u88ad"          # 开始突袭
ALT_ACTION_LABEL = "\u5f00\u59cb\u4f5c\u6218"    # 开始作战
PLOT_LABEL = "\u5267\u60c5"                      # 剧情
AUTO_LABEL = "\u4ee3\u7406\u6307\u6325"          # 代理指挥

# Settlement screens must never be read as a detail panel.
_SETTLEMENT_MARKERS = (
    "\u884c\u52a8\u7ed3\u675f",   # 行动结束
    "\u6f14\u4e60\u7ed3\u675f",   # 演习结束
    "\u4efb\u52a1\u5931\u8d25",   # 任务失败
    "\u884c\u52a8\u5931\u8d25",   # 行动失败
    "\u6f14\u4e60\u5931\u8d25",   # 演习失败
    "MISSIONRESULTS",
    "MISSIONFAILED",
)

# Purchase, confirmation and paused-battle overlays.
_OVERLAY_MARKERS = (
    "\u8d2d\u4e70",               # 购买
    "\u91c7\u8d2d",               # 采购
    "\u5151\u6362",               # 兑换
    "\u6e90\u77f3",               # 源石
    "\u5408\u6210\u7389",         # 合成玉
    "\u82b1\u8d39",               # 花费
    "\u662f\u5426\u82b1\u8d39",   # 是否花费
    "\u786e\u8ba4",               # 确认
    "\u786e\u5b9a",               # 确定
    "\u53d6\u6d88",               # 取消
    "\u653e\u5f03\u884c\u52a8",   # 放弃行动
    "\u7ee7\u7eed\u884c\u52a8",   # 继续行动
    "\u6682\u505c",               # 暂停
)

# OCR punctuation folded before exact label comparison.  ASCII '-' is kept so
# a stage code such as SN-1 is never turned into SN1.
_PUNCTUATION = frozenset(
    " \t\r\n\u3000.,;:!?，。；：！？、…·•—–_/\\|()[]{}<>《》〈〉「」『』【】\"'“”‘’"
)

_ANCHOR_ORDER = ("header", "action", "plot", "auto_deploy", "practice")


@dataclass(frozen=True, slots=True)
class Anchor:
    """One selected exact text/control box.

    ``box`` is ``(x1, y1, x2, y2)`` in 1280x720 coordinates.  It is evidence
    only; this module never turns it into an input coordinate.
    """

    name: str
    text: str
    box: tuple[float, float, float, float]
    score: float

    @property
    def roi(self) -> tuple[int, int, int, int]:
        x1, y1, x2, y2 = self.box
        left, top = int(round(x1)), int(round(y1))
        right, bottom = int(round(x2)), int(round(y2))
        return (left, top, right - left, bottom - top)


@dataclass(frozen=True, slots=True)
class Recognition:
    """Result of one pure classification.  ``ok=False`` always fails closed."""

    ok: bool
    reason: str = ""
    code: str | None = None
    mode: str | None = None
    anchors: tuple[Anchor, ...] = ()

    @property
    def rois(self) -> tuple[tuple[int, int, int, int], ...]:
        return tuple(anchor.roi for anchor in self.anchors)

    @property
    def settle_kind(self) -> str:
        """The unchanged motion gate kind required for these text anchors."""
        return "map"


@dataclass(frozen=True, slots=True)
class _Entry:
    text: str
    box: tuple[float, float, float, float]
    score: float


def _compact(value: str) -> str:
    """Fold OCR spacing/punctuation while preserving code hyphens."""
    text = unicodedata.normalize("NFKC", value)
    return "".join(ch for ch in text if not ch.isspace() and ch not in _PUNCTUATION)


def _number(value):
    if isinstance(value, bool) or not isinstance(value, numbers.Real):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _box(value):
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    values = tuple(_number(v) for v in value)
    if any(v is None for v in values):
        return None
    x1, y1, x2, y2 = values
    if not (0.0 <= x1 < x2 <= SCREEN_WIDTH and 0.0 <= y1 < y2 <= SCREEN_HEIGHT):
        return None
    return (x1, y1, x2, y2)


def _inside(box, bounds) -> bool:
    left, top, right, bottom = bounds
    x1, y1, x2, y2 = box
    return left <= x1 and x2 <= right and top <= y1 and y2 <= bottom


def _entries(texts) -> tuple[_Entry, ...]:
    if texts is None or isinstance(texts, (str, bytes, bytearray, Mapping)):
        return ()
    try:
        iterator = iter(texts)
    except TypeError:
        return ()
    entries = []
    for item in iterator:
        if not isinstance(item, Mapping):
            continue
        text = item.get("text")
        if not isinstance(text, str) or not text:
            continue
        score = _number(item.get("score"))
        box = _box(item.get("box"))
        if score is None or score < MIN_SCORE or box is None:
            continue
        entries.append(_Entry(text, box, score))
    return tuple(entries)


def _parse_merged_header(text: str):
    """Return ``SN-<digits>`` for one coherent OPERATION+code OCR row."""
    compact = _compact(text).upper()
    if not compact.startswith(HEADER_PREFIX):
        return None
    rest = compact[len(HEADER_PREFIX):].lstrip("-")
    return rest if re.fullmatch(r"SN-\d+", rest) else None


def _coherent(first, second) -> bool:
    """Two split OCR rows must sit on one lower-left header line."""
    first_y = (first[1] + first[3]) / 2.0
    second_y = (second[1] + second[3]) / 2.0
    if abs(first_y - second_y) > 8.0:
        return False
    gap = max(first[0], second[0]) - min(first[2], second[2])
    return gap <= 12.0


def _union(first, second):
    return (
        min(first[0], second[0]),
        min(first[1], second[1]),
        max(first[2], second[2]),
        max(first[3], second[3]),
    )


def _header(entries):
    """Return ``(code, box, text, score)`` for one unique coherent header."""
    inside = [entry for entry in entries if _inside(entry.box, HEADER_BOUNDS)]
    merged = []
    for entry in inside:
        code = _parse_merged_header(entry.text)
        if code is not None:
            merged.append((code, entry))
    operations = [
        entry for entry in inside
        if _compact(entry.text).upper() == HEADER_PREFIX
    ]
    codes = [
        entry for entry in inside
        if re.fullmatch(r"SN-\d+", _compact(entry.text).upper())
    ]
    if len(operations) > 1 or len(codes) > 1:
        return None
    if merged:
        # A merged row plus a split duplicate is ambiguous OCR, not two pages.
        if len(merged) != 1 or operations or codes:
            return None
        code, entry = merged[0]
        return code, entry.box, entry.text, entry.score
    if len(operations) == 1 and len(codes) == 1 and _coherent(operations[0].box, codes[0].box):
        code = _compact(codes[0].text).upper()
        return (
            code,
            _union(operations[0].box, codes[0].box),
            operations[0].text + " " + codes[0].text,
            min(operations[0].score, codes[0].score),
        )
    return None


def _exact(entries, label, bounds):
    wanted = _compact(label).upper()
    return tuple(
        entry for entry in entries
        if _compact(entry.text).upper() == wanted and _inside(entry.box, bounds)
    )


def _no(reason: str) -> Recognition:
    return Recognition(False, reason)


def classify(texts, code=None) -> Recognition:
    """Classify one fresh OCR frame as the evidenced SN normal detail panel.

    ``texts`` is an iterable of mappings with ``text``/``score``/``box``.
    ``code`` optionally requires an exact ``SN-<digits>`` target.  The function
    never mutates ``texts`` and never returns an input coordinate.
    """
    expected = None
    if code is not None:
        if not isinstance(code, str):
            return _no("expected code must be a string")
        expected = code.strip().upper()
        if not re.fullmatch(r"SN-\d+", expected):
            return _no("expected code is not a strict SN-<digits> code")

    entries = _entries(texts)
    if not entries:
        return _no("no valid OCR entries")

    words = "".join(_compact(entry.text).upper() for entry in entries)
    for marker in _SETTLEMENT_MARKERS:
        if marker.upper() in words:
            return _no("settlement overlay visible")
    for marker in _OVERLAY_MARKERS:
        if marker.upper() in words:
            return _no("purchase/confirmation/pause overlay visible")

    header = _header(entries)
    if header is None:
        return _no("no coherent lower-left OPERATION+SN-<digits> header")
    header_code, header_box, header_text, header_score = header
    if expected is not None and header_code != expected:
        return _no("header code %s does not match expected %s" % (header_code, expected))

    # No retained SN raid frame exists, and 开始作战 is not the evidenced SN
    # normal action.  Refuse both rather than infer a different page.
    if _exact(entries, RAID_LABEL, ACTION_BOUNDS) or _exact(entries, RAID_LABEL, PRACTICE_BOUNDS):
        return _no("SN raid detail is not evidenced; refusing to guess")
    if _exact(entries, ALT_ACTION_LABEL, ACTION_BOUNDS) or _exact(entries, ALT_ACTION_LABEL, PRACTICE_BOUNDS):
        return _no("SN alternate start action is not evidenced")

    action = _exact(entries, ACTION_LABEL, ACTION_BOUNDS)
    if len(action) != 1:
        return _no("bottom-right %s is missing or not unique" % ACTION_LABEL)
    plot = _exact(entries, PLOT_LABEL, CONTROL_BOUNDS)
    if len(plot) != 1:
        return _no("bottom-right %s is missing or not unique" % PLOT_LABEL)
    auto = _exact(entries, AUTO_LABEL, CONTROL_BOUNDS)
    if len(auto) != 1:
        return _no("bottom-right %s is missing or not unique" % AUTO_LABEL)
    practice = _exact(entries, PRACTICE_LABEL, PRACTICE_BOUNDS)
    if len(practice) > 1:
        return _no("bottom-right %s is not unique" % PRACTICE_LABEL)

    anchors = [
        Anchor("header", header_text, header_box, header_score),
        Anchor("action", action[0].text, action[0].box, action[0].score),
        Anchor("plot", plot[0].text, plot[0].box, plot[0].score),
        Anchor("auto_deploy", auto[0].text, auto[0].box, auto[0].score),
    ]
    if practice:
        anchors.append(Anchor("practice", practice[0].text, practice[0].box, practice[0].score))
    anchors.sort(key=lambda anchor: _ANCHOR_ORDER.index(anchor.name))
    if any(_box(anchor.box) is None for anchor in anchors):
        return _no("invalid anchor box")
    return Recognition(True, "", header_code, "normal", tuple(anchors))


def revalidate(previous, texts, code=None) -> Recognition:
    """Re-classify fresh post-settle OCR and require the same page identity.

    Returns the *fresh* recognition, never the previous anchors.  A changed
    code/mode or a page that no longer classifies fails closed.
    """
    if not isinstance(previous, Recognition) or not previous.ok:
        return _no("prior SN detail evidence is not valid")
    fresh = classify(texts, code)
    if not fresh.ok:
        return fresh
    if (fresh.code, fresh.mode) != (previous.code, previous.mode):
        return _no("SN detail page changed during observation")
    return fresh


def settle_rois(recognition) -> tuple[tuple[int, int, int, int], ...]:
    """Return the ``(x,y,w,h)`` ROIs for ``motion.settle(kind='map')``."""
    if not isinstance(recognition, Recognition) or not recognition.ok:
        return ()
    return recognition.rois
