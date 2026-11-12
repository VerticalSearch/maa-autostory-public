"""Preserve the stopped scan and source before the special-page fixes."""
import json, shutil, sys, urllib.request
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from autostory.common import DATA, now, write_json
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
with opener.open('http://127.0.0.1:18741/api/state',timeout=5) as r:state=json.load(r)
assert not state['running'],'Do not interrupt a live task'
backup=DATA/'backups/flow0936-before';backup.mkdir(parents=True,exist_ok=False)
for name in ('progress.json','campaign-results.json','attempt-history.json','scan-report.json','status.json','operators.json','nav-hints.json'):
    shutil.copy2(DATA/name,backup/name)
shutil.copy2(ROOT/'settings.json',backup/'settings.json')
source=ROOT/'_tmp/flow0936-before';source.mkdir(parents=True,exist_ok=False)
shutil.copytree(ROOT/'autostory',source/'autostory',ignore=shutil.ignore_patterns('__pycache__'))
for name in ('README.md','CORE_RELEASE_STATUS.json'):shutil.copy2(ROOT/name,source/name)
evidence=ROOT/'release/evidence/flow0936';evidence.mkdir(parents=True,exist_ok=False)
for key,label in (('1793522433004089700','dv-special'),('1793522436200163700','dv-back-failed'),('1793522299663675500','cw-morse')):
    for ext in ('.json','.png'):shutil.copy2(DATA/'screens/state-timeouts'/(key+ext),evidence/(label+ext))
write_json(DATA/'flow0936-task.json',dict(version='0.9.36',status='implementing',started=now(),conversation_id='autostory-flow0936-01a195bb',previous_scan=state['status'],scope=['DV special panel identity and return','CW Morse-map navigation','source/frozen/native scan validation']))
print(json.dumps(dict(backup=str(backup),group=state['scan_report']['active_group'],confirmed=state['scan_report']['summary']['confirmed'])))
