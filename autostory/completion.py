"""Pure execution-completion summary for AutoStory.

This module contains no I/O, no filesystem/process/UI imports and no project
imports.  It reads the mappings it is given and returns a fresh dict.  Inputs
are never modified.

Semantics
---------
* Only ``normal``/``raid`` catalogue stages in the requested groups form the
  execution scope and contribute to ``total``.  Other modes in those groups
  contribute to ``unsupported`` and are reported separately; they never make a
  scope fail the normal/raid completion test, but they are never claimed as
  cleared either.
* ``cleared``/``pending``/``locked`` are read only from the exact catalogue
  key in ``progress``.  A record is malformed when it is not a mapping, lacks a
  recognised state, or carries a ``code``/``mode`` that contradicts the
  catalogue stage.  Absent and malformed records both count as ``unknown``.
  A malformed record invalidates only its own stage: later valid records are
  still classified, while the overall result remains fail-closed ``partial``.
* Reports are considered only for the requested groups.  The last report for a
  group wins, and only that effective latest entry is validated; a malformed
  superseded report or an unrelated-group report cannot poison the result.
  ``blocked_groups`` is therefore scoped to requested groups, and ``skipped``
  counts only requested normal/raid catalogue keys.  A skipped foreign code is
  ignored.
 * Reports never upgrade a stage to cleared. Two observed full activity counters
   can separately cover an activity scope; these are not new stage wins and do
   not change progress/history. Only per-stage records contribute to cleared.
* ``done`` requires a non-empty normal/raid scope that is entirely cleared,
  valid evidence, and no latest blocking report in the requested groups.
  Empty, invalid or unknown scope is always ``partial``, never vacuously
  ``done``.
* Budget exhaustion is reported only for a valid, non-empty, incomplete
  normal/raid scope.  An already cleared scope stays ``done`` even when the
  ledger has no budget left.  A finite limit is exhausted at ``count >= limit``;
  ``None`` means unlimited.
"""

__all__ = ["summarize_execution"]


def activity_progress_groups(book, proof):
    """Validate the aggregate evidence contract without reading files."""
    import math
    import re
    if not isinstance(proof, dict) or type(proof.get('schema')) is not int or proof['schema'] != 1 or proof.get('source') != 'activity_counter':
        return ()
    base = proof.get('base_group')
    profiles = book.get('profiles', {}) if isinstance(book, dict) else {}
    if not isinstance(profiles, dict) or not isinstance(base, str):return ()
    owner = profiles.get(base, {})
    if not isinstance(owner, dict) or owner.get('kind') != 'side' or owner.get('base', base) != base:return ()
    current, total = proof.get('current'), proof.get('total')
    if type(current) is not int or type(total) is not int or not 0 <= current <= total or not 0 < total <= 9999:return ()
    if proof.get('new_clear') is not False or not isinstance(proof.get('evidence'), str) or not proof['evidence']:return ()
    samples, frames = proof.get('observations'), proof.get('frames')
    if not isinstance(samples, list) or len(samples) != 2 or not isinstance(frames, list) or len(frames) != 2:return ()
    kinds = set()
    for sample in samples:
        if not isinstance(sample, dict):return ()
        if sample.get('base_group') != base or sample.get('current') != current or sample.get('total') != total:return ()
        if type(sample.get('current')) is not int or type(sample.get('total')) is not int:return ()
        if sample.get('screen_kind') not in ('map', 'intro', 'collection') or sample.get('probe_count') != 2:return ()
        if sample['screen_kind'] == 'collection':
            if sample.get('completion_tab') is not True or sample.get('title') != owner.get('title'):return ()
            for key in ('counter_box', 'title_box'):
                box = sample.get(key)
                if not isinstance(box, (list, tuple)) or len(box) != 4:return ()
                if any(type(v) not in (int, float) or not math.isfinite(v) for v in box):return ()
                if not (50 <= box[0] < box[2] <= 1155 and 75 <= box[1] < box[3] <= 695):return ()
        score = sample.get('score')
        if type(score) not in (int, float) or not math.isfinite(score) or not .88 <= score <= 1:return ()
        kinds.add(sample['screen_kind'])
    if len(kinds) != 1:return ()
    for frame in frames:
        if not isinstance(frame, dict) or not isinstance(frame.get('path'), str) or not frame['path']:return ()
        if not isinstance(frame.get('sha256'), str) or not re.fullmatch(r'[0-9a-f]{64}', frame['sha256']):return ()
    return tuple(group for group, profile in profiles.items() if isinstance(profile, dict)
                 and profile.get('kind') == 'side' and profile.get('base', group) == base)


