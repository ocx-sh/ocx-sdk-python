# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The OCX Authors

"""Subprocess lifecycle for ocx-sdk (contract C-010).

Every ocx spawn in this SDK goes through this module, and it takes
**primitives only**: an argv sequence, a finished env mapping, a redaction
callable. `OcxConfig`, `HostEnv`, and the client handles never reach here —
composition happens one layer up, so nothing here knows what a credential is
beyond "a string `redact` removes".

Four entry points, mirroring the stdlib's own split:

- `run_command` / `run_command_async` — one-shots with timeout, retry, and
  captured output, the way `subprocess.run` is a one-shot.
- `spawn` / `spawn_async` — live handles the caller drives, the way `Popen`
  is a live handle. No SDK timeout there: the process belongs to the caller.

Redaction runs on every outbound surface — captured stderr, `on_log` lines,
the argv in DEBUG records, and the argv and stderr carried on raised errors —
so a token that reached argv or the child env cannot escape through a log
(CWE-532). Captured **stdout is deliberately never redacted** (§12): it is the
raw JSON payload a parser consumes, and a substitution inside it would corrupt
the document. Consumers must not paste raw stdout into error text.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shlex
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Awaitable, Callable, Collection, Mapping, Sequence
from contextlib import ExitStack, contextmanager
from typing import IO, TYPE_CHECKING, Any, NamedTuple, Protocol, cast

# _EXIT_CODE_ERRORS is package-internal, and this module is its one consumer:
# C-010 pins the exit-code map as the seam between _errors and every spawn.
from ._errors import (
    _EXIT_CODE_ERRORS,  # pyright: ignore[reportPrivateUsage]
    ExitCode,
    OcxProcessError,
    OcxTimeoutError,
)
from ._retry import run_with_retry, run_with_retry_async

if TYPE_CHECKING:
    from collections.abc import Generator
    from types import FrameType

    from ._types import RetryPolicy

KILL_GRACE = 5.0
"""Seconds a timed-out child gets between SIGTERM and SIGKILL."""

_LOG = logging.getLogger("ocx_sdk")
"""SDK events: the redacted argv, the exit status, the duration."""

_PROCESS_LOG = logging.getLogger("ocx_sdk.process")
"""ocx's own stderr, one redacted line per record."""

_POSIX = sys.platform != "win32"
"""Whether the platform has process groups worth signalling."""

_PUMP_JOIN = 5.0
"""Seconds to wait for a pump thread before abandoning it."""

_READ_CHUNK = 65536
"""Bytes per async pipe read — one page-ish, the size a pipe buffer holds."""

_REJECTED_POPEN_KW = ("args", "shell", "executable")
"""Spawn kwargs that would defeat the frozen exe, the composed argv, or `shell=False`.

`env` is on the same list in the design and needs no entry here: it is this
module's own parameter, so it can never arrive through `**popen_kw` at all.
"""

type Redact = Callable[[str], str]
"""Exact-string secret scrubber, `_env.build_spawn_env`'s pinned seam."""

type Clock = Callable[[], float]
"""Monotonic clock seam; tests inject a deterministic one."""

type StrPath = str | os.PathLike[str]
"""A working directory, in either shape the stdlib accepts."""

type PopenFactory = Callable[..., subprocess.Popen[Any]]
"""`subprocess.Popen` seam, so the kill ladder is unit-testable without a child."""

type ExecFactory = Callable[..., Awaitable[asyncio.subprocess.Process]]
"""`asyncio.create_subprocess_exec` seam, the async twin of `PopenFactory`."""


def _identity(text: str) -> str:
    """Return `text` unchanged — the default when no secrets are configured."""
    return text


class Completed(NamedTuple):
    """What one finished ocx process produced.

    Attributes:
        exit_code: The child's exit status.
        stdout: Captured stdout, empty when `capture=False`.
        stderr: Captured stderr, redacted, empty when `capture=False`.
    """

    exit_code: int
    stdout: str
    stderr: str


class _Killable(Protocol):
    """The slice of a child handle the kill ladder needs, sync or async."""

    @property
    def pid(self) -> int: ...

    @property
    def returncode(self) -> int | None: ...

    def terminate(self) -> None: ...

    def kill(self) -> None: ...


