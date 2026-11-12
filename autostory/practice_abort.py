"""Reject one interrupted exercise only after proving return to its map."""
import time
from .common import DATA,now,write_json
from .home_observation import navigation_regions
from .recovery import PracticeFailure,recover_practice

def exercise_abort(events):
    starts=[(i,e) for i,e in enumerate(events) if e.get('msg')==20002 and
            e.get('details',{}).get('task') in ('BattleStartExercise','BattleStartRaid','BattleStartNormal')]
    if len(starts)!=1 or starts[0][1]['details']['task']!='BattleStartExercise':return None
    start_index,start=starts[0];task_id=start.get('taskid')
    if task_id is None or start.get('taskchain')!='Copilot':return None
    for i,event in enumerate(events):
        d=event.get('details',{})
        if not (i>start_index and event.get('taskid')==task_id and event.get('taskchain')=='Copilot'
                and event.get('msg')==20003 and event.get('what')=='ExceededLimit'
                and d.get('task')=='BattlePauseCancel' and type(d.get('exec_times')) is int
                and type(d.get('max_times')) is int and 0<d['max_times']<=d['exec_times']):continue
        complete=next((e for e in events[i+1:] if e.get('msg')==10002 and e.get('taskid')==task_id
                       and e.get('taskchain')=='Copilot'),None)
        if complete:return {'start':start,'limit':event,'complete':complete}
    return None

def reject_aborted_practice(nav,prefix,code,mode,events):
    abort=exercise_abort(events)
    if abort is None:
        # This hook is only used for exercises. A partial abort record must not
        # fall through to settlement-star matching and authorize a paid run.
        if any(e.get('msg')==20003 and e.get('what')=='ExceededLimit'
               and e.get('details',{}).get('task')=='BattlePauseCancel' for e in events):
            raise RuntimeError('MAA 演习中断回调不完整，禁止将结算页判为演习通过')
        return
    evidence=DATA/'screens/practice-aborts'/f'{time.time_ns()}-{code}-{mode}'
    from .motion import UnsettledFrameError
    try:image,texts=nav.observe(str(evidence/'returned-map'))
    except UnsettledFrameError:
        image,texts=None,[]
    page,_=navigation_regions(texts)
    from .navigation import is_settlement,header_matches
    write_json(evidence/'observation.json',{'at':now(),'code':code,'mode':mode,'callbacks':abort,
               'page':page,'texts':texts,'game_result':'unverified','may_start_formal':False})
    recovered=None
    if any(x.get('text') in ('放弃行动','继续行动') for x in texts):
        raise RuntimeError('演习中断后画面可能仍在战斗，停止后续操作')
    if page!='map' or not nav.visible_stages(texts,prefix):
        # The exact exercise-only abort proof permits waiting through loading.
        # Recovery itself only clicks a failed result/target settlement, never
        # an unknown frame or a paused battle, and has a bounded deadline.
        if is_settlement(texts) and not header_matches(texts,code):
            raise RuntimeError('演习中断后结算关卡不符，停止后续操作')
        recovered=recover_practice(nav,code,mode,prefix)
    row=nav.find(code,mode=mode)
    if (row.get('code')!=code or row.get('mode')!=mode or row.get('state') not in ('uncleared','partial')):
        raise RuntimeError('演习中断后目标关卡或难度未确认，停止后续操作')
    write_json(evidence/'result.json',{'at':now(),'kind':'practice_execution_aborted','game_result':'unverified',
               'code':code,'mode':mode,'callbacks':abort,'returned_detail':row,'recovery':recovered,'may_start_formal':False})
    failure=PracticeFailure(f'{code} 演习执行中断：MAA 取消暂停超过次数；已确认返回目标地图，未取得通关结算，跳过此作业',evidence)
    failure.recovered=True;failure.failure_kind='execution_aborted';failure.game_result='unverified'
    raise failure
