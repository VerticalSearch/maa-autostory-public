from __future__ import annotations
import copy
import hashlib
import json
import math
import re
import time
import ssl
import http.client
import urllib.error
import urllib.request
import urllib.parse
from .common import MAA, DATA, read_json, write_json, check_stop, now, log
from .campaign import catalogue

def supports_mode(doc,mode):
    difficulty=doc.get('difficulty',0)
    if difficulty in (1,2,3):return bool(difficulty & (2 if mode=='raid' else 1))
    declared=' '.join(str(doc.get('doc',{}).get(k,'')) for k in ('title','details'))
    raid='#f#' in str(doc.get('stage_name','')) or bool(re.search(r'突袭|磨难|险地',declared))
    return raid if mode=='raid' else (not raid or '普通' in declared)

class Catalog:
    def __init__(self):
        self.maps=list(read_json(MAA/'resource/Arknights-Tile-Pos/overview.json').values())
        self.alias={}
        for m in self.maps:
            for k in ('stageId','levelId','code','name'):
                self.alias.setdefault(m.get(k,''),[]).append(m)
        self.costs={s['code']:s['apCost'] for s in read_json(MAA/'resource/stages.json') if not s['stageId'].endswith('#f#')}
        for s in catalogue()['stages'].values():
            if s['mode']=='normal' and s.get('cost') is not None:self.costs.setdefault(s['code'],s['cost'])

    def code(self,name):
        matches=self.alias.get(str(name),[]) or self.alias.get(str(name).removesuffix('_perm'),[])
        codes={m['code'] for m in matches}
        return next(iter(codes)) if len(codes)==1 else None

    def stages(self,prefix):
        known=catalogue()['profiles'].get(prefix,{}).get('codes')
        codes={s for s in known if self.code(s)==s} if known else {m['code'] for m in self.maps if re.fullmatch(re.escape(prefix)+r'-\d+',m['code'])}
        return sorted(codes,key=lambda s:[int(x) if x.isdigit() else x for x in re.split(r'(\d+)',s)])

_SEARCH_CACHE_TTL=15*60
_SEARCH_SAFE=re.compile(r'[A-Za-z0-9_#-]+')

class JobServiceUnavailable(RuntimeError):
    """A service failure must preserve the campaign, not reject all jobs/maps."""

def _retryable_request_error(error):
    if isinstance(error,urllib.error.HTTPError):return error.code in (408,429,500,502,503,504)
    cause=error.reason if isinstance(error,urllib.error.URLError) else error
    if isinstance(cause,ssl.SSLCertVerificationError):return False
    return isinstance(error,urllib.error.URLError) or isinstance(cause,(TimeoutError,ConnectionError,ssl.SSLEOFError,http.client.IncompleteRead))

def _search_segment(value,label):
    if value is None:raise ValueError(f'非法{label}')
    text=str(value)
    if not text or text in ('.','..') or not _SEARCH_SAFE.fullmatch(text):raise ValueError(f'非法{label}')
    return text

def _search_page_ok(page):
    return isinstance(page,dict) and isinstance(page.get('data'),list) and all(isinstance(item,dict) and 'id' in item for item in page['data'])

def _search_page_path(cache,code,mode,page):
    return cache/'search-cache'/f'{code}-{mode}-{page}.json'

def _read_search_page(cache,code,mode,page,force=False):
    if force:return None
    try:cached=read_json(_search_page_path(cache,code,mode,page))
    except Exception:return None
    if not isinstance(cached,dict):return None
    at=cached.get('at')
    if isinstance(at,bool) or not isinstance(at,(int,float)):return None
    stamp=time.time()
    if at!=at or at>stamp or stamp-at>_SEARCH_CACHE_TTL:return None
    if cached.get('code')!=code or cached.get('mode')!=mode or cached.get('page')!=page:return None
    payload=cached.get('payload')
    if not _search_page_ok(payload):return None
    return copy.deepcopy(payload)

def _write_search_page(cache,code,mode,page,payload):
    if not _search_page_ok(payload):raise ValueError('作业响应格式无效')
    write_json(_search_page_path(cache,code,mode,page),{'at':time.time(),'code':code,'mode':mode,'page':page,'payload':payload})

def _search_has_next(page):
    value=page.get('has_next')
    if value is None:value=page.get('hasNext',False)
    return bool(value)

