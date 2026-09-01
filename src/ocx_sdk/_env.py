# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The OCX Authors

"""Spawn environment assembly and secret redaction (contract v0.1 C-009).

Every child environment this SDK hands to a process is composed here, and
every secret that env carries is scrubbed through the `redact` callable
returned alongside it. `_process` composes nothing and holds no secret
values: it takes a finished `SpawnEnv` and runs `redact` over whatever text
it is about to log, attach to a `CommandResult`, or raise (§6, §9, §12).

One choke point means one place to audit. The layering rule that keeps it
true: no other module composes a child env or reads a credential value.

Three transformations happen, in order:

1. **Neutralize.** Every key in `_NEUTRALIZED_KEYS` is always dropped,
   whatever case the host spelled it in — the SDK targets a project through
   an explicit `--project`, and an inherited `OCX_PROJECT` would silently
   retarget every call; an inherited `OCX_NO_VERIFY` would silently disable
   signature verification.
2. **Apply the config.** `OcxConfig` fields map onto the `OCX_*` wire vars
   ocx reads. A field typed `X | None` uses `None` for "leave whatever the
   host had"; a plain `bool` has no such tier, so `False` means "not
   requested" and leaves the ambient value alone.
3. **Apply auth.** Ambient `OCX_AUTH_*` passes through untouched, except
   where `config.auth` names the same registry — explicit configuration wins
   over the ambient environment, per slug.

**The case rule (D14), stated once for the whole module.** Ambient names are
matched on `key.upper()` everywhere: all three passes and the redaction set.
`HostEnv.current()` cannot hand us a lower-cased `ocx_no_verify` (Windows
upper-cases its env names, POSIX would not resolve one), but a hand-built
`HostEnv` can — and a Windows child resolves env lookups case-insensitively,
so two dict keys differing only in case are one variable to it and that
spelling arrives live.
"""

from __future__ import annotations

import base64
import json
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Final

from ._config import OcxConfig
from ._errors import OcxError
from ._types import Auth, BasicAuth, EnvValue, HostEnv, ListVar, PathVar

_NEUTRALIZED_KEYS: Final = (
    "OCX_PROJECT",
    "OCX_GLOBAL",
    "OCX_QUIET",
    "OCX_NO_VERIFY",
    "OCX_NO_HOOK",
    "OCX_NO_COMPLETIONS",
)
"""Ambient variables dropped from every spawn, whatever the config says.

`OCX_NO_VERIFY` is the security one (D12): ambient, it silently disables
signature verification, and a skipped verification is byte-identical to a
passed one from the SDK side. Nothing is lost by dropping it — `install` and
`pull` take `verify=`, and the argv flag outranks the variable, so a caller who
means it says so at the call site. `OCX_NO_HOOK` and `OCX_NO_COMPLETIONS`
govern interactive-shell affordances a spawned child never renders.

Spelled uppercase here, but matched case-insensitively — see `build_spawn_env`.
"""

_AUTH_PREFIX: Final = "OCX_AUTH_"
"""Namespace of the credential variables ocx reads."""

_AUTH_SUFFIXES: Final = ("TYPE", "USER", "TOKEN")
"""The `OCX_AUTH_<SLUG>_*` triple ocx reads (ocx_lib `auth::get_env_auth`)."""

_REDACTED: Final = "***"
"""What a secret is replaced with in logs, errors, and captured output."""

_NON_ALNUM: Final = re.compile(r"[^a-zA-Z0-9]")
"""ocx's strict `StringExt::to_slug`: everything else becomes an underscore."""

_SLUG_SAFE: Final = re.compile(r"[A-Za-z0-9_]+")
"""What a slug must look like before it may name an env var — fail-closed."""

_VALID_ENV_KEY: Final = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
"""ocx's `env::is_valid_env_key` — the POSIX environment-name grammar."""


