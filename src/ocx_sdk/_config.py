# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The OCX Authors

"""Session configuration for the runtime layer (contract C-007).

`OcxConfig` is the one place a caller states policy for everything a handle
spawns: which ocx home, which config tier, which credentials, how long to wait.
It carries data only — `_env.build_spawn_env()` turns it into argv globals and
child environment variables, and nothing here touches a process, a file, or the
ambient environment.

Two fields decide security posture and are worth reading twice:

- `insecure_registries` is `None` to inherit whatever the ambient environment
  allows, and any explicit value — **including the empty tuple** — to replace
  the ambient set entirely. `()` is therefore "plaintext nowhere", the
  fail-closed answer to an `OCX_INSECURE_REGISTRIES` a CI image exported.
- `auth` and `insecure_registries` naming the same registry means credentials
  would travel over plaintext HTTP (CWE-319), so the constructor warns. It
  warns again on a non-lowercase `auth` key, which would export credentials
  under a variable name ocx does not read.
"""

from __future__ import annotations

import warnings
from collections.abc import Collection, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import TypedDict

from ._types import Auth, LogLevel, RetryPolicy


class ConfigOverrides(TypedDict, total=False):
    """The `OcxConfig` fields a `with_config` call may replace, all optional.

    What makes `Ocx.with_config(...)` and `Project.with_config(...)` check
    their keyword arguments instead of accepting `**overrides: Any`, so a
    misspelled field is a type error rather than a `TypeError` at runtime.
    Public for the same reason `MaybeRetry` is: a wrapper around this SDK can
    forward a caller's overrides with `**overrides: Unpack[ConfigOverrides]`
    rather than re-declaring the field list itself.

    Mirrors `OcxConfig` field for field — a field added there and not here is
    a field `with_config` would refuse.

    Optional at the class level rather than per field: this module postpones
    annotation evaluation, and a `NotRequired[...]` inside a string annotation
    is invisible to `__required_keys__`, so anything introspecting the type at
    runtime would be told every field was mandatory. Every field is optional
    here anyway, which is exactly what `total=False` says.

    Example:
        ```python
        from typing import Unpack

        from ocx_sdk import ConfigOverrides, Ocx


        def hermetic(ocx: Ocx, **overrides: Unpack[ConfigOverrides]) -> Ocx:
            return ocx.with_config(no_config=True, **overrides)
        ```
    """

    home: Path | None
    offline: bool
    frozen: bool
    config: Path | None
    no_config: bool
    managed_config: str | None
    auth: Mapping[str, Auth]
    insecure_registries: Collection[str] | None
    docker_config: Path | None
    index: Path | None
    jobs: int | None
    log_level: LogLevel | None
    mirrors: Mapping[str, str] | None
    no_update_check: bool
    no_config_refresh: bool | None
    retry: RetryPolicy | None
    timeout: float | None


