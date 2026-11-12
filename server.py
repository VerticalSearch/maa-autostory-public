"""Loopback-only dashboard. No browser is opened; no desktop APIs are used."""
import json
import re
import secrets
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from urllib.parse import urlsplit,parse_qs
from autostory.common import ROOT,MAA,DATA,settings,read_json,write_json,now,worker_command
from autostory.navigation import CHAPTERS
from autostory import __version__
from autostory.runtime_state import reconcile_status,task_running
from autostory.campaign import catalogue,coverage
from autostory.dashboard import state as dashboard_state,cached_coverage

TOKEN=secrets.token_urlsafe(24)
LOCK=threading.Lock()
PROCESS=None

class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def local_request(self):
        # Validate before serving the token-bearing page as well as mutations.
        # A loopback bind alone still accepts an attacker-controlled Host.
        hosts=self.headers.get_all('Host',[])
        origins=self.headers.get_all('Origin',[])
        port=self.server.server_address[1]
        allowed={f'127.0.0.1:{port}',f'localhost:{port}'}
        if port==80:allowed.update(('127.0.0.1','localhost'))
        host=hosts[0].lower() if len(hosts)==1 else ''
        valid=host in allowed and len(origins)<=1 and self.path.startswith('/') and not self.path.startswith('//')
        if valid and origins:
            # Browser Origin is a scheme + authority, never a URL with a path.
            valid=origins[0].lower()=='http://'+host
        if not valid:
            self.close_connection=True
            self.send(403,{'error':'仅允许来自当前本机地址的请求'})
        return valid
    def send(self,status,obj,kind='application/json; charset=utf-8'):
        content=obj if isinstance(obj,bytes) else json.dumps(obj,ensure_ascii=False).encode('utf-8')
        if status in (400,413):self.close_connection=True
        self.send_response(status);self.send_header('Content-Type',kind);self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff');self.send_header('Content-Length',str(len(content)));self.end_headers();self.wfile.write(content)

    def do_GET(self):
        if not self.local_request():return
        if self.path=='/api/health':return self.send(200,{'application':'MAA-AutoStory','protocol':1,'version':__version__,
            'root':str(ROOT),'pid':__import__('os').getpid(),'frozen':bool(getattr(sys,'frozen',False)),'executable':sys.executable})
        if self.path=='/api/coverage':return self.send(200,cached_coverage()[1])
        if urlsplit(self.path).path=='/api/dashboard':
            revision,rows=cached_coverage()
            previous=parse_qs(urlsplit(self.path).query).get('revision',[''])[0]
            return self.send(200,{'state':dashboard_state(CHAPTERS,compact=True),'revision':revision,
                                 'coverage':None if previous==revision else rows})
        if self.path=='/':
            html=(ROOT/'web/index.html').read_text('utf-8').replace('__TOKEN__',TOKEN)
            return self.send(200,html.encode('utf-8'),'text/html; charset=utf-8')
        if self.path=='/api/state':
            return self.send(200,dashboard_state(CHAPTERS))
        return self.send(404,{'error':'not found'})

    def do_POST(self):
        global PROCESS
        if not self.local_request():return
        if self.headers.get('X-AutoStory-Token')!=TOKEN:return self.send(403,{'error':'请求校验失败，请刷新本地页面'})
        raw_length=self.headers.get('Content-Length','0')
        try:length=int(raw_length)
        except (TypeError,ValueError):return self.send(400,{'error':'无效请求'})
        if length<0:return self.send(400,{'error':'无效请求'})
        if length>2048:return self.send(413,{'error':'请求过大'})
        try:body=json.loads(self.rfile.read(length))
        except Exception:return self.send(400,{'error':'无效请求'})
        if not isinstance(body,dict):return self.send(400,{'error':'无效请求'})
        with LOCK:
            if self.path=='/api/stop':
                (DATA/'STOP').write_text(now(),'utf-8')
                return self.send(200,{'message':'停止信号已发送；MAA 会停止当前操作，游戏可能留在当前页面'})
            if self.path!='/api/action':return self.send(404,{'error':'not found'})
            action=body.get('action');chapter=body.get('chapter','DV')
            if action not in ('refresh','scan','scan-current','scan-all','plan','run','run-all','resume-all','diagnose'):return self.send(400,{'error':'不支持的操作'})
            if not isinstance(chapter,str) or not re.fullmatch(r'[A-Z0-9]{1,8}(?:-[A-Z0-9]{1,8}){0,3}',chapter):return self.send(400,{'error':'关卡前缀格式错误'})
            # Busy/conflict paths must not delete STOP.  The worker compares
            # STOP mtime with this accepted timestamp, so an older marker is
            # reset for the new action while a STOP after acceptance still wins.
            if PROCESS is not None and PROCESS.poll() is None:return self.send(409,{'error':'已有后台任务正在运行'})
            if task_running(DATA):return self.send(409,{'error':'已有后台任务正在运行'})
            accepted_ns=time.time_ns()
            try:out=(DATA/'worker.log').open('ab')
            except OSError:return self.send(500,{'error':'后台任务启动失败'})
            try:
                PROCESS=subprocess.Popen(worker_command(action,'--chapter',chapter,'--accepted-ns',str(accepted_ns)),cwd=ROOT,
                    stdout=out,stderr=out,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            except OSError:
                return self.send(500,{'error':'后台任务启动失败'})
            finally:out.close()
            return self.send(200,{'message':'后台任务已启动','pid':PROCESS.pid,'accepted_ns':accepted_ns})

def serve(port=None):
    port=int(port or settings().get('port',18741))
    server=ThreadingHTTPServer(('127.0.0.1',port),Handler)
    write_json(DATA/'server.json',{'pid':__import__('os').getpid(),'url':f'http://127.0.0.1:{port}','started':now()})
    server.serve_forever()

if __name__=='__main__':serve()
