"""Shared helpers for the isolated AutoStory runtime coordination tests.

The sandbox is a temporary copy of the candidate files plus test doubles for
``device``/``core``/``controller``.  The candidate ``autostory.common`` is
copied so STOP-threshold behavior is tested unchanged.  No real Device,
Core, MAA DLL, ADB, game window, production server or production worker is
used.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

REPORT_DIR = Path(__file__).resolve().parents[1]
CANDIDATE_DIR = REPORT_DIR
PYTHON = sys.executable
CREATE_NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)

NAVIGATION_STUB = '''CHAPTERS = ['DV', 'CW']

class Navigator:
    def __init__(self, device, vision):
        self.device = device
        self.vision = vision
        self.core = None
    def enter(self, prefix):
        return None
    def scan(self, prefix):
        return []
'''

DEVICE_STUB = '''from pathlib import Path
import os

class Device:
    serial = 'test-device'
    def __init__(self):
        marker = os.environ.get('FAKE_DEVICE_MARKER')
        if marker:
            Path(marker).write_text('device-instantiated', encoding='utf-8')
    def shot(self, path):
        return None

class Vision:
    pass
'''

CORE_STUB = '''class Core:
    def __init__(self, device):
        self.device = device
    def refresh_box(self):
        return None
    def close(self):
        return None
'''

CONTROLLER_STUB = '''import json
import os
import time
from pathlib import Path

from autostory.common import DATA, now, read_json, check_stop, stop_requested

def _snapshot(tag):
    marker = os.environ.get('FAKE_CALL_MARKER')
    if not marker:
        return
    payload = {
        'tag': tag,
        'pid': os.getpid(),
        'status': read_json(DATA / 'status.json', None),
        'claim': read_json(DATA / 'worker.json', None),
        'stop': (DATA / 'STOP').exists(),
        'stop_requested': stop_requested(),
        'at': now(),
    }
    Path(marker).write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')

def build_plan(chapter):
    check_stop()
    _snapshot('plan')
    release = os.environ.get('FAKE_PLAN_RELEASE')
    if release:
        # Hold the fake operation until the HTTP conflict has been checked.
        # A bounded rendezvous makes the busy assertion independent of whether
        # STOP arrives before the worker's initial cancellation check.
        deadline = time.monotonic() + 15
        while not Path(release).exists():
            if time.monotonic() >= deadline:
                raise TimeoutError('fake plan release was not signalled')
            time.sleep(0.01)
    delay = float(os.environ.get('FAKE_PLAN_SECONDS', '0'))
    if delay:
        time.sleep(delay)
    if os.environ.get('FAKE_PLAN_FAIL') == '1':
        raise RuntimeError('fake plan failure')
    (DATA / 'plan-called.json').write_text(chapter, encoding='utf-8')

class Runner:
    def __init__(self, core, nav):
        self.core = core
        self.nav = nav
    def run(self, chapter):
        _snapshot('run')
        self.last_completion = json.loads(os.environ.get('FAKE_RUN_RESULT', '{"state":"done","message":"fixture completed"}'))
    def run_all(self, resume=False):
        self.run('ALL')
'''

HOLDER_STUB = '''import sys
import time
from pathlib import Path

project = Path(sys.argv[1])
data = Path(sys.argv[2])
ready = Path(sys.argv[3])
release = Path(sys.argv[4])
sys.path.insert(0, str(project))
from autostory.runtime_state import TaskLock, clear_claim, make_identity, write_claim

lock = TaskLock(data / 'alltasks.lock')
if not lock.acquire():
    ready.write_text('busy', encoding='utf-8')
    raise SystemExit(2)
identity = make_identity('held', 'DV')
write_claim(data, identity)
ready.write_text('locked', encoding='utf-8')
deadline = time.time() + 30
while not release.exists() and time.time() < deadline:
    time.sleep(0.02)
clear_claim(data, identity['run_id'])
lock.release()
'''


def make_project():
    """Create a temporary importable project and return its root Path."""
    tmp_parent = REPORT_DIR / '_tmp'
    tmp_parent.mkdir(exist_ok=True)
    root = tmp_parent / ('autostory-runtime-test-' + uuid.uuid4().hex)
    # Plain mkdir (not tempfile.mkdtemp) because the sandbox's mkdtemp ACL
    # prevents creating package subdirectories inside the new directory.
    root.mkdir()
    pkg = root / 'autostory'
    pkg.mkdir()
    shutil.copy2(CANDIDATE_DIR / 'autostory' / '__init__.py', pkg / '__init__.py')
    shutil.copy2(CANDIDATE_DIR / 'autostory' / 'common.py', pkg / 'common.py')
    (pkg / 'navigation.py').write_text(NAVIGATION_STUB, encoding='utf-8')
    (pkg / 'device.py').write_text(DEVICE_STUB, encoding='utf-8')
    (pkg / 'core.py').write_text(CORE_STUB, encoding='utf-8')
    (pkg / 'controller.py').write_text(CONTROLLER_STUB, encoding='utf-8')
    (pkg / 'startup.py').write_text('def ensure_ready(*args, **kwargs): return None\n', encoding='utf-8')
    (pkg / 'resource_preflight.py').write_text('def ensure_resources(*args, **kwargs): return {"status":"healthy","net_writes":0}\n', encoding='utf-8')
    shutil.copy2(CANDIDATE_DIR / 'worker.py', root / 'worker.py')
    (pkg / 'recovery.py').write_text('class PracticeBudgetExhausted(RuntimeError): pass\n', encoding='utf-8')
    shutil.copy2(CANDIDATE_DIR / 'autostory' / 'campaign.py', pkg / 'campaign.py')
    shutil.copy2(CANDIDATE_DIR / 'server.py', root / 'server.py')
    shutil.copy2(CANDIDATE_DIR / 'autostory' / 'runtime_state.py', pkg / 'runtime_state.py')
    for name in ('workflow.py','dashboard.py'):
        shutil.copy2(CANDIDATE_DIR / 'autostory' / name, pkg / name)
    (root / 'web').mkdir()
    (root / 'web' / 'index.html').write_text('<html>__TOKEN__</html>', encoding='utf-8')
    (root / 'settings.json').write_text(json.dumps({'port': 0}), encoding='utf-8')
    (root / 'runtime').mkdir()
    return root


def remove_project(root):
    root=Path(root).resolve()
    expected=(REPORT_DIR/'_tmp').resolve()
    if not root.is_relative_to(expected) or not root.name.startswith('autostory-runtime-test-'):
        raise ValueError('Refusing removal outside test sandbox')
    shutil.rmtree(root, ignore_errors=True)


def clean_runtime(root):
    runtime = root / 'runtime'
    for name in ('status.json', 'worker.json', 'STOP', 'worker.log', 'activity.log',
                 'alltasks.lock', 'plan-called.json', 'snapshot.json', 'device.marker',
                 'workflow.json','progress.json','plan.json','campaign-results.json'):
        path = runtime / name
        try:
            path.unlink()
        except FileNotFoundError:
            pass


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8-sig'))
    except FileNotFoundError:
        return default


def wait_until(predicate, timeout=8.0, interval=0.02):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


def wait_for(path, timeout=8.0):
    return wait_until(lambda: Path(path).exists(), timeout=timeout)


def start_worker(root, action='plan', chapter='DV', env=None, log_name='worker-test.log',
                 accepted_ns=None):
    """Run the candidate worker in its own process; capture output via a file."""
    merged = os.environ.copy()
    merged.update(env or {})
    log_path = root / 'runtime' / log_name
    log_path.parent.mkdir(parents=True, exist_ok=True)
    out = log_path.open('ab')
    cmd = [PYTHON, '-u', str(root / 'worker.py'), action, '--chapter', chapter]
    if accepted_ns is not None:
        cmd.extend(['--accepted-ns', str(accepted_ns)])
    proc = subprocess.Popen(
        cmd, cwd=root, env=merged, stdout=out, stderr=subprocess.STDOUT,
        creationflags=CREATE_NO_WINDOW,
    )
    proc._autostory_log = out  # closed by finish_worker
    return proc


def finish_worker(proc, timeout=30):
    try:
        code = proc.wait(timeout=timeout)
    finally:
        log = getattr(proc, '_autostory_log', None)
        if log is not None:
            log.close()
    return code


def run_worker(root, action='plan', chapter='DV', env=None, timeout=30, accepted_ns=None):
    proc = start_worker(root, action=action, chapter=chapter, env=env,
                        accepted_ns=accepted_ns)
    return finish_worker(proc, timeout=timeout)


def start_lock_holder(root):
    """Hold the all-task lock in a separate process (real msvcrt contention)."""
    holder = root / 'runtime' / '_holder.py'
    holder.write_text(HOLDER_STUB, encoding='utf-8')
    ready = root / 'runtime' / 'holder.ready'
    release = root / 'runtime' / 'holder.release'
    for path in (ready, release):
        try:
            path.unlink()
        except FileNotFoundError:
            pass
    out = (root / 'runtime' / 'holder.log').open('ab')
    proc = subprocess.Popen(
        [PYTHON, '-u', str(holder), str(root), str(root / 'runtime'), str(ready), str(release)],
        cwd=root, stdout=out, stderr=subprocess.STDOUT,
        creationflags=CREATE_NO_WINDOW,
    )
    proc._autostory_log = out
    if not wait_for(ready):
        finish_worker(proc)
        raise AssertionError('lock holder did not become ready')
    state = ready.read_text(encoding='utf-8')
    if state != 'locked':
        finish_worker(proc)
        raise AssertionError(f'lock holder state: {state}')
    return proc, release


def stop_lock_holder(proc, release):
    release.write_text('release', encoding='utf-8')
    return finish_worker(proc)


def start_sleeper():
    """Start a live, unrelated Python process for stale-PID tests."""
    return subprocess.Popen(
        [PYTHON, '-c', 'import time; time.sleep(30)'],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=CREATE_NO_WINDOW,
    )


def stop_process(proc):
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
