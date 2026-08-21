# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The OCX Authors

"""Contract tests for `ocx_sdk._process` (C-010, S-004, S-006).

Named rows from the design's mechanism matrix live here:
`test_leading_dash_rejected`, `test_double_dash_emitted`,
`test_timeout_kill_ladder`, `test_sigint_forwarding`,
`test_pump_captures_all_lines`, and the log/error half of
`test_secrets_absent_from_repr_logs_errors`.

The kill ladder and the signal handling run against a fake `Popen` and an
injected clock, so the assertions pin the exact call sequence instead of
hoping a real child dies on schedule. Three tests do spawn a real child —
they are the ones where a fake would prove nothing: that two pump threads
survive more output than a pipe buffer holds, and that the ladder actually
reaps a process that ignores nothing.
"""

from __future__ import annotations

import asyncio
import io
import logging
import signal
import subprocess
import sys
import threading
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path
from typing import Any

import pytest

from ocx_sdk import _process
from ocx_sdk._errors import AuthError, DataError, OcxProcessError, OcxTimeoutError, TempFailError, UsageError
from ocx_sdk._process import Completed, compose_argv, run_command, run_command_async, spawn, spawn_async
from ocx_sdk._types import RetryPolicy

_TOKEN = "ghp_supersecret"

_posix_only = pytest.mark.skipif(
    sys.platform == "win32",
    reason="POSIX process groups: the ladder signals a group, which Windows has no equivalent for (design §10)",
)
"""Guard for the tests that drive signal handling directly. Everything that
takes the `killpg` fixture is guarded by the fixture itself."""


def _interrupt(signum: int, frame: Any) -> None:
    """Stand in for `signal.default_int_handler`."""
    raise KeyboardInterrupt


def _redact_token(text: str) -> str:
    """Stand in for `build_spawn_env`'s exact-string scrubber."""
    return text.replace(_TOKEN, "***")


class _FakePopen:
    """A `subprocess.Popen` stand-in that scripts its waits and records the ladder.

    `waits` is the outcome of each successive `wait()`: an exit status, the
    string `timeout` to raise `TimeoutExpired` the way a real wait does, or
    `interrupt` to raise `KeyboardInterrupt` into the caller.

    Liveness is modelled: `returncode` stays `None` until a wait returns one,
    which is what the SDK checks before it signals a process group.
    """

    def __init__(
        self,
        *,
        exit_code: int = 0,
        stdout: str = "",
        stderr: str = "",
        waits: Sequence[int | str] | None = None,
        pid: int = 4321,
        stdin_broken: bool = False,
        stuck_stdout: _StuckStream | None = None,
    ) -> None:
        self._stdin_broken = stdin_broken
        self._stuck_stdout = stuck_stdout
        self.on_wait: Callable[[], None] | None = None
        self._exit_code = exit_code
        self._stdout_text = stdout
        self._stderr_text = stderr
        self._waits = list(waits) if waits is not None else [exit_code]
        self.pid = pid
        self.returncode: int | None = None
        self.argv: list[str] = []
        self.kwargs: dict[str, Any] = {}
        self.wait_timeouts: list[float | None] = []
        self.signals: list[str] = []
        self.stdout: io.StringIO | _StuckStream | None = None
        self.stderr: io.StringIO | None = None
        self.stdin: _FakeStdin | None = None

    def opened(self, argv: Sequence[str], kwargs: dict[str, Any]) -> _FakePopen:
        self.argv = list(argv)
        self.kwargs = kwargs
        if kwargs.get("stdout") == subprocess.PIPE:
            self.stdout = self._stuck_stdout or io.StringIO(self._stdout_text)
        if kwargs.get("stderr") == subprocess.PIPE:
            self.stderr = io.StringIO(self._stderr_text)
        if kwargs.get("stdin") == subprocess.PIPE:
            self.stdin = _FakeStdin(broken=self._stdin_broken)
        return self

    def wait(self, timeout: float | None = None) -> int:
        self.wait_timeouts.append(timeout)
        if self.on_wait is not None:
            hook, self.on_wait = self.on_wait, None
            hook()
        outcome = self._waits.pop(0) if self._waits else self._exit_code
        if outcome == "timeout":
            raise subprocess.TimeoutExpired(self.argv, timeout or 0.0)
        if outcome == "interrupt":
            raise KeyboardInterrupt
        self.returncode = int(outcome)
        return self.returncode

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.signals.append("terminate")
        self.returncode = -15

    def kill(self) -> None:
        self.signals.append("kill")
        self.returncode = -9


class _StuckStream:
    """A pipe a grandchild is holding open: iteration never reaches EOF."""

    def __init__(self) -> None:
        self.release = threading.Event()

    def __enter__(self) -> _StuckStream:
        return self

    def __exit__(self, *_exc: object) -> bool:
        return False

    def __iter__(self) -> Iterator[str]:
        self.release.wait(5)
        return iter(())


