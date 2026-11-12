"""Bounded background inspection of the two retained special pages; no battle input."""
import argparse,json,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from autostory.common import DATA,DeviceLock,arm_stop_threshold,write_json
from autostory.runtime_state import TaskLock
from autostory.device import Device,Vision
from autostory.navigation import Navigator
parser=argparse.ArgumentParser();parser.add_argument('action',choices=('capture','dv-condition','dv-condition-back','dv-exit','cw-enter','cw-node','cw-rest','back'));parser.add_argument('--point',type=int,default=0);args=parser.parse_args()
folder=ROOT/'release/evidence/flow0936/explore';folder.mkdir(parents=True,exist_ok=True)
task=TaskLock(DATA/'alltasks.lock');assert task.acquire(),'Another task owns emulator'
arm_stop_threshold(time.time_ns())
try:
    with DeviceLock():
        d=Device();v=Vision();nav=Navigator(d,v)
        im=d.shot(folder/(args.action+'-before.png'));t=v.texts(im)
        words={x['text'] for x in t}
        if args.action=='cw-rest':
            import cv2
            from autostory.navigation import detail_mode,classify_stars
            assert detail_mode(t)=='normal' and any('CW-S-1-B' in x['text'] for x in t)
            for i,(x,y) in enumerate(((505,429),(790,429),(1070,429),(1070,329),(640,510)),2):
                d.back();im,t=nav.observe()
                assert {'未来将至','过往未尽','紧握当下'}<={r['text'] for r in t}
                assert not detail_mode(t)
                d.tap(x,y,.4);im,t=nav.observe()
                assert detail_mode(t)=='normal'
                headers=[r['text'] for r in t if 800<r['x']<1020 and 65<r['y']<115]
                cv2.imencode('.png',im)[1].tofile(folder/('cw-node'+str(i)+'.png'))
                write_json(folder/('cw-node'+str(i)+'.json'),t)
                print(json.dumps(dict(point=i,header=headers,stars=classify_stars(im)),ensure_ascii=False),flush=True)
            d.back();nav.observe()
            raise SystemExit(0)
        if args.action in ('dv-condition','dv-exit'):
            assert 'DV-S-1' in words or 'DV-S-2' in words
            assert {'EXIT','SPECIAL ACCESS CONTENT','开始行动'}<=words
            if args.action=='dv-exit':d.tap(1208,76,.4)
            else:
                button=[x for x in t if x['text']=='查看条件' and x['x']<200 and 560<x['y']<630]
                assert len(button)==1;d.tap(button[0]['x'],button[0]['y'],.4)
        elif args.action=='dv-condition-back':
            assert {'BACK','CLASSIFIED INFORMATION','解析'}<=words
            assert '困心' in words or '衡虑' in words
            d.tap(1208,76,.4)
        elif args.action=='cw-enter':
            nav.enter('CW');im,t=nav.observe()
            assert nav.click_text('紧握当下',[x for x in t if x['y']>580],exact=True)
        elif args.action=='cw-node':
            assert {'未来将至','过往未尽','紧握当下'}<=words
            assert not any(x in words for x in ('开始行动','开始突袭','开始作战'))
            points=((220,329),(220,429),(505,429),(790,429),(1070,429),(1070,329),(640,510))
            x,y=points[args.point];d.tap(x,y,.4)
        elif args.action=='back':d.back()
        im,t=nav.observe('explore0936-'+args.action)
        import cv2,numpy as np
        cv2.imencode('.png',im)[1].tofile(folder/(args.action+str(args.point)+'.png'))
        write_json(folder/(args.action+str(args.point)+'.json'),t)
        print(json.dumps(dict(action=args.action,point=args.point,texts=[{k:x[k] for k in ('text','x','y')} for x in t]),ensure_ascii=False))
finally:task.release()
