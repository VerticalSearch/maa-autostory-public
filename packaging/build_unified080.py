"""Build one audited source snapshot with DirectML and current workflow logic."""
from pathlib import Path
import hashlib,json,os,shutil,subprocess,sys

ROOT=Path(__file__).resolve().parents[1]
TARGET=ROOT/'App-0.8.0'
SNAP=ROOT/'_tmp'/'unified080-source'
PYTHON=ROOT/'.venv-gpu/Scripts/python.exe'

def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()

def main():
    if TARGET.exists():raise RuntimeError('Release already exists; choose a new version, never overwrite a running release')
    SNAP.mkdir(parents=True,exist_ok=True)
    for path in ROOT.glob('*.py'):shutil.copy2(path,SNAP/path.name)
    shutil.copytree(ROOT/'autostory',SNAP/'autostory',dirs_exist_ok=True,ignore=shutil.ignore_patterns('__pycache__'))
    (SNAP/'packaging').mkdir(exist_ok=True)
    text=(ROOT/'packaging/version-logic-070.txt').read_text('utf-8-sig').replace('(0,7,0,0)','(0,8,0,0)').replace('0.7.0','0.8.0')
    (SNAP/'packaging/version.txt').write_text(text,encoding='utf-8')
    spec=(ROOT/'packaging/AutoStory.spec').read_text('utf-8')
    icon=ROOT/'assets/shu.ico'
    if not icon.exists():icon=ROOT/'assets/app.ico'
    spec=spec.replace("str(project.parent/'Python')",repr(str(ROOT.parent/'Python')))
    spec=spec.replace("str(project/'assets/app.ico')",repr(str(icon)))
    (SNAP/'packaging/AutoStory.spec').write_text(spec,encoding='utf-8')
    source={str(p.relative_to(ROOT)):sha(p) for p in list(ROOT.glob('*.py'))+list((ROOT/'autostory').glob('*.py'))}
    with (ROOT/'runtime/package-unified080-build.log').open('w',encoding='utf-8') as out:
        subprocess.run([str(PYTHON),'-m','PyInstaller','--noconfirm','--distpath',str(ROOT),
            '--workpath',str(ROOT/'_build-unified080'),str(SNAP/'packaging/AutoStory.spec')],
            env={**os.environ,'AUTOSTORY_APP_NAME':'App-0.8.0'},stdout=out,stderr=subprocess.STDOUT,check=True)
    for name,digest in source.items():
        if sha(ROOT/name)!=digest:raise RuntimeError('Source changed during build: '+name)
    # Verify every packaged project module against this exact source snapshot.
    from PyInstaller.archive.readers import CArchiveReader
    import marshal
    archive=CArchiveReader(str(TARGET/'MAA自动推图助手.exe')).open_embedded_archive('PYZ.pyz')
    checked=[]
    for path in sorted((SNAP/'autostory').glob('*.py')):
        name='autostory' if path.stem=='__init__' else 'autostory.'+path.stem
        if name not in archive.toc:raise RuntimeError('Missing frozen module: '+name)
        frozen=archive.extract(name)
        source_code=compile(path.read_text('utf-8-sig'),frozen.co_filename,'exec',optimize=0)
        if marshal.dumps(frozen)!=marshal.dumps(source_code):
            # PyInstaller rewrites child co_filename fields; compare executable content recursively.
            import types
            def normalize(code):
                consts=tuple(normalize(x) if isinstance(x,types.CodeType) else x for x in code.co_consts)
                return code.replace(co_filename='',co_consts=consts)
            if normalize(frozen)!=normalize(source_code):raise RuntimeError('Frozen/source mismatch: '+name)
        checked.append(name)
    report={'ok':True,'version':'0.8.0','source_sha256':source,'verified_frozen_modules':checked,
            'exe_sha256':sha(TARGET/'MAA自动推图助手.exe'),'snapshot':str(SNAP),
            'bytes':sum(p.stat().st_size for p in TARGET.rglob('*') if p.is_file())}
    (ROOT/'runtime/package-unified080-manifest.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'ok':True,'modules':len(checked),'sha256':report['exe_sha256']},ensure_ascii=False))

if __name__=='__main__':main()
