"""Stop the current read-only scan normally and preserve evidence for 0.9.35."""
import json,re,shutil,sys,time,urllib.request
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from autostory.common import read_json,write_json,now
DATA=ROOT/'runtime';BASE='http://127.0.0.1:18741'
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
def get(path):
    with opener.open(BASE+path,timeout=5) as r:return json.load(r)
state=get('/api/state')
if state['running']:
    assert state['status']['action']=='scan-all','Do not interrupt a different task'
    with opener.open(BASE+'/',timeout=5) as r:html=r.read().decode('utf-8')
    token=re.search(r"const token='([^']+)'",html).group(1)
    req=urllib.request.Request(BASE+'/api/stop',data=b'{}',headers={'Content-Type':'application/json','X-AutoStory-Token':token})
    with opener.open(req,timeout=5) as r:json.load(r)
    deadline=time.monotonic()+45
    while time.monotonic()<deadline:
        state=get('/api/state')
        if not state['running']:break
        time.sleep(.5)
    assert not state['running'],'Worker did not finish normal stop'
backup=DATA/'backups/flow0935-before';backup.mkdir(parents=True,exist_ok=False)
for name in ('progress.json','campaign-results.json','attempt-history.json','scan-report.json','status.json','operators.json','nav-hints.json'):
    shutil.copy2(DATA/name,backup/name)
shutil.copy2(ROOT/'settings.json',backup/'settings.json')
source=ROOT/'_tmp/flow0935-before';source.mkdir(parents=True,exist_ok=False)
for name in ('autostory/navigation.py','autostory/story_zones.py','autostory/__init__.py','autostory/home_observation.py','autostory/scanning.py','README.md','CORE_RELEASE_STATUS.json'):
    target=source/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(ROOT/name,target)
evidence=ROOT/'release/evidence/flow0935';evidence.mkdir(parents=True,exist_ok=False)
for name in ('zone-NL-EX.png','zone-MN-TR.png','entry-IS.png'):
    shutil.copy2(DATA/'screens'/name,evidence/name)
shutil.copy2(ROOT/'release/evidence/flow0934/entry-IS.json',evidence/'entry-IS.json')
write_json(DATA/'flow0935-task.json',dict(version='0.9.35',status='implementing',started=now(),
    conversation_id='autostory-flow0935-01a195bb',previous_scan=state['status'],
    scope=['NL/MN icon zone navigation','IS multi-area navigation','packaged regression and live read-only validation']))
print(json.dumps(dict(stopped=True,group=state['scan_report']['active_group'],confirmed=state['scan_report']['summary']['confirmed']),ensure_ascii=False))
