"""Complete per-stage queries with explicit source coverage and duplicate detection."""
import copy,json,time,re
from .common import DATA,ROOT,read_json,write_json,check_stop,now
from .campaign import stage_info
from .job_sources import canonical,sha,additional_jobs
from . import workflow


def query_specs(catalog,code,mode):
    maps=[row for row in getattr(catalog,'maps',[]) if row.get('code')==code]
    info=stage_info(code,mode)
    internal=sorted({row['stageId'].split('#')[0].removesuffix('_perm') for row in maps if row.get('stageId')})
    names=sorted({row['name'] for row in maps if row.get('name') and row['name']!=code})
    singles=[('text',{'document':code})]
    singles += [('stage',{'levelKeyword':value}) for value in internal]
    singles += [('text',{'document':value}) for value in names+internal]
    set_terms=[code]+[info[k] for k in ('zone',) if info.get(k)]
    chapter=info.get('chapter','')
    if info.get('kind')=='main' and str(chapter).isdigit():
        n=int(chapter);set_terms.append(f'第{n}章')
        chinese={0:'零',1:'一',2:'二',3:'三',4:'四',5:'五',6:'六',7:'七',8:'八',9:'九',10:'十',11:'十一',12:'十二',13:'十三',14:'十四',15:'十五',16:'十六'}
        if n in chinese:set_terms.append('第'+chinese[n]+'章')
    seen=set();specs=[]
    for kind,params in singles+[('set',{'keyword':term}) for term in set_terms]:
        key=canonical([kind,params])
        if key not in seen:seen.add(key);specs.append((kind,params))
    return specs


def _cached_page(store,kind,params,force,meta):
    key={'kind':kind,'params':params};path=store.cache/'search-v2'/(sha(key)+'.json')
    try:old=read_json(path,{})
    except (ValueError,OSError):old={}
    stamp=old.get('at')
    if (not force and old.get('query')==key and type(stamp) in (int,float)
            and 0<=time.time()-stamp<900 and isinstance(old.get('payload'),dict)):
        meta['cache_hits']+=1;return copy.deepcopy(old['payload'])
    payload=store.query_sets(params) if kind=='set' else store.get('/copilot/query',params)
    write_json(path,{'at':time.time(),'query':key,'payload':payload});return payload


