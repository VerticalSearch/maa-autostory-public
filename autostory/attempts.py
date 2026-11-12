"""Persistent battle-attempt history keyed by stage, job and roster digests.

One worker owns mutations.  This module deliberately has no locks, child
processes or threads; ``blocked`` rereads the file so a restarted worker sees
the previous worker's failures.

The two fingerprints are conservative: known non-execution metadata is
dropped, every other field (including unknown ones) participates in the hash.
Missing fields are not defaulted, so absent-vs-null remains distinguishable.
"""
from __future__ import annotations

import copy
import hashlib
import json
from datetime import datetime
from pathlib import Path

from .common import DATA, now, read_json, write_json

SCHEMA = 1
_MISSING = object()
MODES = ('normal', 'raid')
FAILURE_OUTCOMES = ('practice_failed', 'formal_failed')
OUTCOMES = FAILURE_OUTCOMES + ('passed',)
MAX_REASON = 1000

# Timestamp/recognition/snapshot plumbing must never invalidate a fingerprint.
_META_KEYS = frozenset({
    'synctime', 'sync_time', 'source', 'done', 'at', 'time', 'timestamp',
    'created_at', 'updated_at', 'fetched_at', 'recognized_at', 'recognition',
    'confidence', 'score', 'bbox', 'rect', 'screenshot', 'image', 'image_path',
    'screenshot_path', 'ocr', 'raw', 'debug',
})
# Top-level job-document metadata; the narrative ``doc`` subtree is excluded too.
_JOB_META_KEYS = _META_KEYS | frozenset({
    'doc', 'id', 'title', 'url', 'like', 'views', 'rating', 'rating_ratio',
    'available', 'uploader', 'content', 'preview', 'status_code', 'statuscode',
    'has_next', 'hasnext',
})


class AttemptHistoryError(ValueError):
    """The persisted history is unreadable; never overwrite it silently."""


def _canonical(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'))


def _sha256(value) -> str:
    return hashlib.sha256(_canonical(value).encode('utf-8')).hexdigest()


def _strip_meta(value, keys):
    if isinstance(value, dict):
        # Drop known envelope fields only. A nested skill may legitimately
        # contain a semantic field named time/score/source in future snapshots.
        return {k: copy.deepcopy(v) for k, v in value.items()
                if k.lower() not in keys}
    return copy.deepcopy(value)


def roster_fingerprint(box: dict) -> str:
    """Hash owned operators only; ignore snapshot time and row ordering."""
    if not isinstance(box, dict):
        raise TypeError('box must be a dict')
    rows = box.get('own_opers', [])
    if not isinstance(rows, list):
        raise ValueError('box own_opers must be a list')
    owned = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError('box own_opers entries must be dicts')
        if not row.get('own'):
            continue
        clean = _strip_meta(row, _META_KEYS)
        owned.append(clean)
    owned.sort(key=lambda r: (_canonical(r.get('id')),
                              _canonical(r.get('name')), _canonical(r)))
    rest = {k: v for k, v in box.items() if k != 'own_opers'}
    return _sha256({'box': _strip_meta(rest, _META_KEYS), 'owned': owned})


def job_fingerprint(doc: dict) -> str:
    """Hash executable job content, ignoring only narrative/metadata fields."""
    if not isinstance(doc, dict):
        raise TypeError('job document must be a dict')
    executable = {k: copy.deepcopy(v) for k, v in doc.items()
                  if k.lower() not in _JOB_META_KEYS}
    return _sha256(executable)


def game_day(at=None):
    """Arknights CN server day: resets at 04:00 UTC+8."""
    from datetime import timedelta, timezone
    moment = at if at is not None else datetime.now(timezone.utc)
    return (moment.astimezone(timezone(timedelta(hours=8))) - timedelta(hours=4)).date().isoformat()


def team_fingerprint(box, match, context='MAA-6.20.1', support_day=None):
    """Bind a result to the chosen operators, not unrelated account changes.

    Only observed battle attributes participate. Missing skill/module values
    remain missing; timestamps and evidence paths never change the digest.
    """
    by_id={row['id']:row for row in box.get('own_opers',[]) if row.get('own')}
    team=[]
    for slot in match.get('assignment',[]):
        if slot.get('support'):
            # A borrowed unit is chosen by MaaCore at battle time from the
            # current support list. Bind the slot's borrowable set and the game
            # day (04:00 UTC+8 reset), so an unavailable borrow is retried on a
            # later day instead of excluding the job forever.
            team.append({'slot':slot['slot'],'support':True,'day':support_day if support_day else game_day(),
                         'operators':copy.deepcopy(slot.get('support_opers',[slot['operator']]))})
            continue
        oid=slot['id']
        if oid not in by_id:raise ValueError('Assigned operator is missing from current snapshot')
        row=by_id[oid]
        fields=('id','name','elite','level','potential','mainSkillLevel','skills','observedSkillLevels','equips','trust')
        actual={key:copy.deepcopy(row[key]) for key in fields if key in row}
        team.append({'slot':slot['slot'],'operator':slot['operator'],'actual':actual})
    return 'team-v1:'+_sha256({'context':context,'team':sorted(team,key=_canonical)})


def _text(value, name):
    if not isinstance(value, str):
        raise TypeError(f'{name} must be a string')
    if not value.strip():
        raise ValueError(f'{name} must be a non-empty string')
    return value


def _job_id(value):
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise TypeError('job_id must be an int or non-empty string')
    if isinstance(value, int):
        if value < 0:
            raise ValueError('job_id must not be negative')
        return value
    return _text(value, 'job_id')


def _same_job_id(left, right) -> bool:
    return type(left) is type(right) and left == right


def outcome_stamp(value):
    try:
        dt=datetime.fromisoformat(value)
        if dt.tzinfo is None:raise ValueError('timezone missing')
        return dt.timestamp()
    except (ValueError,TypeError) as exc:
        raise AttemptHistoryError('恢复结果的时间无效') from exc


def outcome_key(entry):
    return (entry['code'],entry['mode'],type(entry['job_id']).__name__,entry['job_id'],entry['job_sha'],entry['roster_sha'])


def _evidence(value):
    if value is None or isinstance(value, str):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (list, dict)):
        try:
            json.dumps(value, ensure_ascii=False)
        except (TypeError, ValueError) as exc:
            raise TypeError('evidence must be JSON-serializable') from exc
        return copy.deepcopy(value)
    raise TypeError('evidence must be None, str, Path, list or dict')


