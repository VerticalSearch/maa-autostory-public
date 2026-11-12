"""Read both complete special groups on the emulator, under the normal locks."""
import argparse,hashlib,json,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
p=argparse.ArgumentParser();p.add_argument('--frozen',action='store_true');p.add_argument('--groups',default='CW-S,DV-S');args=p.parse_args()
if args.frozen:
    from frozen_logic_loader import install
    install('App-0.9.36',extra_modules=('worker',))
sys.path.insert(0,str(ROOT))
from autostory.common import DATA,DeviceLock,arm_stop_threshold,write_json,read_json,now
from autostory.runtime_state import TaskLock
from autostory.device import Device,Vision
from autostory.navigation import Navigator
from autostory.startup import _read
import autostory.navigation as navigation
task=TaskLock(DATA/'alltasks.lock');assert task.acquire(),'Another task owns emulator'
arm_stop_threshold(time.time_ns());kind='frozen' if args.frozen else 'source'
folder=ROOT/'release/evidence/flow0936'/kind;folder.mkdir(parents=True,exist_ok=True)
protected=('campaign-results.json','operators.json','attempt-history.json','scan-report.json','../settings.json')
def hashes():return {n:hashlib.sha256((DATA/n).read_bytes()).hexdigest() for n in protected}
before=hashes();progress_before=read_json(DATA/'progress.json',{})
report=dict(at=now(),frozen=args.frozen,loader=type(navigation.__spec__.loader).__name__,groups=[])
try:
    with DeviceLock():
        d=Device();v=Vision();nav=Navigator(d,v)
        d.shot(folder/'before.png');_,initial=_read(d,v);report['initial']=initial['state']
        assert initial['state']=='ready',initial['reason']
        for group in args.groups.split(','):
            started=time.monotonic();nav.enter(group);d.shot(folder/(group+'-map.png'))
            events=[];nav.on_scan_stage=lambda row:events.append(dict(row))
            result=nav.scan(group,include_raid=True,incremental=False)
            persisted=read_json(DATA/'progress.json',{})
            assert all(persisted[r['code']+':'+r['mode']]==json.loads(json.dumps(r)) for r in events)
            expected=8 if group=='CW-S' else 2
            assert len(events)==len(result)==expected,(group,len(events),len(result))
            assert all(r['state']==('cleared' if group=='CW-S' else 'unknown') for r in result)
            row=dict(group=group,seconds=round(time.monotonic()-started,1),rows=[{k:r.get(k) for k in ('code','mode','state','at','evidence','reason')} for r in result],callbacks=len(events))
            report['groups'].append(row);print(json.dumps(row,ensure_ascii=False),flush=True)
        nav.home();d.shot(folder/'home.png');report['returned_home']=True;report['ok']=True
except Exception as error:report.update(ok=False,error=repr(error))
finally:
    report['account_and_scan_checkpoint_unchanged']=hashes()==before
    report['progress_keys_removed']=sorted(set(progress_before)-set(read_json(DATA/'progress.json',{})))
    report['ok']=report.get('ok',False) and report['account_and_scan_checkpoint_unchanged'] and not report['progress_keys_removed']
    write_json(DATA/('flow0936-'+kind+'-live.json'),report);task.release()
print(json.dumps({k:v for k,v in report.items() if k!='groups'},ensure_ascii=False))
raise SystemExit(0 if report['ok'] else 1)
