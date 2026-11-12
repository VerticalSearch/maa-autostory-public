"""Verify deployment evidence and publish an accurate local 0.9.30 record."""
import hashlib
import json
from pathlib import Path
import sys
import urllib.request

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from autostory.common import read_json,write_json,now

VERSION='0.9.30'
DATA=ROOT/'runtime'
manifest=read_json(DATA/'package-recovery0930-manifest.json')
source=read_json(DATA/'recovery0930-source-check.json')
frozen=read_json(DATA/'recovery0930-frozen-check.json')
package=read_json(DATA/'package-integration.json')
frames=read_json(DATA/'collection0930-frame-check.json')
target=read_json(DATA/'collection0930-target-check.json')
terminal=read_json(DATA/'terminal0930-frame-check.json')
deployment=read_json(DATA/'deployment-recovery0930.json')
attempt=read_json(DATA/'nav0930-baseline-live.json')
for report in (manifest,source,frozen,package,frames,target,terminal):
    assert report['ok'],report
assert not source['live_records_touched'] and not frozen['live_records_touched']
assert manifest['version']==frozen['version']==package['health']['version']==deployment['Version']==VERSION
assert deployment['RecordsUnchanged'] and attempt['records_unchanged']
exe=ROOT/'App-0.9.30/MAA自动推图助手.exe'
assert hashlib.sha256(exe.read_bytes()).hexdigest()==manifest['exe_sha256']
for relative,sha in manifest['source_sha256'].items():
    assert hashlib.sha256((ROOT/relative).read_bytes()).hexdigest()==sha,relative
assert hashlib.sha256((ROOT/'web/index.html').read_bytes()).hexdigest()==manifest['ui_sha256']
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
def get(path):
    with opener.open('http://127.0.0.1:18741'+path,timeout=30) as response:return json.load(response)
health=get('/api/health');state=get('/api/state')
assert health['version']==VERSION and Path(health['executable'])==exe
assert not state['running'],state['status']
release={'at':now(),'version':VERSION,'ok':True,'exe_sha256':manifest['exe_sha256'],
         'source':source,'frozen':frozen,'package_integration_ok':True,
         'offline_collection':{'frames':frames['frames'],'before':frames['old_count'],'after':frames['new_count'],
                              'added':frames['added_titles'],'lost':frames['lost_titles'],'target_lookup_ok':target['ok']},
         'terminal_frame_replay_ok':terminal['ok'],'health':health,'status':state['status'],
         'account_records_unchanged':True,'live_navigation_complete':False,
         'live_attempt':{'at':attempt['at'],'error':attempt.get('error'),'battles_started':attempt['battles_started']},
         'ui_changed':False,'browser_suite_rerun':False,'older_installed_bundles_pruned':False,
         'delegation_conversation_id':'autostory-nav0930-01a19588'}
write_json(DATA/'recovery0930-release-proof.json',release)

status=read_json(ROOT/'CORE_RELEASE_STATUS.json')
status.update(version=VERSION,deployed_version=VERSION,report='RELEASE_0930.md',
              state='Collection OCR misses remain unknown; shared Terminal failures preserve the checkpoint and stop the round',
              exe_sha256=manifest['exe_sha256'],source_tests=source['tests'],frozen_tests=frozen['tests'],
              current_task=state['status'],latest_live_result='0.9.30 live navigation not completed: game resource startup timed out, '
                    'then the emulator went offline. No battle started. Offline and isolated package checks passed.')
status['source_sha256'].update(manifest['source_sha256'])
status['source_sha256']['web\\index.html']=manifest['ui_sha256']
status['recovery0930']={'proof':'runtime/recovery0930-release-proof.json',
    'manifest':'runtime/package-recovery0930-manifest.json','deployment':'runtime/deployment-recovery0930.json',
    'collection_frames':'runtime/collection0930-frame-check.json','target_lookup':'runtime/collection0930-target-check.json',
    'terminal_frames':'runtime/terminal0930-frame-check.json','live_attempt':'runtime/nav0930-baseline-live.json',
    'ui_unchanged_from':'0.9.29','live_navigation_complete':False}
write_json(ROOT/'CORE_RELEASE_STATUS.json',status)

