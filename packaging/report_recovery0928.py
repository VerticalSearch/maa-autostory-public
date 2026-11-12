"""Collect and assert the 0.9.28 release evidence, then update the release records."""
import hashlib, json, re, sys, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from autostory.common import DATA, read_json, write_json, now

VERSION = '0.9.28'
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def get(path):
    with OPENER.open('http://127.0.0.1:18741' + path, timeout=30) as response:
        return json.load(response)


manifest = read_json(DATA / 'package-recovery0928-manifest.json')
frozen = read_json(DATA / 'recovery0928-frozen-check.json')
integration = read_json(DATA / 'package-integration.json')
deployment = read_json(DATA / 'deployment-recovery0928.json')
assert manifest['ok'] and frozen['ok'] and integration['ok'] and not frozen['live_records_touched']
assert manifest['version'] == frozen['version'] == deployment['Version'] == VERSION
assert integration['health']['version'] == VERSION and integration['worker_status']['state'] == 'done'
assert digest(ROOT / ('App-%s/MAA自动推图助手.exe' % VERSION)) == manifest['exe_sha256']
assert digest(ROOT / 'web/index.html') == manifest['ui_sha256']
for relative, sha in manifest['source_sha256'].items():
    if relative != 'autostory\\__init__.py':
        assert digest(ROOT / relative) == sha, relative
source_log = (DATA / 'tests-recovery0928-source.log').read_text('utf-8-sig')
assert re.search(r'\nOK\s*$', source_log), 'source suite did not end OK'
source_tests = int(re.search(r'Ran (\d+) tests', source_log)[1])
assert deployment['RecordsUnchanged']
release = read_json(ROOT / 'active-release.json')
assert release == {'version': VERSION, 'directory': 'App-' + VERSION, 'sha256': manifest['exe_sha256']}, release

health = get('/api/health')
state = get('/api/state')
assert health['version'] == VERSION, health
assert not state['running'] and state['status']['state'] == 'interrupted', state['status']
assert state['resume']['available'] and state['training']['state'] == 'ready'
prune = deployment['Prune']
live = {'ok': True, 'at': now(), 'health': health, 'status': state['status'], 'resume': state['resume'],
        'training': {k: state['training'].get(k) for k in ('state', 'support', 'summary', 'ranking', 'more')},
        'source_tests': source_tests, 'frozen_tests': frozen['tests'], 'frozen_excluded': frozen['excluded'],
        'bundle_integration_ok': True, 'pruned_bundles': len(prune['Removed']), 'freed_mb': prune['FreedMB'],
        'kept_bundles': prune['Kept'], 'device_smoke': 'pending: emulator not running after the 03:00 user reboot'}
try:  # keep a device result recorded after the first run
    previous = read_json(DATA / 'recovery0928-live-proof.json').get('device_smoke')
except (OSError, ValueError, AttributeError):
    previous = None
if isinstance(previous, dict):
    live['device_smoke'] = previous
write_json(DATA / 'recovery0928-live-proof.json', live)

report = read_json(ROOT / 'CORE_RELEASE_STATUS.json')
report.update(version=VERSION, deployed_version=VERSION, report='RELEASE_0928.md',
              state='Support unit for one missing operator, free formal stages, training report, '
                    'stale-status repair, cached GUI-idle check, bounded logs and bundle pruning',
              exe_sha256=manifest['exe_sha256'], ui_sha256=manifest['ui_sha256'],
              source_tests=source_tests, frozen_tests=frozen['tests'], current_task=state['status'],
              latest_live_result='0.9.28 installed cold (service was down after a user reboot); stale 02:46 '
                                 'scan persisted as interrupted, resume offered; device smoke pending.')
report['source_sha256'].update({k: v for k, v in manifest['source_sha256'].items() if k != 'autostory\\__init__.py'})
report['recovery0928'] = {
    'manifest': 'runtime/package-recovery0928-manifest.json',
    'source': 'runtime/tests-recovery0928-source.log',
    'frozen': 'runtime/recovery0928-frozen-check.json',
    'frozen_log': 'runtime/tests-frozen0928.log',
    'bundle_integration': 'runtime/package-integration.json',
    'deployment': 'runtime/deployment-recovery0928.json',
    'live_proof': 'runtime/recovery0928-live-proof.json',
    'changed_modules': manifest['changed_modules'],
    'notes': [
        'Support unit only when exactly one operator is missing, skill requirement <= 7, ignore_requirements stays false',
        'Own-roster jobs always rank before support jobs; settings.support_unit_when_missing_one=false disables it',
        'Formal-only applies to normal, non-predefined stages without practice entry and a confirmed cost of 0',
        'Training report is read-only and cached by input hash',
        'Housekeeping runs once per worker under the task lock, before device input, and can never fail a run',
        'Frozen check excludes test_server_state (in-process stub server would hit the live runtime) and guards live records',
        'Device smoke test pending: emulator not running after the 03:00 user reboot',
    ]}
write_json(ROOT / 'CORE_RELEASE_STATUS.json', report)

readme = ROOT / 'README.md'
text = readme.read_text('utf-8-sig')
if not text.startswith('> **当前统一版本：%s' % VERSION):
    text = text.replace('> **当前统一版本：0.9.27', '> **历史版本：0.9.27', 1)
    readme.write_text(
        '> **当前统一版本：0.9.28（2026-11-01）**。作业只缺 1 名干员时由 MAA 借 1 名助战（技能要求 ≤7，自有干员作业优先，可在设置关闭）；'
        '无演习入口、确认 0 理智的普通关直接正式作战；新增只读的「练谁最划算」；重启后自动把失效的「运行中」改为中断并给出继续入口；'
        'GUI 空闲检查增量缓存、点按等待缩短；事件日志 20 MB 轮换、截图目录 1 GB 上限；旧版本目录只保留最近 3 个。'
        '%d 项源码、%d 项冻结字节码、打包隔离检查通过。详见 [0.9.28 说明](RELEASE_0928.md)。\n\n' % (source_tests, frozen['tests']) + text,
        encoding='utf-8')
print(json.dumps(live, ensure_ascii=False, indent=2))
