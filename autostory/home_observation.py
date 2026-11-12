"""Use proven home controls, not an animated assistant, as settling evidence."""
from functools import lru_cache


def blocked_overlay(texts):
    words=''.join(str(x.get('text','')).replace(' ','') for x in texts)
    if '是否花费' in words and '源石' in words and ('兑换' in words or '合成玉' in words):
        return '检测到源石兑换弹窗，请先取消弹窗再开始扫描或推图；尚未执行任务'
    if '放弃行动' in words and '继续行动' in words:
        return '检测到暂停中的战斗，请先处理当前战斗，再开始扫描或推图'
    return None


def home_regions(texts):
    if blocked_overlay(texts):return ()
    # Three independently located home labels are required. These opaque tiles
    # stay fixed while the assistant, advertising and terminal video animate.
    required=(
        ('采购中心',(700,425,930,550)),
        ('公开招募',(900,470,1120,580)),
    )
    regions=fixed_regions(texts,required)
    if not regions:return ()
    # Decorative recruitment lettering sometimes reads 千员 instead of 干员.
    # Keep exact OCR and three independent controls, using a different known
    # home tile rather than accepting a fuzzy label or only two anchors.
    for anchor in (
        ('干员寻访',(1060,480,1270,600)),
        ('编队',(680,280,900,410)),
        ('角色管理',(920,350,1100,420)),
    ):
        extra=fixed_regions(texts,(anchor,))
        if extra:return regions+extra
    return ()


def fixed_regions(texts,anchors):
    regions=[]
    for label,bounds in anchors:
        left,top,right,bottom=bounds
        matches=[t for t in texts if t.get('text','').replace(' ','')==label and t.get('score',0)>=.85
                 and left<t.get('x',0)<right and top<t.get('y',0)<bottom]
        if len(matches)!=1:return ()
        box=matches[0].get('box',())
        if not isinstance(box,(tuple,list)) or len(box)!=4:return ()
        x1,y1,x2,y2=box
        if not (left<=x1<x2<=right and top<=y1<y2<=bottom):return ()
        regions.append((int(x1)-4,int(y1)-4,int(x2-x1)+8,int(y2-y1)+8))
    return tuple(regions)


@lru_cache(maxsize=1)
def map_codes():
    from .campaign import catalogue
    return frozenset(row['code'] for row in catalogue()['stages'].values())


def navigation_texts(vision,image):
    """Fresh OCR with a bounded exact-code fallback for the verified SN map."""
    texts=vision.texts(image)
    zoom=getattr(vision,'texts_zoom',None)
    if not callable(zoom):return texts
    from .sn_observation import enrich
    from .common import check_stop
    return enrich(image,texts,zoom,check_stop,map_codes())


def navigation_regions(texts,image=None):
    if blocked_overlay(texts):return None,()
    from .sn_detail import classify
    sn=classify(texts)
    if sn.ok:return 'sn_detail',sn.rois
    regions=home_regions(texts)
    if regions:return 'home',regions
    from .dv_special import panel_regions
    regions=panel_regions(texts)
    if regions:return 'dv_special',regions
    from .cw_morse import map_regions
    regions=map_regions(image,texts)
    if regions:return 'cw_morse',regions
    for page,anchors in (
        ('stories',(('乐章收录',(1080,10,1275,95)),('曲谱',(255,620,385,715)))),
        ('terminal',(('终端',(45,630,165,710)),('前往上一次作战',(960,520,1190,570)))),
        # Animated operator portraits remain in motion after a profile read.
        # Three opaque controls identify the profile; the portrait is not a
        # navigation target and must not prevent a verified return to home.
        ('operator',(('精英化',(950,240,1065,300)),('潜能',(1160,240,1255,300)),
                     ('专精',(1150,380,1245,435)))),
    ):
        regions=fixed_regions(texts,anchors)
        if regions:return page,regions
    # Stage-label positions themselves must settle after a pan. Neither a
    # detail panel nor a settlement screen is a map, even if codes are visible.
    words=''.join(t.get('text','').replace(' ','') for t in texts)
    if any(word in words for word in ('开始行动','开始作战','开始突袭','行动结束','演习结束','行动失败','演习失败','任务失败','MISSIONFAILED','MISSIONRESULTS')):
        return None,()
    from .siracusa import overview_regions
    regions=overview_regions(texts)
    if regions:return 'is_overview',regions
    candidates={}
    for t in texts:
        code=t.get('text','').replace(' ','').upper()
        box=t.get('box',())
        if code not in map_codes() or t.get('score',0)<.9 or len(box)!=4:continue
        x1,y1,x2,y2=box
        if not (5<=x1<x2<=1274 and 90<=y1<y2<=610):continue
        candidates.setdefault(code,[]).append((int(x1)-4,int(y1)-4,int(x2-x1)+8,int(y2-y1)+8))
    regions=[v[0] for v in candidates.values() if len(v)==1]
    if len(regions)<2:
        from .sn_sparse_map import regions as sparse_regions
        sparse=sparse_regions(image,texts,map_codes())
        if sparse:return 'sn_map',sparse
    return ('map',tuple(regions[:4])) if len(regions)>=2 else (None,())
