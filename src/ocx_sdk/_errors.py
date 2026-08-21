# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The OCX Authors

"""Exception hierarchy for ocx-sdk (contract C-001).

The exit code of the ocx process *is* the error category: `_process` maps it
to a subclass through `_EXIT_CODE_ERRORS`, and nothing in this SDK ever
classifies a failure by matching stderr text.

Every message ends with an actionable next step. The message is what a CI log
shows, and a bare fact leaves the reader guessing what to do about it.

Stdlib-only leaf: this module must never import from `ocx_sdk`, which is what
keeps `OcxProcessError.retryable` policy-independent (fixed `True` for exit
75) instead of coupling it to whatever `RetryPolicy` a caller passed.
"""

from __future__ import annotations

import shlex
from collections.abc import Sequence
from enum import IntEnum

_STDERR_EXCERPT = 500
"""Trailing characters of stderr kept in a message; the attribute holds it all."""


class ExitCode(IntEnum):
    """Exit codes ocx assigns to error categories (`sysexits.h` plus ocx's own).

    `TEMP_FAIL` is the retry signal — ocx maps 429/5xx/network timeouts onto
    it, while `UNAVAILABLE` marks a failure that retrying cannot fix.
    """

    OK = 0
    FAILURE = 1
    USAGE = 64
    DATA_ERR = 65
    UNAVAILABLE = 69
    IO_ERR = 74
    TEMP_FAIL = 75
    NO_PERM = 77
    CONFIG = 78
    NOT_FOUND = 79
    AUTH = 80
    POLICY_BLOCKED = 81
    DIRTY_RC_BLOCK = 82


def _rebuild(cls: type[OcxError], args: tuple[object, ...], state: dict[str, object]) -> OcxError:
    """Rebuild an unpickled error from its attributes, bypassing `__init__`.

    Constructor signatures differ across the hierarchy and `attempts` is set
    after construction, so replaying `__init__` would drop state.
    """
    err = cls.__new__(cls)
    err.args = args
    err.__dict__.update(state)
    return err


class OcxError(Exception):
    """Base class for every error raised by ocx-sdk.

    Subclasses set `_hint`: the next step a caller can actually take. It is
    appended to the message of every error in the hierarchy.

    Errors cross process boundaries intact — a `ProcessPoolExecutor` worker or
    a pytest-xdist node has to be able to hand one back.
    """

    _hint = ""

    def _summary(self) -> str:
        """Return the failure itself, without the hint."""
        return super().__str__()

    def __str__(self) -> str:
        return f"{self._summary()} {self._hint}".strip()

    def __reduce__(self) -> tuple[object, ...]:
        return (_rebuild, (type(self), self.args, dict(self.__dict__)))


class OcxExecutionError(OcxError):
    """Base for failures an ocx process raised — non-zero exits and timeouts.

    `except OcxExecutionError` is the catch shape a caller usually wants: it
    covers both a process that failed and one that never finished.

    Attributes:
        argv: The argv that ran, with secrets already redacted by `_process`.
        stderr: Captured stderr in full; the message shows only its tail.
    """

    argv: tuple[str, ...]
    stderr: str

    def __init__(self, argv: Sequence[str], stderr: str = "") -> None:
        super().__init__()
        self.argv = tuple(argv)
        self.stderr = stderr

    def _stderr_excerpt(self) -> str:
        """Return the trailing slice of stderr for the message, or empty."""
        text = self.stderr.strip()
        if not text:
            return ""
        if len(text) > _STDERR_EXCERPT:
            text = f"...{text[-_STDERR_EXCERPT:]}"
        return f" stderr: {text}"


class OcxProcessError(OcxExecutionError):
    """An ocx process exited non-zero.

    Attributes:
        exit_code: The exit status. `int` rather than `ExitCode` because a
            process killed by a signal exits with a code ocx never assigns,
            and building an error must not itself raise.
        attempts: Attempts made; greater than 1 when a retry policy was
            active. `_retry` sets it on the final failure.
    """

    _hint = "Inspect `.stderr` for the ocx log, or re-run with log_level='debug' for more detail."

    exit_code: int
    attempts: int

    def __init__(self, exit_code: int, argv: Sequence[str], stderr: str = "", *, attempts: int = 1) -> None:
        super().__init__(argv, stderr)
        self.exit_code = exit_code
        self.attempts = attempts

    @property
    def retryable(self) -> bool:
        """Whether ocx called this failure transient (exit 75).

        Fixed semantics, independent of any `RetryPolicy`: this reports what
        ocx said about the failure, not whether a caller chose to retry it.
        """
        return self.exit_code == ExitCode.TEMP_FAIL

    def _summary(self) -> str:
        retried = f" after {self.attempts} attempts" if self.attempts > 1 else ""
        return f"`{shlex.join(self.argv)}` exited {self.exit_code}{retried}.{self._stderr_excerpt()}"


class UsageError(OcxProcessError):
    """ocx rejected the command line (exit 64)."""

    _hint = "Check the arguments against `ocx <command> --help` — the SDK forwards them verbatim."


class DataError(OcxProcessError):
    """Input or resolved data was malformed, ambiguous, or platform-mismatched (exit 65)."""

    _hint = "Check the identifiers and the target platform in the request."


class UnavailableError(OcxProcessError):
    """A resource is unavailable and ocx classified it as non-transient (exit 69)."""

    _hint = "Retrying will not help — check the registry or mirror the request resolved to."


