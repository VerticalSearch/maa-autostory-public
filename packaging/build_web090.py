"""Preserve 0.8.2 logic; update desktop launch and the unified version label."""
import importlib.util
import json
import marshal
from pathlib import Path
import shutil
import struct
import zlib
from PyInstaller.archive.readers import CArchiveReader
from PyInstaller.utils.win32 import versioninfo
from build_ui_release import PayloadWriter, digest

ROOT=Path(__file__).resolve().parents[1]
BASE=ROOT/'App-0.8.2'
TARGET=ROOT/'App-0.9.0'
EXENAME='MAA自动推图助手.exe'

def main():
    if TARGET.exists():raise RuntimeError('Release already exists; do not overwrite it')
    old=CArchiveReader(str(BASE/EXENAME))
    old_entry=marshal.loads(old.extract('desktop'))
    source=ROOT/'desktop.py'
    payload=marshal.dumps(compile(source.read_text('utf-8-sig'),old_entry.co_filename,'exec'))
    old_pyz=old.open_embedded_archive('PYZ.pyz')
    version_payload=marshal.dumps(compile((ROOT/'autostory/__init__.py').read_text('utf-8-sig'),old_pyz.extract('autostory').co_filename,'exec'))
    raw_pyz=old.extract('PYZ.pyz')
    assert raw_pyz[4:8]==importlib.util.MAGIC_NUMBER
    pyz=bytearray(raw_pyz[:16]);toc=[]
    for name,(kind,_,_) in old_pyz.toc.items():
        raw_module=version_payload if name=='autostory' else old_pyz.extract(name,raw=True)
        blob=zlib.compress(raw_module,6) if raw_module is not None else b''
        toc.append((name,(kind,len(pyz),len(blob))));pyz.extend(blob)
    struct.pack_into('!i',pyz,8,len(pyz));pyz.extend(marshal.dumps(toc))
    entries=[(name,payload if name=='desktop' else bytes(pyz) if name=='PYZ.pyz' else old.extract(name),spec[3],spec[4]) for name,spec in old.toc.items()]
    entries += [(name,b'',False,'o') for name in old.options]
    raw=(BASE/EXENAME).read_bytes()
    cookie=raw[old._end_offset-old._COOKIE_LENGTH:old._end_offset]
    *_,pylib=struct.unpack(old._COOKIE_FORMAT,cookie)
    work=ROOT/'_tmp/web-release-090'
    work.mkdir(parents=True,exist_ok=True)
    pkg=work/'web.pkg'
    PayloadWriter(str(pkg),entries,pylib.rstrip(b'\0').decode())
    TARGET.mkdir()
    exe=TARGET/EXENAME
    exe.write_bytes(raw[:old._start_offset])
    version_file=work/'version.txt'
    version_file.write_text((ROOT/'packaging/version-ui-081.txt').read_text('utf-8').replace('(0,8,1,0)','(0,9,0,0)').replace('0.8.1','0.9.0'),encoding='utf-8')
    versioninfo.write_version_info_to_executable(str(exe),versioninfo.load_version_info_from_text_file(str(version_file)))
    with exe.open('ab') as f:f.write(pkg.read_bytes())
    shutil.copytree(BASE/'_internal',TARGET/'_internal')
    shutil.copytree(ROOT/'_tmp/webhost-090',TARGET/'webview')
    current=CArchiveReader(str(exe))
    assert set(old.toc)==set(current.toc) and old.options==current.options
    changed=[name for name in old.toc if old.extract(name)!=current.extract(name)]
    assert changed==['desktop','PYZ.pyz'],changed
    new_pyz=current.open_embedded_archive('PYZ.pyz')
    changed_modules=[name for name in old_pyz.toc if old_pyz.extract(name,raw=True)!=new_pyz.extract(name,raw=True)]
    assert changed_modules==['autostory'],changed_modules
    runtime=list((BASE/'_internal').rglob('*'))
    for path in runtime:
        if path.is_file():assert digest(path)==digest(TARGET/path.relative_to(BASE)),path
    host_files={str(p.relative_to(TARGET/'webview')):digest(p) for p in (TARGET/'webview').rglob('*') if p.is_file()}
    report={'ok':True,'version':'0.9.0','baseline':str(BASE),'target':str(TARGET),'changed_archive_entries':changed,
        'backend_logic_unchanged':True,'changed_frozen_modules':changed_modules,'frozen_change_scope':'package version label only',
        'unchanged_runtime_files':sum(p.is_file() for p in runtime),
        'launcher_source_sha256':digest(source),'exe_sha256':digest(exe),'host_files':host_files,
        'web_source_sha256':digest(ROOT/'web/index.html'),'backend_version':'0.9.0'}
    (ROOT/'runtime/package-web-090-manifest.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
