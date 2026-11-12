"""Additional JSON-only sources; no imported scripts or document instructions run."""
import hashlib,io,json,re,time,urllib.parse,urllib.request,zipfile
from pathlib import Path
from .common import ROOT,MAA,check_stop,read_json,write_json


def canonical(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'))
def sha(value):return hashlib.sha256(canonical(value).encode()).hexdigest()


def public_get(url):
    parsed=urllib.parse.urlsplit(url)
    if parsed.scheme!='https' or not parsed.netloc or parsed.username or parsed.password:
        raise ValueError('作业源必须使用不含凭据的HTTPS地址')
    check_stop()
    req=urllib.request.Request(url,headers={'User-Agent':'MAA-AutoStory/0.9.21'})
    with urllib.request.urlopen(req,timeout=25) as response:data=response.read(4_000_001)
    check_stop()
    if len(data)>4_000_000:raise ValueError('作业源响应过大，未标记检索完成')
    return json.loads(data)


def cached_public(url,cache,ttl=3600):
    path=cache/'source-cache'/(sha(url)+'.json')
    try:old=read_json(path,{})
    except (ValueError,OSError):old={}
    at=old.get('at')
    if type(at) in (int,float) and 0<=time.time()-at<ttl and old.get('url')==url:return old['data']
    value=public_get(url);write_json(path,{'at':time.time(),'url':url,'data':value});return value


def documents(value):
    """Accept raw standard jobs and server export envelopes, never executable code."""
    if isinstance(value,list):
        for row in value:yield from documents(row)
    elif isinstance(value,dict):
        if isinstance(value.get('stage_name'),str) and isinstance(value.get('actions'),list):yield value
        elif isinstance(value.get('content'),str):
            try:yield from documents(json.loads(value['content']))
            except ValueError:return
        elif isinstance(value.get('data'),(dict,list)):yield from documents(value['data'])


def archive_documents(source,cache,force=False):
    """Read verified ZIP members in memory; never extract or execute files."""
    url=source['url'];expected=source['sha256'];parts=urllib.parse.urlsplit(url)
    if (parts.scheme!='https' or not parts.netloc or parts.username or parts.password
            or not re.fullmatch(r'[a-f0-9]{64}',expected)):raise ValueError('归档地址或摘要无效')
    path=cache/'archives'/(expected+'.zip')
    payload=path.read_bytes() if path.exists() and not force else None
    if payload is not None and hashlib.sha256(payload).hexdigest()!=expected:payload=None
    if payload is None:
        check_stop()
        request=urllib.request.Request(url,headers={'User-Agent':'MAA-AutoStory/0.9.21'})
        with urllib.request.urlopen(request,timeout=30) as response:payload=response.read(32_000_001)
        check_stop()
        if len(payload)>32_000_000 or hashlib.sha256(payload).hexdigest()!=expected:raise ValueError('归档过大或内容摘要不符，检索未完成')
        path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(payload)
    result=[]
    with zipfile.ZipFile(io.BytesIO(payload)) as bundle:
        if sum(info.file_size for info in bundle.infolist())>128_000_000:raise ValueError('归档解压数据过大，检索未完成')
        for info in bundle.infolist():
            check_stop()
            if info.filename.lower().endswith('.json'):
                value=json.loads(bundle.read(info).decode('utf-8-sig'))
                result.extend(documents(value))
    return result


def additional_jobs(cache,force=False,code=None,catalog=None):
    config=read_json(ROOT/'job-sources.json',{})
    if not isinstance(config,dict):raise ValueError('job-sources.json必须为对象')
    result=[];reports=[]
    def add(value,origin):
        count=0
        for doc in documents(value):
            if code and catalog is not None and catalog.code(doc.get('stage_name'))!=code:continue
            ident='ext-'+sha(doc)
            entry={'document':doc,'source':origin}
            write_json(cache/'external'/(ident+'.json'),entry)
            result.append({'id':ident,'preview':doc,'content':canonical(doc),'available':True,
                           'source':origin['name'],'source_url':origin.get('url',''),'provenance':[origin]})
            count+=1
        return count
    local=[('MAA内置作业',MAA/'resource/copilot'),('本地导入',ROOT/'jobs-import')]
    for folder in config.get('local_dirs',[]):
        path=Path(folder);local.append(('本地目录',path if path.is_absolute() else ROOT/path))
    for name,folder in local:
        report={'name':name,'path':str(folder),'complete':True,'files':0,'jobs':0,'errors':[]}
        for path in sorted(folder.rglob('*.json')) if folder.exists() else []:
            check_stop();report['files']+=1
            try:report['jobs']+=add(read_json(path),{'name':name,'path':str(path)})
            except (ValueError,OSError) as exc:report['errors'].append({'path':str(path),'error':str(exc)})
        report['complete']=not report['errors'];reports.append(report)
    for source in config.get('github_repositories',[]):
        if source.get('enabled',True) is not True:continue
        report={'name':source.get('name',source.get('repository','GitHub')),'complete':False,'files':0,'jobs':0,'errors':[]}
        try:
            repo=source['repository'];ref=source.get('ref','HEAD');prefix=source.get('path','').strip('/')
            if not re.fullmatch(r'[\w.-]+/[\w.-]+',repo) or not isinstance(ref,str) or not ref:raise ValueError('GitHub作业库参数无效')
            if source.get('manifest'):
                manifest=ROOT/source['manifest']
                if not manifest.resolve().is_relative_to((ROOT/'data').resolve()):raise ValueError('归档索引必须位于data目录')
                tree=read_json(manifest,{})
                if tree.get('repository')!=repo or tree.get('sha')!=ref:raise ValueError('归档索引来源与固定版本不一致')
                report['revision_mode']='pinned_snapshot'
            else:
                tree_url=f'https://api.github.com/repos/{repo}/git/trees/{urllib.parse.quote(ref,safe="")}?recursive=1'
                tree=cached_public(tree_url,cache,0 if force else 3600)
            if not isinstance(tree,dict) or not isinstance(tree.get('tree'),list) or tree.get('truncated') is not False:
                raise ValueError('GitHub文件目录不完整，不能宣称搜完')
            commit=tree['sha']
            if not isinstance(commit,str) or not re.fullmatch(r'[a-f0-9]{40}',commit):raise ValueError('GitHub版本编号无效')
            report.update(repository=repo,revision=commit,indexed_files=len(tree['tree']))
            for item in tree['tree']:
                check_stop();path=item.get('path','')
                if item.get('type')!='blob' or not path.lower().endswith('.json'):continue
                if prefix and not path.startswith(prefix+'/'):continue
                if code and item.get('code_hint') and item['code_hint']!=code:continue
                url=f'https://raw.githubusercontent.com/{repo}/{commit}/'+urllib.parse.quote(path,safe='/')
                report['files']+=1
                try:
                    value=cached_public(url,cache,365*86400)
                    if item.get('sha'):report.setdefault('manifest_kind','git-tree')
                    report['jobs']+=add(value,{'name':report['name'],'url':url,'repository':repo,'revision':commit,'path':path})
                except InterruptedError:raise
                except Exception as exc:report['errors'].append({'url':url,'error':str(exc)})
            report['complete']=not report['errors']
        except InterruptedError:raise
        except Exception as exc:report['errors'].append({'error':str(exc)})
        reports.append(report)
    for url in config.get('json_urls',[]):
        report={'name':'外部JSON','url':url,'complete':False,'jobs':0,'errors':[]}
        try:
            report['jobs']=add(cached_public(url,cache,0 if force else 900),{'name':'外部JSON','url':url})
            report['complete']=True
        except InterruptedError:raise
        except Exception as exc:report['errors'].append({'error':str(exc)})
        reports.append(report)
    for source in config.get('json_archives',[]):
        if source.get('enabled',True) is not True:continue
        report={'name':source.get('name','JSON归档'),'url':source.get('url'),'complete':False,'jobs':0,'errors':[],
                'revision_mode':'pinned_snapshot','sha256':source.get('sha256')}
        try:
            docs=archive_documents(source,cache,force);report['total_documents']=len(docs)
            report['jobs']=add(docs,{'name':report['name'],'url':source['url'],'sha256':source['sha256']})
            report['complete']=True
        except InterruptedError:raise
        except Exception as exc:report['errors'].append({'error':str(exc)})
        reports.append(report)
    return result,reports


def external_document(cache,ident):
    if not isinstance(ident,str) or not re.fullmatch(r'ext-[a-f0-9]{64}',ident):raise ValueError('外部作业编号无效')
    item=read_json(cache/'external'/(ident+'.json'),{})
    doc=item.get('document')
    if not isinstance(doc,dict) or ident!='ext-'+sha(doc):raise ValueError('外部作业内容缺失或已变化，必须重新搜索')
    return doc