class _FakeStdin:
    """A child's stdin pipe: records what was written and whether it was closed."""

    def __init__(self, *, broken: bool = False) -> None:
        self.text = ""
        self.closed = False
        self._broken = broken

    def write(self, text: str) -> int:
        if self._broken:
            raise BrokenPipeError(32, "Broken pipe")
        self.text += text
        return len(text)

    def close(self) -> None:
        self.closed = True


def _factory(*procs: _FakePopen) -> Any:
    """Return a `popen_factory` handing out `procs` in spawn order."""
    queue = list(procs)

    def make(argv: Sequence[str], **kwargs: Any) -> _FakePopen:
        return queue.pop(0).opened(argv, kwargs)

    return make


class _FakeReader:
    """An `asyncio.StreamReader` stand-in over a fixed byte string."""

    def __init__(self, data: bytes, *, suspend: bool = False, error: Exception | None = None) -> None:
        self._data = data
        self._suspend = suspend
        self._error = error

    async def read(self, n: int = -1) -> bytes:
        if self._error is not None:
            raise self._error
        if not self._data and self._suspend:
            # A pipe still open with nothing on it: the read a cancellation
            # has to interrupt mid-flight.
            await asyncio.Event().wait()
        chunk = self._data if n < 0 else self._data[:n]
        self._data = self._data[len(chunk) :]
        return chunk


class _FakeWriter:
    """An `asyncio.StreamWriter` stand-in over the child's stdin."""

    def __init__(self, *, broken: bool = False) -> None:
        self.data = b""
        self.closed = False
        self._broken = broken

    def write(self, data: bytes) -> None:
        self.data += data

    async def drain(self) -> None:
        if self._broken:
            raise ConnectionResetError(104, "Connection reset by peer")

    def close(self) -> None:
        self.closed = True


class _FakeProcess:
    """An `asyncio.subprocess.Process` stand-in.

    `hangs` is how many leading `wait()` calls block forever — the shape of a
    child that outlives its deadline. Later waits return, the way a real child
    does once the ladder has signalled it.
    """

    def __init__(
        self,
        *,
        exit_code: int = 0,
        stdout: bytes = b"",
        stderr: bytes = b"",
        hangs: int = 0,
        pid: int = 8899,
        stdin_broken: bool = False,
        suspend: bool = False,
        read_error: Exception | None = None,
    ) -> None:
        self.stdin = _FakeWriter(broken=stdin_broken)
        self._exit_code = exit_code
        self._hangs = hangs
        self.pid = pid
        self.returncode: int | None = None
        self.stdout = _FakeReader(stdout, error=read_error)
        self.stderr = _FakeReader(stderr, suspend=suspend)
        self.argv: list[str] = []
        self.kwargs: dict[str, Any] = {}
        self.signals: list[str] = []

    async def wait(self) -> int:
        if self._hangs > 0:
            self._hangs -= 1
            await asyncio.Event().wait()
        self.returncode = self._exit_code
        return self._exit_code

    def terminate(self) -> None:
        self.signals.append("terminate")

    def kill(self) -> None:
        self.signals.append("kill")


def _async_factory(*procs: _FakeProcess) -> Any:
    """Return an `exec_factory` handing out `procs` in spawn order."""
    queue = list(procs)

    async def make(*argv: str, **kwargs: Any) -> _FakeProcess:
        proc = queue.pop(0)
        proc.argv = list(argv)
        proc.kwargs = kwargs
        return proc

    return make


@pytest.fixture
def killpg(monkeypatch: pytest.MonkeyPatch) -> list[tuple[int, int]]:
    """Record every process-group signal instead of sending it.

    Skips the whole test on Windows rather than repeating a decorator on each
    one: every user of this fixture is POSIX-only by construction, since
    Windows has no process group for the ladder to signal.
    """
    if sys.platform == "win32":
        pytest.skip("POSIX process groups; Windows has no killpg (design §10)")
    sent: list[tuple[int, int]] = []
    monkeypatch.setattr(_process.os, "killpg", lambda pid, sig: sent.append((pid, sig)))
    return sent


# --- compose_argv (C-010 argv composition, CWE-88) -------------------------


def test_compose_argv_puts_global_flags_before_the_command() -> None:
    argv = compose_argv("/bin/ocx", ["--project", "/w", "--color", "never"], ["status"])

    assert argv == ("/bin/ocx", "--project", "/w", "--color", "never", "status")


def test_leading_dash_rejected() -> None:
    with pytest.raises(ValueError, match="may not start with"):
        compose_argv("/bin/ocx", [], ["package", "install"], ["--privileged"])


def test_leading_dash_rejection_names_every_offender() -> None:
    with pytest.raises(ValueError, match=r"'-rf'.*'--force'"):
        compose_argv("/bin/ocx", [], ["package", "install"], ["ok", "-rf", "--force"])


