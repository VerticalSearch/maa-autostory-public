"""Overlay verified logic modules on the 0.6.0 desktop release.

Other frozen modules and runtime files remain byte-identical to the baseline;
concurrent vision/UI source work is not silently incorporated into this release.
"""
import hashlib
import importlib.util
import json
import marshal
from pathlib import Path
import shutil
import struct
import zlib
import types
from PyInstaller.archive.readers import CArchiveReader
from PyInstaller.archive.writers import CArchiveWriter
from PyInstaller.utils.win32 import versioninfo

ROOT=Path(__file__).resolve().parents[1]
BASE=ROOT/'App-0.6.0'
TARGET=ROOT/'App-0.7.0'
EXENAME='MAA自动推图助手.exe'
MODULES={name:'autostory/'+name.rsplit('.',1)[1]+'.py' for name in (
    'autostory.controller','autostory.attempts','autostory.core','autostory.jobs','autostory.recovery',
    'autostory.run_ledger','autostory.operator_details','autostory.terminal_navigation')}


def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()

class PayloadWriter(CArchiveWriter):
    def _write_entry(self,fp,entry):
        name,payload,compress,typecode=entry
        return self._write_blob(fp,payload,name,typecode,compress=compress)

def main():
    if TARGET.exists():raise RuntimeError('Release directory already exists; no files overwritten')
    expected=json.loads((ROOT/'runtime/package-logic-060-manifest.json').read_text('utf-8'))
    if not expected['ok'] or digest(BASE/EXENAME)!=expected['exe_sha256']:
        raise RuntimeError('Baseline does not match its verification manifest')
    old=CArchiveReader(str(BASE/EXENAME));old_pyz=old.open_embedded_archive('PYZ.pyz')
    raw_pyz=old.extract('PYZ.pyz')
    if raw_pyz[4:8]!=importlib.util.MAGIC_NUMBER:raise RuntimeError('Python bytecode version mismatch')
    payloads={name:marshal.dumps(compile((ROOT/file).read_text('utf-8-sig'),file,'exec')) for name,file in MODULES.items()}
    # Preserve concurrent navigation work: replace ONLY terminal() in the frozen
    # baseline class. Other Navigator methods remain byte-identical to 0.6.0.
    helper=marshal.loads(payloads['autostory.terminal_navigation'])
    replacement=next(x for x in helper.co_consts if isinstance(x,types.CodeType) and x.co_name=='terminal')
    navigation=old_pyz.extract('autostory.navigation')
    changed_methods=[]
    def patch_terminal(code):
        children=[]
        for item in code.co_consts:
            if isinstance(item,types.CodeType):
                if code.co_name=='Navigator' and item.co_name=='terminal':
                    children.append(replacement.replace(co_qualname='Navigator.terminal'));changed_methods.append('Navigator.terminal')
                else:children.append(patch_terminal(item))
            else:children.append(item)
        return code.replace(co_consts=tuple(children))
    payloads['autostory.navigation']=marshal.dumps(patch_terminal(navigation))
    assert changed_methods==['Navigator.terminal']
    new_pyz=bytearray(raw_pyz[:16]);toc=[]
    for name in sorted(set(old_pyz.toc)|set(MODULES)):
        kind=old_pyz.toc[name][0] if name in old_pyz.toc else 0
        raw=payloads[name] if name in payloads else old_pyz.extract(name,raw=True)
        compressed=zlib.compress(raw,6) if raw is not None else b''
        toc.append((name,(kind,len(new_pyz),len(compressed))));new_pyz.extend(compressed)
    struct.pack_into('!i',new_pyz,8,len(new_pyz));new_pyz.extend(marshal.dumps(toc))
    build=ROOT/'_tmp/logic-release-070';build.mkdir(parents=True,exist_ok=True)
    entries=[(name,bytes(new_pyz) if name=='PYZ.pyz' else old.extract(name),spec[3],spec[4]) for name,spec in old.toc.items()]
    entries += [(name,b'',False,'o') for name in old.options]
    raw_exe=(BASE/EXENAME).read_bytes()
    cookie=raw_exe[old._end_offset-old._COOKIE_LENGTH:old._end_offset]
    *_,pylib=struct.unpack(old._COOKIE_FORMAT,cookie)
    pkg=build/'logic.pkg';PayloadWriter(str(pkg),entries,pylib.rstrip(b'\0').decode())
    TARGET.mkdir();exe=TARGET/EXENAME
    exe.write_bytes(raw_exe[:old._start_offset])
    versioninfo.write_version_info_to_executable(str(exe),versioninfo.load_version_info_from_text_file(str(ROOT/'packaging/version-logic-070.txt')))
    with exe.open('ab') as stream:stream.write(pkg.read_bytes())
    shutil.copytree(BASE/'_internal',TARGET/'_internal')
    new=CArchiveReader(str(exe));new_archive=new.open_embedded_archive('PYZ.pyz')
    assert set(new_archive.toc)==set(old_pyz.toc)|set(MODULES)
    changed=[]
    for name in new_archive.toc:
        if name in payloads:
            assert new_archive.extract(name,raw=True)==payloads[name];changed.append(name)
        else:assert old_pyz.extract(name,raw=True)==new_archive.extract(name,raw=True),name
    assert old.options==new.options and set(old.toc)==set(new.toc)
    for name in old.toc:
        if name!='PYZ.pyz':assert old.extract(name)==new.extract(name),name
    files=[p for p in (BASE/'_internal').rglob('*') if p.is_file()]
    for path in files:assert digest(path)==digest(TARGET/path.relative_to(BASE)),path
    report={'ok':True,'baseline':str(BASE),'target':str(TARGET),'changed_frozen_modules':changed,
            'patched_frozen_methods':changed_methods,
            'unchanged_runtime_files':len(files),'source_sha256':{name:digest(ROOT/file) for name,file in MODULES.items()},
            'exe_sha256':digest(exe),'exe_bytes':exe.stat().st_size}
    (ROOT/'runtime/package-logic-070-manifest.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
