"""Resume the saved scan through the installed service and record evidence."""
import hashlib
import json
from pathlib import Path
import re
import sys
import time
import urllib.request

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from autostory.common import read_json,write_json,now
DATA=ROOT/'runtime'
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
BASE='http://127.0.0.1:18741'
def get(path):
    with opener.open(BASE+path,timeout=5) as response:return json.load(response)
def digest(name):return hashlib.sha256((DATA/name).read_bytes()).hexdigest()

health=get('/api/health');state=get('/api/state')
assert health['version']=='0.9.36'
assert not state['running'],'Task already running; do not create a duplicate'
before=state['scan_report'];prior_group=before['active_group']
progress_before=read_json(DATA/'progress.json',{})
campaign_before=digest('campaign-results.json')
logfile=DATA/'activity.log';offset=logfile.stat().st_size if logfile.exists() else 0
with opener.open(BASE+'/',timeout=5) as response:html=response.read().decode('utf-8')
token=re.search(r"const token='([^']+)'",html).group(1)
request=urllib.request.Request(BASE+'/api/action',
    data=json.dumps(dict(action='scan-all',chapter='ALL')).encode(),
    headers={'Content-Type':'application/json','X-AutoStory-Token':token})
with opener.open(request,timeout=5) as response:accepted=json.load(response)
started=time.monotonic();last_phase=None;ok=False
while time.monotonic()-started<150:
    time.sleep(2)
    state=get('/api/state');status=state['status'];scan=state['scan_report']
    startup=read_json(DATA/'startup.json',{})
    workflow=state.get('workflow',{})
    phase=(status.get('state'),workflow.get('phase'),scan.get('active_group'),scan['summary']['confirmed'])
    if phase!=last_phase:
        print(json.dumps(dict(status=phase,elapsed=round(time.monotonic()-started)),ensure_ascii=False),flush=True)
        last_phase=phase
    with logfile.open('rb') as stream:
        stream.seek(offset);new_log=stream.read().decode('utf-8','replace')
    resumed=f'从上次中断的分区 {prior_group} 继续扫描' in new_log
    if status.get('pid')!=accepted['pid']:continue
    if not state['running']:break
    if time.monotonic()-started>=15 and startup.get('state')=='ready' and (
            resumed or scan['summary']['confirmed']>before['summary']['confirmed']):
        ok=True;break

progress_after=read_json(DATA/'progress.json',{})
missing=[key for key in progress_before if key not in progress_after]
campaign_unchanged=campaign_before==digest('campaign-results.json')
evidence=[line for line in new_log.splitlines() if any(word in line for word in
    ('启动检查：','从上次中断的分区','仅扫描','已保存','继续扫描'))][-30:]
report=dict(at=now(),version=health['version'],ok=ok and not missing and campaign_unchanged,
    accepted=accepted,status=state['status'],running=state['running'],startup=startup,
    previous_group=prior_group,current_group=scan['active_group'],resumed_group_log=resumed,
    confirmed_before=before['summary']['confirmed'],confirmed_after=scan['summary']['confirmed'],
    total=scan['summary']['total'],progress_keys_removed=missing,campaign_records_unchanged=campaign_unchanged,
    evidence=evidence,workflow=state.get('workflow',{}))
write_json(DATA/'flow0936-resume-check.json',report)
print(json.dumps({key:value for key,value in report.items() if key not in ('startup','workflow','evidence')},ensure_ascii=False))
raise SystemExit(0 if report['ok'] else 1)
