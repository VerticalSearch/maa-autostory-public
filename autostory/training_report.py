"""Read-only "练谁最划算" report.

For every pending stage with a saved job search, each job that the current box
cannot field (even after the authorised automatic skill training and one
borrowed support unit) is re-matched with exactly one owned operator raised to
that job's stated requirement.  Operators whose training alone unlocks the most
pending stages, at the lowest estimated effort, rank first.

Nothing here touches the device, MaaCore or the job service: inputs are the
saved box, progress and per-stage search results.
"""
from __future__ import annotations
import copy
import re
import threading
from .common import DATA, MAA, read_json, settings, now

MODULE_TYPES = {1: 'X', 2: 'Y', 3: 'A', 4: 'D', 5: 'B'}
TOP = 10
_NAME = re.compile(r'；([^；:：]+):')


def skill_text(level):
    return f'专精{level-7}' if level > 7 else f'{level}级'


def _box():
    return read_json(DATA / 'operators.json') or read_json(MAA / 'data/OperBoxData.json', {}) or {}


def _ceiling_box(box, config):
    """The box as the runner sees it once authorised skill training is applied."""
    from .skill_training import training_policy
    try:
        if training_policy(config) is None:
            return box
    except ValueError:
        return box
    result = copy.deepcopy(box)
    for row in result.get('own_opers', []):
        if not row.get('own') or row.get('rarity') not in (3, 4, 5):
            continue
        ceiling = 7 if row.get('elite', 0) >= 1 else 4
        if type(row.get('mainSkillLevel')) is int and row['mainSkillLevel'] >= ceiling:
            continue
        row['mainSkillLevel'] = ceiling
        row.pop('skills', None)
        row['observedSkillLevels'] = {k: ceiling for k in row.get('observedSkillLevels', {})}
    return result


def _current_skill(row, skill, need):
    """The rank match_job treats as known, or None when it would only warn."""
    observed = row.get('observedSkillLevels', {}).get(str(skill))
    if type(observed) is int and 1 <= observed <= 10:
        return observed
    known = row.get('mainSkillLevel')
    if type(known) is int and 1 <= known <= 7 and (known < 7 or need <= 7):
        return known
    skills = row.get('skills', [])
    if not skill or known is None or not isinstance(skills, list) or len(skills) < skill:
        return None
    extra = skills[skill - 1].get('level', 0) if isinstance(skills[skill - 1], dict) else 0
    return known + extra if type(known) is int and type(extra) is int else None


