"""Pure scan-only coverage and resume planning for AutoStory.

This module contains no I/O, no project imports and no network access.  It
only reads the two mappings it is given (a catalogue ``book`` and a progress
``progress`` mapping) and returns fresh dictionaries/lists.

Semantics
---------
* A catalogue stage belongs to a group when ``stage["group"]`` equals the
  requested group exactly.  Profile titles, chapter values and parent normal
  state are never used to infer another stage's status.
* Only ``normal`` and ``raid`` modes are in scan scope.  Every other mode is
  reported in ``unsupported_keys`` and forces ``complete`` to stay false: a
  scan-only pass cannot prove an unsupported difficulty.
* A progress record confirms a catalogue key only when all of the following
  hold: the record exists under that exact catalogue key, its ``code`` and
  ``mode`` equal the catalogue stage's values, its state is one of
  ``cleared``/``uncleared``/``partial``/``locked``, and both ``evidence`` and
  ``at`` are non-empty strings.  Absence, counts, an ``unseen`` state or a
  cleared parent stage are never treated as confirmation.
* ``confirmed`` therefore means "observed with evidence", not "cleared".
  ``pending`` is the observed subset in ``uncleared``/``partial``.
* ``complete`` is true only when at least one scan-scope key exists, every
  scan-scope key is confirmed, and no unsupported-mode keys exist.  A group
  whose stages are all confirmed while still uncleared is complete for
  scan-only purposes; it does not need a battle.

Planning
--------
``plan_groups`` plans scan work, not combat.  A group needs work when it has
at least one normal/raid key that is not confirmed.  Groups whose scan-scope
keys are all confirmed are skipped even if some keys are merely
``uncleared``/``partial`` (scan-only does not fight) and even if the group
contains unsupported modes (which a scan cannot confirm and can never
resolve).  Unsupported keys remain visible through ``summarize_group`` and
``summarize_scan`` so callers cannot mistake such a group for fully complete.

``arrange_groups`` only reorders a plan: the interrupted group first, and
groups whose remaining keys a finished pass already failed to observe (or
whose entry could not be reached) last.  It never drops or confirms a key.
"""

__all__ = ["plan_groups", "arrange_groups", "stalled_group", "summarize_group", "summarize_scan"]

SCAN_MODES = ("normal", "raid")
CONFIRMED_STATES = ("cleared", "uncleared", "partial", "locked")
PENDING_STATES = ("uncleared", "partial")


def _as_dict(value):
    return value if isinstance(value, dict) else {}


def _sort_keys(keys):
    # str() makes the ordering deterministic for malformed, non-string keys.
    return sorted(keys, key=lambda key: str(key))


def _stage_map(book):
    return _as_dict(_as_dict(book).get("stages"))


def _group_parts(book, group):
    """Return ([(key, stage), ...], sorted_unsupported_keys) for one group."""
    expected = []
    unsupported = []
    for key, stage in _stage_map(book).items():
        if not isinstance(stage, dict):
            continue
        if stage.get("group") != group:
            continue
        if stage.get("mode") in SCAN_MODES:
            expected.append((key, stage))
        else:
            unsupported.append(key)
    return expected, _sort_keys(unsupported)


def _is_confirmed(record, stage):
    if not isinstance(record, dict):
        return False
    if record.get("code") != stage.get("code"):
        return False
    if record.get("mode") != stage.get("mode"):
        return False
    if record.get("state") not in CONFIRMED_STATES:
        return False
    evidence = record.get("evidence")
    if not isinstance(evidence, str) or not evidence.strip():
        return False
    at = record.get("at")
    if not isinstance(at, str) or not at.strip():
        return False
    return True


def _profile_names(book):
    """Ordered profile names, preserving catalogue insertion order."""
    profiles = _as_dict(book).get("profiles")
    names = []
    if isinstance(profiles, dict):
        source = profiles.keys()
    elif isinstance(profiles, (list, tuple)):
        source = profiles
    else:
        source = ()
    for item in source:
        if isinstance(item, str):
            name = item
        elif isinstance(item, dict):
            name = None
            for field in ("name", "group", "id"):
                if isinstance(item.get(field), str):
                    name = item[field]
                    break
            if name is None:
                continue
        else:
            continue
        if name not in names:
            names.append(name)
    return names


def _summary(book, progress, group):
    expected, unsupported = _group_parts(book, group)
    progress = _as_dict(progress)
    confirmed_keys = []
    cleared_keys = []
    pending_keys = []
    locked_keys = []
    missing_keys = []
    for key, stage in expected:
        record = progress.get(key)
        if not _is_confirmed(record, stage):
            missing_keys.append(key)
            continue
        confirmed_keys.append(key)
        state = record.get("state")
        if state == "cleared":
            cleared_keys.append(key)
        elif state in PENDING_STATES:
            pending_keys.append(key)
        elif state == "locked":
            locked_keys.append(key)
    total = len(expected)
    return {
        "group": group,
        "total": total,
        "confirmed": len(confirmed_keys),
        "cleared": len(cleared_keys),
        "pending": len(pending_keys),
        "missing_keys": _sort_keys(missing_keys),
        "pending_keys": _sort_keys(pending_keys),
        "locked_keys": _sort_keys(locked_keys),
        "unsupported_keys": unsupported,
        "complete": bool(total) and not missing_keys and not unsupported,
    }


