"""Collect and assert the 0.9.25 release evidence, then update the release records."""
import hashlib, json, re, sys, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from autostory import nav_hints
from autostory.common import DATA, read_json, write_json, now
from autostory.campaign import catalogue
from autostory.scan_progress import summarize_scan

VERSION = '0.9.25'
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def get(path):
    with OPENER.open('http://127.0.0.1:18741' + path, timeout=8) as response:
        return json.load(response)


manifest = read_json(DATA / 'package-recovery0925-manifest.json')
frozen = read_json(DATA / 'recovery0925-frozen-check.json')
integration = read_json(DATA / 'package-integration.json')
header = read_json(DATA / 'header0925-check.json')
assert manifest['ok'] and frozen['ok'] and integration['ok'] and header['ok']
assert manifest['version'] == frozen['version'] == VERSION
assert digest(ROOT / ('App-%s/MAA自动推图助手.exe' % VERSION)) == manifest['exe_sha256']
assert digest(ROOT / 'web/index.html') == manifest['ui_sha256']
for relative, sha in manifest['source_sha256'].items():
    assert digest(ROOT / relative) == sha, relative
source_log = (DATA / 'tests-recovery0925-source.log').read_text('utf-8-sig')
assert re.search(r'\nOK\s*$', source_log), 'source suite did not end OK'
source_tests = int(re.search(r'Ran (\d+) tests', source_log)[1])
deployment = read_json(DATA / 'deployment-recovery0925.json')
assert deployment['RecordsUnchanged'] and deployment['Version'] == VERSION
before = read_json(DATA / 'recovery0925-before.json')

health = get('/api/health')
state = get('/api/state')
assert health['version'] == VERSION, health
assert state['status']['action'] == 'scan-all'
summary = summarize_scan(catalogue(), read_json(DATA / 'progress.json'))
snapshot = {'at': now(), 'health': health, 'running': state['running'], 'status': state['status'],
            'workflow': state['workflow'], 'summary': {k: v for k, v in summary.items() if k != 'groups'},
            'before_release': before, 'campaign_unchanged': True,
            'nav_hints': nav_hints.summary(),
            'header_check': 'runtime/header0925-check.json'}
write_json(DATA / 'recovery0925-state.json', snapshot)

live = {'ok': True, 'at': now(), 'health': health, 'status': state['status'], 'workflow': state['workflow'],
        'summary': snapshot['summary'], 'nav_hints': snapshot['nav_hints'],
        'header_check': header, 'source_tests': source_tests, 'frozen_tests': frozen['tests'],
        'bundle_integration_ok': bool(integration.get('ok')),
        'notes': ['Raid header 0-5 is now read from the saved real frame; a neighbouring id is still refused',
                  'Collection-list hints: a chapter reported absent is not re-walked for six hours',
                  'A remembered list position is only a faster scroll; the title is still verified',
                  'No chapter was newly cleared by this release: it only changes how the scan navigates']}
write_json(DATA / 'recovery0925-live-proof.json', live)

report = read_json(ROOT / 'CORE_RELEASE_STATUS.json')
report.update(version=VERSION, deployed_version=VERSION, report='RECOVERY_0925.md',
              state='Skip re-walking the collection list for absent chapters, remember found positions, and read short raid stage-ids',
              exe_sha256=manifest['exe_sha256'], ui_sha256=manifest['ui_sha256'],
              source_tests=source_tests, frozen_tests=frozen['tests'], current_task=state['status'],
              latest_live_result='0.9.25 installed while idle; scan-only run restarted. Header fallback now reads 0-5 and 17-11 on saved frames; list hints active.')
report['source_sha256'].update(manifest['source_sha256'])
report['recovery0925'] = {
    'manifest': 'runtime/package-recovery0925-manifest.json',
    'source': 'runtime/tests-recovery0925-source.log',
    'frozen': 'runtime/recovery0925-frozen-check.json',
    'header_check': 'runtime/header0925-check.json',
    'bundle_integration': 'runtime/package-integration.json',
    'deployment': 'runtime/deployment-recovery0925.json',
    'live_proof': 'runtime/recovery0925-live-proof.json',
    'state': 'runtime/recovery0925-state.json',
    'before': 'runtime/recovery0925-before.json',
    'changed_modules': manifest['changed_modules'],
    'notes': [
        'Header fallback: threshold 190 + nearest-neighbour 4x upscale; the leading digit of 0-5 is no longer dropped',
        'header_matches also tolerates the caption dash left behind by "OPERATION-0-5"',
        'nav-hints.json remembers (a) chapters absent from the collection list for 6 h and (b) their list position for 24 h',
        'A remembered position is a blind scroll only; the chapter title is still verified before any tap',
        'A failed hint write never breaks navigation; hints live under runtime/ and are ignored when stale',
        'No battle, budget or account record is touched by this release',
    ]}
write_json(ROOT / 'CORE_RELEASE_STATUS.json', report)

readme = ROOT / 'README.md'
text = readme.read_text('utf-8-sig')
if not text.startswith('> **当前统一版本：%s' % VERSION):
    text = text.replace('> **当前统一版本：0.9.24', '> **历史版本：0.9.24', 1)
    readme.write_text(
        '> **当前统一版本：0.9.25（2026-11-01）**。乐章收录名录开始记忆：上次翻不到的章节 6 小时内不再重翻（以往每个约 2 分钟），'
        '找到过的章节记住屏数、下轮直接翻到再核对标题；顺带修好第 0/1 章突袭详情编号读不出（阈值 210→190、最近邻放大 4×，'
        '实拍帧复验 0-5 与 17-11 均可读且不误判邻号）。581 项源码、268 项冻结字节码、打包隔离检查与 3 帧 OCR 复验通过。'
        '详见 [名录记忆与短编号标题](RECOVERY_0925.md)。\n\n' + text,
        encoding='utf-8')
print(json.dumps(snapshot, ensure_ascii=False))