def trained_row(row, entry):
    """Return ``(row', steps, effort)`` raising one operator to ``entry``'s needs."""
    req = entry.get('requirements', {}) if isinstance(entry.get('requirements'), dict) else {}
    skill = entry.get('skill', 0) if type(entry.get('skill', 0)) is int else 0
    out = copy.deepcopy(row)
    steps = []
    effort = 0.0
    elite = out.get('elite', 0) or 0
    level = out.get('level', 0) or 0
    need_elite = req.get('elite', -1) if type(req.get('elite', -1)) is int else -1
    need_level = req.get('level', -1) if type(req.get('level', -1)) is int else -1
    need_skill = req.get('skill_level', 0) if type(req.get('skill_level', 0)) is int else 0
    target_elite = max(elite, need_elite)
    if skill == 3 or need_skill > 7:
        target_elite = max(target_elite, 2)
    elif skill == 2 or need_skill > 4:
        target_elite = max(target_elite, 1)
    if target_elite > elite:
        effort += sum({1: 2.0, 2: 6.0}.get(e, 0) for e in range(elite + 1, target_elite + 1))
        steps.append(f'精{"零一二"[elite]}→精{"零一二"[target_elite]}')
        level = 1
    target_level = level
    if need_level > 0 and (need_elite == target_elite or need_elite < 0):
        target_level = max(level, need_level)
    if target_level > level:
        effort += (target_level - level) / 10 * {0: .3, 1: .5, 2: 1.0}.get(target_elite, 1.0)
        steps.append(f'等级{level}→{target_level}' if level > 1 or target_elite == elite else f'等级{target_level}')
    out['elite'] = target_elite
    out['level'] = target_level
    potential = req.get('potential', req.get('potentiality', 0))
    if type(potential) is int and potential > (out.get('potential', 0) or 0):
        effort += 10.0 * (potential - (out.get('potential', 0) or 0))
        steps.append(f'潜能{out.get("potential", 0)}→{potential}（需信物）')
        out['potential'] = potential
    current = _current_skill(out, skill, need_skill) if need_skill > 0 and skill else None
    if current is not None and current < need_skill:
        # Only a *known* shortfall is a training step; an unread skill rank is
        # never a hard blocker in match_job, so it needs no hypothetical.
        effort += .3 * max(0, min(7, need_skill) - min(7, current)) + 2.5 * max(0, need_skill - max(7, current))
        steps.append(f'{skill}技能{skill_text(current)}→{skill_text(need_skill)}')
        levels = dict(out.get('observedSkillLevels', {}))
        levels[str(skill)] = need_skill
        out['observedSkillLevels'] = levels
        if type(out.get('mainSkillLevel')) is not int or out['mainSkillLevel'] < min(7, need_skill):
            out['mainSkillLevel'] = min(7, need_skill)
    module = req.get('module', -1)
    if type(module) is int and module > 0 and module in MODULE_TYPES:
        kind = MODULE_TYPES[module]
        need = max(1, req.get('module_level', 1) if type(req.get('module_level', 1)) is int else 1)
        equips = [dict(e) for e in out.get('equips', []) if isinstance(e, dict)]
        equip = next((e for e in equips if e.get('type') == kind), None)
        have = equip.get('level') if equip and type(equip.get('level')) is int else 0
        if have < need:
            effort += (4.0 if have == 0 else 0) + 1.5 * (need - max(1, have))
            steps.append(f'{kind}模组{"解锁" if have == 0 else f"{have}级"}→{need}级')
            if equip is None:
                equips.append({'type': kind, 'level': need})
            else:
                equip['level'] = need
            out['equips'] = equips
    return out, steps, round(effort, 2)


def _with_row(box, row):
    own = [row if o.get('id') == row.get('id') else o for o in box.get('own_opers', [])]
    return {**box, 'own_opers': own}


def _entries(doc, name):
    found = [o for o in doc.get('opers', []) if isinstance(o, dict) and o.get('name') == name]
    for group in doc.get('groups', []):
        if isinstance(group, dict):
            found += [o for o in group.get('opers', []) if isinstance(o, dict) and o.get('name') == name]
    return found


