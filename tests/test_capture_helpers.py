"""Unit tests for the standalone ADB transport and raw screencap helpers.

Run from this directory with:

    python -m unittest -v test_helpers.py

The tests use only real ``sys.executable`` child processes for cancellation,
timeout, large-pipe and post-communicate-stop behaviour.  They do not touch
ADB, the game, MAA or any external service.
"""

from __future__ import annotations

import ctypes
import os
import struct
import subprocess
import sys
import time
import unittest
import uuid
from pathlib import Path
from typing import Any
from unittest import mock

import numpy as np

# Make the flat helper directory importable regardless of the caller's cwd.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from autostory import adb_transport
from autostory.adb_transport import run_command
from autostory.raw_capture import RawCaptureError, decode_raw_screencap


# ---------------------------------------------------------------------------
# Windows process-liveness probe (used to prove a timed-out/cancelled child is
# really gone).  On POSIX, signal 0 is sufficient.
# ---------------------------------------------------------------------------

_IS_WINDOWS = os.name == "nt"
_STILL_ACTIVE = 259

if _IS_WINDOWS:
    from ctypes import wintypes

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _kernel32.OpenProcess.restype = wintypes.HANDLE
    _kernel32.GetExitCodeProcess.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.DWORD),
    ]
    _kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel32.CloseHandle.restype = wintypes.BOOL