def complete_search(store,code,limit=None,mode='normal',accept=None,force=False,exclude_ids=()):
    from .jobs import _search_segment,supports_mode,JobServiceUnavailable
    from .attempts import job_fingerprint
    code=_search_segment(code,'作业代码');mode=_search_segment(mode,'难度')
    if mode not in ('normal','raid'):raise ValueError('作业难度无效')
    if accept is not None and not callable(accept):raise TypeError('accept必须为可调用对象')
    if limit is not None and (type(limit) is not int or limit<0):raise ValueError('检索条数必须为非负整数或null')
    meta={'schema':2,'code':code,'mode':mode,'limit':limit,'pages':0,'cache_hits':0,'eligible':0,
          'capped':False,'exhausted':False,'complete':False,'stop_reason':'','queries':[],'sources':[],
          'errors':[],'duplicates':0,'malformed':0,'unavailable':[],'at':now()}
    store.last_search=meta;results=[];by_content={};seen_ids=set();excluded=set(exclude_ids);set_ids=set()
    def persist():
        meta.update(at=now(),candidates=len(results));store.last_search=copy.deepcopy(meta)
        write_json(store.cache/f'{code}-{mode}-coverage.json',meta)
    def add(item,origin):
        if not isinstance(item,dict) or not isinstance(item.get('id'),(int,str)) or type(item.get('id')) is bool:
            meta['malformed']+=1;return
        if not item.get('available',True):return
        ident=item['id']
        try:doc=item.get('preview') or json.loads(item['content'])
        except (ValueError,KeyError,TypeError):meta['malformed']+=1;return
        if not isinstance(doc,dict):
            meta['malformed']+=1;return
        if store.catalog.code(doc.get('stage_name'))!=code or not supports_mode(doc,mode):return
        # PRTS query payloads are previews: actions are deliberately omitted.
        # Equal rosters do not prove equal battle scripts. Deduplicate previews
        # by their source ID; full external documents can use executable hashes.
        digest=job_fingerprint(doc) if isinstance(doc.get('actions'),list) and doc['actions'] else 'prts:'+str(ident)
        if digest in by_content:
            prior=by_content[digest]
            for source in item.get('provenance',[])+[origin]:
                if source not in prior['provenance']:prior['provenance'].append(source)
            meta['duplicates']+=1;return
        if ident in seen_ids or ident in excluded:return
        seen_ids.add(ident)
        candidate=copy.deepcopy(item);candidate.update(preview=doc,source=item.get('source','PRTS Plus'),
            source_url=item.get('source_url',f'https://prts.plus/copilot/{ident}'),
            provenance=item.get('provenance',[])+[origin])
        # Malformed requirements are rejected as a candidate, never allowed to
        # stop searching the remaining public jobs.
        try:eligible=accept is None or accept(doc)
        except (ValueError,TypeError,KeyError,AttributeError,IndexError):meta['malformed']+=1;return
        results.append(candidate);by_content[digest]=candidate
        if eligible:meta['eligible']+=1
    def pages(kind,query):
        q={'kind':kind,'query':query,'pages':0,'rows':0,'complete':False}
        meta['queries'].append(q);page=1;signatures=set()
        while True:
            check_stop();workflow.update('planning',code=code,mode=mode,message=f'{code}：正在完整检索 {kind}，第{page}页')
            params={'page':page,'limit':20,'desc':True if kind=='set' else 'true',
                    ('order_by' if kind=='set' else 'orderBy'):'id',**query}
            if kind=='set':params['only_following']=False
            payload=_cached_page(store,kind,params,force,meta)
            if not isinstance(payload,dict) or not isinstance(payload.get('data'),list):raise JobServiceUnavailable('作业列表格式无效，检索未完成')
            rows=payload['data'];nxt=payload.get('has_next',payload.get('hasNext'))
            if type(nxt) is not bool:raise JobServiceUnavailable('作业列表缺少明确末页标志，检索未完成')
            if any(not isinstance(row,dict) or type(row.get('id')) is not int for row in rows):raise JobServiceUnavailable('作业列表编号无效，检索未完成')
            signature=tuple(row['id'] for row in rows)
            if (nxt and not rows) or (rows and signature in signatures):raise JobServiceUnavailable('作业接口重复页面或空页仍声称有后续，检索未完成')
            if rows:signatures.add(signature)
            meta['pages']+=1;q['pages']+=1;q['rows']+=len(rows);q['total']=payload.get('total')
            if kind=='stage':
                known=[]
                for row in rows:
                    try:known.append(store.catalog.code(json.loads(row.get('content','{}')).get('stage_name')))
                    except (ValueError,TypeError,AttributeError):pass
                if any(value!=code for value in known):
                    q['error']='关卡筛选未被服务端准确采用，保留关键词补搜结果';meta['errors'].append(q['error']);persist();return
            yield rows,q
            if not nxt:q['complete']=True;persist();return
            page+=1;persist();time.sleep(.15)
    try:
        if limit==0:meta.update(capped=True,stop_reason='limit');return []
        specs=query_specs(store.catalog,code,mode)
        for kind,query in specs:
            for rows,q in pages(kind,query):
                origin={'name':'PRTS Plus','kind':kind,'query':query}
                if kind=='set':
                    for row in rows:
                        members=row.get('copilot_ids',row.get('copilotIds'))
                        if not isinstance(members,list) or any(type(i) is not int or i<=0 for i in members):
                            raise JobServiceUnavailable('作业集成员格式无效，检索未完成')
                        set_ids.update(members)
                else:
                    for row in rows:add(row,origin)
                if limit is not None and len(results)>=limit:
                    meta.update(capped=True,stop_reason='limit');return results[:limit]
        extra_ids=set()
        source_config=read_json(ROOT/'job-sources.json',{})
        for ident in source_config.get('prts_sets',[]):
            check_stop()
            if type(ident) is not int or ident<=0:raise ValueError('配置的作业集编号无效')
            detail=store.get('/set/get',{'id':ident});members=detail.get('copilot_ids',detail.get('copilotIds'))
            if not isinstance(members,list) or any(type(i) is not int or i<=0 for i in members):raise JobServiceUnavailable('指定作业集成员格式无效')
            extra_ids.update(members)
        # Request all public set members in natural API-sized batches, including
        # ones absent from title searches. Each batch is paged to its real end.
        members=sorted((set_ids|extra_ids)-seen_ids-excluded)
        for start in range(0,len(members),20):
            batch=members[start:start+20];found=set()
            for rows,q in pages('members',{'copilotIds':','.join(map(str,batch))}):
                if any(row['id'] not in batch for row in rows):raise JobServiceUnavailable('作业集成员查询未被接口采用，检索未完成')
                found.update(row['id'] for row in rows)
                for row in rows:add(row,{'name':'PRTS Plus 作业集','kind':'members'})
            meta['unavailable'].extend(sorted(set(batch)-found))
        extra,reports=additional_jobs(store.cache,force,code,store.catalog)
        meta['sources']=[{'name':'PRTS Plus','complete':all(q['complete'] for q in meta['queries'])},*reports]
        for item in extra:
            check_stop();add(item,{'name':item['source'],'url':item.get('source_url','')})
        results.sort(key=lambda item:(-float(item.get('rating_ratio',0) or 0),-int(item.get('like',0) or 0),str(item['id'])))
        complete=all(s['complete'] for s in meta['sources'])
        meta.update(complete=complete,exhausted=complete,stop_reason='exhausted' if complete else 'source_incomplete',set_members=len(set_ids|extra_ids))
        write_json(store.cache/f'{code}-{mode}-search.json',results)
        return results
    except Exception as exc:
        meta.update(stop_reason='stopped' if isinstance(exc,InterruptedError) else 'error',error=str(exc));raise
    finally:persist()
