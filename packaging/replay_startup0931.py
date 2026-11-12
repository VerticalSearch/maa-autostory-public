"""Read retained real startup frames; no live emulator input."""
import argparse
import json
import sys
from pathlib import Path
import cv2
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
parser=argparse.ArgumentParser();parser.add_argument('--extract-only',action='store_true');args=parser.parse_args()
from autostory import common
common.DATA=ROOT/'_tmp/startup0931-replay';common.DATA.mkdir(parents=True,exist_ok=True)
from autostory.device import Vision
from autostory.home_observation import navigation_regions
vision=Vision(backend='cpu')
cases={
    'scan0930-update-before.png':('com.android.packageinstaller','install_confirm'),
    'scan0930-update-after.png':('com.android.packageinstaller','installing'),
    'scan0930-install.png':('com.mumu.launcher','unknown'),
    'scan0930-loading.png':('com.hypergryph.arknights','loading'),
    'scan0930-loading2.png':('com.hypergryph.arknights','loading'),
    'scan0930-loading3.png':('com.hypergryph.arknights','loading'),
    'scan0930-loading4.png':('com.hypergryph.arknights','ready'),
    'startup/1793514678941587600.png':('com.hypergryph.arknights','login_start'),
    'startup0931-home-ad.png':('com.hypergryph.arknights','ready'),
    'startup0932-health-banner.png':('com.hypergryph.arknights','loading'),
    'startup0932-small-start.png':('com.hypergryph.arknights','login_start'),
    'startup0932-after-wakeup.png':('com.hypergryph.arknights','loading'),
    'startup0932-final-unknown.png':('com.hypergryph.arknights','loading'),
    'startup0933-collection.png':('com.hypergryph.arknights','ready'),
}
output=[]
for name,(package,expected) in cases.items():
    path=ROOT/'runtime/screens'/name
    frame=cv2.imdecode(np.fromfile(str(path),np.uint8),cv2.IMREAD_COLOR)
    texts=vision.texts(frame)
    row=dict(file=str(path),texts=texts,foreground_fixture=package,expected=expected)
    if not args.extract_only:
        from autostory.startup_screens import classify
        page=navigation_regions(texts)[0]
        row['classification']=classify(texts,package,page)
        row['negative']=classify(texts,'other.untrusted.app',page)
        row['ok']=(expected is None or row['classification']['state']==expected) and row['negative'].get('button') is None
    output.append(row)
report=dict(live_device_used=False,foreground_is_fixture=True,cases=output)
if not args.extract_only:report['ok']=all(row['ok'] for row in output)
destination=ROOT/('runtime/startup0931-ocr-frames.json' if args.extract_only else 'runtime/startup0931-frame-check.json')
destination.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(dict(file=str(destination),results=[dict(name=Path(row['file']).name,
     state=row.get('classification'),words=[t['text'] for t in row['texts']]) for row in output]),ensure_ascii=False))
if not args.extract_only:raise SystemExit(0 if report['ok'] else 1)