def summarize_group(book, progress, group):
    """Summarize scan coverage for one catalogue group.

    Unknown/empty groups return ``total == 0``, empty key lists and
    ``complete is False``.  Inputs are never modified.
    """
    return _summary(book, progress, group)


def summarize_scan(book, progress):
    """Summarize scan coverage across every known catalogue stage key.

    Only ``book["stages"]`` contributes to counts.  Stale progress entries for
    keys not present in the catalogue are ignored.  ``unseen`` is every known
    scan-scope key that lacks a valid confirmed record (absent, ``unseen`` or
    malformed progress all count as unseen).
    """
    stages = _stage_map(book)
    group_order = []
    for group in _profile_names(book):
        if group not in group_order:
            group_order.append(group)
    for stage in stages.values():
        if not isinstance(stage, dict):
            continue
        group = stage.get("group")
        if group not in group_order:
            group_order.append(group)

    groups = {}
    for group in group_order:
        groups[group] = _summary(book, progress, group)

    total = sum(summary["total"] for summary in groups.values())
    confirmed = sum(summary["confirmed"] for summary in groups.values())
    cleared = sum(summary["cleared"] for summary in groups.values())
    pending = sum(summary["pending"] for summary in groups.values())
    unsupported = sum(len(summary["unsupported_keys"]) for summary in groups.values())
    return {
        "total": total,
        "confirmed": confirmed,
        "cleared": cleared,
        "pending": pending,
        "unseen": total - confirmed,
        "unsupported": unsupported,
        "complete": bool(total) and confirmed == total and unsupported == 0,
        "groups": groups,
    }


def _group_needs_scan(book, progress, group):
    expected, _unsupported = _group_parts(book, group)
    if not expected:
        return False
    progress = _as_dict(progress)
    return any(
        not _is_confirmed(progress.get(key), stage) for key, stage in expected
    )


def plan_groups(book, progress, previous=None):
    """Return existing profile names that still have scan work, in order.

    Catalogue profile order is retained, except that a still-incomplete
    ``previous["active_group"]`` is moved to the front.  ``previous`` may be
    missing, empty, malformed or refer to a removed group; such a value is
    ignored safely.  Old reports inside ``previous`` are never used to skip a
    group.
    """
    planned = [
        name
        for name in _profile_names(book)
        if _group_needs_scan(book, progress, name)
    ]
    active = previous.get("active_group") if isinstance(previous, dict) else None
    if active in planned:
        planned.remove(active)
        planned.insert(0, active)
    return planned


def _stall_reason(progress, key, stage):
    """Why a finished pass could not confirm ``key``; None if nothing is known.

    ``unseen`` is written only after a group's map pass completed without
    finding the node.  A raid node is only opened after its normal node is
    confirmed ``cleared``, so a confirmed normal node in any other state keeps
    the raid node unobservable.  Neither reason is ever a confirmation.
    """
    record = progress.get(key)
    if (isinstance(record, dict) and record.get("state") == "unseen"
            and record.get("code") == stage.get("code")
            and record.get("mode") == stage.get("mode")):
        return "unseen"
    if stage.get("mode") == "raid":
        normal = {"code": stage.get("code"), "mode": "normal"}
        row = progress.get(f"{stage.get('code')}:normal")
        if _is_confirmed(row, normal) and row.get("state") != "cleared":
            return "raid_locked"
        if isinstance(row, dict) and row.get("state") == "unseen" and row.get("code") == stage.get("code"):
            return "unseen"
    return None


def stalled_group(book, progress, group):
    """True when every unconfirmed scan-scope key has a known stall reason.

    Such a group cannot gain anything from being scanned first: the previous
    finished pass already failed to observe what remains.
    """
    expected, _unsupported = _group_parts(book, group)
    progress = _as_dict(progress)
    missing = [(key, stage) for key, stage in expected
               if not _is_confirmed(progress.get(key), stage)]
    return bool(missing) and all(_stall_reason(progress, key, stage) for key, stage in missing)


def arrange_groups(groups, book, progress, previous=None, failed=()):
    """Final scan order: resume first, groups without new evidence last.

    ``groups`` is an already planned (and possibly index-ordered) list.  A group
    is deferred when it is in ``failed`` (its entry could not be reached) or
    when :func:`stalled_group` holds.  The interrupted ``active_group`` moves to
    the front only when it is not deferred.  Nothing is dropped and relative
    order within each tier is kept.  Returns ``(ordered, deferred)``.
    """
    groups = list(groups)
    failed = {name for name in failed if isinstance(name, str)}
    deferred = [g for g in groups if g in failed or stalled_group(book, progress, g)]
    held = set(deferred)
    fresh = [g for g in groups if g not in held]
    active = previous.get("active_group") if isinstance(previous, dict) else None
    if active in fresh:
        fresh.remove(active)
        fresh.insert(0, active)
    return fresh + deferred, deferred
