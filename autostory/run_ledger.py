"""Per-stage attempt accounting embedded in the existing atomic campaign file."""
import copy
from .common import now
from .recovery import StateIntegrityError


def optional_limit(value,minimum=0):
    """A null cap is unlimited; malformed values never silently disable it."""
    if value is not None and (type(value) is not int or value<minimum):
        raise StateIntegrityError('次数上限必须为有效整数或 null（不限次数）')
    return value


def limit_reached(count,limit):
    return limit is not None and count>=limit


def stage_entry(ledger,key,limit):
    optional_limit(limit,1)
    stages=ledger.setdefault('stages',{})
    if not isinstance(stages,dict):raise StateIntegrityError('单关检查点格式无效')
    item=stages.setdefault(key,{'practice_count':0,'candidate_limit':limit,'attempts':[]})
    if (not isinstance(item,dict) or type(item.get('practice_count')) is not int
        or item['practice_count']<0 or 'candidate_limit' not in item
        or not isinstance(item.get('attempts'),list)
        or any(not isinstance(x,dict) or 'job_id' not in x or 'phase' not in x for x in item['attempts'])):
        raise StateIntegrityError('单关尝试次数或历史无效，停止续跑')
    optional_limit(item['candidate_limit'],1)
    if len(item['attempts'])!=item['practice_count']:
        raise StateIntegrityError('单关次数与尝试记录不一致，停止续跑')
    for attempt in item['attempts']:
        if attempt['phase'] in ('practice_failed','formal_failed') and any(
                not isinstance(attempt.get(field),str) or not attempt[field] for field in ('job_sha','team_sha')):
            raise StateIntegrityError('已失败尝试缺少作业或阵容指纹，停止续跑')
    return item


def reserve_practice(ledger,key,limit,job_id,job_sha,team_sha):
    item=stage_entry(ledger,key,limit)
    if limit_reached(item['practice_count'],item['candidate_limit']):
        raise StateIntegrityError('本关演习额度已用完')
    item['practice_count']+=1
    ledger['practice_count']+=1
    record={'job_id':job_id,'job_sha':job_sha,'team_sha':team_sha,'phase':'practice_reserved','at':now()}
    item['attempts'].append(record)
    return record


def transition(record,phase,**fields):
    record.update(copy.deepcopy(fields),phase=phase,updated_at=now())


def validate_stages(ledger):
    stages=ledger.get('stages',{})
    if not isinstance(stages,dict):raise StateIntegrityError('单关检查点格式无效')
    total=0
    for key in list(stages):total+=stage_entry(ledger,key,3)['practice_count']
    if total>ledger.get('practice_count',0):
        raise StateIntegrityError('单关尝试总数超过全局计数，停止续跑')
