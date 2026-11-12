"""Seal offline algorithm evidence; never install, launch or modify checkpoints."""
from datetime import datetime
import hashlib
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from autostory.runtime_state import TaskLock


def read(path):
    return json.loads(path.read_text('utf-8-sig'))


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


lock = TaskLock(ROOT / 'runtime/alltasks.lock')
if not lock.acquire():
    raise RuntimeError('An existing task owns the runtime')
try:
    runtime = ROOT / 'runtime'
    evidence = ROOT / 'release/evidence/algorithm0938'
    baseline = ROOT / '_tmp/algorithm0938-before'
    manifest = read(runtime / 'package-algorithm0938-manifest.json')
    active = read(ROOT / 'active-release.json')
    assert active['version'] == '0.9.36'
    assert digest(ROOT / 'active-release.json') == digest(baseline / 'active-release.json')
    assert digest(ROOT / 'App-0.9.38/MAA自动推图助手.exe') == manifest['exe_sha256']
    assert digest(ROOT / 'App-0.9.37/MAA自动推图助手.exe') == manifest['baseline_sha256']
    assert digest(ROOT / 'App-0.9.36/MAA自动推图助手.exe') == active['sha256']
    for name, sha in (manifest['source_sha256'] | manifest['asset_sha256']).items():
        assert digest(ROOT / name) == sha, name
    previous = baseline / 'autostory'
    current = ROOT / 'autostory'
    names = {p.relative_to(previous) for p in previous.rglob('*.py')} | {
        p.relative_to(current) for p in current.rglob('*.py')}
    changed = [str(p) for p in sorted(names) if not (previous/p).exists()
               or not (current/p).exists() or digest(previous/p) != digest(current/p)]
    assert set(changed) == {str(Path(p).relative_to('autostory')) for p in manifest['source_sha256']}
    assert digest(previous / 'motion.py') == digest(current / 'motion.py')
    checks = {}
    for path in (runtime / 'backups/algorithm0938-before').iterdir():
        target = ROOT/path.name if path.name == 'settings.json' else runtime/path.name
        checks[path.name] = {'unchanged': digest(path) == digest(target), 'sha256': digest(target)}
    assert len(checks) == 8 and all(c['unchanged'] for c in checks.values())
    validation = {kind: read(runtime/f'algorithm0938-{kind}-check.json') for kind in ('source', 'frozen')}
    replays = {kind: read(runtime/f'algorithm0938-{kind}-replay.json') for kind in ('source', 'frozen')}
    for result in validation.values():
        assert result['ok'] and not result['live_records_touched'] and result['skipped'] == 0
    for result in replays.values():
        assert result['ok'] and result['protected_files_unchanged']
        assert [r['boundary'] for r in result['retained_frame_sequence']] == [False, False, True, True]
        assert not any(r['boundary'] for r in result['same_identifiers_moving_positions'])
        assert len(result['controlled_motion_sequences']) == 12
    log = (runtime / 'tests-algorithm0938-source.log').read_text('utf-8').splitlines()
    focused = sum(line.endswith(' ... ok') and any(name in line for name in (
        'test_map_traversal0938.', 'test_pan_review0938.', 'test_pan_navigation0938.')) for line in log)
    scan = read(runtime / 'scan-report.json')
    at = datetime.now().astimezone().isoformat(timespec='seconds')
    names = ['package-algorithm0938-manifest.json', 'algorithm0938-source-check.json',
             'algorithm0938-frozen-check.json', 'algorithm0938-source-replay.json',
             'algorithm0938-frozen-replay.json', 'tests-algorithm0938-source.log',
             'tests-algorithm0938-frozen.log']
    proof = {'version': '0.9.38', 'at': at, 'status': 'algorithm_verified_pending_live',
        'offline_algorithm_verified': True, 'deployed': False, 'native_runtime_verified': False,
        'full_flow_verified': False, 'installed_release': active,
        'candidate_baseline': '0.9.37', 'exe_sha256': manifest['exe_sha256'],
        'focused_tests': focused, 'regression': validation,
        'source_sha256': manifest['source_sha256'], 'changed_production_files': changed,
        'motion_thresholds_unchanged': True, 'protected_files': checks,
        'checkpoint': {k: v for k, v in scan['summary'].items() if k != 'groups'},
        'replay': replays, 'evidence_sha256': {name: digest(runtime/name) for name in names},
        'limits': ['Four retained SN frames with actual per-frame OCR; other maps lack live evidence',
                   'Moving labels, background changes and traversal integration are controlled offline cases',
                   'Boundary is a bounded search stopping rule; it does not confirm a clear or full coverage',
                   'No EXE launch, activation, resumed scan or game input in this iteration']}
    write(runtime / 'algorithm0938-proof.json', proof)
    task = read(runtime / 'algorithm0938-task.json')
    task.update(status=proof['status'], updated=at, proof='runtime/algorithm0938-proof.json',
                deferred=['Native candidate validation', 'Activation', 'Scan resume'])
    write(runtime / 'algorithm0938-task.json', task)
    core = read(ROOT / 'CORE_RELEASE_STATUS.json')
    assert core['deployed_version'] == core['version'] == '0.9.36'
    core['source_version'] = '0.9.38'
    core['algorithm_candidate'] = {'version': '0.9.38', 'state': proof['status'],
        'supersedes_candidate': '0.9.37', 'deployed': False, 'native_runtime_verified': False,
        'source_tests': validation['source']['tests'], 'frozen_tests': validation['frozen']['tests'],
        'report': 'ALGORITHM_0938.md', 'proof': 'runtime/algorithm0938-proof.json',
        'manifest': 'runtime/package-algorithm0938-manifest.json', 'exe_sha256': manifest['exe_sha256']}
    write(ROOT / 'CORE_RELEASE_STATUS.json', core)
    for name in names + ['algorithm0938-proof.json', 'algorithm0938-task.json']:
        shutil.copy2(runtime/name, evidence/name)
    print(json.dumps({'status': proof['status'], 'focused_tests': focused,
        'source_tests': validation['source']['tests'], 'frozen_tests': validation['frozen']['tests'],
        'confirmed': proof['checkpoint']['confirmed'], 'installed_version': active['version']}))
finally:
    lock.release()