class JobStore:
    def __init__(self):
        self.catalog=Catalog();self.cache=DATA/'jobs';self.cache.mkdir(exist_ok=True)
        self.last_search=None

    def get(self,path,params=None):
        return self._request(path,params)

    def query_sets(self,params):
        return self._request('/set/query',body=params)

    def _request(self,path,params=None,body=None):
        url='https://prts.maa.plus'+path
        if params:url+='?'+urllib.parse.urlencode(params)
        headers={'User-Agent':'MAA-AutoStory/0.9.21 (local personal client)'}
        if body is not None:headers['Content-Type']='application/json'
        req=urllib.request.Request(url,data=None if body is None else json.dumps(body).encode(),headers=headers)
        for attempt in range(3):
            check_stop()
            try:
                with urllib.request.urlopen(req,timeout=25) as r:
                    raw=r.read(4_000_001)
                break
            except (urllib.error.URLError,OSError,http.client.IncompleteRead) as exc:
                if isinstance(exc,InterruptedError):raise
                if isinstance(exc,urllib.error.HTTPError) and exc.code==404 and path.startswith('/copilot/get/'):
                    raise ValueError('这份作业已不存在，请尝试其他候选') from exc
                if not _retryable_request_error(exc):
                    raise JobServiceUnavailable('作业网站请求失败，未重试；已保留检查点：'+str(exc)) from exc
                if attempt==2:
                    raise JobServiceUnavailable('作业网站连续 3 次连接失败；已保留检查点，请稍后续跑：'+str(exc)) from exc
                log(f'作业网站连接中断，正在重试 {attempt+2}/3：{type(exc).__name__}')
                for _ in range(4*(attempt+1)):
                    check_stop();time.sleep(.25)
        check_stop()
        if len(raw)>4_000_000:raise JobServiceUnavailable('作业响应过大，已停止并保留检查点')
        try:
            obj=json.loads(raw)
            if not isinstance(obj,dict):raise ValueError('响应不是对象')
            code=obj.get('status_code',obj.get('statusCode'))
            if code==404 and path.startswith('/copilot/get/'):
                raise LookupError('这份作业已不存在，请尝试其他候选')
            if code!=200 or 'data' not in obj:raise ValueError('服务响应状态或数据格式不正确')
        except LookupError as exc:
            raise ValueError(str(exc)) from exc
        except (ValueError,TypeError) as exc:
            raise JobServiceUnavailable('作业网站返回无效数据，未重试；已保留检查点：'+str(exc)) from exc
        return obj['data']

    def search(self,code,limit=None,mode='normal',accept=None,force=False,exclude_ids=()):
        from .job_search import complete_search
        return complete_search(self,code,limit,mode,accept,force,exclude_ids)

    def download(self,item,code,mode='normal'):
        ident=item['id']
        if isinstance(ident,str) and ident.startswith('ext-'):
            from .job_sources import external_document
            doc=external_document(self.cache,ident)
        else:
            ident=int(ident);detail=self.get(f'/copilot/get/{ident}')
            if not isinstance(detail,dict) or not isinstance(detail.get('content'),str):
                raise JobServiceUnavailable('作业详情服务响应缺少 content 文本，已停止并保留检查点')
            doc=json.loads(detail['content'])
        if not isinstance(doc,dict):raise ValueError('作业文档必须是对象')
        if self.catalog.code(doc.get('stage_name'))!=code:raise ValueError('作业关卡不一致')
        if not supports_mode(doc,mode):raise ValueError('完整作业没有声明支持目标难度')
        if not isinstance(doc.get('actions'),list) or not doc['actions']:raise ValueError('缺少可执行动作')
        if any(not isinstance(action,dict) for action in doc['actions']):raise ValueError('作业动作条目必须是对象')
        if not isinstance(doc.get('minimum_required','0.0.0'),str):raise ValueError('作业版本要求格式错误')
        version=tuple(int(x) for x in re.findall(r'\d+',doc.get('minimum_required','0.0.0'))[:3])
        if version>(6,20,1):raise ValueError('作业要求更新的 MAA 版本')
        allowed={'Deploy','Skill','Retreat','SpeedUp','BulletTime','SkillUsage','Output','SkillDaemon','MoveCamera','ResetStopwatch','SetUnitLocation',
                 '部署','技能','撤退','二倍速','子弹时间','技能用法','打印','摆完挂机','移动镜头','重置全局计时器','设置单位坐标'}
        if any(not isinstance(a.get('type','Deploy'),str) or a.get('type','Deploy') not in allowed for a in doc['actions']):raise ValueError('此作业包含首版未支持的自由点击动作')
        raw_doc=doc
        doc=copy.deepcopy(doc)
        if doc['actions'][-1].get('type') not in ('SkillDaemon','摆完挂机'):
            doc['actions'].append({'type':'SkillDaemon'})
        for a in doc['actions']:
            value=a.get('timeout',180000)
            try:
                if isinstance(value,bool) or not isinstance(value,(int,float,str)):raise ValueError()
                numeric=float(value)
                if not math.isfinite(numeric) or numeric<0 or not numeric.is_integer():raise ValueError()
                timeout=int(value)
                if timeout<0:raise ValueError()
            except (ValueError,TypeError,OverflowError) as exc:
                raise ValueError('作业动作 timeout 必须是非负有限整数') from exc
            a['timeout']=min(timeout,180000)
        raw_path=self.cache/f'{ident}-source.json';write_json(raw_path,raw_doc)
        path=self.cache/f'{ident}.json';write_json(path,doc)
        return path,doc,hashlib.sha256(path.read_bytes()).hexdigest()