def test_double_dash_emitted() -> None:
    argv = compose_argv("/bin/ocx", ["--project", "/w"], ["run"], child=["task", "verify"])

    assert argv == ("/bin/ocx", "--project", "/w", "run", "--", "task", "verify")


def test_compose_argv_allows_a_leading_dash_behind_the_double_dash() -> None:
    argv = compose_argv("/bin/ocx", [], ["run"], child=["task", "--force"])

    assert argv == ("/bin/ocx", "run", "--", "task", "--force")


def test_compose_argv_omits_the_double_dash_when_there_is_no_child_group() -> None:
    argv = compose_argv("/bin/ocx", [], ["lock"])

    assert argv == ("/bin/ocx", "lock")


# --- run_command: capture, exit codes, check ------------------------------


def test_run_command_returns_the_exit_code_and_both_streams() -> None:
    proc = _FakePopen(exit_code=0, stdout='{"ok": true}\n', stderr="resolving\ndone\n")

    done = run_command(["ocx", "status"], {}, popen_factory=_factory(proc))

    assert done == Completed(0, '{"ok": true}\n', "resolving\ndone\n")


@pytest.mark.parametrize(
    ("exit_code", "expected"),
    [
        pytest.param(64, UsageError, id="usage-64"),
        pytest.param(75, TempFailError, id="temp-fail-75"),
        pytest.param(80, AuthError, id="auth-80"),
        pytest.param(1, OcxProcessError, id="generic-failure-1"),
        pytest.param(137, OcxProcessError, id="signal-killed-137"),
    ],
)
def test_run_command_raises_the_error_the_exit_code_maps_to(exit_code: int, expected: type[OcxProcessError]) -> None:
    proc = _FakePopen(exit_code=exit_code, stderr="boom\n")

    with pytest.raises(expected, match="exited") as caught:
        run_command(["ocx", "status"], {}, popen_factory=_factory(proc))

    assert type(caught.value) is expected
    assert caught.value.exit_code == exit_code
    assert caught.value.stderr == "boom\n"
    assert caught.value.argv == ("ocx", "status")


def test_run_command_returns_the_failure_instead_of_raising_when_check_is_off() -> None:
    proc = _FakePopen(exit_code=79, stdout="{}\n", stderr="not found\n")

    done = run_command(["ocx", "info", "x"], {}, check=False, popen_factory=_factory(proc))

    assert done == Completed(79, "{}\n", "not found\n")


def test_run_command_snapshots_the_env_and_forwards_the_working_directory(tmp_path: Path) -> None:
    proc = _FakePopen()
    source = {"PATH": "/usr/bin"}

    run_command(["ocx", "status"], source, cwd=tmp_path, popen_factory=_factory(proc))

    assert proc.kwargs["env"] == source
    assert proc.kwargs["env"] is not source
    assert proc.kwargs["cwd"] == tmp_path
    assert proc.kwargs["shell"] is False


def test_run_command_without_capture_leaves_both_streams_inherited() -> None:
    proc = _FakePopen(stdout="ignored", stderr="ignored")

    done = run_command(["ocx", "run"], {}, capture=False, popen_factory=_factory(proc))

    assert done == Completed(0, "", "")
    assert proc.kwargs["stdout"] is None
    assert proc.kwargs["stderr"] is None


def test_run_command_starts_the_child_in_its_own_session_on_posix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_process, "_POSIX", True)
    proc = _FakePopen()

    run_command(["ocx", "status"], {}, popen_factory=_factory(proc))

    assert proc.kwargs["start_new_session"] is True


# --- the stderr pump (design §12 layer 1 and 2) ---------------------------


def test_run_command_streams_every_stderr_line_to_on_log() -> None:
    proc = _FakePopen(stderr="resolving uv\nverifying digest\ndone\n")
    lines: list[str] = []

    run_command(["ocx", "pull"], {}, on_log=lines.append, popen_factory=_factory(proc))

    assert lines == ["resolving uv", "verifying digest", "done"]


def test_run_command_logs_every_stderr_line_at_debug(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG, logger="ocx_sdk")
    proc = _FakePopen(stderr="resolving uv\ndone\n")

    run_command(["ocx", "pull"], {}, popen_factory=_factory(proc))

    pumped = [rec.getMessage() for rec in caplog.records if rec.name == "ocx_sdk.process"]
    assert pumped == ["resolving uv", "done"]


def test_run_command_logs_the_argv_and_the_exit_as_sdk_events(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG, logger="ocx_sdk")
    clock = iter([10.0, 10.25]).__next__
    proc = _FakePopen(exit_code=0)

    run_command(["ocx", "status"], {}, clock=clock, popen_factory=_factory(proc))

    events = [rec.getMessage() for rec in caplog.records if rec.name == "ocx_sdk"]
    assert events == ["run: ocx status", "exit 0 in 0.250s: ocx status"]


