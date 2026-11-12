"""All-task serialization and worker identity for AutoStory.

Standard library only.  This module deliberately does not import
``autostory.common`` so it can be tested without creating/using the real
runtime directory or touching the game controls.

Integration target: ``AutoStory/autostory/runtime_state.py``.
"""
from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

TASK_LOCK_NAME = 'alltasks.lock'
CLAIM_NAME = 'worker.json'
EXIT_BUSY = 3

_STILL_ACTIVE = 259
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_KERNEL32 = None


class TaskBusy(RuntimeError):
    """Another worker currently owns the all-task lock."""


def _now():
    import datetime as dt
    return dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).isoformat(timespec='seconds')


def new_run_id():
    return uuid.uuid4().hex


# --- process identity -------------------------------------------------------

def _kernel32():
    global _KERNEL32
    if _KERNEL32 is not None:
        return _KERNEL32
    import ctypes
    from ctypes import wintypes

    lib = ctypes.WinDLL('kernel32', use_last_error=True)
    lib.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    lib.OpenProcess.restype = wintypes.HANDLE
    lib.GetProcessTimes.argtypes = [
        wintypes.HANDLE, ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME), ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
    ]
    lib.GetProcessTimes.restype = wintypes.BOOL
    lib.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    lib.GetExitCodeProcess.restype = wintypes.BOOL
    lib.CloseHandle.argtypes = [wintypes.HANDLE]
    lib.CloseHandle.restype = wintypes.BOOL
    _KERNEL32 = lib
    return lib


def process_snapshot(pid):
    """Return ``{'pid': int, 'created': marker}`` for a live process, else None.

    The creation marker makes a stale status/PID resistant to PID reuse.
    ``os.kill(pid, 0)`` is intentionally not used on Windows: there it can
    terminate the process for signals other than the console events.
    """
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return None
    if pid <= 0:
        return None
    if os.name != 'nt':
        try:
            os.kill(pid, 0)
        except OSError:
            return None
        return {'pid': pid, 'created': None}

    import ctypes
    from ctypes import wintypes

    lib = _kernel32()
    handle = lib.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return None
    try:
        created = wintypes.FILETIME()
        exited = wintypes.FILETIME()
        kernel = wintypes.FILETIME()
        user = wintypes.FILETIME()
        if not lib.GetProcessTimes(
                handle, ctypes.byref(created), ctypes.byref(exited),
                ctypes.byref(kernel), ctypes.byref(user)):
            return None
        code = wintypes.DWORD()
        if not lib.GetExitCodeProcess(handle, ctypes.byref(code)):
            return None
        if code.value != _STILL_ACTIVE:
            return None
        marker = (created.dwHighDateTime << 32) | created.dwLowDateTime
        return {'pid': pid, 'created': marker}
    finally:
        lib.CloseHandle(handle)


def process_creation_marker(pid):
    snapshot = process_snapshot(pid)
    return snapshot.get('created') if snapshot else None


def make_identity(action, chapter, run_id=None, started=None):
    pid = os.getpid()
    return {
        'run_id': run_id or new_run_id(),
        'pid': pid,
        'pid_created': process_creation_marker(pid),
        'action': action,
        'chapter': chapter,
        'started': started or _now(),
    }


def identity_alive(identity):
    """True only when the recorded process still exists with the same birth marker."""
    if not isinstance(identity, dict):
        return False
    snapshot = process_snapshot(identity.get('pid'))
    if not snapshot:
        return False
    expected = identity.get('pid_created')
    if expected is None:
        return True
    try:
        return snapshot.get('created') is not None and int(expected) == int(snapshot['created'])
    except (TypeError, ValueError):
        return False


# --- all-task lock ----------------------------------------------------------

def _lock_handle(handle):
    if os.name == 'nt':
        import msvcrt
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock_handle(handle):
    if os.name == 'nt':
        import msvcrt
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


class TaskLock:
    """Non-blocking, one-byte file lock shared by server and CLI workers."""

    def __init__(self, path):
        self.path = Path(path)
        self._file = None

    def acquire(self):
        if self._file is not None:
            return True
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open('a+b')
        try:
            handle.seek(0)
            if not handle.read(1):
                handle.write(b'0')
                handle.flush()
            handle.seek(0)
            _lock_handle(handle)
        except OSError:
            handle.close()
            return False
        self._file = handle
        return True

    def release(self):
        handle, self._file = self._file, None
        if handle is None:
            return
        try:
            _unlock_handle(handle)
        except OSError:
            pass
        finally:
            handle.close()

    def __enter__(self):
        if not self.acquire():
            raise TaskBusy('已有后台任务正在运行')
        return self

    def __exit__(self, *args):
        self.release()
        return False