def full_activity_groups(book, proof):
    groups = activity_progress_groups(book, proof)
    return groups if groups and proof['current'] == proof['total'] else ()

SCAN_MODES = ("normal", "raid")
PENDING_STATES = ("uncleared", "partial")
UNCONFIRMED_STATES = ("unseen", "unknown")
VALID_STATES = ("cleared",) + PENDING_STATES + ("locked",) + UNCONFIRMED_STATES


def _is_int(value):
    # bool is an int subclass; checkpoints use plain integers.
    return type(value) is int


def _stage_map(book):
    if not isinstance(book, dict):
        return {}, True
    stages = book.get("stages")
    if not isinstance(stages, dict):
        return {}, True
    return stages, False


def _profile_names(book):
    names = []
    if not isinstance(book, dict):
        return names
    profiles = book.get("profiles")
    if isinstance(profiles, dict):
        source = profiles.keys()
    elif isinstance(profiles, (list, tuple)):
        source = profiles
    else:
        return names
    for item in source:
        if isinstance(item, str):
            name = item
        elif isinstance(item, dict):
            name = None
            for field in ("name", "group", "id"):
                value = item.get(field)
                if isinstance(value, str):
                    name = value
                    break
            if name is None:
                continue
        else:
            continue
        if name and name not in names:
            names.append(name)
    return names


def _known_groups(book, stages):
    known = set(_profile_names(book))
    for stage in stages.values():
        if not isinstance(stage, dict):
            continue
        group = stage.get("group")
        if isinstance(group, str) and group:
            known.add(group)
    return known


def _requested_groups(groups):
    """Return (deduplicated valid group names, malformed-list flag)."""
    if not isinstance(groups, (list, tuple)):
        return [], True
    ordered = []
    seen = set()
    malformed = False
    for group in groups:
        if not isinstance(group, str) or not group:
            malformed = True
            continue
        if group not in seen:
            seen.add(group)
            ordered.append(group)
    return ordered, malformed


def _ledger_problem(ledger):
    """Return a short Chinese problem string, or None for a valid ledger."""
    if not isinstance(ledger, dict):
        return "账本不是映射"
    for field in ("sanity", "count", "limit", "practice_count", "practice_limit"):
        if field not in ledger:
            return "账本缺少 " + field
    if not _is_int(ledger["sanity"]):
        return "账本 sanity 不是整数"
    if not _is_int(ledger["count"]) or ledger["count"] < 0:
        return "账本 count 不是非负整数"
    limit = ledger["limit"]
    if limit is not None and (not _is_int(limit) or limit < 0):
        return "账本 limit 不是非负整数或 null"
    if not _is_int(ledger["practice_count"]) or ledger["practice_count"] < 0:
        return "账本 practice_count 不是非负整数"
    practice_limit = ledger["practice_limit"]
    if practice_limit is not None and (not _is_int(practice_limit) or practice_limit < 0):
        return "账本 practice_limit 不是非负整数或 null"
    return None


def _budget_reasons(ledger):
    """Detailed internal reasons; field names are allowed here."""
    reasons = []
    if ledger["sanity"] <= 0:
        reasons.append("理智已用尽（sanity={}）".format(ledger["sanity"]))
    if ledger["limit"] is not None and ledger["count"] >= ledger["limit"]:
        reasons.append("正式作战次数已达上限（count={}，limit={}）".format(
            ledger["count"], ledger["limit"]))
    if ledger["practice_limit"] is not None and ledger["practice_count"] >= ledger["practice_limit"]:
        reasons.append("演习次数已达上限（practice_count={}，practice_limit={}）".format(
            ledger["practice_count"], ledger["practice_limit"]))
    return reasons


