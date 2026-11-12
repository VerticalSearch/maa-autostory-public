"""Bounded startup supervision, using only the selected emulator.

No battle dispatch, account switching, process killing, APK paths, or desktop
input. The main worker owns both task/device locks before calling this module.
"""
import math
import re
import time

from .common import DATA, check_stop, log, now, settings, write_json
from .home_observation import navigation_regions,navigation_texts
from .startup_screens import classify
from . import workflow

GAME = 'com.hypergryph.arknights'
LAUNCHERS = frozenset(('com.mumu.launcher', 'com.mumu.launcher2',
                      'com.android.launcher', 'com.android.launcher3', 'app.lawnchair'))
RECOVERABLE = frozenset(('loading', 'download', 'login_start', 'update_confirm',
                         'install_confirm', 'installing', 'install_done', 'launcher'))


class StartupError(RuntimeError):
    """Startup failed before any new battle was dispatched."""


def _focused_package(text):
    # MuMu retains multiple displays, with stale mFocusedApp values for the
    # launcher and installer. Only a non-null mCurrentFocus is current evidence.
    focused=[]
    for line in text.splitlines():
        if 'mCurrentFocus=' not in line:continue
        match=re.search(r'\b([a-zA-Z][\w]*(?:\.[\w]+)+)/[\w.$]+',line)
        if match:focused.append(match.group(1))
    return focused[0] if len(focused)==1 else None


def foreground(device):
    raw=device.raw('-s',device.serial,'shell','dumpsys','window','displays',timeout=5)
    return _focused_package(raw.decode('utf-8','replace'))


def _read(device, vision):
    check_stop()
    before = foreground(device)
    image = device.shot()
    texts = navigation_texts(vision,image)
    after = foreground(device)
    page = navigation_regions(texts,image)[0]
    observed = classify(texts, after if before == after else None, page)
    if observed['state']=='login_start':
        from .startup_controls import start_button
        observed['button']=start_button(image,texts,vision)
    if before == after and after in LAUNCHERS:
        observed = dict(state='launcher', reason='模拟器桌面，准备启动明日方舟', progress=None, button=None)
    observed.update(foreground=after, texts=texts)
    return image, observed


def _pause(seconds):
    deadline = time.monotonic()+max(0, seconds)
    while time.monotonic() < deadline:
        check_stop()
        time.sleep(min(.1, max(0, deadline-time.monotonic())))


def _limits():
    options = settings().get('startup', {})
    if not isinstance(options, dict):
        options = {}
    result = {}
    for name, default, low, high in (('total_timeout',1200,30,3600),
                                    ('stall_timeout',180,15,600),
                                    ('unknown_timeout',15,3,60)):
        try:
            value = float(options.get(name, default))
            if not math.isfinite(value):raise ValueError()
            result[name] = max(low, min(high, value))
        except (ValueError, TypeError):
            result[name] = default
    return result