def test_pump_captures_all_lines() -> None:
    """A real child, more output than a pipe buffer holds, on both streams."""
    script = (
        "import sys\n"
        "for i in range(2000):\n"
        "    sys.stdout.write(f'out {i:035d}\\n')\n"
        "    sys.stderr.write(f'err {i:035d}\\n')\n"
    )

    done = run_command([sys.executable, "-c", script], {}, timeout=30)

    assert done.exit_code == 0
    assert done.stdout.splitlines() == [f"out {i:035d}" for i in range(2000)]
    assert done.stderr.splitlines() == [f"err {i:035d}" for i in range(2000)]


# --- S-006: secrets never reach a log, a callback, or an error ------------


def test_secrets_absent_from_logs_errors_and_on_log(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG, logger="ocx_sdk")
    proc = _FakePopen(exit_code=80, stderr=f"authenticating with {_TOKEN}\n")
    lines: list[str] = []

    with pytest.raises(AuthError) as caught:
        run_command(
            ["ocx", "login", "--token", _TOKEN],
            {"OCX_AUTH_X_TOKEN": _TOKEN},
            redact=_redact_token,
            on_log=lines.append,
            popen_factory=_factory(proc),
        )

    assert _TOKEN not in caplog.text
    assert _TOKEN not in "".join(lines)
    assert _TOKEN not in str(caught.value)
    assert _TOKEN not in caught.value.stderr
    assert _TOKEN not in " ".join(caught.value.argv)
    assert caught.value.argv == ("ocx", "login", "--token", "***")


def test_run_command_still_gives_the_child_the_unredacted_env_and_argv() -> None:
    proc = _FakePopen()

    run_command(["ocx", "login", "--token", _TOKEN], {"T": _TOKEN}, redact=_redact_token, popen_factory=_factory(proc))

    assert proc.argv == ["ocx", "login", "--token", _TOKEN]
    assert proc.kwargs["env"] == {"T": _TOKEN}


def test_timeout_error_carries_redacted_partial_stderr(killpg: list[tuple[int, int]]) -> None:
    proc = _FakePopen(stderr=f"using {_TOKEN}\n", waits=["timeout", -15])

    with pytest.raises(OcxTimeoutError) as caught:
        run_command(["ocx", "pull", _TOKEN], {}, timeout=0.5, redact=_redact_token, popen_factory=_factory(proc))

    assert caught.value.stderr == "using ***\n"
    assert caught.value.argv == ("ocx", "pull", "***")
    assert caught.value.timeout == 0.5
    assert killpg == [(4321, signal.SIGTERM)]


# --- S-004: the timeout kill ladder --------------------------------------


def test_timeout_kill_ladder(killpg: list[tuple[int, int]]) -> None:
    proc = _FakePopen(pid=555, waits=["timeout", "timeout", -9])

    with pytest.raises(OcxTimeoutError, match=r"timed out after 0\.5s"):
        run_command(["ocx", "pull"], {}, timeout=0.5, kill_grace=5.0, popen_factory=_factory(proc))

    assert killpg == [(555, signal.SIGTERM), (555, signal.SIGKILL)]
    assert proc.wait_timeouts == [0.5, 5.0, None]


def test_timeout_stops_at_sigterm_when_the_child_goes_quietly(killpg: list[tuple[int, int]]) -> None:
    proc = _FakePopen(pid=555, waits=["timeout", -15])

    with pytest.raises(OcxTimeoutError):
        run_command(["ocx", "pull"], {}, timeout=0.5, popen_factory=_factory(proc))

    assert killpg == [(555, signal.SIGTERM)]
    assert proc.wait_timeouts == [0.5, 5.0]


@_posix_only
def test_timeout_ladder_tolerates_a_child_that_already_exited(monkeypatch: pytest.MonkeyPatch) -> None:
    def gone(pid: int, sig: int) -> None:
        raise ProcessLookupError(pid, sig)

    monkeypatch.setattr(_process.os, "killpg", gone)
    proc = _FakePopen(waits=["timeout", -15])

    with pytest.raises(OcxTimeoutError):
        run_command(["ocx", "pull"], {}, timeout=0.5, popen_factory=_factory(proc))


def test_timeout_kills_a_real_child_that_would_outlive_its_deadline() -> None:
    spawned: list[subprocess.Popen[str]] = []

    def recording(argv: Sequence[str], **kwargs: Any) -> subprocess.Popen[str]:
        proc = subprocess.Popen(argv, **kwargs)
        spawned.append(proc)
        return proc

    with pytest.raises(OcxTimeoutError):
        run_command([sys.executable, "-c", "import time; time.sleep(60)"], {}, timeout=0.3, popen_factory=recording)

    assert spawned[0].poll() is not None


# --- SIGINT forwarding on passthrough runs -------------------------------


