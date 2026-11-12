from __future__ import annotations
import hashlib
import copy
import re
import time
from datetime import datetime,timezone
from .common import DATA, MAA, settings, read_json, write_json, now, log, check_stop, DeviceUnavailableError
from .jobs import JobStore, JobServiceUnavailable, match_job, assigned_document, support_allowed
from .campaign import catalogue,export_coverage,stage_info,execution_blockers,formal_without_practice
from .navigation import is_settlement,header_matches
from .navigation_errors import ChapterEntryUnavailable
from .navigation_recovery import locate
from .terminal_navigation import TerminalNavigationError
from .completion import summarize_execution,full_activity_groups,activity_progress_groups
from .activity_completion import ActivityAlreadyComplete
from . import workflow
from .resilient_ocr import OCRBackendChanged
from .run_ledger import stage_entry,reserve_practice,transition,validate_stages,optional_limit,limit_reached
from .operator_details import OperatorInspector
from .skill_training import SkillTrainer,TrainingUnavailableError,training_plan
from .recovery import (PracticeFailure,FormalFailure,RecoveryFailure,PracticeBudgetExhausted,StateIntegrityError,
                       failure_screen,recover_practice)


def formal_limit(config):
    """None means unlimited; zero remains an explicit zero-battle allowance."""
    limit=config.get('max_battles_per_run')
    if limit is not None and (type(limit) is not int or limit<0):
        raise ValueError('正式作战次数上限必须为非负整数或 null（不限次数）')
    return limit


def formal_limit_reached(ledger):
    return ledger['limit'] is not None and ledger['count']>=ledger['limit']


def resume_budget(old,config):
    """Lift an explicitly removed cap without refunding any spent resources."""
    if (not isinstance(old,dict) or 'limit' not in old
        or not all(type(old.get(k)) is int and old[k]>=0 for k in ('sanity','count'))
        or (old['limit'] is not None and
            (type(old['limit']) is not int or old['limit']<0 or old['count']>old['limit']))):
        raise RuntimeError('没有有效的全目录检查点，无法续跑')
    ledger=copy.deepcopy(old)
    if 'max_battles_per_run' in config and formal_limit(config) is None:
        ledger['limit']=None
    if 'max_practice_attempts_per_run' in config:
        optional_limit(ledger.get('practice_limit'))
        if type(ledger.get('practice_count',0)) is not int or ledger.get('practice_count',0)<0:
            raise StateIntegrityError('演习尝试检查点无效，停止续跑')
        if optional_limit(config['max_practice_attempts_per_run']) is None:
            ledger['practice_limit']=None
    if 'max_candidates_per_stage' in config:
        validate_stages(ledger)
        if optional_limit(config['max_candidates_per_stage'],1) is None:
            for item in ledger.get('stages',{}).values():item['candidate_limit']=None
    return ledger


def candidate_match(doc,box):
    config=settings()
    plan=training_plan(doc,box,config,match_job)
    if plan['status']!='blocked' or not support_allowed(config):return plan
    # Borrow only when the own roster cannot field the job even after the
    # authorized skill training, and exactly one slot remains empty.
    supported=training_plan(doc,box,config,lambda d,b:match_job(d,b,allow_support=True))
    return supported if supported['status']!='blocked' else plan


def candidate_priority(candidate):
    match=candidate.get('match',{})
    assigned=match.get('assignment',[])
    unknown=sum(len(op.get('unknown',[])) for op in assigned)
    ident=candidate['id'];identity=(0,ident) if type(ident) is int else (1,str(ident))
    # An own-roster job always precedes one that must borrow a support unit.
    return (match.get('status')=='blocked',bool(match.get('needs_support')),unknown,len(assigned) or 99,-candidate.get('like',0),identity)


def stage_priority(stage):
    eligible=[c for c in stage.get('candidates',[]) if c.get('match',{}).get('status')!='blocked']
    if stage.get('state') not in ('uncleared','partial') or not eligible:return (1,1,999,999,999,stage['code'])
    best=min(eligible,key=candidate_priority)
    score=candidate_priority(best)
    cost=stage.get('cost') if stage.get('cost') is not None else 999
    return (0,score[1],score[2],cost,score[3],stage['code'])


def search_extendable(meta):
    return (isinstance(meta,dict) and type(meta.get('pages')) is int and meta['pages']>0
            and not meta.get('exhausted') and meta.get('stop_reason') in ('eligible','limit','legacy_page_cap','page_cap'))

def box_is_stale(box,max_age=1800):
    try:
        at=datetime.fromisoformat(box['syncTime'])
        age=(datetime.now(timezone.utc)-at).total_seconds()
        return age<0 or age>max_age
    except (KeyError,TypeError,ValueError):return True


def assert_execution_unlocked():
    if (DATA/'execution-lock.json').exists():
        raise StateIntegrityError('上次出现作战模式异常，可能已消耗理智；执行已锁定，请核对 execution-lock.json 与游戏状态后处理，不能自动续跑或重置额度')


