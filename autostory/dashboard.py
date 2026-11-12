"""Cheap local dashboard reads, revisioned coverage and bounded log tail."""
import hashlib
import threading
from .common import ROOT, MAA, DATA, read_json, settings
from .campaign import catalogue, coverage
from .runtime_state import reconcile_status,persist_interrupted

_lock=threading.Lock()
_revision=None
_coverage=None

def tail_text(path, limit=18000):
    try:
        with path.open('rb') as stream:
            stream.seek(0,2)
            stream.seek(max(0,stream.tell()-limit*4))
            return stream.read(limit*4).decode('utf-8','replace').replace('\r\n','\n').replace('\r','\n')[-limit:]
    except FileNotFoundError:return ''

def coverage_revision():
    stamps=[]
    for path in (ROOT/'data/catalogue.json', DATA/'progress.json', DATA/'plan.json',
                 DATA/'scan-report.json', DATA/'campaign-results.json'):
        try:
            stat=path.stat();stamps.append((str(path),stat.st_mtime_ns,stat.st_size))
        except FileNotFoundError:stamps.append((str(path),0,0))
    return hashlib.sha256(repr(stamps).encode()).hexdigest()[:24]

def cached_coverage():
    global _revision,_coverage
    with _lock:
        revision=coverage_revision()
        if revision!=_revision or _coverage is None:
            _coverage=coverage();_revision=revision
        return _revision,_coverage

RESUMABLE=('error','stopped','interrupted','running','partial','budget_exhausted')

def resume_info(campaign,running,config=None):
    """Whether "继续上轮全目录" can do useful work, mirroring run_all(resume=True)."""
    if running:return {'available':False,'reason':'后台任务运行中'}
    state=campaign.get('state') if isinstance(campaign,dict) else None
    if not state:return {'available':False,'reason':'还没有全目录检查点，请先开始全目录推图'}
    if state not in RESUMABLE:return {'available':False,'reason':'上轮全目录已完成，请开始新一轮'}
    try:
        from .controller import resume_budget,formal_limit_reached
        from .run_ledger import limit_reached
        ledger=resume_budget(campaign.get('budget'),settings() if config is None else config)
    except Exception as error:
        return {'available':False,'reason':'上轮检查点无效，无法续跑：'+str(error)}
    if formal_limit_reached(ledger) or ledger['sanity']<=0:
        return {'available':False,'reason':'上轮预算已用完，请开始新一轮'}
    if limit_reached(ledger.get('practice_count',0),ledger.get('practice_limit')):
        return {'available':False,'reason':'上轮演习尝试额度已用完，请开始新一轮'}
    limit='不限次数' if ledger['limit'] is None else f'{ledger["count"]}/{ledger["limit"]} 次'
    return {'available':True,'reason':f'沿用上轮剩余理智 {ledger["sanity"]}（正式作战 {limit}），跳过已完成分区'}

def training_info():
    """The read-only "练谁最划算" report; a failure never breaks the dashboard."""
    try:
        from .training_report import cached
        return cached()
    except Exception as error:
        return {'state':'error','error':str(error) or type(error).__name__}

def state(chapters,compact=False):
    box=read_json(MAA/'data/OperBoxData.json',{})
    raw=read_json(DATA/'status.json',{'state':'idle'})
    status,running=reconcile_status(raw,DATA)
    if isinstance(raw,dict) and raw.get('state')=='running' and status.get('state')=='interrupted':
        # A worker killed without cleanup (crash, power loss, forced update):
        # record that once so a restarted UI no longer shows it as running.
        persist_interrupted(DATA,raw)
    campaign=read_json(DATA/'campaign-results.json',{})
    if not isinstance(campaign,dict):campaign={}
    if campaign.get('state')=='running' and not running:
        campaign={**campaign,'state':'interrupted'}
    workflow=read_json(DATA/'workflow.json',{})
    if not status.get('run_id') or workflow.get('run_id')!=status.get('run_id'):
        # Existing workers from the previous release have no phase publisher.
        workflow={'chapter':campaign.get('active_group') if running and status.get('chapter')=='ALL' else status.get('chapter'),
                  'code':'', 'phase':'starting' if running else status.get('state','idle'),
                  'message':'后台任务运行中' if running else status.get('message',''),
                  'updated':status.get('finished',status.get('started'))}
    elif not running and workflow.get('phase') not in ('finished','partial','budget_exhausted','stopped','error'):
        workflow={**workflow,'phase':status.get('state','idle'),'message':status.get('message','任务已结束')}
    progress=list(read_json(DATA/'progress.json',{}).values())
    if compact:
        progress=[{k:row[k] for k in ('code','mode','state','stars','at','cost') if k in row} for row in progress]
    return {'status':status,'running':running,'workflow':workflow,
            'scan_report':{k:v for k,v in read_json(DATA/'scan-report.json',{}).items() if k in ('schema','at','state','active_group','reused_groups','summary')},
            'diagnostics':read_json(DATA/'diagnostics.json',{}),
            'campaign':{k:campaign.get(k) for k in ('active_group','state','budget','completion')},
            'resume':resume_info(campaign,running),
            'training':training_info(),
            'operators':{'count':len(box.get('own_opers',[])),'time':box.get('syncTime'),
                         'source':box.get('source'),'missing':read_json(DATA/'box-diff.json',{}).get('not_seen',[])},
            'chapters':chapters,'profiles':catalogue()['profiles'],
            'progress':progress,
            'plan':read_json(DATA/'plan.json',{}),'results':read_json(DATA/'run-results.json',[]),
            'settings':settings(),'logs':tail_text(DATA/'activity.log')}
