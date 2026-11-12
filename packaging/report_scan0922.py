"""Record verified package and a timestamped live scan checkpoint without device input."""
import hashlib,json,re,sys,urllib.request
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from autostory.common import DATA,read_json,write_json,now
from autostory.campaign import catalogue
from autostory.scan_progress import summarize_scan
def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()
manifest=read_json(DATA/'package-scan0922-manifest.json')
frozen=read_json(DATA/'scan0922-frozen-check.json');browser=read_json(DATA/'web0922-browser-audit.json')
source=(DATA/'tests-scan0922-source.log').read_text('utf-8-sig')
assert manifest['ok'] and frozen['ok'] and browser['ok'] and re.search(r'\nOK\s*$',source)
source_count=int(re.search(r'Ran (\d+) tests',source)[1])
assert digest(ROOT/'App-0.9.22/MAA自动推图助手.exe')==manifest['exe_sha256']
assert digest(ROOT/'web/index.html')==manifest['ui_sha256']
for rel,value in manifest['source_sha256'].items():assert digest(ROOT/rel)==value,rel
deployment=read_json(DATA/'deployment-scan0922.json');assert deployment['RecordsUnchanged']
navigation=read_json(DATA/'scan0922-live.json');assert navigation['verified'] and navigation['budget_unchanged']
start=read_json(DATA/'scan0922-start.json');assert start['action']=='scan-all'
resume=read_json(DATA/'scan0922-resume-proof.json');assert resume['ok'] and resume['progress_preserved'] and resume['campaign_unchanged']
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
def get(path):
    with opener.open('http://127.0.0.1:18741'+path,timeout=10) as response:return json.load(response)
health=get('/api/health');state=get('/api/state')
assert health['version']=='0.9.22' and health['frozen']
assert state['status']['action']=='scan-all'
budget_unchanged=digest(DATA/'campaign-results.json')==start['campaign_sha256'];assert budget_unchanged
book=catalogue();progress=read_json(DATA/'progress.json');summary=summarize_scan(book,progress)
initial=summarize_scan(book,start['initial_progress'])
changed=[key for key,row in progress.items() if row!=start['initial_progress'].get(key)]
events=[]
for line in (DATA/'core-events.ndjson').read_text('utf-8-sig').splitlines():
    event=json.loads(line)
    if event.get('time','')>=start['at']:events.append(event)
chains=sorted({event.get('taskchain','') for event in events if event.get('taskchain')})
assert set(chains)<= {'Custom'},chains
counters={key:start['budget'][key] for key in ('sanity','count','practice_count','limit','practice_limit')}
snapshot={'at':now(),'health':health,'running':state['running'],'status':state['status'],'workflow':state['workflow'],
    'initial_confirmed':initial['confirmed'],'summary':{key:value for key,value in summary.items() if key!='groups'},
    'new_confirmed':summary['confirmed']-initial['confirmed'],'changed_keys':changed,'budget':counters,
    'campaign_file_unchanged':budget_unchanged,'maa_taskchains_since_start':chains,
    'scan_report':{key:value for key,value in read_json(DATA/'scan-report.json').items() if key!='summary'}}
write_json(DATA/'scan0922-state.json',snapshot)
report=read_json(ROOT/'CORE_RELEASE_STATUS.json')
report.update(version='0.9.22',deployed_version='0.9.22',report='SCAN_0922.md',
    state='Unified resumable scan-only workflow deployed; live coverage is timestamped and incomplete',
    exe_sha256=manifest['exe_sha256'],ui_sha256=manifest['ui_sha256'],source_tests=source_count,
    frozen_tests=frozen['tests'],browser_checks=browser['passed'],current_task=snapshot['status'],current_budget=counters,
    latest_live_result=f"Map16 motion confirmed in 2.16 seconds; source navigation16/0/1 verified. Installed scanner currently confirms {summary['confirmed']}/{summary['total']} normal/raid records; {summary['unsupported']} other difficulties are not adapted.")
report['source_sha256'].update(manifest['source_sha256'])
report['scan0922']={'manifest':'runtime/package-scan0922-manifest.json','source_tests':'runtime/tests-scan0922-source.log',
    'frozen_tests':'runtime/scan0922-frozen-check.json','browser_tests':'runtime/web0922-browser-audit.json',
    'deployment':'runtime/deployment-scan0922.json','map_replay':'runtime/scan0922/replay.json',
    'source_navigation_live':'runtime/scan0922-live.json','frozen_scan_start':'runtime/scan0922-start.json',
    'installed_stop_resume':'runtime/scan0922-resume-proof.json',
    'current_checkpoint':'runtime/scan0922-state.json','uncleared':'runtime/uncleared.csv',
    'delegation':'runtime/scan0922-delegation.json','summary':snapshot['summary']}
report['notes']=['One desktop entry retains the wallpaper Shu icon; existing window was not closed; release/ unchanged',
    'Only verified map-label anchors use native-resolution subpixel motion; fixed controls retain the existing gate',
    'Every stage record is saved before its scan callback; interruption resumes missing normal/raid keys',
    'Unseen is not uncleared; other difficulties remain explicit and prevent claiming complete coverage',
    'This live task only scans. Campaign budget107, five formal and eighteen practice attempts are unchanged',
    '438 release-verifier checks include6 source child-process lock checks; the scan dispatcher itself is loaded from the exact packaged worker bytecode',
    'No new combat result or all-stage automation claim is made']
write_json(ROOT/'CORE_RELEASE_STATUS.json',report)
readme=ROOT/'README.md';text=readme.read_text('utf-8-sig')
if not text.startswith('> **当前统一版本：0.9.22'):
    text=text.replace('> **当前统一版本：0.9.21','> **历史版本：0.9.21',1)
    text='> **当前统一版本：0.9.22（2026-11-01）**。修复主线地图缓慢漂移被误判为加载、导致扫描停止的问题；增加直接的“扫描未通关关卡”入口、逐关保存、断点续扫和未通关清单。523项源码、438项打包验证检查、72项浏览器检查通过；已启动安装版的后台只扫描任务。普通/突袭共1,646项，另有231项特殊难度待适配；当前没有全部扫描完成。详见 [扫描稳定性与断点续扫](SCAN_0922.md)。\n\n'+text
readme.write_text(text,encoding='utf-8')
print(json.dumps({key:snapshot[key] for key in ('at','running','workflow','summary','new_confirmed','campaign_file_unchanged','maa_taskchains_since_start')},ensure_ascii=False))