class _Supervisor:
    def __init__(self, device, vision, allow_launch, allow_settlement=False):
        self.device, self.vision, self.allow_launch = device, vision, allow_launch
        self.allow_settlement = allow_settlement
        self.limits = _limits()
        self.started = self.changed = time.monotonic()
        self.next_poll = 0
        self.state = None
        self.progress = None
        self.image = None
        self.observed = {}
        self.trace = []
        self.taps = 0
        self.last_tap_state = None
        self.core_runs = 0
        self.launches = 0
        self.button_label = None
        self.started_at = now()

    def record(self, status, reason=None):
        write_json(DATA/'startup.json', dict(at=now(), started=self.started_at,
            state=status, reason=reason or self.observed.get('reason'),
            screen=self.observed.get('state'), progress=self.observed.get('progress'),
            foreground=self.observed.get('foreground'), serial=self.device.serial,
            seconds=round(time.monotonic()-self.started, 1), actions=self.taps,
            observations=self.trace[-100:]))

    def observe(self):
        check_stop()
        self.image, current = _read(self.device, self.vision)
        self.observed = current
        at = time.monotonic()
        state, progress = current['state'], current.get('progress')
        label=(current.get('button') or {}).get('text')
        changed = state != self.state or label != self.button_label
        advanced = (progress is not None and (self.progress is None or progress > self.progress))
        if changed or advanced:
            self.changed = at
            self.trace.append(dict(at=now(), state=state, reason=current['reason'],
                                   progress=progress, foreground=current['foreground']))
            if changed:
                self.last_tap_state = None
                self.progress = None
                log('启动检查：'+current['reason'])
            if progress is not None:self.progress = progress
            self.state = state
            self.button_label = label
            self.record('running')
        workflow.update('starting', message='启动检查：'+current['reason'],
                        startup_state=state, startup_progress=progress)
        if at-self.started > self.limits['total_timeout']:
            raise StartupError('启动/更新超过总等待时限，已保留断点')
        limit = self.limits['unknown_timeout'] if state == 'unknown' else self.limits['stall_timeout']
        if at-self.changed > limit:
            raise StartupError(current['reason']+'；持续未取得可确认进展，已保留断点')
        if state in ('battle','attention'):
            raise StartupError(current['reason']+'；需要处理后继续，进度已保留')
        if state == 'settlement' and not self.allow_settlement:
            raise StartupError('游戏停留在结算页，请先完成结算处理再扫描；进度已保留')
        return current

    def click(self):
        state = self.observed['state']
        identity=(state,(self.observed.get('button') or {}).get('text'))
        if self.last_tap_state == identity:return
        if self.taps >= 6:raise StartupError('启动确认次数超过上限，已保留断点')
        # Re-read immediately before input; never replay coordinates from an
        # earlier frame or a dialog that has changed underneath us.
        expected = self.observed.get('button')
        _pause(.2)
        fresh = self.observe()
        button = fresh.get('button')
        if fresh['state'] != state or not expected or not button:return
        if (button['text'] != expected['text'] or
            abs(button['x']-expected['x']) > 12 or abs(button['y']-expected['y']) > 12):return
        check_stop()
        self.record('confirming')
        self.device.tap(button['x'], button['y'], .5)
        self.taps += 1
        self.last_tap_state = identity
        self.trace.append(dict(at=now(), state=state, tap=[button['x'],button['y']], label=button['text']))
        self.record('running')

    def launch(self):
        if self.launches:raise StartupError('启动游戏后又返回模拟器桌面，已保留现场')
        fresh=self.observe()
        if fresh['state']!='launcher':return
        check_stop()
        self.device.raw('-s',self.device.serial,'shell','monkey','-p',GAME,
                        '-c','android.intent.category.LAUNCHER','1')
        self.device.invalidate_frame()
        self.launches+=1
        self.trace.append(dict(at=now(),state='launcher',launch=GAME))
        self.record('running')

    def evidence(self, error):
        folder = DATA/'screens'/'startup'
        path = folder/str(time.time_ns())
        try:
            folder.mkdir(parents=True,exist_ok=True)
            image_path = None
            if self.image is not None:
                import cv2
                ok, pixels = cv2.imencode('.png', self.image)
                if ok:
                    image_path = str(path.with_suffix('.png'));pixels.tofile(image_path)
            write_json(path.with_suffix('.json'),dict(at=now(), error=str(error),
                observed=self.observed, observations=self.trace[-100:], screenshot=image_path))
            self.record('stopped' if isinstance(error,InterruptedError) else 'error', str(error))
        except Exception as evidence_error:
            log('启动诊断记录保存失败：'+str(evidence_error))


def ensure_ready(device, vision, core=None, *, allow_launch=True, allow_settlement=False):
    """Prepare the game once; preserve all account/scan/battle records.

    allow_launch=False is used for scan-current: do not navigate away from the
    user's selected map to satisfy readiness.
    """
    supervisor = _Supervisor(device, vision, allow_launch, allow_settlement)
    try:
        while True:
            screen = supervisor.observe()['state']
            if screen == 'ready' or (screen == 'settlement' and allow_settlement):
                _pause(.2)
                if supervisor.observe()['state'] == screen:
                    supervisor.record(screen)
                    return dict(state=screen, actions=supervisor.taps, core_runs=supervisor.core_runs,launches=supervisor.launches)
                continue
            if not allow_launch:
                raise StartupError('当前地图尚未就绪；扫描当前地图不会自动切换页面，进度已保留')
            if screen in ('install_confirm','install_done','update_confirm','login_start'):
                supervisor.click()
            elif screen=='launcher':
                supervisor.launch()
            _pause(2)
    except BaseException as error:
        supervisor.evidence(error)
        raise


def recover_scan_startup(nav):
    """Recover only an observed startup state, never replay a battle/campaign."""
    image, observed = _read(nav.d, nav.v)
    if observed['state'] not in RECOVERABLE:return False
    log('扫描遇到启动/更新页面，保留当前分区并尝试恢复：'+observed['reason'])
    ensure_ready(nav.d, nav.v, getattr(nav,'core',None))
    return True
