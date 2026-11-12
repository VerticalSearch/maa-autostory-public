"""Replay retained real home/stage/list frames: none is Terminal arrival."""
import json
from pathlib import Path
import sys

import cv2
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from autostory import common
common.DATA=ROOT/'_tmp/nav0930-frame-replay'
common.DATA.mkdir(parents=True,exist_ok=True)
from autostory.device import Vision
from autostory.home_observation import home_regions
from autostory.terminal_navigation import terminal_visible

vision=Vision(backend='cpu')
results=[]
for name,expect_home in [('ocr0928-stuck.png',True),('ocr0928-stuck-0.png',True),
                         ('ocr0928-stuck-1.png',True),('collection-0.png',False),
                         ('17-11-detail.png',False)]:
    path=ROOT/'runtime/screens'/name
    frame=cv2.imdecode(np.fromfile(str(path),np.uint8),cv2.IMREAD_COLOR)
    texts=vision.texts(frame)
    actual_home=bool(home_regions(texts))
    terminal=terminal_visible(texts)
    results.append({'frame':str(path),'expected_home':expect_home,'home':actual_home,
                    'terminal':terminal,'ok':actual_home==expect_home and not terminal})
report={'ok':all(r['ok'] for r in results),'cases':results,'live_device_used':False}
(ROOT/'runtime/terminal0930-frame-check.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(report,ensure_ascii=False))
raise SystemExit(0 if report['ok'] else 1)
