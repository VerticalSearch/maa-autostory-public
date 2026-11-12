"""Siracusa's building map, verified by named landmarks and stage headers.

Mappings come from the retained activity_table.siracusaData.stagePointList
joined to stage_table.stages, excluding story and ambiguous IS-QT task nodes.
"""
from .common import DATA,check_stop,log,now,read_json,write_json
from .campaign import stage_info

GROUPS=frozenset(('IS','IS-TR','IS-EX','IS-S'))
POINTS={
    'IS-1':'新城区大街','IS-2':'沃尔西尼大饭店','IS-3':'沃尔西尼监狱',
    'IS-4':'沃尔西尼法庭','IS-5':'贝佩的手艺','IS-6':'米兰剧场',
    'IS-7':'米兰剧场外','IS-8':'企鹅物流据点','IS-9':'教堂',
    'IS-10':'次级核心区指挥塔','IS-TR-1':'树荫公园',
    'IS-EX-1':'咖啡店','IS-EX-2':'洗车店','IS-EX-3':'露珠花店',
    'IS-EX-4':'医院','IS-EX-5':'后巷','IS-EX-6':'日暮酒吧',
    'IS-EX-7':'小径公园','IS-EX-8':'维修店','IS-S-1':'罗塞蒂家',
    'IS-S-2':'老街道','IS-S-3':'贝洛内家','IS-S-4':'小巷','IS-S-5':'居民区',
}
LANDMARKS=frozenset(POINTS.values())|{'马蒂诺披萨','萨卢佐家','电视塔','沃尔西尼市政中心','居民点'}


def places(texts):
    """Unique, complete labels in the map, away from the fixed toolbar."""
    result={}
    for t in texts:
        name=t.get('text','').replace(' ','')
        box=t.get('box',())
        if name not in LANDMARKS or t.get('score',0)<.9 or len(box)!=4:continue
        x1,y1,x2,y2=box
        if not (15<=x1<x2<=1140 and 95<=y1<y2<=580):continue
        if not (x1<=t.get('x',-1)<=x2 and y1<=t.get('y',-1)<=y2):continue
        result.setdefault(name,[]).append(t)
    return {name:rows[0] for name,rows in result.items() if len(rows)==1}


def overview_regions(texts):
    from .navigation import detail_mode,is_settlement,header_matches
    from .home_observation import blocked_overlay
    if detail_mode(texts) or is_settlement(texts) or blocked_overlay(texts):return ()
    header=[t for t in texts if 800<t.get('x',0)<1010 and 65<t.get('y',0)<112]
    if any(header_matches(header,code) for code in POINTS):return ()
    if any(t.get('text') in ('确认','确定','取消','正在提交反馈至神经') for t in texts):return ()
    named=places(texts)
    # Generic names like 医院/教堂 alone cannot establish this particular map.
    if len(named)<2 or not any(len(name)>=4 for name in named):return ()
    regions=[]
    for t in named.values():
        x1,y1,x2,y2=t['box']
        region=(int(x1)-4,int(y1)-4,int(x2-x1)+8,int(y2-y1)+8)
        if any(abs(region[0]-r[0])<40 and abs(region[1]-r[1])<40 for r in regions):continue
        regions.append(region)
    return tuple(regions[:4]) if len(regions)>=2 else ()


def is_overview(texts):return bool(overview_regions(texts))


def is_detail(texts):
    from .navigation import detail_mode,header_matches,is_settlement
    if not detail_mode(texts) or is_settlement(texts):return False
    header=[t for t in texts if 800<t.get('x',0)<1010 and 65<t.get('y',0)<112]
    return sum(header_matches(header,code) for code in POINTS)==1


def reuse_overview(nav):
    _,texts=nav.observe()
    if is_detail(texts):
        nav.close_detail();_,texts=nav.observe()
    if not is_overview(texts):return False
    nav._last_story_base='IS'
    return True


