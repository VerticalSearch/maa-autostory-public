"""Locked, no-battle live startup acceptance using source or shipped bytecode."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser();parser.add_argument('--source',action='store_true')
parser.add_argument('--home-launch',action='store_true')
parser.add_argument('--cold-launch',action='store_true');args=parser.parse_args()
if not args.source:
    from frozen_logic_loader import install
    install('App-0.9.31',extra_modules=('worker',))
sys.path.insert(0,str(ROOT))
from autostory.common import DATA, DeviceLock, arm_stop_threshold, now, write_json
from autostory.runtime_state import TaskLock
from autostory.device import Device, Vision
from autostory.startup import ensure_ready, _read

task=TaskLock(DATA/'alltasks.lock')
if not task.acquire():raise RuntimeError('Another worker owns the task lock')
arm_stop_threshold(time.time_ns())
FILES=('progress.json','campaign-results.json','operators.json','attempt-history.json',
       'scan-report.json','nav-hints.json','collection-index.json','../settings.json')
def hashes():
    return {name:hashlib.sha256((DATA/name).read_bytes()).hexdigest()
            if (DATA/name).exists() else None for name in FILES}
before=hashes();report=dict(at=now(),source=args.source,home_launch=args.home_launch,cold_launch=args.cold_launch,battles_started=0)
event_file=DATA/'core-events.ndjson'
event_offset=event_file.stat().st_size if event_file.exists() else 0
folder=DATA/'screens/startup0931-live';folder.mkdir(parents=True,exist_ok=True)
try:
    with DeviceLock():
        device=Device();vision=Vision()
        device.shot(folder/'before.png')
        _,observed=_read(device,vision)
        report['before']=observed
        if args.home_launch or args.cold_launch:
            if observed['state']!='ready':raise RuntimeError('Live cold-launch test requires an already verified non-battle game page')
            if args.cold_launch:
                if observed['foreground']!='com.hypergryph.arknights':raise RuntimeError('Unexpected foreground')
                device.raw('-s',device.serial,'shell','am','force-stop','com.hypergryph.arknights')
            else:
                device.raw('-s',device.serial,'shell','input','keyevent',3)
            device.invalidate_frame()
            time.sleep(1)
            _,report['launch_screen']=_read(device,vision)
            if report['launch_screen']['state']!='launcher':
                raise RuntimeError('Launcher was not reached; startup exercise not proven')
        report['startup']=ensure_ready(device,vision)
        device.shot(folder/'after.png')
        _,report['after']=_read(device,vision)
        report['ok']=report['after']['state']=='ready' and (not (args.home_launch or args.cold_launch) or report['startup']['core_runs']>0)
except Exception as error:
    report.update(ok=False,error=repr(error))
finally:
    events=[]
    if event_file.exists():
        with event_file.open('rb') as stream:
            stream.seek(event_offset)
            for line in stream:
                try:events.append(json.loads(line))
                except (ValueError,UnicodeDecodeError):pass
    chains=[row.get('taskchain') for row in events if row.get('msg')==10001]
    report['core_tasks_started']=chains
    report['battles_started']=sum(name in ('Fight','Copilot') for name in chains)
    report['records_unchanged']=before==hashes()
    report['ok']=report.get('ok',False) and report['records_unchanged'] and report['battles_started']==0
    write_json(DATA/'startup0931-live-check.json',report)
    task.release()
print(json.dumps({k:v for k,v in report.items() if k not in ('before','after')},ensure_ascii=False))
raise SystemExit(0 if report['ok'] else 1)
