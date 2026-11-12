"""Publish only verified installed/startup evidence and its explicit limits."""
import hashlib
import json
import shutil
from pathlib import Path
import sys
import urllib.request

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from autostory.common import read_json,write_json,now
DATA=ROOT/'runtime';VERSION='0.9.32'
manifest=read_json(DATA/'package-startup0932-manifest.json')
source=read_json(DATA/'startup0932-source-check.json')
frozen=read_json(DATA/'startup0932-frozen-check.json')
frames=read_json(DATA/'startup0931-frame-check.json')
live=read_json(DATA/'startup0932-live-check.json')
package=read_json(DATA/'package-integration.json')
deployment=read_json(DATA/'deployment-startup0932.json')
for report in (manifest,source,frozen,frames,live,package):assert report['ok'],report
assert manifest['version']==source['version']==frozen['version']==live['version']==VERSION
assert package['health']['version']==deployment['Version']==VERSION
assert not source['live_records_touched'] and not frozen['live_records_touched']
assert live['loader']=='FrozenLoader' and not live['source'] and live['cold_launch']
assert live['startup']['core_runs']>0 and live['core_tasks_started']==['StartUp']
assert live['records_unchanged'] and live['battles_started']==0 and deployment['RecordsUnchanged']
exe=ROOT/'App-0.9.32/MAA自动推图助手.exe'
assert hashlib.sha256(exe.read_bytes()).hexdigest()==manifest['exe_sha256']
for name,sha in manifest['source_sha256'].items():assert hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==sha,name
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
def get(path):
    with opener.open('http://127.0.0.1:18741'+path,timeout=5) as response:return json.load(response)
health=get('/api/health');state=get('/api/state')
assert health['version']==VERSION and Path(health['executable'])==exe
evidence_dir=ROOT/'release/evidence/startup0932';evidence_dir.mkdir(parents=True,exist_ok=True)
evidence_hashes={}
for row in frames['cases']:
    path=Path(row['file']);target=evidence_dir/path.name
    shutil.copy2(path,target);evidence_hashes[path.name]=hashlib.sha256(target.read_bytes()).hexdigest()
write_json(evidence_dir/'frame-hashes.json',evidence_hashes)
shutil.copy2(DATA/'startup0931-frame-check.json',evidence_dir/'frame-check.json')
shutil.copy2(DATA/'startup0932-live-check.json',evidence_dir/'cold-start-check.json')
release=dict(at=now(),version=VERSION,ok=True,source=source,frozen=frozen,
    retained_frame_count=len(frames['cases']),frame_replay_ok=True,
    foreground_for_saved_frames='supplied test fixture; live foreground separately verified',
    live_cold_start_ok=True,live_core_tasks=live['core_tasks_started'],
    live_account_records_unchanged=True,installation_records_unchanged=True,
    natural_update_install_end_to_end=False,long_unattended_run_verified=False,
    health=health,current_status=state['status'],exe_sha256=manifest['exe_sha256'],
    ui_changed=False,full_browser_interaction_suite_rerun=False,
    retained_frame_archive=str(evidence_dir),
    delegation_conversation_id='autostory-startup0931-01a195bb')
write_json(DATA/'startup0932-release-proof.json',release)
status=read_json(ROOT/'CORE_RELEASE_STATUS.json',{})
status.update(version=VERSION,deployed_version=VERSION,report='STARTUP_0932.md',
    state='Startup readiness, guarded updates and bounded scan recovery installed',
    exe_sha256=manifest['exe_sha256'],source_tests=source['tests'],frozen_tests=frozen['tests'],
    current_task=state['status'],latest_live_result='Exact shipped code cold-launched the official game from MuMu launcher to verified home; resumed scan checked separately. No battle dispatched.')
status.setdefault('source_sha256',{}).update(manifest['source_sha256'])
status['startup0932']={'proof':'runtime/startup0932-release-proof.json',
    'manifest':'runtime/package-startup0932-manifest.json','live':'runtime/startup0932-live-check.json',
    'frames':'runtime/startup0931-frame-check.json','deployment':'runtime/deployment-startup0932.json'}
write_json(ROOT/'CORE_RELEASE_STATUS.json',status)
readme=ROOT/'README.md';old=readme.read_text('utf-8-sig')
old=old.replace('> **当前统一版本：0.9.30','> **历史版本：0.9.30',1)
intro=('> **当前统一版本：0.9.32（2026-11-01）**。正式扫描、干员刷新和推图前检查游戏就绪状态；支持后台启动、已登录标题页、可信安装器确认、下载进度和卡住诊断。'
       f'{source["tests"]} 项源码、{frozen["tests"]} 项 EXE 代码回归通过；真实冷启动已通过，完整更新安装和长时间无人值守仍待自然场景验证。详见 [启动与恢复验收](STARTUP_0932.md)。\n\n')
