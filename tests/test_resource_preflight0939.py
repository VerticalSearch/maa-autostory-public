"""Independent tests for candidate autostory/resource_preflight.py.

Run from the task root:
    set TEMP=<task-root>\\tmp
    set TMP=<task-root>\\tmp
    python -m unittest discover -s candidate/tests -v
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import tempfile
import unittest
import unittest.mock
import uuid
import warnings
import zipfile
from pathlib import Path

from autostory import resource_preflight as rp


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def snapshot(root: Path) -> dict[str, tuple[bytes, int]]:
    result: dict[str, tuple[bytes, int]] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            result[str(path.relative_to(root))] = (
                path.read_bytes(),
                path.stat().st_mtime_ns,
            )
    return result


class World:
    """A miniature installed MAA tree plus a matching OTA ZIP."""

    def __init__(self, base: Path):
        self.base = base
        self.maa = base / "maa"
        self.audit = base / "audit"
        self.core = b"dummy-core-v1"
        self.tasks = json.dumps(
            {"Block": {"algorithm": "MatchTemplate"}, "StartUp": {"algorithm": "JustReturn"}},
            sort_keys=True,
        ).encode("utf-8")
        self.initial: dict[str, bytes] = {
            "MaaCore.dll": self.core,
            "resource/tasks/tasks.json": self.tasks,
            "resource/tasks/UiTheme/Depot.json": b'{"theme":"old"}',
            "resource/template/UiTheme/Default/DepotEnter-90.png": b"old-theme-png",
            "resource/item_index.json": b'{"index":"old"}',
            "resource/stages.json": b'{"stages":"same"}',
        }
        self.archive: dict[str, bytes] = dict(self.initial)
        self.archive.update(
            {
                "resource/tasks/UiTheme/Depot.json": b'{"theme":"new"}',
                "resource/template/UiTheme/Default/DepotEnter-90.png": b"new-theme-png",
                "resource/template/avatar/a.png": b"avatar-a",
                "resource/template/avatar/b.png": b"avatar-b",
                "resource/item_index.json": b'{"index":"new"}',
            }
        )
        self.write_files(self.initial)

    def write_files(self, files: dict[str, bytes]) -> None:
        for relative, data in files.items():
            path = self.maa / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)

    def build_zip(
        self,
        filename: str = "MAAComponent-OTA-test.zip",
        entries: dict[str, bytes] | None = None,
        duplicate: str | None = None,
        symlink: str | None = None,
    ) -> Path:
        path = self.maa / filename
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
                for name, data in sorted((entries or self.archive).items()):
                    archive.writestr(name, data)
                # Safe explicit directory entries must be accepted and skipped.
                archive.writestr("resource/template/avatar/", b"")
                if duplicate is not None:
                    archive.writestr(duplicate, b"duplicate-data")
                if symlink is not None:
                    info = zipfile.ZipInfo(symlink)
                    info.create_system = 3
                    info.external_attr = 0o120777 << 16
                    archive.writestr(info, b"..\\..\\outside")
        return path

    def audit_latest(self) -> dict:
        return json.loads(
            (self.audit / "resource-preflight" / "latest.json").read_text(encoding="utf-8")
        )


class PreflightTests(unittest.TestCase):
    def setUp(self) -> None:
        # Plain mkdir (not TemporaryDirectory/mkdtemp): on this host Python's
        # 0o700 temporary-directory mode can produce an inaccessible ACL under
        # the sandbox.  Normal directories inherit the task root ACL.
        root = Path(tempfile.gettempdir())
        root.mkdir(parents=True, exist_ok=True)
        self._temporary = root / (
            "resource0939-" + str(os.getpid()) + "-" + uuid.uuid4().hex[:10]
        )
        self._temporary.mkdir(parents=False, exist_ok=False)
        self.world = World(self._temporary)

    def tearDown(self) -> None:
        shutil.rmtree(self._temporary, ignore_errors=True)

    def assert_no_outside_escape(self) -> None:
        for path in (
            self.world.maa / "escape.txt",
            self.world.base / "escape.txt",
            self.world.maa.parent / "escape.txt",
        ):
            self.assertFalse(path.exists(), f"zip-slip created {path}")

    def test_repairs_missing_and_theme_with_backups(self) -> None:
        archive = self.world.build_zip()
        result = rp.ensure_resources(self.world.maa, self.world.audit)

        self.assertEqual(result["status"], "repaired_partial")
        self.assertTrue(result["ok"])
        self.assertTrue(result["source_verified"])
        self.assertFalse(result["verified_healthy"])
        self.assertEqual(result["writes"], 4)
        self.assertEqual(result["archive_sha256"], sha_file(archive))
        self.assertEqual(
            result["identity"]["archive"][rp.CORE_ENTRY]["sha256"],
            sha(self.world.core),
        )
        self.assertEqual(
            result["identity"]["archive"][rp.TASKS_ENTRY]["sha256"],
            sha(self.world.tasks),
        )
        self.assertEqual(
            (self.world.maa / "resource/item_index.json").read_bytes(),
            b'{"index":"old"}',
        )
        self.assertEqual(
            (self.world.maa / "resource/tasks/UiTheme/Depot.json").read_bytes(),
            b'{"theme":"new"}',
        )
        self.assertEqual(
            (
                self.world.maa
                / "resource/template/UiTheme/Default/DepotEnter-90.png"
            ).read_bytes(),
            b"new-theme-png",
        )
        for name in ("a.png", "b.png"):
            self.assertEqual(
                (self.world.maa / "resource/template/avatar" / name).read_bytes(),
                f"avatar-{name[0]}".encode(),
            )

        self.assertEqual(len(result["copied_missing"]), 2)
        self.assertEqual(len(result["replaced_theme"]), 2)
        self.assertTrue(
            {item["path"] for item in result["copied_missing"]}
            == {
                "resource/template/avatar/a.png",
                "resource/template/avatar/b.png",
            }
        )
        self.assertTrue(
            {item["path"] for item in result["replaced_theme"]}
            == {
                "resource/tasks/UiTheme/Depot.json",
                "resource/template/UiTheme/Default/DepotEnter-90.png",
            }
        )
        for item in result["replaced_theme"]:
            backup = Path(item["backup"])
            self.assertTrue(backup.is_file())
            original_name = item["path"].split("resource/", 1)[1]
            self.assertEqual(backup.read_bytes(), self.world.initial[f"resource/{original_name}"])
            self.assertEqual(item["prior_sha256"], sha(backup.read_bytes()))

        self.assertEqual(
            result["final_hashes"]["resource/tasks/UiTheme/Depot.json"],
            sha(b'{"theme":"new"}'),
        )
        self.assertEqual(
            result["final_hashes"]["resource/template/avatar/a.png"],
            sha(b"avatar-a"),
        )
        self.assertEqual(
            result["final_hashes"]["resource/item_index.json"],
            sha(b'{"index":"old"}'),
        )
        self.assertEqual(result["net_writes"], 4)
        self.assertTrue(
            any(
                item["path"] == "resource/item_index.json"
                for item in result["untouched_mismatches"]
            )
        )
        audit = self.world.audit_latest()
        self.assertEqual(audit["attempt_id"], result["attempt_id"])
        self.assertEqual(audit["status"], "repaired_partial")
        self.assertTrue(Path(result["audit"]["attempt_path"]).is_file())
        self.assertTrue(Path(result["audit"]["latest_path"]).is_file())

    def test_nonallowlisted_mismatch_is_never_overwritten(self) -> None:
        self.world.build_zip()
        result = rp.ensure_resources(self.world.maa, self.world.audit)
        self.assertEqual(
            (self.world.maa / "resource/item_index.json").read_bytes(),
            b'{"index":"old"}',
        )
        self.assertNotIn(
            "resource/item_index.json",
            {mutation["path"] for mutation in result["mutations"]},
        )
        self.assertEqual(
            [item["path"] for item in result["remaining_mismatches"]],
            ["resource/item_index.json"],
        )

    def test_idempotent_repeat_writes_zero(self) -> None:
        self.world.build_zip()
        first = rp.ensure_resources(self.world.maa, self.world.audit)
        before = snapshot(self.world.maa)
        audit_dir = self.world.audit / "resource-preflight"
        attempts_before = len([p for p in audit_dir.glob("*.json") if p.name != "latest.json"])

        second = rp.ensure_resources(self.world.maa, self.world.audit)
        after = snapshot(self.world.maa)
        attempts_after = len([p for p in audit_dir.glob("*.json") if p.name != "latest.json"])

        self.assertEqual(second["writes"], 0)
        self.assertEqual(second["mutations"], [])
        self.assertEqual(second["copied_missing"], [])
        self.assertEqual(second["replaced_theme"], [])
        self.assertTrue(second["source_verified"])
        self.assertEqual(second["status"], "partial")
        self.assertFalse(second["verified_healthy"])
        self.assertEqual(before, after)
        self.assertEqual(attempts_after, attempts_before + 1)
        self.assertNotEqual(first["attempt_id"], second["attempt_id"])
        self.assertEqual(self.world.audit_latest()["attempt_id"], second["attempt_id"])

    def test_audit_inside_resource_is_refused_without_writes(self) -> None:
        self.world.build_zip()
        before = snapshot(self.world.maa)
        with self.assertRaises(rp.ResourcePreflightError):
            rp.ensure_resources(
                self.world.maa, self.world.maa / "resource" / "audit"
            )
        self.assertEqual(before, snapshot(self.world.maa))
        self.assertFalse((self.world.maa / "resource" / "audit").exists())

    def test_no_archive_skips_without_mutation(self) -> None:
        before = snapshot(self.world.maa)
        result = rp.ensure_resources(self.world.maa, self.world.audit)
        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["reason"], "no_source")
        self.assertFalse(result["source_verified"])
        self.assertFalse(result["verified_healthy"])
        self.assertEqual(result["writes"], 0)
        self.assertEqual(before, snapshot(self.world.maa))

    def test_mismatched_core_is_no_source(self) -> None:
        entries = dict(self.world.archive)
        entries[rp.CORE_ENTRY] = b"different-core"
        self.world.build_zip(entries=entries)
        before = snapshot(self.world.maa)
        result = rp.ensure_resources(self.world.maa, self.world.audit)
        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["reason"], "no_source")
        self.assertFalse(result["source_verified"])
        self.assertFalse(result["candidates"][0]["core_match"])
        self.assertEqual(before, snapshot(self.world.maa))

    def test_mismatched_main_task_is_no_source(self) -> None:
        entries = dict(self.world.archive)
        entries[rp.TASKS_ENTRY] = b'{"Block":{"algorithm":"Different"}}'
        self.world.build_zip(entries=entries)
        before = snapshot(self.world.maa)
        result = rp.ensure_resources(self.world.maa, self.world.audit)
        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["reason"], "no_source")
        self.assertFalse(result["source_verified"])
        self.assertFalse(result["candidates"][0]["tasks_match"])
        self.assertEqual(before, snapshot(self.world.maa))

    def test_ambiguous_matching_archives_are_rejected(self) -> None:
        self.world.build_zip(filename="a.zip")
        second_entries = dict(self.world.archive)
        second_entries["resource/tasks/UiTheme/Depot.json"] = b'{"theme":"other"}'
        self.world.build_zip(filename="b.zip", entries=second_entries)
        before = snapshot(self.world.maa)
        result = rp.ensure_resources(self.world.maa, self.world.audit)
        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["reason"], "ambiguous_source")
        self.assertFalse(result["source_verified"])
        self.assertEqual(result["writes"], 0)
        self.assertEqual(len(result["ambiguous_matches"]), 2)
        self.assertEqual(before, snapshot(self.world.maa))

    def test_traversal_archive_is_rejected(self) -> None:
        entries = dict(self.world.archive)
        entries["../escape.txt"] = b"evil"
        self.world.build_zip(entries=entries)
        result = rp.ensure_resources(self.world.maa, self.world.audit)
        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["reason"], "no_source")
        self.assertEqual(result["writes"], 0)
        self.assertIn("dot segment", result["candidates"][0]["reason"])
        self.assert_no_outside_escape()

    def test_duplicate_archive_path_is_rejected(self) -> None:
        self.world.build_zip(duplicate="resource/tasks/UiTheme/Depot.json")
        result = rp.ensure_resources(self.world.maa, self.world.audit)
        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["reason"], "no_source")
        self.assertIn("duplicate", result["candidates"][0]["reason"])
        self.assertEqual(
            (self.world.maa / "resource/tasks/UiTheme/Depot.json").read_bytes(),
            b'{"theme":"old"}',
        )

    def test_case_collision_archive_is_rejected(self) -> None:
        entries = dict(self.world.archive)
        entries["resource/template/avatar/A.png"] = b"upper-a"
        self.world.build_zip(entries=entries)
        result = rp.ensure_resources(self.world.maa, self.world.audit)
        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["reason"], "no_source")
        self.assertIn("colliding", result["candidates"][0]["reason"])

    def test_symlink_archive_entry_is_rejected(self) -> None:
        self.world.build_zip(symlink="resource/template/avatar/link.png")
        result = rp.ensure_resources(self.world.maa, self.world.audit)
        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["reason"], "no_source")
        self.assertIn("symlink", result["candidates"][0]["reason"])
        self.assertFalse((self.world.maa / "resource/template/avatar/link.png").exists())

    def test_corrupt_archive_crc_is_skipped(self) -> None:
        archive = self.world.build_zip()
        self._corrupt_central_crc(archive, "resource/template/avatar/a.png")
        before = snapshot(self.world.maa)
        result = rp.ensure_resources(self.world.maa, self.world.audit)
        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["reason"], "archive_invalid")
        self.assertFalse(result["source_verified"])
        self.assertEqual(result["writes"], 0)
        self.assertEqual(before, snapshot(self.world.maa))

    def test_interrupted_transaction_rolls_back(self) -> None:
        self.world.build_zip()
        calls = {"count": 0}

        def stop() -> None:
            calls["count"] += 1
            if calls["count"] >= 6:
                raise InterruptedError("test stop")

        with self.assertRaises(InterruptedError):
            rp.ensure_resources(self.world.maa, self.world.audit, check_stop=stop)

        self.assertEqual(
            (self.world.maa / "resource/tasks/UiTheme/Depot.json").read_bytes(),
            b'{"theme":"old"}',
        )
        self.assertEqual(
            (
                self.world.maa
                / "resource/template/UiTheme/Default/DepotEnter-90.png"
            ).read_bytes(),
            b"old-theme-png",
        )
        self.assertFalse(
            (self.world.maa / "resource/template/avatar/a.png").exists()
        )
        self.assertFalse(
            (self.world.maa / "resource/template/avatar/b.png").exists()
        )
        self.assertFalse((self.world.maa / "resource/template/avatar").exists())

        latest = self.world.audit_latest()
        self.assertEqual(latest["status"], "interrupted")
        self.assertEqual(latest["reason"], "stopped")
        self.assertEqual(latest["writes"], 3)
        self.assertEqual(len(latest["mutations"]), 3)
        self.assertEqual(latest["rollback"]["attempted"], 3)
        self.assertEqual(latest["rollback"]["restored"], 2)
        self.assertEqual(latest["rollback"]["deleted"], 1)
        self.assertTrue(latest["rollback"]["complete"])
        self.assertEqual(latest["rollback"]["errors"], [])
        self.assertEqual(latest["net_writes"], 0)
        self.assertEqual(
            latest["final_hashes"]["resource/tasks/UiTheme/Depot.json"],
            sha(b'{"theme":"old"}'),
        )
        self.assertIsNone(
            latest["final_hashes"]["resource/template/avatar/a.png"]
        )
        self.assertIsNone(
            latest["final_hashes"]["resource/template/avatar/b.png"]
        )
        self.assertGreaterEqual(calls["count"], 6)
        self.assertTrue(latest["replaced_theme"])
        self.assertTrue(latest["copied_missing"])

    def test_partial_rollback_error_is_recorded(self) -> None:
        self.world.build_zip()
        calls = {"count": 0}

        def stop() -> None:
            calls["count"] += 1
            if calls["count"] >= 5:
                raise InterruptedError("test stop")

        real_restore = rp._restore_from_backup
        failures = {"remaining": 1}

        def flaky_restore(backup: Path, target: Path, expected_sha: str) -> None:
            if failures["remaining"]:
                failures["remaining"] -= 1
                raise rp.ResourceIntegrityError("simulated restore failure")
            real_restore(backup, target, expected_sha)

        with unittest.mock.patch.object(rp, "_restore_from_backup", flaky_restore):
            with self.assertRaises(InterruptedError):
                rp.ensure_resources(self.world.maa, self.world.audit, check_stop=stop)

        latest = self.world.audit_latest()
        self.assertEqual(latest["status"], "interrupted")
        self.assertEqual(latest["writes"], 2)
        self.assertEqual(latest["net_writes"], 1)
        self.assertFalse(latest["rollback"]["complete"])
        self.assertEqual(len(latest["rollback"]["errors"]), 1)
        self.assertIn("simulated restore failure", latest["rollback"]["errors"][0])
        self.assertEqual(
            (self.world.maa / "resource/tasks/UiTheme/Depot.json").read_bytes(),
            b'{"theme":"old"}',
        )
        # The first restore failed, so the template file remains at its new
        # bytes; final_hashes must report that actual state.
        self.assertEqual(
            (
                self.world.maa
                / "resource/template/UiTheme/Default/DepotEnter-90.png"
            ).read_bytes(),
            b"new-theme-png",
        )
        self.assertEqual(
            latest["final_hashes"][
                "resource/template/UiTheme/Default/DepotEnter-90.png"
            ],
            sha(b"new-theme-png"),
        )

    def test_malformed_installed_main_task_fails_clearly(self) -> None:
        (self.world.maa / "resource/tasks/tasks.json").write_bytes(b"{not-json")
        before = snapshot(self.world.maa)
        with self.assertRaises(rp.ResourceIntegrityError):
            rp.ensure_resources(self.world.maa, self.world.audit)
        self.assertEqual(before, snapshot(self.world.maa))
        latest = self.world.audit_latest()
        self.assertEqual(latest["status"], "failed")
        self.assertEqual(latest["reason"], "installed_integrity")

    def test_missing_installed_core_fails_clearly(self) -> None:
        (self.world.maa / "MaaCore.dll").unlink()
        before = snapshot(self.world.maa)
        with self.assertRaises(rp.ResourceIntegrityError):
            rp.ensure_resources(self.world.maa, self.world.audit)
        self.assertEqual(before, snapshot(self.world.maa))
        latest = self.world.audit_latest()
        self.assertEqual(latest["status"], "failed")
        self.assertEqual(latest["reason"], "installed_integrity")

    @staticmethod
    def _corrupt_central_crc(archive: Path, name: str) -> None:
        payload = bytearray(archive.read_bytes())
        encoded = name.encode("utf-8")
        position = payload.rfind(encoded)
        while position >= 0:
            start = position - 46
            if start >= 0 and payload[start : start + 4] == b"PK\x01\x02":
                actual = int.from_bytes(payload[start + 16 : start + 20], "little")
                payload[start + 16 : start + 20] = (actual ^ 0xFFFFFFFF).to_bytes(
                    4, "little"
                )
                archive.write_bytes(bytes(payload))
                return
            position = payload.rfind(encoded, 0, position)
        raise AssertionError(f"central directory entry not found for {name}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
