"""Native Tk control window for the loopback-only MAA AutoStory server.

This module is a self-contained candidate integration unit.  It has no import
side effects and uses only the Python standard library.  Codex owns the
production entrypoint; this file is intended to be copied or imported as-is.

Public entrypoint::

    run_ui(url: str, root: pathlib.Path, smoke: bool = False) -> dict | None

``url`` is the loopback base URL (for example ``http://127.0.0.1:18741``).
``root`` is the installed AutoStory directory; it is used only to locate the
optional window/sidebar icons (``assets/shu.ico``, ``assets/app.ico`` and the
optional ``assets/shu.png`` portrait).  In normal mode the function blocks in
Tk's main loop and returns ``None`` when the control window closes.  Closing the
window never stops the server or the running worker.  In smoke mode the window
is withdrawn before it is populated, one bounded dashboard fetch is performed,
and a small result dictionary is returned.  Smoke failures raise so package
validation can see them.

HTTP requests run on two background threads: a GET-only dashboard poller and
an independent POST command/stop transport.  Results are handed to the Tk
thread through a queue drained by ``root.after``; neither worker calls Tk.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import queue
import re
import threading
import time
import tkinter as tk
import webbrowser
from collections import Counter
from datetime import datetime, timezone
from tkinter import font as tkfont
from tkinter import ttk
from urllib import error as urlerror
from urllib import request as urlrequest
from urllib.parse import urlencode, urlsplit

TITLE = "MAA 自动推图助手"
WINDOW_SIZE = "1100x760"
MIN_SIZE = (1050, 720)
POLL_SECONDS = 4.0
POLL_ACTIVE_SECONDS = 2.0
MAX_COVERAGE_ROWS = 200
PAGE_SIZE = MAX_COVERAGE_ROWS
DEFAULT_TIMEOUT = 5.0
MAX_RESPONSE_BYTES = 16 * 1024 * 1024
COMMAND_QUEUE_SIZE = 6

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
TOKEN_RE = re.compile(r"const\s+token\s*=\s*(['\"])([A-Za-z0-9._~+=-]{1,256})\1")
NATURAL_RE = re.compile(r"(\d+)")

ALLOWED_POST_ACTIONS = frozenset(
    {
        "refresh",
        "scan",
        "scan-current",
        "scan-all",
        "plan",
        "run",
        "run-all",
        "resume-all",
        "diagnose",
        "stop",
    }
)

STATUS_LABELS = {
    "idle": "空闲",
    "running": "运行中",
    "done": "本轮已完成",
    "error": "已暂停",
    "stopped": "已停止",
    "interrupted": "已中断",
    "paused": "已暂停",
}
ACTION_LABELS = {
    "refresh": "更新干员",
    "scan": "扫描分区",
    "scan-current": "扫描当前地图",
    "scan-all": "扫描全目录",
    "plan": "筛选作业",
    "run": "分区推图",
    "run-all": "全目录推图",
    "resume-all": "续跑全目录",
    "verify-raid": "突袭实机验收",
    "diagnose": "连接检查",
    "stop": "停止任务",
}
# New dashboard workflow phases. Unknown values are displayed verbatim.
WORKFLOW_LABELS = {
    "starting": "启动中",
    "refresh": "更新干员",
    "navigation": "导航中",
    "scan": "扫描进度",
    "planning": "筛选作业",
    "practice": "演习中",
    "formal": "正式作战",
    "verify": "结果核验",
    "verification": "结果核验",
    "running": "后台运行中",
    "idle": "等待开始",
    "done": "本轮完成",
    "interrupted": "已中断",
    "finished": "本轮完成",
    "stopped": "已停止",
    "error": "任务异常",
}
STATE_LABELS = {
    "cleared": "已完成目标难度",
    "partial": "已通关未三星",
    "uncleared": "未通关",
    "unknown": "状态待确认",
    "unseen": "未扫描到",
    "locked": "未解锁",
}
# Compact table labels; the selection detail keeps the full state wording.
TABLE_STATE_LABELS = {
    "cleared": "已通关",
    "partial": "未三星",
    "uncleared": "未通关",
    "unknown": "待确认",
    "unseen": "未扫描",
    "locked": "未解锁",
}
MODE_LABELS = {
    "normal": "普通",
    "raid": "突袭",
    "tough": "磨难 / 险地",
    "easy": "简单",
    "six_star": "六星",
}
MODE_ORDER = ("normal", "raid", "tough", "easy", "six_star")
STATE_FILTERS = (
    ("全部状态", "all"),
    ("已确认待通关", "pending"),
    ("未扫描", "unseen"),
    ("有阻塞原因", "blocked"),
    ("已通关", "cleared"),
)
MODE_FILTERS = (("全部难度", "all"),) + tuple(
    (MODE_LABELS[mode], mode) for mode in MODE_ORDER
)

# Design system: a dark blue-green operations sidebar, a light mist
# workspace and a single deep-teal accent.  Red is reserved for stop/error.
BG = "#F2F5F3"
CARD = "#FFFFFF"
BORDER = "#D8E2DE"
TEXT = "#213A38"
MUTED = "#5B706D"
ACCENT = "#237C71"
ACCENT_HOVER = "#2C9184"
ACCENT_DARK = "#1B635B"
DANGER = "#B4443C"
DANGER_HOVER = "#C7584F"
DANGER_DARK = "#8F342E"
SUCCESS = "#1F7A5C"
WARN = "#A66A1F"

SIDEBAR = "#173235"
SIDEBAR_PANEL = "#1F3F3A"
SIDEBAR_BORDER = "#2E4E48"
SIDEBAR_TEXT = "#E7F0EC"
SIDEBAR_MUTED = "#A7BDB7"
SAGE_ACTIVE = "#DCE9E1"
SAGE_ACTIVE_HOVER = "#E8F1EB"
FOCUS = "#7FB8AC"
ROW_ALT = "#F5F8F6"
SELECTION_BG = "#CFE3DB"
HEADING_BG = "#EDF2EF"
SOFT_SURFACE = "#EEF4F1"
SIDEBAR_WIDTH = 216
TREE_ROW_HEIGHT = 24
TREE_VISIBLE_ROWS = 12


class ApiError(RuntimeError):
    """A bounded, user-presentable HTTP/API failure."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(str(message))
        self.status = status


def normalize_base_url(url: str) -> str:
    """Validate and normalize a loopback HTTP base URL.

    Only loopback hosts are accepted because the AutoStory dashboard binds to
    127.0.0.1 and has no authentication beyond its per-process token.
    """

    if not isinstance(url, str):
        raise ValueError("后台地址必须是字符串")
    text = url.strip()
    if not text:
        raise ValueError("后台地址为空")
    parts = urlsplit(text)
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https"):
        raise ValueError("后台地址必须使用 http:// 或 https://")
    if not parts.hostname or parts.hostname not in LOOPBACK_HOSTS:
        raise ValueError("后台地址必须指向本机回环地址")
    try:
        parts.port
    except ValueError as exc:
        raise ValueError("后台端口无效") from exc
    if parts.username or parts.password:
        raise ValueError("后台地址不能包含账号信息")
    if parts.path not in ("", "/"):
        raise ValueError("后台地址不能包含额外路径")
    if parts.query or parts.fragment:
        raise ValueError("后台地址不能包含查询参数或片段")
    return f"{scheme}://{parts.netloc}"


def parse_token(html: str) -> str | None:
    """Extract the dashboard's ``const token='...'`` value."""

    if not isinstance(html, str):
        return None
    match = TOKEN_RE.search(html)
    return match.group(2) if match else None


def natural_key(value: object) -> list[object]:
    """Key for human/numeric ordering of profile names such as ``CW-EX``."""

    return [
        int(part) if part.isdigit() else part.casefold()
        for part in NATURAL_RE.split(str(value))
    ]


def as_list(value: object) -> list[str]:
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value if item is not None and str(item)]
    if isinstance(value, str):
        return [value] if value.strip() else []
    return []


def format_int(value: object) -> str:
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return "—" if value is None else str(value)


def pretty_time(value: object) -> str:
    text = str(value or "").strip()
    if len(text) >= 19 and text[4] == "-" and text[10] in ("T", " "):
        return f"{text[:10]} {text[11:19]}"
    return text or "—"


def clip_text(value: object, limit: int = 600) -> str:
    """Collapse whitespace and cap UI-bound text without touching the log."""

    text = " ".join(str(value or "").split())
    cap = max(1, int(limit))
    return text if len(text) <= cap else text[:cap] + "…"


def state_label(state: object) -> str:
    key = str(state or "").strip()
    return STATE_LABELS.get(key, key or "未知")


def mode_label(mode: object) -> str:
    key = str(mode or "").strip()
    return MODE_LABELS.get(key, key or "未知")


def status_label(state: object) -> str:
    key = str(state or "idle").strip()
    return STATUS_LABELS.get(key, key or "未知")


def action_label(action: object) -> str:
    key = str(action or "").strip()
    return ACTION_LABELS.get(key, key)


def phase_label(phase: object) -> str:
    key = str(phase or "").strip()
    return WORKFLOW_LABELS.get(key, key or "等待开始")


