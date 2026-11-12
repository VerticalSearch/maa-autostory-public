"""0.9.28: support unit for one missing operator, free formal stages, training report,
resume/stale-status repair, cached GUI-idle check, shorter blind waits, bounded logs.

Baseline is the verified 0.9.27 executable; only the changed modules are
re-compiled into the embedded PYZ. ``autostory.training_report`` and
``autostory.housekeeping`` are new modules; ``worker`` is a top-level module.
"""
import importlib.util
import json
import marshal
from pathlib import Path
import shutil
import struct
import zlib
from PyInstaller.archive.readers import CArchiveReader
from PyInstaller.utils.win32 import versioninfo
from build_ui_release import PayloadWriter,digest

ROOT=Path(__file__).resolve().parents[1]
BASE=ROOT/'App-0.9.27'
TARGET=ROOT/'App-0.9.28'
EXENAME='MAA自动推图助手.exe'
VERSION='0.9.28'
CHANGED=('autostory','autostory.attempts','autostory.campaign','autostory.common','autostory.controller',
         'autostory.dashboard','autostory.device','autostory.formation_recovery','autostory.jobs',
         'autostory.operator_details','autostory.runtime_state','autostory.skill_training',
         'autostory.training_report','autostory.housekeeping','worker')
NEW_MODULES={'autostory.training_report','autostory.housekeeping'}

def source_path(name):
    if name=='autostory':return ROOT/'autostory/__init__.py'
    return ROOT/(name.replace('.','/')+'.py')

def main():
    if TARGET.exists():raise RuntimeError('Release target already exists')
    baseline=json.loads((ROOT/'runtime/package-recovery0927-manifest.json').read_text('utf-8-sig'))
    if not baseline['ok'] or digest(BASE/EXENAME)!=baseline['exe_sha256']:raise RuntimeError('Unverified baseline')
    old=CArchiveReader(str(BASE/EXENAME));pyz=old.open_embedded_archive('PYZ.pyz')
    data=old.extract('PYZ.pyz')
    if data[4:8]!=importlib.util.MAGIC_NUMBER:raise RuntimeError('Bytecode mismatch')
    if {n for n in CHANGED if n not in pyz.toc}!=NEW_MODULES:raise RuntimeError('Unexpected module set')
    work=ROOT/'_tmp/recovery0928-source'
    if work.exists():shutil.rmtree(work)
    work.mkdir(parents=True)
    payloads={};hashes={}
    for name in CHANGED:
        path=source_path(name)
        source=path.read_text('utf-8-sig')
        if name=='autostory':
            source='"""Background-only Arknights story controller."""\n__version__ = "%s"\n'%VERSION
        copy=work/path.relative_to(ROOT);copy.parent.mkdir(parents=True,exist_ok=True)
        if name=='autostory':copy.write_text(source,encoding='utf-8')
        else:shutil.copy2(path,copy)
        payloads[name]=marshal.dumps(compile(source,str(path.relative_to(ROOT)),'exec'))
        hashes[str(path.relative_to(ROOT))]=digest(copy)
    new_pyz=bytearray(data[:16]);toc=[]
    for name in list(pyz.toc)+[name for name in CHANGED if name not in pyz.toc]:
        kind=pyz.toc[name][0] if name in pyz.toc else 0
        raw=payloads[name] if name in payloads else pyz.extract(name,raw=True)
        compressed=zlib.compress(raw,6) if raw is not None else b''
        toc.append((name,(kind,len(new_pyz),len(compressed))));new_pyz.extend(compressed)
    struct.pack_into('!i',new_pyz,8,len(new_pyz));new_pyz.extend(marshal.dumps(toc))
    entries=[(name,bytes(new_pyz) if name=='PYZ.pyz' else old.extract(name),spec[3],spec[4]) for name,spec in old.toc.items()]
    entries.extend((name,b'',False,'o') for name in old.options)
    original=(BASE/EXENAME).read_bytes();cookie=original[old._end_offset-old._COOKIE_LENGTH:old._end_offset]
    *_,pylib=struct.unpack(old._COOKIE_FORMAT,cookie)
    pkg=work/'recovery0928.pkg';PayloadWriter(str(pkg),entries,pylib.rstrip(b'\0').decode())
    TARGET.mkdir();exe=TARGET/EXENAME;exe.write_bytes(original[:old._start_offset])
    ver=work/'version.txt'
    ver.write_text((ROOT/'packaging/version-ui-081.txt').read_text('utf-8-sig')
        .replace('(0,8,1,0)','(0,9,28,0)').replace('0.8.1',VERSION),encoding='utf-8')
    versioninfo.write_version_info_to_executable(str(exe),versioninfo.load_version_info_from_text_file(str(ver)))
    with exe.open('ab') as output:output.write(pkg.read_bytes())
    for folder in ('_internal','webview'):shutil.copytree(BASE/folder,TARGET/folder)
    new=CArchiveReader(str(exe));archive=new.open_embedded_archive('PYZ.pyz')
    assert new.options==old.options and set(new.toc)==set(old.toc)
    for name in old.toc:
        if name!='PYZ.pyz':assert old.extract(name)==new.extract(name),name
    for name in archive.toc:
        expected=payloads[name] if name in payloads else pyz.extract(name,raw=True)
        assert archive.extract(name,raw=True)==expected,name
    assert set(archive.toc)==set(pyz.toc)|set(CHANGED)
    for folder in ('_internal','webview'):
        for path in (BASE/folder).rglob('*'):
            if path.is_file():assert digest(path)==digest(TARGET/path.relative_to(BASE)),path
    for name in CHANGED:
        if name=='autostory':continue
        path=source_path(name)
        assert digest(path)==hashes[str(path.relative_to(ROOT))],path
    assert digest(BASE/EXENAME)==baseline['exe_sha256'],'Baseline changed during build'
    shutil.copy2(ROOT/'web/index.html',work/'index.html')
    report={'ui_sha256':digest(work/'index.html'),'ok':True,'version':VERSION,'baseline_version':'0.9.27',
        'baseline_sha256':baseline['exe_sha256'],'exe_sha256':digest(exe),'changed_modules':list(CHANGED),
        'source_sha256':hashes,'new_modules':sorted(NEW_MODULES),
        'other_modules_unchanged':True,'desktop_entry_unchanged':True,'runtime_and_webview_unchanged':True,
        'snapshot':str(work)}
    (ROOT/'runtime/package-recovery0928-manifest.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False))

if __name__=='__main__':main()