def compose_argv(
    exe: str,
    global_flags: Sequence[str],
    command: Sequence[str],
    positionals: Sequence[str] = (),
    *,
    child: Sequence[str] = (),
) -> tuple[str, ...]:
    """Build an ocx argv: `[exe, *global_flags, *command, *positionals, --, *child]`.

    Global flags come **before** the subcommand — ocx's clap grammar takes
    them there and nowhere else.

    Args:
        exe: The ocx binary.
        global_flags: Flags that belong ahead of the subcommand.
        command: Subcommand path plus its own flags, SDK-composed.
        positionals: Identifiers ocx resolves itself. Always guarded: a
            leading dash here would be parsed as a flag.
        child: Argv for a process ocx hosts (`run`, `package exec`), emitted
            behind `--` where a leading dash is meaningful and safe. Empty
            means the command hosts nothing and no `--` is written.

    Returns:
        The argv, ready for `run_command` or `spawn`.

    Raises:
        ValueError: A positional starts with `-`, so ocx would parse it as a
            flag (CWE-88).
    """
    flaglike = [value for value in positionals if value.startswith("-")]
    if flaglike:
        listed = ", ".join(repr(value) for value in flaglike)
        raise ValueError(
            f"positional argument may not start with '-': {listed}. ocx would parse it as a flag; "
            "identifiers never begin with a dash, and child argv belongs in the `child` group, behind `--`."
        )
    if child:
        return (exe, *global_flags, *command, *positionals, "--", *child)
    return (exe, *global_flags, *command, *positionals)


def run_command(
    argv: Sequence[str],
    env: Mapping[str, str],
    *,
    input: str | None = None,
    redact: Redact = _identity,
    timeout: float | None = None,
    retry: RetryPolicy | None = None,
    on_log: Callable[[str], None] | None = None,
    capture: bool = True,
    check: bool = True,
    ok_codes: Collection[int] = (0,),
    cwd: StrPath | None = None,
    popen_factory: PopenFactory | None = None,
    clock: Clock = time.monotonic,
    kill_grace: float = KILL_GRACE,
) -> Completed:
    """Run ocx once and wait for it, the way `subprocess.run` does.

    With `capture=True` both streams are piped and drained by pump threads,
    so neither can fill its pipe buffer and deadlock the child. Every stderr
    line is redacted, then logged at DEBUG to `ocx_sdk.process` and handed to
    `on_log`. With `capture=False` the child inherits this process's stdio,
    nothing is captured, and SIGINT is forwarded to it for the duration.

    A capture is held whole in memory and has no ceiling by design — the
    payload is a JSON document a parser needs intact, and a truncated one is
    worse than a large one; pass `capture=False` for a chatty child whose
    output nothing here parses.

    Windows has no process group to signal, so a timeout goes straight to
    `TerminateProcess` with no graceful phase and no SIGINT forwarding:
    `capture=False` together with `timeout` is the documented degraded
    combination there (design §10).

    Args:
        argv: The full argv, `compose_argv`'s output.
        env: The finished child environment; it is snapshotted here.
        input: Written to the child's stdin, which is then closed so a reader
            like `login --password-stdin` sees EOF. It is treated as a secret
            for this call: whatever `redact` already scrubs, plus this value.
        redact: Scrubs secrets from everything that leaves this module.
        timeout: Per-attempt budget in seconds. `None` waits forever.
        retry: Retry the exit codes in `RetryPolicy.retry_on`. Timeouts are
            never retried. `None` runs a single attempt.
        on_log: Called with each redacted stderr line, from the pump thread —
            so it must be thread-safe.
        capture: Pipe and capture both streams, unbounded.
        check: Raise on a non-zero exit instead of returning it.
        ok_codes: Exit codes that count as success for `check` — pass
            `(0, 1)` when exit 1 carries a parseable payload.
        cwd: Working directory for the child.
        popen_factory: `subprocess.Popen` seam for tests.
        clock: Monotonic clock seam for the duration this logs.
        kill_grace: Seconds between SIGTERM and SIGKILL on timeout.

    Returns:
        The exit code and captured output of the final attempt.

    Raises:
        OcxProcessError: The child exited non-zero and `check` is set; the
            subclass is chosen by exit code.
        OcxTimeoutError: `timeout` expired; the child's process group was
            terminated and then killed.
    """
    scrub = _also_redacting(redact, input) if input else redact
    logged = tuple(scrub(arg) for arg in argv)
    finished: list[Completed] = []

    def attempt() -> Completed:
        done = _run_once(
            argv,
            env,
            logged=logged,
            input=input,
            redact=scrub,
            timeout=timeout,
            on_log=on_log,
            capture=capture,
            cwd=cwd,
            popen_factory=popen_factory,
            clock=clock,
            kill_grace=kill_grace,
        )
        finished.append(done)
        if done.exit_code not in ok_codes:
            raise _exit_error(done, logged)
        return done

    try:
        return attempt() if retry is None else run_with_retry(attempt, retry, _retryable(retry))
    except OcxProcessError:
        if check:
            raise
        return finished[-1]