if not old.startswith('> **当前统一版本：0.9.32'):old=intro+old
old=old.replace('## 桌面程序（当前 0.9.30）','## 桌面程序（当前 0.9.32）')
old=old.replace('目标为本目录 `App-0.9.30/MAA自动推图助手.exe`','目标为本目录 `App-0.9.32/MAA自动推图助手.exe`')
old=old.replace('已包含在 `App-0.9.30/_internal`','已包含在 `App-0.9.32/_internal`')
readme.write_text(old,encoding='utf-8')
text=f'''# 0.9.32 启动与异常恢复（2026-11-01）

已安装至 `App-0.9.32/MAA自动推图助手.exe`，桌面快捷方式和后台服务指向该版本。

- 正式扫描、干员刷新和推图入口先检查游戏就绪状态；模拟器桌面上可启动官服客户端，已登录的“开始唤醒”页面交给 MAA 启动任务处理。扫描当前地图只检查就绪，不自动切换页面。
- 结合实际前台包名与截图识别更新、安装、下载、资源检查、登录和就绪页面。只在系统安装器内确认精确应用名“明日方舟”和唯一按钮时操作；点击前重新截图核对。MAA 停止输入后才接管安装窗口。
- 下载/资源检查按可确认的百分比进展等待；默认总等待 1200 秒、无进展 180 秒、未知页面 15 秒。可通过 settings.json 的 startup.total_timeout、startup.stall_timeout、startup.unknown_timeout 调整。停止指令始终有效。
- 密码、验证码、权限、维护、消费确认与正在战斗等情况保留断点并报告原因。已有战斗结算仍交给原来的证据核验与恢复流程，启动模块不确认战斗胜负。
- 扫描途中遇到可识别的启动异常时，每个分区最多恢复一次，再按已有逐关记录继续同一分区；持续入口失败仍停止或保留缺项。作战中断不会被整轮重放。
- 修复 MuMu 多显示区的旧焦点干扰、正常标题页底部账号入口误报、主页月卡广告价格误报和健康游戏标语误报。小字 START 漏读时用固定资源页控件确认页面，再由 MAA 已有 GameStart 模板决定点击。截图/OCR/操作证据写入 runtime/screens/startup，当前启动状态写入 runtime/startup.json。

验收：{source['tests']} 项源码测试、{frozen['tests']} 项最终 EXE 代码测试通过；冻结检查排除会指向真实 runtime 的 test_server_state，该模块在源码套件通过。两套检查确认账号记录和任务记录未变化。隔离双启动、worker 与隐藏 WebView 宿主检查通过；页面未改动，没有重跑完整浏览器交互套件。

{len(frames['cases'])} 张保留实拍帧经真实 OCR 回放，包括更新确认、安装中、6%/43%/74% 进度、已登录标题页及主页广告。回放中的前台包名是测试输入，实际前台身份另由本机测试核对。

实际最终包冷启动：先确认非战斗页面，关闭官服客户端，从 MuMu 桌面启动、处理标题页，到达经过两帧确认的主页。实际核心任务仅 StartUp，未启动战斗；账号进度、扫描断点、失败历史、预算及设置哈希一致。安装时也确认账号文件未变化，旧版本保留。

边界：当前游戏已完成更新，因此本轮没有再次自然触发“下载 APK → 安装 → 返回游戏”的完整更新流程；安装确认分支以实拍回放与隔离状态序列验证。未宣称长时间无人值守已验收、全目录扫描已完成或新增通关。

证据：runtime/startup0932-release-proof.json；源码/包哈希、回归、实拍、冷启动和安装记录见 CORE_RELEASE_STATUS.json。

DeepSeek 使用既有 Harness 会话 autostory-startup0931-01a195bb，仅写独立副本。桥返回码 124；已落盘分类器作为候选由 Codex 检查、选取并修正，未直接把其完成声明作为验收。最终主流程、实际安装包和实机结果由 Codex 独立验收。
'''
(ROOT/'STARTUP_0932.md').write_text(text,encoding='utf-8')
record=read_json(DATA/'startup0931-task.json')
record.update(version=VERSION,status='verified',proof='runtime/startup0932-release-proof.json',
              delegate_result_id='evt_20261101T063009Z_0a038eee92',delegate_return_code=124,
              delegate_candidate_snapshot_sha256='aeff76caaa91521165373b7b62d2a7c76a8557ae90d917186b2177488b37c058')
write_json(DATA/'startup0931-task.json',record)
print(json.dumps(dict(version=VERSION,source_tests=source['tests'],frozen_tests=frozen['tests'],
                     live_cold_start=True,status=state['status']),ensure_ascii=False))
