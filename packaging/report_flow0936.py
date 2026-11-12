"""Seal the release only after source, shipped bytecode and native EXE checks."""
import hashlib,json,shutil,sys,urllib.request
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from autostory.common import DATA,read_json,write_json,now
def get(name):return read_json(DATA/name,{})
def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()
manifest=get('package-flow0936-manifest.json')
names=('flow0936-source-check.json','flow0936-frozen-check.json','flow0936-source-live.json',
       'flow0936-frozen-live.json','flow0936-installed-check.json','flow0936-resume-check.json')
checks={n:get(n) for n in names}
assert manifest.get('ok') and manifest['version']=='0.9.36'
assert all(r.get('ok') for r in checks.values()),{n:r.get('ok') for n,r in checks.items()}
for relative,value in manifest['source_sha256'].items():assert digest(ROOT/relative)==value,relative
for relative,value in manifest['asset_sha256'].items():assert digest(ROOT/relative)==value,relative
assert digest(ROOT/'web/index.html')==manifest['ui_sha256']
exe=ROOT/'App-0.9.36/MAA自动推图助手.exe';assert digest(exe)==manifest['exe_sha256']
bundle=get('package-integration.json');deployment=get('deployment-flow0936.json')
assert bundle.get('ok') and bundle['health']['version']=='0.9.36'
assert bundle['parallel_launch_exit_codes']==[0,0] and bundle['worker_status']['state']=='done'
assert bundle['hidden_ui']['ok'] and bundle['hidden_ui']['renderer']=='WebView2'
assert deployment['Version']=='0.9.36' and deployment['RecordsUnchanged']
assert Path(deployment['Health']['executable'])==exe and Path(deployment['Shortcut']['Target'])==exe
native_process=get('flow0936-installed-worker-process.json')
assert Path(native_process['executable'])==exe and '--worker scan --chapter CW-S' in native_process['command']
assert native_process['pid']==checks['flow0936-installed-check.json']['groups'][0]['accepted']['pid']
dv_process=get('flow0936-installed-dv-worker-process.json')
assert Path(dv_process['executable'])==exe and '--worker scan --chapter DV-S' in dv_process['command']
assert dv_process['pid']==checks['flow0936-installed-check.json']['groups'][1]['accepted']['pid']
for kind in ('source','frozen'):
    report=checks[f'flow0936-{kind}-live.json']
    assert report['returned_home'] and report['account_and_scan_checkpoint_unchanged'] and not report['progress_keys_removed']
    groups={r['group']:r for r in report['groups']};assert set(groups)=={'CW-S','DV-S'}
    assert len(groups['CW-S']['rows'])==8 and all(r['state']=='cleared' for r in groups['CW-S']['rows'])
    assert len(groups['DV-S']['rows'])==2 and all(r['state']=='unknown' for r in groups['DV-S']['rows'])
    if kind=='frozen':assert report['loader']=='FrozenLoader'
for name in ('campaign-results.json','attempt-history.json','operators.json'):
    assert digest(DATA/name)==digest(DATA/'backups/flow0936-before'/name),name
assert digest(ROOT/'settings.json')==digest(DATA/'backups/flow0936-before/settings.json')
assert set(read_json(DATA/'backups/flow0936-before/progress.json',{}))<=set(read_json(DATA/'progress.json',{}))
op=urllib.request.build_opener(urllib.request.ProxyHandler({}))
with op.open('http://127.0.0.1:18741/api/health',timeout=5) as r:health=json.load(r)
with op.open('http://127.0.0.1:18741/api/state',timeout=5) as r:state=json.load(r)
assert health['version']=='0.9.36' and health['frozen'] and Path(health['executable'])==exe
summary={k:v for k,v in state['scan_report']['summary'].items() if k!='groups'}
proof=dict(at=now(),version='0.9.36',ok=True,exe_sha256=manifest['exe_sha256'],
    checks={n:dict(ok=r['ok'],tests=r.get('tests')) for n,r in checks.items()},
    health=health,running=state['running'],status=state['status'],workflow=state['workflow'],summary=summary,
    account_budget_and_failures_unchanged=True,progress_keys_preserved=True,
    limits=['DV-S-1/2 identified and safely exited; completion stays unknown without positive evidence',
            'CW-S-2-B/3-B progress can be read; missing MAA combat resources are not added',
            '231 unsupported difficulties remain; full-account scan and full combat flow not accepted'],
    delegation_conversation='autostory-flow0936-01a195bb')
write_json(DATA/'flow0936-release-proof.json',proof)
evidence=ROOT/'release/evidence/flow0936'
for name in names+('package-flow0936-manifest.json','deployment-flow0936.json','flow0936-release-proof.json','package-integration.json','flow0936-installed-worker-process.json','flow0936-installed-dv-worker-process.json'):
    shutil.copy2(DATA/name,evidence/name)
