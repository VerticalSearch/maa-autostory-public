"""Exclude a rejected exercise formation only after proving no battle began."""
import time
from .common import DATA,now,write_json,check_stop
from .home_observation import fixed_regions,blocked_overlay
from .recovery import PracticeFailure,RecoveryFailure


def formation_rejection(error,support=False):
    from .core import CoreTaskError
    if not isinstance(error,CoreTaskError) or error.task!='Copilot':return None
    rows=error.events;tid=error.task_id
    if type(tid) is not int or tid<=0 or not rows:return None
    if any(e.get('msg') in (0,1) or e.get('details',{}).get('task') in
           ('BattleStartExercise','BattleStartNormal','BattleStartRaid') for e in rows):return None
    # Capture timing is a benign connection-info callback interleaved with the
    # task. Other connection events (including disconnects) remain disqualifying.
    rows=[e for e in rows if not (e.get('msg')==2 and e.get('what')=='ScreencapCost'
          and e.get('taskid') is None and e.get('taskchain') is None)]
    if not rows:return None
    # MaaCore emits an all-tasks-completed envelope even when this task failed.
    # It is not a successful Copilot completion and never substitutes for one.
    if (rows[-1].get('msg')==3 and rows[-1].get('finished_tasks')==[tid]
            and rows[-1].get('taskchain')=='Copilot' and rows[-1].get('taskid')==tid):rows=rows[:-1]
    # Formation's internal skill-page scroll uses taskid=0 in MaaCore6.20.1.
    # Only these prebattle ProcessTask callbacks are excluded from ID checking.
    # A support borrow leaves and re-enters quick formation the same way; any
    # battle start was already rejected above, so no start can be hidden here.
    def internal(e):
        task=e.get('details',{}).get('task','')
        return (e.get('taskid')==0 and e.get('taskchain')=='Copilot' and e.get('msg') in (20001,20002)
                and e.get('subtask')=='ProcessTask' and isinstance(task,str)
                and (task.startswith('BattleQuickFormation') or (support and not task.startswith('BattleStart'))))
    rows=[e for e in rows if not internal(e)]
    if not rows:return None
    if any(e.get('taskchain')!='Copilot' or e.get('taskid')!=tid for e in rows):return None
    if rows[0].get('msg')!=10001 or rows[-1].get('msg')!=10000:return None
    if sum(e.get('msg')==10001 for e in rows)!=1 or any(e.get('msg')==10002 for e in rows):return None
    formation=next((i for i,e in enumerate(rows) if e.get('msg')==20001 and e.get('subtask')=='BattleFormationTask'),None)
    # Without support only requirement mismatches prove the job unusable. With
    # support, MaaCore reports the single unborrowable slot as Missing too.
    accepted=('Unavailable','Missing') if support else ('Unavailable',)
    for i,e in enumerate(rows):
        missing=e.get('details',{}).get('opers')
        if (formation is not None and formation<i<len(rows)-1 and e.get('msg')==20000
                and e.get('subtask')=='BattleFormationTask' and e.get('why')=='OperatorMissing'
                and isinstance(missing,dict) and missing and (not support or len(missing)==1)
                and all(isinstance(v,list) and v and all(isinstance(o,dict) and o.get('name')
                    and o.get('reason') in accepted for o in v) for v in missing.values())):
            return {'start':rows[0],'rejected':e,'terminal':rows[-1],'support':bool(support)}
    return None


def reject_formation(nav,prefix,code,mode,error,support=False):
    proof=formation_rejection(error,support)
    if proof is None:return
    evidence=DATA/'screens/formation-rejections'/f'{time.time_ns()}-{code}-{mode}'
    _,texts=nav.observe(str(evidence/'selection'))
    write_json(evidence/'before.json',{'at':now(),'code':code,'mode':mode,'proof':proof,'events':error.events,'texts':texts})
    anchors=(('清空选择',(410,640,570,715)),('确认',(980,635,1180,715)),('稀有度',(890,5,1010,85)))
    if blocked_overlay(texts) or not fixed_regions(texts,anchors):
        raise RecoveryFailure('编队被拒绝，但当前选人界面未确认；停止自动恢复')
    check_stop();nav.home();nav.enter(prefix);row=nav.find(code,mode=mode)
    if row.get('code')!=code or row.get('mode')!=mode or row.get('state') not in ('uncleared','partial'):
        raise RecoveryFailure('编队失败恢复后未确认目标关卡与难度')
    write_json(evidence/'result.json',{'at':now(),'kind':'formation_rejected','game_result':'not_started',
        'proof':proof,'returned_detail':row,'may_start_formal':False})
    failure=PracticeFailure(f'{code} '+('未借到符合要求的助战干员' if support else '作业编队不可用')+'；已确认未开战并返回地图，排除此作业',evidence)
    failure.recovered=True;failure.failure_kind='support_unavailable' if support else 'formation_rejected'
    failure.game_result='not_started'
    raise failure