SUPPORT_MASTERY_LIMIT=7  # Non-friend support skills are capped at rank 7.


def support_allowed(config):
    """Borrowing one support unit for exactly one missing slot is on by default."""
    value=(config or {}).get('support_unit_when_missing_one',True)
    if type(value) is not bool:raise ValueError('support_unit_when_missing_one 必须为 true 或 false')
    return value


def _support_slot(group):
    """Operators MAA may borrow for one unfillable slot, or None if unsafe.

    MaaCore borrows only among the slot's own Missing/Unavailable operators
    (requirements are never ignored, so an under-trained own copy is not
    fielded). A non-friend support never exceeds skill rank 7, so a slot that
    requires mastery stays blocked.
    """
    opers=[o for o in group.get('opers',[]) if isinstance(o,dict) and o.get('name')]
    if not opers:return None
    for o in opers:
        level=o.get('requirements',{}).get('skill_level',0) if isinstance(o.get('requirements',{}),dict) else None
        if type(level) is not int or level>SUPPORT_MASTERY_LIMIT:return None
    return opers


def match_job(doc,box,allow_support=False,chars=None):
    if not isinstance(doc,dict) or any(not isinstance(doc.get(k,[]),list) for k in ('opers','groups')):
        return {'status':'blocked','reasons':['作业编队格式无效'],'assignment':[]}
    if any(not isinstance(o,dict) or not isinstance(o.get('name'),str) for o in doc.get('opers',[])) or any(not isinstance(g,dict) or not isinstance(g.get('name'),str) or not isinstance(g.get('opers'),list) for g in doc.get('groups',[])):
        return {'status':'blocked','reasons':['作业编队条目无效'],'assignment':[]}
    own=[o for o in box.get('own_opers',[]) if o.get('own')]
    names={}
    for o in own:names.setdefault(o['name'],[]).append(o)
    if chars is None:chars=read_json(MAA/'resource/battle_data.json')['chars']
    roles={'guard':'WARRIOR','vanguard':'PIONEER','defender':'TANK','supporter':'SUPPORT','specialist':'SPECIAL'}
    slots=[{'name':f'指定:{i}:{o["name"]}','opers':[o]} for i,o in enumerate(doc.get('opers',[]))]+doc.get('groups',[])
    adjacency=[];reasons=[];empty=[]
    for group in slots:
        options=[];bad=[]
        for op in group.get('opers',[]):
            for actual in names.get(op.get('name'),[]):
                hard=[];unknown=[];req=op.get('requirements',{});skill=op.get('skill',0)
                if op.get('role'):
                    role=roles.get(op['role'].lower(),op['role'].upper())
                    if chars.get(actual['id'],{}).get('profession')!=role:continue
                elite=actual.get('elite',0);level=actual.get('level',0)
                if skill==3 and elite<2:hard.append('技能三未解锁')
                if skill==2 and elite<1:hard.append('技能二未解锁')
                re= req.get('elite',-1);rl=req.get('level',-1)
                if elite<re or (elite==re and level<rl) or (re<0 and rl>level):hard.append('等级不足')
                if req.get('potential',req.get('potentiality',0))>actual.get('potential',0):hard.append('潜能不足')
                if req.get('skill_level',0)>0:
                    # These are hard game prerequisites even if skill OCR is unknown.
                    if req['skill_level']>4 and elite<1:hard.append('精零技能最高4级')
                    if req['skill_level']>7 and elite<2:hard.append('精英二未解锁专精')
                    known=actual.get('mainSkillLevel')
                    skills=actual.get('skills',[])
                    observed=actual.get('observedSkillLevels',{}).get(str(skill))
                    if type(observed) is int and 1<=observed<=10:
                        if observed<req['skill_level']:hard.append('技能等级不足')
                    elif type(known) is int and 1<=known<=7 and (known<7 or req['skill_level']<=7):
                        # Mastery is unavailable below rank seven. A known base
                        # rank can reject low training even when mastery icons
                        # were unreadable or the selected skill was omitted.
                        if known<req['skill_level']:hard.append('技能等级不足')
                    elif not skill or known is None or len(skills)<skill:unknown.append('技能/专精待实测')
                    elif known+skills[skill-1].get('level',0)<req['skill_level']:hard.append('技能等级不足')
                if req.get('module',-1)>0:
                    module_type={1:'X',2:'Y',3:'A',4:'D',5:'B'}.get(req['module'])
                    equip=next((e for e in actual.get('equips',[]) if e.get('type')==module_type),None) if module_type else None
                    if equip is None or type(equip.get('level')) is not int:
                        unknown.append('模组待实测')
                        if req.get('module_level',0)>0:unknown.append('模组等级待实测')
                    elif equip['level']<max(1,req.get('module_level',1)):hard.append('模组等级不足')
                elif req.get('module_level',0)>0:unknown.append('模组等级待实测')
                if not req or not all(k in req for k in ('elite','level')):unknown.append('作者未完整注明练度')
                if hard:bad.append(op['name']+':'+','.join(hard))
                else:options.append((actual['id'],op,unknown))
        if not options:
            reasons.append(group['name']+'：没有符合已知条件的干员'+('；'+'；'.join(bad) if bad else ''))
            empty.append(len(adjacency))
        adjacency.append(options)
    support=None
    if reasons and allow_support and len(empty)==1:
        borrow=_support_slot(slots[empty[0]])
        if borrow is not None:support=(empty[0],borrow)
    if reasons and support is None:return {'status':'blocked','reasons':reasons,'assignment':[]}
    if not slots:return {'status':'blocked','reasons':['作业未声明编队'],'assignment':[]}
    skip={support[0]} if support else set()
    assignment={};used=set();order=sorted((i for i in range(len(slots)) if i not in skip),key=lambda i:len(adjacency[i]))
    def solve(k):
        if k==len(order):return True
        i=order[k]
        for oid,op,unknown in sorted(adjacency[i],key=lambda x:len(x[2])):
            if oid in used:continue
            used.add(oid);assignment[i]={'slot':slots[i]['name'],'id':oid,'operator':op,'unknown':unknown}
            if solve(k+1):return True
            used.remove(oid);assignment.pop(i,None)
        return False
    if not solve(0):return {'status':'blocked','reasons':['替换组争用同一个干员，无法组成完整队伍'],'assignment':[]}
    if support:
        index,borrow=support
        names_text='/'.join(o['name'] for o in borrow)
        assignment[index]={'slot':slots[index]['name'],'id':None,'operator':copy.deepcopy(borrow[0]),
                           'unknown':['助战干员待借用'],'support':True,'support_opers':copy.deepcopy(borrow)}
    items=[assignment[i] for i in range(len(slots))]
    result={'status':'trial_required','reasons':list(dict.fromkeys(r for a in items for r in a['unknown'])) or ['首次执行需要演习验证'], 'assignment':items}
    if support:
        result.update(needs_support=True,support_slot=support[0])
        result['reasons']=[f'缺 1 名干员，演习时借用助战：{names_text}']+[r for r in result['reasons'] if r!='助战干员待借用']
    return result

def assigned_document(doc,match):
    if match.get('status')=='blocked' or len(match.get('assignment',[]))!=len(doc.get('opers',[]))+len(doc.get('groups',[])):
        raise ValueError('编队匹配未完成，不能生成执行作业')
    result=copy.deepcopy(doc);n=len(result.get('opers',[]));a=match['assignment']
    if sum(bool(x.get('support')) for x in a)>1:raise ValueError('每次作战最多借用一名助战干员')
    if any(x.get('support') for x in a) and not match.get('needs_support'):raise ValueError('助战编队标记不一致')
    for i in range(n):
        # A support slot keeps its original operator so MaaCore sees it missing.
        if not a[i].get('support'):result['opers'][i]=copy.deepcopy(a[i]['operator'])
    for i,g in enumerate(result.get('groups',[])):
        slot=a[n+i]
        g['opers']=copy.deepcopy(slot['support_opers']) if slot.get('support') else [copy.deepcopy(slot['operator'])]
    return result