async def run_command_async(
    argv: Sequence[str],
    env: Mapping[str, str],
    *,
    input: str | None = None,
    redact: Redact = _identity,
    timeout: float | None = None,
    retry: RetryPolicy | None = None,
    on_log: Callable[[str], None] | None = None,
    capture: bool = True,
    check: bool = True,
    ok_codes: Collection[int] = (0,),
    cwd: StrPath | None = None,
    exec_factory: ExecFactory | None = None,
    clock: Clock = time.monotonic,
    kill_grace: float = KILL_GRACE,
) -> Completed:
    """Run ocx once on the event loop — the async twin of `run_command`.

    Cancelling the awaiting task terminates the child before the
    `CancelledError` propagates; CPython does not do this for you
    ([gh-88050](https://github.com/python/cpython/issues/88050)), and the
    orphan would outlive the loop that started it.

    Args:
        argv: The full argv, `compose_argv`'s output.
        env: The finished child environment; it is snapshotted here.
        input: Written to the child's stdin, which is then closed. Treated as
            a secret for this call, exactly as in `run_command`.
        redact: Scrubs secrets from everything that leaves this module.
        timeout: Per-attempt budget in seconds. `None` waits forever.
        retry: Retry the exit codes in `RetryPolicy.retry_on`.
        on_log: Rejected here — see Raises.
        capture: Pipe and capture both streams.
        check: Raise on a non-zero exit instead of returning it.
        ok_codes: Exit codes that count as success for `check` — pass
            `(0, 1)` when exit 1 carries a parseable payload.
        cwd: Working directory for the child.
        exec_factory: `asyncio.create_subprocess_exec` seam for tests.
        clock: Monotonic clock seam for the duration this logs.
        kill_grace: Seconds between SIGTERM and SIGKILL on timeout.

    Returns:
        The exit code and captured output of the final attempt.

    Raises:
        ValueError: `on_log` was given. A pump firing a blocking callback on
            the event loop stalls every other task on it, so v0.1 refuses
            rather than ships an accidental contract.
        OcxProcessError: The child exited non-zero and `check` is set.
        OcxTimeoutError: `timeout` expired; the child was terminated.
    """
    if on_log is not None:
        raise ValueError(
            "on_log is unsupported on async paths in v0.1 — a blocking callback would stall the event "
            "loop it fires on. Read the stream through logging.getLogger('ocx_sdk.process') instead."
        )
    scrub = _also_redacting(redact, input) if input else redact
    logged = tuple(scrub(arg) for arg in argv)
    finished: list[Completed] = []

    async def attempt() -> Completed:
        done = await _run_once_async(
            argv,
            env,
            logged=logged,
            input=input,
            redact=scrub,
            timeout=timeout,
            capture=capture,
            cwd=cwd,
            exec_factory=exec_factory,
            clock=clock,
            kill_grace=kill_grace,
        )
        finished.append(done)
        if done.exit_code not in ok_codes:
            raise _exit_error(done, logged)
        return done

    try:
        return await (attempt() if retry is None else run_with_retry_async(attempt, retry, _retryable(retry)))
    except OcxProcessError:
        if check:
            raise
        return finished[-1]


