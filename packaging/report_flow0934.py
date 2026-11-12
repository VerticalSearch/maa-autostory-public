"""Publish evidence for installed 0.9.34 only after its checks pass."""
import hashlib,json,shutil,sys,urllib.request
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from autostory.common import read_json,write_json,now
DATA=ROOT/'runtime'
manifest=read_json(DATA/'package-flow0934-manifest.json')
source=read_json(DATA/'flow0934-source-check.json')
frozen=read_json(DATA/'flow0934-frozen-check.json')
live=read_json(DATA/'flow0934-frozen-live.json')
source_live=read_json(DATA/'flow0934-source-live.json')
web=read_json(DATA/'web0934-browser-audit.json')
package=read_json(DATA/'package-integration.json')
deployment=read_json(DATA/'deployment-flow0934.json')
resume=read_json(DATA/'flow0934-resume-check.json')
for check in (manifest,source,frozen,live,source_live,web,package,resume):assert check['ok']
assert source['version']==frozen['version']==manifest['version']==deployment['Version']=='0.9.34'
assert package['health']['version']=='0.9.34' and not source['live_records_touched'] and not frozen['live_records_touched']
assert live['frozen'] and live['loader']=='FrozenLoader' and live['details']
assert [r['group'] for r in live['groups']]==['PV-EX','PV-S','HE-EX']
for row in live['groups']:
    assert row['group'] in row['map_groups'] and row['detail']['code'].startswith(row['group']+'-')
    assert row['detail']['state'] in ('cleared','uncleared','partial','locked')
assert live['account_and_scan_checkpoint_unchanged'] and not live['progress_keys_removed']
assert deployment['RecordsUnchanged']
for name,sha in {**manifest['source_sha256'],**manifest['asset_sha256']}.items():
    assert hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==sha,name
assert hashlib.sha256((ROOT/'web/index.html').read_bytes()).hexdigest()==manifest['ui_sha256']
assert hashlib.sha256((ROOT/'App-0.9.34/MAA自动推图助手.exe').read_bytes()).hexdigest()==manifest['exe_sha256']
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
with opener.open('http://127.0.0.1:18741/api/health',timeout=5) as response:health=json.load(response)
assert health['version']=='0.9.34'
proof=dict(at=now(),version='0.9.34',ok=True,source=source,frozen=frozen,browser_checks=web['passed'],
    manifest=manifest,live_groups=[{k:v for k,v in row.items() if k!='texts'} for row in live['groups']],
    deployment=deployment['Health'],scan_resume=resume,
    battles_started=0,all_catalogue_complete=False,all_special_difficulties_supported=False,
    natural_update_install_retested=False,known_limitations=['PV-SP lacks MAA map resources and uses predefined teams',
       'IS multi-area overview remains unadapted','No new formal battle acceptance in this release'])
write_json(DATA/'flow0934-release-proof.json',proof)
evidence=ROOT/'release/evidence/flow0934'
for name in ('flow0934-source-live.json','flow0934-frozen-live.json','web0934-browser-audit.json','flow0934-resume-check.json',
             'flow0934-source-check.json','flow0934-frozen-check.json','package-flow0934-manifest.json',
             'package-integration.json','deployment-flow0934.json','flow0934-release-proof.json'):
    shutil.copy2(DATA/name,evidence/name)
status=read_json(ROOT/'CORE_RELEASE_STATUS.json',{})
status.update(version='0.9.34',deployed_version='0.9.34',report='FLOW_0934.md',
    state='Verified zone switching and accurate partial/budget/completed outcomes',
    exe_sha256=manifest['exe_sha256'],ui_sha256=manifest['ui_sha256'],source_tests=source['tests'],
    frozen_tests=frozen['tests'],browser_checks=web['passed'],current_task=resume['status'],
    latest_live_result='Exact shipped code entered PV-EX, PV-S and HE-EX and read first-stage detail; scan resumed. No battles started.')
status.setdefault('source_sha256',{}).update(manifest['source_sha256'])
status['flow0934']={'proof':'runtime/flow0934-release-proof.json','manifest':'runtime/package-flow0934-manifest.json',
                  'live':'runtime/flow0934-frozen-live.json','resume':'runtime/flow0934-resume-check.json'}