@dataclass(frozen=True, slots=True)
class SpawnEnv:
    """A finished child environment plus the redactor for its secrets.

    The pinned seam between `_env` and `_process`: assembly and secret
    knowledge stop here, and `_process` receives `redact` as an explicit
    argument rather than reaching for module-global state.

    `mapping` deliberately *contains* the secrets — the child cannot
    authenticate without them. The secrecy property is that they never reach
    a human-readable surface: `__repr__` shows key names and masks every
    value, and `redact` removes them from arbitrary text.

    Attributes:
        mapping: The environment to spawn with, secrets included. Read-only,
            so a consumer cannot rewrite a composed env behind `_env`'s back.
        redact: Replaces every credential in `mapping` with `***` — the ones
            the config supplied *and* the ones the host already exported.
            Identity when the composed env carries none.
    """

    mapping: Mapping[str, str] = field(repr=False)
    redact: Callable[[str], str] = field(repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "mapping", MappingProxyType(dict(self.mapping)))

    def __repr__(self) -> str:
        return f"SpawnEnv(keys=[{', '.join(sorted(self.mapping))}], values={_REDACTED})"


def build_spawn_env(host: HostEnv, config: OcxConfig) -> SpawnEnv:
    """Compose the environment for one ocx spawn.

    The single assembly point for child environments (§6). `host` is already
    narrowed to whatever the caller chose to inherit — `build_spawn_env` adds
    the config on top rather than filtering again.

    Args:
        host: The snapshot the child inherits from.
        config: The session configuration whose fields become `OCX_*` vars.

    Returns:
        The finished mapping and the redactor for the secrets it carries.

    Raises:
        OcxError: A registry in `config.auth` canonicalizes to a slug that
            cannot name an environment variable. Refused rather than
            emitted anonymously.
    """
    # Case rule, module docstring. Over-dropping a variable nothing reads is
    # the cheap direction; leaking the kill switch is not.
    mapping = {key: value for key, value in host.source.items() if key.upper() not in _NEUTRALIZED_KEYS}

    _apply_config(mapping, config)
    _apply_auth(mapping, config.auth)

    return SpawnEnv(mapping, _make_redactor(_secrets(mapping)))


def _secrets(mapping: Mapping[str, str]) -> list[str]:
    """Collect every credential the composed env carries.

    Read off the finished mapping rather than off `config.auth`, so a
    credential the *host* exported is scrubbed too. The SDK did not choose an
    ambient `OCX_AUTH_*`, but it still travels to the child and can surface in
    ocx's own trace output, which is exactly what `redact` exists to catch.

    Each basic credential also contributes the base64 of `user:password` —
    the form an `Authorization: Basic` header carries, so a trace-level log
    of the request would otherwise leak it past a plaintext-only scrub.

    Names are matched per the module's case rule. A credential left out of
    this set by a case mismatch reaches captured stderr, `on_log`, logged argv
    and exception text — CWE-532. The `_USER` companion is therefore indexed
    to **every** value spelled under that name, each contributing its own
    header form: a last-wins index would drop precisely the live pair, since
    on POSIX the child resolves the exact-case companion.
    """
    users: dict[str, list[str]] = {}
    for key, value in mapping.items():
        users.setdefault(key.upper(), []).append(value)

    secrets: list[str] = []
    for key, token in mapping.items():
        name = key.upper()
        if not (name.startswith(_AUTH_PREFIX) and name.endswith("_TOKEN")):
            continue
        secrets.append(token)
        secrets.extend(
            base64.b64encode(f"{user}:{token}".encode()).decode()
            for user in users.get(f"{name.removesuffix('TOKEN')}USER", ())
        )
    return secrets