def spawn(
    argv: Sequence[str],
    env: Mapping[str, str],
    *,
    redact: Redact = _identity,
    cwd: StrPath | None = None,
    popen_factory: PopenFactory | None = None,
    **popen_kw: Any,
) -> subprocess.Popen[Any]:
    """Start ocx and hand back the live `Popen` — the caller owns it.

    No timeout, no pumps, no captured output: waiting, draining pipes, and
    killing are the caller's job. Pass `stdout=subprocess.PIPE` without
    draining it and the child deadlocks on a full pipe buffer, exactly as it
    would with a bare `Popen`.

    Args:
        argv: The full argv, `compose_argv`'s output.
        env: The finished child environment; it is snapshotted here.
        redact: Scrubs secrets from the argv this logs.
        cwd: Working directory for the child.
        popen_factory: `subprocess.Popen` seam for tests.
        **popen_kw: Forwarded to `Popen`, minus the rejected keys below.
            `start_new_session` is re-applied after them, so a spawn cannot
            quietly lose the process group a caller-side kill depends on.

    Returns:
        The running child.

    Raises:
        ValueError: `popen_kw` carried `args`, `shell`, or `executable`.
            Those would defeat the composed argv, the `shell=False`
            invariant, or the frozen exe. (`env` cannot be smuggled in: it is
            this function's own parameter.)
    """
    _reject_owned_kwargs(popen_kw)
    if _LOG.isEnabledFor(logging.DEBUG):
        _LOG.debug("spawn: %s", shlex.join(redact(arg) for arg in argv))
    factory = popen_factory or subprocess.Popen
    return factory(list(argv), env=dict(env), cwd=cwd, shell=False, **{**popen_kw, **_session_kwargs()})


async def spawn_async(
    argv: Sequence[str],
    env: Mapping[str, str],
    *,
    redact: Redact = _identity,
    cwd: StrPath | None = None,
    exec_factory: ExecFactory | None = None,
    **popen_kw: Any,
) -> asyncio.subprocess.Process:
    """Start ocx on the event loop and hand back the live process.

    The async twin of `spawn`, with the same ownership split: no timeout, no
    pumps, no captured output.

    Args:
        argv: The full argv, `compose_argv`'s output.
        env: The finished child environment; it is snapshotted here.
        redact: Scrubs secrets from the argv this logs.
        cwd: Working directory for the child.
        exec_factory: `asyncio.create_subprocess_exec` seam for tests.
        **popen_kw: Forwarded to the factory, minus the rejected keys below,
            and with `start_new_session` re-applied after them.

    Returns:
        The running child.

    Raises:
        ValueError: `popen_kw` carried `args`, `shell`, or `executable`.
    """
    _reject_owned_kwargs(popen_kw)
    if _LOG.isEnabledFor(logging.DEBUG):
        _LOG.debug("spawn: %s", shlex.join(redact(arg) for arg in argv))
    factory = exec_factory or asyncio.create_subprocess_exec
    return await factory(*argv, env=dict(env), cwd=cwd, **{**popen_kw, **_session_kwargs()})


def _reject_owned_kwargs(popen_kw: Mapping[str, object]) -> None:
    """Refuse spawn kwargs that would take back what this module owns."""
    rejected = [name for name in _REJECTED_POPEN_KW if name in popen_kw]
    if rejected:
        raise ValueError(
            f"{', '.join(rejected)} cannot be set on a spawn: the argv and the child environment come from "
            "the SDK, and shell=False is an invariant. Configure the child through `OcxConfig` and the "
            "`HostEnv` the handle was built with, and put the command itself in `argv`."
        )


def _session_kwargs() -> dict[str, Any]:
    """Put the child in its own session, where the platform has them.

    A session of its own gives the child a process group the kill ladder can
    signal as a unit, so a timeout reaps ocx's own children too.
    """
    if not _POSIX:  # pragma: no cover - Windows: no new process group, so Ctrl-C still reaches the child (§10)
        return {}
    return {"start_new_session": True}