write_json(ROOT/'CORE_RELEASE_STATUS.json',status)
readme=ROOT/'README.md';old=readme.read_text('utf-8-sig').replace('> **当前统一版本：0.9.33','> **历史版本：0.9.33',1)
intro=f'> **当前统一版本：0.9.34（2026-11-01）**。修复 PV-EX/PV-S 装饰文字和 HE-EX 图标入口，同一活动可核对当前地图后直接切换分区；提前报告缺少地图资源。推图区分目标完成、部分完成和预算用完，续跑重查未确认分区。{source["tests"]} 项源码、{frozen["tests"]} 项 EXE 代码、{web["passed"]} 项浏览器检查通过，三个分区已用正式包实机读取详情。详见 [流程优化验收](FLOW_0934.md)。\n\n'
if not old.startswith('> **当前统一版本：0.9.34'):old=intro+old
old=old.replace('## 桌面程序（当前 0.9.33）','## 桌面程序（当前 0.9.34）').replace('目标为本目录 `App-0.9.33/','目标为本目录 `App-0.9.34/').replace('已包含在 `App-0.9.33/_internal`','已包含在 `App-0.9.34/_internal`')
readme.write_text(old,encoding='utf-8')
text=f'''# 0.9.34 流程优化与验收

已安装到 App-0.9.34，桌面快捷方式和后台服务已核对为该版本。

- PV-EX/PV-S：识别限定位置的精确中英文分区名称，必要时放大底部 OCR；合并的多按钮文字不会用作坐标。HE-EX：在两个准确关卡编号证实 HE 地图后，匹配受限区域的 EX 图标。所有点击后重新确认目标分区编号，超时不重放点击。
- 同一活动连续切换可复用当前地图，但每次都重新读取实际页面；缓存只是提示，不是页面证据。源码实机 PV-EX → PV-S 为约 5 秒，不能据此推断所有导航速度。
- PV-SP 当前缺少 MAA 地图资源且为固定编队关卡。入口预检明确报告资源缺口，不再反复进入活动，不计作已扫描/已通关。IS 多区域概览仍有适配缺口。
- 全目录和本章推图的完成状态贯通控制器、worker、检查点和控制页：普通/突袭范围全有通关记录才为目标完成；未知、锁定、无作业和阻塞保留部分完成；本轮预算耗尽单独显示。特殊难度单列，不推断通关。
- 续跑只跳过有通关记录的完整分区；以前只是访问过或扫描缺项的分区会再次核对。预算和失败历史持续沿用。

源码 {source['tests']} 项、EXE 字节码 {frozen['tests']} 项回归通过；冻结套件排除 test_server_state，该模块由源码套件覆盖。两套检查确认真实任务及账号文件未被测试修改。隔离双启动和 worker 检查通过。浏览器 {web['passed']} 项检查通过，所有 POST 被测试拦截，没有向真实服务发出操作。

实机：源码和从最终 EXE 加载的生产字节码分别进入 PV-EX、PV-S、HE-EX，读取 PV-EX-1、PV-S-1、HE-EX-1 的普通详情并核对编号。只导航、读进度，没有演习、正式作战或养成；账号预算、正式通关记录、失败历史、扫描检查点和设置哈希未改变，已有进度键未删除。详情观察可能更新相应扫描记录的时间和证据。生产字节码探针是独立 Python 进程，安装后的真实 EXE 任务另外验收启动和扫描续跑。

安装后通过正式服务继续 scan-all。验收时 {resume['confirmed_after']}/{resume['total']} 项已确认，当前分区 {resume['current_group']}；这不是全目录完成。未验收所有特殊地图、全更新安装链路或长时间连续实战，也未宣称新增通关。

证据见 runtime/flow0934-release-proof.json 与 release/evidence/flow0934。DeepSeek Harness 会话 autostory-flow0934-01a195bb 仅实现独立完成状态汇总模块，候选经两轮审查，Codex 负责生产集成、包检查和实机验收。
'''
(ROOT/'FLOW_0934.md').write_text(text,encoding='utf-8')
task=read_json(DATA/'flow0934-task.json');task.update(status='verified',proof='runtime/flow0934-release-proof.json',
    delegate_result_id='evt_20261101T072047Z_f2115cc93d',delegate_sha256=manifest['source_sha256']['autostory\\completion.py'])
write_json(DATA/'flow0934-task.json',task)
print(json.dumps(dict(version='0.9.34',ok=True,source_tests=source['tests'],frozen_tests=frozen['tests'],browser_checks=web['passed']),ensure_ascii=False))
