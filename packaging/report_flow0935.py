"""Seal 0.9.35 evidence after installation and checkpoint resume."""
import hashlib,json,shutil,sys,urllib.request
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from autostory.common import read_json,write_json,now
DATA=ROOT/'runtime';VERSION='0.9.35'
names=('package-flow0935-manifest','flow0935-source-check','flow0935-frozen-check',
       'flow0935-source-live','flow0935-frozen-live','siracusa0935-source-live',
       'siracusa0935-frozen-live','package-integration','flow0935-resume-check','flow0935-installed-is-check')
reports={name:read_json(DATA/(name+'.json')) for name in names}
assert all(r['ok'] for r in reports.values())
manifest=reports[names[0]];source=reports[names[1]];frozen=reports[names[2]]
assert manifest['version']==source['version']==frozen['version']==VERSION
assert not source['live_records_touched'] and not frozen['live_records_touched']
assert reports['package-integration']['health']['version']==VERSION
positive={'cleared','uncleared','partial','locked'}
for kind in ('source','frozen'):
    zones=reports['flow0935-'+kind+'-live'];city=reports['siracusa0935-'+kind+'-live']
    for live in (zones,city):
        assert live['account_and_scan_checkpoint_unchanged'] and not live['progress_keys_removed']
        if kind=='frozen':assert live['frozen'] and live['loader']=='FrozenLoader'
    assert [r['group'] for r in zones['groups']]==['NL-EX','NL-S','MN-EX']
    for row in zones['groups']:
        assert row['group'] in row['map_groups']
        for key,mode in (('detail','normal'),('raid_detail','raid')):
            assert row[key]['code']==row['group']+'-1' and row[key]['mode']==mode and row[key]['state'] in positive
    expected={(code,'normal') for code in ('IS-1','IS-TR-1','IS-EX-1','IS-10','IS-S-1')}|{('IS-EX-1','raid'),('IS-S-1','raid')}
    assert {(r['code'],r['mode']) for r in city['rows']}==expected
    assert all(r['state'] in positive for r in city['rows'])
    if kind=='frozen':assert city['scan_group']==dict(group='IS-TR',rows=1,callbacks=1,persisted=True)
for name,sha in {**manifest['source_sha256'],**manifest['asset_sha256']}.items():
    assert hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==sha,name
assert hashlib.sha256((ROOT/'App-0.9.35/MAA自动推图助手.exe').read_bytes()).hexdigest()==manifest['exe_sha256']
assert hashlib.sha256((ROOT/'web/index.html').read_bytes()).hexdigest()==manifest['ui_sha256']
prior=read_json(DATA/'package-flow0934-manifest.json')
assert manifest['ui_sha256']==prior['ui_sha256'],'No UI change was planned'
deploy=read_json(DATA/'deployment-flow0935.json');resume=reports['flow0935-resume-check']
assert deploy['Version']==VERSION and deploy['RecordsUnchanged'] and resume['version']==VERSION
installed=reports['flow0935-installed-is-check']
assert installed['version']==VERSION and installed['actual_group_scan_log'] and installed['account_and_checkpoint_unchanged']
process=read_json(DATA/'flow0935-installed-worker-process.json')
assert process['Verified'] and process['Pid']==resume['accepted']['pid'] and process['RunId']==resume['status']['run_id']
backup=DATA/'backups/flow0935-before'
preserved={}
for name in ('campaign-results.json','operators.json','attempt-history.json','settings.json'):
    current=ROOT/name if name=='settings.json' else DATA/name
    preserved[name]=hashlib.sha256((backup/name).read_bytes()).hexdigest()==hashlib.sha256(current.read_bytes()).hexdigest()
assert all(preserved.values()),preserved
assert not set(read_json(backup/'progress.json',{}))-set(read_json(DATA/'progress.json',{}))
op=urllib.request.build_opener(urllib.request.ProxyHandler({}))
with op.open('http://127.0.0.1:18741/api/health',timeout=5) as r:health=json.load(r)
assert health['version']==VERSION and health['frozen']
proof=dict(at=now(),version=VERSION,ok=True,source=source,frozen=frozen,manifest=manifest,
    zones=reports['flow0935-frozen-live'],siracusa=reports['siracusa0935-frozen-live'],
    deployment=deploy,scan_resume=resume,installed_group_scan=installed,installed_worker=process,session_preservation=preserved,ui_unchanged_from='0.9.34',battles_started=0,
    all_catalogue_complete=False,all_special_difficulties_supported=False,
    limitations=['PV-SP and IS-QT lack usable MAA maps','231 special difficulty variants remain unadapted',
                 'No new formal battle or long unattended combat acceptance'])
write_json(DATA/'flow0935-release-proof.json',proof)
evidence=ROOT/'release/evidence/flow0935'
for name in names+('deployment-flow0935','flow0935-release-proof','flow0935-installed-worker-process'):
    shutil.copy2(DATA/(name+'.json'),evidence/(name+'.json'))
for row in reports['flow0935-frozen-live']['groups']:
    for field in ('detail','raid_detail'):
        image=Path(row[field]['evidence']);shutil.copy2(image,evidence/'frozen'/image.name)