@dataclass(frozen=True, slots=True)
class OcxConfig:
    """Policy for every ocx process a handle spawns.

    Containers are snapshotted at construction — the mappings as read-only
    proxies, `insecure_registries` as a tuple — so neither mutating the argument
    afterwards nor reaching into the field can rewrite a config a handle already
    holds. Derive a variant with `Ocx.with_config(...)`
    rather than reaching for a mutation that the frozen dataclass refuses.

    Attributes:
        home: `$OCX_HOME` for the spawned process; `None` keeps ocx's default.
        offline: Refuse every network access.
        frozen: Refuse any lockfile change.
        config: Explicit `config.toml`; `None` keeps ocx's discovery chain.
        no_config: Hermetic mode — drops the discovered chain and the managed
            tier. Wins over `managed_config` when both are set, which is ocx's
            own precedence, not an SDK invention.
        managed_config: OCI reference of the managed config tier.
            `MANAGED_CONFIG_DISABLED` force-disables an ambient one; `None`
            leaves the ambient setting alone.
        auth: Credentials per registry, keyed lowercase. An entry wins over an
            ambient `OCX_AUTH_<SLUG>_*` for the same registry, whatever case
            that variable spells the slug in.
        insecure_registries: Registries reachable over plaintext HTTP. `None`
            inherits the ambient set; any explicit value replaces it entirely,
            so `()` blocks an ambient re-enable (fail-closed).
        docker_config: Directory for `DOCKER_CONFIG`. Keep it `0700` — it holds
            registry credentials.
        index: Explicit index path; `None` keeps ocx's default.
        jobs: Parallel job cap; `None` keeps ocx's default.
        log_level: ocx trace verbosity. At `'trace'` ocx may print secrets of
            its own; the SDK can only redact the values it was given.
        mirrors: Registry host to mirror host, serialized into `OCX_MIRRORS`.
        no_update_check: Suppress ocx's update check. `True` by default — an
            SDK call is a program step, not an interactive session.
        no_config_refresh: Suppress the managed-config refresh; `None` leaves
            the decision to whoever owns that tier.
        retry: Retry policy for failures ocx marked transient; `None` disables
            retrying.
        timeout: Per-attempt budget in seconds; `None` waits indefinitely.

    Example:
        >>> OcxConfig().insecure_registries is None
        True
        >>> OcxConfig(insecure_registries=()).insecure_registries
        ()
    """

    home: Path | None = None
    offline: bool = False
    frozen: bool = False
    config: Path | None = None
    no_config: bool = False
    managed_config: str | None = None
    auth: Mapping[str, Auth] = field(default_factory=dict[str, Auth])
    insecure_registries: Collection[str] | None = None
    docker_config: Path | None = None
    index: Path | None = None
    jobs: int | None = None
    log_level: LogLevel | None = None
    mirrors: Mapping[str, str] | None = None
    no_update_check: bool = True
    no_config_refresh: bool | None = None
    retry: RetryPolicy | None = None
    timeout: float | None = None

    def __post_init__(self) -> None:
        if isinstance(self.insecure_registries, str):
            raise TypeError(
                "insecure_registries takes a collection of registry hosts, not a single string — "
                f"pass ({self.insecure_registries!r},) to allow plaintext for that one registry."
            )
        object.__setattr__(self, "auth", MappingProxyType(dict(self.auth)))
        if self.mirrors is not None:
            object.__setattr__(self, "mirrors", MappingProxyType(dict(self.mirrors)))
        if self.insecure_registries is not None:
            object.__setattr__(self, "insecure_registries", tuple(self.insecure_registries))
        # Registry hosts are case-insensitive, so `Reg.Example` in `auth` and
        # `reg.example` in `insecure_registries` are the same registry — and the
        # same plaintext exposure.
        plaintext = {registry.casefold() for registry in self.insecure_registries or ()}
        both = sorted(registry for registry in self.auth if registry.casefold() in plaintext)
        if both:
            warnings.warn(
                f"Registries {', '.join(both)} carry credentials in `auth` and are listed in "
                "`insecure_registries`, so those credentials would travel over plaintext HTTP (CWE-319). "
                "Drop them from `insecure_registries`, or use anonymous access there.",
                stacklevel=3,
            )
        # ocx slugs the registry as it resolved it — lowercase, in practice —
        # and `to_slug` does not case-fold, so a mixed-case key here names an
        # OCX_AUTH_* variable ocx will never look up: the credentials would be
        # exported dead and the pull would run anonymously. Silent-anonymous
        # is the outcome the slug carve-out forbids, so this refuses instead
        # of warning (and the case-fold clear in `_env` would additionally
        # have destroyed a working ambient triple on the way).
        mixed = sorted(registry for registry in self.auth if registry != registry.lower())
        if mixed:
            raise ValueError(
                f"Registries {', '.join(mixed)} in `auth` are not lowercase. Registry hosts are "
                "case-insensitive but the OCX_AUTH_<SLUG>_* variable name ocx reads is not, so those "
                "credentials would be exported under a name ocx does not read and the request would "
                "run anonymously. Spell them lowercase."
            )
