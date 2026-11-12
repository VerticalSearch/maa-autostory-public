from __future__ import annotations
import datetime as dt
import json
import os
from pathlib import Path
import threading
import sys
import tempfile
import time

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

ROOT = Path(os.environ.get('AUTOSTORY_ROOT') or (
    Path(sys.executable).resolve().parent.parent if getattr(sys,'frozen',False)
    else Path(__file__).resolve().parents[1])).resolve()
MAA = ROOT.parent
DATA = ROOT / 'runtime'
DATA.mkdir(exist_ok=True)
STOP = threading.Event()
_STOP_THRESHOLD_NS = None

class DeviceUnavailableError(ConnectionError):
    """Device-wide failure: do not misreport the remaining maps as unsupported."""


# A lost screenshot stream or a re-enumerated emulator can come back on its own.
# Configuration and authorisation problems cannot, and must stop the task.
TRANSIENT_DEVICE_MARKERS=('error: closed','device offline','device not found',
                          'no devices/emulators found','connection reset','protocol fault',
                          '模拟器连接或响应异常','截图恢复时模拟器未就绪')
FATAL_DEVICE_MARKERS=('不在线','未授权','unauthorized','无法唯一确定模拟器')


def is_transient_device_error(error):
    """True only when waiting for the same emulator may legitimately recover."""
    if not isinstance(error,DeviceUnavailableError):
        return False
    text=str(error)
    if any(marker in text for marker in FATAL_DEVICE_MARKERS):
        return False
    return any(marker in text for marker in TRANSIENT_DEVICE_MARKERS)

def worker_command(*args):
    if getattr(sys,'frozen',False):return [sys.executable,'--worker',*map(str,args)]
    return [sys.executable,'-u',str(ROOT/'worker.py'),*map(str,args)]

def now():
    return dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).isoformat(timespec='seconds')

def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8-sig'))
    except FileNotFoundError:
        return default

def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Serialize before touching disk, and give each writer its own file. The
    # former fixed .tmp name collided between workers and diagnostic processes.
    content = json.dumps(value, ensure_ascii=False, indent=2)
    tmp = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8',
                prefix=path.name+'.', suffix='.tmp', dir=path.parent, delete=False) as output:
            tmp = Path(output.name)
            output.write(content)
        # Windows may briefly deny rename while another writer/reader closes
        # the destination. Retry only sharing/access conflicts, at most 0.2s.
        for attempt in range(5):
            try:
                os.replace(tmp, path)
                break
            except PermissionError as error:
                if attempt == 4 or getattr(error,'winerror',None) not in (5,32,33):raise
                time.sleep(.02*(attempt+1))
    finally:
        if tmp is not None:
            try:tmp.unlink(missing_ok=True)
            except OSError:pass

def arm_stop_threshold(accepted_ns):
    """Ignore STOP files older than the accepted request.

    This lets an explicitly accepted new action reset an older STOP without
    deleting the file (deleting it would race with a concurrently written
    STOP).  A STOP written after ``accepted_ns`` is always honoured and is
    never removed by the worker.
    """
    global _STOP_THRESHOLD_NS
    _STOP_THRESHOLD_NS = int(accepted_ns) if accepted_ns is not None else None

def stop_requested():
    if STOP.is_set():
        return True
    path = DATA / 'STOP'
    try:
        mtime_ns = path.stat().st_mtime_ns
    except FileNotFoundError:
        return False
    except OSError:
        return True
    threshold = _STOP_THRESHOLD_NS
    return threshold is None or mtime_ns >= threshold

def check_stop():
    if stop_requested():
        raise InterruptedError('已停止；进度已保存')

def log(message):
    line = f'[{now()}] {message}'
    with (DATA / 'activity.log').open('a', encoding='utf-8') as f:
        f.write(line + '\n')
    print(line, flush=True)

def settings():
    return read_json(ROOT / 'settings.json', {})

_GUI_TAIL = 500_000
_GUI_LOCK = threading.Lock()
_GUI_CACHE = {'config': None, 'timers': False, 'log': None, 'offset': 0, 'running': False, 'mark': -1}


def _file_stamp(path):
    try:
        stat = Path(path).stat()
    except FileNotFoundError:
        return None
    # Identity of the file content as far as an append-only log is concerned.
    return (str(path), stat.st_mtime_ns, stat.st_size, getattr(stat, 'st_ino', 0))


def _gui_timers_enabled(path):
    stamp = _file_stamp(path)
    if stamp is None or stamp != _GUI_CACHE['config']:
        cfg = read_json(path, {})
        _GUI_CACHE['timers'] = any(t.get('IsEnabled') for t in cfg.get('Timers', {}).get('List', []))
        _GUI_CACHE['config'] = stamp
    return _GUI_CACHE['timers']


def _gui_running(path):
    """Last ``[RunningState]`` within gui.log's 500 KB tail, reading only bytes
    appended since the previous call. A truncated/replaced log is re-read."""
    stamp = _file_stamp(path)
    if stamp is None:
        _GUI_CACHE.update(log=None, offset=0, running=False, mark=-1)
        return False
    previous = _GUI_CACHE['log']
    if stamp == previous:
        return _GUI_CACHE['running']
    size = stamp[2]
    floor = max(0, size - _GUI_TAIL)
    grown = (previous is not None and previous[0] == stamp[0] and previous[3] == stamp[3]
             and previous[2] <= size and floor <= _GUI_CACHE['offset'] <= size)
    # Outside the incremental case behave exactly like a fresh tail read. A
    # state line that has since left the tail no longer counts, as before.
    start = _GUI_CACHE['offset'] if grown else floor
    running, mark = (_GUI_CACHE['running'], _GUI_CACHE['mark']) if grown else (False, -1)
    if mark < floor:
        running, mark = False, -1
    with Path(path).open('rb') as f:
        f.seek(start)
        data = f.read(size - start)
    # Parse the unfinished last line too, but re-read it next time from its start.
    cut = data.rfind(b'\n')
    end = len(data)
    for raw in reversed(data.splitlines(keepends=True)):
        end -= len(raw)
        line = raw.decode('utf-8', 'replace')
        if '[RunningState]' in line and 'Idle:' in line:
            running, mark = 'to false' in line, start + end
            break
    _GUI_CACHE.update(log=stamp, offset=start + cut + 1, running=running, mark=mark)
    return running


def assert_gui_idle():
    """Read only. Never stop, activate, or change the desktop MAA instance.

    Called before every input and on each MaaCore poll, so both files are
    re-read only when their size/mtime changes and gui.log only from the
    previously consumed offset.
    """
    with _GUI_LOCK:
        timers = _gui_timers_enabled(MAA / 'config/gui.new.json')
        running = _gui_running(MAA / 'debug/gui.log')
    if timers:
        raise RuntimeError('MAA 定时任务已启用；为避免争抢模拟器，请先关闭定时执行')
    if running:
        raise RuntimeError('MAA 正在运行任务，后台助手暂停，避免同时控制模拟器')

class DeviceLock:
    def __enter__(self):
        import msvcrt
        self.file = (DATA / 'device.lock').open('a+b')
        self.file.seek(0)
        if not self.file.read(1):
            self.file.write(b'0'); self.file.flush()
        self.file.seek(0)
        try:
            msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            self.file.close()
            raise RuntimeError('已有后台任务正在控制模拟器')
        try:
            assert_gui_idle()
        except Exception:
            self.__exit__()
            raise
        return self

    def __exit__(self, *args):
        import msvcrt
        self.file.seek(0)
        msvcrt.locking(self.file.fileno(), msvcrt.LK_UNLCK, 1)
        self.file.close()
