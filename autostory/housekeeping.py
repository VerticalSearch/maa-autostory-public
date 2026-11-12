"""Bounded disk use for diagnostic output (0.9.28).

Two independent, best-effort jobs; neither ever touches account records:

* ``rotate``: ``core-events.ndjson`` -> ``.1`` -> ``.2`` -> ``.3`` once it
  passes a size limit. It runs at worker start under the task lock, before
  this process creates the MAA core that appends to the file.
* ``prune_screens``: evidence screenshots are deleted oldest-first until the
  folder fits its budget. Files referenced by any runtime JSON record or a
  project report, recent files (a run may still be writing them) and the
  regression fixtures are never deleted.

Both are called at worker start, before the device is touched. Failure is
logged and never stops a task.
"""
from __future__ import annotations

import os
from pathlib import Path
import re
import time

from .common import DATA, log, read_json, settings

CORE_EVENTS_LIMIT = 20 * 1024 * 1024
CORE_EVENTS_KEEP = 3
SCREENS_BUDGET_MB = 1024
# Files touched this recently may belong to a running or just-interrupted
# attempt whose ledger entry is not written yet.
RECENT_SECONDS = 6 * 3600

# Replay fixtures read by the regression suite and the package self-check.
FIXTURES = ('CW-complete-84.png', 'DV-1-detail.png', 'inspect-0-5.png', '0-5-detail.png',
            '17-11-detail.png', 'scan-start092', 'state-timeouts',
            'operator-details/1793377917275644400-char_2025_shu')


def rotate(path, limit=CORE_EVENTS_LIMIT, keep=CORE_EVENTS_KEEP):
    """Rotate ``path`` when larger than ``limit``. Returns True if rotated."""
    path = Path(path)
    try:
        if path.stat().st_size <= limit:
            return False
    except FileNotFoundError:
        return False
    oldest = path.with_name(f'{path.name}.{keep}')
    oldest.unlink(missing_ok=True)
    for index in range(keep - 1, 0, -1):
        source = path.with_name(f'{path.name}.{index}')
        if source.exists():
            os.replace(source, path.with_name(f'{path.name}.{index + 1}'))
    os.replace(path, path.with_name(f'{path.name}.1'))
    return True


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _strings(item)


def _key(path):
    return os.path.normcase(os.path.normpath(str(path)))


def referenced(screens, data=DATA, ledgers=None):
    """Normalised paths inside ``screens`` that a runtime JSON record mentions.

    ``ledgers=None`` scans every top-level ``runtime/*.json`` (progress,
    attempt history, campaign results and the dated live-check reports).
    """
    screens = Path(screens).resolve()
    marker = _key(screens)
    found = set()
    sources = sorted(Path(data).glob('*.json')) if ledgers is None else [Path(data) / name for name in ledgers]
    for source in sources:
        try:
            document = read_json(source)
        except (OSError, ValueError):
            continue
        for text in _strings(document):
            if 'screens' not in text:
                continue
            try:
                candidate = Path(text)
                if not candidate.is_absolute():
                    candidate = Path(data).parent / candidate
                normal = _key(candidate)
            except (TypeError, ValueError):
                continue
            if normal == marker or normal.startswith(marker + os.sep):
                found.add(normal)
    return found


_DOC_REFERENCE = re.compile(r'screens[\\/]([A-Za-z0-9_.\-\\/]+)')


def documented(screens, root):
    """Screenshots cited by the project's Markdown reports.

    A bare category folder (``screens/settlements/``) is a generic mention and
    protects nothing; a named file or a specific evidence folder is kept.
    """
    screens = Path(screens)
    found = set()
    for doc in Path(root).glob('*.md'):
        try:
            text = doc.read_text('utf-8', 'replace')
        except OSError:
            continue
        for match in _DOC_REFERENCE.finditer(text):
            raw = match.group(1).rstrip('.,;:')
            relative = raw.rstrip('/\\')
            parts = [part for part in re.split(r'[\\/]', relative) if part]
            if not parts or (len(parts) == 1 and raw != relative):
                continue
            found.add(_key(screens.joinpath(*parts)))
    return found


def prune_screens(screens=None, budget_mb=None, data=DATA, now=None, recent=RECENT_SECONDS,
                  fixtures=FIXTURES, ledgers=None, docs=None, dry_run=False):
    """Delete the oldest unprotected screenshots until under budget."""
    screens = Path(screens or Path(data) / 'screens').resolve()
    if budget_mb is None:
        try:
            budget_mb = float(settings().get('screens_budget_mb', SCREENS_BUDGET_MB))
        except (TypeError, ValueError):
            budget_mb = SCREENS_BUDGET_MB
    report = {'budget_mb': budget_mb, 'before_mb': 0.0, 'after_mb': 0.0, 'deleted': 0, 'protected': 0}
    if not screens.is_dir() or budget_mb is None or budget_mb <= 0:
        return report
    now = time.time() if now is None else now
    keep = referenced(screens, data, ledgers)
    keep |= {_key(screens / item) for item in fixtures}
    keep |= documented(screens, Path(data).parent if docs is None else docs)
    root = _key(screens)

    def protected(normal):
        # A protected entry may be the file itself or any enclosing folder.
        while len(normal) > len(root):
            if normal in keep:
                return True
            normal = os.path.dirname(normal)
        return False

    files = []
    total = 0
    for path in screens.rglob('*'):
        try:
            if not path.is_file():
                continue
            stat = path.stat()
        except OSError:
            continue
        total += stat.st_size
        if now - stat.st_mtime < recent or protected(_key(path)):
            report['protected'] += 1
        else:
            files.append((stat.st_mtime, stat.st_size, path))
    budget = budget_mb * 1024 * 1024
    report['before_mb'] = round(total / 1048576, 1)
    folders = set()
    for _, size, path in sorted(files, key=lambda row: row[0]):
        if total <= budget:
            break
        if not dry_run:
            try:
                path.unlink()
            except OSError:
                continue
        total -= size
        report['deleted'] += 1
        folders.add(path.parent)
    for folder in ([] if dry_run else sorted(folders, key=lambda item: len(item.parts), reverse=True)):
        while folder != screens and screens in folder.parents:
            try:
                folder.rmdir()
            except OSError:
                break
            folder = folder.parent
    report['after_mb'] = round(total / 1048576, 1)
    return report


def run(data=DATA):
    """Best-effort housekeeping at worker start. Never raises."""
    result = {}
    try:
        result['core_events_rotated'] = rotate(Path(data) / 'core-events.ndjson', CORE_EVENTS_LIMIT, CORE_EVENTS_KEEP)
    except OSError as error:
        result['core_events_error'] = str(error)
    try:
        result['screens'] = prune_screens(data=data)
        if result['screens'].get('deleted'):
            screens = result['screens']
            log(f'截图证据目录超出 {screens["budget_mb"]:.0f} MB 上限：已删除最旧的 {screens["deleted"]} 个未被记录引用的文件'
                f'（{screens["before_mb"]} MB → {screens["after_mb"]} MB）')
    except Exception as error:  # housekeeping must never block a task
        result['screens_error'] = str(error)
    if result.get('core_events_rotated'):
        log('core-events.ndjson 超过 20 MB，已轮换为 .1（保留最近 3 份）')
    return result
