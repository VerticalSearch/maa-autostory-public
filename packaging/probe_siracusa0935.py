"""Locked read-only IS navigation/detail acceptance, source or shipped code."""
import argparse,hashlib,json,shutil,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
p=argparse.ArgumentParser();p.add_argument('--frozen',action='store_true');p.add_argument('--codes',default='IS-1,IS-TR-1,IS-EX-1,IS-10,IS-S-1');p.add_argument('--raid',action='store_true');p.add_argument('--scan',action='store_true');a=p.parse_args()
if a.frozen:
    from frozen_logic_loader import install
    install('App-0.9.35')
sys.path.insert(0,str(ROOT))
from autostory.common import DATA,DeviceLock,arm_stop_threshold,write_json,read_json,now
from autostory.runtime_state import TaskLock
from autostory.device import Device,Vision
from autostory.navigation import Navigator
from autostory.campaign import stage_info
from autostory import siracusa
lock=TaskLock(DATA/'alltasks.lock');assert lock.acquire(),'Another task owns device'
arm_stop_threshold(time.time_ns())
protected=('campaign-results.json','operators.json','attempt-history.json','scan-report.json','../settings.json')
def hashes():return {n:hashlib.sha256((DATA/n).read_bytes()).hexdigest() for n in protected}
before=hashes();keys=set(read_json(DATA/'progress.json',{}));kind='frozen' if a.frozen else 'source'
folder=ROOT/'release/evidence/flow0935'/('is-'+kind);folder.mkdir(parents=True,exist_ok=True)
report=dict(at=now(),frozen=a.frozen,loader=type(siracusa.__spec__.loader).__name__,rows=[])
try:
 with DeviceLock():
    nav=Navigator(Device(),Vision());nav.close_detail();nav.enter('IS')
    for code in a.codes.split(','):
        started=time.monotonic();row=nav.find(code)
        assert row['code']==code and row['state'] in ('cleared','uncleared','partial','locked'),row
        shutil.copy2(row['evidence'],folder/(code+'.png'))
        item={k:row.get(k) for k in ('code','mode','state','at','evidence')};item['seconds']=round(time.monotonic()-started,1)
        report['rows'].append(item);print(json.dumps(item,ensure_ascii=False),flush=True)
        if a.raid and stage_info(code,'raid'):
            nav.set_mode('raid');row=nav.detail(code,'raid')
            assert row['state'] in ('cleared','uncleared','partial','locked'),row
            shutil.copy2(row['evidence'],folder/(code+'-raid.png'))
            report['rows'].append({k:row.get(k) for k in ('code','mode','state','at','evidence')})
        nav.close_detail()
        if a.scan and code=='IS-TR-1':
            callbacks=[];nav.on_scan_stage=callbacks.append
            scanned=nav.scan('IS-TR',incremental=False)
            nav.on_scan_stage=None
            assert len(scanned)==len(callbacks)==1 and callbacks[0]['code']=='IS-TR-1'
            assert callbacks[0]['mode']=='normal' and callbacks[0]['state'] in ('cleared','uncleared','partial','locked')
            report['scan_group']=dict(group='IS-TR',rows=len(scanned),callbacks=len(callbacks),
                persisted=read_json(DATA/'progress.json',{}).get('IS-TR-1:normal')==json.loads(json.dumps(callbacks[0])))
            assert report['scan_group']['persisted']
    report['ok']=True
except Exception as e:report.update(ok=False,error=repr(e))
finally:
 report['account_and_scan_checkpoint_unchanged']=before==hashes()
 report['progress_keys_removed']=sorted(keys-set(read_json(DATA/'progress.json',{})))
 report['ok']=report.get('ok',False) and report['account_and_scan_checkpoint_unchanged'] and not report['progress_keys_removed']
 write_json(DATA/('siracusa0935-'+kind+'-live.json'),report);lock.release()
print(json.dumps(report,ensure_ascii=False))
raise SystemExit(0 if report['ok'] else 1)
