"""Exercise the built EXE using an isolated, inert account fixture (no ADB)."""
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import time
import urllib.request
import uuid
import argparse

ROOT=Path(__file__).resolve().parents[1]
EXE=ROOT/'App/MAA自动推图助手.exe'
FLAGS=getattr(subprocess,'CREATE_NO_WINDOW',0)

def main():
    global EXE
    parser=argparse.ArgumentParser()
    parser.add_argument('--app-name',default='App')
    args=parser.parse_args()
    if not re.fullmatch(r'App(?:-\d+\.\d+\.\d+)?',args.app_name):raise ValueError('Invalid app directory')
    EXE=ROOT/args.app_name/'MAA自动推图助手.exe'
    sandbox=ROOT/'_tmp'/('package-test-'+uuid.uuid4().hex)
    maa=sandbox/'maa';project=maa/'AutoStory'
    (maa/'resource/Arknights-Tile-Pos').mkdir(parents=True)
    (maa/'resource/Arknights-Tile-Pos/overview.json').write_text('{}')
    (maa/'resource/stages.json').write_text('[]')
    (project/'web').mkdir(parents=True)
    (project/'web/index.html').write_text("<script>const token='__TOKEN__';</script>",encoding='utf-8')
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    (project/'settings.json').write_text(json.dumps({'port':port}))
    url=f'http://127.0.0.1:{port}'
    children=[];server_pid=None;results={}
    # A deliberately empty PATH proves the launcher does not invoke python.exe.
    env={**os.environ,'PATH':str(Path(os.environ['SystemRoot'])/'System32')}
    try:
        for _ in range(2):
            children.append(subprocess.Popen([str(EXE),'--headless','--root',str(project)],env=env,
                stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=FLAGS))
        results['parallel_launch_exit_codes']=[p.wait(timeout=55) for p in children]
        assert results['parallel_launch_exit_codes']==[0,0]
        def get(path):
            with urllib.request.urlopen(url+path,timeout=5) as r:return json.load(r)
        h=get('/api/health');server_pid=h['pid'];results['health']=h
        assert h['frozen'] and Path(h['root'])==project
        html=urllib.request.urlopen(url,timeout=5).read().decode()
        token=re.search("const token='([^']+)'",html).group(1)
        req=urllib.request.Request(url+'/api/action',data=b'{"action":"plan","chapter":"DV"}',headers={'X-AutoStory-Token':token,'Content-Type':'application/json'})
        results['accepted_worker']=json.load(urllib.request.urlopen(req,timeout=5))
        deadline=time.monotonic()+35
        while time.monotonic()<deadline:
            state=get('/api/state')
            if state['status'].get('state') in ('done','error','stopped'):break
            time.sleep(.3)
        assert state['status']['state']=='done',state
        results['worker_status']=state['status']
        assert (project/'runtime/plan.json').exists()
        result=subprocess.run([str(EXE),'--smoke-ui',str(sandbox/'ui.json'),'--root',str(project)],env=env,
            timeout=35,creationflags=FLAGS)
        assert result.returncode==0,(sandbox/'ui.json').read_text('utf-8')
        results['hidden_ui']=json.loads((sandbox/'ui.json').read_text('utf-8'))
        results['ok']=True
    except Exception as e:
        results.update(ok=False,error=repr(e));raise
    finally:
        if server_pid:
            # Verify the executable of the PID returned by this test's unique
            # loopback endpoint before stopping this test-owned server.
            import ctypes
            from ctypes import wintypes
            kernel=ctypes.WinDLL('kernel32',use_last_error=True)
            kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD];kernel.OpenProcess.restype=wintypes.HANDLE
            kernel.QueryFullProcessImageNameW.argtypes=[wintypes.HANDLE,wintypes.DWORD,wintypes.LPWSTR,ctypes.POINTER(wintypes.DWORD)]
            kernel.TerminateProcess.argtypes=[wintypes.HANDLE,wintypes.UINT];kernel.CloseHandle.argtypes=[wintypes.HANDLE]
            handle=kernel.OpenProcess(0x1001,False,server_pid)
            if handle:
                size=wintypes.DWORD(32768);buf=ctypes.create_unicode_buffer(size.value)
                if kernel.QueryFullProcessImageNameW(handle,0,buf,ctypes.byref(size)) and Path(buf.value)==EXE:
                    # Health PID belongs to the unique port/root verified above.
                    kernel.TerminateProcess(handle,0)
                kernel.CloseHandle(handle)
        for child in children:
            if child.poll() is None:child.terminate();child.wait(timeout=5)
        results['sandbox']=str(sandbox)
        (ROOT/'runtime/package-integration.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(results,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
