"""Actual installed EXE scans, preserving the all-scan checkpoint and budgets."""
import hashlib,json,re,sys,time,urllib.request
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from autostory.common import DATA,read_json,write_json,now
from autostory.campaign import catalogue
BASE='http://127.0.0.1:18741';op=urllib.request.build_opener(urllib.request.ProxyHandler({}))
def get(path):
    with op.open(BASE+path,timeout=5) as r:return json.load(r)
health=get('/api/health');assert health['version']=='0.9.36' and health['frozen']
assert not get('/api/state')['running']
protected=('campaign-results.json','operators.json','attempt-history.json','scan-report.json','../settings.json')
def hashes():return {n:hashlib.sha256((DATA/n).read_bytes()).hexdigest() for n in protected}
before=hashes();progress=read_json(DATA/'progress.json',{});keys=set(progress)
with op.open(BASE+'/',timeout=5) as r:html=r.read().decode('utf-8')
token=re.search(r"const token='([^']+)'",html).group(1)
def post(path,data):
    req=urllib.request.Request(BASE+path,data=json.dumps(data).encode(),headers={'Content-Type':'application/json','X-AutoStory-Token':token})
    with op.open(req,timeout=5) as r:return json.load(r)
proof=dict(at=now(),version='0.9.36',health=health,groups=[],ok=False)
try:
    for group in ('CW-S','DV-S'):
        accepted=post('/api/action',{'action':'scan','chapter':group})
        write_json(DATA/'flow0936-native-active.json',dict(group=group,accepted=accepted))
        print(json.dumps(dict(group=group,accepted=accepted),ensure_ascii=False),flush=True)
        started=time.monotonic();last=None
        while time.monotonic()-started<300:
            time.sleep(2);state=get('/api/state');status=state['status']
            phase=(status.get('state'),state.get('workflow',{}).get('phase'))
            if phase!=last:print(json.dumps(dict(group=group,state=phase,elapsed=round(time.monotonic()-started))),flush=True);last=phase
            if status.get('pid')==accepted['pid'] and not state['running']:break
        else:
            post('/api/stop',{});raise RuntimeError('Installed scan exceeded deadline; requested normal stop')
        assert status.get('state')=='done',status
        db=read_json(DATA/'progress.json',{})
        expected=[k for k,s in catalogue()['stages'].items() if s['group']==group and s['mode'] in ('normal','raid')]
        records={k:db.get(k,{}) for k in expected}
        assert len(records)==(8 if group=='CW-S' else 2)
        for key,row in records.items():
            assert row.get('state')==('cleared' if group=='CW-S' else 'unknown'),(key,row)
            assert row.get('at','')>=status['started'] and row.get('at')!=progress.get(key,{}).get('at'),key
            assert row.get('evidence') and Path(row['evidence']).is_file(),key
        proof['groups'].append(dict(group=group,accepted=accepted,status=status,records=records))
    proof['ok']=True
except Exception as error:proof['error']=repr(error)
finally:
    proof['progress_keys_removed']=sorted(keys-set(read_json(DATA/'progress.json',{})))
    proof['account_and_checkpoint_unchanged']=before==hashes()
    proof['ok']=proof['ok'] and not proof['progress_keys_removed'] and proof['account_and_checkpoint_unchanged']
    write_json(DATA/'flow0936-installed-check.json',proof)
print(json.dumps({k:v for k,v in proof.items() if k not in ('health','groups')},ensure_ascii=False))
raise SystemExit(0 if proof['ok'] else 1)