def build(box=None, config=None, progress=None, cache_dir=None):
    from .jobs import match_job, support_allowed
    from .campaign import execution_blockers, stage_info
    config = settings() if config is None else config
    box = _box() if box is None else box
    progress = read_json(DATA / 'progress.json', {}) if progress is None else progress
    cache_dir = DATA / 'jobs' if cache_dir is None else cache_dir
    try:
        borrow = support_allowed(config)
    except ValueError:
        borrow = False
    chars = read_json(MAA / 'resource/battle_data.json', {}).get('chars', {})
    ready = _ceiling_box(box, config)
    own = {}
    for row in ready.get('own_opers', []):
        if row.get('own'):
            own.setdefault(row.get('name'), []).append(row)

    def fields(doc, trial):
        try:
            return match_job(doc, trial, allow_support=borrow, chars=chars)['status'] != 'blocked'
        except (ValueError, TypeError, KeyError, AttributeError, IndexError):
            return False

    summary = {'pending': 0, 'searched': 0, 'ready': 0, 'blocked': 0, 'jobs_blocked': 0,
               'jobs_single': 0, 'jobs_multi': 0, 'unsearched': []}
    best = {}
    for row in progress.values() if isinstance(progress, dict) else []:
        if not isinstance(row, dict) or row.get('state') not in ('uncleared', 'partial'):
            continue
        code = row.get('code')
        mode = row.get('mode', 'normal')
        if mode not in ('normal', 'raid') or not isinstance(code, str):
            continue
        try:
            if execution_blockers(stage_info(code, mode)):
                continue
        except Exception:
            continue
        summary['pending'] += 1
        label = code + (' 突袭' if mode == 'raid' else '')
        jobs = read_json(cache_dir / f'{code}-{mode}-search.json', None)
        if not isinstance(jobs, list):
            summary['unsearched'].append(label)
            continue
        summary['searched'] += 1
        docs = [item.get('preview') for item in jobs if isinstance(item, dict) and isinstance(item.get('preview'), dict)]
        if any(fields(doc, ready) for doc in docs):
            summary['ready'] += 1
            continue
        summary['blocked'] += 1
        for item in jobs:
            doc = item.get('preview') if isinstance(item, dict) else None
            if not isinstance(doc, dict):
                continue
            summary['jobs_blocked'] += 1
            try:
                reasons = match_job(doc, ready, allow_support=borrow, chars=chars).get('reasons', [])
            except (ValueError, TypeError, KeyError, AttributeError, IndexError):
                continue
            names = {m.group(1).strip() for reason in reasons for m in _NAME.finditer(reason)}
            unlocked = False
            for name in sorted(names):
                for actual in own.get(name, []):
                    for entry in _entries(doc, name):
                        trained, steps, effort = trained_row(actual, entry)
                        if not steps or not fields(doc, _with_row(ready, trained)):
                            continue
                        unlocked = True
                        stages = best.setdefault(name, {})
                        prior = stages.get(label)
                        if prior is None or effort < prior['effort']:
                            stages[label] = {'stage': label, 'job': item.get('id'), 'steps': steps,
                                             'effort': effort, 'cost': row.get('cost')}
            summary['jobs_single' if unlocked else 'jobs_multi'] += 1
    ranking = []
    for name, stages in best.items():
        rows = sorted(stages.values(), key=lambda r: (r['effort'], r['stage']))
        effort = max(r['effort'] for r in rows)
        ranking.append({'name': name, 'stages': [r['stage'] for r in rows], 'count': len(rows),
                        'cheapest': rows[0], 'effort': effort, 'details': rows[:6]})
    ranking.sort(key=lambda r: (-r['count'], r['effort'], r['name']))
    return {'at': now(), 'box_time': box.get('syncTime'), 'support': borrow,
            'summary': summary, 'ranking': ranking[:TOP], 'more': max(0, len(ranking) - TOP)}


# --- cached access for the dashboard ----------------------------------------
# A full report over the saved searches takes well under a second; it is only
# rebuilt when one of its input files changes, so dashboard polling is cheap.

_lock = threading.Lock()
_cache = {'key': None, 'report': None, 'error': None}


def _stamp(path):
    try:
        st = path.stat()
        return (str(path), st.st_mtime_ns, st.st_size)
    except OSError:
        return (str(path), 0, 0)


def inputs_key(data=None, maa=None, root=None):
    from .common import ROOT
    data = DATA if data is None else data
    maa = MAA if maa is None else maa
    root = ROOT if root is None else root
    paths = [data / 'operators.json', maa / 'data/OperBoxData.json', data / 'progress.json', root / 'settings.json']
    stamps = [_stamp(p) for p in paths]
    try:
        stamps += sorted(_stamp(p) for p in (data / 'jobs').glob('*-search.json'))
    except OSError:
        pass
    return tuple(stamps)


def cached():
    """Latest report, rebuilt only when the box, progress, settings or searches change."""
    key = inputs_key()
    with _lock:
        if _cache['key'] != key:
            try:
                _cache.update(key=key, report=build(), error=None)
            except Exception as error:  # A report failure must never break the dashboard.
                _cache.update(key=key, report=None, error=str(error) or type(error).__name__)
        report = _cache['report']
        result = {'state': 'error' if _cache['error'] else 'ready', 'error': _cache['error']}
    if report is not None:
        result.update({k: report.get(k) for k in ('at', 'box_time', 'support', 'summary', 'ranking', 'more')})
    return result
