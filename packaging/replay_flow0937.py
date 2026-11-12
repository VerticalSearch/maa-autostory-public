"""Real OCR and unchanged motion gates replayed against retained SN failures."""
import argparse,hashlib,json,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
p=argparse.ArgumentParser();p.add_argument('--frozen',action='store_true');args=p.parse_args()
if args.frozen:
    from frozen_logic_loader import install
    install('App-0.9.37',extra_modules=('worker',))
sys.path.insert(0,str(ROOT))
import cv2,numpy as np
from unittest.mock import Mock
from autostory.device import Vision
from autostory.common import DATA,arm_stop_threshold,write_json,now
from autostory.runtime_state import TaskLock
from autostory.home_observation import navigation_regions,map_codes
from autostory.sn_observation import enrich
from autostory.motion import settle,map_motion,map_signature
import autostory.sn_observation as module
lock=TaskLock(DATA/'alltasks.lock');assert lock.acquire(),'Another task is running'
# This replay never uses Device or writes progress, startup, status or STOP.
files=('progress.json','campaign-results.json','attempt-history.json','operators.json','scan-report.json','status.json','workflow.json','STOP','../settings.json')
def hashes():return {n:hashlib.sha256((DATA/n).read_bytes()).hexdigest() if (DATA/n).exists() else None for n in files}
before=hashes();report=dict(at=now(),frozen=args.frozen,loader=type(module.__spec__.loader).__name__,frames=[])
try:
    fixture=ROOT/'release/evidence/flow0937';vision=Vision();captured=[]
    for path in sorted(fixture.glob('179*.png'))[-4:]:
        image=cv2.imdecode(np.fromfile(path,np.uint8),1);full=vision.texts(image);zoom_calls=[]
        def zoom(im,roi,scale=2):
            zoom_calls.append(list(roi));return vision.texts_zoom(im,roi,scale)
        started=time.monotonic();labels=enrich(image,full,zoom,lambda:None,map_codes())
        page,rois=navigation_regions(labels,image)
        row=dict(frame=path.name,before_page=navigation_regions(full,image)[0],after_page=page,
            seconds=round(time.monotonic()-started,3),zoom_calls=zoom_calls,rois=rois,
            codes=[r['text'] for r in labels if r['text'] in map_codes()])
        assert page=='map' and len(rois)>=2 and len(zoom_calls)<=6,row
        report['frames'].append(row);captured.append(image)
    windows=[]
    for index in (0,1):
        _,result=settle(Mock(side_effect=captured[index:index+3]),lambda:None,lambda delay:None,clock=lambda:0,rois=rois,kind='map')
        assert result['confirmed'];windows.append(result)
    report['settling']=windows;checks=[]
    for axis in (0,1):
        for pixels in (1,2,5):
            for roi in rois:
                result=map_motion(map_signature(captured[-1],roi),map_signature(np.roll(captured[-1],pixels,axis=axis),roi))
                assert not result['stable'],result
                checks.append(dict(axis=axis,pixels=pixels,roi=roi,rejected=True))
    report['motion_negatives']=checks;report['ok']=True
except Exception as error:report.update(ok=False,error=repr(error))
finally:
    report['account_and_task_files_unchanged']=before==hashes();report['ok']=report.get('ok',False) and report['account_and_task_files_unchanged']
    kind='frozen' if args.frozen else 'source';write_json(DATA/f'flow0937-{kind}-replay.json',report);lock.release()
print(json.dumps(report,ensure_ascii=False));raise SystemExit(0 if report['ok'] else 1)
