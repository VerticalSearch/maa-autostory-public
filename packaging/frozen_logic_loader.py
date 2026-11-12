"""Load the exact shipped AutoStory bytecode for focused regression/recovery checks."""
import importlib.abc
import importlib.util
from pathlib import Path
import sys
from PyInstaller.archive.readers import CArchiveReader

ROOT=Path(__file__).resolve().parents[1]

def install(app_name='App-0.6.0',extra_modules=()):
    exe=ROOT/app_name/'MAA自动推图助手.exe'
    archive=CArchiveReader(str(exe)).open_embedded_archive('PYZ.pyz')
    class FrozenLoader(importlib.abc.MetaPathFinder,importlib.abc.Loader):
        def find_spec(self,fullname,path=None,target=None):
            if fullname!='autostory' and not fullname.startswith('autostory.') and fullname not in extra_modules:return None
            if fullname not in archive.toc:return None
            return importlib.util.spec_from_loader(fullname,self,is_package=archive.toc[fullname][0]==1)
        def create_module(self,spec):return None
        def exec_module(self,module):
            relative=module.__name__.replace('.','/')
            module.__file__=str(ROOT/(relative+'/__init__.py' if module.__spec__.submodule_search_locations is not None else relative+'.py'))
            exec(archive.extract(module.__name__),module.__dict__)
    if any(n=='autostory' or n.startswith('autostory.') or n in extra_modules for n in sys.modules):
        raise RuntimeError('AutoStory modules already imported; use a fresh verification process')
    sys.meta_path.insert(0,FrozenLoader())
    return exe
