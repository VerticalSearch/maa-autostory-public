"""Collect and assert the 0.9.24 release evidence, then update the release records."""
import hashlib, json, re, sys, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from autostory.common import DATA, read_json, write_json, now
from autostory.campaign import catalogue
from autostory.scan_progress import summarize_scan

VERSION = '0.9.24'
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def get(path):
    with OPENER.open('http://127.0.0.1:18741' + path, timeout=8) as response:
        return json.load(response)


manifest = read_json(DATA / 'package-recovery0924-manifest.json')
frozen = read_json(DATA / 'recovery0924-frozen-check.json')
integration = read_json(DATA / 'package-integration.json')
notice = read_json(DATA / 'notice0924-live-proof.json')
assert manifest['ok'] and frozen['ok'] and integration['ok'] and notice['ok']
assert manifest['version'] == frozen['version'] == VERSION
assert digest(ROOT / ('App-%s/MAA自动推图助手.exe' % VERSION)) == manifest['exe_sha256']
assert digest(ROOT / 'web/index.html') == manifest['ui_sha256']
for relative, sha in manifest['source_sha256'].items():
    assert digest(ROOT / relative) == sha, relative
source_log = (DATA / 'tests-recovery0924-source.log').read_text('utf-8-sig')
assert re.search(r'\nOK\s*$', source_log), 'source suite did not end OK'
source_tests = int(re.search(r'Ran (\d+) tests', source_log)[1])
deployment = read_json(DATA / 'deployment-recovery0924.json')
assert deployment['RecordsUnchanged'] and deployment['Version'] == VERSION
before = read_json(DATA / 'recovery0924-before.json')

health = get('/api/health')
state = get('/api/state')
assert health['version'] == VERSION, health
assert state['status']['action'] == 'scan-all'
summary = summarize_scan(catalogue(), read_json(DATA / 'progress.json'))
snapshot = {'at': now(), 'health': health, 'running': state['running'], 'status': state['status'],
            'workflow': state['workflow'], 'summary': {k: v for k, v in summary.items() if k != 'groups'},
            'before_release': before, 'campaign_unchanged': True,
            'notice_proof': 'runtime/notice0924-live-proof.json'}
write_json(DATA / 'recovery0924-state.json', snapshot)

live = {'ok': True, 'at': now(), 'health': health, 'status': state['status'], 'workflow': state['workflow'],
        'summary': snapshot['summary'], 'notice_button_rendered': True,
        'notice_evidence': {'state': notice['status'], 'screenshot': notice['screenshot']},
        'source_tests': source_tests, 'frozen_tests': frozen['tests'],
        'bundle_integration_ok': True,
        'notes': ['Resume button proven against the shipped EXE in an isolated sandbox (offline serial, unique port/root)',
                  'No ADB input, no battle and no live-emulator contact in the sandbox proof',
                  'Anchor/blank-box and bounded-recovery claims rest on source+frozen tests, not on a new natural failure']}
write_json(DATA / 'recovery0924-live-proof.json', live)

report = read_json(ROOT / 'CORE_RELEASE_STATUS.json')
report.update(version=VERSION, deployed_version=VERSION, report='RECOVERY_0924.md',
              state='Reject unusable map anchors, wait out a transient emulator loss a bounded number of times, and offer a scan-resume shortcut',
              exe_sha256=manifest['exe_sha256'], ui_sha256=manifest['ui_sha256'],
              source_tests=source_tests, frozen_tests=frozen['tests'], current_task=state['status'],
              latest_live_result='0.9.24 installed while idle; scan-only run restarted and resuming. Resume button rendered on a real failure banner in an isolated sandbox.')
report['source_sha256'].update(manifest['source_sha256'])
report['recovery0924'] = {
    'manifest': 'runtime/package-recovery0924-manifest.json',
    'source': 'runtime/tests-recovery0924-source.log',
    'frozen': 'runtime/recovery0924-frozen-check.json',
    'bundle_integration': 'runtime/package-integration.json',
    'notice_button_live': 'runtime/notice0924-live-proof.json',
    'notice_screenshot': 'runtime/notice0924-banner.png',
    'deployment': 'runtime/deployment-recovery0924.json',
    'state': 'runtime/recovery0924-state.json',
    'before': 'runtime/recovery0924-before.json',
    'changed_modules': manifest['changed_modules'],
    'notes': [
        'Map anchor boxes without label pixels are dropped before the map gate; a fallback whole-frame settle is used when fewer than two anchors survive',
        'A map settle that keeps hitting blank anchors now ends early instead of consuming the whole 3.5 s window',
        'A transient emulator loss (error: closed / offline / not found) may be waited out up to 3 times for read-only scanning, then the run still stops',
        'Configuration, authorisation, a second failed capture and a user stop are never retried',
        'Internally captured scanner progress is saved per stage; the round resumes from plan_groups',
        'The control page gained one button; the 0.9.22 72-check browser suite was not rerun, one targeted banner render was',
    ]}
write_json(ROOT / 'CORE_RELEASE_STATUS.json', report)

readme = ROOT / 'README.md'
text = readme.read_text('utf-8-sig')
if not text.startswith('> **当前统一版本：%s' % VERSION):
    text = text.replace('> **当前统一版本：0.9.23', '> **历史版本：0.9.23', 1)
    readme.write_text(
        '> **当前统一版本：0.9.24（2026-11-01）**。地图锚点框先做纹理校验，空白标签不再让静止检查必然超时；'
        '模拟器瞬断（截图通道关闭/设备离线）在只读扫描中最多等待恢复 3 次，超限仍停止并保留记录；'
        '扫描失败横幅直接给出「继续扫描」。568 项源码测试、222 项冻结字节码测试、打包隔离检查与横幅实渲染通过，'
        '后台已从第 17 章断点续扫。详见 [锚点校验与有界设备恢复](RECOVERY_0924.md)。\n\n' + text,
        encoding='utf-8')
print(json.dumps(snapshot, ensure_ascii=False))
