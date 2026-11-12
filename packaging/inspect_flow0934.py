"""OCR retained entry frames without touching the emulator."""
import json
from pathlib import Path
import sys
import cv2
import numpy as np
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from autostory.device import Vision
vision=Vision(backend='cpu')
for path in sorted((ROOT/'release/evidence/flow0934').glob('entry-*.png')):
    frame=cv2.imdecode(np.fromfile(path,np.uint8),cv2.IMREAD_COLOR)
    texts=vision.texts(frame)
    extra=vision.texts_zoom(frame,(0,560,1280,160),scale=2)
    path.with_suffix('.json').write_text(json.dumps(dict(texts=texts,bottom=extra),ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(dict(file=path.name,texts=[(x['text'],round(x['x']),round(x['y']),round(x['score'],2)) for x in texts],
                         bottom=[(x['text'],round(x['x']),round(x['y']),round(x['score'],2)) for x in extra]),ensure_ascii=False))