def _put(mapping: dict[str, str], key: str, value: str | None) -> None:
    """Write (or clear) one wire variable, dropping every ambient spelling of it.

    `key` is the uppercase name ocx documents; every key the host exported
    that differs from it only in case goes first, per the module's case rule.
    `value=None` clears the name outright.

    Set-only semantics live at the **call sites**, not here: a field the
    caller never set must not reach `_put` at all, or an unrequested flag
    would silently clear a value the host meant to keep. That is why the
    `if requested:` / `if value is not None:` guards in `_apply_config` sit
    outside the call rather than folding into this `None` arm.
    """
    for spelling in [name for name in mapping if name.upper() == key]:
        del mapping[spelling]
    if value is not None:
        mapping[key] = value


def _apply_config(mapping: dict[str, str], config: OcxConfig) -> None:
    """Write `config`'s fields onto `mapping` as the `OCX_*` vars ocx reads.

    A plain `bool` is set-only: `False` carries no "leave the ambient value"
    tier, so it means "not requested" and the host's value survives. The
    fields that can actively clear an ambient value are typed `| None`, and
    the `None` arm is what defers to the host — except on the two variables
    written either way, `OCX_NO_UPDATE_CHECK` and `OCX_SIGSTORE_TRUSTED_ROOT`,
    where saying nothing has to mean ocx's default rather than the host's
    value. Both carry the reason at their write site.

    Every write and clear **in this function** goes through `_put`, so the
    SDK's answer replaces the host's in any spelling. `_apply_auth` writes
    directly; `_clear_claimed_triples` says why that is safe.
    """
    for key, requested in (
        ("OCX_OFFLINE", config.offline),
        ("OCX_FROZEN", config.frozen),
        ("OCX_NO_CONFIG", config.no_config),
    ):
        if requested:
            _put(mapping, key, "1")

    # The one flag whose default is True, so it is written either way: a
    # caller asking for the update check back has to beat an ambient
    # OCX_NO_UPDATE_CHECK=1, which a set-only write could not do. ocx parses
    # "0" as false through env::flag → BooleanString.
    _put(mapping, "OCX_NO_UPDATE_CHECK", "1" if config.no_update_check else "0")

    for key, value in (
        ("OCX_HOME", config.home),
        ("OCX_CONFIG", config.config),
        ("OCX_INDEX", config.index),
        ("DOCKER_CONFIG", config.docker_config),
        ("OCX_JOBS", config.jobs),
    ):
        if value is not None:
            _put(mapping, key, str(value))

    if config.mirrors is not None:
        _put(mapping, "OCX_MIRRORS", json.dumps(dict(config.mirrors)))

    if config.managed_config is not None and not config.no_config:
        # `MANAGED_CONFIG_DISABLED` travels as the empty string on purpose:
        # ocx reads that as unset, which is how a caller force-disables a tier
        # a config file would otherwise switch on. Skipped entirely under
        # `no_config`, which suppresses the managed tier on ocx's own read
        # side — naming a source beside it would describe a tier already off.
        _put(mapping, "OCX_MANAGED_CONFIG", config.managed_config)

    # D13: written either way, like OCX_NO_UPDATE_CHECK and for the same
    # reason — a caller asking for ocx's own root of trust back has to beat an
    # ambient OCX_SIGSTORE_TRUSTED_ROOT, which a set-only write could not do.
    # This variable does not disable verification, it redefines what "trusted"
    # means, and it outranks `[trust.sigstore]`, so a hostile ambient value
    # repoints Fulcio, CT, and Rekor while every install still reports
    # verified — failure presenting as success. `None` therefore clears rather
    # than defers, which is the `_put` arm that takes no value.
    root = config.sigstore_trusted_root
    _put(mapping, "OCX_SIGSTORE_TRUSTED_ROOT", None if root is None else str(root))

    if config.no_config_refresh is not None:
        if config.no_config_refresh:
            _put(mapping, "OCX_NO_CONFIG_REFRESH", "1")
        else:
            _put(mapping, "OCX_NO_CONFIG_REFRESH", None)

    if config.insecure_registries is not None:
        for registry in config.insecure_registries:
            if "," in registry:
                raise OcxError(
                    f"Insecure-registry entry {registry!r} contains ',', which is what ocx "
                    f"splits OCX_INSECURE_REGISTRIES on, so it would widen the plaintext "
                    f"allowlist to hosts that were never listed. Pass one host per entry."
                )
        # Fail-closed: an explicit value replaces the ambient set outright, so
        # `()` blocks a plaintext registry a CI image exported instead of
        # quietly inheriting it.
        _put(mapping, "OCX_INSECURE_REGISTRIES", ",".join(config.insecure_registries))