def _budget_message_reasons(ledger):
    """Player-facing reasons: per-run allowance wording, no field names."""
    reasons = []
    if ledger["sanity"] <= 0:
        reasons.append("理智预算已用完")
    if ledger["limit"] is not None and ledger["count"] >= ledger["limit"]:
        reasons.append("正式作战次数已达上限")
    if ledger["practice_limit"] is not None and ledger["practice_count"] >= ledger["practice_limit"]:
        reasons.append("演习次数已达上限")
    return reasons


def _record_problem(record, stage):
    """True when a present progress record is malformed for this stage."""
    if not isinstance(record, dict):
        return True
    state = record.get("state")
    if not isinstance(state, str) or state not in VALID_STATES:
        return True
    # Missing code/mode fields are fine for older records; a contradictory
    # one is not proof for this catalogue key.
    expected_code = stage.get("code")
    if (isinstance(expected_code, str) and "code" in record
            and record.get("code") != expected_code):
        return True
    expected_mode = stage.get("mode")
    if (isinstance(expected_mode, str) and "mode" in record
            and record.get("mode") != expected_mode):
        return True
    return False


def _current_record_cleared(progress, key, stages):
    """Whether the *current* progress record positively clears this key."""
    if not isinstance(progress, dict) or key not in progress:
        return False
    record = progress[key]
    if not isinstance(record, dict) or record.get("state") != "cleared":
        return False
    if key in stages:
        stage = stages[key]
        if not isinstance(stage, dict):
            return False
        if _record_problem(record, stage):
            return False
    return True


def _latest_reports(reports, requested):
    """Return (latest_by_group, problem_or_None) for requested groups only.

    Reports are grouped first and the last entry for a group wins.  Only the
    effective latest entries are validated, so a malformed report that is
    superseded by a valid later report cannot poison the result.  Reports for
    unrelated groups are ignored entirely.
    """
    if reports is None:
        return {}, None
    if not isinstance(reports, (list, tuple)):
        return {}, "reports 不是列表"

    latest = {}
    for entry in reports:
        if not isinstance(entry, dict):
            continue
        group = entry.get("group")
        if not isinstance(group, str) or not group or group not in requested:
            continue
        latest[group] = entry

    problem = None
    for group, report in latest.items():
        outcomes = report.get("outcomes", [])
        if outcomes is None:
            continue
        if not isinstance(outcomes, (list, tuple)):
            problem = problem or "{} 的报告 outcomes 不是列表".format(group)
    return latest, problem


def _report_counts(latest, progress, stages, scope_keys):
    """Return (blocked_group_names, skipped_keys, problem_or_None).

    ``latest`` is already limited to requested groups.  Skipped outcomes are
    counted only for catalogue keys in the requested normal/raid scope; a
    skipped foreign code is ignored rather than counted or treated as invalid.
    """
    blocked = []
    skipped = set()
    problem = None
    for group, report in latest.items():
        if report.get("blocked"):
            blocked.append(group)
        outcomes = report.get("outcomes", [])
        if outcomes is None:
            outcomes = []
        if not isinstance(outcomes, (list, tuple)):
            problem = problem or "{} 的报告 outcomes 不是列表".format(group)
            continue
        for outcome in outcomes:
            if not isinstance(outcome, dict):
                problem = problem or "{} 的报告含无效 outcome".format(group)
                continue
            if outcome.get("result") != "skipped":
                continue
            code = outcome.get("code")
            mode = outcome.get("mode")
            if not isinstance(code, str) or not code or not isinstance(mode, str) or not mode:
                problem = problem or "{} 的报告跳过项缺少 code/mode".format(group)
                continue
            key = code + ":" + mode
            if key not in scope_keys:
                continue
            # A stale skipped outcome must not survive a current cleared record.
            if _current_record_cleared(progress, key, stages):
                continue
            skipped.add(key)
    return blocked, skipped, problem