def test_sigint_forwarding(monkeypatch: pytest.MonkeyPatch, killpg: list[tuple[int, int]]) -> None:
    """A Ctrl-C mid-run reaches the child AND still interrupts the caller."""
    installed: list[Any] = []
    monkeypatch.setattr(_process.signal, "signal", lambda signum, handler: installed.append(handler))
    monkeypatch.setattr(_process.signal, "getsignal", lambda signum: _interrupt)
    proc = _FakePopen(pid=777)
    proc.on_wait = lambda: installed[0](signal.SIGINT, None)

    with pytest.raises(KeyboardInterrupt):
        run_command(["ocx", "run"], {}, capture=False, popen_factory=_factory(proc))

    assert killpg == [(777, signal.SIGINT), (777, signal.SIGTERM)]
    assert proc.returncode is not None
    assert installed[-1] is _interrupt


def test_sigint_forwarding_skips_a_child_that_has_already_been_reaped(
    monkeypatch: pytest.MonkeyPatch, killpg: list[tuple[int, int]]
) -> None:
    """Signalling a reaped pid could hit whoever the OS gave it to next."""
    installed: list[Any] = []
    monkeypatch.setattr(_process.signal, "signal", lambda signum, handler: installed.append(handler))
    monkeypatch.setattr(_process.signal, "getsignal", lambda signum: signal.SIG_IGN)
    proc = _FakePopen(pid=777)

    run_command(["ocx", "run"], {}, capture=False, popen_factory=_factory(proc))
    installed[0](signal.SIGINT, None)

    assert killpg == []


@_posix_only
def test_sigint_handler_restored_even_when_the_child_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    installed: list[Any] = []
    monkeypatch.setattr(_process.signal, "signal", lambda signum, handler: installed.append(handler))
    proc = _FakePopen(exit_code=64)

    with pytest.raises(UsageError):
        run_command(["ocx", "run"], {}, capture=False, popen_factory=_factory(proc))

    assert len(installed) == 2


