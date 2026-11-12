from pathlib import Path
import os,re
from PyInstaller.utils.hooks import collect_data_files,collect_dynamic_libs

project=Path(SPECPATH).parent
datas=collect_data_files('rapidocr_onnxruntime')
binaries=collect_dynamic_libs('onnxruntime')
a=Analysis([str(project/'desktop.py')],pathex=[str(project),str(project.parent/'Python')],
    binaries=binaries,datas=datas,
    hiddenimports=['desktop_ui','asst.asst','asst.utils'],
    excludes=['torch','torchvision','torchaudio','tensorflow','matplotlib','pandas','scipy','IPython','pytest','PyQt5','PyQt6','PySide2','PySide6'],
    noarchive=False)
pyz=PYZ(a.pure)
exe=EXE(pyz,a.scripts,[],exclude_binaries=True,name='MAA自动推图助手',
    debug=False,bootloader_ignore_signals=False,strip=False,upx=False,
    console=False,icon=str(project/'assets/app.ico'),version=str(project/'packaging/version.txt'))
app_name=os.environ.get('AUTOSTORY_APP_NAME','App')
if not re.fullmatch(r'App(?:-\d+\.\d+\.\d+)?',app_name):raise ValueError('Invalid package directory')
coll=COLLECT(exe,a.binaries,a.datas,strip=False,upx=False,name=app_name)