def _counts_detail(summary):
    result = "已通关 {}、待确认 {}、未通关/未满星 {}、锁定 {}".format(
        summary["cleared"], summary["unknown"],
        summary["pending"], summary["locked"])
    if summary.get('activity_complete'):
        result += '、活动满进度覆盖 {}'.format(summary['activity_complete'])
    return result


def _counts_text(summary):
    return "普通/突袭共 {} 项，{}".format(
        summary["total"], _counts_detail(summary))


def _names_text(names, limit=4):
    shown = names[:limit]
    text = "、".join(shown)
    if len(names) > limit:
        text += " 等 {} 个".format(len(names))
    return text


def summarize_execution(book, progress, groups, ledger, reports=()):
    """Summarize execution completion for the requested catalogue groups.

    Returns ``{"state", "message", "reason", "summary"}``.  ``state`` is one of
    ``done``/``partial``/``budget_exhausted``.  See the module docstring for the
    evidence rules.  The function never mutates its arguments.
    """
    stages, book_invalid = _stage_map(book)
    known_groups = _known_groups(book, stages)
    requested, groups_invalid = _requested_groups(groups)
    requested_set = set(requested)
    unknown_groups = [group for group in requested if group not in known_groups]
    latest, reports_problem = _latest_reports(reports, requested_set)
    activity_groups = {group for group, report in latest.items()
                       if not report.get('blocked') and group in full_activity_groups(book, report.get('activity_completion'))}

    progress_shape_invalid = not isinstance(progress, dict)
    progress_evidence_invalid = progress_shape_invalid
    progress_map = progress if isinstance(progress, dict) else {}
    ledger_problem = _ledger_problem(ledger)
    scope_keys = set()

    summary = {
        "total": 0,
        "cleared": 0,
        "pending": 0,
        "locked": 0,
        "unknown": 0,
        "unsupported": 0,
        "blocked_groups": 0,
        "skipped": 0,
    }
    if activity_groups:summary['activity_complete'] = 0

    for key, stage in stages.items():
        if not isinstance(stage, dict):
            book_invalid = True
            continue
        group = stage.get("group")
        if not isinstance(group, str) or not group:
            book_invalid = True
            continue
        if group not in requested_set:
            continue
        mode = stage.get("mode")
        if mode in SCAN_MODES:
            summary["total"] += 1
            scope_keys.add(key)
            if group in activity_groups and not _current_record_cleared(progress_map, key, stages):
                summary['activity_complete'] += 1
                continue
            if progress_shape_invalid or key not in progress_map:
                summary["unknown"] += 1
                continue
            record = progress_map[key]
            if _record_problem(record, stage):
                # Only this stage is unknown.  A later valid record must still
                # be classified normally; the flag is evidence, not a filter.
                progress_evidence_invalid = True
                summary["unknown"] += 1
                continue
            state = record.get("state")
            if state == "cleared":
                summary["cleared"] += 1
            elif state in PENDING_STATES:
                summary["pending"] += 1
            elif state == "locked":
                summary["locked"] += 1
            else:
                summary["unknown"] += 1
        elif isinstance(mode, str) and mode:
            summary["unsupported"] += 1
        else:
            # A catalogue stage without a usable mode cannot prove scope.
            book_invalid = True

    blocked_names, skipped_keys, outcome_problem = _report_counts(
        latest, progress_map, stages, scope_keys)
    reports_problem = reports_problem or outcome_problem
    summary["blocked_groups"] = len(blocked_names)
    summary["skipped"] = len(skipped_keys)

    remaining = summary["total"] - summary["cleared"] - summary.get('activity_complete', 0)
    evidence_invalid = bool(
        book_invalid or groups_invalid or progress_evidence_invalid
        or ledger_problem or reports_problem)
    scope_invalid = bool(groups_invalid or not requested or unknown_groups)

    # A group with no normal/raid stages can never be a vacuous success.
    only_special = summary["total"] == 0 and summary["unsupported"] > 0
    no_scope_stages = summary["total"] == 0 and summary["unsupported"] == 0

    if scope_invalid:
        state = "partial"
    elif summary["total"] == 0:
        state = "partial"
    elif evidence_invalid:
        state = "partial"
    elif remaining == 0:
        state = "partial" if blocked_names else "done"
    else:
        budget_reasons = [] if ledger_problem else _budget_reasons(ledger)
        state = "budget_exhausted" if budget_reasons else "partial"

    # ---- reason ---------------------------------------------------------
    if state == "done":
        reason = "普通/突袭范围全部通关"
        if summary.get('activity_complete'):
            reason = '范围完成；逐关通关证据 {} 项，活动满进度覆盖 {} 项'.format(summary['cleared'], summary['activity_complete'])
        if summary["unsupported"]:
            reason += "；另有 {} 个特殊难度未验证".format(summary["unsupported"])
    elif state == "budget_exhausted":
        reason = ("预算耗尽：" + "；".join(_budget_reasons(ledger))
                  + "；剩余 {} 项未通关".format(remaining))
    else:
        issues = []
        if book_invalid:
            issues.append("目录数据无效")
        if groups_invalid:
            issues.append("所选分组列表无效")
        elif not requested:
            issues.append("未选择任何分组")
        if unknown_groups:
            issues.append("未知分组：" + _names_text(unknown_groups))
        if progress_evidence_invalid:
            issues.append("进度记录无效")
        if ledger_problem:
            issues.append("账本无效：" + ledger_problem)
        if reports_problem:
            issues.append("分组报告无效：" + reports_problem)
        if not issues:
            if only_special:
                issues.append("所选分组只有特殊难度")
            elif no_scope_stages:
                issues.append("所选分组没有普通/突袭关卡")
            elif remaining != 0:
                issues.append("普通/突袭范围未完成")
        if blocked_names:
            issues.append("{} 个分组最近报告阻塞".format(len(blocked_names)))
        if not issues:
            issues.append("普通/突袭范围未完成")
        reason = "；".join(issues)

    # ---- message --------------------------------------------------------
    if state == "done":
        message = "普通/突袭范围完成"
        if summary.get('activity_complete'):
            message = '范围完成（含活动总进度已满而跳过的关卡）'
        if summary["unsupported"]:
            message += "；另有 {} 个特殊难度未验证".format(summary["unsupported"])
        message += "。"
    elif state == "budget_exhausted":
        parts = ["本轮" + "；".join(_budget_message_reasons(ledger))]
        parts.append("普通/突袭范围尚余 {} 项（共 {} 项，{}）".format(
            remaining, summary["total"], _counts_detail(summary)))
        if summary["unsupported"]:
            parts.append("另有 {} 个特殊难度未验证".format(summary["unsupported"]))
        if blocked_names:
            parts.append("{} 个分组最近报告阻塞（{}）".format(
                len(blocked_names), _names_text(blocked_names)))
        if summary["skipped"]:
            parts.append("{} 个关卡被跳过".format(summary["skipped"]))
        message = "；".join(parts) + "。"
    else:
        parts = []
        if book_invalid:
            parts.append("目录数据无效")
        if groups_invalid:
            parts.append("所选分组列表无效")
        elif not requested:
            parts.append("未选择任何分组")
        if unknown_groups:
            parts.append("分组 {} 不在目录中，按阻塞处理".format(
                _names_text(unknown_groups)))
        if progress_evidence_invalid:
            parts.append("进度记录无效")
        if ledger_problem:
            parts.append("预算记录无效")
        if reports_problem:
            parts.append("分组报告无效")
        if only_special:
            parts.append("所选分组没有普通/突袭关卡")
        parts.append(_counts_text(summary))
        if summary["unsupported"]:
            parts.append("另有 {} 个特殊难度未适配".format(summary["unsupported"]))
        if blocked_names:
            parts.append("{} 个分组最近报告阻塞（{}）".format(
                len(blocked_names), _names_text(blocked_names)))
        if summary["skipped"]:
            parts.append("{} 个关卡被跳过".format(summary["skipped"]))
        message = "未完成：" + "；".join(parts) + "。"

    return {
        "state": state,
        "message": message,
        "reason": reason,
        "summary": summary,
    }
