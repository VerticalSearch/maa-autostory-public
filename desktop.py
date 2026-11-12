"""Frozen desktop/daemon/worker entrypoint. Never injects Windows input."""
from __future__ import annotations
import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import traceback
import urllib.request
import urllib.error
import threading

TITLE='MAA 自动推图助手'

def local_urlopen(url,timeout):
    return urllib.request.build_opener(urllib.request.ProxyHandler({})).open(url,timeout=timeout)

def health(url,root):
    try:
        with local_urlopen(url+'/api/health',timeout=2) as r:data=json.load(r)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f'控制端口被其他服务占用（HTTP {e.code}），请查看启动日志') from e
    except (OSError,ValueError):return None
    if data.get('application')!='MAA-AutoStory' or Path(data.get('root','')).resolve()!=root.resolve():
        raise RuntimeError('控制端口属于另一套程序，未启动重复服务')
    return data

def entry_command(*args):
    if getattr(sys,'frozen',False):return [sys.executable,*map(str,args)]
    return [sys.executable,'-u',str(Path(__file__).resolve()),*map(str,args)]

def ensure_service(root,url,timeout=35):
    from autostory.runtime_state import TaskLock
    current=health(url,root)
    if current:return current
    lock=TaskLock(root/'runtime'/'server-start.lock');deadline=time.monotonic()+timeout
    while not lock.acquire():
        current=health(url,root)
        if current:return current
        if time.monotonic()>deadline:raise RuntimeError('另一个启动过程尚未就绪，请稍后再试')
        time.sleep(.25)
    try:
        current=health(url,root)
        if current:return current
        with (root/'runtime'/'desktop-server.log').open('ab') as out:
            child_env={**os.environ,'PYINSTALLER_RESET_ENVIRONMENT':'1'}
            process=subprocess.Popen(entry_command('--server','--root',root),cwd=root,
                stdin=subprocess.DEVNULL,stdout=out,stderr=out,env=child_env,
                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        while time.monotonic()<deadline:
            current=health(url,root)
            if current:return current
            if process.poll() is not None:raise RuntimeError('后台服务启动失败，详见 runtime/packaged-server.log')
            time.sleep(.3)
        raise RuntimeError('后台服务启动超时，详见 runtime/packaged-server.log')
    finally:lock.release()

class WindowInstance:
    def __init__(self,root):
        from ctypes import wintypes
        self.lib=ctypes.WinDLL('kernel32',use_last_error=True)
        self.lib.CreateMutexW.argtypes=[ctypes.c_void_p,wintypes.BOOL,wintypes.LPCWSTR]
        self.lib.CreateMutexW.restype=wintypes.HANDLE
        self.lib.CloseHandle.argtypes=[wintypes.HANDLE]
        key=hashlib.sha256(str(root).casefold().encode()).hexdigest()[:20]
        ctypes.set_last_error(0)
        self.handle=self.lib.CreateMutexW(None,False,'Local\\MAAAutoStory-UI-'+key)
        if not self.handle:raise ctypes.WinError(ctypes.get_last_error())
        self.owned=ctypes.get_last_error()!=183
    def close(self):
        if self.handle:self.lib.CloseHandle(self.handle);self.handle=None
    def restore(self):
        from ctypes import wintypes
        user32=ctypes.WinDLL('user32',use_last_error=True)
        user32.FindWindowW.argtypes=[wintypes.LPCWSTR,wintypes.LPCWSTR]
        user32.FindWindowW.restype=wintypes.HWND
        user32.ShowWindow.argtypes=[wintypes.HWND,ctypes.c_int]
        user32.SetForegroundWindow.argtypes=[wintypes.HWND]
        for _ in range(30):
            hwnd=user32.FindWindowW(None,TITLE)
            if hwnd:
                user32.ShowWindow(hwnd,9);user32.SetForegroundWindow(hwnd);return
            time.sleep(.25)

def self_test(root):
    import cv2
    import numpy as np
    import onnxruntime
    from autostory.device import Vision
    from autostory.campaign import catalogue
    from autostory.common import MAA
    vision=Vision();blank=np.zeros((720,1280,3),np.uint8)
    assert vision.texts(blank)==[]
    sample=root/'runtime/screens/CW-complete-84.png'
    ocr_sample=None
    if sample.exists():
        im=cv2.imdecode(np.fromfile(sample,np.uint8),cv2.IMREAD_COLOR)
        ocr_sample=any('84/84' in x['text'].replace(' ','') for x in vision.texts(im))
        if not ocr_sample:raise RuntimeError('已知截图 OCR 回放不匹配')
    sys.path.insert(0,str(MAA/'Python'))
    from asst.asst import Asst
    dll_dir=os.add_dll_directory(str(MAA))
    maa_test=root/'runtime'/'package-check-maa';maa_test.mkdir(exist_ok=True)
    assert Asst.load(MAA,user_dir=maa_test),'MAA DLL/resource load failed'
    core=Asst();version=core.get_version()
    # Loading resources/version is read-only with respect to the emulator.
    result={'ok':True,'frozen':bool(getattr(sys,'frozen',False)),'executable':sys.executable,
        'root':str(root),'maa_version':version,'python':sys.version,'cv2':cv2.__version__,
        'onnxruntime':onnxruntime.__version__,'onnxruntime_file':onnxruntime.__file__,
        'catalogue_variants':len(catalogue()['stages']),'ocr_known_sample':ocr_sample,
        'ocr_backend':vision.backend}
    del core;dll_dir.close();return result

def web_host(root):
    base=Path(sys.executable).parent if getattr(sys,'frozen',False) else root
    candidate=base/'webview'/'AutoStory.WebHost.exe'
    return candidate if candidate.is_file() else None


def launch_web_desktop(root,url,report=None):
    host=web_host(root)
    if host is None:return False
    ensure_service(root,url)
    command=[str(host),url.rstrip('/')+'/',str(root)]
    if report is not None:command.extend(['--smoke-report',str(report)])
    # This child is the requested visible UI; the launcher retains the single-
    # instance mutex until it exits. Closing it never sends a task command.
    with (root/'runtime'/'webview-launcher.log').open('ab') as out:
        process=subprocess.Popen(command,cwd=root,stdin=subprocess.DEVNULL,stdout=out,stderr=out)
        try:code=process.wait(timeout=40 if report is not None else None)
        except subprocess.TimeoutExpired:
            process.terminate();process.wait(timeout=5)
            raise RuntimeError('界面启动检查超时；后台任务未停止')
    if code:raise RuntimeError('新界面启动失败，详见 runtime/webview-host.log')
    return True


def launch_desktop(root,url):
    if launch_web_desktop(root,url):return
    """Show the control window while the service starts, keeping Tk responsive."""
    from desktop_ui import AutoStoryDesktop
    app=AutoStoryDesktop(url,root)
    ready=threading.Event();errors=[]
    def boot():
        try:ensure_service(root,url)
        except Exception as exc:
            errors.append(str(exc));traceback.print_exc()
        finally:ready.set()
    def check_ready():
        if not ready.is_set():
            app.tk_root.after(100,check_ready);return
        if errors:app._set_message('后台启动失败：'+errors[0],'error')
        else:app._request_poll(silent=True)
    app._set_message('正在连接本机后台…','info')
    threading.Thread(target=boot,name='autostory-service-start',daemon=True).start()
    app.tk_root.after(100,check_ready)
    app.run()

def main(argv=None):
    parser=argparse.ArgumentParser(description=TITLE)
    parser.add_argument('--root',type=Path)
    group=parser.add_mutually_exclusive_group()
    group.add_argument('--server',action='store_true')
    group.add_argument('--worker',action='store_true')
    group.add_argument('--headless',action='store_true')
    group.add_argument('--self-test',type=Path,metavar='REPORT_JSON')
    group.add_argument('--smoke-ui',type=Path,metavar='REPORT_JSON')
    args,remaining=parser.parse_known_args(argv)
    if args.root:os.environ['AUTOSTORY_ROOT']=str(args.root.resolve())
    from autostory.common import ROOT,DATA,settings,write_json,now
    mode='server' if args.server else 'worker' if args.worker else 'desktop'
    for stream in ('stdout','stderr'):
        if getattr(sys,stream) is None:setattr(sys,stream,(DATA/f'packaged-{mode}.log').open('a',encoding='utf-8',buffering=1))
    if sys.stdin is None:sys.stdin=open(os.devnull,'r')
    try:
        if args.worker:
            import worker
            sys.argv=[sys.executable,*remaining];return worker.main()
        if remaining:raise ValueError('未知启动参数：'+' '.join(remaining))
        if args.server:
            import server
            server.serve();return 0
        if args.self_test:
            result=self_test(ROOT);result['at']=now();write_json(args.self_test,result);return 0
        url=f'http://127.0.0.1:{int(settings().get("port",18741))}'
        if args.headless:
            ready=ensure_service(ROOT,url);write_json(DATA/'launcher-check.json',{'ok':True,'at':now(),'health':ready});return 0
        if args.smoke_ui:
            ensure_service(ROOT,url)
            if launch_web_desktop(ROOT,url,args.smoke_ui):
                result=json.loads(args.smoke_ui.read_text('utf-8-sig'))
                if not result.get('ok'):raise RuntimeError('WebView 界面启动检查失败')
                write_json(args.smoke_ui,{**result,'at':now(),'renderer':'WebView2'});return 0
            from desktop_ui import run_ui
            result=run_ui(url,ROOT,smoke=True)
            write_json(args.smoke_ui,{**result,'at':now()});return 0
        instance=WindowInstance(ROOT)
        try:
            if not instance.owned:instance.restore();return 0
            launch_desktop(ROOT,url)
        finally:instance.close()
        return 0
    except Exception as e:
        traceback.print_exc()
        report=args.self_test or args.smoke_ui
        if report:write_json(report,{'ok':False,'at':now(),'error':str(e),'traceback':traceback.format_exc()})
        elif not (args.headless or args.server or args.worker):
            ctypes.windll.user32.MessageBoxW(None,str(e)+'\n\n日志：'+str(DATA/'packaged-desktop.log'),TITLE,0x10)
        return 1

if __name__=='__main__':
    import multiprocessing
    multiprocessing.freeze_support()
    raise SystemExit(main())