def find(nav,code,max_swipes=18,mode='normal'):
    """Read exactly one requested operation; a building name never proves clear."""
    if code not in POINTS:raise ValueError('叙拉古人关卡尚无唯一建筑映射：'+code)
    if mode not in ('normal','raid'):raise ValueError('未支持的难度')
    nav.close_detail()
    target=POINTS[code]
    # Unlike a linear stage map this city also scrolls vertically. Sweep the
    # current row in both directions, then overlapping rows in a zigzag. Both
    # vertical directions are bounded by observed motion and a total pan cap.
    remaining=max(4,min(int(max_swipes)*4,96))
    def missing():return RuntimeError(f'叙拉古人地图未找到 {code} 对应建筑 {target}，保留待确认项')
    def look():
        check_stop();_,texts=nav.observe('is-city')
        if not is_overview(texts):raise RuntimeError('叙拉古人建筑地图未确认，已停止定位')
        named=places(texts)
        if target in named:
            nav.tap_stage(named[target],code);nav.set_mode(mode)
            return nav.detail(code,mode),None
        signature=tuple(sorted((name,round(t['x']/8),round(t['y']/8)) for name,t in named.items()))
        return None,signature
    def pan(direction,vertical=False):
        nonlocal remaining
        if remaining<=0:raise missing()
        check_stop();remaining-=1
        if vertical:
            if direction>0:nav.d.swipe(800,580,800,230)
            else:nav.d.swipe(800,230,800,580)
        elif direction<0:nav.d.swipe(350,380,1050,380)
        else:nav.d.swipe(1050,380,350,380)
    directions=(-1,1)
    for vertical in (1,-1):
        for _ in range(8):
            for direction in directions:
                previous=None;stationary=0
                while True:
                    result,signature=look()
                    if result is not None:return result
                    stationary=stationary+1 if signature==previous else 0;previous=signature
                    if stationary>=2:break
                    pan(direction)
            directions=(-direction,)
            # An edge must remain unchanged after two separate bounded pans.
            before=signature;pan(vertical,vertical=True)
            result,after=look()
            if result is not None:return result
            if before==after:
                pan(vertical,vertical=True);result,again=look()
                if result is not None:return result
                if again==after:break
    raise missing()


def scan(nav,prefix,include_raid=True,incremental=False,only_missing=False):
    from .scan_progress import _is_confirmed
    from .workflow import update
    if prefix not in GROUPS:raise ValueError('未适配的叙拉古人分区')
    db=read_json(DATA/'progress.json',{}) if incremental else {}
    results=[]
    def reusable(row,code,mode):
        return _is_confirmed(row,{'code':code,'mode':mode}) if only_missing else row.get('state')=='cleared'
    for code in nav.catalog.stages(prefix):
        check_stop()
        normal=db.get(code+':normal',{});raid=db.get(code+':raid',{})
        raid_required=include_raid and bool(stage_info(code,'raid'))
        if reusable(normal,code,'normal') and (not raid_required or reusable(raid,code,'raid')):
            results.append(normal)
            if raid_required:results.append(raid)
            continue
        update('scan',chapter=prefix,code=code,mode='normal')
        try:
            row=find(nav,code);results.append(row);log(f'关卡 {code}: {row["state"]}')
            if raid_required and row['state']=='cleared':
                _,texts=nav.observe()
                if any(t.get('text')=='突袭模式' for t in texts):
                    update('scan',code=code,mode='raid')
                    nav.set_mode('raid');raid=nav.detail(code,'raid');results.append(raid)
                    log(f'关卡 {code} 突袭: {raid["state"]}')
        except RuntimeError as error:
            log(f'{code} 本节点扫描未完成：{error}；保留缺项')
        nav.close_detail()
        if not is_overview(nav.observe()[1]):raise RuntimeError('扫描后叙拉古人建筑地图未确认')
    db=read_json(DATA/'progress.json',{})
    for code in nav.catalog.stages(prefix):
        if code+':normal' not in db:db[code+':normal']={'code':code,'mode':'normal','state':'unseen','at':now()}
    write_json(DATA/'progress.json',db)
    log(f'{prefix} 建筑地图扫描结束，读取或复用 {len(results)} 条记录；完整性仍按目录核对')
    return results
