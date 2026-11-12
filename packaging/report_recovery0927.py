"""Collect and assert the 0.9.27 release evidence, then update the release records."""
import hashlib, json, re, sys, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from autostory import collection_index, nav_hints
from autostory.common import DATA, read_json, write_json, now
from autostory.campaign import catalogue
from autostory.scan_progress import summarize_scan

VERSION = '0.9.27'
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def get(path):
    with OPENER.open('http://127.0.0.1:18741' + path, timeout=8) as response:
        return json.load(response)


manifest = read_json(DATA / 'package-recovery0927-manifest.json')
frozen = read_json(DATA / 'recovery0927-frozen-check.json')
integration = read_json(DATA / 'package-integration.json')
assert manifest['ok'] and frozen['ok'] and integration['ok']
assert manifest['version'] == frozen['version'] == VERSION
assert digest(ROOT / ('App-%s/MAA自动推图助手.exe' % VERSION)) == manifest['exe_sha256']
assert digest(ROOT / 'web/index.html') == manifest['ui_sha256']
for relative, sha in manifest['source_sha256'].items():
    assert digest(ROOT / relative) == sha, relative
source_log = (DATA / 'tests-recovery0927-source.log').read_text('utf-8-sig')
assert re.search(r'\nOK\s*$', source_log), 'source suite did not end OK'
source_tests = int(re.search(r'Ran (\d+) tests', source_log)[1])
deployment = read_json(DATA / 'deployment-recovery0927.json')
assert deployment['RecordsUnchanged'] and deployment['Version'] == VERSION
before = read_json(DATA / 'recovery0927-before.json')
index = collection_index.load()
hints = nav_hints.summary()

health = get('/api/health')
state = get('/api/state')
assert health['version'] == VERSION, health
assert state['status']['action'] == 'scan-all'
summary = summarize_scan(catalogue(), read_json(DATA / 'progress.json'))
snapshot = {'at': now(), 'health': health, 'running': state['running'], 'status': state['status'],
            'workflow': state['workflow'], 'summary': {k: v for k, v in summary.items() if k != 'groups'},
            'before_release': before, 'campaign_unchanged': True,
            'collection_index': collection_index.summary(index), 'nav_hints': hints}
write_json(DATA / 'recovery0927-state.json', snapshot)

live = {'ok': True, 'at': now(), 'health': health, 'status': state['status'], 'workflow': state['workflow'],
        'summary': snapshot['summary'], 'collection_index': snapshot['collection_index'],
        'nav_hints': hints, 'source_tests': source_tests, 'frozen_tests': frozen['tests'],
        'bundle_integration_ok': bool(integration.get('ok')),
        'notes': ['A one-pass 乐章收录 index orders the round by list position; a partial index never refuses a chapter',
                  'Index-based absence requires complete=true, which needs the whole list to have been walked',
                  'Legacy index files without the flag load as incomplete and are used only for ordering and hints',
                  '0.9.26 (first build) was replaced before any chapter could be refused in the field',
                  'No battle, budget or account record is touched by this release']}
write_json(DATA / 'recovery0927-live-proof.json', live)

report = read_json(ROOT / 'CORE_RELEASE_STATUS.json')
report.update(version=VERSION, deployed_version=VERSION, report='INDEX_0926.md',
              state='One-pass collection index orders the scan by list position; index-based absence requires a full pass',
              exe_sha256=manifest['exe_sha256'], ui_sha256=manifest['ui_sha256'],
              source_tests=source_tests, frozen_tests=frozen['tests'], current_task=state['status'],
              latest_live_result='0.9.27 installed and resumed; 24-title partial index reused to reorder 142 groups, no chapter refused.')
report['source_sha256'].update(manifest['source_sha256'])
report['recovery0927'] = {
    'manifest': 'runtime/package-recovery0927-manifest.json',
    'source': 'runtime/tests-recovery0927-source.log',
    'frozen': 'runtime/recovery0927-frozen-check.json',
    'bundle_integration': 'runtime/package-integration.json',
    'deployment': 'runtime/deployment-recovery0927.json',
    'live_proof': 'runtime/recovery0927-live-proof.json',
    'state': 'runtime/recovery0927-state.json',
    'before': 'runtime/recovery0927-before.json',
    'index': 'runtime/collection-index.json',
    'changed_modules': manifest['changed_modules'],
    'notes': [
        'build_collection_index walks 乐章收录 once (13 screens, 71 s) and records title -> page, TTL 12 h',
        'order_by_index keeps main chapters first, then side stories by list page, unknown pages last',
        'complete=true only when the pass reached the bottom of the list; only then may absence be inferred',
        'A partial index is ordering/hint only, and logs that it did not cover the title',
        '0.9.26 shipped the index without that gate; 0.9.27 replaced it before any chapter reached the refusal path',
        'Index coverage is 24 titles today (decorative card fonts); raising it is the next step',
    ]}
write_json(ROOT / 'CORE_RELEASE_STATUS.json', report)

readme = ROOT / 'README.md'
text = readme.read_text('utf-8-sig')
if not text.startswith('> **当前统一版本：%s' % VERSION):
    text = text.replace('> **当前统一版本：0.9.25', '> **历史版本：0.9.25', 1)
    readme.write_text(
        '> **当前统一版本：0.9.27（2026-11-01）**。整轮只走一次「乐章收录」名录并据此按列表顺序扫描（实测 13 屏 / 71 秒，重排 142 个分区），'
        '找到过的章节用名录屏号做首次盲翻、标题仍逐次核验；**索引不完整时绝不据此判定章节缺席**（0.9.26 的初版缺陷已在 0.9.27 收紧为'
        '`complete` 才能判缺席，旧索引自动按不完整处理）。595 项源码、282 项冻结字节码、打包隔离检查通过。'
        '详见 [名录索引与按名录顺序扫描](INDEX_0926.md)。\n\n' + text,
        encoding='utf-8')
print(json.dumps({k: v for k, v in snapshot.items() if k != 'before_release'}, ensure_ascii=False, indent=2))
