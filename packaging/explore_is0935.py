"""Observe a named IS overview building, without starting a battle."""
import argparse,json,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from autostory.common import DATA,DeviceLock,arm_stop_threshold,write_json
from autostory.runtime_state import TaskLock
from autostory.device import Device,Vision
from autostory.navigation import Navigator
p=argparse.ArgumentParser();p.add_argument('--area');p.add_argument('--current',action='store_true');p.add_argument('--back',action='store_true');p.add_argument('--menu',action='store_true');p.add_argument('--pan-up',action='store_true');a=p.parse_args()
lock=TaskLock(DATA/'alltasks.lock');assert lock.acquire(),'Another task owns device'
arm_stop_threshold(time.time_ns())
folder=ROOT/'release/evidence/flow0935';folder.mkdir(parents=True,exist_ok=True)
try:
 with DeviceLock():
    nav=Navigator(Device(),Vision())
    if not a.current:
        nav.home()
        try:nav.enter('IS')
        except RuntimeError as e:
            if '关卡地图入口未适配' not in str(e):raise
    if a.back:nav.d.back()
    image,texts=nav.observe()
    if a.menu or a.pan_up:
        from autostory.siracusa import is_overview
        assert is_overview(texts),'Not verified IS overview'
        if a.menu:nav.d.tap(1216,660,.3)
        else:nav.d.swipe(800,580,800,200)
        image,texts=nav.observe()
    if a.area:
        names={'洗车店','沃尔西尼大饭店','咖啡店','新城区大街','马蒂诺披萨','树荫公园'}
        found={t['text'] for t in texts if t.get('score',0)>=.9 and t['text'] in names}
        assert len(found)>=3,('Not verified IS overview',found)
        match=[t for t in texts if t['text']==a.area and t.get('score',0)>=.9]
        assert len(match)==1,match
        nav.d.tap(match[0]['x'],match[0]['y'],.5)
        image,texts=nav.observe()
    import cv2
    name='is-'+(a.area or ('menu' if a.menu else 'pan-up' if a.pan_up else 'back' if a.back else 'current'))
    cv2.imencode('.png',image)[1].tofile(str(folder/(name+'.png')))
    write_json(folder/(name+'.json'),dict(texts=texts))
    print(json.dumps(dict(image=str(folder/(name+'.png')),texts=[(t['text'],round(t['x']),round(t['y']),round(t['score'],2)) for t in texts]),ensure_ascii=False))
finally:lock.release()