@_posix_only
def test_sigint_forwarding_skipped_off_the_main_thread(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(signum: int, handler: Any) -> Any:
        raise ValueError("signal only works in main thread")

    monkeypatch.setattr(_process.signal, "signal", refuse)
    proc = _FakePopen()

    done = run_command(["ocx", "run"], {}, capture=False, popen_factory=_factory(proc))

    assert done.exit_code == 0


def test_run_command_captures_from_a_worker_thread() -> None:
    proc = _FakePopen(stdout="ok\n")
    results: list[Completed] = []

    worker = threading.Thread(target=lambda: results.append(run_command(["ocx"], {}, popen_factory=_factory(proc))))
    worker.start()
    worker.join()

    assert results == [Completed(0, "ok\n", "")]


# --- S-004: retry hookup --------------------------------------------------


def test_run_command_retries_a_transient_exit_and_then_succeeds() -> None:
    slept: list[float] = []
    policy = RetryPolicy(attempts=3, jitter=False, sleep=slept.append)
    procs = (_FakePopen(exit_code=75), _FakePopen(exit_code=75), _FakePopen(exit_code=0, stdout="ok\n"))

    done = run_command(["ocx", "pull"], {}, retry=policy, popen_factory=_factory(*procs))

    assert done == Completed(0, "ok\n", "")
    assert slept == [1.0, 2.0]


def test_run_command_reports_attempts_on_the_final_transient_failure() -> None:
    policy = RetryPolicy(attempts=3, jitter=False, sleep=lambda _delay: None)
    procs = (_FakePopen(exit_code=75), _FakePopen(exit_code=75), _FakePopen(exit_code=75))

    with pytest.raises(TempFailError, match="after 3 attempts") as caught:
        run_command(["ocx", "pull"], {}, retry=policy, popen_factory=_factory(*procs))

    assert caught.value.attempts == 3


def test_run_command_does_not_retry_an_exit_code_outside_the_policy() -> None:
    policy = RetryPolicy(attempts=3, jitter=False, sleep=lambda _delay: None)
    proc = _FakePopen(exit_code=80)

    with pytest.raises(AuthError):
        run_command(["ocx", "login"], {}, retry=policy, popen_factory=_factory(proc))

    assert proc.wait_timeouts == [None]


def test_run_command_never_retries_a_timeout(killpg: list[tuple[int, int]]) -> None:
    policy = RetryPolicy(attempts=3, jitter=False, sleep=lambda _delay: None)
    proc = _FakePopen(waits=["timeout", -15])

    with pytest.raises(OcxTimeoutError):
        run_command(["ocx", "pull"], {}, timeout=0.5, retry=policy, popen_factory=_factory(proc))

    assert killpg == [(4321, signal.SIGTERM)]


def test_run_command_returns_the_last_attempt_when_retries_run_out_without_check() -> None:
    policy = RetryPolicy(attempts=2, jitter=False, sleep=lambda _delay: None)
    procs = (_FakePopen(exit_code=75, stdout="one\n"), _FakePopen(exit_code=75, stdout="two\n"))

    done = run_command(["ocx", "pull"], {}, retry=policy, check=False, popen_factory=_factory(*procs))

    assert done == Completed(75, "two\n", "")


# --- spawn: the caller owns the handle -----------------------------------


def test_spawn_returns_the_live_handle_without_waiting() -> None:
    proc = _FakePopen()

    handle = spawn(["ocx", "run"], {"PATH": "/usr/bin"}, popen_factory=_factory(proc))

    assert handle is proc
    assert proc.wait_timeouts == []
    assert proc.kwargs["env"] == {"PATH": "/usr/bin"}
    assert proc.kwargs["shell"] is False


@pytest.mark.parametrize(
    "rejected",
    [
        pytest.param("args", id="args-would-replace-the-argv"),
        pytest.param("shell", id="shell-breaks-the-invariant"),
        pytest.param("executable", id="executable-would-replace-the-exe"),
    ],
)
def test_spawn_rejects_the_kwargs_that_would_defeat_the_invariants(rejected: str) -> None:
    smuggled: dict[str, Any] = {rejected: "x"}

    with pytest.raises(ValueError, match=rejected):
        spawn(["ocx", "run"], {}, popen_factory=_factory(_FakePopen()), **smuggled)


def test_spawn_forwards_the_kwargs_the_caller_is_allowed_to_set() -> None:
    proc = _FakePopen()

    spawn(["ocx", "run"], {}, popen_factory=_factory(proc), stdout=subprocess.PIPE, bufsize=1)

    assert proc.kwargs["stdout"] == subprocess.PIPE
    assert proc.kwargs["bufsize"] == 1


def test_spawn_logs_the_redacted_argv(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG, logger="ocx_sdk")

    spawn(["ocx", "login", _TOKEN], {}, redact=_redact_token, popen_factory=_factory(_FakePopen()))

    assert _TOKEN not in caplog.text
    assert "spawn: ocx login '***'" in caplog.text


# --- the async twins ------------------------------------------------------


async def test_run_command_async_returns_the_exit_code_and_both_streams() -> None:
    proc = _FakeProcess(stdout=b'{"ok": true}\n', stderr=b"resolving\n")

    done = await run_command_async(["ocx", "status"], {}, exec_factory=_async_factory(proc))

    assert done == Completed(0, '{"ok": true}\n', "resolving\n")
    assert proc.argv == ["ocx", "status"]


async def test_run_command_async_rejects_on_log() -> None:
    with pytest.raises(ValueError, match="on_log is unsupported on async paths"):
        await run_command_async(["ocx", "status"], {}, on_log=lambda _line: None)


async def test_run_command_async_raises_the_error_the_exit_code_maps_to() -> None:
    proc = _FakeProcess(exit_code=64, stderr=b"bad flag\n")

    with pytest.raises(UsageError, match="exited 64") as caught:
        await run_command_async(["ocx", "status"], {}, exec_factory=_async_factory(proc))

    assert caught.value.stderr == "bad flag\n"


async def test_run_command_async_returns_the_failure_when_check_is_off() -> None:
    proc = _FakeProcess(exit_code=79, stdout=b"{}\n")

    done = await run_command_async(["ocx", "info"], {}, check=False, exec_factory=_async_factory(proc))

    assert done == Completed(79, "{}\n", "")


async def test_run_command_async_redacts_the_captured_stderr() -> None:
    proc = _FakeProcess(stderr=f"using {_TOKEN}\n".encode())

    done = await run_command_async(["ocx", "pull"], {}, redact=_redact_token, exec_factory=_async_factory(proc))

    assert done.stderr == "using ***\n"


async def test_run_command_async_without_capture_leaves_both_streams_inherited() -> None:
    proc = _FakeProcess(stdout=b"ignored", stderr=b"ignored")

    done = await run_command_async(["ocx", "run"], {}, capture=False, exec_factory=_async_factory(proc))

    assert done == Completed(0, "", "")
    assert proc.kwargs["stdout"] is None


async def test_run_command_async_timeout_terminates_the_child_and_keeps_partial_stderr(
    killpg: list[tuple[int, int]],
) -> None:
    proc = _FakeProcess(pid=999, stderr=b"resolving\n", hangs=1)

    with pytest.raises(OcxTimeoutError, match=r"timed out after 0\.01s") as caught:
        await run_command_async(["ocx", "pull"], {}, timeout=0.01, exec_factory=_async_factory(proc))

    assert caught.value.stderr == "resolving\n"
    assert killpg == [(999, signal.SIGTERM)]


async def test_run_command_async_timeout_escalates_to_a_kill(killpg: list[tuple[int, int]]) -> None:
    proc = _FakeProcess(pid=999, hangs=2)

    with pytest.raises(OcxTimeoutError):
        await run_command_async(["ocx", "pull"], {}, timeout=0.01, kill_grace=0.01, exec_factory=_async_factory(proc))

    assert killpg == [(999, signal.SIGTERM), (999, signal.SIGKILL)]


async def test_run_command_async_cancellation_terminates_the_child(killpg: list[tuple[int, int]]) -> None:
    proc = _FakeProcess(pid=999, hangs=5)
    task = asyncio.create_task(run_command_async(["ocx", "pull"], {}, exec_factory=_async_factory(proc)))
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert killpg == [(999, signal.SIGTERM)]


async def test_run_command_async_retries_a_transient_exit() -> None:
    policy = RetryPolicy(attempts=3, backoff=0.0, jitter=False)
    procs = (_FakeProcess(exit_code=75), _FakeProcess(exit_code=0, stdout=b"ok\n"))

    done = await run_command_async(["ocx", "pull"], {}, retry=policy, exec_factory=_async_factory(*procs))

    assert done == Completed(0, "ok\n", "")


async def test_run_command_async_logs_stderr_lines_and_the_exit(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG, logger="ocx_sdk")
    clock = iter([4.0, 4.5]).__next__
    proc = _FakeProcess(stderr=b"resolving\ndone\n")

    await run_command_async(["ocx", "pull"], {}, clock=clock, exec_factory=_async_factory(proc))

    pumped = [rec.getMessage() for rec in caplog.records if rec.name == "ocx_sdk.process"]
    events = [rec.getMessage() for rec in caplog.records if rec.name == "ocx_sdk"]
    assert pumped == ["resolving", "done"]
    assert events == ["run: ocx pull", "exit 0 in 0.500s: ocx pull"]


async def test_run_command_async_runs_a_real_child() -> None:
    script = "import sys; print('out'); sys.stderr.write('err\\n')"

    done = await run_command_async([sys.executable, "-c", script], {}, timeout=30)

    assert done == Completed(0, "out\n", "err\n")


async def test_spawn_async_returns_the_live_handle() -> None:
    proc = _FakeProcess()

    handle = await spawn_async(["ocx", "run"], {"PATH": "/usr/bin"}, exec_factory=_async_factory(proc))

    assert handle is proc
    assert proc.kwargs["env"] == {"PATH": "/usr/bin"}


async def test_spawn_async_rejects_the_kwargs_that_would_defeat_the_invariants() -> None:
    with pytest.raises(ValueError, match="shell"):
        await spawn_async(["ocx", "run"], {}, exec_factory=_async_factory(_FakeProcess()), shell=True)


async def test_spawn_async_logs_the_redacted_argv(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG, logger="ocx_sdk")

    await spawn_async(["ocx", "login", _TOKEN], {}, redact=_redact_token, exec_factory=_async_factory(_FakeProcess()))

    assert _TOKEN not in caplog.text
    assert "spawn: ocx login '***'" in caplog.text


async def test_spawn_async_runs_a_real_child() -> None:
    handle = await spawn_async([sys.executable, "-c", "pass"], {})

    assert await handle.wait() == 0


# --- nothing outlives the call, whatever ended it ------------------------


def test_keyboard_interrupt_during_the_wait_leaves_no_live_child(killpg: list[tuple[int, int]]) -> None:
    proc = _FakePopen(pid=555, waits=["interrupt", -15])

    with pytest.raises(KeyboardInterrupt):
        run_command(["ocx", "pull"], {}, popen_factory=_factory(proc))

    assert killpg == [(555, signal.SIGTERM)]
    assert proc.returncode == -15


def test_a_successful_run_is_not_signalled_on_the_way_out(killpg: list[tuple[int, int]]) -> None:
    proc = _FakePopen(exit_code=0)

    run_command(["ocx", "status"], {}, popen_factory=_factory(proc))

    assert killpg == []


def test_join_abandons_a_pump_that_never_reaches_eof(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A grandchild holding the pipe must not cost the caller its deadline."""
    monkeypatch.setattr(_process, "_PUMP_JOIN", 0.01)
    caplog.set_level(logging.DEBUG, logger="ocx_sdk")
    stuck = _StuckStream()
    proc = _FakePopen(stuck_stdout=stuck)

    try:
        done = run_command(["ocx", "status"], {}, popen_factory=_factory(proc))
    finally:
        stuck.release.set()

    assert done.exit_code == 0
    assert "abandoning it" in caplog.text


async def test_a_read_that_blows_up_still_reaps_the_child(killpg: list[tuple[int, int]]) -> None:
    proc = _FakeProcess(pid=999, read_error=OSError("pipe went away"))

    with pytest.raises(ExceptionGroup, match="unhandled errors in a TaskGroup"):
        await run_command_async(["ocx", "pull"], {}, exec_factory=_async_factory(proc))

    assert killpg == [(999, signal.SIGTERM)]


async def test_run_command_async_cancellation_keeps_the_partial_stderr(killpg: list[tuple[int, int]]) -> None:
    proc = _FakeProcess(pid=999, stderr=b"resolving uv\n", suspend=True)
    task = asyncio.create_task(run_command_async(["ocx", "pull"], {}, exec_factory=_async_factory(proc)))
    for _ in range(6):
        await asyncio.sleep(0)

    task.cancel()
    with pytest.raises(asyncio.CancelledError) as caught:
        await task

    assert killpg == [(999, signal.SIGTERM)]
    assert caught.value.__notes__ == ["partial ocx stderr before cancellation: resolving uv\n"]


# --- stdin: the `login --password-stdin` shape ---------------------------


def test_run_command_pipes_input_to_the_child_and_closes_it() -> None:
    proc = _FakePopen()

    run_command(["ocx", "login"], {}, input="hunter2\n", popen_factory=_factory(proc))

    assert proc.kwargs["stdin"] == subprocess.PIPE
    assert proc.stdin is not None
    assert proc.stdin.text == "hunter2\n"
    assert proc.stdin.closed


def test_run_command_leaves_stdin_inherited_when_there_is_no_input() -> None:
    proc = _FakePopen()

    run_command(["ocx", "status"], {}, popen_factory=_factory(proc))

    assert proc.kwargs["stdin"] is None


def test_run_command_redacts_the_input_even_when_redact_does_not_know_it(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG, logger="ocx_sdk")
    proc = _FakePopen(exit_code=80, stderr=f"rejected token {_TOKEN}\n")
    lines: list[str] = []

    with pytest.raises(AuthError) as caught:
        run_command(["ocx", "login"], {}, input=_TOKEN, on_log=lines.append, popen_factory=_factory(proc))

    assert _TOKEN not in caplog.text
    assert _TOKEN not in "".join(lines)
    assert _TOKEN not in str(caught.value)
    assert _TOKEN not in caught.value.stderr
    assert proc.stdin is not None
    assert proc.stdin.text == _TOKEN


def test_run_command_survives_a_child_that_closed_stdin_first() -> None:
    proc = _FakePopen(exit_code=64, stdin_broken=True)

    with pytest.raises(UsageError):
        run_command(["ocx", "login"], {}, input=_TOKEN, popen_factory=_factory(proc))


def test_run_command_without_capture_still_pipes_input() -> None:
    proc = _FakePopen()

    run_command(["ocx", "login"], {}, input="tok\n", capture=False, popen_factory=_factory(proc))

    assert proc.kwargs["stdin"] == subprocess.PIPE
    assert proc.kwargs["stdout"] is None
    assert proc.stdin is not None
    assert proc.stdin.text == "tok\n"


def test_run_command_feeds_a_real_child_its_stdin() -> None:
    done = run_command([sys.executable, "-c", "import sys; print(sys.stdin.read().strip())"], {}, input="hunter2\n")

    assert done == Completed(0, "hunter2\n", "")


async def test_run_command_async_pipes_input_to_the_child_and_closes_it() -> None:
    proc = _FakeProcess()

    await run_command_async(["ocx", "login"], {}, input="hunter2\n", exec_factory=_async_factory(proc))

    assert proc.kwargs["stdin"] == asyncio.subprocess.PIPE
    assert proc.stdin.data == b"hunter2\n"
    assert proc.stdin.closed


async def test_run_command_async_redacts_the_input_from_the_captured_stderr() -> None:
    proc = _FakeProcess(stderr=f"rejected {_TOKEN}\n".encode())

    done = await run_command_async(["ocx", "login"], {}, input=_TOKEN, exec_factory=_async_factory(proc))

    assert done.stderr == "rejected ***\n"


async def test_run_command_async_survives_a_child_that_closed_stdin_first() -> None:
    proc = _FakeProcess(exit_code=64, stdin_broken=True)

    with pytest.raises(UsageError):
        await run_command_async(["ocx", "login"], {}, input=_TOKEN, exec_factory=_async_factory(proc))


async def test_run_command_async_feeds_a_real_child_its_stdin() -> None:
    script = "import sys; print(sys.stdin.read().strip())"

    done = await run_command_async([sys.executable, "-c", script], {}, input="hunter2\n")

    assert done == Completed(0, "hunter2\n", "")


# --- layering (architecture.md: _process takes primitives only) -----------


def test_process_imports_no_runtime_module() -> None:
    source = Path(_process.__file__).read_text(encoding="utf-8")

    for forbidden in ("_config", "_env", "_envmodel", "_client", "_results"):
        assert f"from .{forbidden} import" not in source
        assert f"import {forbidden}" not in source


def test_run_command_ok_codes_returns_a_tolerated_exit_instead_of_raising() -> None:
    proc = _FakePopen(exit_code=1, stdout='{"status": "failed"}')

    done = run_command(["ocx", "package", "test"], {}, ok_codes=(0, 1), popen_factory=_factory(proc))

    assert done == Completed(1, '{"status": "failed"}', "")


def test_run_command_ok_codes_still_raises_outside_the_tolerated_set() -> None:
    proc = _FakePopen(exit_code=65, stderr="stale lock\n")

    with pytest.raises(DataError):
        run_command(["ocx", "package", "test"], {}, ok_codes=(0, 1), popen_factory=_factory(proc))