def _apply_auth(mapping: dict[str, str], auth: Mapping[str, Auth]) -> None:
    """Replace the ambient credential triple for every configured registry.

    Ambient `OCX_AUTH_*` for any other registry is left alone — value sourcing
    is the caller's job, and a config entry only overrides its own slug.

    Raises:
        OcxError: A registry canonicalizes to a slug that cannot name an
            environment variable, or two registries share one slug.
    """
    claimed: dict[str, str] = {}
    for registry in auth:
        slug = _registry_slug(registry)
        if not _SLUG_SAFE.fullmatch(slug):
            raise OcxError(
                f"Registry {registry!r} canonicalizes to an empty slug, which cannot name an "
                f"environment variable, so its credentials would be dropped and the pull would "
                f"run anonymously. Pass the registry as ocx spells it, such as 'ghcr.io'."
            )
        if slug in claimed:
            raise OcxError(
                f"Registries {claimed[slug]!r} and {registry!r} both canonicalize to the slug "
                f"{slug!r}, so they share one OCX_AUTH_{slug}_* triple and whichever came last "
                f"would silently take over the other's credentials. Configure one of them."
            )
        claimed[slug] = registry

    _clear_claimed_triples(mapping, claimed)

    for registry, credentials in auth.items():
        prefix = f"OCX_AUTH_{_registry_slug(registry)}"
        if isinstance(credentials, BasicAuth):
            mapping[f"{prefix}_TYPE"] = "basic"
            mapping[f"{prefix}_USER"] = credentials.user
            mapping[f"{prefix}_TOKEN"] = credentials.password
        else:
            mapping[f"{prefix}_TYPE"] = "token"
            mapping[f"{prefix}_TOKEN"] = credentials.token


def _clear_claimed_triples(mapping: dict[str, str], claimed: Mapping[str, str]) -> None:
    """Drop every ambient `OCX_AUTH_*` triple a configured registry claims.

    The whole triple goes, not just the keys about to be written. Today a
    written `_TYPE` is authoritative — ocx's `get_env_auth` reads `_USER` only
    under `AuthType::Basic` — so a stale `_USER` beside a bearer token is
    inert rather than a live confusion. This is defense in depth against that
    precedence changing, not a fix for a bug that exists now.

    Prefix, slug and suffix are all matched per the module's case rule, which
    matters doubly here: registry hosts are case-insensitive, so an ambient
    `ocx_auth_ghcr_io_*` is a credential for the registry a configured
    `ghcr.io` covers, yet `to_slug` deliberately does not case-fold. Fail
    closed — explicit configuration wins over the ambient environment. This
    sweep is also what lets `_apply_auth` write its triple without `_put`:
    every ambient spelling of the claimed slug is already gone.
    """
    wanted = {slug.casefold() for slug in claimed}
    for key in list(mapping):
        name = key.upper()
        if not name.startswith(_AUTH_PREFIX):
            continue
        slug, _, suffix = name.removeprefix(_AUTH_PREFIX).rpartition("_")
        if suffix in _AUTH_SUFFIXES and slug.casefold() in wanted:
            mapping.pop(key)