def task_lock_path(data_dir):
    return Path(data_dir) / TASK_LOCK_NAME


# --- claim / status reconciliation -----------------------------------------

def claim_path(data_dir):
    return Path(data_dir) / CLAIM_NAME


def _read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8-sig'))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return default


def _write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(tmp, path)


def write_claim(data_dir, identity):
    _write_json(claim_path(data_dir), identity)


def read_claim(data_dir):
    return _read_json(claim_path(data_dir), None)


def task_running(data_dir):
    """Read-only busy check: true only for a live claimed worker.

    The server intentionally does not acquire the all-task lock just to test
    it, so an HTTP request cannot make a starting worker lose the race.
    """
    claim = read_claim(data_dir)
    return bool(claim) and identity_alive(claim)


def clear_claim(data_dir, run_id=None):
    path = claim_path(data_dir)
    if run_id is not None:
        current = _read_json(path, None)
        if current is not None and current.get('run_id') != run_id:
            return False
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    except OSError:
        return False
    return True


def reconcile_status(status, data_dir):
    """Return ``(effective_status, running)`` for ``GET /api/state``.

    The live claim (written only after the all-task lock is owned) is the
    authority.  A status from a dead/unrelated PID is reported as
    ``interrupted`` instead of trusting a server-local ``Popen`` object.
    """
    result = dict(status) if isinstance(status, dict) else {'state': 'idle'}
    claim = read_claim(data_dir)
    claim_alive = bool(claim) and identity_alive(claim)
    if claim_alive:
        if claim.get('run_id') and result.get('run_id') == claim.get('run_id'):
            return result, result.get('state') == 'running'
        merged = dict(result)
        for key in ('run_id', 'pid', 'pid_created', 'action', 'chapter', 'started', 'accepted_ns'):
            if claim.get(key) is not None:
                merged[key] = claim[key]
        merged['state'] = 'running'
        return merged, True
    if result.get('state') == 'running':
        result['state'] = 'interrupted'
        result.setdefault('message', '后台任务已结束或运行标识不匹配')
    return result, result.get('state') == 'running'


def _claim_blocks(data_dir, run_id):
    """A live claim, or a dead claim for some *other* run, blocks persisting."""
    claim = read_claim(data_dir)
    if not claim:
        return False
    return identity_alive(claim) or claim.get('run_id') != run_id


def persist_interrupted(data_dir, observed):
    """Write a dead worker's ``running`` status back as ``interrupted``.

    ``observed`` is the on-disk status already judged dead.  The file is only
    replaced while it still holds that exact run and no live claim exists, and
    the dead claim of that same run is removed afterwards.  A worker starting
    concurrently writes its claim before its status, and ``reconcile_status``
    lets a live claim win, so the remaining check/replace window is harmless.
    Returns True when the file was updated.
    """
    if not isinstance(observed, dict) or observed.get('state') != 'running':
        return False
    run_id = observed.get('run_id')
    path = Path(data_dir) / 'status.json'
    if _claim_blocks(data_dir, run_id):
        return False
    current = _read_json(path, None)
    if not isinstance(current, dict) or current.get('state') != 'running':
        return False
    if current.get('run_id') != run_id or current.get('pid') != observed.get('pid'):
        return False
    updated = dict(current)
    updated.update(state='interrupted', interrupted_detected=_now(),
                   message=current.get('message') or '后台任务意外结束（进程已退出），进度保存在上次检查点')
    import tempfile
    try:
        fd, tmp = tempfile.mkstemp(prefix='status.json.', suffix='.tmp', dir=str(Path(data_dir)))
    except OSError:
        return False
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            stream.write(json.dumps(updated, ensure_ascii=False, indent=2))
        if _claim_blocks(data_dir, run_id):
            return False
        os.replace(tmp, path)
    except OSError:
        return False
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass
    if run_id:
        clear_claim(data_dir, run_id)
    return True