def _run_once(
    argv: Sequence[str],
    env: Mapping[str, str],
    *,
    logged: tuple[str, ...],
    input: str | None,
    redact: Redact,
    timeout: float | None,
    on_log: Callable[[str], None] | None,
    capture: bool,
    cwd: StrPath | None,
    popen_factory: PopenFactory | None,
    clock: Clock,
    kill_grace: float,
) -> Completed:
    """Spawn ocx, drain it, and wait — one attempt, no retry."""
    # `printable` feeds DEBUG records only, and joining a long argv is not
    # free, so it is built only when something is listening.
    printable = shlex.join(logged) if _LOG.isEnabledFor(logging.DEBUG) else ""
    started = clock()
    _LOG.debug("run: %s", printable)
    factory = popen_factory or subprocess.Popen
    proc = factory(
        list(argv),
        env=dict(env),
        cwd=cwd,
        shell=False,
        stdin=subprocess.PIPE if input is not None else None,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
        text=True,
        encoding="utf-8",
        errors="replace",
        **_session_kwargs(),
    )
    if input is not None:
        _write_stdin(cast("IO[str]", proc.stdin), input)
    out: list[str] = []
    err: list[str] = []
    pumps: list[threading.Thread] = []
    try:
        with ExitStack() as stack:
            if capture:
                pumps.append(_pump(cast("IO[str]", proc.stdout), out.append, "ocx-sdk-stdout"))
                pumps.append(_pump(cast("IO[str]", proc.stderr), _stderr_sink(err, redact, on_log), "ocx-sdk-stderr"))
            else:
                stack.enter_context(_sigint_forwarded(proc))
            exit_code = proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired as expired:
        _reap(proc, kill_grace)
        _join(pumps)
        raise OcxTimeoutError(expired.timeout, logged, "".join(err)) from None
    finally:
        # Whatever ended the wait — a KeyboardInterrupt raised into it, an
        # on_log callback that blew up — the child must not outlive this call
        # and the pumps must not stay attached to it. Both are idempotent.
        _reap(proc, kill_grace)
        _join(pumps)
    _LOG.debug("exit %d in %.3fs: %s", exit_code, clock() - started, printable)
    return Completed(exit_code, "".join(out), "".join(err))


async def _run_once_async(
    argv: Sequence[str],
    env: Mapping[str, str],
    *,
    logged: tuple[str, ...],
    input: str | None,
    redact: Redact,
    timeout: float | None,
    capture: bool,
    cwd: StrPath | None,
    exec_factory: ExecFactory | None,
    clock: Clock,
    kill_grace: float,
) -> Completed:
    """Spawn ocx on the loop, drain it, and wait — one attempt, no retry."""
    printable = shlex.join(logged) if _LOG.isEnabledFor(logging.DEBUG) else ""
    started = clock()
    _LOG.debug("run: %s", printable)
    factory = exec_factory or asyncio.create_subprocess_exec
    proc = await factory(
        *argv,
        env=dict(env),
        cwd=cwd,
        stdin=asyncio.subprocess.PIPE if input is not None else None,
        stdout=asyncio.subprocess.PIPE if capture else None,
        stderr=asyncio.subprocess.PIPE if capture else None,
        **_session_kwargs(),
    )
    if input is not None:
        await _write_stdin_async(cast("asyncio.StreamWriter", proc.stdin), input)
    out: list[bytes] = []
    err: list[bytes] = []
    try:
        async with asyncio.timeout(timeout):
            if capture:
                # Buffers, not `communicate()`: a cancelled `communicate` drops
                # what it had already read, and the partial stderr is the whole
                # point of the error a timeout raises.
                async with asyncio.TaskGroup() as group:
                    group.create_task(_read_all(cast("asyncio.StreamReader", proc.stdout), out))
                    group.create_task(_read_all(cast("asyncio.StreamReader", proc.stderr), err))
            exit_code = await proc.wait()
    except TimeoutError:
        await _kill_ladder_async(proc, kill_grace)
        raise OcxTimeoutError(timeout or 0.0, logged, _decode(err, redact)) from None
    except asyncio.CancelledError as cancelled:
        # gh-88050: CPython leaves the child running when the awaiting task is
        # cancelled. No grace wait and no awaits at all here — awaiting inside a
        # cancellation handler is how a caller that asked to stop ends up
        # hanging instead.
        _terminate_group(proc)
        if err:
            cancelled.add_note(f"partial ocx stderr before cancellation: {_decode(err, redact)}")
        raise
    except BaseException:
        # Anything else — an ExceptionGroup out of the TaskGroup, a
        # KeyboardInterrupt — still must not orphan the child.
        await _kill_ladder_async(proc, kill_grace)
        raise
    stderr_text = _decode(err, redact)
    # Splitting a whole capture into lines to hand each one to a disabled
    # logger is pure waste; the sync path pays the same cost per line inside
    # its pump, where the split has already happened anyway.
    if _PROCESS_LOG.isEnabledFor(logging.DEBUG):
        for line in stderr_text.splitlines():
            _PROCESS_LOG.debug("%s", line)
    _LOG.debug("exit %d in %.3fs: %s", exit_code, clock() - started, printable)
    return Completed(exit_code, _decode(out, _identity), stderr_text)


