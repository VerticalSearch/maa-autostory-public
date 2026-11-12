"""Recreate the actual 42 missing + 12 stale files in an isolated MAA tree."""
import argparse,hashlib,json,shutil,sys,zipfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
parser=argparse.ArgumentParser();parser.add_argument('--frozen',action='store_true');args=parser.parse_args()
if args.frozen:
    from frozen_logic_loader import install
    install('App-0.9.39')
from autostory.resource_preflight import ensure_resources
missing=json.loads((ROOT/'runtime/live0938-resource-repair.json').read_text('utf-8'))
stale=json.loads((ROOT/'runtime/live0938-theme-repair.json').read_text('utf-8'))
archive=Path(missing['archive'])
kind='frozen' if args.frozen else 'source'
scratch=ROOT/'_tmp'/f'resource0939-replay-{kind}'
scratch.mkdir(parents=True,exist_ok=False)
maa=scratch/'maa';maa.mkdir()
shutil.copy2(archive,maa/archive.name)
excluded={entry['path'] for entry in missing['restored']}
old={entry['path']:entry for entry in stale['files']}
with zipfile.ZipFile(archive) as z:
    for name in z.namelist():
        if not (name=='MaaCore.dll' or name.startswith('resource/')) or name.endswith('/') or name in excluded:continue
        target=maa/name
        assert target.resolve().is_relative_to(maa.resolve())
        target.parent.mkdir(parents=True,exist_ok=True)
        if name in old:
            data=(Path(stale['backup'])/name).read_bytes()
            assert hashlib.sha256(data).hexdigest()==old[name]['before_sha256']
        else:data=(ROOT.parent/name).read_bytes()
        target.write_bytes(data)
first=ensure_resources(maa,scratch/'audit')
assert {row['path'] for row in first['copied_missing']}==excluded
assert {row['path'] for row in first['replaced_theme']}==set(old)
assert first['net_writes']==54
for row in first['replaced_theme']:
    assert hashlib.sha256(Path(row['backup']).read_bytes()).hexdigest()==old[row['path']]['before_sha256']
second=ensure_resources(maa,scratch/'audit')
assert second['net_writes']==0 and not second['missing_before']
report=dict(ok=True,frozen=args.frozen,copied_missing=len(first['copied_missing']),replaced_theme=len(first['replaced_theme']),
            idempotent_writes=second['net_writes'],untouched_mismatches=len(second['untouched_mismatches']),
            first_audit=first['audit'],second_audit=second['audit'])
(ROOT/'runtime'/f'autonomy0939-resource-replay-{kind}.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(report,ensure_ascii=False))