class IoError(OcxProcessError):
    """A filesystem or network I/O operation failed (exit 74)."""

    _hint = "Check free space and permissions on $OCX_HOME and the cache directory."


class TempFailError(OcxProcessError):
    """A transient failure ocx marked as retry-safe (exit 75)."""

    _hint = "Retry with a policy — `OcxConfig(retry=RetryPolicy())` — or try again later."


class PermissionDeniedError(OcxProcessError):
    """ocx was denied access to a resource (exit 77)."""

    _hint = "Check ownership and permissions on $OCX_HOME, the store, and the config directory."


class ConfigError(OcxProcessError):
    """ocx's configuration is missing or invalid (exit 78)."""

    _hint = "Check ocx.toml and config.toml, or run hermetically with `OcxConfig(no_config=True)`."


class NotFoundError(OcxProcessError):
    """A package, registry, or reference does not exist (exit 79).

    Covers both things ocx can fail to find: the identifier a call named, and
    the project file a `--project` pointed at.
    """

    _hint = (
        "Check the identifier and that the registry actually hosts it — or, on a project-tier call, "
        "that `Ocx.project(...)` names a real ocx.toml."
    )


class AuthError(OcxProcessError):
    """Registry authentication or authorization failed (exit 80)."""

    _hint = (
        "Set OCX_AUTH_<SLUG>_* for the registry, pass `OcxConfig(auth=...)`, or run `ocx.login(...)` — "
        "auth failures are never retried."
    )


class PolicyBlockedError(OcxProcessError):
    """ocx refused the operation under an offline or frozen policy (exit 81)."""

    _hint = "Drop `offline=`/`frozen=`, or pre-populate ocx.lock so the request resolves without the network."


class DirtyRcBlockError(OcxProcessError):
    """A managed-config fence carried local edits and refused to overwrite them (exit 82)."""

    _hint = "Review the local edits inside the managed fence, then re-run with `force=True`."


class OcxTimeoutError(OcxExecutionError):
    """ocx did not finish within the timeout and the child was terminated.

    Attributes:
        timeout: The per-attempt budget, in seconds, that expired.
        argv: The argv that ran.
        stderr: Whatever stderr was captured before the child was killed.
    """

    _hint = "Raise `timeout=`, or check whether the registry is reachable — timeouts are not retried by default."

    timeout: float

    def __init__(self, timeout: float, argv: Sequence[str], stderr: str = "") -> None:
        super().__init__(argv, stderr)
        self.timeout = timeout

    def _summary(self) -> str:
        return f"`{shlex.join(self.argv)}` timed out after {self.timeout}s.{self._stderr_excerpt()}"


class BootstrapError(OcxError):
    """Base for failures while resolving, downloading, or installing an ocx binary."""

    _hint = "Check the bootstrap arguments passed to `bootstrap.ensure()`."


class DownloadError(BootstrapError):
    """A manifest or artifact could not be fetched."""

    _hint = "Check network access to the dist host, pass `mirror_url=` for an internal mirror, or warm the cache."


class ChecksumMismatchError(BootstrapError):
    """A downloaded artifact did not match the sha256 the manifest declared."""

    _hint = (
        "Never retried — the bytes are wrong, not late. Verify the mirror serves the pinned release, "
        "then re-pin `dist=` to a `dist/<sha256>.json` snapshot."
    )


class DistManifestError(BootstrapError):
    """A dist manifest was malformed, unparseable, or failed field validation."""

    _hint = "Pin a snapshot with `DistSource.url(..., sha256=...)`, or report the manifest to its publisher."


class UnsupportedPlatformError(BootstrapError):
    """No ocx release matches the host platform."""

    _hint = "Install ocx by hand for this platform and pass `exe=` to `Ocx()`."


class OcxNotFoundError(OcxError):
    """No ocx binary could be discovered."""

    _hint = "Install one with `ocx_sdk.bootstrap.ensure()`, set OCX_SDK_EXE, or pass `exe=` to `Ocx()`."


class VersionCompatError(OcxError):
    """The discovered ocx binary is older than this SDK supports.

    Attributes:
        found: The version the binary reported.
        minimum: The oldest version this SDK is tested against.
    """

    _hint = "Upgrade ocx — `bootstrap.ensure(version=...)` installs a known-good build — or pin an older ocx-sdk."

    found: str
    minimum: str

    def __init__(self, found: str, minimum: str) -> None:
        super().__init__(f"ocx {found} is older than the minimum supported {minimum}.")
        self.found = found
        self.minimum = minimum


_EXIT_CODE_ERRORS: dict[ExitCode, type[OcxProcessError]] = {
    ExitCode.USAGE: UsageError,
    ExitCode.DATA_ERR: DataError,
    ExitCode.UNAVAILABLE: UnavailableError,
    ExitCode.IO_ERR: IoError,
    ExitCode.TEMP_FAIL: TempFailError,
    ExitCode.NO_PERM: PermissionDeniedError,
    ExitCode.CONFIG: ConfigError,
    ExitCode.NOT_FOUND: NotFoundError,
    ExitCode.AUTH: AuthError,
    ExitCode.POLICY_BLOCKED: PolicyBlockedError,
    ExitCode.DIRTY_RC_BLOCK: DirtyRcBlockError,
}
"""Exit code to exception class, for `_process`. Codes absent here (notably the
generic `FAILURE`) raise a plain `OcxProcessError`."""