def _pump(stream: IO[str], consume: Callable[[str], None], name: str) -> threading.Thread:
    """Start a thread draining `stream` line by line into `consume`.

    Both streams get one, so a child that fills one pipe buffer while the SDK
    waits on the other cannot deadlock.
    """
    thread = threading.Thread(target=_drain, args=(stream, consume), name=name, daemon=True)
    thread.start()
    return thread


def _drain(stream: IO[str], consume: Callable[[str], None]) -> None:
    """Hand every line of `stream` to `consume`, then close it."""
    with stream:
        for line in stream:
            consume(line)


def _join(pumps: Sequence[threading.Thread]) -> None:
    """Wait for the pumps, bounded, so a stuck pipe cannot outlast the deadline.

    A pump can outlive its child: on Windows a grandchild that inherited the
    pipe handle holds it open, so the read never sees EOF however dead the
    child is. The threads are daemons — abandoning one costs a thread, while
    blocking here would cost the caller the timeout it asked for.
    """
    for pump in pumps:
        pump.join(_PUMP_JOIN)
        if pump.is_alive():
            _LOG.debug("%s still draining after %.0fs; abandoning it", pump.name, _PUMP_JOIN)


def _also_redacting(redact: Redact, secret: str) -> Redact:
    """Widen a scrubber to cover one more value, for the length of one call.

    `input` carries a credential the configured `redact` has never seen — a
    password piped to `login --password-stdin` is not in the spawn env.
    """

    def scrub(text: str) -> str:
        return redact(text).replace(secret, "***")

    return scrub


def _write_stdin(stream: IO[str], text: str) -> None:
    """Feed the child its stdin and close it, so a reader sees EOF."""
    # ponytail: written before the wait rather than from a third thread. Fine
    # for a credential; an input larger than the pipe buffer would block here
    # if the child never reads it — pump it from a thread if that ever lands.
    try:
        stream.write(text)
        stream.close()
    except BrokenPipeError:
        _LOG.debug("child closed stdin before the input was written")


async def _write_stdin_async(stream: asyncio.StreamWriter, text: str) -> None:
    """The async twin of `_write_stdin`."""
    try:
        stream.write(text.encode())
        await stream.drain()
    except (BrokenPipeError, ConnectionResetError):
        _LOG.debug("child closed stdin before the input was written")
    stream.close()


def _stderr_sink(chunks: list[str], redact: Redact, on_log: Callable[[str], None] | None) -> Callable[[str], None]:
    """Build the pump's stderr consumer: redact, keep, log, and stream."""

    def consume(line: str) -> None:
        text = redact(line)
        chunks.append(text)
        message = text.rstrip("\n")
        _PROCESS_LOG.debug("%s", message)
        if on_log is not None:
            on_log(message)

    return consume


async def _read_all(stream: asyncio.StreamReader, chunks: list[bytes]) -> None:
    """Drain a pipe to EOF, keeping whatever arrived before a cancellation."""
    while chunk := await stream.read(_READ_CHUNK):
        chunks.append(chunk)


def _decode(chunks: list[bytes], redact: Redact) -> str:
    """Join captured bytes into redacted text, replacing anything undecodable.

    Newlines are translated like the sync path's text-mode pipes, so the same
    child yields byte-identical results from both drivers on every platform.
    """
    text = b"".join(chunks).decode("utf-8", "replace")
    return redact(text.replace("\r\n", "\n"))


