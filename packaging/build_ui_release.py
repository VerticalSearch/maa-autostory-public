"""Build a UI-only release from 0.4.0, preserving every other frozen module.

This avoids incorporating concurrent, unverified backend source edits. Uses
the installed PyInstaller archive writer; verifies archive payload equality.
"""
import hashlib
import importlib.util
import json
import marshal
from pathlib import Path
import shutil
import struct
import zlib

from PyInstaller.archive.readers import CArchiveReader
from PyInstaller.archive.writers import CArchiveWriter
from PyInstaller.utils.win32 import versioninfo

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT/'App-0.4.0'
TARGET = ROOT/'App-0.5.0'
EXENAME = 'MAA自动推图助手.exe'
VERSION_FILE = ROOT/'packaging/version.txt'
BUILD_NAME = 'ui-release-050'
MANIFEST_NAME = 'package-ui-050-manifest.json'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class PayloadWriter(CArchiveWriter):
    def _write_entry(self, fp, entry):
        name, payload, compress, typecode = entry
        return self._write_blob(fp, payload, name, typecode, compress=compress)


def main():
    if TARGET.exists():
        raise RuntimeError(f'Release target already exists: {TARGET}')
    old = CArchiveReader(str(BASE/EXENAME))
    old_pyz = old.open_embedded_archive('PYZ.pyz')
    raw_old_pyz = old.extract('PYZ.pyz')
    if raw_old_pyz[4:8] != importlib.util.MAGIC_NUMBER:
        raise RuntimeError('Python bytecode version differs from release baseline')
    ui_source = ROOT/'desktop_ui.py'
    new_code = compile(ui_source.read_text(encoding='utf-8-sig'), 'desktop_ui.py', 'exec')
    ui_payload = marshal.dumps(new_code)
    # Preserve all original marshalled bytecode, including its filenames.
    new_pyz = bytearray(raw_old_pyz[:16])
    toc = []
    for name, (kind, _, _) in old_pyz.toc.items():
        raw = ui_payload if name == 'desktop_ui' else old_pyz.extract(name, raw=True)
        compressed = zlib.compress(raw, 6) if raw is not None else b''
        toc.append((name, (kind, len(new_pyz), len(compressed))))
        new_pyz.extend(compressed)
    struct.pack_into('!i', new_pyz, 8, len(new_pyz))
    new_pyz.extend(marshal.dumps(toc))
    build = ROOT/'_tmp'/BUILD_NAME
    build.mkdir(parents=True, exist_ok=True)
    pkg = build/'ui.pkg'
    entries = [(name, bytes(new_pyz) if name == 'PYZ.pyz' else old.extract(name),
                spec[3], spec[4]) for name, spec in old.toc.items()]
    entries += [(name, b'', False, 'o') for name in old.options]
    old_exe = (BASE/EXENAME).read_bytes()
    cookie = old_exe[old._end_offset-old._COOKIE_LENGTH:old._end_offset]
    _, _, _, _, _, pylib = struct.unpack(old._COOKIE_FORMAT, cookie)
    PayloadWriter(str(pkg), entries, pylib.rstrip(b'\0').decode())
    TARGET.mkdir()
    target_exe = TARGET/EXENAME
    target_exe.write_bytes(old_exe[:old._start_offset])
    versioninfo.write_version_info_to_executable(str(target_exe),
        versioninfo.load_version_info_from_text_file(str(VERSION_FILE)))
    with target_exe.open('ab') as fp:
        fp.write(pkg.read_bytes())
    shutil.copytree(BASE/'_internal', TARGET/'_internal')
    new = CArchiveReader(str(target_exe))
    new_archive = new.open_embedded_archive('PYZ.pyz')
    assert old.options == new.options and set(old.toc) == set(new.toc)
    assert set(old_pyz.toc) == set(new_archive.toc)
    changed = [name for name in old_pyz.toc if old_pyz.extract(name, raw=True) != new_archive.extract(name, raw=True)]
    assert changed == ['desktop_ui'], changed
    assert new_archive.extract('desktop_ui', raw=True) == ui_payload
    for name in old.toc:
        if name != 'PYZ.pyz':
            assert old.extract(name) == new.extract(name), name
    files = list((BASE/'_internal').rglob('*'))
    for path in files:
        if path.is_file():
            assert digest(path) == digest(TARGET/path.relative_to(BASE)), path
    report = {'ok': True, 'baseline': str(BASE), 'target': str(TARGET),
              'changed_frozen_modules': changed, 'frozen_module_count': len(old_pyz.toc),
              'unchanged_runtime_files': sum(path.is_file() for path in files),
              'desktop_entry_unchanged': True, 'source_sha256': digest(ui_source),
              'exe_sha256': digest(target_exe), 'exe_bytes': target_exe.stat().st_size}
    (ROOT/'runtime'/MANIFEST_NAME).write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
