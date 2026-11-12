"""Run one real installed EXE scan via its service; preserve all-scan checkpoint."""
import hashlib,json,re,sys,time,urllib.request
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from autostory.common import DATA,read_json,write_json,now
BASE='http://127.0.0.1:18741';op=urllib.request.build_opener(urllib.request.ProxyHandler({}))
def get(path):
    with op.open(BASE+path,timeout=5) as r:return json.load(r)
health=get('/api/health');assert health['version']=='0.9.35' and health['frozen']
assert not get('/api/state')['running']
protected=('campaign-results.json','operators.json','attempt-history.json','scan-report.json','../settings.json')
def hashes():return {n:hashlib.sha256((DATA/n).read_bytes()).hexdigest() for n in protected}
before=hashes();progress=read_json(DATA/'progress.json',{});keys=set(progress)
old_at=progress.get('IS-TR-1:normal',{}).get('at')
log=DATA/'activity.log';offset=log.stat().st_size
with op.open(BASE+'/',timeout=5) as r:html=r.read().decode('utf-8')
token=re.search(r"const token='([^']+)'",html).group(1)
def post(path,data):
    request=urllib.request.Request(BASE+path,data=json.dumps(data).encode(),
        headers={'Content-Type':'application/json','X-AutoStory-Token':token})
    with op.open(request,timeout=5) as r:return json.load(r)
accepted=post('/api/action',{'action':'scan','chapter':'IS-TR'})
started=time.monotonic();last=None
while time.monotonic()-started<240:
    time.sleep(2);state=get('/api/state');status=state['status']
    phase=(status.get('state'),state.get('workflow',{}).get('phase'))
    if phase!=last:print(json.dumps(dict(state=phase,elapsed=round(time.monotonic()-started))),flush=True);last=phase
    if status.get('pid')==accepted['pid'] and not state['running']:break
else:
    post('/api/stop',{})
    raise RuntimeError('Installed scan exceeded acceptance deadline; requested normal stop')
after=read_json(DATA/'progress.json',{});record=after.get('IS-TR-1:normal',{})
with log.open('rb') as stream:stream.seek(offset);new_log=stream.read().decode('utf-8')
proof=dict(at=now(),version='0.9.35',health=health,accepted=accepted,status=status,
    record=record,progress_keys_removed=sorted(keys-set(after)),account_and_checkpoint_unchanged=before==hashes(),
    actual_group_scan_log='IS-TR 建筑地图扫描结束' in new_log,
    startup=read_json(DATA/'startup.json',{}))
proof['ok']=(status.get('state')=='done' and status.get('pid')==accepted['pid'] and
    record.get('code')=='IS-TR-1' and record.get('mode')=='normal' and
    record.get('state') in ('cleared','uncleared','partial','locked') and
    record.get('at')!=old_at and record.get('at','')>=status.get('started','') and
    proof['actual_group_scan_log'] and not proof['progress_keys_removed'] and proof['account_and_checkpoint_unchanged'])
write_json(DATA/'flow0935-installed-is-check.json',proof)
print(json.dumps({k:v for k,v in proof.items() if k not in ('record','startup')},ensure_ascii=False))
raise SystemExit(0 if proof['ok'] else 1)
