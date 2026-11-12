"""Locked background zone navigation and optional detail read; no battles."""
import argparse,hashlib,json,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser();parser.add_argument('--frozen',action='store_true')
parser.add_argument('--groups',default='NL-EX,NL-S,MN-EX')
parser.add_argument('--details',action='store_true');parser.add_argument('--raid',action='store_true');args=parser.parse_args()
if args.frozen:
    from frozen_logic_loader import install
    install('App-0.9.35',extra_modules=('worker',))
sys.path.insert(0,str(ROOT))
from autostory.common import DATA,DeviceLock,arm_stop_threshold,write_json,read_json,now
from autostory.runtime_state import TaskLock
from autostory.device import Device,Vision
from autostory.navigation import Navigator
from autostory.startup import _read
from autostory.story_zones import map_groups
from autostory.navigation import CHAPTERS
import autostory.navigation as navigation

task=TaskLock(DATA/'alltasks.lock')
if not task.acquire():raise RuntimeError('Another task owns emulator')
arm_stop_threshold(time.time_ns())
protected=('campaign-results.json','operators.json','attempt-history.json','scan-report.json','../settings.json')
def hashes():return {name:hashlib.sha256((DATA/name).read_bytes()).hexdigest() for name in protected}
before=hashes();progress_before=read_json(DATA/'progress.json',{})
folder=ROOT/'release/evidence/flow0935'/('frozen' if args.frozen else 'source')
folder.mkdir(parents=True,exist_ok=True)
report=dict(at=now(),frozen=args.frozen,loader=type(navigation.__spec__.loader).__name__,groups=[],details=args.details)
try:
    with DeviceLock():
        device=Device();vision=Vision();nav=Navigator(device,vision)
        device.shot(folder/'before.png')
        _,initial=_read(device,vision)
        report['initial']=initial
        if initial['state']!='ready':raise RuntimeError('Initial page not verified ready: '+initial['reason'])
        nav.home()
        for prefix in args.groups.split(','):
            started=time.monotonic();nav.enter(prefix)
            image=device.shot(folder/(prefix+'.png'));texts=vision.texts(image)
            row=dict(group=prefix,seconds=round(time.monotonic()-started,1),map_groups=sorted(map_groups(texts,CHAPTERS)),texts=texts)
            if args.details:
                code=CHAPTERS[prefix]['codes'][0]
                detail=nav.find(code,max_swipes=12)
                row['detail']={k:detail.get(k) for k in ('code','mode','state','at','evidence')}
                if args.raid:
                    nav.set_mode('raid');raid=nav.detail(code,'raid')
                    row['raid_detail']={k:raid.get(k) for k in ('code','mode','state','at','evidence')}
                nav.close_detail()
            report['groups'].append(row)
            print(json.dumps({k:v for k,v in row.items() if k!='texts'},ensure_ascii=False),flush=True)
        report['ok']=True
except Exception as error:report.update(ok=False,error=repr(error))
finally:
    report['account_and_scan_checkpoint_unchanged']=hashes()==before
    report['progress_keys_removed']=sorted(set(progress_before)-set(read_json(DATA/'progress.json',{})))
    report['ok']=report.get('ok',False) and report['account_and_scan_checkpoint_unchanged'] and not report['progress_keys_removed']
    write_json(DATA/('flow0935-'+('frozen' if args.frozen else 'source')+'-live.json'),report)
    task.release()
print(json.dumps({k:v for k,v in report.items() if k not in ('groups','initial')},ensure_ascii=False))
raise SystemExit(0 if report['ok'] else 1)
