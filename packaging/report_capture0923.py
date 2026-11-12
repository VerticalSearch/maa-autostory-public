import hashlib,json,re,sys,urllib.request
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from autostory.common import DATA,read_json,write_json,now
from autostory.campaign import catalogue
from autostory.scan_progress import summarize_scan
def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest()
manifest=read_json(DATA/'package-capture0923-manifest.json')
frozen=read_json(DATA/'capture0923-frozen-check.json');live=read_json(DATA/'capture0923-live-proof.json')
assert manifest['ok'] and frozen['ok'] and live['ok']
assert digest(ROOT/'App-0.9.23/MAA自动推图助手.exe')==manifest['exe_sha256']
assert digest(ROOT/'web/index.html')==manifest['ui_sha256']==read_json(DATA/'package-scan0922-manifest.json')['ui_sha256']
for rel,sha in manifest['source_sha256'].items():assert digest(ROOT/rel)==sha
source=(DATA/'tests-capture0923-source.log').read_text('utf-8-sig');assert re.search(r'\nOK\s*$',source)
count=int(re.search(r'Ran (\d+) tests',source)[1])
deployment=read_json(DATA/'deployment-capture0923.json');assert deployment['RecordsUnchanged']
start=read_json(DATA/'capture0923-start.json')
assert digest(DATA/'campaign-results.json')==start['campaign_sha256']
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
def get(path):
    with opener.open('http://127.0.0.1:18741'+path,timeout=8) as response:return json.load(response)
health=get('/api/health');state=get('/api/state');assert health['version']=='0.9.23'
assert state['status']['action']=='scan-all'
summary=summarize_scan(catalogue(),read_json(DATA/'progress.json'))
snapshot={'at':now(),'health':health,'running':state['running'],'status':state['status'],'workflow':state['workflow'],
    'summary':{k:v for k,v in summary.items() if k!='groups'},'campaign_unchanged':True}
write_json(DATA/'capture0923-state.json',snapshot)
report=read_json(ROOT/'CORE_RELEASE_STATUS.json')
report.update(version='0.9.23',deployed_version='0.9.23',report='CAPTURE_0923.md',
    state='Recover one transient closed screenshot stream after same-device readiness proof; scan-only resumed',
    exe_sha256=manifest['exe_sha256'],ui_sha256=manifest['ui_sha256'],source_tests=count,frozen_tests=frozen['tests'],
    current_task=state['status'],latest_live_result='Fault-injected screenshot stream failure recovered on real emulator in0.44s. Installed scanner resumed chapter17; no new battle claimed.')
report['source_sha256'].update(manifest['source_sha256'])
report['capture0923']={'manifest':'runtime/package-capture0923-manifest.json','source':'runtime/tests-capture0923-source.log',
    'frozen':'runtime/capture0923-frozen-check.json','fault_injection_live':'runtime/capture0923-live-proof.json',
    'deployment':'runtime/deployment-capture0923.json','start':'runtime/capture0923-start.json','state':'runtime/capture0923-state.json',
    'delegation_conversation_id':'autostory-capture0923-e7a401','ui_unchanged_from':'0.9.22'}
report['notes']=['Transient screenshot error:closed gets at most one same-device probe/retry; input commands are never replayed',
    'Offline/unauthorized/repeated failure and user stop still propagate',
    'Screenshot recovery tested by fault injection followed by real same-emulator capture, not a natural recurrence',
    'Original budget, battle attempts and account records retained; scan only resumed',
    'UI unchanged from0.9.22; its72 browser checks were not rerun; original window retained',
    'Other frozen modules and runtime unchanged from0.9.22; release/ not modified']
write_json(ROOT/'CORE_RELEASE_STATUS.json',report)
p=ROOT/'README.md';text=p.read_text('utf-8-sig')
if not text.startswith('> **当前统一版本：0.9.23'):
    text=text.replace('> **当前统一版本：0.9.22','> **历史版本：0.9.22',1)
    p.write_text('> **当前统一版本：0.9.23（2026-11-01）**。修复一次ADB截图通道关闭就停止整轮的问题：仅截图可在原设备在线确认后重取一次，点击与滑动不重放。已完成故障注入后的真实模拟器截图恢复，后台从第17章断点续扫。详见 [截图中断恢复](CAPTURE_0923.md)。\n\n'+text,encoding='utf-8')
print(json.dumps(snapshot,ensure_ascii=False))
