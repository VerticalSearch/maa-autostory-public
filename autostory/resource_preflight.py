"""Bounded, local-only resource self-repair for AutoStory startup.

This module is intentionally standalone (standard library only) so the worker
can import it before ``Asst.load`` without creating an import cycle with
``autostory.common``.

The only trusted source of repair bytes is a local OTA ZIP whose entry
``MaaCore.dll`` *and* entry ``resource/tasks/tasks.json`` are byte-identical
(by SHA-256) to the installed files.  A filename or a version string is never
used as evidence.  If more than one distinct archive satisfies both hashes the
archive is ambiguous and no file is touched.

Repair policy:

* copy archive entries under ``resource/`` that are missing locally;
* replace ``resource/tasks/UiTheme/**`` and ``resource/template/UiTheme/**``
  entries whose bytes differ from the same archive;
* leave every other mismatch untouched, even if the archive has a newer copy;
* never download, never touch ``userdata`` or settings, never extractall.

Every overwrite is backed up with its prior SHA-256, staged in the destination
directory, and installed with ``os.replace``.  A failure or stop during the
transaction rolls back all completed mutations; rollback errors are reported,
not hidden.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import stat
import tempfile
import time
import unicodedata
import uuid
import zipfile
import zlib
from pathlib import Path
from typing import Any, Callable

__all__ = [
    "ResourceIntegrityError",
    "ResourcePreflightError",
    "ensure_resources",
]

SCHEMA = "autostory-resource-preflight/1"
AUDIT_DIRNAME = "resource-preflight"
CORE_ENTRY = "MaaCore.dll"
TASKS_ENTRY = "resource/tasks/tasks.json"
RESOURCE_PREFIX = "resource/"
THEME_TASK_PREFIX = "resource/tasks/UiTheme/"
THEME_TEMPLATE_PREFIX = "resource/template/UiTheme/"

# Hard bounds.  These are deliberately generous for the real MAA OTA archive
# (about 27 MB compressed / 64 MB expanded) and still reject zip bombs.
MAX_ARCHIVE_FILES = 64
MAX_ARCHIVE_BYTES = 512 * 1024 * 1024
MAX_ENTRIES = 20_000
MAX_ENTRY_BYTES = 128 * 1024 * 1024
MAX_TOTAL_UNCOMPRESSED = 1024 * 1024 * 1024
MAX_COMPRESSION_RATIO = 1000
RATIO_MIN_SIZE = 1024 * 1024
MAX_INSTALLED_BYTES = 128 * 1024 * 1024
MAX_TASKS_BYTES = 64 * 1024 * 1024

_CHUNK = 1024 * 1024
_FILE_ATTRIBUTE_REPARSE_POINT = 0x400
_RESERVED_WINDOWS_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}
_INVALID_WINDOWS_CHARS = set('<>:"|?*')


class ResourcePreflightError(RuntimeError):
    """The preflight could not run safely; the caller must not assume health."""


class ResourceIntegrityError(ResourcePreflightError):
    """Installed resources cannot be identified or verified safely."""


class _ArchiveInvalid(Exception):
    """A candidate ZIP failed a structural/safety/CRC check; never extracted."""


class _TooManyArchives(Exception):
    pass


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="milliseconds")


def _new_attempt(maa_root: Path, audit_root: Path) -> dict:
    attempt_id = (
        _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        + "-"
        + str(os.getpid())
        + "-"
        + uuid.uuid4().hex[:8]
    )
    return {
        "schema": SCHEMA,
        "attempt_id": attempt_id,
        "at": _now_iso(),
        "finished_at": None,
        "maa_root": str(maa_root),
        "audit_root": str(audit_root),
        "status": "running",
        "reason": None,
        "ok": False,
        "source_verified": False,
        "verified_healthy": False,
        "archive": None,
        "archive_sha256": None,
        "identity": None,
        "candidates": [],
        "archive_validation": None,
        "missing_before": [],
        "copied_missing": [],
        "replaced_theme": [],
        "untouched_mismatches": [],
        "remaining_mismatches": [],
        "mutations": [],
        "writes": 0,
        "net_writes": 0,
        "backup_dir": None,
        "final_hashes": {},
        "rollback": None,
        "error": None,
        "warnings": [],
        "audit": {},
    }


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _is_reparse(stat_result: os.stat_result) -> bool:
    """True for symlinks and Windows reparse points (including junctions)."""
    if stat.S_ISLNK(stat_result.st_mode):
        return True
    attributes = getattr(stat_result, "st_file_attributes", 0)
    return bool(attributes & _FILE_ATTRIBUTE_REPARSE_POINT)


def _lstat(path: Path) -> os.stat_result | None:
    try:
        return os.lstat(path)
    except FileNotFoundError:
        return None


def _read_bytes_bounded(path: Path, limit: int, label: str) -> bytes:
    stat_result = _lstat(path)
    if stat_result is None:
        raise ResourceIntegrityError(f"{label} is missing: {path}")
    if _is_reparse(stat_result):
        raise ResourceIntegrityError(f"{label} is a link/reparse point: {path}")
    if not stat.S_ISREG(stat_result.st_mode):
        raise ResourceIntegrityError(f"{label} is not a regular file: {path}")
    if stat_result.st_size > limit:
        raise ResourceIntegrityError(
            f"{label} is {stat_result.st_size} bytes, above the {limit}-byte limit: {path}"
        )
    try:
        with path.open("rb") as stream:
            data = stream.read(limit + 1)
    except OSError as exc:
        raise ResourceIntegrityError(f"{label} cannot be read: {path}: {exc}") from exc
    if len(data) > limit:
        raise ResourceIntegrityError(f"{label} grew past the {limit}-byte limit: {path}")
    return data


def _sha256_file_bounded(path: Path, limit: int, label: str) -> tuple[str, int]:
    stat_result = _lstat(path)
    if stat_result is None:
        raise ResourceIntegrityError(f"{label} is missing: {path}")
    if _is_reparse(stat_result):
        raise ResourceIntegrityError(f"{label} is a link/reparse point: {path}")
    if not stat.S_ISREG(stat_result.st_mode):
        raise ResourceIntegrityError(f"{label} is not a regular file: {path}")
    if stat_result.st_size > limit:
        raise ResourceIntegrityError(
            f"{label} is {stat_result.st_size} bytes, above the {limit}-byte limit: {path}"
        )
    digest = hashlib.sha256()
    size = 0
    try:
        with path.open("rb") as stream:
            while True:
                chunk = stream.read(_CHUNK)
                if not chunk:
                    break
                size += len(chunk)
                if size > limit:
                    raise ResourceIntegrityError(
                        f"{label} grew past the {limit}-byte limit: {path}"
                    )
                digest.update(chunk)
    except ResourceIntegrityError:
        raise
    except OSError as exc:
        raise ResourceIntegrityError(f"{label} cannot be read: {path}: {exc}") from exc
    return digest.hexdigest(), size


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = -1
    temporary: Path | None = None
    try:
        fd, raw = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
        )
        temporary = Path(raw)
        with os.fdopen(fd, "wb") as stream:
            fd = -1
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        _replace_with_retry(temporary, path)
        temporary = None
    finally:
        if fd >= 0:
            try:
                os.close(fd)
            except OSError:
                pass
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def _replace_with_retry(source: Path, destination: Path) -> None:
    for attempt in range(6):
        try:
            os.replace(source, destination)
            return
        except PermissionError as exc:
            if attempt == 5 or getattr(exc, "winerror", None) not in (5, 32, 33):
                raise
            time.sleep(0.05 * (attempt + 1))


def _json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    ).encode("utf-8")


def _path_is_within(path: Path, root: Path) -> bool:
    """Fail-closed containment check using resolved paths."""
    try:
        resolved_path = Path(os.path.realpath(path))
        resolved_root = Path(os.path.realpath(root))
    except Exception:  # noqa: BLE001 - containment must fail closed
        return True
    path_drive = os.path.normcase(os.path.splitdrive(str(resolved_path))[0])
    root_drive = os.path.normcase(os.path.splitdrive(str(resolved_root))[0])
    if path_drive != root_drive:
        return False
    try:
        common = Path(os.path.commonpath([str(resolved_path), str(resolved_root)]))
    except ValueError:
        return True
    return os.path.normcase(str(common)) == os.path.normcase(str(resolved_root))


def _finalize_audit(attempt: dict) -> None:
    """Write the per-attempt report and ``latest.json``.  Never hides failures."""
    attempt["finished_at"] = _now_iso()
    audit_dir = Path(attempt["audit_root"]) / AUDIT_DIRNAME
    attempt_path = audit_dir / f"{attempt['attempt_id']}.json"
    latest_path = audit_dir / "latest.json"
    attempt["audit"] = {
        "attempt_path": str(attempt_path),
        "latest_path": str(latest_path),
        "error": None,
    }
    # A caller error must not turn the audit writer into a resource mutation.
    if _path_is_within(Path(attempt["audit_root"]), Path(attempt["maa_root"]) / "resource"):
        attempt["audit"]["error"] = (
            "refusing to write audit inside the resource tree"
        )
        attempt["ok"] = False
        return
    try:
        payload = _json_bytes(attempt)
        _atomic_write_bytes(attempt_path, payload)
        _atomic_write_bytes(latest_path, payload)
    except BaseException as exc:  # noqa: BLE001 - audit must not mask the real error
        attempt["audit"]["error"] = f"{type(exc).__name__}: {exc}"
        attempt["ok"] = False


def _validate_roots(attempt: dict) -> tuple[Path, Path, Path]:
    maa_root = Path(attempt["maa_root"])
    audit_root = Path(attempt["audit_root"])
    resource_root = maa_root / "resource"

    # Refuse an audit location inside the resource tree *before* checking that
    # the tree exists: otherwise a missing tree could be created by the audit
    # writer itself.  _path_is_within() fails closed on resolution errors.
    if _path_is_within(audit_root, resource_root):
        raise ResourcePreflightError(
            f"audit_root must not be inside the resource tree: {audit_root}"
        )

    maa_stat = _lstat(maa_root)
    if maa_stat is None or not stat.S_ISDIR(maa_stat.st_mode):
        raise ResourceIntegrityError(f"MAA root is not a directory: {maa_root}")
    if _is_reparse(maa_stat):
        raise ResourceIntegrityError(f"MAA root is a link/reparse point: {maa_root}")

    resource_stat = _lstat(resource_root)
    if resource_stat is None:
        raise ResourceIntegrityError(f"installed resource directory is missing: {resource_root}")
    if _is_reparse(resource_stat):
        raise ResourceIntegrityError(
            f"installed resource directory is a link/reparse point: {resource_root}"
        )
    if not stat.S_ISDIR(resource_stat.st_mode):
        raise ResourceIntegrityError(f"installed resource path is not a directory: {resource_root}")

    attempt["resource_root"] = str(resource_root)
    return maa_root, audit_root, resource_root


def _read_installed(attempt: dict, maa_root: Path, resource_root: Path) -> dict:
    core_path = maa_root / CORE_ENTRY
    tasks_path = resource_root / "tasks" / "tasks.json"
    identity = {
        "method": "sha256(MaaCore.dll)+sha256(resource/tasks/tasks.json)",
        "installed": {},
        "archive": {},
        "matched": False,
    }
    attempt["identity"] = identity

    core_sha, core_size = _sha256_file_bounded(
        core_path, MAX_INSTALLED_BYTES, "installed MaaCore.dll"
    )
    if core_size == 0:
        raise ResourceIntegrityError(f"installed MaaCore.dll is empty: {core_path}")
    identity["installed"][CORE_ENTRY] = {
        "path": str(core_path),
        "size": core_size,
        "sha256": core_sha,
    }

    tasks_data = _read_bytes_bounded(tasks_path, MAX_TASKS_BYTES, "installed tasks.json")
    tasks_sha = _sha256_bytes(tasks_data)
    try:
        tasks_document = json.loads(tasks_data.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ResourceIntegrityError(
            f"installed main task file is not valid UTF-8 JSON: {tasks_path}: {exc}"
        ) from exc
    if not isinstance(tasks_document, dict) or not tasks_document:
        raise ResourceIntegrityError(
            f"installed main task file is malformed (expected a non-empty object): {tasks_path}"
        )
    identity["installed"][TASKS_ENTRY] = {
        "path": str(tasks_path),
        "size": len(tasks_data),
        "sha256": tasks_sha,
        "task_count": len(tasks_document),
    }
    return {"core_sha256": core_sha, "core_size": core_size,
            "tasks_sha256": tasks_sha, "tasks_size": len(tasks_data)}


def _validate_archive_name(name: str, is_dir: bool) -> None:
    if not name or len(name) > 1024:
        raise _ArchiveInvalid(f"invalid entry name length: {name!r}")
    if "\x00" in name:
        raise _ArchiveInvalid(f"NUL in entry name: {name!r}")
    if "\\" in name:
        raise _ArchiveInvalid(f"backslash in ZIP entry name: {name!r}")
    if name.startswith("/") or name.startswith("//"):
        raise _ArchiveInvalid(f"absolute ZIP entry name: {name!r}")
    if len(name) >= 2 and name[1] == ":":
        raise _ArchiveInvalid(f"drive-qualified ZIP entry name: {name!r}")
    check = name[:-1] if is_dir and name.endswith("/") else name
    if not check:
        raise _ArchiveInvalid(f"empty entry name: {name!r}")
    if (name.endswith("/") and not is_dir) or (is_dir and not name.endswith("/")):
        raise _ArchiveInvalid(f"inconsistent directory entry name: {name!r}")
    parts = check.split("/")
    if any(part == "" for part in parts):
        raise _ArchiveInvalid(f"empty path segment in ZIP entry: {name!r}")
    if len(parts) > 128:
        raise _ArchiveInvalid(f"too many path segments in ZIP entry: {name!r}")
    for part in parts:
        if part in (".", ".."):
            raise _ArchiveInvalid(f"dot segment in ZIP entry: {name!r}")
        if part != part.rstrip(" ."):
            raise _ArchiveInvalid(f"trailing dot/space in ZIP entry: {name!r}")
        if any(ord(ch) < 32 for ch in part):
            raise _ArchiveInvalid(f"control character in ZIP entry: {name!r}")
        if any(ch in _INVALID_WINDOWS_CHARS for ch in part):
            raise _ArchiveInvalid(f"invalid Windows character in ZIP entry: {name!r}")
        base = part.split(".", 1)[0].rstrip(" .").upper()
        if base in _RESERVED_WINDOWS_NAMES:
            raise _ArchiveInvalid(f"reserved Windows name in ZIP entry: {name!r}")


def _inspect_archive_structure(path: Path) -> dict:
    try:
        archive_size = path.stat().st_size
    except OSError as exc:
        raise _ArchiveInvalid(f"cannot stat archive: {exc}") from exc
    if archive_size <= 0 or archive_size > MAX_ARCHIVE_BYTES:
        raise _ArchiveInvalid(
            f"archive size {archive_size} is outside 1..{MAX_ARCHIVE_BYTES}"
        )
    try:
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
            if len(infos) > MAX_ENTRIES:
                raise _ArchiveInvalid(f"too many ZIP entries: {len(infos)}")
            entries: dict[str, zipfile.ZipInfo] = {}
            case_keys: dict[str, str] = {}
            total_uncompressed = 0
            file_names: set[str] = set()
            directory_names: set[str] = set()
            for info in infos:
                name = info.filename
                is_dir = name.endswith("/")
                _validate_archive_name(name, is_dir)
                if name in entries:
                    raise _ArchiveInvalid(f"duplicate ZIP path: {name!r}")
                key = unicodedata.normalize("NFC", name).casefold()
                previous = case_keys.get(key)
                if previous is not None:
                    raise _ArchiveInvalid(
                        f"case/Unicode-colliding ZIP paths: {previous!r} and {name!r}"
                    )
                case_keys[key] = name
                entries[name] = info

                if info.flag_bits & 0x1:
                    raise _ArchiveInvalid(f"encrypted ZIP entry: {name!r}")
                if info.file_size < 0 or info.compress_size < 0:
                    raise _ArchiveInvalid(f"negative ZIP size: {name!r}")
                if info.file_size > MAX_ENTRY_BYTES:
                    raise _ArchiveInvalid(
                        f"ZIP entry too large ({info.file_size}): {name!r}"
                    )
                total_uncompressed += info.file_size
                if total_uncompressed > MAX_TOTAL_UNCOMPRESSED:
                    raise _ArchiveInvalid(
                        f"ZIP expands past {MAX_TOTAL_UNCOMPRESSED} bytes"
                    )
                if (
                    info.file_size > RATIO_MIN_SIZE
                    and info.file_size
                    > MAX_COMPRESSION_RATIO * max(info.compress_size, 1)
                ):
                    raise _ArchiveInvalid(
                        f"suspicious compression ratio for {name!r}: "
                        f"{info.file_size}/{info.compress_size}"
                    )

                mode_type = (info.external_attr >> 16) & 0o170000
                if mode_type == 0o120000:
                    raise _ArchiveInvalid(f"symlink ZIP entry: {name!r}")
                if mode_type not in (0, 0o100000, 0o040000):
                    raise _ArchiveInvalid(
                        f"non-regular ZIP entry mode {oct(mode_type)}: {name!r}"
                    )
                if is_dir:
                    if info.file_size != 0:
                        raise _ArchiveInvalid(f"directory entry has data: {name!r}")
                    if mode_type == 0o100000:
                        raise _ArchiveInvalid(f"file mode on directory entry: {name!r}")
                    directory_names.add(name[:-1])
                else:
                    if mode_type == 0o040000:
                        raise _ArchiveInvalid(f"directory mode on file entry: {name!r}")
                    file_names.add(name)

            # A file entry must not also be an ancestor of another entry, and
            # a file must not collide with a directory of the same name.
            for name in file_names:
                parts = name.split("/")
                for index in range(1, len(parts)):
                    ancestor = "/".join(parts[:index])
                    if ancestor in file_names:
                        raise _ArchiveInvalid(
                            f"file/directory ZIP path conflict: {ancestor!r} / {name!r}"
                        )
            file_case = {
                unicodedata.normalize("NFC", name).casefold()
                for name in file_names
            }
            for name in directory_names:
                if unicodedata.normalize("NFC", name).casefold() in file_case:
                    raise _ArchiveInvalid(
                        f"file/directory ZIP path conflict: {name!r}"
                    )
                parts = name.split("/")
                for index in range(1, len(parts)):
                    ancestor = "/".join(parts[:index])
                    if ancestor in file_names:
                        raise _ArchiveInvalid(
                            f"file/directory ZIP path conflict: {ancestor!r} / {name!r}/"
                        )

            resource_infos = [
                entries[name]
                for name in sorted(entries)
                if name.startswith(RESOURCE_PREFIX) and not name.endswith("/")
            ]
    except _ArchiveInvalid:
        raise
    except (zipfile.BadZipFile, zipfile.LargeZipFile, OSError, RuntimeError, ValueError,
            EOFError) as exc:
        raise _ArchiveInvalid(f"unreadable ZIP: {type(exc).__name__}: {exc}") from exc

    return {
        "path": path,
        "size": archive_size,
        "entries": entries,
        "resource_infos": resource_infos,
        "entry_count": len(infos),
        "resource_count": len(resource_infos),
    }


def _read_zip_entry(path: Path, name: str, limit: int) -> tuple[str, int]:
    try:
        with zipfile.ZipFile(path) as archive:
            info = archive.getinfo(name)
            if info.is_dir():
                raise _ArchiveInvalid(f"identity entry is a directory: {name!r}")
            digest = hashlib.sha256()
            crc = 0
            size = 0
            with archive.open(info, "r") as stream:
                while True:
                    chunk = stream.read(_CHUNK)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > limit:
                        raise _ArchiveInvalid(f"entry {name!r} exceeds {limit} bytes")
                    digest.update(chunk)
                    crc = zlib.crc32(chunk, crc)
            if size != info.file_size:
                raise _ArchiveInvalid(
                    f"short read for {name!r}: {size} != {info.file_size}"
                )
            if (crc & 0xFFFFFFFF) != (info.CRC & 0xFFFFFFFF):
                raise _ArchiveInvalid(f"CRC mismatch for {name!r}")
            return digest.hexdigest(), size
    except _ArchiveInvalid:
        raise
    except (zipfile.BadZipFile, RuntimeError, OSError, EOFError, zlib.error,
            ValueError) as exc:
        raise _ArchiveInvalid(f"cannot read {name!r}: {type(exc).__name__}: {exc}") from exc


def _full_scan_archive(path: Path) -> dict[str, str]:
    """CRC/size-check every entry and hash every resource file entry."""
    resource_shas: dict[str, str] = {}
    total_read = 0
    try:
        with zipfile.ZipFile(path) as archive:
            for info in archive.infolist():
                if info.is_dir():
                    if info.file_size != 0:
                        raise _ArchiveInvalid(
                            f"directory entry has data: {info.filename!r}"
                        )
                    continue
                digest = hashlib.sha256()
                crc = 0
                size = 0
                with archive.open(info, "r") as stream:
                    while True:
                        chunk = stream.read(_CHUNK)
                        if not chunk:
                            break
                        size += len(chunk)
                        total_read += len(chunk)
                        if size > MAX_ENTRY_BYTES:
                            raise _ArchiveInvalid(
                                f"entry {info.filename!r} exceeds {MAX_ENTRY_BYTES} bytes"
                            )
                        if total_read > MAX_TOTAL_UNCOMPRESSED:
                            raise _ArchiveInvalid(
                                f"archive expands past {MAX_TOTAL_UNCOMPRESSED} bytes"
                            )
                        digest.update(chunk)
                        crc = zlib.crc32(chunk, crc)
                if size != info.file_size:
                    raise _ArchiveInvalid(
                        f"size mismatch for {info.filename!r}: {size} != {info.file_size}"
                    )
                if (crc & 0xFFFFFFFF) != (info.CRC & 0xFFFFFFFF):
                    raise _ArchiveInvalid(f"CRC mismatch for {info.filename!r}")
                if info.filename.startswith(RESOURCE_PREFIX):
                    resource_shas[info.filename] = digest.hexdigest()
    except _ArchiveInvalid:
        raise
    except (zipfile.BadZipFile, RuntimeError, OSError, EOFError, zlib.error,
            ValueError) as exc:
        raise _ArchiveInvalid(f"archive scan failed: {type(exc).__name__}: {exc}") from exc
    return resource_shas


def _candidate_records(
    maa_root: Path, installed: dict
) -> tuple[list[dict], list[dict]]:
    try:
        archives = sorted(
            (
                child
                for child in maa_root.iterdir()
                if child.name.lower().endswith(".zip")
            ),
            key=lambda item: item.name.casefold(),
        )
    except OSError as exc:
        raise ResourceIntegrityError(f"cannot list MAA root for OTA archives: {exc}") from exc
    if len(archives) > MAX_ARCHIVE_FILES:
        raise _TooManyArchives(
            f"{len(archives)} ZIP files in MAA root; at most {MAX_ARCHIVE_FILES} are considered"
        )

    records: list[dict] = []
    matches: list[dict] = []
    for path in archives:
        record: dict[str, Any] = {
            "path": str(path),
            "size": None,
            "status": "candidate",
            "reason": None,
            "core_match": False,
            "tasks_match": False,
            "archive_sha256": None,
        }
        stat_result = _lstat(path)
        if stat_result is None:
            record.update(status="rejected", reason="file disappeared")
            records.append(record)
            continue
        record["size"] = stat_result.st_size
        if _is_reparse(stat_result) or not stat.S_ISREG(stat_result.st_mode):
            record.update(status="rejected", reason="not a regular archive file")
            records.append(record)
            continue
        try:
            structure = _inspect_archive_structure(path)
        except _ArchiveInvalid as exc:
            record.update(status="rejected", reason=str(exc))
            records.append(record)
            continue
        entries = structure["entries"]
        missing_identity = [
            name for name in (CORE_ENTRY, TASKS_ENTRY) if name not in entries
        ]
        if missing_identity:
            record.update(
                status="identity_mismatch",
                reason="missing archive entries: " + ", ".join(missing_identity),
            )
            records.append(record)
            continue
        try:
            core_sha, core_size = _read_zip_entry(
                path, CORE_ENTRY, MAX_INSTALLED_BYTES
            )
            tasks_sha, tasks_size = _read_zip_entry(
                path, TASKS_ENTRY, MAX_TASKS_BYTES
            )
        except _ArchiveInvalid as exc:
            record.update(status="rejected", reason=str(exc))
            records.append(record)
            continue
        record["core_sha256"] = core_sha
        record["tasks_sha256"] = tasks_sha
        record["core_size"] = core_size
        record["tasks_size"] = tasks_size
        core_match = (
            core_sha == installed["core_sha256"]
            and core_size == installed["core_size"]
        )
        tasks_match = (
            tasks_sha == installed["tasks_sha256"]
            and tasks_size == installed["tasks_size"]
        )
        record["core_match"] = core_match
        record["tasks_match"] = tasks_match
        if core_match and tasks_match:
            record["status"] = "match"
            match = {
                "path": path,
                "structure": structure,
                "record": record,
            }
            matches.append(match)
        else:
            reasons = []
            if not core_match:
                reasons.append("MaaCore.dll SHA256 differs from installed")
            if not tasks_match:
                reasons.append("resource/tasks/tasks.json SHA256 differs from installed")
            record.update(status="identity_mismatch", reason="; ".join(reasons))
        records.append(record)
    return records, matches


def _select_source(
    attempt: dict, installed: dict, matches: list[dict]
) -> dict | None:
    if not matches:
        attempt.update(status="skipped", reason="no_source", ok=False,
                       source_verified=False, verified_healthy=False)
        return None

    digest_groups: dict[str, list[dict]] = {}
    for match in matches:
        path = match["path"]
        try:
            archive_sha, _ = _sha256_file_bounded(
                path, MAX_ARCHIVE_BYTES, "OTA archive"
            )
        except ResourceIntegrityError as exc:
            match["record"].update(status="rejected", reason=str(exc))
            continue
        match["record"]["archive_sha256"] = archive_sha
        digest_groups.setdefault(archive_sha, []).append(match)

    usable = [group for group in digest_groups.values() if group]
    if not usable:
        attempt.update(status="skipped", reason="no_source", ok=False,
                       source_verified=False, verified_healthy=False)
        return None
    if len(usable) > 1:
        attempt.update(status="skipped", reason="ambiguous_source", ok=False,
                       source_verified=False, verified_healthy=False)
        attempt["ambiguous_matches"] = [
            {
                "path": str(match["path"]),
                "archive_sha256": match["record"].get("archive_sha256"),
            }
            for group in usable
            for match in group
        ]
        return None

    group = usable[0]
    selected = group[0]
    if len(group) > 1:
        attempt["warnings"].append(
            "identical matching OTA archives at multiple paths; using "
            + str(selected["path"])
        )
    record = selected["record"]
    attempt["archive"] = str(selected["path"])
    attempt["archive_sha256"] = record["archive_sha256"]
    attempt["identity"]["archive"] = {
        CORE_ENTRY: {
            "size": record["core_size"],
            "sha256": record["core_sha256"],
        },
        TASKS_ENTRY: {
            "size": record["tasks_size"],
            "sha256": record["tasks_sha256"],
        },
    }
    attempt["identity"]["matched"] = True
    attempt["source_verified"] = True
    return selected


def _target_for(resource_root: Path, name: str) -> Path:
    if not name.startswith(RESOURCE_PREFIX):
        raise ResourceIntegrityError(f"archive entry is outside resource/: {name!r}")
    parts = name.split("/")
    if not parts or parts[0] != "resource" or any(
        part in ("", ".", "..") for part in parts
    ):
        raise ResourceIntegrityError(f"unsafe archive entry: {name!r}")
    target = resource_root.joinpath(*parts[1:])
    try:
        relative = target.relative_to(resource_root)
    except ValueError as exc:
        raise ResourceIntegrityError(f"archive entry escapes resource/: {name!r}") from exc
    if any(part in ("", ".", "..") for part in relative.parts):
        raise ResourceIntegrityError(f"archive entry escapes resource/: {name!r}")
    return target


def _check_existing_parents(resource_root: Path, target: Path) -> None:
    relative = target.relative_to(resource_root)
    current = resource_root
    for part in relative.parts[:-1]:
        current = current / part
        stat_result = _lstat(current)
        if stat_result is None:
            return
        if _is_reparse(stat_result):
            raise ResourceIntegrityError(
                f"resource parent is a link/reparse point: {current}"
            )
        if not stat.S_ISDIR(stat_result.st_mode):
            raise ResourceIntegrityError(
                f"resource parent is not a directory: {current}"
            )


def _is_theme_entry(name: str) -> bool:
    return name.startswith(THEME_TASK_PREFIX) or name.startswith(THEME_TEMPLATE_PREFIX)


def _plan_repairs(
    attempt: dict,
    resource_root: Path,
    resource_infos: list[zipfile.ZipInfo],
    resource_shas: dict[str, str],
    installed: dict,
) -> tuple[list[dict], list[str], list[dict], dict[str, str | None]]:
    if resource_shas.get(TASKS_ENTRY) != installed["tasks_sha256"]:
        raise ResourceIntegrityError(
            "archive/installed main task hash mismatch after identity selection"
        )

    actions: list[dict] = []
    missing_before: list[str] = []
    untouched: list[dict] = []
    final_hashes: dict[str, str | None] = {}
    seen_targets: set[str] = set()

    for info in sorted(resource_infos, key=lambda item: item.filename):
        name = info.filename
        if name not in resource_shas:
            raise ResourceIntegrityError(f"archive entry was not CRC-verified: {name!r}")
        target = _target_for(resource_root, name)
        target_key = os.path.normcase(str(target))
        if target_key in seen_targets:
            raise ResourceIntegrityError(f"duplicate target path: {target}")
        seen_targets.add(target_key)
        _check_existing_parents(resource_root, target)
        stat_result = _lstat(target)
        source_sha = resource_shas[name]

        if stat_result is None:
            missing_before.append(name)
            # Actual pre-transaction state: the file is absent.  A successful
            # copy updates this to the source hash in _execute_actions().
            final_hashes[name] = None
            actions.append(
                {
                    "kind": "copied_missing",
                    "name": name,
                    "target": str(target),
                    "source_sha256": source_sha,
                    "size": info.file_size,
                    "prior_sha256": "absent",
                }
            )
            continue

        if _is_reparse(stat_result):
            raise ResourceIntegrityError(f"installed resource target is a link/reparse point: {target}")
        if not stat.S_ISREG(stat_result.st_mode):
            raise ResourceIntegrityError(f"installed resource target is not a regular file: {target}")

        try:
            current_sha, current_size = _sha256_file_bounded(
                target, MAX_ENTRY_BYTES, "installed resource"
            )
        except ResourceIntegrityError as exc:
            if _is_theme_entry(name):
                raise
            attempt["warnings"].append(f"cannot hash installed resource: {target}: {exc}")
            final_hashes[name] = None
            untouched.append(
                {
                    "path": name,
                    "archive_sha256": source_sha,
                    "installed_sha256": None,
                    "archive_size": info.file_size,
                    "installed_size": stat_result.st_size,
                    "reason": "not_in_theme_allowlist",
                    "action": "untouched",
                }
            )
            continue

        final_hashes[name] = current_sha
        if current_sha == source_sha:
            continue

        if _is_theme_entry(name):
            actions.append(
                {
                    "kind": "replaced_theme",
                    "name": name,
                    "target": str(target),
                    "source_sha256": source_sha,
                    "size": info.file_size,
                    "prior_sha256": current_sha,
                    "prior_size": current_size,
                }
            )
        else:
            untouched.append(
                {
                    "path": name,
                    "archive_sha256": source_sha,
                    "installed_sha256": current_sha,
                    "archive_size": info.file_size,
                    "installed_size": current_size,
                    "reason": "not_in_theme_allowlist",
                    "action": "untouched",
                }
            )

    return actions, missing_before, untouched, final_hashes


def _ensure_parent_dirs(
    parent: Path, resource_root: Path, created: list[Path]
) -> None:
    try:
        relative = parent.relative_to(resource_root)
    except ValueError as exc:
        raise ResourceIntegrityError(f"target parent escapes resource/: {parent}") from exc
    if any(part in ("", ".", "..") for part in relative.parts):
        raise ResourceIntegrityError(f"unsafe resource parent: {parent}")
    chain: list[Path] = []
    current = parent
    while True:
        stat_result = _lstat(current)
        if stat_result is not None:
            if _is_reparse(stat_result) or not stat.S_ISDIR(stat_result.st_mode):
                raise ResourceIntegrityError(
                    f"resource parent is not a safe directory: {current}"
                )
            break
        chain.append(current)
        if current == resource_root:
            break
        if current.parent == current:
            raise ResourceIntegrityError(f"cannot reach resource root from: {parent}")
        current = current.parent
    for directory in reversed(chain):
        stat_result = _lstat(directory)
        if stat_result is not None:
            continue
        directory.mkdir()
        created.append(directory)


def _backup_path(backup_root: Path, name: str) -> Path:
    parts = name.split("/")
    if len(parts) < 2 or parts[0] != "resource":
        raise ResourceIntegrityError(f"cannot map backup path for: {name!r}")
    if any(part in ("", ".", "..") for part in parts):
        raise ResourceIntegrityError(f"unsafe backup path for: {name!r}")
    return backup_root.joinpath(*parts[1:])


def _backup_target(target: Path, backup: Path, expected_sha: str) -> None:
    stat_result = _lstat(target)
    if stat_result is None:
        raise ResourceIntegrityError(f"backup source disappeared: {target}")
    if _is_reparse(stat_result) or not stat.S_ISREG(stat_result.st_mode):
        raise ResourceIntegrityError(f"backup source is not a regular file: {target}")
    digest = hashlib.sha256()
    size = 0
    backup.parent.mkdir(parents=True, exist_ok=True)
    fd = -1
    temporary: Path | None = None
    try:
        fd, raw = tempfile.mkstemp(
            prefix=f".{backup.name}.", suffix=".tmp", dir=str(backup.parent)
        )
        temporary = Path(raw)
        with os.fdopen(fd, "wb") as output:
            fd = -1
            with target.open("rb") as source:
                while True:
                    chunk = source.read(_CHUNK)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > MAX_ENTRY_BYTES:
                        raise ResourceIntegrityError(f"backup source is too large: {target}")
                    digest.update(chunk)
                    output.write(chunk)
            output.flush()
            os.fsync(output.fileno())
        if digest.hexdigest() != expected_sha:
            raise ResourceIntegrityError(
                f"installed file changed before backup: {target}"
            )
        _replace_with_retry(temporary, backup)
        temporary = None
    finally:
        if fd >= 0:
            try:
                os.close(fd)
            except OSError:
                pass
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def _write_archive_entry(
    archive: zipfile.ZipFile,
    action: dict,
    attempt_id: str,
    resource_root: Path,
) -> None:
    target = Path(action["target"])
    info = archive.getinfo(action["name"])
    if info.is_dir():
        raise ResourceIntegrityError(f"cannot copy a directory entry: {action['name']!r}")
    _check_existing_parents(resource_root, target)

    fd = -1
    temporary: Path | None = None
    try:
        fd, raw = tempfile.mkstemp(
            prefix=f".{target.name}.{attempt_id[:8]}.",
            suffix=".preflight.tmp",
            dir=str(target.parent),
        )
        temporary = Path(raw)
        digest = hashlib.sha256()
        crc = 0
        size = 0
        with os.fdopen(fd, "wb") as output:
            fd = -1
            with archive.open(info, "r") as source:
                while True:
                    chunk = source.read(_CHUNK)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > MAX_ENTRY_BYTES:
                        raise ResourceIntegrityError(
                            f"archive entry exceeds {MAX_ENTRY_BYTES} bytes: {action['name']!r}"
                        )
                    digest.update(chunk)
                    crc = zlib.crc32(chunk, crc)
                    output.write(chunk)
            output.flush()
            os.fsync(output.fileno())
        if size != info.file_size:
            raise ResourceIntegrityError(
                f"staged size mismatch for {action['name']!r}: {size} != {info.file_size}"
            )
        if (crc & 0xFFFFFFFF) != (info.CRC & 0xFFFFFFFF):
            raise ResourceIntegrityError(f"staged CRC mismatch for {action['name']!r}")
        if digest.hexdigest() != action["source_sha256"]:
            raise ResourceIntegrityError(
                f"staged SHA256 mismatch for {action['name']!r}"
            )

        current = _lstat(target)
        if action["kind"] == "replaced_theme":
            if current is None:
                raise ResourceIntegrityError(f"theme target disappeared: {target}")
            if _is_reparse(current) or not stat.S_ISREG(current.st_mode):
                raise ResourceIntegrityError(f"theme target changed type: {target}")
            current_sha, _ = _sha256_file_bounded(
                target, MAX_ENTRY_BYTES, "theme target"
            )
            if current_sha != action["prior_sha256"]:
                raise ResourceIntegrityError(f"theme target changed before replace: {target}")
        else:
            if current is not None:
                raise ResourceIntegrityError(f"missing target appeared before copy: {target}")

        _check_existing_parents(resource_root, target)
        _replace_with_retry(temporary, target)
        temporary = None
    finally:
        if fd >= 0:
            try:
                os.close(fd)
            except OSError:
                pass
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def _verify_target(target: Path, expected_sha: str) -> None:
    current_sha, _ = _sha256_file_bounded(target, MAX_ENTRY_BYTES, "written resource")
    if current_sha != expected_sha:
        raise ResourceIntegrityError(f"post-write SHA256 mismatch: {target}")


def _record_mutations(attempt: dict, executed: list[dict]) -> None:
    attempt["copied_missing"].clear()
    attempt["replaced_theme"].clear()
    attempt["mutations"].clear()
    for action in executed:
        entry = {
            "path": action["name"],
            "size": action["size"],
            "sha256": action["source_sha256"],
            "prior_sha256": action["prior_sha256"],
            "prior_size": action.get("prior_size", 0)
            if action["prior_sha256"] != "absent"
            else 0,
        }
        if action["kind"] == "replaced_theme":
            entry["backup"] = action.get("backup")
            attempt["replaced_theme"].append(entry)
        else:
            attempt["copied_missing"].append(entry)
        attempt["mutations"].append({"action": action["kind"], **entry})


def _restore_from_backup(backup: Path, target: Path, expected_sha: str) -> None:
    data = _read_bytes_bounded(backup, MAX_ENTRY_BYTES, "rollback backup")
    if _sha256_bytes(data) != expected_sha:
        raise ResourceIntegrityError(f"rollback backup SHA256 mismatch: {backup}")
    target.parent.mkdir(parents=True, exist_ok=True)
    fd = -1
    temporary: Path | None = None
    try:
        fd, raw = tempfile.mkstemp(
            prefix=f".{target.name}.rollback.", suffix=".tmp", dir=str(target.parent)
        )
        temporary = Path(raw)
        with os.fdopen(fd, "wb") as output:
            fd = -1
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        _replace_with_retry(temporary, target)
        temporary = None
    finally:
        if fd >= 0:
            try:
                os.close(fd)
            except OSError:
                pass
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
    _verify_target(target, expected_sha)


def _rollback(
    executed: list[dict], created_dirs: list[Path], resource_root: Path
) -> dict:
    errors: list[str] = []
    restored = 0
    deleted = 0
    for action in reversed(executed):
        target = Path(action["target"])
        try:
            _check_existing_parents(resource_root, target)
            if action["kind"] == "replaced_theme":
                backup_raw = action.get("backup")
                if not backup_raw:
                    raise ResourceIntegrityError(f"no backup recorded for: {target}")
                _restore_from_backup(
                    Path(backup_raw), target, action["prior_sha256"]
                )
                restored += 1
            else:
                stat_result = _lstat(target)
                if stat_result is None:
                    deleted += 1
                    continue
                if _is_reparse(stat_result) or not stat.S_ISREG(stat_result.st_mode):
                    raise ResourceIntegrityError(
                        f"rollback target is no longer a regular file: {target}"
                    )
                current_sha, _ = _sha256_file_bounded(
                    target, MAX_ENTRY_BYTES, "rollback target"
                )
                if current_sha != action["source_sha256"]:
                    raise ResourceIntegrityError(
                        f"rollback target changed after write: {target}"
                    )
                target.unlink()
                deleted += 1
        except BaseException as exc:  # noqa: BLE001 - continue best-effort rollback
            errors.append(f"{target}: {type(exc).__name__}: {exc}")

    for directory in reversed(created_dirs):
        try:
            directory.rmdir()
        except FileNotFoundError:
            pass
        except OSError as exc:
            errors.append(f"{directory}: cannot remove created directory: {exc}")

    return {
        "attempted": len(executed),
        "restored": restored,
        "deleted": deleted,
        "errors": errors,
        "complete": not errors,
    }


def _refresh_final_hashes(attempt: dict, actions: list[dict]) -> None:
    """Replace planned hashes with the actual on-disk state after a failure."""
    for action in actions:
        target = Path(action["target"])
        try:
            stat_result = _lstat(target)
            if stat_result is None:
                attempt["final_hashes"][action["name"]] = None
                continue
            current_sha, _ = _sha256_file_bounded(
                target, MAX_ENTRY_BYTES, "final resource"
            )
        except Exception:  # noqa: BLE001 - audit must not mask the real error
            current_sha = None
        attempt["final_hashes"][action["name"]] = current_sha


def _execute_actions(
    attempt: dict,
    archive_path: Path,
    actions: list[dict],
    check_stop: Callable[[], None],
) -> None:
    backup_root: Path | None = None
    if any(action["kind"] == "replaced_theme" for action in actions):
        backup_root = (
            Path(attempt["audit_root"]) / AUDIT_DIRNAME / "backups" / attempt["attempt_id"]
        )

    created_dirs: list[Path] = []
    executed: list[dict] = []
    writes = 0
    try:
        with zipfile.ZipFile(archive_path) as archive:
            resource_root = Path(attempt["resource_root"])
            for action in actions:
                check_stop()
                target = Path(action["target"])
                _check_existing_parents(resource_root, target)
                if action["kind"] == "replaced_theme":
                    if backup_root is None:
                        raise ResourcePreflightError("internal error: no backup root")
                    backup = _backup_path(backup_root, action["name"])
                    _backup_target(target, backup, action["prior_sha256"])
                    action["backup"] = str(backup)
                    attempt["backup_dir"] = str(backup_root)
                else:
                    _ensure_parent_dirs(target.parent, resource_root, created_dirs)
                _write_archive_entry(archive, action, attempt["attempt_id"], resource_root)
                executed.append(action)
                writes += 1
                attempt["writes"] = writes
                _verify_target(target, action["source_sha256"])
                attempt["final_hashes"][action["name"]] = action["source_sha256"]
    except BaseException:
        attempt["writes"] = writes
        _record_mutations(attempt, executed)
        attempt["rollback"] = _rollback(executed, created_dirs, resource_root)
        _refresh_final_hashes(attempt, actions)
        attempt["net_writes"] = max(
            0,
            writes
            - attempt["rollback"]["restored"]
            - attempt["rollback"]["deleted"],
        )
        raise
    attempt["writes"] = writes
    attempt["net_writes"] = writes
    _record_mutations(attempt, executed)


def _run(attempt: dict, check_stop: Callable[[], None]) -> None:
    check_stop()
    maa_root, audit_root, resource_root = _validate_roots(attempt)
    installed = _read_installed(attempt, maa_root, resource_root)

    try:
        records, matches = _candidate_records(maa_root, installed)
    except _TooManyArchives as exc:
        attempt.update(status="skipped", reason="too_many_archives", ok=False,
                       source_verified=False, verified_healthy=False)
        attempt["warnings"].append(str(exc))
        return
    attempt["candidates"] = records

    selected = _select_source(attempt, installed, matches)
    if selected is None:
        return

    assert attempt["archive"] is not None
    archive_path = Path(attempt["archive"])
    structure = selected["structure"]
    try:
        resource_shas = _full_scan_archive(archive_path)
    except _ArchiveInvalid as exc:
        attempt.update(
            status="skipped",
            reason="archive_invalid",
            ok=False,
            source_verified=False,
            verified_healthy=False,
        )
        attempt["error"] = {"type": "ArchiveInvalid", "message": str(exc)}
        return

    try:
        current_archive_sha, _ = _sha256_file_bounded(
            archive_path, MAX_ARCHIVE_BYTES, "OTA archive"
        )
    except ResourceIntegrityError as exc:
        attempt.update(
            status="skipped", reason="archive_changed", ok=False,
            source_verified=False, verified_healthy=False,
        )
        attempt["error"] = {"type": type(exc).__name__, "message": str(exc)}
        return
    if current_archive_sha != attempt["archive_sha256"]:
        attempt.update(
            status="skipped", reason="archive_changed", ok=False,
            source_verified=False, verified_healthy=False,
        )
        attempt["error"] = {
            "type": "ArchiveChanged",
            "message": "OTA archive changed after identity selection",
        }
        return

    attempt["archive_validation"] = {
        "entries": structure["entry_count"],
        "resource_entries": structure["resource_count"],
        "all_files_crc_checked": True,
        "limits": {
            "max_archive_bytes": MAX_ARCHIVE_BYTES,
            "max_entries": MAX_ENTRIES,
            "max_entry_bytes": MAX_ENTRY_BYTES,
            "max_total_uncompressed": MAX_TOTAL_UNCOMPRESSED,
            "max_compression_ratio": MAX_COMPRESSION_RATIO,
        },
    }

    actions, missing_before, untouched, final_hashes = _plan_repairs(
        attempt, resource_root, structure["resource_infos"], resource_shas, installed
    )
    attempt["missing_before"] = missing_before
    attempt["untouched_mismatches"] = untouched
    attempt["remaining_mismatches"] = [
        {"path": item["path"], "reason": item["reason"]}
        for item in untouched
    ]
    attempt["final_hashes"] = final_hashes

    if actions:
        check_stop()
        _execute_actions(attempt, archive_path, actions, check_stop)

    remaining = attempt["remaining_mismatches"]
    if attempt["writes"]:
        attempt["status"] = "repaired" if not remaining else "repaired_partial"
        attempt["reason"] = (
            "repaired_from_matching_archive"
            if not remaining
            else "repaired_allowlisted_mismatches_unrelated_left_untouched"
        )
    else:
        attempt["status"] = "healthy" if not remaining else "partial"
        attempt["reason"] = (
            "archive_resource_tree_matches"
            if not remaining
            else "unrelated_mismatches_left_untouched"
        )
    attempt["ok"] = True
    attempt["source_verified"] = True
    attempt["verified_healthy"] = not remaining


def ensure_resources(
    maa_root: Path,
    audit_root: Path,
    check_stop: Callable[[], None] = lambda: None,
) -> dict:
    """Repair missing/theme resources from a proven local OTA archive.

    Must be called before ``Asst.load`` while the worker holds its task/device
    lock and the desktop GUI is idle.  The function never downloads, never runs
    ADB, never touches settings/userdata, and never uses ``extractall``.

    Returns the attempt dictionary (also written to ``audit_root``).  Unknown
    or ambiguous archives return ``status == "skipped"`` with
    ``verified_healthy == False`` and no resource mutations.  A stop request
    during the transaction is rolled back and re-raised.
    """
    maa_root = Path(maa_root)
    audit_root = Path(audit_root)
    attempt = _new_attempt(maa_root, audit_root)
    try:
        _run(attempt, check_stop)
    except BaseException as exc:  # noqa: BLE001 - truthful audit, then re-raise
        if isinstance(exc, (InterruptedError, KeyboardInterrupt)):
            attempt["status"] = "interrupted"
            attempt["reason"] = "stopped"
        elif attempt.get("status") in (None, "running"):
            attempt["status"] = "failed"
            if isinstance(exc, ResourceIntegrityError):
                attempt["reason"] = "installed_integrity"
            elif isinstance(exc, ResourcePreflightError):
                attempt["reason"] = "preflight_error"
            else:
                attempt["reason"] = "exception"
        attempt["ok"] = False
        attempt["error"] = {"type": type(exc).__name__, "message": str(exc)}
        _finalize_audit(attempt)
        raise
    _finalize_audit(attempt)
    return attempt
