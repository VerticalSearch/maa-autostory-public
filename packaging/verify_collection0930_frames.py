"""Measure title recovery on 13 retained frames, never use a live device."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import time

import cv2
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser()
parser.add_argument('--baseline-only',action='store_true')
args=parser.parse_args()
sys.path.insert(0,str(ROOT))
from autostory import common
common.DATA=ROOT/'_tmp/collection0930-replay'
common.DATA.mkdir(parents=True,exist_ok=True)
from autostory.device import Vision
from autostory.navigation import CHAPTERS, Navigator, norm

titles=sorted({p['title'] for p in CHAPTERS.values()})
vision=Vision(backend='cpu')
cache_path=common.DATA/'full-frame-cache.json'
cache=common.read_json(cache_path,{})
pages=[];before={};after={};target_cases=[];started=time.monotonic()
if not args.baseline_only:
    from autostory import collection_index,collection_ocr
for index in range(13):
    path=ROOT/f'runtime/screens/collection-{index}.png'
    digest=hashlib.sha256(path.read_bytes()).hexdigest()
    frame=cv2.imdecode(np.fromfile(str(path),np.uint8),cv2.IMREAD_COLOR)
    entry=cache.get(digest)
    if not entry:
        entry={'frame':str(path),'texts':vision.texts(frame)}
        cache[digest]=entry
        common.write_json(cache_path,cache)
    texts=entry['texts']
    # Reproduce 0.9.29 full-frame indexing, including its original tolerance.
    old={title for title in titles if any(norm(title)==norm(t['text']) or
         (len(norm(title))>=2 and norm(title) in norm(t['text'])) for t in texts)}
    for title in old:before.setdefault(title,index)
    new=set()
    if not args.baseline_only:
        entries=[t for t in texts if collection_ocr.in_card(t) and t.get('y',0)>90]
        entries+=collection_ocr.columns(frame,vision.texts_zoom)
        new={title for title in titles if any(collection_index.matches(t['text'],title) for t in entries)}
        for title in new:after.setdefault(title,index)
        nav=Navigator.__new__(Navigator);nav.v=vision
        for title in sorted(new-old):
            profile=next(p for p in CHAPTERS.values() if p['title']==title)
            matches=nav.chapter_candidates(frame,texts,profile)
            target_cases.append({'title':title,'page':index,'matches':matches,'ok':bool(matches)})
    pages.append({'page':index,'old':sorted(old),'new':sorted(new),'added':sorted(new-old)})
report={'at':common.now(),'live_device_used':False,'frames':len(pages),'old_count':len(before),
        'new_count':len(after),'old_titles':before,'new_titles':after,'added_titles':sorted(set(after)-set(before)),
        'lost_titles':sorted(set(before)-set(after)) if not args.baseline_only else [],
        'pages':pages,'seconds':round(time.monotonic()-started,2),
        'baseline_only':args.baseline_only,'ocr_backend':vision.backend.get('active')}
if not args.baseline_only:
    report['source_sha256']={module.__name__:hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest()
                             for module in (collection_index,collection_ocr)}
    selected=[case for case in target_cases if case['title'] in report['added_titles']]
    target_report={'ok':bool(selected) and all(case['ok'] for case in selected),
                   'live_device_used':False,'cases':selected}
    common.write_json(ROOT/'runtime/collection0930-target-check.json',target_report)
    report['target_lookup_ok']=target_report['ok']
    report['ok']=not report['lost_titles'] and target_report['ok']
destination=ROOT/('runtime/collection0930-baseline.json' if args.baseline_only else 'runtime/collection0930-frame-check.json')
common.write_json(destination,report)
print(json.dumps({k:v for k,v in report.items() if k not in ('pages','old_titles','new_titles')},ensure_ascii=False))
if not args.baseline_only:raise SystemExit(0 if report['ok'] else 1)
