# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The OCX Authors

"""Shared vocabulary for both SDK layers (contract C-002).

Bootstrap and runtime speak the same types, so they live here rather than in
either layer. Everything is a frozen, slotted dataclass, a `StrEnum` mirroring
an ocx wire value, or a `Literal` alias for a set that only travels into argv.

Two rules shape this module:

- **Carry, don't parse.** `PackageRef` holds the identifier byte-for-byte;
  ocx owns the identifier grammar, and `str(ref)` gives it back unchanged.
- **Secrets never repr.** Credential fields are `field(repr=False)` and the
  class supplies a masked `__repr__`; the generated one would leak into logs,
  tracebacks, and pytest diffs (CWE-532).
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Final, Literal

from ._errors import ExitCode

TESTED_OCX_VERSION: Final = "0.5.8"
"""The ocx version this SDK's contract tests run against."""

MIN_SUPPORTED: Final = "0.5.8"
"""Oldest ocx the SDK accepts; below it the compat gate raises `VersionCompatError`."""

MANAGED_CONFIG_DISABLED: Final = ""
"""Wire sentinel that force-disables the managed config tier for a spawn."""

type LogLevel = Literal["off", "error", "warn", "info", "debug", "trace"]
"""ocx `--log-level` values. A `Literal` because nothing but argv consumes it."""


class Channel(StrEnum):
    """Release channel of the dist manifest."""

    STABLE = "stable"
    NEXT = "next"


class InstallEnv(StrEnum):
    """The `OCX_INSTALL_*` grammar the setup script defines.

    `bootstrap.ensure()` honors these verbatim, one rung below its explicit
    keyword arguments and one above its own defaults.
    """

    VERSION = "OCX_INSTALL_VERSION"
    DIST_URL = "OCX_INSTALL_DIST_URL"
    MIRROR_URL = "OCX_INSTALL_MIRROR_URL"
    REPO = "OCX_INSTALL_REPO"
    FORCE = "OCX_INSTALL_FORCE"
    QUIET = "OCX_INSTALL_QUIET"


@dataclass(frozen=True, slots=True)
class BasicAuth:
    """Username and password for a registry.

    Example:
        >>> BasicAuth("ci", "hunter2")
        BasicAuth(user='ci', password=***)
    """

    user: str
    password: str = field(repr=False)

    def __repr__(self) -> str:
        return f"BasicAuth(user={self.user!r}, password=***)"


@dataclass(frozen=True, slots=True)
class BearerAuth:
    """A bearer token for a registry.

    Example:
        >>> BearerAuth("ghp_secret")
        BearerAuth(token=***)
    """

    token: str = field(repr=False)

    def __repr__(self) -> str:
        return "BearerAuth(token=***)"


type Auth = BasicAuth | BearerAuth
"""Credentials for one registry. `None` elsewhere means anonymous."""


_MINIMAL_KEYS: Final = ("PATH", "HOME", "TMPDIR")
"""Variables `HostEnv.minimal()` keeps on every platform."""

_MINIMAL_KEYS_WINDOWS: Final = ("SYSTEMROOT", "TEMP")
"""Variables `HostEnv.minimal()` additionally keeps on Windows."""

_NO_METADATA: Final[Mapping[str, object]] = MappingProxyType({})
"""Shared empty metadata for refs that came from a bare identifier."""

_REPR_KEYS: Final = 12
"""Key names `HostEnv.__repr__` lists before it starts counting the rest."""


@dataclass(frozen=True, slots=True)
class HostEnv:
    """A snapshot of the environment a spawned ocx inherits.

    The mapping is copied and made read-only at construction, so neither a
    later mutation of the source dict nor a write through `.source` can
    rewrite a snapshot that was already taken. (A read-only snapshot is not
    picklable — an implementation detail, not a contract.)

    `repr` lists key names only. An ambient snapshot holds `OCX_AUTH_*`
    values, and a repr is exactly what a traceback or a pytest diff prints.

    Example:
        >>> HostEnv({"PATH": "/usr/bin", "TOKEN": "s"}).without("TOKEN").source["PATH"]
        '/usr/bin'
    """

    source: Mapping[str, str]

    def __post_init__(self) -> None:
        object.__setattr__(self, "source", MappingProxyType(dict(self.source)))

    def __repr__(self) -> str:
        keys = sorted(self.source)
        shown = ", ".join(repr(key) for key in keys[:_REPR_KEYS])
        rest = f", +{len(keys) - _REPR_KEYS} more" if len(keys) > _REPR_KEYS else ""
        return f"HostEnv(keys=[{shown}{rest}])"

    @classmethod
    def ambient(cls) -> HostEnv:
        """Return a snapshot of the current `os.environ`."""
        return cls(os.environ)

    @classmethod
    def clean(cls) -> HostEnv:
        """Return an empty environment.

        Hermetic, and a documented footgun: spawned tools lose `PATH` and fail
        in ways that read like anything but a missing variable. `minimal()` is
        the recovery.
        """
        return cls({})

    @classmethod
    def minimal(cls, *, windows: bool | None = None) -> HostEnv:
        """Return only the platform-essential variables from the ambient env.

        `PATH`, `HOME`, and `TMPDIR` everywhere; `SYSTEMROOT` and `TEMP` on
        Windows. Variables that are not set are simply absent.

        Args:
            windows: Force the platform branch. Defaults to detecting the
                host; tests pass it explicitly so both branches run anywhere.
        """
        on_windows = os.name == "nt" if windows is None else windows
        extra = _MINIMAL_KEYS_WINDOWS if on_windows else ()
        return cls.ambient().only(*_MINIMAL_KEYS, *extra)

    def only(self, *keys: str) -> HostEnv:
        """Return a snapshot narrowed to `keys` that are actually set."""
        return HostEnv({key: value for key, value in self.source.items() if key in keys})

    def without(self, *keys: str) -> HostEnv:
        """Return a snapshot with `keys` removed."""
        return HostEnv({key: value for key, value in self.source.items() if key not in keys})