def _validate_record(record):
    if not isinstance(record, dict):
        raise AttemptHistoryError('attempt entry must be an object')
    missing = [k for k in ('code', 'mode', 'job_id', 'job_sha', 'roster_sha',
                           'outcome', 'reason', 'evidence', 'at')
               if k not in record]
    if missing:
        raise AttemptHistoryError(f'attempt entry missing: {", ".join(missing)}')
    for key in ('code', 'job_sha', 'roster_sha', 'at'):
        if not isinstance(record[key], str) or not record[key].strip():
            raise AttemptHistoryError(f'attempt {key} must be a non-empty string')
    if record['mode'] not in MODES:
        raise AttemptHistoryError('attempt mode is invalid')
    if record['outcome'] not in OUTCOMES:
        raise AttemptHistoryError('attempt outcome is invalid')
    if not isinstance(record['reason'], str):
        raise AttemptHistoryError('attempt reason must be a string')
    try:
        _job_id(record['job_id'])
        _evidence(record['evidence'])
    except (TypeError, ValueError) as exc:
        raise AttemptHistoryError(f'invalid attempt field: {exc}') from exc


class AttemptHistory:
    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path is not None else DATA / 'attempt-history.json'

    def _load(self):
        try:
            data = read_json(self.path, _MISSING)
        except ValueError as exc:
            raise AttemptHistoryError(f'malformed attempt history: {exc}') from exc
        if data is _MISSING:
            return []
        if not isinstance(data, dict):
            raise AttemptHistoryError('attempt history root must be an object')
        if type(data.get('schema')) is not int or data['schema'] != SCHEMA:
            raise AttemptHistoryError('unsupported attempt history schema')
        attempts = data.get('attempts')
        if not isinstance(attempts, list):
            raise AttemptHistoryError('attempt history attempts must be a list')
        for record in attempts:
            _validate_record(record)
        return attempts

    def recover_checkpoints(self, budgets):
        """Restore latest confirmed outcomes before a resume or a new budget.

        Original event times are retained. A later exact outcome (including a
        pass) wins; importing the same checkpoint twice makes no new record.
        """
        from .run_ledger import validate_stages
        attempts=self._load();pending={};seen={}
        for budget in budgets:
            if not isinstance(budget,dict):raise AttemptHistoryError('待恢复预算必须是对象')
            validate_stages(budget)
            for key,item in budget.get('stages',{}).items():
                code,sep,mode=key.rpartition(':')
                if not sep or not code or mode not in MODES:raise AttemptHistoryError('待恢复关卡标识无效')
                for entry in item['attempts']:
                    phase=entry['phase']
                    if phase not in (*FAILURE_OUTCOMES,'verified'):continue
                    restored={'code':code,'mode':mode,'job_id':entry['job_id'],
                              'job_sha':entry.get('job_sha'),'roster_sha':entry.get('team_sha'),
                              'outcome':'passed' if phase=='verified' else phase,
                              'reason':'从已确认的检查点恢复结果','evidence':entry.get('evidence'),
                              'at':entry.get('updated_at',entry.get('at'))}
                    _validate_record(restored);stamp=outcome_stamp(restored['at']);key=outcome_key(restored)
                    event=(key,stamp)
                    if event in seen and seen[event]!=restored['outcome']:
                        raise AttemptHistoryError('同一作业与阵容的检查点结果同秒冲突，停止恢复')
                    seen[event]=restored['outcome']
                    if key not in pending or stamp>outcome_stamp(pending[key]['at']):pending[key]=restored
        # History append order is known within its single atomic journal. Across
        # separate checkpoints only the original event time establishes order.
        latest={outcome_key(entry):entry for entry in attempts}
        recovered=0
        for key,entry in sorted(pending.items(),key=lambda pair:(outcome_stamp(pair[1]['at']),repr(pair[0]))):
            existing=latest.get(key)
            if existing:
                old=outcome_stamp(existing['at']);new=outcome_stamp(entry['at'])
                if old==new and existing['outcome']!=entry['outcome']:
                    raise AttemptHistoryError('历史与检查点的同秒结果冲突，停止恢复')
                if old>=new:continue
            attempts.append(entry);recovered+=1
        if recovered:write_json(self.path,{'schema':SCHEMA,'attempts':attempts})
        return recovered

    def latest(self, code, mode, job_id, job_sha, roster_sha):
        """Latest exact outcome, including a pass that supersedes old failures."""
        job_id = _job_id(job_id)
        for record in reversed(self._load()):
            if (record['code'] == code and record['mode'] == mode
                    and _same_job_id(record['job_id'], job_id)
                    and record['job_sha'] == job_sha
                    and record['roster_sha'] == roster_sha):
                return copy.deepcopy(record)
        return None

    def blocked(self, code, mode, job_id, job_sha, roster_sha):
        """Latest exact-match failure, or None if absent or later passed."""
        record=self.latest(code,mode,job_id,job_sha,roster_sha)
        return record if record and record['outcome'] in FAILURE_OUTCOMES else None

    def blocked_equivalent(self,code,mode,job_sha,roster_sha):
        """Reuse an outcome for identical executable content from another source.

        A different script or changed chosen team remains eligible. Metadata,
        URLs and source IDs alone do not justify spending another practice.
        """
        for record in reversed(self._load()):
            if (record['code']==code and record['mode']==mode
                    and record['job_sha']==job_sha and record['roster_sha']==roster_sha):
                return copy.deepcopy(record) if record['outcome'] in FAILURE_OUTCOMES else None
        return None

    def record(self, code, mode, job_id, job_sha, roster_sha, outcome,
               reason='', evidence=None):
        """Append one validated attempt and atomically persist the history."""
        code = _text(code, 'code')
        if mode not in MODES:
            raise ValueError('mode must be normal or raid')
        job_id = _job_id(job_id)
        job_sha = _text(job_sha, 'job_sha')
        roster_sha = _text(roster_sha, 'roster_sha')
        if outcome not in OUTCOMES:
            raise ValueError('outcome must be practice_failed, formal_failed or passed')
        if not isinstance(reason, str):
            raise TypeError('reason must be a string')
        reason = reason[:MAX_REASON]
        evidence = _evidence(evidence)
        attempts = self._load()
        entry = {
            'code': code, 'mode': mode, 'job_id': job_id,
            'job_sha': job_sha, 'roster_sha': roster_sha,
            'outcome': outcome, 'reason': reason,
            'evidence': evidence, 'at': now(),
        }
        attempts.append(entry)
        write_json(self.path, {'schema': SCHEMA, 'attempts': attempts})
        return copy.deepcopy(entry)
