"""Bounded background navigation probe. No battle/formation/upgrade tasks."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from autostory.common import DATA, DeviceLock, arm_stop_threshold, now, write_json
from autostory.runtime_state import TaskLock
from autostory.device import Device, Vision
from autostory.navigation import Navigator


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--startup', action='store_true')
    parser.add_argument('--terminal', action='store_true')
    parser.add_argument('--label', default='before')
    args = parser.parse_args()
    if not args.label.replace('-', '').isalnum():
        raise ValueError('Invalid evidence label')
    task = TaskLock(DATA / 'alltasks.lock')
    if not task.acquire():
        raise RuntimeError('Another worker owns the task lock')
    arm_stop_threshold(time.time_ns())
    protected = ['progress.json', 'campaign-results.json', 'operators.json',
                 'attempt-history.json', 'scan-report.json']
    def hashes():
        return {name: hashlib.sha256((DATA/name).read_bytes()).hexdigest()
                for name in protected if (DATA/name).exists()}
    before = hashes()
    core = None
    report = {'at': now(), 'battles_started': 0, 'label': args.label}
    folder = DATA / 'screens' / ('nav0930-' + args.label)
    folder.mkdir(parents=True, exist_ok=True)
    try:
        with DeviceLock():
            device = Device()
            vision = Vision()
            nav = Navigator(device, vision)
            device.shot(folder/'before.png')
            if args.startup:
                from autostory.core import Core
                core = Core(device)
                core.run('StartUp', {'client_type': 'Official', 'start_game_enabled': True}, timeout=180)
            image = device.shot(folder/'ready.png')
            labels = vision.texts(image)
            report.update(device=device.serial, before_texts=labels)
            if args.terminal:
                started = time.monotonic()
                nav.terminal()
                report['terminal_seconds'] = round(time.monotonic()-started, 2)
            image = device.shot(folder/'after.png')
            report.update(ok=True, after_texts=vision.texts(image))
    except Exception as error:
        report.update(ok=False, error=repr(error))
    finally:
        if core:
            core.close()
        report['records_unchanged'] = before == hashes()
        report['screens'] = str(folder)
        write_json(DATA/('nav0930-'+args.label+'-live.json'), report)
        task.release()
    print(json.dumps({k: v for k, v in report.items() if not k.endswith('_texts')}, ensure_ascii=False))
    return 0 if report['ok'] and report['records_unchanged'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
