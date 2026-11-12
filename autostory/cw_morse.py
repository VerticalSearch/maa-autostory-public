"""CW-S Morse-map navigation, gated by its actual labels and stage detail IDs."""
from functools import lru_cache
import cv2
import numpy as np
from .common import ROOT,DATA,check_stop,log,read_json
from .campaign import stage_info

# Each position was opened independently and checked against the detail header.
# The apparent visual order is not the numeric order of the stages.
POINTS={
    'CW-S-1-A':(220,329),'CW-S-1-B':(220,429),
    'CW-S-2-A':(1070,329),'CW-S-2-B':(1070,429),
    'CW-S-3-A':(505,429),'CW-S-3-B':(790,429),'CW-S-4':(640,510),
}
LABELS=((180,314,108,30),(180,414,108,30),(1030,314,108,30),
        (1030,414,108,30),(466,414,108,30),(748,414,108,30),(548,518,184,14))
FOOTER=(('未来将至',(920,645,1030,700)),('过往未尽',(1030,645,1140,700)),
        ('紧握当下',(1140,645,1250,700)))

@lru_cache(maxsize=1)
def _template():
    path=ROOT/'assets/zone-cw-morse.png'
    return cv2.imdecode(np.fromfile(path,np.uint8),cv2.IMREAD_GRAYSCALE) if path.exists() else None

def map_regions(image,texts):
    from .navigation import detail_mode,is_settlement
    from .home_observation import blocked_overlay,fixed_regions
    if image is None or image.shape!=(720,1280,3) or image.dtype!=np.uint8:return ()
    if detail_mode(texts) or is_settlement(texts) or blocked_overlay(texts):return ()
    words=''.join(str(t.get('text','')).replace(' ','') for t in texts)
    if any(s in words for s in ('确认','确定','取消','退出','放弃行动','继续行动')):return ()
    footer=fixed_regions(texts,FOOTER)
    if not footer:return ()
    template=_template()
    if template is None or template.shape!=(720,1280):return ()
    gray=cv2.cvtColor(image,cv2.COLOR_BGR2GRAY)
    # Compare the seven independent Morse labels, excluding the progress icons.
    # A tiny search tolerance handles rasterisation without permitting map pans.
    for x,y,w,h in LABELS:
        target=(template[y:y+h,x:x+w]>130).astype(np.uint8)*255
        crop=(gray[y-2:y+h+2,x-2:x+w+2]>130).astype(np.uint8)*255
        score=float(cv2.minMaxLoc(cv2.matchTemplate(crop,target,cv2.TM_CCOEFF_NORMED))[1])
        if not np.isfinite(score) or score<.94:return ()
    active=cv2.cvtColor(image[650:690,1145:1245],cv2.COLOR_BGR2HSV)
    if cv2.countNonZero(cv2.inRange(active,np.array([3,130,130]),np.array([25,255,255])))<100:return ()
    return LABELS+footer

def is_map(image,texts):return bool(map_regions(image,texts))

def find(nav,code,mode='normal'):
    if code not in POINTS or mode not in ('normal','raid') or not stage_info(code,mode):
        raise ValueError('未适配的孤星特殊关卡或难度')
    nav.close_detail()
    image,texts=nav.observe()
    if not is_map(image,texts):raise RuntimeError('CW-S 摩斯码地图未确认，未点击关卡')
    check_stop();x,y=POINTS[code]
    nav.tap_stage(dict(x=x,y=y),code)
    nav.set_mode(mode)
    return nav.detail(code,mode)

def scan(nav,include_raid=True,incremental=False,only_missing=False):
    from .scan_progress import _is_confirmed
    from .workflow import update
    db=read_json(DATA/'progress.json',{}) if incremental else {}
    results=[]
    def reusable(row,code,mode):
        return _is_confirmed(row,{'code':code,'mode':mode}) if only_missing else row.get('state')=='cleared'
    # Reading a proven detail does not need combat-map resources. Missing battle
    # maps for 2-B/3-B remain governed by the existing combat dispatch checks.
    for code in POINTS:
        check_stop()
        normal=db.get(code+':normal',{});raid=db.get(code+':raid',{})
        raid_required=include_raid and bool(stage_info(code,'raid'))
        if reusable(normal,code,'normal') and (not raid_required or reusable(raid,code,'raid')):
            results.append(normal)
            if raid_required:results.append(raid)
            continue
        update('scan',chapter='CW-S',code=code,mode='normal')
        try:
            row=find(nav,code);results.append(row);log(f'关卡 {code}: {row["state"]}')
            if raid_required and row['state']=='cleared':
                update('scan',code=code,mode='raid')
                nav.set_mode('raid');raid=nav.detail(code,'raid');results.append(raid)
                log(f'关卡 {code} 突袭: {raid["state"]}')
        except RuntimeError as error:log(f'{code} 本节点扫描未完成：{error}；保留缺项')
        nav.close_detail()
        image,texts=nav.observe()
        if not is_map(image,texts):raise RuntimeError('扫描后 CW-S 摩斯码地图未确认')
    log(f'CW-S 摩斯码地图扫描结束，读取或复用 {len(results)} 条记录')
    return results
