"""Bounded subprocess transport for ADB commands.

This module is intentionally standalone: it does not import ``autostory`` so
the helpers and their tests can be copied into any directory and exercised in
isolation.

Design rules (see REPORT.md for the acceptance evidence):

* ``shell=False`` and no shell interpolation.
* ``stdin`` is ``DEVNULL``; stdout/stderr are pipes and are always read as
  bytes.
* ``Popen.communicate`` is given a short bounded timeout on every iteration so
  a cancellation request (``check_stop``) is observed promptly.  The overall
  command deadline is monotonic and independent of how many poll iterations
  happen.
* On *any* ``BaseException`` only the child created by this call is
  killed/reaped; pipes owned by this call are closed.  No process enumeration,
  no ``adb kill-server`` and no other global cleanup is performed.
* Cleanup is bounded and never replaces the original exception.
"""

from __future__ import annotations

import math
import subprocess
import threading
import time
from typing import Any, Callable, Sequence

__all__ = ["run_command"]

# Bounded cleanup budget.  These are deliberately small: cleanup must never
# turn a cancellation/timeout into an indefinite wait.
_CLEANUP_WAIT_SECONDS = 0.25
_CLEANUP_CLOSE_SECONDS = 0.25


def _positive_seconds(name: str, value: Any) -> float:
    """Return *value* as a finite, strictly positive float or raise ValueError."""

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite positive number, got {value!r}")
    result = float(value)
    if not math.isfinite(result) or result <= 0.0:
        raise ValueError(f"{name} must be a finite positive number, got {value!r}")
    return result


def _close_quietly(stream: Any) -> None:
    try:
        stream.close()
    except BaseException:
        pass


def _close_bounded(stream: Any, timeout: float = _CLEANUP_CLOSE_SECONDS) -> None:
    """Close ``stream`` without ever blocking cleanup indefinitely.

    ``BufferedReader.close`` can wait for a reader thread that is blocked on a
    pipe inherited by a grandchild.  We therefore hand the close to a daemon
    thread and only wait a bounded amount of time for it.  In the normal case
    (the child has been killed/reaped) the close completes immediately.
    """

    if stream is None:
        return
    try:
        if stream.closed:
            return
    except BaseException:
        return

    try:
        closer = threading.Thread(
            target=_close_quietly, args=(stream,), name="adb-transport-close", daemon=True
        )
        closer.start()
        closer.join(timeout)
    except BaseException:
        # If a helper thread could not be started, do not risk a synchronous
        # close that could block forever.  The stream will be reclaimed with
        # the process object.
        pass


def _cleanup_created_child(proc: subprocess.Popen) -> None:
    """Kill/reap/close only *proc*.  This function never raises."""

    # Terminate only the direct child we created.  Never enumerate processes
    # and never touch an ADB server or any other process.
    try:
        if proc.poll() is None:
            proc.kill()
    except BaseException:
        pass

    # Bounded reap.  A process that ignores TerminateProcess (or a platform
    # race) must not hold the caller hostage.
    try:
        proc.wait(timeout=_CLEANUP_WAIT_SECONDS)
    except BaseException:
        pass

    for stream in (proc.stdout, proc.stderr):
        _close_bounded(stream)


def run_command(
    args: Sequence[str],
    *,
    check_stop: Callable[[], Any],
    timeout: float = 20,
    poll_interval: float = 0.1,
    creationflags: int = 0,
) -> subprocess.CompletedProcess:
    """Run ``args`` with a bounded overall deadline and prompt cancellation.

    Parameters
    ----------
    args:
        Argument sequence passed directly to ``Popen`` (``shell=False``).
    check_stop:
        Callable invoked before launch, on every poll iteration, and once more
        after a successful ``communicate`` and before returning.  It may raise
        to cancel the command (``autostory.common.check_stop`` raises
        ``InterruptedError``).
    timeout:
        Finite, strictly positive overall wall-clock budget in seconds,
        measured with ``time.monotonic``.
    poll_interval:
        Finite, strictly positive maximum blocking time for one
        ``communicate`` call.  The command is checked for cancellation at least
        this often.
    creationflags:
        Passed through to ``Popen`` (``CREATE_NO_WINDOW`` on Windows).

    Returns
    -------
    subprocess.CompletedProcess
        ``stdout``/``stderr`` are bytes; a non-zero return code is preserved
        (it is data, not an exception).

    Raises
    ------
    subprocess.TimeoutExpired
        When the overall deadline expires.  ``output``/``stderr`` contain the
        captured partial bytes when the running Python implementation exposes
        them (see REPORT.md for the Windows limitation).
    BaseException
        Any exception raised by ``check_stop`` or by the subprocess layer is
        propagated unchanged after the created child is cleaned up.
    """

    timeout = _positive_seconds("timeout", timeout)
    poll_interval = _positive_seconds("poll_interval", poll_interval)

    if not callable(check_stop):
        raise TypeError("check_stop must be callable")
    if isinstance(args, (str, bytes, bytearray)):
        raise ValueError("args must be a sequence of arguments, not a single string/bytes object")

    # The overall deadline starts just before launch and includes process
    # creation, polling and any output drain performed by communicate().
    deadline = time.monotonic() + timeout

    # Never launch a child after a stop was already requested.
    check_stop()

    proc = subprocess.Popen(
        args,
        shell=False,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=creationflags,
    )

    captured_stdout = None
    captured_stderr = None

    try:
        while True:
            # Check at the top of each iteration, even if the deadline already
            # expired, so cancellation wins over a would-be timeout.
            check_stop()

            remaining = deadline - time.monotonic()
            if remaining <= 0.0:
                raise subprocess.TimeoutExpired(
                    proc.args,
                    timeout,
                    output=captured_stdout,
                    stderr=captured_stderr,
                )

            try:
                stdout, stderr = proc.communicate(timeout=min(poll_interval, remaining))
            except subprocess.TimeoutExpired as exc:
                # ``TimeoutExpired`` carries the *cumulative* output captured
                # so far on implementations that expose it (e.g. POSIX).
                # Store (replace), never append: repeatedly timing out must not
                # duplicate bytes.  On Windows 3.12 these attributes are None
                # while the child is still running.
                exc_stdout = getattr(exc, "stdout", None)
                exc_stderr = getattr(exc, "stderr", None)
                if exc_stdout is not None:
                    captured_stdout = exc_stdout
                if exc_stderr is not None:
                    captured_stderr = exc_stderr
                continue

            # A stop that lands after the child finished must still be
            # honoured; the cleanup path below reaps the already-finished
            # child rather than launching anything else.
            check_stop()

            if time.monotonic() > deadline:
                raise subprocess.TimeoutExpired(proc.args, timeout, output=stdout, stderr=stderr)

            return subprocess.CompletedProcess(
                proc.args,
                proc.returncode,
                stdout if stdout is not None else b"",
                stderr if stderr is not None else b"",
            )
    except BaseException:
        # Includes stop exceptions, TimeoutExpired, OSError from communicate,
        # KeyboardInterrupt and SystemExit.  Cleanup is bounded and swallows
        # its own failures so the original exception is never replaced.
        _cleanup_created_child(proc)
        raise