def _pid_alive(pid: int) -> bool:
    if _IS_WINDOWS:
        handle = _kernel32.OpenProcess(0x1000, False, int(pid))  # QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        try:
            exit_code = wintypes.DWORD()
            if not _kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return False
            return exit_code.value == _STILL_ACTIVE
        finally:
            _kernel32.CloseHandle(handle)

    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _wait_dead(pid: int, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _pid_alive(pid):
            return True
        time.sleep(0.05)
    return not _pid_alive(pid)


def _read_pid(path: Path, timeout: float = 5.0) -> int:
    deadline = time.monotonic() + timeout
    last = ""
    while time.monotonic() < deadline:
        try:
            last = path.read_text(encoding="ascii").strip()
        except FileNotFoundError:
            last = ""
        except OSError:
            last = ""
        if last.isdigit():
            return int(last)
        time.sleep(0.02)
    raise AssertionError(f"child did not publish a pid to {path!s}; last={last!r}")


# The test sandbox does not let child processes write into freshly-created
# subdirectories, so use unique files directly in the task folder.
_TEST_ROOT = Path(__file__).resolve().parent


def _pidfile(label: str) -> Path:
    return _TEST_ROOT / f".test-pid-{os.getpid()}-{label}-{uuid.uuid4().hex}.txt"


def _unlink_quietly(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass


# Child fixture: publish pid, write/flush some stdout, then sleep far longer
# than the test budget.  Killing it is the only way these tests finish quickly.
_SLEEPER_TEMPLATE = """\
import os, sys, time
with open(sys.argv[1], "w", encoding="ascii") as f:
    f.write(str(os.getpid()))
    f.flush()
{spew}time.sleep(60)
"""


def _sleeper(spew: str = "") -> str:
    return _SLEEPER_TEMPLATE.format(spew=spew)


# ---------------------------------------------------------------------------
# ADB transport
# ---------------------------------------------------------------------------


class AdbTransportTests(unittest.TestCase):
    def test_completed_output_after_deadline_is_discarded(self):
        process=mock.Mock(args=['fixture'],returncode=0,stdout=None,stderr=None)
        process.communicate.return_value=(b'late',b'')
        process.poll.return_value=0
        with mock.patch.object(adb_transport.subprocess,'Popen',return_value=process), \
             mock.patch.object(adb_transport.time,'monotonic',side_effect=[0,0,2]):
            with self.assertRaises(subprocess.TimeoutExpired):
                run_command(['fixture'],check_stop=lambda:None,timeout=1)
        process.kill.assert_not_called()

    def test_success_returns_bytes_completedprocess(self) -> None:
        code = (
            "import sys\n"
            "sys.stdout.buffer.write(b'out\\x00\\xff\\n')\n"
            "sys.stderr.buffer.write(b'err\\r\\n')\n"
        )
        args = [sys.executable, "-c", code]

        result = run_command(args, check_stop=lambda: None, timeout=20, poll_interval=0.05)

        self.assertIsInstance(result, subprocess.CompletedProcess)
        self.assertEqual(result.args, args)
        self.assertEqual(result.returncode, 0)
        self.assertIs(type(result.stdout), bytes)
        self.assertIs(type(result.stderr), bytes)
        self.assertEqual(result.stdout, b"out\x00\xff\n")
        self.assertEqual(result.stderr, b"err\r\n")

    def test_success_without_output_is_bytes(self) -> None:
        result = run_command(
            [sys.executable, "-c", "pass"],
            check_stop=lambda: None,
            timeout=20,
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, b"")
        self.assertEqual(result.stderr, b"")

    def test_nonzero_returncode_is_preserved(self) -> None:
        code = (
            "import sys\n"
            "sys.stdout.buffer.write(b'partial')\n"
            "sys.stderr.buffer.write(b'bad things')\n"
            "sys.exit(7)\n"
        )
        result = run_command(
            [sys.executable, "-c", code],
            check_stop=lambda: None,
            timeout=20,
            poll_interval=0.05,
        )
        self.assertEqual(result.returncode, 7)
        self.assertEqual(result.stdout, b"partial")
        self.assertEqual(result.stderr, b"bad things")

    def test_invalid_timeout_and_poll_interval_rejected(self) -> None:
        bad_values: list[Any] = [
            0,
            -1,
            float("inf"),
            float("-inf"),
            float("nan"),
            None,
            "1",
            True,
        ]
        for bad in bad_values:
            with self.subTest(kind="timeout", value=bad):
                with self.assertRaises(ValueError):
                    run_command(
                        [sys.executable, "-c", "pass"],
                        check_stop=lambda: None,
                        timeout=bad,
                    )
            with self.subTest(kind="poll_interval", value=bad):
                with self.assertRaises(ValueError):
                    run_command(
                        [sys.executable, "-c", "pass"],
                        check_stop=lambda: None,
                        timeout=1,
                        poll_interval=bad,
                    )

    def test_invalid_arguments_do_not_launch(self) -> None:
        with mock.patch.object(adb_transport.subprocess, "Popen") as popen:
            with self.assertRaises(ValueError):
                run_command("python -c pass", check_stop=lambda: None)
            with self.assertRaises(TypeError):
                run_command([sys.executable, "-c", "pass"], check_stop=None)
        popen.assert_not_called()

    def test_prelaunch_stop_does_not_launch_child(self) -> None:
        stop_exc = InterruptedError("stop before launch")

        def check_stop() -> None:
            raise stop_exc

        with mock.patch.object(adb_transport.subprocess, "Popen") as popen:
            with self.assertRaises(InterruptedError) as caught:
                run_command(
                    [sys.executable, "-c", "pass"],
                    check_stop=check_stop,
                    timeout=20,
                )
            popen.assert_not_called()
        self.assertIs(caught.exception, stop_exc)

    def test_cancellation_kills_created_child(self) -> None:
        pidfile = _pidfile("cancel")
        try:
            def check_stop() -> None:
                try:
                    text = pidfile.read_text(encoding="ascii").strip()
                except (FileNotFoundError, OSError):
                    return
                if text:
                    raise InterruptedError("cancelled after child start")

            started = time.monotonic()
            with self.assertRaises(InterruptedError):
                run_command(
                    [sys.executable, "-c", _sleeper(), str(pidfile)],
                    check_stop=check_stop,
                    timeout=30,
                    poll_interval=0.02,
                )
            elapsed = time.monotonic() - started

            pid = _read_pid(pidfile)
            self.assertTrue(_wait_dead(pid), f"child {pid} survived cancellation")
            self.assertLess(elapsed, 10.0)
        finally:
            _unlink_quietly(pidfile)

    def test_timeout_raises_and_kills_created_child(self) -> None:
        pidfile = _pidfile("timeout")
        try:
            spew = "sys.stdout.buffer.write(b'partial'); sys.stdout.buffer.flush()\n"
            started = time.monotonic()
            with self.assertRaises(subprocess.TimeoutExpired) as caught:
                run_command(
                    [sys.executable, "-c", _sleeper(spew), str(pidfile)],
                    check_stop=lambda: None,
                    timeout=1.5,
                    poll_interval=0.05,
                )
            elapsed = time.monotonic() - started

            pid = _read_pid(pidfile)
            self.assertTrue(_wait_dead(pid), f"child {pid} survived timeout")
            self.assertGreaterEqual(elapsed, 1.0)
            self.assertLess(elapsed, 8.0)
            self.assertEqual(caught.exception.timeout, 1.5)
            self.assertEqual(caught.exception.cmd[0], sys.executable)
            # On POSIX the transport propagates partial output; on Windows
            # 3.12 communicate() has no partial buffer while the child runs.
            if caught.exception.output:
                self.assertEqual(caught.exception.output, b"partial")
        finally:
            _unlink_quietly(pidfile)

    def test_postcommunicate_stop_is_observed_and_not_replaced(self) -> None:
        pidfile = _pidfile("poststop")
        try:
            code = (
                "import os, sys\n"
                "with open(sys.argv[1], 'w', encoding='ascii') as f:\n"
                "    f.write(str(os.getpid()))\n"
                "sys.stdout.buffer.write(b'ok')\n"
            )
            stop_exc = RuntimeError("stop after successful communicate")
            state = {"communicated": False, "calls": 0, "calls_at_success": None}

            original_communicate = subprocess.Popen.communicate

            def wrapped_communicate(self: subprocess.Popen, *args: Any, **kwargs: Any):
                result = original_communicate(self, *args, **kwargs)
                state["communicated"] = True
                state["calls_at_success"] = state["calls"]
                return result

            def check_stop() -> None:
                state["calls"] += 1
                if state["communicated"]:
                    raise stop_exc

            with mock.patch.object(
                subprocess.Popen, "communicate", new=wrapped_communicate
            ):
                with self.assertRaises(RuntimeError) as caught:
                    run_command(
                        [sys.executable, "-c", code, str(pidfile)],
                        check_stop=check_stop,
                        timeout=20,
                        poll_interval=0.1,
                    )

            self.assertIs(caught.exception, stop_exc)
            self.assertTrue(state["communicated"])
            self.assertIsNotNone(state["calls_at_success"])
            self.assertEqual(
                state["calls"],
                state["calls_at_success"] + 1,
                "check_stop must be called once after the successful communicate",
            )
            pid = _read_pid(pidfile)
            self.assertTrue(_wait_dead(pid), f"child {pid} survived cleanup")
        finally:
            _unlink_quietly(pidfile)

    def test_repeated_communicate_timeouts_do_not_duplicate_stdout(self) -> None:
        chunk = 65_536
        code = (
            "import sys, time\n"
            f"sys.stdout.buffer.write(b'A' * {chunk})\n"
            "sys.stdout.buffer.flush()\n"
            "time.sleep(0.6)\n"
            f"sys.stdout.buffer.write(b'B' * {chunk})\n"
            "sys.stdout.buffer.flush()\n"
        )
        calls = 0

        def check_stop() -> None:
            nonlocal calls
            calls += 1

        result = run_command(
            [sys.executable, "-c", code],
            check_stop=check_stop,
            timeout=15,
            poll_interval=0.02,
        )

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, b"A" * chunk + b"B" * chunk)
        self.assertEqual(result.stderr, b"")
        self.assertGreater(calls, 5, "the child should have forced repeated communicate timeouts")

    def test_large_pipe_output_is_complete_and_not_duplicated(self) -> None:
        size = 2_000_000
        code = (
            "import sys\n"
            f"sys.stdout.buffer.write(b'x' * {size})\n"
            "sys.stdout.buffer.flush()\n"
        )
        result = run_command(
            [sys.executable, "-c", code],
            check_stop=lambda: None,
            timeout=20,
            poll_interval=0.05,
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(len(result.stdout), size)
        self.assertEqual(result.stdout, b"x" * size)
        self.assertEqual(result.stderr, b"")


# ---------------------------------------------------------------------------
# Raw screencap decoding
# ---------------------------------------------------------------------------

RED = (255, 0, 0, 255)
GREEN = (0, 255, 0, 255)
BLUE = (0, 0, 255, 255)
WHITE = (255, 255, 255, 255)


def _rows() -> list[list[tuple[int, int, int, int]]]:
    return [
        [(10, 20, 30, 255), (40, 50, 60, 255)],
        [(70, 80, 90, 255), (100, 110, 120, 255)],
    ]


def _expected_bgr() -> np.ndarray:
    return np.array(
        [
            [[30, 20, 10], [60, 50, 40]],
            [[90, 80, 70], [120, 110, 100]],
        ],
        dtype=np.uint8,
    )


def _capture(
    rows: list[list[tuple[int, int, int, int]]],
    *,
    fmt: int = 1,
    colorspace: int = 1,
    header_size: int = 16,
) -> bytes:
    height = len(rows)
    width = len(rows[0])
    if any(len(row) != width for row in rows):
        raise AssertionError("test pattern must be rectangular")
    header = struct.pack("<III", width, height, fmt)
    if header_size == 16:
        header += struct.pack("<I", colorspace)
    elif header_size != 12:
        raise AssertionError("test header size must be 12 or 16")
    payload = b"".join(bytes(pixel) for row in rows for pixel in row)
    return header + payload


def _header_only(
    width: int,
    height: int,
    *,
    fmt: int = 1,
    colorspace: int = 1,
    header_size: int = 16,
) -> bytes:
    header = struct.pack("<III", width, height, fmt)
    if header_size == 16:
        header += struct.pack("<I", colorspace)
    return header


class RawCaptureTests(unittest.TestCase):
    def test_modern_rgba_known_pattern_and_bgr_byte_order(self) -> None:
        data = _capture(_rows())
        out = decode_raw_screencap(data)

        self.assertEqual(out.shape, (2, 2, 3))
        self.assertEqual(out.dtype, np.uint8)
        self.assertTrue(out.flags.c_contiguous)
        self.assertTrue(out.flags.writeable)
        self.assertTrue(out.flags.owndata)
        np.testing.assert_array_equal(out, _expected_bgr())

    def test_primary_colors_map_to_bgr(self) -> None:
        data = _capture([[RED, GREEN, BLUE, WHITE]])
        out = decode_raw_screencap(data)
        expected = np.array([[[0, 0, 255], [0, 255, 0], [255, 0, 0], [255, 255, 255]]], dtype=np.uint8)
        np.testing.assert_array_equal(out, expected)

    def test_1x1_minimum_capture(self) -> None:
        out = decode_raw_screencap(_capture([[(255, 0, 0, 255)]]))
        self.assertEqual(out.shape, (1, 1, 3))
        np.testing.assert_array_equal(out, np.array([[[0, 0, 255]]], dtype=np.uint8))

    def test_live_measured_1280x720_16_byte_header_shape(self) -> None:
        # The live device was measured with header [1280, 720, 1, 1] and an
        # exact payload of 1280*720*4 bytes.
        width, height = 1280, 720
        data = struct.pack("<IIII", width, height, 1, 1) + bytes(width * height * 4)
        out = decode_raw_screencap(data)
        self.assertEqual(out.shape, (height, width, 3))
        self.assertEqual(out.dtype, np.uint8)
        self.assertTrue(out.flags.c_contiguous)
        self.assertTrue(out.flags.writeable)
        self.assertTrue(out.flags.owndata)

        with self.assertRaises(RawCaptureError):
            decode_raw_screencap(data[:-4], header_bytes=16)

    def test_legacy_12_byte_header_autodetected(self) -> None:
        legacy = _capture(_rows(), header_size=12)
        out = decode_raw_screencap(legacy)
        np.testing.assert_array_equal(out, _expected_bgr())
        np.testing.assert_array_equal(
            decode_raw_screencap(legacy, header_bytes=12), _expected_bgr()
        )

    def test_header_alternatives_and_pinning(self) -> None:
        modern = _capture(_rows())
        legacy = _capture(_rows(), header_size=12)
        expected = _expected_bgr()

        np.testing.assert_array_equal(decode_raw_screencap(modern), expected)
        np.testing.assert_array_equal(
            decode_raw_screencap(modern, header_bytes=16), expected
        )
        np.testing.assert_array_equal(decode_raw_screencap(legacy), expected)
        np.testing.assert_array_equal(
            decode_raw_screencap(legacy, header_bytes=12), expected
        )

        # Pinning the wrong size must be rejected, not silently shifted.
        with self.assertRaises(RawCaptureError):
            decode_raw_screencap(modern, header_bytes=12)
        with self.assertRaises(RawCaptureError):
            decode_raw_screencap(legacy, header_bytes=16)

    def test_rgbx_format2_ignores_alpha_x_byte(self) -> None:
        rows = [[(10, 20, 30, 0), (40, 50, 60, 77)]]
        expected = np.array([[[30, 20, 10], [60, 50, 40]]], dtype=np.uint8)

        modern = decode_raw_screencap(_capture(rows, fmt=2))
        np.testing.assert_array_equal(modern, expected)

        # RGBX has no meaningful alpha byte, so the legacy autodetect path must
        # not apply the opaque-alpha heuristic to it.
        legacy = decode_raw_screencap(_capture(rows, fmt=2, header_size=12))
        np.testing.assert_array_equal(legacy, expected)

    def test_alpha_is_ignored_for_bgr_conversion(self) -> None:
        opaque = _capture([[(1, 2, 3, 255)]])
        transparent = _capture([[(1, 2, 3, 0)]])
        np.testing.assert_array_equal(
            decode_raw_screencap(opaque), decode_raw_screencap(transparent)
        )

    def test_output_is_independent_of_input_buffer(self) -> None:
        data = bytearray(_capture(_rows()))
        out = decode_raw_screencap(data)
        snapshot = out.copy()

        self.assertFalse(np.shares_memory(out, np.frombuffer(data, dtype=np.uint8)))

        data[16:] = b"\x00" * (len(data) - 16)
        np.testing.assert_array_equal(out, snapshot)

        # Mutating the owned output must not write back into the input.
        data2 = bytearray(_capture(_rows()))
        out2 = decode_raw_screencap(data2)
        original_payload = bytes(data2[16:])
        out2[:] = 0
        self.assertEqual(bytes(data2[16:]), original_payload)

    def test_truncated_16_byte_header_by_four_is_rejected_as_ambiguous(self) -> None:
        full = _capture(_rows())
        truncated = full[:-4]

        with self.assertRaises(RawCaptureError) as caught:
            decode_raw_screencap(truncated)
        self.assertIn("ambiguous", str(caught.exception).lower())

        # The caller who knows this device uses a 16-byte header must pin it;
        # the missing four bytes then produce an exact length error.
        with self.assertRaises(RawCaptureError):
            decode_raw_screencap(truncated, header_bytes=16)

    def test_pinned_legacy_trusts_caller_when_alpha_is_not_opaque(self) -> None:
        # A genuine legacy RGBA capture may contain transparent pixels.  The
        # autodetect heuristic refuses it (because it cannot be distinguished
        # from a truncated 16-byte capture); pinning 12 is the explicit escape
        # hatch after initialization validation.
        legacy = _capture([[(10, 20, 30, 0)]], header_size=12)

        with self.assertRaises(RawCaptureError) as caught:
            decode_raw_screencap(legacy)
        self.assertIn("ambiguous", str(caught.exception).lower())

        out = decode_raw_screencap(legacy, header_bytes=12)
        np.testing.assert_array_equal(
            out, np.array([[[30, 20, 10]]], dtype=np.uint8)
        )

    def test_bad_formats_rejected(self) -> None:
        for fmt in (0, 3, 0xFFFFFFFF):
            with self.subTest(fmt=fmt):
                data = _capture(_rows(), fmt=fmt)
                with self.assertRaises(RawCaptureError):
                    decode_raw_screencap(data)
                with self.assertRaises(RawCaptureError):
                    decode_raw_screencap(data, header_bytes=16)

        legacy_bad = _capture(_rows(), fmt=3, header_size=12)
        with self.assertRaises(RawCaptureError):
            decode_raw_screencap(legacy_bad, header_bytes=12)

    def test_unsupported_colorspaces_rejected(self) -> None:
        for colorspace in (0, 2, 0x200, 0xFFFFFFFF):
            with self.subTest(colorspace=colorspace):
                data = _capture(_rows(), colorspace=colorspace)
                with self.assertRaises(RawCaptureError):
                    decode_raw_screencap(data)
                with self.assertRaises(RawCaptureError):
                    decode_raw_screencap(data, header_bytes=16)

    def test_srgb_colorspace_one_accepted(self) -> None:
        out = decode_raw_screencap(_capture(_rows(), colorspace=1), header_bytes=16)
        np.testing.assert_array_equal(out, _expected_bgr())

    def test_bad_lengths_rejected(self) -> None:
        modern = _capture(_rows())
        legacy = _capture(_rows(), header_size=12)
        cases = {
            "empty": b"",
            "eleven_bytes": b"\x00" * 11,
            "fifteen_bytes": modern[:15],
            "header_only_12": modern[:12],
            "truncated_payload_1": modern[:-1],
            "truncated_payload_4": modern[:-4],
            "overlong_1": modern + b"\x00",
            "overlong_4": modern + b"\x00" * 4,
            "overlong_legacy_4": legacy + b"\x00" * 4,
            "truncated_legacy_1": legacy[:-1],
        }
        for name, data in cases.items():
            with self.subTest(case=name):
                with self.assertRaises(RawCaptureError):
                    decode_raw_screencap(data)

    def test_bad_dimensions_rejected_before_payload_allocation(self) -> None:
        cases = {
            "zero_width": (_header_only(0, 1), "width"),
            "zero_height": (_header_only(1, 0), "height"),
            "width_8193": (_header_only(8193, 1), "width"),
            "height_8193": (_header_only(1, 8193), "height"),
            "pixel_cap": (_header_only(4097, 4097), "pixel count"),
            "square_8192": (_header_only(8192, 8192), "pixel count"),
            "huge_uint32": (_header_only(0xFFFFFFFF, 1), "width"),
        }
        for name, (data, needle) in cases.items():
            with self.subTest(case=name):
                started = time.monotonic()
                with self.assertRaises(RawCaptureError) as caught:
                    decode_raw_screencap(data)
                self.assertIn(needle, str(caught.exception).lower())
                self.assertLess(time.monotonic() - started, 2.0)

    def test_big_endian_header_is_not_accepted_as_little_endian(self) -> None:
        payload = b"\x00" * (2 * 2 * 4)
        big_endian = struct.pack(">III", 2, 2, 1) + struct.pack("<I", 1) + payload
        with self.assertRaises(RawCaptureError) as caught:
            decode_raw_screencap(big_endian)
        self.assertIn("width", str(caught.exception).lower())

    def test_header_bytes_parameter_validation(self) -> None:
        data = _capture(_rows())
        for bad in (0, 11, 13, 20, True, 12.0, "12"):
            with self.subTest(header_bytes=bad):
                with self.assertRaises(RawCaptureError):
                    decode_raw_screencap(data, header_bytes=bad)

    def test_non_bytes_input_rejected(self) -> None:
        with self.assertRaises(RawCaptureError):
            decode_raw_screencap("not bytes")  # type: ignore[arg-type]
        with self.assertRaises(RawCaptureError):
            decode_raw_screencap(object())  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main(verbosity=2)
