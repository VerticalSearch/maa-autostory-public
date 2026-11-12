"""One native resume-all request, preserving budget/history; optional cold fixture."""
import argparse,hashlib,json,re,shutil,sys,time,urllib.request
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from autostory.common import DATA,DeviceLock,arm_stop_threshold,now,read_json,write_json
from autostory.runtime_state import TaskLock
parser=argparse.ArgumentParser();parser.add_argument('--cold-fixture',action='store_true');args=parser.parse_args()
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}));BASE='http://127.0.0.1:18741'
def get(path):
    with opener.open(BASE+path,timeout=5) as response:return json.load(response)
health=get('/api/health');state=get('/api/state')
assert health['version']=='0.9.42' and Path(health['root'])==ROOT
assert Path(health['executable'])==ROOT/'App-0.9.42/MAA自动推图助手.exe'
assert not state['running'],'Already running; no duplicate request'
budget=read_json(DATA/'campaign-results.json',{})['budget']
assert (budget['sanity'],budget['count'],budget['practice_count'])==(17,10,31)
snapshot=ROOT/'release/evidence/autonomy0942/before-campaign'
snapshot.mkdir(parents=True,exist_ok=False)
names=('campaign-results.json','attempt-history.json','progress.json','status.json','workflow.json','operators.json')
for name in names:shutil.copy2(DATA/name,snapshot/name)
shutil.copy2(ROOT/'settings.json',snapshot/'settings.json')
if args.cold_fixture:
    from autostory.device import Device,Vision
    from autostory.startup import _read
    task=TaskLock(DATA/'alltasks.lock');assert task.acquire()
    arm_stop_threshold(time.time_ns())
    try:
        with DeviceLock():
            device=Device();vision=Vision();_,before=_read(device,vision)
            assert before['state']=='ready' and before['foreground']=='com.hypergryph.arknights'
            device.raw('-s',device.serial,'shell','am','force-stop','com.hypergryph.arknights')
            device.invalidate_frame();time.sleep(1)
            _,after=_read(device,vision)
            assert after['state']=='launcher'
            write_json(snapshot/'cold-fixture.json',dict(at=now(),before=before,after=after,setup_only=True))
    finally:task.release()
assert not get('/api/state')['running']
with opener.open(BASE+'/',timeout=5) as response:html=response.read().decode('utf-8')
token=re.search(r"const token='([^']+)'",html).group(1)
request=urllib.request.Request(BASE+'/api/action',data=json.dumps(dict(action='resume-all',chapter='ALL')).encode(),
    headers={'Content-Type':'application/json','X-AutoStory-Token':token})
with opener.open(request,timeout=5) as response:accepted=json.load(response)
record=dict(at=now(),health=health,action='resume-all',accepted=accepted,starting_budget=budget,cold_fixture=args.cold_fixture)
with (DATA/'autonomy0942-jobs.ndjson').open('a',encoding='utf-8') as stream:stream.write(json.dumps(record,ensure_ascii=False)+'\n')
task=read_json(DATA/'autonomy0942-task.json',{})
task.update(status='native_campaign_running',accepted=accepted,starting_budget=budget,monitor='pending',previous_navigation_probe='runtime/screens/collection0942-before/report.json',reported=[])
write_json(DATA/'autonomy0942-task.json',task)
print(json.dumps({key:record[key] for key in ('at','action','accepted','cold_fixture')},ensure_ascii=False))