@contextmanager
def _sigint_forwarded(proc: _Killable) -> Generator[None]:
    """Relay this process's SIGINT to the child's group while it runs.

    A passthrough child sits in its own session, so the terminal's Ctrl-C no
    longer reaches it on its own — without this it would ignore the interrupt
    that a plain `ocx run` honors.
    """
    if not _POSIX:  # pragma: no cover - Windows: the child keeps this console's group and gets Ctrl-C (§10)
        yield
        return

    previous = signal.getsignal(signal.SIGINT)

    def forward(signum: int, frame: FrameType | None) -> None:
        _signal_group(proc, signal.SIGINT)
        if callable(previous):
            # Normally signal.default_int_handler, which raises KeyboardInterrupt
            # from here: the run unwinds, the finally reaps the child, and the
            # caller's Ctrl-C still means what it always meant. Swallowing it
            # would also let the child's 130 masquerade as a plain failure.
            # ponytail: a SIG_DFL or SIG_IGN predecessor is left alone — the
            # caller asked for terminate-or-ignore and the child got the signal.
            previous(signum, frame)

    try:
        signal.signal(signal.SIGINT, forward)
    except ValueError:
        # Handlers install on the main thread only. A worker-thread caller
        # loses forwarding, not the run.
        _LOG.debug("SIGINT forwarding unavailable off the main thread")
        yield
        return
    try:
        yield
    finally:
        signal.signal(signal.SIGINT, previous)


def _reap(proc: subprocess.Popen[Any], grace: float) -> None:
    """Run the kill ladder unless the child already exited.

    Idempotent, and called from the `finally` of every run: a wait that ended
    in a `KeyboardInterrupt` or any other exception leaves a live child that
    nobody owns any more.
    """
    if proc.returncode is None:
        _kill_ladder(proc, grace)


def _kill_ladder(proc: subprocess.Popen[Any], grace: float) -> None:
    """Terminate the child's group, wait out `grace`, then kill what is left."""
    _terminate_group(proc)
    try:
        proc.wait(timeout=grace)
    except subprocess.TimeoutExpired:
        _kill_group(proc)
        proc.wait()


async def _kill_ladder_async(proc: asyncio.subprocess.Process, grace: float) -> None:
    """The async twin of `_kill_ladder`."""
    _terminate_group(proc)
    try:
        async with asyncio.timeout(grace):
            await proc.wait()
    except TimeoutError:
        _kill_group(proc)
        await proc.wait()


def _terminate_group(proc: _Killable) -> None:
    """SIGTERM the child's whole process group, if it is still there."""
    if not _POSIX:  # pragma: no cover - Windows: TerminateProcess only, no graceful phase (§10)
        proc.terminate()
        return
    _signal_group(proc, signal.SIGTERM)


def _kill_group(proc: _Killable) -> None:
    """SIGKILL the child's whole process group, if it is still there."""
    if not _POSIX:  # pragma: no cover - Windows: TerminateProcess is the only rung there (§10)
        proc.kill()
        return
    _signal_group(proc, signal.SIGKILL)


def _signal_group(proc: _Killable, sig: int) -> None:
    """Signal a child's process group, if the child is still there to signal.

    The `returncode` check is the pid-recycling guard (CWE-367, bpo-38630):
    once a child is reaped, its pid — and the process-group id that equals it —
    can be handed to an unrelated process, and the signal would hit a stranger.
    asyncio reaps on its own schedule, so this is not hypothetical.

    Every group signal in this module goes through here, which is what keeps
    that one check honest.
    """
    if proc.returncode is not None:
        _LOG.debug("child %d was already reaped; not signalling it", proc.pid)
        return
    try:
        # The child leads its own session, so its pid is its process-group id
        # and this signal can never reach the group the SDK itself runs in.
        os.killpg(proc.pid, sig)
    except ProcessLookupError:
        _LOG.debug("child %d exited before signal %d reached it", proc.pid, sig)


def _exit_error(done: Completed, logged: tuple[str, ...]) -> OcxProcessError:
    """Map an exit status to the error class it means."""
    try:
        code = ExitCode(done.exit_code)
    except ValueError:
        # A signal-killed ocx exits with a status ocx never assigns (137).
        return OcxProcessError(done.exit_code, logged, done.stderr)
    return _EXIT_CODE_ERRORS.get(code, OcxProcessError)(done.exit_code, logged, done.stderr)


def _retryable(policy: RetryPolicy) -> Callable[[Exception], bool]:
    """Build the exit-code classifier `_retry` asks its callers to own.

    `OcxTimeoutError` is not an `OcxProcessError`, which is how a timeout
    stays unretried by default however `retry_on` is configured.
    """

    def classify(err: Exception) -> bool:
        return isinstance(err, OcxProcessError) and err.exit_code in policy.retry_on

    return classify
