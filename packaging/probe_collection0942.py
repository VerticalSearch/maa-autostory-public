"""Exclusive, navigation-only collection diagnostic using packaged logic.

This is a diagnostic harness, not a native campaign acceptance. It archives
the program's own observations; no external/manual tap decisions are made.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import time
import urllib.request
from unittest.mock import patch

import cv2

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument('--release', default='App-0.9.41')
parser.add_argument('--group', default='AD')
parser.add_argument('--label', default='before')
args = parser.parse_args()
if not args.label.replace('-', '').isalnum():
    raise ValueError('Invalid evidence label')
sys.path.insert(0, str(ROOT))
from frozen_logic_loader import install
install(args.release)
from autostory.common import DATA, DeviceLock, arm_stop_threshold, check_stop, now, write_json
from autostory.runtime_state import TaskLock
from autostory.device import Device, Vision
from autostory.navigation import Navigator

opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
with opener.open('http://127.0.0.1:18741/api/state', timeout=5) as response:
    if json.load(response)['running']:
        raise RuntimeError('A native worker is active')
task = TaskLock(DATA / 'alltasks.lock')
if not task.acquire():
    raise RuntimeError('Task owner exists')
arm_stop_threshold(time.time_ns())
folder = DATA / 'screens' / ('collection0942-' + args.label)
folder.mkdir(parents=True, exist_ok=False)
protected = ('campaign-results.json', 'attempt-history.json', 'progress.json',
             'operators.json', 'nav-hints.json', 'workflow.json', '../settings.json')
def hashes():
    return {name: hashlib.sha256((DATA / name).read_bytes()).hexdigest()
            for name in protected if (DATA / name).is_file()}
before = hashes()
report = dict(at=now(), release=args.release, group=args.group, source='packaged-logic diagnostic',
              native_campaign=False, battles_started=0, observations=[], ok=False)
started = time.monotonic()
try:
    with DeviceLock(), patch('autostory.nav_hints._path', return_value=folder / 'nav-hints.json'), \
            patch('autostory.workflow.update'):
        device = Device()
        vision = Vision()
        nav = Navigator(device, vision)
        observe = nav.observe
        def recorded_observe(*a, **kw):
            check_stop()
            if time.monotonic() - started > 480:
                raise TimeoutError('Diagnostic navigation time limit reached')
            image, texts = observe(*a, **kw)
            index = len(report['observations'])
            path = folder / f'{index:03d}.png'
            cv2.imencode('.png', image)[1].tofile(str(path))
            row = dict(at=now(), index=index, image=str(path), texts=texts,
                       settle=getattr(device, 'last_settle', None))
            write_json(path.with_suffix('.json'), row)
            report['observations'].append(dict(index=index, at=row['at'], image=str(path)))
            return image, texts
        nav.observe = recorded_observe
        nav.enter(args.group)
        report['ok'] = True
except Exception as error:
    report['error'] = repr(error)
finally:
    report['records_unchanged'] = before == hashes()
    report['elapsed_seconds'] = round(time.monotonic() - started, 2)
    write_json(folder / 'report.json', report)
    task.release()
print(json.dumps({k: v for k, v in report.items() if k != 'observations'}, ensure_ascii=False))
raise SystemExit(0 if report['ok'] and report['records_unchanged'] else 1)