text=f'''# 0.9.30：名录漏读回退与终端故障断点（2026-11-01）

已安装到 `App-0.9.30/MAA自动推图助手.exe`，后台服务和桌面快捷方式均核对为 0.9.30。

- 乐章收录每屏增加最多四次卡片区域 OCR，点击前的章节标题核验使用同一方法。13 张保留实拍帧由 24 个标题提升至 27 个，新增多索雷斯假日、尘影余音、崔林特尔梅之金，原有标题没有丢失；新增三项均通过定向查找截图复验。
- 翻到名录末尾只表示遍历结束。OCR 没读到的章节仍进行定向搜索，不据此认定缺席。旧版无来源标记的负缓存不再阻止重查；新负缓存仅来自实际定向查找失败，保留 6 小时。正向索引命中优先。
- 单字标题只允许精确匹配；多字标题只容许少量拉丁字母、数字或标点装饰，不把含额外汉字的长文本当成目标标题。缓存中的异常时间、损坏 JSON 和非法来源字段按无可用缓存处理。
- 已识别章节但点击后还留在名录，最多试三次；仍失败则报入口未确认，不把该章节记成不存在。
- 主页到终端最多点击三次，每次都要求重新确认主页与按钮，最后一次点击也复查结果。到达终端需要两个有位置约束的独立控件。持续无响应时保存截图/OCR/点击决策并停止整轮，不再把后续章节批量记成入口失败。旧报告中的同类终端错误也不再作为后移章节的依据。
- 建立名录前发生故障时保留原来的 `active_group`，续扫位置不丢失。源码版本号同步为 0.9.30。

验收：{source['tests']} 项源码、{frozen['tests']} 项最终 EXE 冻结代码测试通过；冻结套件继续排除会指向真实 runtime 的 `test_server_state`，该模块已在源码套件运行。两套检查均确认真实记录哈希未变。双启动、隔离 worker 和隐藏 WebView 宿主启动检查通过；控制页内容未修改，未重新运行完整浏览器交互套件。五张主页/名录/详情实拍帧均未误判终端到达。

安装保留账号进度、干员、失败历史、扫描断点、剩余预算和设置；旧安装版本保留。第一份内部候选包因定向查找复验未通过已归档在 `_tmp/nav0930-first-candidate`，不作为当前入口。

本轮后台实机尝试在游戏资源加载时 `StartUp` 超时，随后观察到模拟器离线；没有启动战斗。本版没有完成真实章节进入、战斗或长时间无人值守验证。27 个是固定截图样本内识别到的标题数量，不代表全目录覆盖，也不代表任何关卡通关。

证据入口：`runtime/recovery0930-release-proof.json`；具体源码/包哈希、回归、截图复验及安装记录均列于其中和 `CORE_RELEASE_STATUS.json`。

DeepSeek 会话 `autostory-nav0930-01a19588` 使用既有 Harness，实际路由为 `deepseek-official/deepseek-flash`，写入仅限独立副本。两次执行超过各自时限，未作为完成结果验收。Codex 审核候选并修正定向识别不一致、缓存来源类型和测试夹具后独立验收；完整模型输出保留在原桥账本，未改写历史事件。
'''
(ROOT/'RELEASE_0930.md').write_text(text,encoding='utf-8')
readme=ROOT/'README.md';old=readme.read_text('utf-8-sig')
if not old.startswith('> **当前统一版本：0.9.30'):
    old=old.replace('> **当前统一版本：0.9.29','> **历史版本：0.9.29',1)
    intro=(f'> **当前统一版本：0.9.30（2026-11-01）**。名录卡片 OCR 与点击前核验统一；'
           f'漏读不再推断章节不存在；入口点击最多三次；终端持续无响应时保存断点并停止整轮。'
           f'13 张截图标题 24→27；{source["tests"]} 项源码、{frozen["tests"]} 项冻结测试和隔离打包检查通过。'
           '实机启动遇到资源加载超时，本版未完成导航/战斗实机验收。详见 [0.9.30 说明](RELEASE_0930.md)。\n\n')
    old=intro+old
old=old.replace('不吃药、不用源石、不借助战。','不吃药、不用源石；作业只缺 1 名干员且技能要求不超过 7 级时可借 1 名助战，自有干员方案优先。')
old=old.replace('## 桌面程序（0.7.0）','## 桌面程序（当前 0.9.30）')
old=old.replace('目标为本目录 `App-0.7.0/MAA自动推图助手.exe`','目标为本目录 `App-0.9.30/MAA自动推图助手.exe`')
old=old.replace('已包含在 `App-0.7.0/_internal`','已包含在 `App-0.9.30/_internal`')
readme.write_text(old,encoding='utf-8')
record=read_json(DATA/'nav0930-delegation.json')
record.update(candidate_completion_accepted=False,reviewed_and_adapted_by='codex',
              verified_main_release='runtime/recovery0930-release-proof.json',
              result_ids=['evt_20261101T053306Z_947869e2fb','evt_20261101T054546Z_55aefb10be'])
write_json(DATA/'nav0930-delegation.json',record)
print(json.dumps({'version':VERSION,'source_tests':source['tests'],'frozen_tests':frozen['tests'],
                 'title_replay':[frames['old_count'],frames['new_count']],'installed':True,
                 'running':state['running'],'records_unchanged':True,'live_navigation_complete':False},ensure_ascii=False))
