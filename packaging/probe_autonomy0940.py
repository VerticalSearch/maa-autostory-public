"""Single-owner startup/navigation acceptance. No battle or budget reset."""
import argparse,hashlib,json,sys,time,urllib.request
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser()
parser.add_argument('--source',action='store_true')
parser.add_argument('--startup-only',action='store_true')
parser.add_argument('--cold-launch',action='store_true')
parser.add_argument('--sn-only',action='store_true')
args=parser.parse_args()
sys.path.insert(0,str(ROOT))
if not args.source:
    from frozen_logic_loader import install
    install('App-0.9.40',extra_modules=('worker',))
import autostory
from autostory.common import DATA,DeviceLock,arm_stop_threshold,now,write_json,check_stop
from autostory.runtime_state import TaskLock
from autostory.device import Device,Vision
from autostory.startup import ensure_ready,_read
from autostory.navigation import Navigator
from autostory.navigation_recovery import locate

opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
with opener.open('http://127.0.0.1:18741/api/state',timeout=5) as response:
    if json.load(response)['running']:raise RuntimeError('Worker active')
task=TaskLock(DATA/'alltasks.lock')
if not task.acquire():raise RuntimeError('Task busy')
arm_stop_threshold(time.time_ns())
FILES=('campaign-results.json','attempt-history.json','../settings.json')
def hashes():
    return {name:hashlib.sha256((DATA/name).read_bytes()).hexdigest() for name in FILES}
before=hashes()
folder=DATA/'screens/autonomy0940'/('source' if args.source else 'native')
folder.mkdir(parents=True,exist_ok=True)
report=dict(at=now(),version=autostory.__version__,source=args.source,cold_launch=args.cold_launch,steps=[],ok=False)
core=None
try:
    with DeviceLock():
        d=Device();v=Vision()
        _,observed=_read(d,v);report['before']=observed
        if args.cold_launch:
            if observed['state']!='ready' or observed['foreground']!='com.hypergryph.arknights':raise RuntimeError('Cold launch requires verified idle game page')
            d.raw('-s',d.serial,'shell','am','force-stop','com.hypergryph.arknights')
            d.invalidate_frame();time.sleep(1)
            if _read(d,v)[1]['state']!='launcher':raise RuntimeError('Launcher not confirmed')
        result=ensure_ready(d,v)
        report['steps'].append(dict(phase='startup',result=result,at=now()))
        d.shot(folder/'startup-ready.png');print(json.dumps(report['steps'][-1],ensure_ascii=False),flush=True)
        if not args.startup_only:
            from autostory.core import Core
            core=Core(d);nav=Navigator(d,v,core)
            stages=(('SN','SN-1','normal'),) if args.sn_only else (('5','5-8','raid'),('SN','SN-1','normal'))
            for group,code,mode in stages:
                check_stop()
                current=nav.observe()[1]
                if not (args.sn_only and any(row.get('text')=='格兰法洛' for row in current)
                        and any(row.get('text')=='自由探索' for row in current)):
                    nav.enter(group)
                row=locate(nav,code,mode)
                if row['code']!=code or row['mode']!=mode:raise RuntimeError('Wrong detail')
                d.shot(folder/f'{code}-{mode}.png')
                nav.close_detail()
                d.shot(folder/f'{code}-returned.png')
                report['steps'].append(dict(phase='enter-read-return',group=group,code=code,mode=mode,row=row,at=now()))
                print(json.dumps(report['steps'][-1],ensure_ascii=False),flush=True)
            nav.enter('5')
        report['after']=_read(d,v)[1]
        report['ok']=report['after']['state']=='ready'
except Exception as error:
    report['error']=repr(error)
finally:
    if core:core.close()
    report['budget_and_history_unchanged']=before==hashes()
    report['ok']=report['ok'] and report['budget_and_history_unchanged']
    write_json(DATA/('autonomy0940-source-live.json' if args.source else 'autonomy0940-native-live.json'),report)
    task.release()
print(json.dumps({key:value for key,value in report.items() if key not in ('before','after','steps')},ensure_ascii=False))
raise SystemExit(0 if report['ok'] else 1)