def serialize_env_value(key: str, value: EnvValue) -> str:
    """Render one `[env]` entry as ocx's `--env KEY[:TYPE[:SEP]]=VALUE`.

    A bare `str` and a `ConstVar` both render as plain `KEY=VALUE`: ocx reads
    an unqualified entry as a constant, so the `:constant` marker adds
    nothing.

    Args:
        key: The variable name.
        value: The entry. `str`/`ConstVar` replace, `PathVar` prepends,
            `ListVar` appends.

    Returns:
        One argument for ocx's repeatable `--env` flag.

    Raises:
        OcxError: `key` is outside the POSIX environment-name grammar, `key`
            is in the `OCX_*`/`__OCX_*` namespace ocx reserves, or a
            `ListVar` separator is one ocx refuses.
    """
    if not _VALID_ENV_KEY.fullmatch(key):
        raise OcxError(
            f"--env key {key!r} is not a valid environment variable name. Use a letter or "
            f"underscore followed by letters, digits and underscores; ':' and '=' in "
            f"particular are structure in ocx's KEY[:TYPE[:SEP]]=VALUE grammar, not name "
            f"characters."
        )
    # Runs after the grammar gate, so `key` is ASCII and `.upper()` cannot
    # case-fold its way past the prefixes the way a Unicode spelling might.
    if key.upper().startswith(("OCX_", "__OCX_")):
        raise OcxError(
            f"--env key {key!r} is reserved: ocx refuses the OCX_* and __OCX_* namespace so "
            f"that a project cannot reconfigure how ocx itself resolves. Set the "
            f"corresponding OcxConfig field instead."
        )

    if isinstance(value, PathVar):
        return f"{key}:path={value.value}"

    if isinstance(value, ListVar):
        if value.separator is None:
            return f"{key}:list={value.value}"
        if not value.separator or any(char in value.separator for char in "=\n\r"):
            raise OcxError(
                f"--env separator {value.separator!r} for {key!r} is one ocx cannot use: a "
                f"separator must be non-empty and free of '=', newline and carriage return. "
                f"Use the separator the variable itself uses, such as ',' or ':'."
            )
        if value.value.startswith(value.separator) or value.value.endswith(value.separator):
            raise OcxError(
                f"--env value {value.value!r} for {key!r} starts or ends with its own "
                f"separator {value.separator!r}, which makes the append ambiguous: the "
                f"separator the fold adds fuses with the value's own. Drop the leading or "
                f"trailing separator — ocx joins the entries itself."
            )
        return f"{key}:list:{value.separator}={value.value}"

    # A bare `str` and a `ConstVar` both render unqualified — ocx reads an
    # entry with no type marker as a constant, so `:constant` adds nothing.
    literal = value if isinstance(value, str) else value.value
    return f"{key}={literal}"


def _registry_slug(registry: str) -> str:
    """Port ocx's `StringExt::to_slug` — every non-alphanumeric becomes `_`.

    Carve-out (b) of design goal 4: the SDK reimplements this one
    canonicalization because `OCX_AUTH_<SLUG>_*` names must be built before
    ocx runs. Pinned by `tests/fixtures/slug_fixtures.json`, extracted from
    the ocx source at the tested tag.

    Strict by design: no case folding, and dots and dashes do not survive.
    ocx's second, relaxed slug (which keeps `.`, `_`, `-`) addresses on-disk
    paths and is deliberately not ported here.

    Args:
        registry: The registry as the caller spelled it.

    Returns:
        The slug, which may be empty when `registry` is. Callers building an
        env var name must reject that; see `build_spawn_env`.
    """
    return _NON_ALNUM.sub("_", registry)


def _make_redactor(secrets: Iterable[str]) -> Callable[[str], str]:
    """Build the exact-string redactor for a set of secret values.

    Args:
        secrets: The values to scrub. Empty strings are ignored — they would
            match everywhere.

    Returns:
        A callable replacing every secret with `***`, longest first so that
        a secret containing another is masked whole.
    """
    ordered = sorted({secret for secret in secrets if secret}, key=len, reverse=True)

    def redact(text: str) -> str:
        for secret in ordered:
            text = text.replace(secret, _REDACTED)
        return text

    return redact