def ledger_failure(item,job,job_sha,team_sha):
    from .attempts import outcome_stamp
    latest=None;seen={}
    for attempt in item['attempts']:
        if (attempt.get('phase') in ('practice_failed','formal_failed','verified') and
            type(attempt.get('job_id')) is type(job) and attempt['job_id']==job and
            attempt.get('job_sha')==job_sha and attempt.get('team_sha')==team_sha):
            stamp=outcome_stamp(attempt.get('updated_at',attempt.get('at')))
            if stamp in seen and seen[stamp]!=attempt['phase']:
                raise StateIntegrityError('同一作业的检查点终态同秒冲突')
            seen[stamp]=attempt['phase']
            if latest is None or stamp>latest[0]:latest=(stamp,attempt)
    if latest and latest[1]['phase']!='verified':
        return {'reason':'检查点已确认该作业与当前阵容失败','outcome':latest[1]['phase']}
    return None

def build_plan(prefix):
    workflow.update('planning',chapter=prefix,code='')
    box=read_json(DATA/'operators.json') or read_json(MAA/'data/OperBoxData.json',{})
    progress=read_json(DATA/'progress.json',{})
    store=JobStore();plan=[]
    allowed=set(store.catalog.stages(prefix))
    for row in sorted(progress.values(),key=lambda r:int(r['code'].rsplit('-',1)[-1]) if r['code'].rsplit('-',1)[-1].isdigit() else 999):
        if row['code'] not in allowed:continue
        mode=row.get('mode','normal')
        record={'code':row['code'],'mode':mode,'state':row['state'],'cost':row.get('cost'),'candidates':[]}
        record['blockers']=execution_blockers(stage_info(row['code'],mode))
        if row['state'] in ('uncleared','partial') and not record['blockers']:
            check_stop();workflow.update('planning',code=row['code'],mode=mode)
            log(f'搜索 {row["code"]} 的作业')
            for item in store.search(row['code'],mode=mode,accept=lambda doc:candidate_match(doc,box)['status']!='blocked'):
                match=candidate_match(item['preview'],box)
                record['candidates'].append({'id':item['id'],'title':item['preview'].get('doc',{}).get('title',''),
                    'url':item.get('source_url',f'https://prts.plus/copilot/{item["id"]}'),
                    'source':item.get('source','PRTS Plus'),'provenance':item.get('provenance',[]),
                    'like':item.get('like',0),'match':match})
            record['candidates'].sort(key=candidate_priority)
            record['search']=getattr(store,'last_search',{})
        plan.append(record)
        if record['candidates']:
            write_json(DATA/'plan.json',{'chapter':prefix,'at':now(),'box_time':box.get('syncTime'),'stages':plan})
    plan.sort(key=stage_priority)
    write_json(DATA/'plan.json',{'chapter':prefix,'at':now(),'box_time':box.get('syncTime'),'stages':plan})
    log(f'作业计划已保存：{prefix}，{sum(bool(x["candidates"]) for x in plan)} 关有检索结果')
    return plan

