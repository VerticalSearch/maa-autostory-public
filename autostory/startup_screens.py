"""Pure startup-screen classifier for Arknights.

This module intentionally performs no ADB, filesystem or process I/O and keeps
no mutable global state.  The caller supplies OCR entries, the exact foreground
Android package and, optionally, a page that an independent navigator has
already verified.

Only the official package ``com.hypergryph.arknights`` and the two AOSP/Google
system installers are accepted.  Anything else is reported as ``unknown`` and
never receives an actionable button.
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

__all__ = ["classify", "GAME_PACKAGE", "INSTALLER_PACKAGES", "VERIFIED_PAGES"]

GAME_PACKAGE = "com.hypergryph.arknights"
INSTALLER_PACKAGES = frozenset(
    {"com.android.packageinstaller", "com.google.android.packageinstaller"}
)
VERIFIED_PAGES = frozenset({"home", "terminal", "stories", "map", "operator", "is_overview", "dv_special", "cw_morse", "sn_map"})

# The OCR pipeline is fixed at 1280x720.  Coordinates outside that surface are
# treated as corrupt evidence, never as clickable targets.
SCREEN_WIDTH = 1280.0
SCREEN_HEIGHT = 720.0
PASSIVE_SCORE = 0.8
ACTION_SCORE = 0.9
PROGRESS_SCORE = 0.8

_PAGE_LABELS = {
    "home": "主页",
    "terminal": "终端",
    "stories": "乐章收录",
    "map": "地图",
    "operator": "干员详情",
    "is_overview": "叙拉古人建筑地图",
    "dv_special": "绿野幻梦特殊关卡详情",
    "cw_morse": "孤星摩斯码地图",
    "sn_map": "愚人号地图单节点与战斗图标",
}

# Punctuation and whitespace are discarded before exact label/phrase matching.
# NFKC normalisation also folds full-width digits, '%' and ASCII punctuation.
_PUNCTUATION = frozenset(
    " \t\r\n\u3000.,;:!?，。；：！？、…·•—–-_/\\|()[]{}<>《》〈〉「」『』【】\"'“”‘’"
)

_PERCENT_RE = re.compile(r"(?<![\d.\-])(\d{1,3}(?:\.\d+)?)\s*[%％]")

# --- attention / hard blockers -------------------------------------------------
# Ordered: purchase and destructive actions are reported before account/login.
_ATTENTION_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "购买/付费界面",
        (
            "购买",
            "支付",
            "付款",
            "充值",
            "兑换",
            "花费",
            "订单",
            "扣款",
            "退款",
            "价格",
        ),
    ),
    (
        "删除/卸载操作",
        ("删除", "清除数据", "卸载", "移除存档", "注销"),
    ),
    (
        "登录/账号界面",
        (
            "登录",
            "账号",
            "帐号",
            "账户",
            "切换账号",
            "用户中心",
            "账号异常",
            "账号封禁",
        ),
    ),
    (
        "密码/安全验证界面",
        (
            "密码",
            "验证码",
            "验证",
            "认证",
            "人脸",
            "实名",
            "防沉迷",
            "健康系统",
            "安全验证",
            "人机验证",
            "滑动验证",
            "CAPTCHA",
        ),
    ),
    (
        "网络错误/维护界面",
        (
            "网络错误",
            "网络异常",
            "网络连接失败",
            "连接失败",
            "无法连接",
            "网络超时",
            "重新连接",
            "断网",
            "网络不稳定",
            "网络不可用",
            "服务器错误",
            "加载失败",
            "请求失败",
            "维护",
            "停机",
        ),
    ),
    (
        "权限/未知来源界面",
        (
            "权限",
            "授权",
            "允许访问",
            "允许来自",
            "授予",
            "未知来源",
            "未知应用",
            "不受信任",
            "非官方",
            "外部来源",
            "安装未知应用",
            "悬浮窗",
            "存储权限",
            "相机权限",
            "麦克风权限",
        ),
    ),
)

_PURCHASE_OR_DELETE = (
    "购买",
    "支付",
    "付款",
    "充值",
    "源石",
    "合成玉",
    "兑换",
    "花费",
    "订单",
    "扣款",
    "退款",
    "价格",
    "¥",
    "￥",
    "删除",
    "清除数据",
    "卸载",
    "移除存档",
    "注销",
)

# --- battle --------------------------------------------------------------------
_BATTLE_SETTLEMENT = (
    "行动结束",
    "演习结束",
    "作战结束",
    "行动失败",
    "演习失败",
    "任务失败",
    "MISSION RESULTS",
    "MISSION FAILED",
    "MISSIONRESULTS",
    "MISSIONFAILED",
)

# --- installers ----------------------------------------------------------------
_INSTALL_ACTIONS = ("更新", "安装")
_OPEN_ACTION = ("打开",)

# --- game update / download / loading -----------------------------------------
# These are specific mandatory-update or resource-update phrases.  A lone
# "更新" is deliberately absent, and announcement wording such as "更新公告"
# or "更新内容" is not accepted.
_UPDATE_MARKERS = (
    "强制更新",
    "必须更新",
    "需要更新",
    "请更新",
    "发现新版本",
    "新版本",
    "版本更新",
    "客户端更新",
    "游戏更新",
    "资源更新",
    "更新资源",
    "更新至最新",
    "最新版本",
    "需要下载",
    "下载更新",
    "更新后才能",
    "更新后继续",
    "更新后重启",
    "资源下载",
    "下载资源",
    "补丁更新",
    "热更新",
    "下载更新包",
)
_UPDATE_NEGATIVE = (
    "已是最新版本",
    "当前已是最新",
    "已为最新版本",
    "最新版本已安装",
    "无需更新",
    "不需要更新",
    "已更新至最新",
    "已更新到最新",
    "已是最新",
)
_UPDATE_ACTIONS = ("确认", "确定", "下载", "更新")
_INSTALLER_CONTEXT = (
    "要更新此应用吗",
    "要安装此应用吗",
    "替换此应用",
    "应用安装程序",
)
_DOWNLOAD_ACTIVE = (
    "正在下载",
    "下载中",
    "正在下载资源",
    "正在下载更新",
    "正在更新资源",
    "资源更新中",
    "正在获取更新",
    "正在安装资源",
)
_DOWNLOAD_RESOURCE = (
    "资源下载",
    "下载资源",
    "更新资源",
    "资源更新",
    "补丁下载",
    "下载更新",
    "资源包下载",
)
_LOADING_MARKERS = (
    "取得神经连接",
    "正在连接服务器",
    "正在释放神经递质",
    "正在加载",
    "资源加载",
    "正在检查资源完整性",
    "正在检查资源",
    "正在解压资源",
    "正在校验资源",
    "正在初始化资源",
    "正在读取资源",
)
_LOGIN_MARKERS = ("START", "开始唤醒", "点击开始")
_STAGE_STARTS = ("开始行动", "开始作战", "开始突袭")
_STAGE_SECOND_CONTROLS = ("代理指挥", "演习")
_BATTLE_SPEED = ("二倍速", "一倍速")


@dataclass(frozen=True, slots=True)
class _Entry:
    text: str
    compact: str
    upper: str
    score: float
    x: float
    y: float


def _compact(value: str) -> str:
    """Fold OCR spacing/punctuation so exact labels can be compared safely."""
    normalised = unicodedata.normalize("NFKC", value)
    return "".join(
        ch for ch in normalised if not ch.isspace() and ch not in _PUNCTUATION
    )


def _is_finite_number(value: Any) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except (TypeError, OverflowError):
        return False


def _valid_box(box: Any) -> bool:
    if box is None:
        return True
    if not isinstance(box, (list, tuple)) or len(box) != 4:
        return False
    if not all(_is_finite_number(v) for v in box):
        return False
    x1, y1, x2, y2 = (float(v) for v in box)
    return x1 < x2 and y1 < y2


def _clean_entries(texts: Any) -> tuple[_Entry, ...]:
    """Keep only entries with finite score/coordinates and a sane optional box."""
    if texts is None or isinstance(texts, (str, bytes, bytearray, Mapping)):
        return ()
    try:
        iterator = iter(texts)
    except TypeError:
        return ()

    cleaned: list[_Entry] = []
    for item in iterator:
        if not isinstance(item, Mapping):
            continue
        raw_text = item.get("text")
        score = item.get("score")
        x = item.get("x")
        y = item.get("y")
        box = item.get("box")
        if not isinstance(raw_text, str):
            continue
        text = unicodedata.normalize("NFKC", raw_text).strip()
        compact = _compact(text)
        if not compact:
            continue
        if not (
            _is_finite_number(score)
            and _is_finite_number(x)
            and _is_finite_number(y)
        ):
            continue
        score_f, x_f, y_f = float(score), float(x), float(y)
        if not (0.0 <= score_f <= 1.0):
            continue
        if not (0.0 <= x_f < SCREEN_WIDTH and 0.0 <= y_f < SCREEN_HEIGHT):
            continue
        if not _valid_box(box):
            continue
        cleaned.append(
            _Entry(
                text=text,
                compact=compact,
                upper=compact.upper(),
                score=score_f,
                x=x_f,
                y=y_f,
            )
        )
    return tuple(cleaned)


def _has_phrase(
    entries: Sequence[_Entry], phrases: Iterable[str], min_score: float = PASSIVE_SCORE
) -> str | None:
    wanted = tuple((phrase, _compact(phrase).upper()) for phrase in phrases)
    for entry in entries:
        if entry.score + 1e-12 < min_score:
            continue
        for phrase, folded in wanted:
            if folded and folded in entry.upper:
                return phrase
    return None


def _first_exact(
    entries: Sequence[_Entry], tokens: Iterable[str], min_score: float = PASSIVE_SCORE
) -> str | None:
    wanted = {_compact(token).upper() for token in tokens}
    for entry in entries:
        if entry.score + 1e-12 >= min_score and entry.upper in wanted:
            return entry.compact
    return None


def _has_exact(
    entries: Sequence[_Entry], tokens: Iterable[str], min_score: float = PASSIVE_SCORE
) -> bool:
    return _first_exact(entries, tokens, min_score) is not None


def _action_buttons(
    entries: Sequence[_Entry],
    tokens: Iterable[str],
    min_score: float = ACTION_SCORE,
) -> list[dict[str, Any]]:
    """Return unique OCR controls for the exact action labels.

    Duplicate OCR of the same control at the same coordinate is folded once; two
    distinct controls with matching labels remain two buttons and therefore an
    ambiguous result.
    """
    wanted = {_compact(token).upper() for token in tokens}
    seen: set[tuple[str, int, int]] = set()
    buttons: list[dict[str, Any]] = []
    for entry in entries:
        if entry.score + 1e-12 < min_score or entry.upper not in wanted:
            continue
        key = (entry.upper, round(entry.x), round(entry.y))
        if key in seen:
            continue
        seen.add(key)
        buttons.append(
            {
                "x": entry.x,
                "y": entry.y,
                "text": entry.compact,
                "score": entry.score,
            }
        )
    return buttons


def _extract_progress(entries: Sequence[_Entry]) -> float | None:
    """Read one unambiguous percentage in [0, 100]; never infer from a spinner."""
    values: set[float] = set()
    for entry in entries:
        if entry.score + 1e-12 < PROGRESS_SCORE:
            continue
        for match in _PERCENT_RE.finditer(entry.text):
            try:
                value = float(match.group(1))
            except ValueError:
                continue
            if 0.0 <= value <= 100.0:
                values.add(round(value, 6))
    if len(values) != 1:
        return None
    return float(next(iter(values)))


def _result(
    state: str,
    reason: str,
    progress: float | None = None,
    button: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "state": state,
        "reason": reason,
        "progress": progress,
        "button": button,
    }


def _progress_note(progress: float | None) -> str:
    return f"，进度 {progress:.1f}%" if progress is not None else ""


def _attention_reason(entries: Sequence[_Entry], *, check_currency=False) -> str | None:
    if check_currency and _has_phrase(entries, ('源石','合成玉','¥','￥')) and _has_phrase(
            entries, ('是否','花费','购买','兑换','支付','确认','确定','取消')):
        return '检测到货币消费确认，需要人工处理'
    for label, phrases in _ATTENTION_GROUPS:
        marker = _has_phrase(entries, phrases)
        if marker:
            return f"需要人工处理：检测到{label}（{marker}），不执行任何输入"
    return None


def _battle_reason(entries: Sequence[_Entry]) -> str | None:
    # Paused battle: both the resume and the abandon/pause control are visible.
    if _has_exact(entries, ("继续行动",)) and _has_exact(
        entries, ("放弃行动", "暂停", "行动暂停", "战斗暂停", "撤退", "技能")
    ):
        return "检测到暂停中的战斗（继续行动/放弃行动），不执行任何输入"

    # Active-battle HUD.  A speed or pause control makes the screen specific to
    # an ongoing battle; quick formation can also show "技能" and "撤退", so
    # those two labels alone are deliberately not enough.
    has_skill = _has_exact(entries, ("技能",))
    has_retreat = _has_exact(entries, ("撤退",))
    has_speed = _has_exact(entries, _BATTLE_SPEED)
    has_pause = _has_exact(entries, ("暂停",))
    if has_speed and (has_skill or has_retreat):
        return "检测到战斗进行中的 HUD（技能/撤退/倍速），不执行任何输入"
    if has_pause and (has_skill or has_retreat):
        return "检测到战斗进行中的 HUD（技能/撤退/暂停），不执行任何输入"
    return None


def _installer_result(entries: Sequence[_Entry]) -> dict[str, Any]:
    app_label = _has_exact(entries, ("明日方舟",))
    if not app_label:
        return _result(
            "unknown",
            "系统安装器前台，但未识别到精确应用名“明日方舟”，不执行安装或更新操作",
        )

    completion = _has_phrase(entries, ("安装完成", "已安装"))
    if completion:
        open_buttons = _action_buttons(entries, _OPEN_ACTION)
        if len(open_buttons) == 1:
            return _result(
                "install_done",
                "系统安装器显示“明日方舟”已安装，且存在唯一“打开”按钮",
                button=open_buttons[0],
            )
        if len(open_buttons) > 1:
            return _result(
                "unknown",
                "安装完成界面存在多个“打开”按钮，拒绝猜测",
            )

    if _has_phrase(entries, ("正在安装",)):
        return _result(
            "installing",
            "系统安装器正在安装“明日方舟”，不执行任何输入",
        )

    install_buttons = _action_buttons(entries, _INSTALL_ACTIONS)
    if len(install_buttons) == 1:
        label = install_buttons[0]["text"]
        return _result(
            "install_confirm",
            f"系统安装器内的“明日方舟”确认界面，唯一按钮为“{label}”",
            button=install_buttons[0],
        )
    if len(install_buttons) > 1:
        return _result(
            "unknown",
            "系统安装器内存在多个“更新/安装”按钮，拒绝猜测",
        )
    return _result(
        "unknown",
        "系统安装器前台，但未识别到可信的安装/更新状态，不执行输入",
    )


def _game_result(entries: Sequence[_Entry], page: Any) -> dict[str, Any]:
    progress = _extract_progress(entries)

    rhodes=any(e.upper=='RHODESISLAND' and e.score>=.9 and 500<e.x<780 and 300<e.y<500 for e in entries)
    tip=any(e.score>=.9 and len(e.compact)>=10 and 200<e.x<1080 and 630<e.y<710 for e in entries)
    if rhodes and tip:
        return _result('loading','罗德岛标志与底部提示确认：正在进入游戏')
    if (_has_exact(entries,('HYPERGRYPH',)) and _has_exact(entries,('STUDIO',))
            and _has_phrase(entries,('健康游戏忠告',))):
        return _result('loading','游戏启动版权与健康提示页，等待启动完成')

    active_download = _has_phrase(entries, _DOWNLOAD_ACTIVE)
    if active_download:
        return _result(
            "download",
            f"“明日方舟”正在下载/更新资源（{active_download}）{_progress_note(progress)}",
            progress=progress,
        )

    update_marker = _has_phrase(entries, _UPDATE_MARKERS)
    update_is_negative = bool(_has_phrase(entries, _UPDATE_NEGATIVE))
    update_is_installer_text = bool(_has_phrase(entries, _INSTALLER_CONTEXT))
    update_is_purchase = bool(_has_phrase(entries, _PURCHASE_OR_DELETE))
    loading_marker = _has_phrase(entries, _LOADING_MARKERS)

    if update_marker and not update_is_negative:
        if update_is_purchase:
            return _result(
                "attention",
                "需要人工处理：更新提示中出现购买/删除内容，不执行任何输入",
            )
        if update_is_installer_text:
            return _result(
                "unknown",
                "游戏前台出现系统安装器文案，不能据此推断安装器身份，不执行输入",
            )
        update_buttons = _action_buttons(entries, _UPDATE_ACTIONS)
        if len(update_buttons) == 1:
            label = update_buttons[0]["text"]
            return _result(
                "update_confirm",
                f"“明日方舟”强制更新/资源更新提示，唯一按钮为“{label}”",
                button=update_buttons[0],
            )
        if len(update_buttons) > 1:
            return _result(
                "unknown",
                "更新提示中存在多个“确认/确定/下载/更新”按钮，拒绝猜测",
            )
        # No actionable button.  A known loading marker or visible resource
        # progress can still prove an already-running startup phase; otherwise
        # an update sentence alone must not become a confirmation screen.
        if loading_marker:
            return _result(
                "loading",
                f"“明日方舟”启动加载中（{loading_marker}）{_progress_note(progress)}",
                progress=progress,
            )
        resource_marker = _has_phrase(entries, _DOWNLOAD_RESOURCE)
        if resource_marker and progress is not None:
            return _result(
                "download",
                f"“明日方舟”正在更新资源（{resource_marker}）{_progress_note(progress)}",
                progress=progress,
            )
        return _result(
            "unknown",
            "检测到更新提示，但缺少唯一可信的确认/下载按钮，不执行输入",
        )

    if loading_marker:
        return _result(
            "loading",
            f"“明日方舟”启动加载中（{loading_marker}）{_progress_note(progress)}",
            progress=progress,
        )

    resource_marker = _has_phrase(entries, _DOWNLOAD_RESOURCE)
    if resource_marker and progress is not None:
        return _result(
            "download",
            f"“明日方舟”正在更新资源（{resource_marker}）{_progress_note(progress)}",
            progress=progress,
        )

    login_marker = _first_exact(entries, _LOGIN_MARKERS)
    if login_marker:
        return _result(
            "login_start",
            f"“明日方舟”登录页开始入口（{login_marker}），不执行输入",
        )

    # The yellow START diamond is too small for full-frame OCR on this client.
    # Its resource-start page has two fixed header controls plus the version.
    # Only identify the page here; startup_controls additionally requires the
    # exact page anchors and a local high-confidence GameStart match for input.
    clear=any(e.compact=='清除缓存' and e.score>=.9 and 15<e.x<160 and 10<e.y<75 for e in entries)
    network=any(e.compact=='网络检测' and e.score>=.9 and 165<e.x<330 and 10<e.y<75 for e in entries)
    version=any(e.upper.startswith('VER') and e.score>=.8 and 15<e.x<300 and 60<e.y<100 for e in entries)
    if clear and network and version:
        return _result('login_start','确认游戏资源启动页，等待开始按钮双帧核验')

    # A stopped collection scan can leave its scrolled catalogue open. Its
    # three fixed progress tabs are independent of all moving chapter cards.
    collection_tabs=(('默认进度',680,795),('奖励进度',795,905),('完成进度',905,1020))
    if all(sum(e.compact==label and e.score>=.9 and left<e.x<right and 15<e.y<70
               for e in entries)==1 for label,left,right in collection_tabs):
        return _result('ready','乐章收录列表的三个固定进度控件已确认，可继续导航')

    if isinstance(page, str) and page in VERIFIED_PAGES:
        return _result(
            "ready",
            f"页面已由独立导航验证为{_PAGE_LABELS[page]}（{page}），可继续",
        )

    stage_starts = _action_buttons(entries, _STAGE_STARTS)
    stage_second = _first_exact(entries, _STAGE_SECOND_CONTROLS)
    if len(stage_starts) == 1 and stage_second:
        start_label = stage_starts[0]["text"]
        return _result(
            "ready",
            f"关卡详情双控件已确认（{stage_second} + {start_label}），不执行输入",
        )
    if len(stage_starts) > 1:
        return _result(
            "unknown",
            "检测到多个关卡开始按钮，拒绝猜测",
        )

    return _result(
        "unknown",
        "未识别到可信的启动界面证据（无已验证页面或双控件关卡详情），不执行输入",
    )


def classify(texts: Any, foreground: Any, page: Any = None) -> dict[str, Any]:
    """Classify one startup screen without any I/O or input side effect.

    ``texts`` is an iterable of OCR mappings with ``text``/``score``/``x``/``y``
    (and optionally ``box``).  ``foreground`` must be the exact Android package
    string.  ``page`` is only trusted when it is one of ``VERIFIED_PAGES``.
    """
    package = foreground.strip() if isinstance(foreground, str) else None
    if package not in ({GAME_PACKAGE} | INSTALLER_PACKAGES):
        if not package:
            reason = "前台包名为空，无法确认游戏或系统安装器，不执行任何输入"
        else:
            shown = package if len(package) <= 80 else package[:77] + "..."
            reason = f"前台包名“{shown}”不在支持范围，不执行任何输入"
        return _result("unknown", reason)

    entries = _clean_entries(texts)

    # On the real logged-in title screen these two bottom controls are always
    # visible. They are alternatives to the verified wake-up button, not an
    # account dialog. Never suppress password/verification/other login text.
    starts=[entry for entry in entries if entry.compact=='开始唤醒' and entry.score>=.9
            and 520<entry.x<760 and 440<entry.y<550]
    title_menu=(package==GAME_PACKAGE and len(starts)==1 and _has_exact(entries,('ARKNIGHTS',)))
    attention_entries=tuple(entry for entry in entries if not (
        title_menu and entry.compact in ('扫码登录','账号管理') and 450<entry.x<850 and 620<entry.y<715))
    attention = _attention_reason(attention_entries, check_currency=package==GAME_PACKAGE)
    if attention:
        return _result("attention", attention)

    if package in INSTALLER_PACKAGES:
        return _installer_result(entries)

    battle = _battle_reason(entries)
    if battle:
        return _result("battle", battle)

    settlement = _has_phrase(entries, _BATTLE_SETTLEMENT)
    if settlement:
        return _result("settlement", f"检测到结算界面（{settlement}），交由已有结算证据核验")

    return _game_result(entries, page)
