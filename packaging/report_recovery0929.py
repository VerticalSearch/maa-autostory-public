"""Collect and assert the 0.9.29 release evidence, then update the release records."""
import hashlib, json, re, sys, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from autostory.common import DATA, read_json, write_json, now

VERSION = '0.9.29'
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
LIVE_SCAN = '2026-11-01T06:52:'   # minute of the deliberate start/stop order check


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def get(path):
    with OPENER.open('http://127.0.0.1:18741' + path, timeout=30) as response:
        return json.load(response)


manifest = read_json(DATA / 'package-recovery0929-manifest.json')
frozen = read_json(DATA / 'recovery0929-frozen-check.json')
integration = read_json(DATA / 'package-integration.json')
deployment = read_json(DATA / 'deployment-recovery0929.json')
assert manifest['ok'] and frozen['ok'] and integration['ok'] and not frozen['live_records_touched']
assert manifest['version'] == frozen['version'] == deployment['Version'] == VERSION
assert integration['health']['version'] == VERSION and integration['worker_status']['state'] == 'done'
assert digest(ROOT / ('App-%s/MAA自动推图助手.exe' % VERSION)) == manifest['exe_sha256']
assert digest(ROOT / 'web/index.html') == manifest['ui_sha256']
for relative, sha in manifest['source_sha256'].items():
    if relative != 'autostory\\__init__.py':
        assert digest(ROOT / relative) == sha, relative
source_log = (DATA / 'tests-recovery0929-source.log').read_text('utf-8-sig')
assert re.search(r'\nOK\s*$', source_log), 'source suite did not end OK'
source_tests = int(re.search(r'Ran (\d+) tests', source_log)[1])
assert deployment['RecordsUnchanged']
release = read_json(ROOT / 'active-release.json')
assert release == {'version': VERSION, 'directory': 'App-' + VERSION, 'sha256': manifest['exe_sha256']}, release

health = get('/api/health')
state = get('/api/state')
assert health['version'] == VERSION, health
assert not state['running'], state['status']

# The live order check: ordering lines are logged before the first navigation.
lines = [line for line in (DATA / 'activity.log').read_text('utf-8-sig').splitlines()
         if line.startswith('[' + LIVE_SCAN)]
order_lines = [line.split('] ', 1)[1] for line in lines
               if any(word in line for word in ('重排', '放到本轮最后', '后台导航到', '继续扫描', '已停止'))]
assert any('放到本轮最后' in line for line in order_lines), order_lines
first_nav = next(line for line in order_lines if line.startswith('后台导航到'))
assert first_nav == '后台导航到 HE-EX', first_nav     # 0.9.28 would have started at 17
scan = read_json(DATA / 'scan-report.json', {})
assert scan.get('planned_groups', [None])[0] == 'HE-EX' and len(scan.get('deferred_groups', [])) == 12, scan.get('deferred_groups')

progress_unchanged = digest(DATA / 'progress.json').upper() == deployment['After']['runtime\\progress.json'].upper()
assert progress_unchanged, 'progress.json changed after deployment'
prune = deployment['Prune']
live = {'ok': True, 'at': now(), 'health': health, 'status': state['status'],
        'source_tests': source_tests, 'frozen_tests': frozen['tests'], 'frozen_excluded': frozen['excluded'],
        'bundle_integration_ok': True, 'pruned_bundles': len(prune['Removed']), 'freed_mb': prune['FreedMB'],
        'kept_bundles': prune['Kept'],
        'scan_order_check': {'log': order_lines, 'first_group': first_nav.split()[-1],
                             'deferred_groups': scan['deferred_groups'], 'scan_state': scan.get('state'),
                             'progress_unchanged': progress_unchanged,
                             'note': 'started and stopped within one second during the 06:00-12:00 server '
                                     'maintenance; the order is decided before any device input'}}
write_json(DATA / 'recovery0929-live-proof.json', live)

report = read_json(ROOT / 'CORE_RELEASE_STATUS.json')
report.update(version=VERSION, deployed_version=VERSION, report='RELEASE_0929.md',
              state='Scan resumes the interrupted group after the collection-index reorder; '
                    'groups a finished pass could not advance are scanned last',
              exe_sha256=manifest['exe_sha256'], ui_sha256=manifest['ui_sha256'],
              source_tests=source_tests, frozen_tests=frozen['tests'], current_task=state['status'],
              latest_live_result='0.9.29 installed hot; live scan-all started at HE-EX (0.9.28 restarted at 17) '
                                 'with 12 dead-end groups deferred, then stopped by request; progress untouched.')
report['source_sha256'].update({k: v for k, v in manifest['source_sha256'].items() if k != 'autostory\\__init__.py'})
report['recovery0929'] = {
    'manifest': 'runtime/package-recovery0929-manifest.json',
    'source': 'runtime/tests-recovery0929-source.log',
    'frozen': 'runtime/recovery0929-frozen-check.json',
    'frozen_log': 'runtime/tests-frozen0929.log',
    'bundle_integration': 'runtime/package-integration.json',
    'deployment': 'runtime/deployment-recovery0929.json',
    'live_proof': 'runtime/recovery0929-live-proof.json',
    'pre_change_sources': 'runtime/backups/pre0929',
    'changed_modules': manifest['changed_modules'],
    'notes': [
        'arrange_groups runs after order_by_index, which had moved the interrupted group behind every digit chapter',
        'Deferred = every unconfirmed key is unseen after a finished pass, or a raid whose normal node is confirmed but not cleared',
        'Also deferred: last-round entry errors (failed_groups, or chapters[].error of pre-0.9.29 reports) and nav-hints absent base chapters',
        'Ordering only: nothing is dropped, confirmed or marked cleared; deferred groups are still scanned at the end of the round',
        'scan-report.json and nav-hints.json added to the install guard list',
    ]}
write_json(ROOT / 'CORE_RELEASE_STATUS.json', report)

readme = ROOT / 'README.md'
text = readme.read_text('utf-8-sig')
if not text.startswith('> **当前统一版本：%s' % VERSION):
    text = text.replace('> **当前统一版本：0.9.28', '> **历史版本：0.9.28', 1)
    readme.write_text(
        '> **当前统一版本：0.9.29（2026-11-01）**。「扫描未通过关卡」中断后从上次停下的分区继续，不再被乐章收录排序挤回第 1 章；'
        '上一轮已在地图上找不到、突袭需先通关普通难度、或分区入口没找到的分区放到本轮最后再查（仍会查，不会被判为已通关）。'
        '%d 项源码、%d 项冻结字节码、打包隔离检查通过。详见 [0.9.29 说明](RELEASE_0929.md)。\n\n' % (source_tests, frozen['tests']) + text,
        encoding='utf-8')
print(json.dumps(live, ensure_ascii=False, indent=2))