source=checks['flow0936-source-check.json']['tests'];frozen=checks['flow0936-frozen-check.json']['tests']
doc=f'''# AutoStory 0.9.36 特殊关卡页面修复

版本与证据封存时间：{proof['at']}。EXE SHA-256：`{manifest['exe_sha256']}`。

## 修改与行为

- DV-S-1/2：按独立面板上的关卡编号、官方名称、页面标题及按钮位置共同确认身份。退出使用实测右上角关闭按钮，随后重新确认地图；不再对无效的 Android 返回键反复等待。
- DV-S 状态仍是 unknown：该面板没有普通三星通关标记。查看条件中的 COMPLETED、已解锁及空奖励区都不作为正式通关证据。可以继续扫描后续分区，但这两条记录不会被算作已确认。
- CW-S：同时验证七组摩斯码图案、三个分区标签和选中标签颜色，才允许使用实机逐个核对的节点位置；打开后再核对准确关卡编号。读取七个普通节点及 CW-S-4 突袭，分别保存。
- CW-S-2-B/3-B 即使缺少 MAA 战斗地图也可以读取详情。这没有补齐战斗资源，也没有绕过正式作战入口的资源检查。
- 两种特殊页面加入启动就绪识别。普通详情页明确排除出地图节点识别，防止把面板标题误当可点击关卡。

## 验证

- 源码回归 {source} 项通过；最终 EXE 内字节码回归 {frozen} 项通过。冻结检查沿用既有隔离方式，不包含 23 项服务状态测试，这些由源码回归覆盖。
- 源码和最终 EXE 字节码各自在真实模拟器完成 CW-S 8 条、DV-S 2 条详情读取、保存回调及返回主页。DV-S 的 unknown 是预期保守结果，不是通关确认。
- 安装后通过本机服务实际启动 EXE worker，再分别扫描 CW-S 与 DV-S；验证任务结束状态、记录新时间戳和截图。
- 独立安装包启动与 worker/WebView2 运行时检查通过。界面文件未修改，没有重做浏览器视觉验收。
- 整轮账户记录、预算、失败历史、设置保留，旧进度键没有删除。保留原扫描检查点并重新启动 scan-all；续扫规划将此前失败或仍待确认的分区后置，实际首先进入 DV-EX，不声称本次从 DV-S 原位置继续点击。

## 当前边界

封存时已确认 {summary['confirmed']}/{summary['total']} 条，扫描状态为 {state['status']['state']}，当前分区 {state['scan_report']['active_group']}。还有 {summary['unsupported']} 项特殊难度未适配。
这轮没有开始演习或正式作战，没有新增正式通关证明，也没有宣称整账户扫描或完整自动战斗流程已通过验收。

证据目录：`release/evidence/flow0936`。原进度备份：`runtime/backups/flow0936-before`。上一安装版 App-0.9.35 保留。
'''
(ROOT/'FLOW_0936.md').write_text(doc,encoding='utf-8')
readme=ROOT/'README.md';text=readme.read_text('utf-8-sig')
text=text.replace('**当前统一版本：0.9.35','**历史版本：0.9.35').replace('**当前版本：0.9.35','**历史版本：0.9.35')
intro=f'> **当前统一版本：0.9.36（2026-11-01）**。修复 DV-S 特殊面板身份核对与退出，适配 CW-S 摩斯码地图的 7 个普通节点和 1 个突袭节点。DV-S 通关状态保留待确认。{source} 项源码、{frozen} 项最终 EXE 字节码检查通过，安装版实机分区扫描通过，原断点已续扫；全流程尚未完成验收。详见 [特殊页面修复与验证](FLOW_0936.md)。\n\n'
readme.write_text(intro+text.lstrip(),encoding='utf-8')
core=read_json(ROOT/'CORE_RELEASE_STATUS.json',{})
core.update(version='0.9.36',state='Verified special-page navigation; DV clearance remains unknown',exe_sha256=manifest['exe_sha256'],source_tests=source,frozen_tests=frozen,
    flow0936_proof=str(DATA/'flow0936-release-proof.json'),full_flow_verified=False,deployed_version='0.9.36',report='FLOW_0936.md',
    latest_live_result='CW-S 8 and DV-S 2 records read by source, shipped bytecode and installed EXE; DV state unknown. Scan resumed, no battles.')
core.setdefault('source_sha256',{}).update(manifest['source_sha256']);write_json(ROOT/'CORE_RELEASE_STATUS.json',core)
task=read_json(DATA/'flow0936-task.json',{})
task.update(status='released',finished=now(),release_proof=str(DATA/'flow0936-release-proof.json'),
    delegation_result='evt_20261101T113134Z_c308db7953',delegation_review='evt_20261101T123440Z_f12a27f07d',
    scan_resumed=checks['flow0936-resume-check.json']['ok'],full_flow_verified=False)
write_json(DATA/'flow0936-task.json',task)
print(json.dumps(dict(ok=True,version='0.9.36',source_tests=source,frozen_tests=frozen,running=state['running'],summary=summary),ensure_ascii=False))