def _iso_datetime(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def format_age(value: object) -> str:
    """Human age of a snapshot timestamp; empty when the value is unusable."""

    parsed = _iso_datetime(value)
    if parsed is None:
        return ""
    try:
        seconds = (datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds()
    except (OverflowError, OSError, ValueError):
        return ""
    if seconds < 60:
        return "刚刚"
    minutes = int(seconds // 60)
    if minutes < 60:
        return f"{minutes} 分钟前"
    hours = minutes // 60
    if hours < 24:
        return f"{hours} 小时前"
    return f"{hours // 24} 天前"


def workflow_summary(state: object) -> tuple[str, str]:
    """Return ``(headline, detail)`` for the compact workflow strip.

    Missing workflow fields are tolerated; no percentage is invented when the
    server does not provide a total.
    """

    source = state if isinstance(state, dict) else {}
    workflow = source.get("workflow")
    status = source.get("status") if isinstance(source.get("status"), dict) else {}
    fallback = False
    if not isinstance(workflow, dict) or not workflow.get("phase"):
        # Old servers have no workflow object; derive a readable phase from
        # the legacy status/action without inventing completion counts.
        action = str(status.get("action") or "")
        phase = {
            "refresh": "refresh",
            "scan": "scan",
            "scan-current": "scan",
            "scan-all": "scan",
            "plan": "planning",
            "run": "running",
            "run-all": "running",
            "resume-all": "running",
            "diagnose": "verify",
        }.get(action, "")
        if not phase or str(status.get('state')) != 'running':
            phase = {
                "done": "finished",
                "stopped": "stopped",
                "interrupted": "stopped",
                "error": "error",
            }.get(str(status.get("state") or ""), "")
        workflow = {
            "phase": phase,
            "chapter": status.get("chapter") or "",
            "message": status.get("message") or status.get("error") or "",
        }
        fallback = True
    parts = [phase_label(workflow.get("phase"))]
    chapter = str(workflow.get("chapter") or "").strip()
    if chapter:
        parts.append("全目录" if chapter.upper() == "ALL" else f"第 {chapter} 章" if chapter.isdigit() else f"分区 {chapter}")
    code = str(workflow.get("code") or "").strip()
    if code:
        parts.append(code)
    mode = str(workflow.get("mode") or "").strip()
    if mode:
        parts.append(mode_label(mode))
    completed = workflow.get("completed")
    total = workflow.get("total")
    try:
        total_int = int(total) if total is not None else None
        completed_int = int(completed) if completed is not None else None
    except (TypeError, ValueError):
        total_int = completed_int = None
    if total_int is not None and total_int > 0 and completed_int is not None:
        parts.append(f"{max(0, completed_int)}/{total_int}")
    headline = " · ".join(part for part in parts if part)

    detail = clip_text(workflow.get("message"), 220)
    age = format_age(workflow.get("updated"))
    if age:
        detail = f"{detail} · 更新于{age}" if detail else f"更新于{age}"
    if not detail:
        detail = status_detail(status) if fallback else "等待后台上报当前阶段"
    return headline, detail


def campaign_summary(state: object) -> str:
    """Describe the remaining campaign budget without inventing percentages."""

    source = state if isinstance(state, dict) else {}
    campaign = source.get("campaign")
    if not isinstance(campaign, dict):
        return ""
    budget = campaign.get("budget")
    if not isinstance(budget, dict):
        budget = {}
    parts: list[str] = []
    sanity = budget.get("sanity")
    limit = budget.get("limit")
    count = budget.get("count")
    if sanity is not None:
        parts.append(f"剩余预算 {format_int(sanity)} 理智")
    if count is not None:
        parts.append(f"正式作战 {format_int(count)}/{format_int(limit)} 次" if limit is not None else f"正式作战 {format_int(count)} 次")
    group = str(campaign.get("active_group") or "").strip()
    if group:
        parts.append(group)
    state_text = str(campaign.get("state") or "").strip()
    if state_text and state_text not in ("running", "idle"):
        parts.append(phase_label(state_text))
    return "全目录预算 · " + " · ".join(parts) if parts else ""


def coverage_mode_counts(rows: object) -> Counter:
    if not isinstance(rows, list):
        return Counter()
    return Counter(
        str(row.get("mode") or "")
        for row in rows
        if isinstance(row, dict) and str(row.get("mode") or "")
    )


def coverage_signature(rows: object) -> str:
    """Stable digest used to avoid rebuilding the table on unchanged polls."""

    try:
        payload = json.dumps(
            rows, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()
    except Exception:
        return repr(rows)


def coverage_summary(coverage: object) -> str:
    if not isinstance(coverage, dict):
        return "尚未读取关卡目录"
    rows = coverage.get("rows")
    if not isinstance(rows, list):
        rows = []
    counts = coverage_mode_counts(rows)
    variants = coverage.get("stage_variants", len(rows))
    chapters = coverage.get("chapter_count", 0)
    battles = coverage.get("battle_codes", 0)
    parts = [
        f"{MODE_LABELS.get(mode, mode)} {format_int(counts.get(mode, 0))}"
        for mode in MODE_ORDER
        if counts.get(mode)
    ]
    unknown = sorted(set(counts) - set(MODE_ORDER))
    parts.extend(f"{mode} {format_int(counts[mode])}" for mode in unknown)
    head = (
        f"{format_int(variants)} 条难度记录 · "
        f"{format_int(chapters)} 个章节 · "
        f"{format_int(battles)} 个战斗关卡"
    )
    return f"{head} · {' / '.join(parts)}" if parts else head


def status_detail(status: object, running: bool = False) -> str:
    if not isinstance(status, dict):
        return "尚未获取后台状态"
    state = str(status.get("state") or "idle")
    action = action_label(status.get("action"))
    chapter = str(status.get("chapter") or "")
    route = " · ".join(part for part in (action, chapter) if part)
    message = clip_text(
        status.get("message") or status.get("error") or status.get("reason") or "",
        240,
    )

    if message:
        return f"{route}：{message}" if route else message
    if state == "error":
        return "后台任务失败；请查看运行记录后重试。"
    if state == "stopped":
        return "任务已停止；可点击“继续上轮全目录”沿用上轮剩余预算。"
    if state == "interrupted":
        return "任务已中断；请检查模拟器连接后继续上轮。"
    if state == "running":
        return f"{route} · 正在执行" if route else "后台任务正在执行"
    if state == "done":
        return f"{route} · 本轮已完成" if route else "本轮任务已完成"
    if state == "idle":
        return "暂无后台任务；点击按钮后才会开始。"
    return route or "—"


def format_budget(settings: object) -> str:
    source = settings if isinstance(settings, dict) else {}
    battles = source.get("max_battles_per_run", 3)
    sanity = source.get("max_sanity_per_run", 60)
    formation = source.get("formation_index", 4)
    device = source.get("device")
    parts = [
        f"每轮最多 {battles} 次正式作战、{sanity} 理智（全目录共用）",
        f"第 {formation} 编队",
    ]
    if device:
        parts.append(f"设备 {device}")
    return " · ".join(parts)


def filter_coverage(
    rows: object,
    query: str = "",
    state_filter: str = "all",
    mode_filter: str = "all",
    limit: int = MAX_COVERAGE_ROWS,
) -> tuple[list[dict], int]:
    """Return ``(visible_rows, total_matching)`` with a hard display cap."""

    if not isinstance(rows, list):
        return [], 0
    needle = str(query or "").strip().casefold()
    wanted_state = state_filter or "all"
    wanted_mode = mode_filter or "all"
    matched: list[dict] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        state = str(row.get("state") or "unseen")
        mode = str(row.get("mode") or "")
        blockers = as_list(row.get("blockers"))
        if wanted_state == "pending" and state not in ("uncleared", "partial"):
            continue
        if wanted_state == "unseen" and state != "unseen":
            continue
        if wanted_state == "blocked" and not blockers:
            continue
        if wanted_state == "cleared" and state != "cleared":
            continue
        if wanted_state not in ("all", "pending", "unseen", "blocked", "cleared"):
            continue
        if wanted_mode != "all" and mode != wanted_mode:
            continue
        if needle:
            haystack = " ".join(
                [
                    str(row.get("code") or ""),
                    str(row.get("name") or ""),
                    str(row.get("title") or ""),
                    str(row.get("group") or ""),
                    str(row.get("zone") or ""),
                    str(row.get("kind") or ""),
                    state_label(state),
                    mode_label(mode),
                    " ".join(blockers),
                ]
            ).casefold()
            if needle not in haystack:
                continue
        matched.append(row)
    cap = max(0, int(limit))
    return matched[:cap], len(matched)


# Blockers that only mean "this has not been looked at yet" are normal
# progress information, not failures.  They must not paint the whole table red.
_SCAN_ONLY_BLOCKERS = (
    "尚未扫描",
    "未扫描",
    "待扫描",
    "等待扫描",
    "尚未读取",
    "未读取",
    "未解锁",
    "尚未解锁",
)
# A blocker that mentions one of these needs a user decision or a connection
# fix, so it may be shown in the danger color even if it also mentions a scan.
_ACTIONABLE_BLOCKER_HINTS = (
    "设备",
    "连接",
    "作业",
    "干员",
    "编队",
    "理智",
    "预算",
    "失败",
    "错误",
    "不足",
    "冲突",
    "端口",
    "异常",
    "超时",
    "未满足",
)


def row_identity(row: object) -> tuple[str, str, str] | None:
    """Stable identity for keeping a selection across refreshed rows."""

    if not isinstance(row, dict):
        return None
    return (
        str(row.get("group") or ""),
        str(row.get("code") or ""),
        str(row.get("mode") or ""),
    )


def blocker_is_actionable(text: object) -> bool:
    """True only for blockers the user can actually act on.

    A never-scanned account is expected early in a campaign; treating it as a
    destructive error makes every untouched stage look broken.
    """

    value = str(text or "").strip()
    if not value:
        return False
    if any(hint in value for hint in _ACTIONABLE_BLOCKER_HINTS):
        return True
    return not any(hint in value for hint in _SCAN_ONLY_BLOCKERS)


def actionable_blockers(row: object) -> list[str]:
    if not isinstance(row, dict):
        return []
    return [text for text in as_list(row.get("blockers")) if blocker_is_actionable(text)]


def row_presentation(row: dict) -> tuple[str, str]:
    """Return ``(tree_tag, reason_text)`` for one coverage row."""

    state = str(row.get("state") or "unseen")
    blockers = as_list(row.get("blockers"))
    reason = "；".join(blockers)
    if not reason:
        reason = "已核验" if state == "cleared" else "待匹配作业与演习"
    if state == "cleared":
        return "cleared", reason
    if state in ("unseen", "unknown"):
        # Not scanned yet is neutral information, never a danger row.
        return "unseen", reason
    if actionable_blockers(row):
        return "blocked", reason
    if state in ("partial", "uncleared"):
        return "pending", reason
    return "unseen", reason


def page_count_for(total: int, page_size: int = PAGE_SIZE) -> int:
    size = max(1, int(page_size))
    return max(1, (max(0, int(total)) + size - 1) // size)


def format_row_details(row: object) -> str:
    """Full, untruncated detail text for the read-only panel."""

    if not isinstance(row, dict):
        return ""
    code = str(row.get("code") or "—").strip()
    name = str(row.get("name") or "").strip()
    chapter = str(row.get("title") or row.get("chapter") or row.get("zone") or "").strip()
    group = str(row.get("group") or "").strip()
    parts = [code]
    if name:
        parts.append(name)
    parts.append(f"章节：{chapter or '—'}")
    parts.append(f"分组：{group or '—'}")
    parts.append(f"难度：{mode_label(row.get('mode'))}")
    parts.append(f"状态：{state_label(row.get('state'))}")
    head = " · ".join(parts)
    blockers = as_list(row.get("blockers"))
    if blockers:
        detail = "阻塞原因：" + "；".join(blockers)
    else:
        detail = "阻塞原因：无"
    return f"{head}\n{detail}"


class ApiClient:
    """Small stdlib HTTP client for the AutoStory loopback API.

    A re-entrant lock serializes calls made through *this* client.  The window
    creates separate poll and command clients so a slow dashboard request can
    never hold the stop request behind it.
    """

    def __init__(self, url: str, timeout: float = DEFAULT_TIMEOUT) -> None:
        self.base_url = normalize_base_url(url)
        self.timeout = max(0.5, float(timeout))
        self._lock = threading.RLock()
        self._token: str | None = None
        self._opener = urlrequest.build_opener(urlrequest.ProxyHandler({}))
        # New /api/dashboard transport state.  A 404 permanently (for this
        # window) falls back to the legacy /api/state + /api/coverage pair.
        self._dashboard_available = True
        self._revision: str | None = None
        self._last_coverage: dict | None = None

    # -- low level -----------------------------------------------------
    def _request(self, path: str, data: bytes | None = None, headers: dict | None = None) -> tuple[int, bytes]:
        request_headers = {
            "Accept": "application/json",
            "User-Agent": "MAA-AutoStory-Desktop/1.0",
            "Cache-Control": "no-store",
        }
        if headers:
            request_headers.update(headers)
        method = "POST" if data is not None else "GET"
        req = urlrequest.Request(
            self.base_url + path,
            data=data,
            headers=request_headers,
            method=method,
        )
        with self._lock:
            try:
                with self._opener.open(req, timeout=self.timeout) as response:
                    raw = response.read(MAX_RESPONSE_BYTES + 1)
                    if len(raw) > MAX_RESPONSE_BYTES:
                        raise ApiError("后台响应内容过大")
                    return int(response.status), raw
            except urlerror.HTTPError as exc:
                try:
                    raw = exc.read(MAX_RESPONSE_BYTES)
                except Exception:
                    raw = b""
                raise ApiError(self._error_text(raw, exc.code), status=exc.code) from exc
            except (urlerror.URLError, TimeoutError, OSError) as exc:
                reason = getattr(exc, "reason", exc)
                raise ApiError(f"无法连接本机后台（{reason}）") from exc

    @staticmethod
    def _error_text(raw: bytes, status: int) -> str:
        text = raw.decode("utf-8", "replace").strip()
        if text:
            try:
                obj = json.loads(text)
            except Exception:
                obj = None
            if isinstance(obj, dict):
                message = obj.get("error") or obj.get("message")
                if message:
                    return str(message)
            if len(text) <= 240 and "<" not in text:
                return text
        return f"后台返回 HTTP {status}"

    def _get_json(self, path: str) -> dict:
        status, raw = self._request(path)
        try:
            obj = json.loads(raw.decode("utf-8"))
        except Exception as exc:
            raise ApiError(f"后台返回了无效数据（HTTP {status}）") from exc
        if not isinstance(obj, dict):
            raise ApiError("后台返回的数据格式无效")
        return obj

    # -- public API ----------------------------------------------------
    def fetch_dashboard(self) -> tuple[dict | None, dict | None, list[str]]:
        """Fetch the dashboard, preferring the revision-aware endpoint.

        Returns the unchanged ``(state, coverage, errors)`` contract.  The new
        endpoint may return ``coverage: null`` to mean "revision unchanged"; in
        that case the last coverage is reused.  A 404 switches this client to
        the legacy pair so an older installed server still works.
        """

        if self._dashboard_available:
            path = "/api/dashboard"
            if self._revision:
                path += "?" + urlencode({"revision": self._revision})
            try:
                payload = self._get_json(path)
            except ApiError as exc:
                if exc.status != 404:
                    return None, self._last_coverage, [f"后台面板：{exc}"]
                self._dashboard_available = False
            else:
                state = payload.get("state")
                if not isinstance(state, dict):
                    return None, self._last_coverage, ["后台面板：响应缺少 state"]
                revision = payload.get("revision")
                if isinstance(revision, str) and revision:
                    self._revision = revision
                coverage = payload.get("coverage")
                if isinstance(coverage, dict):
                    self._last_coverage = coverage
                else:
                    coverage = self._last_coverage
                return state, coverage, []

        state = None
        coverage = None
        errors: list[str] = []
        try:
            state = self._get_json("/api/state")
        except ApiError as exc:
            errors.append(f"状态接口：{exc}")
        try:
            coverage = self._get_json("/api/coverage")
        except ApiError as exc:
            errors.append(f"进度接口：{exc}")
        if isinstance(coverage, dict):
            self._last_coverage = coverage
        elif self._last_coverage is not None:
            coverage = self._last_coverage
        return state, coverage, errors

    def post_action(self, action: str, chapter: str = "DV") -> dict:
        if action not in ALLOWED_POST_ACTIONS:
            raise ValueError("不支持的操作")
        chapter_text = str(chapter or "DV").strip().upper()
        with self._lock:
            token = self._ensure_token()
            try:
                return self._post_once(action, chapter_text, token)
            except ApiError as exc:
                if exc.status != 403:
                    raise
                # A restarted server rotates its per-process token.  Refresh
                # once and retry exactly once.
                token = self._ensure_token(force=True)
                try:
                    return self._post_once(action, chapter_text, token)
                except ApiError as retry_exc:
                    if retry_exc.status == 403:
                        raise ApiError(
                            "本地校验失败：已刷新令牌仍未通过，请重新打开控制页。",
                            status=403,
                        ) from retry_exc
                    raise

    def _ensure_token(self, force: bool = False) -> str:
        if self._token and not force:
            return self._token
        _, raw = self._request("/", headers={"Accept": "text/html"})
        html = raw.decode("utf-8", "replace")
        token = parse_token(html)
        if not token:
            raise ApiError("无法从控制页读取本地访问令牌")
        self._token = token
        return token

    def _post_once(self, action: str, chapter: str, token: str) -> dict:
        path = "/api/stop" if action == "stop" else "/api/action"
        payload = json.dumps(
            {"action": action, "chapter": chapter},
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json; charset=utf-8",
            "X-AutoStory-Token": token,
        }
        status, raw = self._request(path, data=payload, headers=headers)
        try:
            obj = json.loads(raw.decode("utf-8"))
        except Exception as exc:
            raise ApiError(f"后台返回了无效数据（HTTP {status}）") from exc
        if not isinstance(obj, dict):
            raise ApiError("后台返回的数据格式无效")
        if obj.get("error"):
            raise ApiError(str(obj["error"]))
        return obj


class _PollWorker(threading.Thread):
    """GET-only thread; it never touches Tk objects.

    At most one automatic poll is in flight and at most one manual poll is
    pending, so repeated refresh clicks cannot build an unbounded queue.
    """

    def __init__(
        self,
        client: ApiClient,
        outbox: queue.Queue,
        stop_event: threading.Event,
        epoch_source=None,
    ) -> None:
        super().__init__(name="autostory-desktop-poll", daemon=True)
        self.client = client
        self.outbox = outbox
        self.stop_event = stop_event
        self.epoch_source = epoch_source
        self.interval = POLL_SECONDS
        self._wake = threading.Event()
        self._lock = threading.Lock()
        self._pending = False
        self._inflight = False
        # Compatibility hook for callers that used the old single worker.
        self.command_sink = None

    def submit_poll(self) -> bool:
        """Request one manual poll.

        While a request is in flight at most one follow-up is queued, so a
        command-triggered refresh always observes post-command state without
        allowing repeated clicks to build an unbounded queue.
        """

        with self._lock:
            if self.stop_event.is_set() or self._pending:
                return False
            self._pending = True
        self._wake.set()
        return True

    def submit_command(self, action: str, chapter: str, label: str) -> bool:
        sink = self.command_sink
        if sink is None or self.stop_event.is_set():
            return False
        if action == "stop":
            return sink.submit_stop(chapter, label)
        return sink.submit_command(action, chapter, label)

    def _epoch(self) -> int:
        if self.epoch_source is None:
            return 0
        try:
            return int(self.epoch_source())
        except Exception:
            return 0

    def _emit(self, payload: dict) -> None:
        if not self.stop_event.is_set():
            self.outbox.put(("poll", payload))

    def _poll_once(self) -> None:
        with self._lock:
            self._pending = False
            self._inflight = True
        epoch = self._epoch()
        try:
            state, coverage, errors = self.client.fetch_dashboard()
            self._emit(
                {
                    "state": state,
                    "coverage": coverage,
                    "errors": errors,
                    "epoch": epoch,
                }
            )
        except Exception as exc:  # defensive: the UI must keep polling
            self._emit(
                {
                    "state": None,
                    "coverage": None,
                    "errors": [f"后台客户端异常：{exc}"],
                    "epoch": epoch,
                }
            )
        finally:
            with self._lock:
                self._inflight = False

    def run(self) -> None:
        next_due = 0.0
        while not self.stop_event.is_set():
            try:
                wait = max(0.0, next_due - time.monotonic())
                self._wake.wait(wait if wait > 0 else 0.2)
                self._wake.clear()
                if self.stop_event.is_set():
                    break
                self._poll_once()
                next_due = time.monotonic() + max(1.0, float(self.interval))
            except Exception as exc:
                self._emit(
                    {
                        "state": None,
                        "coverage": None,
                        "errors": [f"后台线程异常：{exc}"],
                        "epoch": self._epoch(),
                    }
                )
                time.sleep(0.5)
                next_due = time.monotonic() + POLL_SECONDS


class _CommandWorker(threading.Thread):
    """POST-only thread, independent of dashboard polling.

    Stop is a priority request rather than a queued command, so a slow or
    blocked poll can never delay it.  The queue is bounded and repeated stop
    clicks collapse into one pending stop request.
    """

    def __init__(
        self,
        client: ApiClient,
        outbox: queue.Queue,
        stop_event: threading.Event,
        epoch_source=None,
    ) -> None:
        super().__init__(name="autostory-desktop-command", daemon=True)
        self.client = client
        self.outbox = outbox
        self.stop_event = stop_event
        self.epoch_source = epoch_source
        self.commands: queue.Queue = queue.Queue(maxsize=COMMAND_QUEUE_SIZE)
        self._wake = threading.Event()
        self._stop_lock = threading.Lock()
        self._stop_pending = False
        self._stop_payload: dict | None = None

    def submit_command(self, action: str, chapter: str, label: str) -> bool:
        if self.stop_event.is_set():
            return False
        payload = {
            "action": action,
            "chapter": chapter,
            "label": label,
            "epoch": self._epoch(),
        }
        try:
            self.commands.put_nowait(payload)
        except queue.Full:
            return False
        self._wake.set()
        return True

    def submit_stop(self, chapter: str = "DV", label: str = "停止当前任务") -> bool:
        with self._stop_lock:
            if self.stop_event.is_set() or self._stop_pending:
                return False
            self._stop_pending = True
            self._stop_payload = {
                "action": "stop",
                "chapter": chapter,
                "label": label,
                "epoch": self._epoch(),
            }
        self._wake.set()
        return True

    def _epoch(self) -> int:
        if self.epoch_source is None:
            return 0
        try:
            return int(self.epoch_source())
        except Exception:
            return 0

    def _take_stop(self) -> dict | None:
        with self._stop_lock:
            if not self._stop_pending:
                return None
            self._stop_pending = False
            payload = self._stop_payload
            self._stop_payload = None
            return payload

    def _emit(self, payload: dict) -> None:
        if not self.stop_event.is_set():
            self.outbox.put(("command", payload))

    def _command(self, payload: dict) -> None:
        action = str(payload.get("action") or "")
        chapter = str(payload.get("chapter") or "DV")
        try:
            result = self.client.post_action(action, chapter)
            message = str(
                result.get("message") or result.get("error") or "请求已提交"
            )
            self._emit(
                {**payload, "ok": True, "message": message, "status": 200}
            )
        except Exception as exc:
            self._emit(
                {
                    **payload,
                    "ok": False,
                    "message": str(exc),
                    "status": getattr(exc, "status", None),
                }
            )

    def _clear_commands(self) -> None:
        while True:
            try:
                self.commands.get_nowait()
            except queue.Empty:
                return

    def run(self) -> None:
        while not self.stop_event.is_set():
            try:
                stop_payload = self._take_stop()
                if stop_payload is not None:
                    self._command(stop_payload)
                    # Never execute a queued run after the user asked to stop.
                    self._clear_commands()
                    continue
                try:
                    payload = self.commands.get(timeout=0.12)
                except queue.Empty:
                    continue
                stop_payload = self._take_stop()
                if stop_payload is not None:
                    self._command(stop_payload)
                    self._clear_commands()
                    continue
                if isinstance(payload, dict):
                    self._command(payload)
            except Exception as exc:
                self._emit(
                    {
                        "action": "",
                        "label": "后台命令",
                        "ok": False,
                        "message": str(exc),
                        "status": None,
                    }
                )
                time.sleep(0.2)


class AutoStoryDesktop:
    """The native window.  All Tk access happens on the creating thread."""

    def __init__(self, url: str, install_root: pathlib.Path, smoke: bool = False) -> None:
        self.install_root = pathlib.Path(install_root)
        self.smoke = bool(smoke)
        # Separate clients keep the GET lock from ever blocking a stop POST.
        self.client = ApiClient(url)
        self.command_client = ApiClient(url)
        self._closed = False

        self._running = False
        self._command_busy = False
        self._stop_busy = False
        self._connection_error = False
        self._first_dashboard = True
        self._chapter_key: str | None = None
        self._profiles: dict = {}
        self._chapter_display_to_key: dict[str, str] = {}
        self._profiles_signature: tuple | None = None
        self.coverage: dict | None = None
        self._last_logs: str | None = None
        self._coverage_render_key: tuple | None = None
        self._last_update = ""
        self._filter_after: str | None = None
        self._drain_after: str | None = None
        # Monotonic epoch: results started before a command are discarded so a
        # slow poll cannot paint the pre-command status over the new one.
        self._dashboard_epoch = 0
        self._poll_failures = 0

        self._results: queue.Queue = queue.Queue()
        self._stop_event = threading.Event()
        self._poll_worker = _PollWorker(
            self.client, self._results, self._stop_event, lambda: self._dashboard_epoch
        )
        self._command_worker = _CommandWorker(
            self.command_client,
            self._results,
            self._stop_event,
            lambda: self._dashboard_epoch,
        )
        self._poll_worker.command_sink = self._command_worker
        # Compatibility alias for external diagnostics; commands use the
        # dedicated command worker above.
        self._worker = self._poll_worker
        self._worker_started = False

        self.tk_root = tk.Tk()
        # Requirement: withdraw before any widget geometry update or show.
        self.tk_root.withdraw()
        try:
            self._configure_window()
            self._build_ui()
        except Exception:
            self._destroy_root()
            raise

    # -- window setup --------------------------------------------------
    def _configure_window(self) -> None:
        self.tk_root.title(TITLE)
        self.tk_root.geometry(WINDOW_SIZE)
        self.tk_root.minsize(*MIN_SIZE)
        self.tk_root.configure(bg=BG)
        self.tk_root.protocol("WM_DELETE_WINDOW", self.close)
        self._icon_path = self._pick_window_icon()
        if self._icon_path is not None:
            try:
                self.tk_root.iconbitmap(default=str(self._icon_path))
            except tk.TclError:
                pass

        families = set(tkfont.families(self.tk_root))
        self.font_family = self._pick_font(
            families,
            ("Microsoft YaHei UI", "Microsoft YaHei", "微软雅黑", "Noto Sans SC", "Segoe UI"),
        )
        self.mono_family = self._pick_font(
            families, ("Cascadia Mono", "Consolas", "Courier New", "Courier")
        )
        self.f8 = (self.font_family, 8)
        self.f8b = (self.font_family, 8, "bold")
        self.f9 = (self.font_family, 9)
        self.f9b = (self.font_family, 9, "bold")
        self.f10 = (self.font_family, 10)
        self.f10b = (self.font_family, 10, "bold")
        self.f11b = (self.font_family, 11, "bold")
        self.f12b = (self.font_family, 12, "bold")
        self.f13b = (self.font_family, 13, "bold")
        self.f14b = (self.font_family, 14, "bold")
        self.f15b = (self.font_family, 15, "bold")
        self.f16b = (self.font_family, 15, "bold")
        self.f17b = (self.font_family, 17, "bold")
        self.f18b = (self.font_family, 18, "bold")
        self.mono9 = (self.mono_family, 9)

        style = ttk.Style(self.tk_root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        self._configure_styles(style)

    def _pick_window_icon(self) -> pathlib.Path | None:
        for name in ("shu.ico", "app.ico"):
            path = self.install_root / "assets" / name
            if path.is_file():
                return path
        return None

    def _configure_styles(self, style: ttk.Style) -> None:
        style.configure(".", font=self.f10)
        style.configure("TFrame", background=BG)
        style.configure("Surface.TFrame", background=CARD)
        style.configure("Sidebar.TFrame", background=SIDEBAR)
        style.configure("TLabel", background=BG, foreground=TEXT, font=self.f10)
        style.configure("Surface.TLabel", background=CARD, foreground=TEXT, font=self.f10)
        style.configure("Card.TLabel", background=CARD, foreground=TEXT, font=self.f10)
        style.configure("Muted.TLabel", background=CARD, foreground=MUTED, font=self.f9)
        style.configure("SurfaceMuted.TLabel", background=CARD, foreground=MUTED, font=self.f9)
        style.configure("Title.TLabel", background=BG, foreground=TEXT, font=self.f17b)
        style.configure("Sub.TLabel", background=BG, foreground=MUTED, font=self.f9)

        def flat_button(name: str, background: str, foreground: str, border: str,
                        hover: str, pressed: str, disabled_bg: str, disabled_fg: str,
                        font, padding: tuple[int, int]) -> None:
            style.configure(
                name,
                font=font,
                padding=padding,
                relief="flat",
                borderwidth=0,
                background=background,
                foreground=foreground,
                bordercolor=border,
                lightcolor=background,
                darkcolor=background,
                focuscolor=FOCUS,
                focusthickness=1,
            )
            style.map(
                name,
                background=[
                    ("disabled", disabled_bg),
                    ("pressed", pressed),
                    ("active", hover),
                ],
                foreground=[("disabled", disabled_fg)],
                bordercolor=[("focus", ACCENT), ("active", border)],
            )

        flat_button(
            "TButton", SOFT_SURFACE, TEXT, BORDER, "#E1EBE6", "#D6E3DD",
            "#E6ECE9", "#8A9B97", self.f10, (11, 5),
        )
        flat_button(
            "Quiet.TButton", BG, TEXT, BORDER, "#E7EFEB", "#DCE8E2",
            "#E6ECE9", "#8A9B97", self.f9, (10, 4),
        )
        flat_button(
            "Secondary.TButton", CARD, TEXT, BORDER, "#E8F0EC", "#DDE9E3",
            "#E6ECE9", "#8A9B97", self.f10, (11, 5),
        )
        flat_button(
            "Small.TButton", CARD, TEXT, BORDER, "#E8F0EC", "#DDE9E3",
            "#E6ECE9", "#8A9B97", self.f9, (9, 3),
        )
        flat_button(
            "Accent.TButton", ACCENT, "#FFFFFF", ACCENT, ACCENT_HOVER, ACCENT_DARK,
            "#B7CFC9", "#F3F8F6", self.f10b, (12, 7),
        )
        flat_button(
            "Danger.TButton", DANGER, "#FFFFFF", DANGER, DANGER_HOVER, DANGER_DARK,
            "#E7D2CF", "#8F5D57", self.f10b, (12, 7),
        )
        flat_button(
            "Sidebar.TButton", SIDEBAR_PANEL, SIDEBAR_TEXT, SIDEBAR_BORDER,
            "#2A4D46", "#22433D", "#233C38", "#7E958F", self.f10, (11, 6),
        )

        style.configure(
            "TEntry",
            fieldbackground=CARD,
            foreground=TEXT,
            insertcolor=TEXT,
            bordercolor=BORDER,
            lightcolor=CARD,
            darkcolor=CARD,
            padding=(7, 5),
        )
        style.map(
            "TEntry",
            bordercolor=[("focus", ACCENT)],
            lightcolor=[("focus", CARD)],
            darkcolor=[("focus", CARD)],
        )
        style.configure(
            "TCombobox",
            fieldbackground=CARD,
            background=CARD,
            foreground=TEXT,
            bordercolor=BORDER,
            lightcolor=CARD,
            darkcolor=CARD,
            arrowcolor=MUTED,
            padding=(6, 4),
        )
        style.map(
            "TCombobox",
            fieldbackground=[("readonly", CARD)],
            selectbackground=[("readonly", CARD)],
            selectforeground=[("readonly", TEXT)],
            background=[("active", SOFT_SURFACE)],
            bordercolor=[("focus", ACCENT)],
            arrowcolor=[("active", ACCENT)],
        )
        style.configure(
            "Card.TCheckbutton",
            background=CARD,
            foreground=TEXT,
            font=self.f9,
            focuscolor=FOCUS,
        )
        style.map(
            "Card.TCheckbutton",
            background=[("active", CARD), ("pressed", CARD)],
            foreground=[("disabled", "#8A9B97")],
        )
        style.configure(
            "Treeview",
            background=CARD,
            fieldbackground=CARD,
            foreground=TEXT,
            rowheight=TREE_ROW_HEIGHT,
            font=self.f10,
            borderwidth=0,
            bordercolor=BORDER,
            lightcolor=CARD,
            darkcolor=CARD,
            relief="flat",
        )
        style.map(
            "Treeview",
            background=[("selected", SELECTION_BG)],
            foreground=[("selected", "#17312E")],
        )
        style.configure(
            "Treeview.Heading",
            background=HEADING_BG,
            foreground="#3D5652",
            font=self.f10b,
            relief="flat",
            borderwidth=0,
            padding=(8, 7),
        )
        style.map(
            "Treeview.Heading",
            background=[("active", "#E3ECE7")],
            relief=[("active", "flat")],
        )
        style.configure(
            "Vertical.TScrollbar",
            background="#C8D6D0",
            troughcolor="#F0F5F2",
            bordercolor="#F0F5F2",
            arrowcolor=MUTED,
            width=10,
            relief="flat",
        )
        style.configure(
            "TNotebook",
            background=CARD,
            borderwidth=0,
            bordercolor=BORDER,
            lightcolor=CARD,
            darkcolor=CARD,
            relief="flat",
            tabmargins=(0, 0, 0, 0),
        )
        style.configure(
            "TNotebook.Tab",
            background=HEADING_BG,
            foreground=MUTED,
            padding=(0, 0),
            borderwidth=0,
        )
        # The sidebar is the only visible navigation; the native tab strip is
        # suppressed but the tab frames stay fully functional.
        try:
            style.layout("TNotebook.Tab", [])
        except tk.TclError:
            pass

    @staticmethod
    def _pick_font(families: set[str], candidates: tuple[str, ...]) -> str:
        for name in candidates:
            if name in families:
                return name
        try:
            return str(tkfont.nametofont("TkDefaultFont").actual("family"))
        except Exception:
            return "Segoe UI"

    def _build_ui(self) -> None:
        self.tk_root.rowconfigure(0, weight=1)
        self.tk_root.columnconfigure(0, minsize=SIDEBAR_WIDTH)
        self.tk_root.columnconfigure(1, weight=1)
        self._build_sidebar()
        self._build_workspace()
        self._mutating_buttons = [
            self.btn_scan,
            self.btn_plan,
            self.btn_run,
            self.btn_ops,
            self.btn_run_all,
            self.btn_resume,
        ]
        self._sync_page_chrome(self.coverage_tab)
        self._apply_button_states()
        self.tk_root.bind("<Control-f>", self._focus_search)
        self.tk_root.bind("<Control-F>", self._focus_search)
        self.tk_root.bind("<F5>", self._poll_shortcut)

    # -- sidebar -------------------------------------------------------
    def _build_sidebar(self) -> None:
        side = tk.Frame(self.tk_root, bg=SIDEBAR)
        side.grid(row=0, column=0, sticky="nsew")
        side.columnconfigure(0, weight=1)
        side.rowconfigure(3, weight=1)
        self.sidebar = side

        self.status_var = tk.StringVar(value="正在连接")
        self.status_detail_var = tk.StringVar(value="等待本机后台响应…")
        self.workflow_var = tk.StringVar(value="等待开始")
        self.workflow_detail_var = tk.StringVar(value="等待后台上报当前阶段")
        self.campaign_var = tk.StringVar(value="")

        self._build_brand(side)
        self._build_nav(side)
        self._build_task_block(side)
        self._build_sidebar_actions(side)
        self._build_sidebar_footer(side)

    def _build_brand(self, parent: tk.Frame) -> None:
        brand = tk.Frame(parent, bg=SIDEBAR)
        brand.grid(row=0, column=0, sticky="ew", padx=16, pady=(12, 10))
        brand.columnconfigure(1, weight=1)
        portrait = tk.Canvas(
            brand, width=48, height=48, bg=SIDEBAR, highlightthickness=0, bd=0
        )
        portrait.grid(row=0, column=0, rowspan=2, sticky="w")
        self._sidebar_portrait = portrait
        self._shu_photo = None
        self._draw_brand_mark(portrait)
        tk.Label(
            brand, text="MAA", bg=SIDEBAR, fg=SIDEBAR_TEXT, font=self.f13b, anchor="w"
        ).grid(row=0, column=1, sticky="sw", padx=(11, 0))
        tk.Label(
            brand, text="自动推图助手", bg=SIDEBAR, fg=SIDEBAR_MUTED,
            font=self.f9, anchor="w",
        ).grid(row=1, column=1, sticky="nw", padx=(11, 0), pady=(2, 0))

    def _draw_brand_mark(self, canvas: tk.Canvas) -> None:
        image_path = self.install_root / "assets" / "shu.png"
        image = None
        if image_path.is_file():
            try:
                image = tk.PhotoImage(file=str(image_path))
            except tk.TclError:
                image = None
        if image is not None:
            self._shu_photo = image
            canvas.create_image(24, 24, image=image)
            canvas.create_oval(1, 1, 46, 46, outline="#3C5C55")
            return
        canvas.create_oval(2, 2, 45, 45, fill=SAGE_ACTIVE, outline="")
        canvas.create_text(24, 24, text="M", fill=ACCENT_DARK, font=self.f15b)

    def _build_nav(self, parent: tk.Frame) -> None:
        nav = tk.Frame(parent, bg=SIDEBAR)
        nav.grid(row=1, column=0, sticky="ew", padx=12)
        nav.columnconfigure(0, weight=1)
        tk.Label(
            nav, text="导航", bg=SIDEBAR, fg="#7F9A94", font=self.f8,
            anchor="w",
        ).grid(row=0, column=0, sticky="w", padx=3, pady=(0, 5))
        self.btn_nav_coverage = self._nav_button(nav, "关卡进度", self._show_coverage)
        self.btn_nav_coverage.grid(row=1, column=0, sticky="ew", pady=(0, 3))
        self.btn_nav_logs = self._nav_button(nav, "运行记录", self._show_logs)
        self.btn_nav_logs.grid(row=2, column=0, sticky="ew")

    def _nav_button(self, parent: tk.Frame, text: str, command) -> tk.Button:
        return tk.Button(
            parent,
            text=text,
            command=command,
            anchor="w",
            padx=13,
            pady=8,
            bg=SIDEBAR,
            fg=SIDEBAR_TEXT,
            activebackground=SIDEBAR_PANEL,
            activeforeground="#FFFFFF",
            relief="flat",
            bd=0,
            highlightthickness=1,
            highlightbackground=SIDEBAR,
            highlightcolor=FOCUS,
            font=self.f10b,
            cursor="hand2",
            takefocus=True,
        )

    def _style_nav(self, button: tk.Button, selected: bool) -> None:
        try:
            if selected:
                button.configure(
                    bg=SAGE_ACTIVE,
                    fg="#17312E",
                    activebackground=SAGE_ACTIVE_HOVER,
                    activeforeground="#17312E",
                )
            else:
                button.configure(
                    bg=SIDEBAR,
                    fg=SIDEBAR_TEXT,
                    activebackground=SIDEBAR_PANEL,
                    activeforeground="#FFFFFF",
                )
        except tk.TclError:
            pass

    def _build_task_block(self, parent: tk.Frame) -> None:
        card = tk.Frame(
            parent,
            bg=SIDEBAR_PANEL,
            highlightbackground=SIDEBAR_BORDER,
            highlightthickness=1,
            bd=0,
        )
        card.grid(row=2, column=0, sticky="ew", padx=12, pady=(12, 0))
        card.columnconfigure(1, weight=1)
        tk.Frame(card, bg=ACCENT, width=3).grid(
            row=0, column=0, rowspan=5, sticky="ns"
        )
        pad = {"padx": (12, 12), "sticky": "ew"}
        tk.Label(
            card, text="当前任务", bg=SIDEBAR_PANEL, fg=SIDEBAR_MUTED,
            font=self.f8b, anchor="w",
        ).grid(row=0, column=1, pady=(10, 2), **pad)
        self.status_value_label = tk.Label(
            card,
            textvariable=self.status_var,
            bg=SIDEBAR_PANEL,
            fg="#D5E2DD",
            font=self.f15b,
            anchor="w",
        )
        self.status_value_label.grid(row=1, column=1, **pad)
        tk.Label(
            card,
            textvariable=self.workflow_var,
            bg=SIDEBAR_PANEL,
            fg="#BCD1CB",
            font=self.f9,
            wraplength=164,
            justify="left",
            anchor="w",
        ).grid(row=2, column=1, pady=(2, 4), **pad)
        tk.Label(
            card,
            textvariable=self.workflow_detail_var,
            bg=SIDEBAR_PANEL,
            fg=SIDEBAR_MUTED,
            font=self.f8,
            wraplength=164,
            height=2,
            justify="left",
            anchor="nw",
        ).grid(row=3, column=1, **pad)
        tk.Label(
            card,
            textvariable=self.campaign_var,
            bg=SIDEBAR_PANEL,
            fg="#8FB8AE",
            font=self.f8,
            wraplength=164,
            height=2,
            justify="left",
            anchor="nw",
        ).grid(row=4, column=1, pady=(3, 10), **pad)

    def _build_sidebar_actions(self, parent: tk.Frame) -> None:
        actions = tk.Frame(parent, bg=SIDEBAR)
        actions.grid(row=4, column=0, sticky="ew", padx=12, pady=(4, 0))
        actions.columnconfigure(0, weight=1)
        self._actions_inner = actions
        tk.Label(
            actions, text="全目录", bg=SIDEBAR, fg=SAGE_ACTIVE, font=self.f8b,
            anchor="w",
        ).grid(row=0, column=0, sticky="ew", pady=(0, 4))
        buttons = tk.Frame(actions, bg=SIDEBAR)
        buttons.grid(row=1, column=0, sticky="ew")
        buttons.columnconfigure(0, weight=1)
        self.btn_run_all = ttk.Button(
            buttons,
            text="全目录推图",
            command=lambda: self._post_command("run-all", "ALL", "全目录推图"),
            style="Accent.TButton",
        )
        self.btn_run_all.grid(row=0, column=0, sticky="ew", pady=(0, 5))
        self.btn_resume = ttk.Button(
            buttons,
            text="继续上轮全目录",
            command=lambda: self._post_command("resume-all", "ALL", "继续上轮全目录"),
            style="Sidebar.TButton",
        )
        self.btn_resume.grid(row=1, column=0, sticky="ew")

        self.budget_var = tk.StringVar(value="正在读取预算设置…")
        self.budget_label = tk.Label(
            actions,
            textvariable=self.budget_var,
            bg=SIDEBAR,
            fg=SIDEBAR_MUTED,
            font=self.f8,
            wraplength=184,
            height=3,
            justify="left",
            anchor="nw",
        )
        self.budget_label.grid(row=2, column=0, sticky="ew", pady=(6, 0))

        self.message_var = tk.StringVar(value="正在连接本机后台…")
        self.message_label = tk.Label(
            actions,
            textvariable=self.message_var,
            bg=SIDEBAR,
            fg=SIDEBAR_MUTED,
            font=self.f8,
            wraplength=184,
            height=3,
            justify="left",
            anchor="nw",
        )
        self.message_label.grid(row=3, column=0, sticky="ew", pady=(3, 0))

        stop_frame = tk.Frame(parent, bg=SIDEBAR)
        stop_frame.grid(row=5, column=0, sticky="ew", padx=12, pady=(10, 0))
        stop_frame.columnconfigure(0, weight=1)
        tk.Frame(stop_frame, bg=SIDEBAR_BORDER, height=1).grid(
            row=0, column=0, sticky="ew"
        )
        self.btn_stop = ttk.Button(
            stop_frame,
            text="停止当前任务",
            command=self._post_stop,
            style="Danger.TButton",
        )
        self.btn_stop.grid(row=1, column=0, sticky="ew", pady=(10, 0))

    def _build_sidebar_footer(self, parent: tk.Frame) -> None:
        tk.Label(
            parent,
            text="MAA AutoStory · 本地控制台",
            bg=SIDEBAR,
            fg="#6F8B85",
            font=self.f8,
            anchor="w",
        ).grid(row=6, column=0, sticky="ew", padx=16, pady=(8, 14))

    # -- main workspace ------------------------------------------------
    def _build_workspace(self) -> None:
        workspace = tk.Frame(self.tk_root, bg=BG)
        workspace.grid(row=0, column=1, sticky="nsew")
        workspace.columnconfigure(0, weight=1)
        workspace.rowconfigure(2, weight=1)
        self.workspace = workspace
        self._build_page_header(workspace)

        self.coverage_header = tk.Frame(workspace, bg=BG)
        self.coverage_header.grid(row=1, column=0, sticky="ew", padx=(20, 20))
        self.coverage_header.columnconfigure(0, weight=1)
        self._build_telemetry(self.coverage_header)
        self._build_chapter_row(self.coverage_header)

        self._build_content(workspace)
        self._build_page_footer(workspace)

    def _build_page_header(self, parent: tk.Frame) -> None:
        header = tk.Frame(parent, bg=BG)
        header.grid(row=0, column=0, sticky="ew", padx=(20, 20), pady=(10, 6))
        header.columnconfigure(0, weight=1)
        title_block = tk.Frame(header, bg=BG)
        title_block.grid(row=0, column=0, sticky="w")
        self.page_title_var = tk.StringVar(value="关卡进度")
        tk.Label(
            title_block, textvariable=self.page_title_var, bg=BG, fg=TEXT,
            font=self.f18b, anchor="w",
        ).grid(row=0, column=0, sticky="w")
        self.page_subtitle_var = tk.StringVar(
            value="查看章节进度、筛选作业并开始推图"
        )
        tk.Label(
            title_block,
            textvariable=self.page_subtitle_var,
            bg=BG,
            fg=MUTED,
            font=self.f9,
            anchor="w",
        ).grid(row=1, column=0, sticky="w", pady=(2, 0))

        right = tk.Frame(header, bg=BG)
        right.grid(row=0, column=1, sticky="e")
        self.connection_var = tk.StringVar(value="本机后台：连接中")
        self.connection_label = tk.Label(
            right,
            textvariable=self.connection_var,
            bg=BG,
            fg=MUTED,
            font=self.f8,
            anchor="e",
            width=34,
        )
        self.connection_label.grid(row=0, column=0, sticky="e")
        actions = tk.Frame(right, bg=BG)
        actions.grid(row=1, column=0, sticky="e", pady=(5, 0))
        self.btn_refresh = ttk.Button(
            actions,
            text="刷新状态",
            command=lambda: self._request_poll(silent=False),
            style="Quiet.TButton",
        )
        self.btn_refresh.grid(row=0, column=0, padx=(0, 6))
        self.btn_web = ttk.Button(
            actions,
            text="网页控制页",
            command=self._open_web,
            style="Quiet.TButton",
        )
        self.btn_web.grid(row=0, column=1)

    def _build_telemetry(self, parent: tk.Frame) -> None:
        band = tk.Frame(
            parent,
            bg=CARD,
            highlightbackground=BORDER,
            highlightthickness=1,
            bd=0,
        )
        band.grid(row=0, column=0, sticky="ew")
        band.columnconfigure(0, weight=1)
        inner = tk.Frame(band, bg=CARD)
        inner.grid(row=0, column=0, sticky="ew", padx=16, pady=8)
        inner.columnconfigure(0, weight=3)
        inner.columnconfigure(2, weight=2)

        account = tk.Frame(inner, bg=CARD)
        account.grid(row=0, column=0, sticky="ew")
        account.columnconfigure(0, weight=1)
        account_head = tk.Frame(account, bg=CARD)
        account_head.grid(row=0, column=0, sticky="ew")
        account_head.columnconfigure(0, weight=1)
        tk.Label(
            account_head, text="账号进度", bg=CARD, fg=MUTED, font=self.f9,
            anchor="w",
        ).grid(row=0, column=0, sticky="w")
        self.progress_var = tk.StringVar(value="—")
        tk.Label(
            account_head,
            textvariable=self.progress_var,
            bg=CARD,
            fg=TEXT,
            font=self.f15b,
            anchor="e",
        ).grid(row=0, column=1, sticky="e", padx=(8, 0))
        self.progress_canvas = tk.Canvas(
            account, width=260, height=6, bg=CARD, highlightthickness=0, bd=0
        )
        self.progress_canvas.grid(row=1, column=0, sticky="ew", pady=(6, 5))
        self.progress_canvas.bind("<Configure>", self._draw_progress_meter)
        self.progress_detail_var = tk.StringVar(value="尚未读取关卡进度")
        tk.Label(
            account,
            textvariable=self.progress_detail_var,
            bg=CARD,
            fg=MUTED,
            font=self.f9,
            anchor="w",
            width=1,
        ).grid(row=2, column=0, sticky="ew")

        tk.Frame(inner, bg=BORDER, width=1).grid(
            row=0, column=1, sticky="ns", padx=18
        )

        snapshot = tk.Frame(inner, bg=CARD)
        snapshot.grid(row=0, column=2, sticky="ew")
        snapshot.columnconfigure(1, weight=1)
        tk.Label(
            snapshot, text="干员快照", bg=CARD, fg=MUTED, font=self.f9,
            anchor="w",
        ).grid(row=0, column=0, sticky="w")
        self.operators_var = tk.StringVar(value="—")
        tk.Label(
            snapshot,
            textvariable=self.operators_var,
            bg=CARD,
            fg=TEXT,
            font=self.f13b,
            anchor="e",
        ).grid(row=0, column=1, sticky="e", padx=(8, 0))
        self.operators_detail_var = tk.StringVar(value="尚未读取干员快照")
        tk.Label(
            snapshot,
            textvariable=self.operators_detail_var,
            bg=CARD,
            fg=MUTED,
            font=self.f8,
            anchor="w",
            justify="left",
            width=1,
            height=2,
        ).grid(row=1, column=0, columnspan=2, sticky="ew", pady=(5, 0))

    def _build_chapter_row(self, parent: tk.Frame) -> None:
        row = tk.Frame(parent, bg=BG)
        row.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        row.columnconfigure(6, weight=1)
        self.chapter_row = row
        tk.Label(
            row, text="当前章节", bg=BG, fg=MUTED, font=self.f9, anchor="w"
        ).grid(row=0, column=0, sticky="w", padx=(0, 8))
        self.chapter_var = tk.StringVar()
        self.chapter_combo = ttk.Combobox(
            row,
            textvariable=self.chapter_var,
            state="readonly",
            width=20,
            font=self.f10,
        )
        self.chapter_combo.grid(row=0, column=1, sticky="w", padx=(0, 8))
        self.chapter_combo.bind("<<ComboboxSelected>>", self._on_chapter_selected)

        self.btn_scan = ttk.Button(
            row,
            text="扫描本分区",
            command=lambda: self._post_command("scan", self._selected_chapter(), "扫描本分区"),
            style="Small.TButton",
        )
        self.btn_plan = ttk.Button(
            row,
            text="筛选作业",
            command=lambda: self._post_command("plan", self._selected_chapter(), "筛选作业"),
            style="Small.TButton",
        )
        self.btn_run = ttk.Button(
            row,
            text="本分区推图",
            command=lambda: self._post_command("run", self._selected_chapter(), "本分区推图"),
            style="Small.TButton",
        )
        self.btn_ops = ttk.Button(
            row,
            text="更新干员",
            command=lambda: self._post_command("refresh", self._selected_chapter(), "更新干员"),
            style="Small.TButton",
        )
        for column, button in (
            (2, self.btn_scan),
            (3, self.btn_plan),
            (4, self.btn_run),
            (5, self.btn_ops),
        ):
            button.grid(row=0, column=column, sticky="w", padx=(0, 5))
        tk.Label(
            row,
            text="仅作用于当前章节",
            bg=BG,
            fg=MUTED,
            font=self.f8,
            anchor="e",
        ).grid(row=0, column=6, sticky="e")

    def _build_content(self, parent: tk.Frame) -> None:
        card = tk.Frame(
            parent,
            bg=CARD,
            highlightbackground=BORDER,
            highlightthickness=1,
            bd=0,
        )
        card.grid(row=2, column=0, sticky="nsew", padx=(20, 20), pady=(6, 0))
        card.columnconfigure(0, weight=1)
        card.rowconfigure(0, weight=1)
        self.content_card = card

        self.notebook = ttk.Notebook(card)
        self.notebook.grid(row=0, column=0, sticky="nsew")
        self.coverage_tab = tk.Frame(self.notebook, bg=CARD)
        self.logs_tab = tk.Frame(self.notebook, bg=CARD)
        self.notebook.add(self.coverage_tab, text="关卡进度")
        self.notebook.add(self.logs_tab, text="运行记录")
        self._build_coverage(self.coverage_tab)
        self._build_logs(self.logs_tab)
        self.notebook.bind("<<NotebookTabChanged>>", self._on_tab_changed)

    def _build_coverage(self, parent: tk.Frame) -> None:
        inner = tk.Frame(parent, bg=CARD)
        inner.pack(fill="both", expand=True, padx=14, pady=(8, 8))
        inner.columnconfigure(0, weight=1)
        inner.rowconfigure(2, weight=1)

        top = tk.Frame(inner, bg=CARD)
        top.grid(row=0, column=0, sticky="ew")
        top.columnconfigure(1, weight=1)
        tk.Label(
            top, text="关卡明细", bg=CARD, fg=TEXT, font=self.f11b, anchor="w"
        ).grid(row=0, column=0, sticky="w")
        self.coverage_summary_var = tk.StringVar(value="正在读取关卡目录…")
        tk.Label(
            top,
            text="按关卡与难度分别记录",
            width=1,
            bg=CARD,
            fg=MUTED,
            font=self.f9,
            anchor="w",
        ).grid(row=0, column=1, sticky="ew", padx=(12, 10))

        pager = tk.Frame(top, bg=CARD)
        pager.grid(row=0, column=2, sticky="e")
        self.btn_prev_page = ttk.Button(
            pager, text="上一页", width=6, command=self._prev_page,
            style="Small.TButton",
        )
        self.btn_prev_page.grid(row=0, column=0, padx=(0, 5))
        self.page_info_var = tk.StringVar(value="—")
        tk.Label(
            pager,
            textvariable=self.page_info_var,
            bg=CARD,
            fg=MUTED,
            font=self.f9,
            anchor="center",
        ).grid(row=0, column=1, padx=(0, 5))
        self.btn_next_page = ttk.Button(
            pager, text="下一页", width=6, command=self._next_page,
            style="Small.TButton",
        )
        self.btn_next_page.grid(row=0, column=2)

        filters = tk.Frame(inner, bg=CARD)
        filters.grid(row=1, column=0, sticky="ew", pady=(6, 6))
        filters.columnconfigure(6, weight=1)
        tk.Label(
            filters, text="搜索", bg=CARD, fg=MUTED, font=self.f9
        ).grid(row=0, column=0, sticky="w", padx=(0, 5))
        self.search_var = tk.StringVar()
        self.search_entry = ttk.Entry(
            filters, textvariable=self.search_var, width=24, font=self.f10
        )
        self.search_entry.grid(row=0, column=1, sticky="w", padx=(0, 8))
        self.search_entry.bind("<Return>", self._apply_search_now)
        self.search_entry.bind("<Escape>", self._clear_search)
        self.search_var.trace_add("write", self._schedule_filter)

        self.state_choices = {label: value for label, value in STATE_FILTERS}
        self.state_combo = ttk.Combobox(
            filters,
            values=[label for label, _ in STATE_FILTERS],
            state="readonly",
            width=13,
            font=self.f9,
        )
        self.state_combo.current(0)
        self.state_combo.grid(row=0, column=2, padx=(0, 6))
        self.state_combo.bind("<<ComboboxSelected>>", self._on_filter_change)

        self.mode_choices = {label: value for label, value in MODE_FILTERS}
        self.mode_combo = ttk.Combobox(
            filters,
            values=[label for label, _ in MODE_FILTERS],
            state="readonly",
            width=11,
            font=self.f9,
        )
        self.mode_combo.current(0)
        self.mode_combo.grid(row=0, column=3, padx=(0, 6))
        self.mode_combo.bind("<<ComboboxSelected>>", self._on_filter_change)

        self.btn_reset_filters = ttk.Button(
            filters, text="重置筛选", command=self._reset_filters,
            style="Small.TButton",
        )
        self.btn_reset_filters.grid(row=0, column=4, sticky="w")
        self.filter_count_var = tk.StringVar(value="")
        tk.Label(
            filters,
            textvariable=self.filter_count_var,
            bg=CARD,
            fg=MUTED,
            font=self.f9,
            anchor="e",
        ).grid(row=0, column=7, sticky="e")

        tree_frame = tk.Frame(inner, bg=CARD)
        tree_frame.grid(row=2, column=0, sticky="nsew")
        tree_frame.columnconfigure(0, weight=1)
        tree_frame.rowconfigure(0, weight=1)
        self.tree_frame = tree_frame

        columns = ("stage", "chapter", "mode", "state", "reason")
        self.tree = ttk.Treeview(
            tree_frame,
            columns=columns,
            show="headings",
            selectmode="browse",
            height=TREE_VISIBLE_ROWS,
        )
        headings = {
            "stage": "关卡",
            "chapter": "章节",
            "mode": "难度",
            "state": "状态",
            "reason": "待处理原因",
        }
        widths = {
            "stage": 168,
            "chapter": 116,
            "mode": 56,
            "state": 88,
            "reason": 270,
        }
        for column in columns:
            self.tree.heading(column, text=headings[column], anchor="w")
            self.tree.column(
                column,
                width=widths[column],
                minwidth=50,
                anchor="w",
                stretch=(column == "reason"),
            )
        # Semantic tags only set the text colour.  Zebra tags provide the
        # neutral row backgrounds, so no state paints a whole row green.
        self.tree.tag_configure("cleared", foreground=TEXT)
        self.tree.tag_configure("pending", foreground=WARN)
        self.tree.tag_configure("blocked", foreground=DANGER)
        self.tree.tag_configure("unseen", foreground="#667A76")
        self.tree.tag_configure("odd", background=ROW_ALT)
        self.tree.tag_configure("even", background=CARD)
        self.tree.tag_configure(
            "selected", background=SELECTION_BG, foreground="#17312E"
        )
        self.tree.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(
            tree_frame, orient="vertical", command=self.tree.yview
        )
        self.tree.configure(yscrollcommand=scrollbar.set)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.tree.bind("<<TreeviewSelect>>", self._on_tree_select)

        details = tk.Frame(
            inner,
            bg=SOFT_SURFACE,
            highlightbackground=BORDER,
            highlightthickness=1,
            bd=0,
        )
        details.grid(row=3, column=0, sticky="ew", pady=(6, 0))
        details.columnconfigure(1, weight=1)
        self.details_panel = details
        tk.Frame(details, bg=ACCENT, width=3).grid(
            row=0, column=0, rowspan=2, sticky="ns"
        )
        detail_head = tk.Frame(details, bg=SOFT_SURFACE)
        detail_head.grid(row=0, column=1, columnspan=2, sticky="ew", padx=(10, 8), pady=(5, 2))
        detail_head.columnconfigure(0, weight=1)
        tk.Label(
            detail_head, text="选中关卡", bg=SOFT_SURFACE, fg=MUTED,
            font=self.f8b, anchor="w",
        ).grid(row=0, column=0, sticky="w")
        self.btn_copy_details = ttk.Button(
            detail_head, text="复制详情", width=8, command=self._copy_details,
            style="Small.TButton",
        )
        self.btn_copy_details.grid(row=0, column=1, sticky="e")

        self.details_text = tk.Text(
            details,
            height=2,
            width=1,
            wrap="char",
            state="disabled",
            bg=SOFT_SURFACE,
            fg="#334C48",
            relief="flat",
            font=self.f9,
            padx=10,
            pady=2,
            highlightthickness=0,
        )
        self.details_text.grid(row=1, column=1, sticky="nsew", padx=(10, 0), pady=(0, 6))
        detail_scroll = ttk.Scrollbar(
            details, orient="vertical", command=self.details_text.yview
        )
        self.details_text.configure(yscrollcommand=detail_scroll.set)
        detail_scroll.grid(row=1, column=2, sticky="ns", padx=(0, 4), pady=(0, 6))

        self._iid_rows: dict[str, dict] = {}
        self._coverage_page = 0
        self._page_count = 1
        self._coverage_render_key = None
        self._last_filter_key: tuple[str, str, str] | None = None
        self._details_state = "initial"
        self.coverage_placeholder = "正在读取关卡目录…"
        self.filter_count_var.set(self.coverage_placeholder)
        self._render_details(None, empty=False)
        self._set_page_buttons(False, False)

    def _build_logs(self, parent: tk.Frame) -> None:
        inner = tk.Frame(parent, bg=CARD)
        inner.pack(fill="both", expand=True, padx=14, pady=(10, 10))
        inner.columnconfigure(0, weight=1)
        inner.rowconfigure(1, weight=1)

        top = tk.Frame(inner, bg=CARD)
        top.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        top.columnconfigure(1, weight=1)
        tk.Label(
            top, text="实时输出", bg=CARD, fg=TEXT, font=self.f11b, anchor="w"
        ).grid(row=0, column=0, sticky="w")
        tk.Label(
            top,
            text="只读 · 关闭窗口不会停止任务",
            bg=CARD,
            fg=MUTED,
            font=self.f9,
            anchor="w",
        ).grid(row=0, column=1, sticky="w", padx=(10, 0))
        self.follow_var = tk.BooleanVar(value=True)
        self.chk_follow = ttk.Checkbutton(
            top,
            text="跟随最新",
            variable=self.follow_var,
            style="Card.TCheckbutton",
            command=self._on_follow_changed,
        )
        self.chk_follow.grid(row=0, column=2, sticky="e", padx=(8, 8))
        self.btn_log_latest = ttk.Button(
            top, text="跳到最新", command=self._scroll_logs_latest,
            style="Small.TButton",
        )
        self.btn_log_latest.grid(row=0, column=3, sticky="e")

        text_frame = tk.Frame(inner, bg=CARD)
        text_frame.grid(row=1, column=0, sticky="nsew")
        text_frame.columnconfigure(0, weight=1)
        text_frame.rowconfigure(0, weight=1)
        self.log_text = tk.Text(
            text_frame,
            height=12,
            wrap="word",
            state="disabled",
            bg="#F6F9F7",
            fg="#2B423F",
            relief="flat",
            font=self.mono9,
            padx=12,
            pady=10,
            highlightthickness=1,
            highlightbackground=BORDER,
            highlightcolor=BORDER,
        )
        self.log_text.grid(row=0, column=0, sticky="nsew")
        log_scroll = ttk.Scrollbar(
            text_frame, orient="vertical", command=self.log_text.yview
        )
        self.log_text.configure(yscrollcommand=log_scroll.set)
        log_scroll.grid(row=0, column=1, sticky="ns")
        self.log_text.bind("<Control-c>", self._copy_log_selection)
        self._logs_opened = False
        self.log_text.bind("<Map>", self._on_logs_mapped)
        self._replace_logs("尚未读取运行记录。", first=True)

    def _build_page_footer(self, parent: tk.Frame) -> None:
        footer = tk.Frame(parent, bg=BG)
        footer.grid(row=3, column=0, sticky="ew", padx=(20, 20), pady=(4, 8))
        footer.columnconfigure(0, weight=1)
        tk.Label(
            footer,
            text="关闭窗口不会停止后台任务。",
            bg=BG,
            fg=MUTED,
            font=self.f8,
            anchor="w",
        ).grid(row=0, column=0, sticky="w")
        tk.Label(
            footer,
            text="Ctrl+F 搜索 · F5 刷新状态",
            bg=BG,
            fg=MUTED,
            font=self.f8,
            anchor="e",
        ).grid(row=0, column=1, sticky="e", padx=(12, 0))

    # -- navigation / presentation helpers ----------------------------
    def _show_coverage(self) -> None:
        self._select_page(self.coverage_tab)

    def _show_logs(self) -> None:
        self._select_page(self.logs_tab)

    def _select_page(self, tab: tk.Frame) -> None:
        if self._closed:
            return
        try:
            self.notebook.select(tab)
        except tk.TclError:
            return
        self._sync_page_chrome(tab)
        if tab is self.logs_tab:
            self._on_logs_mapped()

    def _on_tab_changed(self, _event=None) -> None:
        if not hasattr(self, "btn_nav_coverage"):
            return
        try:
            selected = self.notebook.nametowidget(self.notebook.select())
        except (tk.TclError, KeyError):
            return
        self._sync_page_chrome(selected)

    def _sync_page_chrome(self, tab: object) -> None:
        if not hasattr(self, "coverage_header"):
            return
        logs = tab is self.logs_tab
        if logs:
            self.coverage_header.grid_remove()
            title = "运行记录"
            subtitle = "后台输出的只读记录，关闭窗口不会停止任务"
        else:
            self.coverage_header.grid()
            title = "关卡进度"
            subtitle = "查看章节进度、筛选作业并开始推图"
        self.page_title_var.set(title)
        self.page_subtitle_var.set(subtitle)
        self._style_nav(self.btn_nav_coverage, not logs)
        self._style_nav(self.btn_nav_logs, logs)

    def _draw_progress_meter(self, _event=None) -> None:
        canvas = getattr(self, "progress_canvas", None)
        if canvas is None:
            return
        try:
            width = int(canvas.winfo_width())
            if width <= 1:
                width = int(canvas.cget("width")) or 260
            height = int(canvas.winfo_height())
            if height <= 1:
                height = int(canvas.cget("height")) or 6
            canvas.delete("all")
            canvas.create_rectangle(0, 0, width, height, fill="#E3EBE7", outline="")
            ratio = max(0.0, min(1.0, float(getattr(self, "_progress_ratio", 0.0))))
            if ratio > 0:
                canvas.create_rectangle(
                    0, 0, max(2, int(width * ratio)), height,
                    fill=ACCENT, outline="",
                )
        except (tk.TclError, ValueError):
            return

    def _selected_chapter(self) -> str:
        key = self._chapter_key
        if key and key in self._profiles:
            return key
        return "DV"

    def _on_chapter_selected(self, _event=None) -> None:
        display = self.chapter_var.get()
        key = self._chapter_display_to_key.get(display)
        if key:
            self._chapter_key = key

    def _post_stop(self) -> None:
        if self._closed:
            return
        if self._stop_busy:
            self._set_message("停止信号已发送，正在等待后台确认；请勿重复点击。", "warn")
            return
        # A newer epoch makes any in-flight poll result stale.
        self._dashboard_epoch += 1
        self._stop_busy = True
        self._apply_button_states()
        if not self._command_worker.submit_stop(
            self._selected_chapter(), "停止当前任务"
        ):
            self._stop_busy = False
            self._apply_button_states()
            self._set_message("停止请求未能排队，请稍后重试。", "error")
            return
        self._set_message("正在发送停止信号…", "warn")

    def _post_command(self, action: str, chapter: str, label: str) -> None:
        if self._closed:
            return
        if self._running or self._command_busy or self._stop_busy:
            self._set_message(
                "后台任务正在运行，会修改任务的按钮已禁用；停止按钮始终可用。",
                "warn",
            )
            return
        self._dashboard_epoch += 1
        self._command_busy = True
        self._apply_button_states()
        self._set_message(f"正在提交“{label}”…", "info")
        if not self._command_worker.submit_command(action, chapter, label):
            self._command_busy = False
            self._apply_button_states()
            self._set_message("命令队列已满或界面正在关闭，请稍后重试。", "error")

    def _request_poll(self, silent: bool = True) -> None:
        if self._closed:
            return
        submitted = self._poll_worker.submit_poll()
        if not silent:
            self._set_message(
                "正在刷新后台状态…" if submitted else "状态正在刷新，请稍候…",
                "info",
            )

    def _open_web(self) -> None:
        if self._closed:
            return
        url = self.client.base_url + "/"
        try:
            opened = webbrowser.open_new_tab(url)
        except Exception as exc:
            self._set_message(f"无法打开默认浏览器：{exc}", "error")
            return
        if opened:
            self._set_message(f"已请求浏览器打开：{url}", "info")
        else:
            self._set_message(f"未能启动默认浏览器；可手动访问 {url}", "warn")

    def _cancel_filter_after(self) -> None:
        if self._filter_after is None:
            return
        try:
            self.tk_root.after_cancel(self._filter_after)
        except tk.TclError:
            pass
        self._filter_after = None

    def _clear_search(self, _event=None):
        """Escape in the search box clears the query and returns to page one."""

        self._coverage_page = 0
        self._cancel_filter_after()
        self.search_var.set("")
        self._cancel_filter_after()
        self._render_coverage()
        return "break"

    def _apply_search_now(self, _event=None):
        self._coverage_page = 0
        self._cancel_filter_after()
        self._render_coverage()
        return "break"

    def _schedule_filter(self, *_args) -> None:
        if self._closed:
            return
        if self._filter_after is not None:
            try:
                self.tk_root.after_cancel(self._filter_after)
            except tk.TclError:
                pass
        # Any text refinement starts again at the first page.
        self._coverage_page = 0
        self._filter_after = self.tk_root.after(180, self._render_coverage)

    def _on_filter_change(self, _event=None) -> None:
        self._coverage_page = 0
        self._cancel_filter_after()
        self._render_coverage()

    def _reset_filters(self, _event=None):
        """Reset search, state and mode together; local only, never a POST."""

        if self._closed:
            return "break"
        self._coverage_page = 0
        self._cancel_filter_after()
        self.search_var.set("")
        self.state_combo.current(0)
        self.mode_combo.current(0)
        self._cancel_filter_after()
        self._render_coverage()
        return "break"

    def _prev_page(self) -> None:
        if self._closed or self._coverage_page <= 0:
            return
        self._cancel_filter_after()
        self._coverage_page -= 1
        self._render_coverage()

    def _next_page(self) -> None:
        if self._closed or self._coverage_page + 1 >= self._page_count:
            return
        self._cancel_filter_after()
        self._coverage_page += 1
        self._render_coverage()

    def _set_page_buttons(self, previous: bool, following: bool) -> None:
        for button, enabled in (
            (getattr(self, "btn_prev_page", None), previous),
            (getattr(self, "btn_next_page", None), following),
        ):
            if button is None:
                continue
            try:
                button.configure(state="normal" if enabled else "disabled")
            except tk.TclError:
                pass

    def _focus_search(self, _event=None):
        """Ctrl+F: show the coverage tab and select the current query."""

        if self._closed:
            return "break"
        self._show_coverage()
        self.search_entry.focus_set()
        try:
            self.search_entry.selection_range(0, "end")
        except tk.TclError:
            pass
        return "break"

    def _poll_shortcut(self, _event=None):
        """F5 performs a GET-only dashboard poll."""

        self._request_poll(silent=False)
        return "break"

    # -- state application --------------------------------------------
    def _apply_button_states(self) -> None:
        disabled = self._running or self._command_busy or self._stop_busy
        state = "disabled" if disabled else "normal"
        for button in self._mutating_buttons:
            try:
                button.configure(state=state)
            except tk.TclError:
                pass
        # Stop and local refresh are always available, even while running.
        for button in (self.btn_stop, self.btn_refresh, self.btn_web):
            try:
                button.configure(state="normal")
            except tk.TclError:
                pass

    def _set_message(self, text: str, kind: str = "info") -> None:
        colors = {
            "info": SIDEBAR_MUTED,
            "success": "#86D3B2",
            "warn": "#F0C27A",
            "error": "#F2A39C",
        }
        self.message_var.set(clip_text(text))
        if hasattr(self, "message_label"):
            self.message_label.configure(fg=colors.get(kind, MUTED))

    def _apply_dashboard(self, payload: object) -> None:
        if not isinstance(payload, dict):
            self._set_message("后台返回了无效的刷新结果。", "error")
            return
        epoch = payload.get("epoch")
        if isinstance(epoch, int) and epoch < self._dashboard_epoch:
            # The result started before a newer command; never repaint the
            # pre-command status over the command's fresh state.
            return
        state = payload.get("state")
        coverage = payload.get("coverage")
        errors = as_list(payload.get("errors"))

        if isinstance(state, dict):
            self._apply_state(state)
        if isinstance(coverage, dict):
            self._apply_coverage(coverage)

        now_text = time.strftime("%H:%M:%S")
        if errors:
            self._poll_failures += 1
            if not self._connection_error:
                self._set_message(
                    "后台读取失败："
                    + "；".join(errors)
                    + f"（每 {int(POLL_SECONDS)} 秒自动重试，保留上次有效数据）",
                    "error",
                )
            self._connection_error = True
            self.connection_var.set(
                f"本机后台：连接异常 · 自动重试中（第 {self._poll_failures} 次）"
            )
            self.connection_label.configure(fg=DANGER)
            self._last_update = now_text
            return

        was_error = self._connection_error
        self._connection_error = False
        self._poll_failures = 0
        self._last_update = now_text
        self.connection_var.set(f"本机后台：已连接 · 最后更新 {now_text}")
        self.connection_label.configure(fg=SUCCESS)
        if was_error:
            self._first_dashboard = False
            self._set_message("后台连接已恢复。", "success")
        elif self._first_dashboard:
            self._first_dashboard = False
            self._set_message(
                "已连接本机后台。请选择章节后手动开始；关闭窗口不会停止后台任务。",
                "info",
            )

    def _apply_state(self, state: dict) -> None:
        status = state.get("status")
        if not isinstance(status, dict):
            status = {}
        running = bool(state.get("running")) or str(status.get("state")) == "running"
        self._running = running
        # Poll faster while work is active; stay gentle when idle.
        self._poll_worker.interval = POLL_ACTIVE_SECONDS if running else POLL_SECONDS
        self._apply_button_states()

        state_key = str(status.get("state") or "idle")
        self.status_var.set(status_label(state_key))
        self.status_detail_var.set(status_detail(status, running))
        workflow_headline, workflow_detail = workflow_summary(state)
        self.workflow_var.set(workflow_headline)
        self.workflow_detail_var.set(workflow_detail)
        self.campaign_var.set(campaign_summary(state))
        color = {
            "running": "#7FD1AF",
            "error": "#F2A39C",
            "stopped": "#F0C27A",
            "interrupted": "#F0C27A",
            "done": "#8FD7B8",
            "idle": "#D5E2DD",
        }.get(state_key, SIDEBAR_TEXT)
        self.status_value_label.configure(fg=color)

        operators = state.get("operators")
        if not isinstance(operators, dict):
            operators = {}
        count = operators.get("count")
        self.operators_var.set(
            f"{format_int(count)} 名" if count is not None else "—"
        )
        detail_parts = []
        if operators.get("time"):
            snapshot = f"快照 {pretty_time(operators.get('time'))}"
            age = format_age(operators.get("time"))
            if age:
                snapshot += f"（{age}）"
            detail_parts.append(snapshot)
        else:
            detail_parts.append("尚未识别快照时间")
        missing = as_list(operators.get("missing"))
        if missing:
            detail_parts.append(f"{format_int(len(missing))} 名历史干员未识别")
        self.operators_detail_var.set("\n".join(detail_parts))

        settings = state.get("settings")
        self.budget_var.set(format_budget(settings))
        profiles = state.get("profiles")
        if isinstance(profiles, dict):
            self._populate_profiles(profiles)
        self._apply_logs(state.get("logs"))
        self._update_progress_tile()

    def _apply_coverage(self, coverage: dict) -> None:
        rows = coverage.get("rows")
        if not isinstance(rows, list):
            self._set_message("关卡进度数据格式异常，将继续重试。", "error")
            return
        self.coverage = coverage
        self.coverage_summary_var.set(coverage_summary(coverage))
        self._update_progress_tile()
        self._render_coverage()

    def _update_progress_tile(self) -> None:
        coverage = self.coverage
        if not isinstance(coverage, dict) or not isinstance(coverage.get("rows"), list):
            return
        rows = coverage.get("rows") or []
        variants = coverage.get("stage_variants", len(rows))
        confirmed = coverage.get("confirmed")
        cleared = coverage.get("cleared")
        if confirmed is None:
            confirmed = sum(
                1
                for row in rows
                if isinstance(row, dict)
                and str(row.get("state") or "unseen") not in ("unknown", "unseen")
            )
        if cleared is None:
            cleared = sum(
                1
                for row in rows
                if isinstance(row, dict) and str(row.get("state")) == "cleared"
            )
        try:
            pending_scan = max(0, int(variants) - int(confirmed))
        except (TypeError, ValueError):
            pending_scan = 0
        self.progress_var.set(f"{format_int(confirmed)} / {format_int(variants)}")
        self.progress_detail_var.set(
            f"已通关 {format_int(cleared)} · 已确认 {format_int(confirmed)}"
            f" · 待扫描 {format_int(pending_scan)}"
        )
        try:
            ratio = int(confirmed) / int(variants) if int(variants) > 0 else 0.0
        except (TypeError, ValueError):
            ratio = 0.0
        self._progress_ratio = max(0.0, min(1.0, ratio))
        self._draw_progress_meter()

    def _populate_profiles(self, profiles: dict) -> None:
        profiles = {str(key): value for key, value in profiles.items()}
        signature = tuple(
            (str(key), str(value.get("title", "")) if isinstance(value, dict) else "")
            for key, value in sorted(profiles.items(), key=lambda item: natural_key(item[0]))
        )
        if signature == self._profiles_signature:
            return
        self._profiles_signature = signature
        self._profiles = profiles
        displays: list[str] = []
        mapping: dict[str, str] = {}
        for key in sorted(profiles, key=natural_key):
            profile = profiles.get(key)
            title = profile.get("title") if isinstance(profile, dict) else ""
            display = f"{key} · {title}" if title else str(key)
            displays.append(display)
            mapping[display] = str(key)
        self._chapter_display_to_key = mapping
        self.chapter_combo.configure(values=displays)

        available = [str(key) for key in sorted(profiles, key=natural_key)]
        preferred = self._chapter_key if self._chapter_key in available else None
        if preferred is None:
            for candidate in ("CW-EX", "DV"):
                if candidate in available:
                    preferred = candidate
                    break
        if preferred is None and available:
            preferred = available[0]
        self._chapter_key = preferred
        if preferred is not None:
            display = next(
                (text for text, value in mapping.items() if value == preferred), ""
            )
            self.chapter_var.set(display)
        else:
            self.chapter_var.set("")

    def _apply_logs(self, logs: object) -> None:
        text = logs if isinstance(logs, str) else ""
        if text == self._last_logs:
            return
        self._last_logs = text
        content = text if text else "暂无运行记录。"
        self._replace_logs(content, first=False)

    def _replace_logs(self, content: str, first: bool = False) -> None:
        try:
            follow = True if first else bool(self.follow_var.get())
            at_bottom = True
            top_fraction = 0.0
            if not first:
                try:
                    view = self.log_text.yview()
                    at_bottom = view[1] >= 0.985
                    top_fraction = view[0]
                except tk.TclError:
                    at_bottom = True
            self.log_text.configure(state="normal")
            self.log_text.delete("1.0", "end")
            self.log_text.insert("1.0", content)
            self.log_text.configure(state="disabled")
            if first or (follow and at_bottom):
                self.log_text.see("end")
            else:
                # The user is reading older output (or explicitly turned
                # following off): keep the current reading position.
                self.log_text.yview_moveto(top_fraction)
        except tk.TclError:
            pass

    def _on_logs_mapped(self, _event=None) -> None:
        # The hidden notebook tab has no usable text viewport. Defer initial
        # following until it is displayed; later tab switches preserve reading.
        if not self._logs_opened:
            self._logs_opened = True
            self._on_follow_changed()

    def _on_follow_changed(self) -> None:
        if self.follow_var.get():
            self._scroll_logs_latest()

    def _scroll_logs_latest(self) -> None:
        if self._closed:
            return
        try:
            self.log_text.see("end")
        except tk.TclError:
            pass

    def _copy_log_selection(self, _event=None):
        try:
            selected = self.log_text.get("sel.first", "sel.last")
        except tk.TclError:
            return "break"
        try:
            self.tk_root.clipboard_clear()
            self.tk_root.clipboard_append(selected)
        except tk.TclError:
            pass
        return "break"

    def _selected_row(self) -> dict | None:
        selection = self.tree.selection()
        if not selection:
            return None
        row = self._iid_rows.get(selection[0])
        return row if isinstance(row, dict) else None

    def _selected_row_identity(self) -> tuple[str, str, str] | None:
        return row_identity(self._selected_row())

    def _on_tree_select(self, _event=None) -> None:
        """Show full details locally; selection never starts a game task."""

        self._sync_selected_tags()
        # Deleting the selected item queues this event after the filter has
        # already rendered its empty state. Keep that state on delivery.
        empty = self.coverage is not None and not self.tree.get_children()
        self._render_details(self._selected_row(), empty=empty)

    def _sync_selected_tags(self) -> None:
        """Keep selection visible even with zebra row background tags."""

        try:
            selected = set(self.tree.selection())
            for iid in self.tree.get_children():
                tags = [
                    tag for tag in self.tree.item(iid, "tags") if tag != "selected"
                ]
                if iid in selected:
                    tags.insert(0, "selected")
                self.tree.item(iid, tags=tags)
        except tk.TclError:
            pass

    def _render_details(self, row: object, empty: bool = False) -> None:
        if not hasattr(self, "details_text"):
            return
        if isinstance(row, dict):
            text = format_row_details(row)
            self._details_state = "row"
        elif empty:
            text = "没有匹配筛选条件的关卡。可调整搜索条件，或点击“重置筛选”。"
            self._details_state = "empty"
        else:
            text = "在列表中选择一行，查看完整关卡信息与阻塞原因。"
            self._details_state = "initial"
        try:
            self.details_text.configure(state="normal")
            self.details_text.delete("1.0", "end")
            self.details_text.insert("1.0", text)
            self.details_text.configure(state="disabled")
            self.details_text.yview_moveto(0.0)
        except tk.TclError:
            pass
        if hasattr(self, "btn_copy_details"):
            try:
                self.btn_copy_details.configure(
                    state="normal" if isinstance(row, dict) else "disabled"
                )
            except tk.TclError:
                pass

    def _copy_details(self, _event=None):
        """Copy the full (untruncated) selected-row detail.  No command POST."""

        row = self._selected_row()
        if not isinstance(row, dict):
            self._set_message("请先在关卡列表中选择一行，再复制详情。", "warn")
            return "break"
        text = format_row_details(row)
        try:
            self.tk_root.clipboard_clear()
            self.tk_root.clipboard_append(text)
        except tk.TclError as exc:
            self._set_message(f"复制详情失败：{exc}", "error")
            return "break"
        self._set_message("已复制选中关卡的完整详情。", "success")
        return "break"

    def _render_coverage(self) -> None:
        # A direct refresh may run before the debounced search callback. Cancel
        # it before clearing its id so close() cannot leave an orphan callback.
        self._cancel_filter_after()
        if self._closed:
            return
        coverage = self.coverage
        rows = coverage.get("rows") if isinstance(coverage, dict) else []
        if not isinstance(rows, list):
            rows = []
        state_filter = self.state_choices.get(
            self.state_combo.get(), "all"
        )
        mode_filter = self.mode_choices.get(self.mode_combo.get(), "all")
        query = self.search_var.get()

        # Guard direct/programmatic filter changes too, not just the widget
        # events: a new filter always starts at the first page.
        filter_key = (state_filter, mode_filter, query)
        if (
            self._last_filter_key is not None
            and filter_key != self._last_filter_key
        ):
            self._coverage_page = 0
        self._last_filter_key = filter_key

        # Filter the complete catalogue, then slice exactly one real page.
        # ``filter_coverage`` itself keeps its public 200-row cap contract.
        if coverage is None:
            all_matching: list[dict] = []
            total = 0
        else:
            all_matching, total = filter_coverage(
                rows,
                query,
                state_filter,
                mode_filter,
                max(1, len(rows)),
            )
        page_count = page_count_for(total)
        page = min(max(0, self._coverage_page), page_count - 1)
        self._coverage_page = page
        self._page_count = page_count
        start = page * PAGE_SIZE
        visible = all_matching[start : start + PAGE_SIZE]

        render_key = (
            coverage is not None,
            coverage_signature(rows),
            state_filter,
            mode_filter,
            query,
            page,
        )
        if render_key == self._coverage_render_key:
            # Unchanged refresh: keep the existing selection and scroll
            # position exactly where the reader left them.
            self._set_page_buttons(page > 0, page < page_count - 1)
            return
        selected_identity = self._selected_row_identity()

        try:
            self.tree.delete(*self.tree.get_children())
        except tk.TclError:
            return
        self._iid_rows = {}
        for index, row in enumerate(visible):
            tag, reason = row_presentation(row)
            code = str(row.get("code") or "")
            name = str(row.get("name") or "")
            stage = f"{code}  {name}".strip()
            values = (
                stage,
                str(row.get("title") or row.get("group") or "—"),
                "磨难" if row.get("mode") == "tough" else mode_label(row.get("mode")),
                TABLE_STATE_LABELS.get(str(row.get("state")), state_label(row.get("state"))),
                clip_text(reason, 220),
            )
            iid = str(start + index)
            zebra = "odd" if index % 2 else "even"
            self.tree.insert(
                "", "end", iid=iid, values=values, tags=(tag, zebra)
            )
            self._iid_rows[iid] = row

        restored: dict | None = None
        if selected_identity is not None:
            for iid, row in self._iid_rows.items():
                if row_identity(row) == selected_identity:
                    self.tree.selection_set(iid)
                    self.tree.see(iid)
                    restored = row
                    break

        if coverage is None:
            self.filter_count_var.set(self.coverage_placeholder)
            self.page_info_var.set("—")
            self._set_page_buttons(False, False)
            self._render_details(None, empty=False)
        else:
            self.filter_count_var.set(f"筛选 {format_int(total)} 条")
            if page_count > 1:
                self.page_info_var.set(
                    f"第 {page + 1} / {page_count} 页 · "
                    f"{start + 1}–{start + len(visible)} / {total}"
                )
            else:
                self.page_info_var.set("第 1 / 1 页")
            self._set_page_buttons(page > 0, page < page_count - 1)
            if restored is not None:
                self._render_details(restored, empty=False)
            elif total <= 0:
                self._render_details(None, empty=True)
            else:
                self._render_details(None, empty=False)
        self._coverage_render_key = render_key

    # -- worker/results plumbing --------------------------------------
    def _start_worker(self) -> None:
        if self._worker_started:
            return
        self._worker_started = True
        self._drain_after = self.tk_root.after(100, self._drain_results)
        self._poll_worker.start()
        self._command_worker.start()

    def _drain_results(self) -> None:
        if self._closed:
            return
        try:
            while True:
                kind, payload = self._results.get_nowait()
                if kind == "poll":
                    self._apply_dashboard(payload)
                elif kind == "command":
                    self._handle_command(payload)
        except queue.Empty:
            pass
        except Exception as exc:
            self._set_message(f"界面刷新失败：{exc}", "error")
        if not self._closed:
            self._drain_after = self.tk_root.after(100, self._drain_results)

    def _handle_command(self, payload: object) -> None:
        if self._closed or not isinstance(payload, dict):
            return
        action = str(payload.get("action") or "")
        epoch = payload.get("epoch")
        stale = isinstance(epoch, int) and epoch < self._dashboard_epoch
        if action == "stop":
            self._stop_busy = False
            # Stop discards queued start commands, which have no result event.
            self._command_busy = False
        else:
            self._command_busy = False
        self._apply_button_states()
        if stale:
            # A newer stop/command owns the status line; still refresh once.
            self._request_poll(silent=True)
            return
        # Invalidate polls that were already in flight before this result and
        # queue exactly one fresh reconciliation poll.
        self._dashboard_epoch += 1
        label = str(payload.get("label") or action or "操作")
        message = str(payload.get("message") or "")
        if payload.get("ok"):
            self._set_message(
                f"{label}：{message}" if message else f"{label}：请求已提交",
                "success",
            )
        else:
            self._set_message(f"{label}失败：{message or '未知错误'}", "error")
        # Always reconcile once after a command, including stop, so the tile
        # shows the actual phase rather than the optimistic button label.
        self._request_poll(silent=True)

    # -- lifecycle -----------------------------------------------------
    def run(self) -> None:
        self.tk_root.update_idletasks()
        self._center_window()
        self._start_worker()
        self.tk_root.deiconify()
        self.tk_root.mainloop()

    def run_smoke(self) -> dict:
        try:
            state, coverage, errors = self.client.fetch_dashboard()
            if state is None or coverage is None:
                detail = "；".join(errors) or "后台返回空数据"
                raise RuntimeError(f"冒烟检查失败：{detail}")
            for key in ("status", "operators", "profiles", "settings"):
                if not isinstance(state.get(key), dict):
                    raise RuntimeError(f"冒烟检查失败：/api/state 缺少 {key} 字段")
            if not isinstance(coverage.get("rows"), list):
                raise RuntimeError("冒烟检查失败：/api/coverage 缺少 rows 数组")
            self._apply_dashboard(
                {"state": state, "coverage": coverage, "errors": errors}
            )
            self.tk_root.update_idletasks()
            return {
                "ok": True,
                "profiles": len(state["profiles"]),
                "coverage_rows": len(coverage["rows"]),
                "tk": tk.TkVersion,
            }
        finally:
            self.close()

    def _center_window(self) -> None:
        try:
            width, height = (int(part) for part in WINDOW_SIZE.split("x", 1))
            screen_width = self.tk_root.winfo_screenwidth()
            screen_height = self.tk_root.winfo_screenheight()
            x = max(0, (screen_width - width) // 2)
            y = max(0, (screen_height - height) // 3)
            self.tk_root.geometry(f"{width}x{height}+{x}+{y}")
        except tk.TclError:
            pass

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._stop_event.set()
        if self._filter_after is not None:
            try:
                self.tk_root.after_cancel(self._filter_after)
            except tk.TclError:
                pass
            self._filter_after = None
        if self._drain_after is not None:
            try:
                self.tk_root.after_cancel(self._drain_after)
            except tk.TclError:
                pass
            self._drain_after = None
        self._destroy_root()

    def _destroy_root(self) -> None:
        try:
            self.tk_root.destroy()
        except tk.TclError:
            pass


def run_ui(url: str, root: pathlib.Path, smoke: bool = False) -> dict | None:
    """Show the native control window, or run the bounded smoke path.

    Normal mode performs only GET polling until the user explicitly presses a
    task button.  The window is withdrawn immediately after creation so the
    smoke path can never flash a visible window.
    """

    ui: AutoStoryDesktop | None = None
    try:
        ui = AutoStoryDesktop(url, root, smoke=smoke)
        if smoke:
            return ui.run_smoke()
        ui.run()
        return None
    finally:
        if ui is not None:
            ui.close()