@dataclass(frozen=True, slots=True)
class ConstVar:
    """An `[env]` value that replaces whatever the parent env had."""

    value: str


@dataclass(frozen=True, slots=True)
class PathVar:
    """An `[env]` value prepended to a PATH-like variable, deduplicated."""

    value: str


@dataclass(frozen=True, slots=True)
class ListVar:
    """An `[env]` value appended to a separated list.

    Attributes:
        value: The entry to append.
        separator: The list separator. `None` means the entry agrees with
            whatever separator the variable already uses; a conflict is an
            error at merge time, not a silent re-split.
    """

    value: str
    separator: str | None = field(default=None, kw_only=True)


type EnvValue = str | ConstVar | PathVar | ListVar
"""An `[env]` value. A bare `str` is a `ConstVar`, matching ocx's own grammar."""


@dataclass(frozen=True, slots=True, eq=False)
class PackageRef:
    """A package identifier carried verbatim from ocx, never parsed.

    ocx owns the identifier grammar. The SDK stores the string it was given
    and hands it back unchanged, so a ref that came out of one result flows
    into the next call's argv byte-for-byte.

    Identity is the identifier alone. `metadata` is decoration carried along
    from whichever JSON row produced the ref, so two refs to the same package
    from different commands must not compare unequal. The mapping is copied
    and made read-only at construction (and is therefore not picklable).

    Example:
        >>> ref = PackageRef("ocx.sh/astral-sh/uv:0.9.7")
        >>> str(ref)
        'ocx.sh/astral-sh/uv:0.9.7'
    """

    identifier: str
    metadata: Mapping[str, object] = _NO_METADATA

    def __post_init__(self) -> None:
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, PackageRef):
            return NotImplemented
        return self.identifier == other.identifier

    def __hash__(self) -> int:
        return hash(self.identifier)

    def __str__(self) -> str:
        return self.identifier


type PackageLike = str | PackageRef
"""Anything an identifier parameter accepts; coerced with `str()`."""


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """How to retry a failure ocx classified as transient.

    Attempt one is not a retry: `attempts=3` means at most two backoff waits.
    `timeout` is per attempt, so the worst case is
    `attempts * timeout + sum(backoff)`.

    Attributes:
        attempts: Total attempts, including the first.
        backoff: Delay before the first retry, in seconds.
        multiplier: Growth factor applied to `backoff` per retry.
        max_backoff: Ceiling for the computed delay.
        max_retry_after: Ceiling for a server-supplied `Retry-After`. It is
            honored verbatim up to this value rather than truncated to
            `max_backoff` — truncating it would hammer a registry that just
            asked for room. Beyond it the answer is to fail, not to wait.
        jitter: Draw the delay uniformly from `[0, delay]` (AWS full jitter).
        retry_on: Exit codes worth retrying. Applies to the process path
            only; `_dist` classifies by transport condition instead, so this
            set does not affect `bootstrap.ensure()`.
        sleep: Sleep seam, defaulting to `time.sleep`. Async drivers use
            `asyncio.sleep` and ignore this field.
    """

    attempts: int = 3
    backoff: float = 1.0
    multiplier: float = 2.0
    max_backoff: float = 30.0
    max_retry_after: float = 300.0
    jitter: bool = True
    retry_on: frozenset[ExitCode] = frozenset({ExitCode.TEMP_FAIL})
    sleep: Callable[[float], None] | None = None

    def __post_init__(self) -> None:
        if self.attempts < 1:
            raise ValueError(f"attempts must be at least 1, got {self.attempts}")
        if self.multiplier < 1:
            raise ValueError(f"multiplier must be at least 1, got {self.multiplier}")
        negative = [name for name in ("backoff", "max_backoff", "max_retry_after") if getattr(self, name) < 0]
        if negative:
            raise ValueError(f"{', '.join(negative)} must not be negative")