text=f'''# 0.9.35 建筑地图与图标入口优化验收

已安装到 App-0.9.35，后台服务和桌面快捷方式均已核对。

- 修复 NL-EX、NL-S、MN-EX 图标入口。先通过当前地图至少两个准确关卡编号确认活动，再在右下限定区域匹配唯一图标；错误活动、重复图标、模糊结果不点击。点击后确认目标分区编号。
- 接入叙拉古人建筑地图。24 个关卡与建筑的对应关系由本地活动数据、关卡数据交叉核对；不再按普通线性地图假设扫描。二维查找有总移动次数上限，每次移动后重新确认建筑地图，选中建筑后核对实际关卡编号和难度。
- 修复已验证 IS EX/S 长编号的突袭标题识别：独立读取完整编号区域，避免英文标题与编号粘连。普通、突袭分别保存；目标未找到时保留缺项。
- 沿用增量扫描、逐关持久化、停止与断点续跑，不改变原有推图预算、失败历史或通关判定。

源码 {source['tests']} 项测试和 EXE 字节码 {frozen['tests']} 项测试通过；冻结套件排除 test_server_state，该模块由源码套件覆盖。两套检查均验证真实账号和任务记录未被测试改写。隔离双启动、worker、WebView2 运行时检查通过。控制页文件与 0.9.34 哈希相同，本版未重复宣称新的浏览器检查结果。

实机验证分两层：源码，以及从最终 EXE 加载的生产字节码。每层均进入 NL-EX、NL-S、MN-EX 并读取第一关的普通和突袭详情；另外读取 IS-1、IS-TR-1、IS-EX-1、IS-10、IS-S-1，其中 IS-EX-1、IS-S-1 也读取突袭。最终包代码还实际执行 IS-TR 分区扫描，验证逐关回调及落盘记录。跨区域查找的耗时因起始位置不同而变化，仍有优化空间。生产字节码探针在独立 Python 进程中运行；安装后的真实 EXE 另外验证启动和扫描断点续跑。

本轮没有演习、正式战斗或养成操作。预算账本、正式作战结果、失败历史、扫描检查点和设置在导航探针期间保持一致，已有进度键未删除。关卡详情读取会更新对应扫描记录及证据。这些已通关详情是识别到的账号既有状态，不是本轮新增通关。

安装后，通过正式服务实际启动 EXE 的 IS-TR 单分区扫描，启动检查、建筑地图识别、详情读取、进度落盘及任务结束均通过，原全目录检查点保持不变。随后从 {resume['previous_group']} 断点继续 scan-all。验收时已确认 {resume['confirmed_after']}/{resume['total']} 项；仍不是全目录完成。PV-SP、IS-QT 当前缺少可用 MAA 地图资源，231 项特殊难度尚未适配；尚未验收本版长时间连续实战。

证据：runtime/flow0935-release-proof.json 和 release/evidence/flow0935。先前的突袭标题失败及横向查找失败也保留在证据目录。验收脚本曾因内存元组与 JSON 列表的表示差异误报落盘不一致，已按 JSON 规范化后逐字段比较并通过复测；生产代码未因此改动。安装后全目录续扫任务的进程路径及 PID 也已独立核对。

DeepSeek Harness 会话 autostory-flow0935-01a195bb 仅负责隔离的图标匹配模块。首个回复编码降级，历史结果维持 needs_validation；后续 ASCII 交付引用同一产物。Codex 独立检查文件、集成、回归并完成实机验证。
'''
(ROOT/'FLOW_0935.md').write_text(text,encoding='utf-8')
readme=ROOT/'README.md';old=readme.read_text('utf-8-sig').replace('> **当前统一版本：0.9.34','> **历史版本：0.9.34',1)
intro=f'> **当前统一版本：0.9.35（2026-11-01）**。补上 NL-EX/NL-S/MN-EX 图标入口，以及叙拉古人建筑地图的定位与分难度扫描；源码 {source["tests"]} 项、EXE 字节码 {frozen["tests"]} 项回归通过，已完成代表性实机读取和断点续扫验证。详见 [本版验收](FLOW_0935.md)。\n\n'
if not old.startswith('> **当前统一版本：0.9.35'):old=intro+old
old=old.replace('## 桌面程序（当前 0.9.34）','## 桌面程序（当前 0.9.35）').replace('目标为本目录 `App-0.9.34/','目标为本目录 `App-0.9.35/').replace('已包含在 `App-0.9.34/_internal`','已包含在 `App-0.9.35/_internal`')
readme.write_text(old,encoding='utf-8')
status=read_json(ROOT/'CORE_RELEASE_STATUS.json',{})
status.update(version=VERSION,deployed_version=VERSION,report='FLOW_0935.md',
    state='Verified icon selectors and IS building-map navigation with separate difficulty reads',
    exe_sha256=manifest['exe_sha256'],ui_sha256=manifest['ui_sha256'],source_tests=source['tests'],
    frozen_tests=frozen['tests'],current_task=resume['status'],latest_live_result='Three icon zones and five IS operations verified, including raid reads; scan resumed. No new battles.')
status.setdefault('source_sha256',{}).update(manifest['source_sha256'])
status['flow0935']={'proof':'runtime/flow0935-release-proof.json','manifest':'runtime/package-flow0935-manifest.json'}
write_json(ROOT/'CORE_RELEASE_STATUS.json',status)
task=read_json(DATA/'flow0935-task.json');task.update(status='verified',proof='runtime/flow0935-release-proof.json',
    delegate_result_id='evt_20261101T075541Z_e833e95d65',delegate_sha256=manifest['source_sha256']['autostory\\zone_icons.py'])
write_json(DATA/'flow0935-task.json',task)
print(json.dumps(dict(version=VERSION,ok=True,source_tests=source['tests'],frozen_tests=frozen['tests'],confirmed=resume['confirmed_after']),ensure_ascii=False))
