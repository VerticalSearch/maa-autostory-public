"""Isolated UI preview with read-only snapshots; rejects every action POST."""
import argparse
import json
import pathlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--html',required=True)
    parser.add_argument('--port',type=int,default=18923)
    args=parser.parse_args()
    page=pathlib.Path(args.html).resolve()
    client=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with client.open('http://127.0.0.1:18741/api/dashboard',timeout=6) as response:
        fixture=json.load(response)
    output=ROOT/'runtime/web090-preview'
    output.mkdir(parents=True,exist_ok=True)
    (output/'dashboard.json').write_text(json.dumps(fixture,ensure_ascii=False),encoding='utf-8')
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args): pass
        def send(self,status,data,kind='application/json; charset=utf-8'):
            raw=data if isinstance(data,bytes) else json.dumps(data,ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header('Content-Type',kind)
            self.send_header('Cache-Control','no-store')
            self.send_header('Content-Length',str(len(raw)))
            self.end_headers();self.wfile.write(raw)
        def do_GET(self):
            if self.path=='/':
                return self.send(200,page.read_text('utf-8-sig').replace('__TOKEN__','inert-preview-token').encode(),'text/html; charset=utf-8')
            if self.path.startswith('/api/dashboard'):
                return self.send(200,fixture)
            if self.path=='/api/state':return self.send(200,fixture['state'])
            if self.path=='/api/coverage':return self.send(200,fixture['coverage'])
            return self.send(404,{'error':'Not found'})
        def do_POST(self):
            data=self.rfile.read(min(int(self.headers.get('Content-Length','0')),2048)).decode('utf-8','replace')
            with (output/'rejected-posts.ndjson').open('a',encoding='utf-8') as log:
                log.write(json.dumps({'path':self.path,'body':data},ensure_ascii=False)+'\n')
            return self.send(403,{'error':'界面预览环境，未执行任何任务。'})
    print(json.dumps({'port':args.port,'html':str(page),'post_policy':'reject-all'}),flush=True)
    ThreadingHTTPServer(('127.0.0.1',args.port),Handler).serve_forever()

if __name__=='__main__':main()