class Runner:
    @property
    def phase(self):return getattr(self,'_phase','starting')

    @phase.setter
    def phase(self,value):
        self._phase=value
        fields={'chapter':getattr(self,'current_prefix',''),'code':getattr(self,'current_code',''),
                'mode':getattr(self,'current_mode','normal')}
        if value=='recovery':fields['message']='演习未通过，正在确认返回地图后更换作业'
        workflow.update('verify' if value=='recovery' else value,**fields)

    def __init__(self,core,nav,history=None):
        from .attempts import AttemptHistory
        self.core=core;self.nav=nav;self.d=nav.d;self.v=nav.v
        self.core.profile_vision=self.v
        self.nav.core=core
        self.store=JobStore()
        self.history=history if history is not None else AttemptHistory()
        self.inspector=OperatorInspector(core,self.d,self.v)
        self.trainer=SkillTrainer(core,nav,settings)
        version=core.asst.get_version()
        self.execution_context=version.lstrip('v') if isinstance(version,str) else '6.20.1'

    def history_call(self,method,*args,**kwargs):
        try:return getattr(self.history,method)(*args,**kwargs)
        except Exception as exc:
            raise StateIntegrityError('作业尝试记录不可用，停止执行：'+str(exc)) from exc

    def recover_previous_failures(self):
        budgets=[]
        for name in ('campaign-results.json','chapter-checkpoint.json'):
            report=read_json(DATA/name,{})
            if not isinstance(report,dict):raise StateIntegrityError('历史检查点格式无效，停止执行')
            budgets.append(report.get('budget',{}))
        self.history_call('recover_checkpoints',budgets)

    def result(self,code,trial,mode='normal'):
        """Verify settlement before advancing; task completion alone is insufficient."""
        evidence=DATA/'screens'/'settlements'/f'{int(time.time())}-{code}-{mode}-{"trial" if trial else "battle"}'
        self.last_result_evidence=evidence
        for index in range(35):
            check_stop();im=self.d.shot(evidence/f'{index:02d}.png')
            template='StageDrops-Stars-Adverse' if mode=='raid' else 'StageDrops-Stars-3'
            win=self.v.match(im,template,(30,240,310,160),.88)
            try:texts=self.v.texts(im)
            except OCRBackendChanged:
                write_json(evidence/f'{index:02d}.json',{'at':now(),'discarded':'OCR backend changed; recapture required'})
                continue
            words=' '.join(x['text'] for x in texts)
            write_json(evidence/f'{index:02d}.json',{'at':now(),'words':words,'match':win,'template':template})
            if failure_screen(texts):return False
            if win and header_matches(texts,code):
                return True
            if win:raise RuntimeError('结算成功标记存在，但关卡编号未确认，停止后续操作')
            # Acknowledge mission-complete transition / experience page only.
            if any(s in words for s in ('行动结束','演习结束','MISSION ACCOMPLISHED','等级提升')):
                self.d.tap(1255,180,.8)
            else:time.sleep(.8)
        raise RuntimeError('未取得三星结算证据，禁止记录通关')

    def exit_result(self):
        for _ in range(30):
            check_stop()
            im,t=self.nav.observe()
            if any(x['text'] in ('开始行动','开始作战','开始突袭') for x in t):return
            if self.nav.visible_stages(t,self.current_prefix):return
            words=' '.join(x['text'] for x in t)
            if any(s in words for s in ('任务失败','行动失败')):raise RuntimeError('战斗失败，已暂停')
            if is_settlement(t) or '等级提升' in words:self.d.tap(1255,180,1.2)
            else:time.sleep(.8)  # Loading/transition: wait without blindly clicking.
        raise RuntimeError('结算后未返回地图')

    def battle(self,code,path,trial,mode='normal',prepared=None):
        assert_execution_unlocked()
        if not trial:self.formal_dispatched=False
        row=prepared if prepared is not None else locate(self.nav,code,mode)
        if not trial and row['state']=='cleared':return 'already_cleared'
        if row['state'] not in ('uncleared','partial','cleared'):raise RuntimeError('关卡状态不确定，禁止启动战斗')
        im,t=self.nav.observe()
        if prepared is not None:
            from .navigation import detail_mode
            if not self.nav._detail_identity(im,t,code) or detail_mode(t)!=mode:
                raise RuntimeError('开战前关卡或难度发生变化，未发送作战任务')
        if trial:
            if not self.nav.click_text(['演习','开始演习'],t,exact=True):raise RuntimeError('此关没有可确认的演习入口')
            im,t=self.nav.observe()
            words=' '.join(x['text'] for x in t)
            if '不足' in words and any(s in words for s in ('演习券','演习次数')):
                raise PracticeBudgetExhausted('演习次数不足，已暂停；不会跳过演习直接正式作战')
            if '演习' not in words and not self.v.match(im,'BattleStartExercise'):
                raise RuntimeError('未确认演习模式，禁止开始战斗')
        else:
            if ('开始突袭' if mode=='raid' else '开始行动') not in ' '.join(x['text'] for x in t):raise RuntimeError('正式作战入口未确认')
        support=bool(getattr(self,'support_required',False))
        params={'filename':str(path),'formation':True,'formation_index':int(settings().get('formation_index',4)),
                'ignore_requirements':False,'support_unit_usage':1 if support else 0,'use_sanity_potion':False,'loop_times':1,'add_trust':False}
        required='BattleStartExercise' if trial else ('BattleStartRaid' if mode=='raid' else 'BattleStartNormal')
        def reject_mode(event):
            # Persist a separate interlock first: even starting a new round must
            # not erase uncertainty about an unexpected potentially paid start.
            lock={'at':now(),'code':code,'mode':mode,'expected':required,'actual_event':event,
                  'reason':'作战模式与请求不符，可能已产生正式消耗，需要核对后解锁'}
            write_json(DATA/'execution-lock.json',lock)
            record=getattr(self,'active_attempt',None)
            if record is not None:
                transition(record,'mode_mismatch',expected_start=required,unexpected_event=event)
                self.persist_checkpoint()
            raise StateIntegrityError('MAA 开战模式不符，已立即停止并锁定执行；请核对游戏与预算')
        def on_event(event):
            task=event.get('details',{}).get('task')
            if event.get('msg')==20002 and task in ('BattleStartExercise','BattleStartRaid','BattleStartNormal'):
                if task!=required:reject_mode(event)
                record=getattr(self,'active_attempt',None)
                if record is not None:
                    transition(record,'practice_started' if trial else 'formal_started',start_task=task,start_event=event)
                    self.persist_checkpoint()
        # Once dispatched, a missing callback cannot prove no paid start.
        if not trial:self.formal_dispatched=True
        from .core import CoreTaskError
        try:events=self.core.run('Copilot',params,timeout=900,event_hook=on_event)
        except CoreTaskError as exc:
            # A rejected formation before any start is provably free. Practice,
            # zero-sanity formal runs and an unborrowable support may then
            # switch to another job instead of stopping the whole run.
            if trial or support or getattr(self,'free_formal',False):
                from .formation_recovery import reject_formation
                reject_formation(self.nav,getattr(self,'current_prefix',''),code,mode,exc,support=support)
            raise
        starts=[e.get('details',{}).get('task') for e in events if e.get('msg')==20002]
        actual_starts={s for s in starts if s in ('BattleStartExercise','BattleStartRaid','BattleStartNormal')}
        if actual_starts!={required}:
            wrong=next((e for e in events if e.get('msg')==20002 and e.get('details',{}).get('task') in actual_starts-{required}),None)
            if wrong:reject_mode(wrong)
            raise RuntimeError('MAA 未提供预期作战模式的开始证据，停止后续操作')
        if trial:
            from .practice_abort import reject_aborted_practice
            reject_aborted_practice(self.nav,getattr(self,'current_prefix',''),code,mode,events)
        good=self.result(code,trial,mode)
        if not good:
            error=PracticeFailure if trial else FormalFailure
            raise error(f'{code} {"演习" if trial else "正式作战"}失败',self.last_result_evidence)
        self.exit_result()
        return 'four_star' if mode=='raid' else 'three_star'

    def inspect_assignment(self,doc,box,match,prefix):
        # Reading one group's preferred operator may select a replacement.
        # Inspect that replacement too, until assignment stabilizes. Repeated
        # assignments cannot loop and unreadable details remain unknown.
        seen=set()
        while match['status']!='blocked':
            key=tuple((s['slot'],s['id'],s['operator'].get('skill',0)) for s in match.get('assignment',[]))
            if key in seen:break
            seen.add(key);self.phase='refresh'
            box,navigated=self.inspector.inspect(box,match);self.box=box
            if navigated:
                self.phase='navigation';self.nav.enter(prefix)
            match=candidate_match(doc,box)
        return box,match

    def run(self,prefix,box=None,budget_state=None):
        assert_execution_unlocked()
        if budget_state is None:self.recover_previous_failures()
        from .attempts import roster_fingerprint,job_fingerprint,team_fingerprint
        self.current_prefix=prefix
        self.current_code='';self.current_mode='normal'
        self.last_activity_skip=None
        ledger=budget_state if budget_state is not None else {'sanity':int(settings().get('max_sanity_per_run',200)),
                'limit':formal_limit(settings()),'count':0}
        ledger.setdefault('practice_count',0)
        ledger.setdefault('practice_limit',optional_limit(settings().get('max_practice_attempts_per_run')))
        self.last_ledger=ledger
        # Always refresh through MAA before live runs, even if a cached timestamp is recent.
        self.phase='refresh'
        box=box if box is not None else self.core.refresh_box()
        self.box=box
        self.phase='navigation'
        prior_skip=getattr(self.nav,'_full_activity_skip_enabled',False)
        self.nav._full_activity_skip_enabled=True
        try:
            self.nav.enter(prefix)
        except ActivityAlreadyComplete as complete:
            if prefix not in full_activity_groups(catalogue(),complete.proof):raise
            self.last_activity_skip=complete.proof
            write_json(DATA/'run-results.json',[])
            log(str(complete))
            self.last_completion=summarize_execution(catalogue(),read_json(DATA/'progress.json',{}),
                [prefix],ledger,[{'group':prefix,'outcomes':[],'activity_completion':complete.proof}])
            return []
        finally:
            self.nav._full_activity_skip_enabled=prior_skip
        self.phase='scan'
        if getattr(self,'resuming',False):self.nav.scan(prefix,incremental=True)
        else:self.nav.scan(prefix)
        self.phase='planning'
        plan=build_plan(prefix)
        candidate_limit=optional_limit(settings().get('max_candidates_per_stage'),1)
        budget=ledger['sanity'];count=ledger['count']
        outcomes=[]
        attempted=set()
        def record(code,mode,result,job=None,reason='',evidence=None):
            row={'code':code,'mode':mode,'result':result,'at':now()}
            if job is not None:row['job']=job
            if reason:row['reason']=reason
            if evidence:row['evidence']=str(evidence)
            outcomes.append(row);write_json(DATA/'run-results.json',outcomes)
        def checkpoint():
            if callable(getattr(self,'campaign_checkpoint',None)):self.campaign_checkpoint()
            else:write_json(DATA/'chapter-checkpoint.json',{'at':now(),'chapter':prefix,'budget':ledger})
        self.persist_checkpoint=checkpoint
        while plan and not formal_limit_reached(ledger):
            check_stop()
            stage=plan.pop(0)
            code=stage['code'];mode=stage.get('mode','normal');key=code+':'+mode
            cost=stage.get('cost') if stage.get('cost') is not None else self.store.catalog.costs.get(code)
            if key in attempted:continue
            if stage['state'] not in ('uncleared','partial'):continue
            blockers=execution_blockers(stage_info(code,mode))
            if blockers:
                record(code,mode,'skipped',reason='；'.join(blockers));continue
            if type(cost) is not int or cost<0 or cost>budget:
                record(code,mode,'skipped',reason='关卡理智消耗未知或超过本轮剩余预算');continue
            eligible=sorted((x for x in stage['candidates'] if x['match']['status']!='blocked'),key=candidate_priority)
            if not eligible:
                log(f'{code} 无符合已有干员条件的作业，跳过')
                record(code,mode,'skipped',reason='没有符合当前干员条件的候选作业');continue
            self.current_code=code;self.current_mode=mode
            attempted.add(key)
            item=stage_entry(ledger,key,candidate_limit)
            stage_trials=item['practice_count'];candidate_cap=item['candidate_limit'];cleared=False
            checked_ids=set();search_meta=stage.get('search',{});refills=0
            while eligible or search_extendable(search_meta):
                check_stop()
                if limit_reached(stage_trials,candidate_cap):break
                if not eligible:
                    refills+=1
                    log(f'{code} 当前候选已排除，继续检索后续作业页')
                    extra=self.store.search(code,mode=mode,exclude_ids=checked_ids,
                                            accept=lambda doc:candidate_match(doc,box)['status']!='blocked')
                    search_meta=self.store.last_search
                    eligible=[]
                    for result in extra:
                        if result['id'] in checked_ids:continue
                        checked_ids.add(result['id'])
                        matched=candidate_match(result['preview'],box)
                        if matched['status']!='blocked':
                            eligible.append({'id':result['id'],'like':result.get('like',0),'match':matched})
                    eligible.sort(key=candidate_priority)
                    if not eligible:break
                selected=eligible.pop(0)
                job=selected['id'];self.support_required=False
                checked_ids.add(job)
                try:
                    if box_is_stale(box):
                        self.phase='refresh';box=self.core.refresh_box()
                        self.box=box
                        self.phase='navigation';self.nav.enter(prefix)
                    self.phase='planning'
                    try:path,doc,_=self.store.download(selected,code,mode=mode)
                    except (InterruptedError,DeviceUnavailableError,JobServiceUnavailable):raise
                    except (ValueError,RuntimeError,OSError) as exc:
                        record(code,mode,'candidate_rejected',job,str(exc))
                        log(f'{code} 作业 #{job} 无法使用：{exc}');continue
                    match=candidate_match(doc,box)
                    if match['status']=='blocked':
                        record(code,mode,'candidate_rejected',job,'完整作业重新匹配未通过：'+'；'.join(match.get('reasons',[])))
                        continue
                    box,match=self.inspect_assignment(doc,box,match,prefix)
                    if match['status']=='blocked':
                        record(code,mode,'candidate_rejected',job,'技能详情核查后练度不满足：'+'；'.join(match.get('reasons',[])))
                        continue
                    if match['status']=='training_required':
                        self.phase='training'
                        try:box,trained,reason=self.trainer.apply(box,match)
                        except TrainingUnavailableError as exc:
                            # This typed rejection is raised before navigation,
                            # upgrades or ledger reservations. Real upgrade errors
                            # and user stops still terminate the task.
                            self.phase='planning'
                            record(code,mode,'candidate_rejected',job,str(exc))
                            log(f'{code} 作业 #{job}：{exc}，继续其他作业')
                            continue
                        self.box=box
                        self.phase='navigation';self.nav.enter(prefix)
                        match=candidate_match(doc,box)
                        if not trained or match['status'] in ('blocked','training_required'):
                            reason=reason or '补技能后重新匹配未通过：'+'；'.join(match.get('reasons',[]))
                            record(code,mode,'candidate_rejected',job,reason)
                            log(f'{code} 作业 #{job}：{reason}，继续其他作业');continue
                    self.phase='planning'
                    assigned=assigned_document(doc,match)
                    job_sha=job_fingerprint(assigned);roster_sha=team_fingerprint(box,match,self.execution_context)
                    # Merge the latest ledger outcome first. A later history pass
                    # must never be overridden by an older ledger failure.
                    self.history_call('recover_checkpoints',[ledger])
                    prior=self.history_call('blocked',code,mode,job,job_sha,roster_sha)
                    if not prior:
                        prior=self.history_call('blocked_equivalent',code,mode,job_sha,roster_sha)
                    if not prior and self.execution_context=='6.20.1':
                        # Preserve old exact-account failures on first use. New records
                        # no longer become eligible due to an unrelated operator upgrade.
                        legacy=self.history_call('blocked',code,mode,job,job_sha,roster_fingerprint(box))
                        if legacy and self.history_call('latest',code,mode,job,job_sha,roster_sha) is None:
                            prior=self.history_call('record',code,mode,job,job_sha,roster_sha,
                                legacy['outcome'],legacy.get('reason',''),legacy.get('evidence'))
                    if prior:
                        reason='同一作业与干员条件已失败，跳过：'+prior.get('reason','')
                        log(f'{code} 作业 #{job} {reason}')
                        record(code,mode,'candidate_excluded',job,reason);continue
                    if limit_reached(ledger['practice_count'],ledger['practice_limit']):
                        raise PracticeBudgetExhausted(f'本轮已尝试演习 {ledger["practice_count"]} 次，达到上限，进度已保存')
                    # A confirmed zero-sanity stage without an exercise entry is
                    # tested by its formal run; a loss there spends nothing.
                    free=cost==0 and formal_without_practice(stage_info(code,mode))
                    self.support_required=bool(match.get('needs_support'));self.free_formal=False
                    if self.support_required:
                        log(f'{code} 作业 #{job} 自有干员缺 1 名，本次演习与正式作战由 MAA 借用助战：'+match.get('reasons',[''])[0])
                    write_json(path,assigned)
                    digest=hashlib.sha256(path.read_bytes()).hexdigest()
                    # Arrival is navigation, not an exercise attempt. Finish
                    # bounded recovery before reserving any attempt or sanity.
                    self.phase='navigation'
                    prepared=locate(self.nav,code,mode)
                    if prepared['state'] not in ('uncleared','partial','cleared'):
                        raise RuntimeError('定位后关卡状态不确定，未预留演习或理智')
                    self.active_attempt=reserve_practice(ledger,key,candidate_cap,job,job_sha,roster_sha)
                    stage_trials=item['practice_count']
                    cap_text='不限次数' if candidate_cap is None else str(candidate_cap)
                    if free:
                        transition(self.active_attempt,'practice_skipped',reason='零理智且无演习入口，以正式作战验证')
                        self.phase='formal';checkpoint()
                        log(f'{code} 无演习入口且不消耗理智，候选 #{job} 直接正式作战（本关第 {stage_trials}/{cap_text} 份）')
                        workflow.update(message=f'{code} 作业 #{job}：零理智关卡直接正式作战（第 {stage_trials} 份，{cap_text}）')
                    else:
                        self.phase='practice';checkpoint()
                        log(f'{code} 候选 #{job}，本轮此关演习 {stage_trials}/{cap_text}')
                        workflow.update(message=f'{code} 作业 #{job}：第 {stage_trials} 次演习（{cap_text}），通过后自动正式作战')
                        try:trial_result=self.battle(code,path,trial=True,mode=mode,prepared=prepared)
                        except PracticeFailure as exc:
                            transition(self.active_attempt,'practice_failed',evidence=exc.evidence,
                                       failure_kind=getattr(exc,'failure_kind','practice_loss'),
                                       game_result=getattr(exc,'game_result','failed'));checkpoint()
                            self.history_call('record',code,mode,job,job_sha,roster_sha,'practice_failed',str(exc),exc.evidence)
                            record(code,mode,'practice_failed',job,str(exc),exc.evidence)
                            self.phase='recovery'
                            if not getattr(exc,'recovered',False):recover_practice(self.nav,code,mode,prefix)
                            transition(self.active_attempt,'practice_failed',recovered=True)
                            log(f'{code} 演习失败已记录，返回地图确认成功，尝试下一份作业')
                            checkpoint();continue
                        transition(self.active_attempt,'practice_passed');checkpoint()
                        write_json(DATA/'trial.json',{'code':code,'mode':mode,'job':job,'sha256':digest,'box_time':box['syncTime'],'at':now(),'result':trial_result})
                        log(f'{code} {mode} 演习成功，开始正式作战')
                    check_stop()
                    if hashlib.sha256(path.read_bytes()).hexdigest()!=digest:raise RuntimeError('演习后作业文件变化，必须重新验证')
                    self.phase='navigation'
                    prepared=locate(self.nav,code,mode)
                    if prepared['state'] not in ('uncleared','partial','cleared'):
                        raise RuntimeError('正式作战前关卡状态不确定，未预留理智')
                    # Reserve before any paid action, including a failing one.
                    budget-=cost;count+=1
                    transition(self.active_attempt,'formal_reserved',sanity_reserved=cost)
                    ledger.update(sanity=budget,count=count);self.phase='formal';checkpoint()
                    self.formal_dispatched=None;self.free_formal=free
                    try:result=self.battle(code,path,trial=False,mode=mode,prepared=prepared)
                    except FormalFailure as exc:
                        transition(self.active_attempt,'formal_failed',evidence=exc.evidence);checkpoint()
                        self.history_call('record',code,mode,job,job_sha,roster_sha,'formal_failed',str(exc),exc.evidence)
                        if not free:raise
                        # Zero-sanity loss: acknowledge it like an exercise loss
                        # and keep searching; a paid loss still stops the run.
                        record(code,mode,'formal_failed',job,str(exc),exc.evidence)
                        self.phase='recovery';recover_practice(self.nav,code,mode,prefix)
                        transition(self.active_attempt,'formal_failed',recovered=True,sanity_spent=0);checkpoint()
                        log(f'{code} 零理智正式作战失败已记录（未消耗理智），返回地图后尝试下一份作业');continue
                    except PracticeFailure as exc:
                        # Only a proven pre-start formation rejection: a free run,
                        # or a support unit that MAA could not borrow this time.
                        if not (free or self.support_required) or getattr(exc,'game_result',None)!='not_started':raise
                        budget+=cost;count-=1;ledger.update(sanity=budget,count=count)
                        transition(self.active_attempt,'formal_failed',evidence=exc.evidence,recovered=True,
                                   failure_kind=getattr(exc,'failure_kind','formation_rejected'),
                                   game_result='not_started',sanity_released=cost);checkpoint()
                        self.history_call('record',code,mode,job,job_sha,roster_sha,'formal_failed',str(exc),exc.evidence)
                        record(code,mode,'formal_failed',job,str(exc),exc.evidence)
                        log(f'{code} {exc}');continue
                    except Exception as exc:
                        if self.formal_dispatched is False:
                            # Exact local control-flow evidence: battle() failed
                            # during navigation before sending Copilot to MAA.
                            budget+=cost;count-=1
                            ledger.update(sanity=budget,count=count)
                            transition(self.active_attempt,'formal_not_started',sanity_released=cost,
                                       reason=str(exc),dispatch_proven_absent=True)
                            checkpoint()
                            log(f'{code} 正式任务尚未交给 MAA，释放未使用的 {cost} 理智与 1 次正式预留')
                        raise
                    finally:self.free_formal=False
                    if result=='already_cleared':
                        # battle() returned before starting; release unused reservation.
                        budget+=cost;count-=1
                        ledger.update(sanity=budget,count=count);checkpoint()
                    self.phase='verification';row=locate(self.nav,code,mode)
                    if row['state']!='cleared':raise RuntimeError('结算后关卡星级未确认，未记录通关')
                    transition(self.active_attempt,'verified',result=result,evidence=row.get('evidence'));checkpoint()
                    self.history_call('record',code,mode,job,job_sha,roster_sha,'passed',evidence=row.get('evidence'))
                    record(code,mode,result,job)
                    log(f'{code} 已通过结算与地图双重核验')
                    cleared=True;break
                except Exception as exc:
                    record(code,mode,'stopped',job,str(exc),getattr(exc,'evidence',None));raise
            if not cleared:
                reason='达到本关演习候选上限，先处理其他关卡' if limit_reached(stage_trials,candidate_cap) else '搜索范围内候选已检查完，未取得可正式执行的作业'
                record(code,mode,'skipped',reason=reason);log(f'{code} {reason}')
            if cleared and not formal_limit_reached(ledger) and budget>0:
                self.phase='scan'
                self.nav.scan(prefix,incremental=True)
                self.phase='planning'
                plan=build_plan(prefix)
        export_coverage()
        log(f'本轮结束：正式作战 {count} 次，剩余本轮预算 {budget} 理智')
        self.last_completion=summarize_execution(catalogue(),read_json(DATA/'progress.json',{}),
            [prefix],ledger,[{'group':prefix,'outcomes':outcomes}])
        return outcomes

    def run_all(self,resume=False):
        """One global budget across every map group, with durable blocked reasons."""
        assert_execution_unlocked()
        self.recover_previous_failures()
        self.resuming=resume
        ledger={'sanity':int(settings().get('max_sanity_per_run',200)),
                'limit':formal_limit(settings()),'count':0,
                'practice_count':0,'practice_limit':optional_limit(settings().get('max_practice_attempts_per_run'))}
        previous=read_json(DATA/'campaign-results.json',{})
        report=[];completed=set();activity_completed=set()
        if previous:write_json(DATA/'backups'/f'campaign-{time.time_ns()}.json',previous)
        if resume:
            old=previous.get('budget',{})
            ledger=resume_budget(old,settings());report=list(previous.get('groups',[]))
            latest={r['group']:r for r in report}
        ledger.setdefault('practice_count',0)
        ledger.setdefault('practice_limit',optional_limit(settings().get('max_practice_attempts_per_run')))
        optional_limit(ledger['practice_limit'])
        if type(ledger['practice_count']) is not int or ledger['practice_count']<0:
            raise RuntimeError('演习尝试检查点无效，停止续跑')
        validate_stages(ledger)
        book=catalogue();progress=read_json(DATA/'progress.json',{})
        pending={book['stages'].get(k,{}).get('group') for k,r in progress.items() if r['state'] in ('uncleared','partial')}
        groups=sorted(book['profiles'],key=lambda p:(p not in pending,not p.isdigit(),int(p) if p.isdigit() else p))
        current=None
        navigation_failures=[];last_navigation_reason=None
        def checkpoint(state='running',completion=None):
            write_json(DATA/'campaign-results.json',{'at':now(),'budget':ledger,'groups':report,'active_group':current,'state':state,
                **({'completion':completion} if completion else {})})
        def finish():
            self.last_completion=summarize_execution(book,read_json(DATA/'progress.json',{}),groups,ledger,report)
            checkpoint(self.last_completion['state'],self.last_completion)
            export_coverage()
            return report
        self.last_ledger=ledger
        self.campaign_checkpoint=checkpoint
        if resume:
            completed={k for k,r in latest.items() if 'outcomes' in r and not r.get('blocked') and not r.get('activity_completion')
                and summarize_execution(book,progress,[k],ledger,[r])['state']=='done'}
            log(f'续跑检查点：跳过已确认通关的 {len(completed)} 个分区，保留剩余预算 {ledger["sanity"]}')
        if (formal_limit_reached(ledger) or ledger['sanity']<=0 or
                limit_reached(ledger['practice_count'],ledger['practice_limit'])):
            return finish()
        box=self.core.refresh_box()
        nav=getattr(self,'nav',None)
        if getattr(type(nav),'supports_activity_progress',False):
            self.phase='activity_progress'
            workflow.update('activity_progress',chapter='ALL',code='',message='正在从乐章收录筛选未满进度活动')
            observed=nav.collect_activity_progress(book['profiles'])
            for group,profile in book['profiles'].items():
                if profile.get('kind')!='side':continue
                base=profile.get('base',group)
                proof=observed.get(base) if isinstance(observed,dict) else None
                if group in full_activity_groups(book,proof):
                    report.append({'group':group,'outcomes':[],'at':now(),
                        'activity_completion':proof,'skip_reason':'activity_counter_full'})
                    activity_completed.add(group)
                elif group in activity_progress_groups(book,proof):
                    # Fresh incomplete activity evidence outranks an old all-clear
                    # partition checkpoint when choosing what to inspect now.
                    completed.discard(group)
                    report.append({'group':group,'at':now(),'collection_progress':proof})
                else:
                    report.append({'group':group,'phase':'activity_progress','at':now(),
                        'blocked':'活动完成进度未确认，保留待确认且不进入逐关扫描',
                        'error_kind':'activity_progress_unconfirmed','base_group':base})
                    activity_completed.add(group)
            checkpoint()
        for prefix in groups:
            if prefix in activity_completed:continue
            if prefix in completed and prefix not in pending:continue
            check_stop()
            if formal_limit_reached(ledger) or ledger['sanity']<=0:break
            current=prefix;checkpoint()
            log(f'全量推图：{prefix}，剩余预算 {ledger["sanity"]}')
            try:
                if box_is_stale(box):
                    self.phase='refresh';box=self.core.refresh_box()
                outcomes=self.run(prefix,box=box,budget_state=ledger)
                box=getattr(self,'box',box)
                proof=getattr(self,'last_activity_skip',None)
                family=full_activity_groups(book,proof)
                if prefix in family:
                    # An activity counter covers its entire family. Keep this
                    # separate from per-stage wins, budget and failure history.
                    for group in family:
                        if group in groups:
                            report.append({'group':group,'outcomes':[],'at':now(),
                                           'activity_completion':proof,'skip_reason':'activity_counter_full'})
                            activity_completed.add(group)
                else:
                    report.append({'group':prefix,'outcomes':outcomes,'at':now()})
                navigation_failures=[];last_navigation_reason=None
            except InterruptedError:
                checkpoint('stopped');raise
            except PracticeBudgetExhausted:
                # A spent allowance ends this round without claiming clearance.
                # The existing ledger and pending map records remain authoritative.
                return finish()
            except Exception as e:
                if self.phase=='navigation' and isinstance(e,ChapterEntryUnavailable):
                    # A missing chapter entry is chapter-local and expected while
                    # the finite catalogue is walked. Keep the chapter pending,
                    # persist why it was skipped, and continue with other groups.
                    # It neither proves nor clears a systemic navigation failure,
                    # so leave ``navigation_failures`` untouched.
                    report.append({'group':prefix,'phase':'navigation','blocked':str(e),'at':now(),
                                   'error_kind':'chapter_entry_unavailable',
                                   'base_group':e.base,'cached':e.cached})
                    log(f'{prefix} 章节入口暂不可用：{e}')
                    checkpoint()
                    continue
                report.append({'group':prefix,'phase':self.phase,'blocked':str(e),'at':now()})
                log(f'{prefix} 阻塞：{e}')
                if isinstance(e,(DeviceUnavailableError,JobServiceUnavailable,RecoveryFailure,PracticeBudgetExhausted,StateIntegrityError,TerminalNavigationError)) or self.phase in ('refresh','training','practice','recovery','formal','verification'):
                    checkpoint('error')
                    export_coverage()
                    raise
                if self.phase in ('navigation','scan'):
                    reason=str(e).split('：',1)[0]
                    if reason!=last_navigation_reason:navigation_failures=[]
                    last_navigation_reason=reason;navigation_failures.append(prefix)
                    if len(navigation_failures)>=3:
                        checkpoint('error');export_coverage()
                        raise RuntimeError('连续 3 个分区遇到相同导航或扫描错误，已停止以避免反复空转：'+
                                           '、'.join(navigation_failures)+'；'+reason) from e
                else:navigation_failures=[];last_navigation_reason=None
            checkpoint()
        return finish()
