"""Read-only local API benchmark against an ephemeral server, never invokes ADB."""
import json
import statistics
import threading
import time
import sys
from pathlib import Path
from http.server import ThreadingHTTPServer
from urllib.request import ProxyHandler,build_opener

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import server

def main():
    httpd=ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
    thread=threading.Thread(target=httpd.serve_forever,daemon=True);thread.start()
    opener=build_opener(ProxyHandler({}));base=f'http://127.0.0.1:{httpd.server_port}'
    def get(path):
        started=time.perf_counter()
        with opener.open(base+path,timeout=5) as response:raw=response.read()
        return raw,round((time.perf_counter()-started)*1000,3)
    try:
        first,elapsed=get('/api/dashboard');revision=json.loads(first)['revision']
        report={'first':{'bytes':len(first),'ms':elapsed}}
        for label,path in (('legacy_state','/api/state'),('legacy_coverage','/api/coverage'),('unchanged','/api/dashboard?revision='+revision)):
            samples=[]
            for _ in range(10):
                raw,elapsed=get(path);samples.append(elapsed)
            report[label]={'bytes':len(raw),'median_ms':round(statistics.median(samples),3)}
        report['unchanged_reduction_percent']=round(100*(1-report['unchanged']['bytes']/(report['legacy_state']['bytes']+report['legacy_coverage']['bytes'])),1)
        (ROOT/'runtime/ui-flow-performance.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
        print(json.dumps(report))
    finally:httpd.shutdown();thread.join();httpd.server_close()

if __name__=='__main__':main()
