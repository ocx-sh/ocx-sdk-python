# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The OCX Authors

"""Typed results parsed from ocx's JSON output (contract C-003).

Every `json.loads` in this SDK happens here. One command, one result struct,
one hand-written parser — no serialization framework, and unknown keys are
ignored so a newer ocx that adds a field keeps working against an older SDK.

Entry points come in two shapes, because ocx's own output does:

- a command whose payload is one object gets a `from_json(stdout)` classmethod,
- a command whose payload is a keyed map or a bare array gets a module-level
  `parse_*(stdout)` function, since there is no single object to hang it on.

Either way the argument is **raw stdout text**, never a pre-decoded object.

Three asymmetries in ocx's own surface are load-bearing here, and a future
contributor should not assume any two of them are interchangeable:

- **Three "give me a path" shapes.** `pull` (project tier) returns
  `{identifier: {path, kind}}` *plus* a sibling `advisories` key,
  `package which` returns `{identifier: {path, kind}}` with no sibling, and
  `package pull` returns `{identifier: "<path>"}` — a bare string.
- **Two mutators return an array**, not a keyed object: `package uninstall`
  and `package deselect` both emit `[{package, status, path}]`.
- **`package test` only emits the stable envelope for `--script` runs.** The
  `-- CMD` form prints the child's raw stdout verbatim even under
  `--format json`, so `TestResult.from_json` must never see it.

Empty stdout is an error here, deliberately. `lock --check` and
`update --check` succeed with no body at all, but "nothing to parse" is a fact
about the command, not about the payload — the client layer knows which calls
can return nothing and answers `None` for them without asking a parser.

Typing note: decoded JSON is `Any` by nature (`json.loads` says so), so
`from_dict` takes `Mapping[str, Any]` and the validation lives in `_need`,
which turns a missing required field into a message naming the command. Every
*field* on every struct is precisely typed — that is the boundary that matters.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Final, Literal

from ._types import TESTED_OCX_VERSION, ConstVar, EnvValue, ListVar, PackageRef, PathVar

if TYPE_CHECKING:
    from ._envmodel import ComposedEnv

_EMPTY: Final[Mapping[str, Any]] = MappingProxyType({})
"""Shared read-only stand-in for an absent JSON object."""

_EXCERPT: Final = 200
"""Characters of unparseable stdout quoted back in an error message."""

_PASSED: Final = "passed"
"""The one `package test` status that means the test passed."""

_ADVISORIES: Final = "advisories"
"""Project-tier `pull`'s sibling key, sitting among the identifier keys."""

type EnvEntryType = Literal["constant", "path", "list"]
"""The `type` an `ocx env` entry declares. Mirrors ocx's `--env KEY:TYPE=VALUE`."""

type InfoResult = Mapping[str, Mapping[str, Any] | None]
"""`package info`, keyed by identifier as given.

The value is `null` whenever the registry holds no description metadata, which
was every package reachable during the WP00 probe. The inner shape of a
non-null entry is UNCONFIRMED upstream, so it stays a pass-through mapping
rather than a struct that guesses at fields.
"""

_ENV_VALUES: Final[Mapping[str, Callable[[str, str | None], EnvValue]]] = {
    "constant": lambda value, _separator: ConstVar(value),
    "path": lambda value, _separator: PathVar(value),
    "list": lambda value, separator: ListVar(value, separator=separator),
}
"""ocx's `[env]` type tokens, mapped to the `_types` value each one folds as.

The parser validates against these keys and `compose` folds through them, so a
type ocx grows that the SDK cannot fold is refused at parse time rather than
silently mis-merged. Only `list` reads the second argument: `ocx env
--format json` puts a `separator` on a list entry whose project declared one,
and folding that entry with `_envmodel`'s default instead would quietly join a
comma-separated variable with a space.
"""


def _excerpt(raw: str) -> str:
    """Return `raw` trimmed to a length an error message can carry."""
    text = raw.strip()
    return f"{text[:_EXCERPT]}..." if len(text) > _EXCERPT else text


def _is_object(value: Any) -> bool:
    """Whether a decoded payload is a JSON object."""
    return isinstance(value, dict)


def _is_array(value: Any) -> bool:
    """Whether a decoded payload is a JSON array."""
    return isinstance(value, list)


def _decode(raw: str, what: str) -> Any:
    """Decode `raw` as JSON.

    Args:
        raw: Captured stdout, exactly as ocx wrote it.
        what: The command that produced it, for the error message.

    Returns:
        The decoded payload.

    Raises:
        ValueError: `raw` is empty or is not JSON.
    """
    if not raw.strip():
        raise ValueError(
            f"`ocx {what}` wrote nothing to parse. Only `lock --check` and `update --check` exit 0 "
            "with an empty body, and the SDK answers None for those without parsing — check that "
            "stdout was captured (not stderr) and that the call passed `--format json`."
        )
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"`ocx {what}` did not write JSON: {_excerpt(raw)!r}. Typed calls pin `--format json`; "
            "a plain-text body means the flag was dropped or ocx failed before it wrote a payload."
        ) from exc


def _object(raw: str, what: str) -> Mapping[str, Any]:
    """Decode `raw` as a JSON object.

    Raises:
        ValueError: `raw` is empty, is not JSON, or is not an object.
    """
    data = _decode(raw, what)
    if not _is_object(data):
        raise ValueError(f"`ocx {what}` wrote {_excerpt(raw)!r}, expected a JSON object.")
    return _table(data)


def _array(raw: str, what: str) -> tuple[Mapping[str, Any], ...]:
    """Decode `raw` as a JSON array of objects.

    Raises:
        ValueError: `raw` is empty, is not JSON, or is not an array.
    """
    data = _decode(raw, what)
    if not _is_array(data):
        raise ValueError(f"`ocx {what}` wrote {_excerpt(raw)!r}, expected a JSON array.")
    return _rows(data)


def _need(data: Mapping[str, Any], key: str, what: str) -> Any:
    """Return the required field `key`, or raise naming the command that lacked it.

    Optional fields skip this and use `data.get(key)` — absent means absent,
    and the struct carries `None`.

    Raises:
        ValueError: `data` has no `key`.
    """
    if key not in data:
        raise ValueError(
            f"`ocx {what}` JSON has no {key!r} field; it carried {sorted(data)}. This SDK is tested "
            f"against ocx {TESTED_OCX_VERSION} — an older or newer binary may spell the field "
            "differently."
        )
    return data[key]


def _not_a_string(value: Any, expected: str) -> None:
    """Refuse a bare string where the wire carries a container.

    Every field these three helpers read is an object or an array on the wire.
    A string in its place is silent corruption in `_texts`, which would shred
    it into one-character entries, and a bare `dict() argument` traceback in
    the other two. All three refuse it the same way and name the shape they
    wanted, so a payload that changed shape reads as that rather than as a
    result full of plausible nonsense.

    Raises:
        ValueError: `value` is a `str`.
    """
    if isinstance(value, str):
        raise ValueError(
            f"expected {expected}, got the string {_excerpt(value)!r}. This SDK is tested against "
            f"ocx {TESTED_OCX_VERSION} — an older or newer binary may spell the field differently."
        )


def _table(value: Any) -> Mapping[str, Any]:
    """Return a read-only copy of a JSON object, or an empty one if it is absent.

    Frozen results should not hand out a mapping a caller can write through.

    Raises:
        ValueError: `value` is a string rather than an object.
    """
    _not_a_string(value, "a JSON object")
    return MappingProxyType(dict(value)) if value else _EMPTY


def _opt_table(value: Any) -> Mapping[str, Any] | None:
    """Return a read-only copy of a JSON object, or `None` if it is absent.

    Raises:
        ValueError: `value` is a string rather than an object.
    """
    _not_a_string(value, "a JSON object")
    return MappingProxyType(dict(value)) if value is not None else None


def _rows(value: Any) -> tuple[Mapping[str, Any], ...]:
    """Return a JSON array of objects as read-only rows, or empty if it is absent.

    Raises:
        ValueError: `value` is a string rather than an array.
    """
    _not_a_string(value, "a JSON array of objects")
    return tuple(MappingProxyType(dict(row)) for row in value or ())


def _texts(value: Any) -> tuple[str, ...]:
    """Return a JSON array of strings as a tuple, or empty if it is absent.

    Raises:
        ValueError: `value` is a string rather than an array. `tuple(str)`
            would happily shred it into one-character entries.
    """
    _not_a_string(value, "a JSON array of strings")
    return tuple(str(item) for item in value or ())


@dataclass(frozen=True, slots=True)
class CommandResult:
    """The outcome of one ocx process, before any typing.

    What `invoke()` returns and what every typed method is built on.

    Attributes:
        argv: The argv that ran, with secrets already redacted by `_process`.
        exit_code: The exit status. `int`, not `ExitCode`: a child killed by a
            signal reports a code ocx never assigns.
        stdout: Raw stdout — JSON text for a `--format json` call.
        stderr: Captured stderr in full, redacted.
    """

    argv: tuple[str, ...]
    exit_code: int
    stdout: str
    stderr: str


@dataclass(frozen=True, slots=True)
class VersionInfo:
    """`ocx version` under `--format json`.

    Only `version` is guaranteed; everything else depends on how the binary was
    built. The three sub-objects stay mappings on purpose — they are build
    provenance, nothing in the SDK dispatches on their fields, and modelling
    them would pin a schema ocx explicitly keeps open for extension.

    Note that `Ocx.version()` reads the *plain* output instead: a bare semver
    line is ocx's documented stable contract (design §11).
    """

    version: str
    channel: str | None = None
    cargo_pkg_version: str | None = None
    commit: Mapping[str, Any] = _EMPTY
    build: Mapping[str, Any] = _EMPTY
    ci: Mapping[str, Any] = _EMPTY

    @classmethod
    def from_json(cls, raw: str) -> VersionInfo:
        """Parse `ocx --format json version` output."""
        data = _object(raw, "version")
        return cls(
            version=_need(data, "version", "version"),
            channel=data.get("channel"),
            cargo_pkg_version=data.get("cargo_pkg_version"),
            commit=_table(data.get("commit")),
            build=_table(data.get("build")),
            ci=_table(data.get("ci")),
        )


@dataclass(frozen=True, slots=True)
class AboutInfo:
    """`ocx about` — the binary plus the host it is running on.

    `channel` appears only when it was baked into the build.
    """

    version: str
    registry: str
    home: str
    shell: str
    platforms: tuple[str, ...] = ()
    libc: tuple[str, ...] = ()
    channel: str | None = None
    commit: Mapping[str, Any] = _EMPTY
    build: Mapping[str, Any] = _EMPTY
    ci: Mapping[str, Any] = _EMPTY

    @classmethod
    def from_json(cls, raw: str) -> AboutInfo:
        """Parse `ocx --format json about` output."""
        data = _object(raw, "about")
        return cls(
            version=_need(data, "version", "about"),
            registry=_need(data, "registry", "about"),
            home=_need(data, "home", "about"),
            shell=_need(data, "shell", "about"),
            platforms=_texts(data.get("platforms")),
            libc=_texts(data.get("libc")),
            channel=data.get("channel"),
            commit=_table(data.get("commit")),
            build=_table(data.get("build")),
            ci=_table(data.get("ci")),
        )


@dataclass(frozen=True, slots=True)
class LockStatus:
    """The `lock` block of `ocx status`.

    A lock file that is broken or unreadable is *payload*, not failure: ocx
    still exits 0 and reports `error` here. Callers check `current`, not the
    exit code.

    Attributes:
        present: A lock file exists.
        current: It matches the declaration it was generated from.
        lock_version: Lock file format version.
        declaration_hash: Hash recorded in the lock file.
        declaration_hash_expected: Hash of the declaration as it reads now.
            Differs from `declaration_hash` exactly when `current` is false.
        generated_by: The ocx version that wrote the lock file.
        generated_at: When it did, as ocx spelled it.
        error: Why the lock file could not be read, when it could not be.
    """

    present: bool = False
    current: bool = False
    lock_version: int | None = None
    declaration_hash: str | None = None
    declaration_hash_expected: str | None = None
    generated_by: str | None = None
    generated_at: str | None = None
    error: str | None = None

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> LockStatus:
        """Build from the decoded `lock` object."""
        return cls(
            present=bool(data.get("present")),
            current=bool(data.get("current")),
            lock_version=data.get("lock_version"),
            declaration_hash=data.get("declaration_hash"),
            declaration_hash_expected=data.get("declaration_hash_expected"),
            generated_by=data.get("generated_by"),
            generated_at=data.get("generated_at"),
            error=data.get("error"),
        )


@dataclass(frozen=True, slots=True)
class ToolBinding:
    """One tool in a group of `ocx status`, and how far it is bound.

    Binding state is **key presence**, not a status field: `declared` without
    `platforms` is declared-but-unlocked, `platforms` without `declared` is a
    stale lock entry nothing declares any more, and both means fully bound.

    Attributes:
        declared: The identifier `ocx.toml` declares, if it declares one.
        platforms: Platform to digest, from the lock file, if it has an entry.
    """

    declared: str | None = None
    platforms: Mapping[str, str] | None = None

    @property
    def ref(self) -> PackageRef | None:
        """The declared identifier as a `PackageRef`, or `None` if nothing declares it."""
        return PackageRef(self.declared) if self.declared is not None else None

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ToolBinding:
        """Build from one decoded entry of a group's `tools` object."""
        return cls(declared=data.get("declared"), platforms=_opt_table(data.get("platforms")))


@dataclass(frozen=True, slots=True)
class GroupStatus:
    """One group of `ocx status`.

    Attributes:
        tools: Binding name to its binding state.
        env: The group's `[env]` table, keyed by variable name, carried
            through untyped. It is keyed here (`{KEY: {type, value}}`) rather
            than the array form `ocx env` emits, and no probe has yet seen it
            populated — so it stays a pass-through instead of a struct built
            on an unobserved shape.
    """

    tools: Mapping[str, ToolBinding] = _EMPTY
    env: Mapping[str, Mapping[str, Any]] = _EMPTY

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> GroupStatus:
        """Build from one decoded entry of the `groups` object."""
        tools = {name: ToolBinding.from_dict(entry) for name, entry in _table(data.get("tools")).items()}
        env = {key: _table(entry) for key, entry in _table(data.get("env")).items()}
        return cls(tools=MappingProxyType(tools), env=MappingProxyType(env))


@dataclass(frozen=True, slots=True)
class StatusReport:
    """`ocx status` — the project's declaration, lock, and groups.

    Exits 0 even when the lock is broken, so this struct is where a caller
    learns something is wrong (`lock.current`, `lock.error`).

    Attributes:
        project: Path to the `ocx.toml` the report describes.
        lock: Lock file state.
        groups: Group name to its contents.
        package_settings: Per-package settings, carried through untyped —
            they are ocx's configuration surface, not the SDK's.
    """

    project: str
    lock: LockStatus = field(default_factory=LockStatus)
    groups: Mapping[str, GroupStatus] = _EMPTY
    package_settings: Mapping[str, Mapping[str, Any]] = _EMPTY

    @classmethod
    def from_json(cls, raw: str) -> StatusReport:
        """Parse `ocx --format json status` output."""
        data = _object(raw, "status")
        groups = {name: GroupStatus.from_dict(entry) for name, entry in _table(data.get("groups")).items()}
        settings = {name: _table(entry) for name, entry in _table(data.get("package_settings")).items()}
        return cls(
            project=_need(data, "project", "status"),
            lock=LockStatus.from_dict(_table(data.get("lock"))),
            groups=MappingProxyType(groups),
            package_settings=MappingProxyType(settings),
        )


@dataclass(frozen=True, slots=True)
class Candidate:
    """One platform's manifest for a package, as `inspect` lists it.

    `media_type` and `size` are present on the `package inspect` tier only;
    the toolchain tier omits them.
    """

    digest: str
    pinned: str
    platform: str
    media_type: str | None = None
    size: int | None = None

    @property
    def ref(self) -> PackageRef:
        """The pinned identifier, ready for the next call's argv."""
        return PackageRef(self.pinned)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Candidate:
        """Build from one decoded entry of a package's `candidates` array."""
        return cls(
            digest=_need(data, "digest", "inspect"),
            pinned=_need(data, "pinned", "inspect"),
            platform=_need(data, "platform", "inspect"),
            media_type=data.get("media_type"),
            size=data.get("size"),
        )


@dataclass(frozen=True, slots=True)
class InspectedPackage:
    """One package row of `inspect` or `package inspect`.

    `name` reads differently per tier: a binding name at the toolchain tier,
    the full requested identifier at the package tier.

    Everything from `pinned_identifier` down appears only under `--resolve` or
    `--closure`. `resolution` and `closure` stay mappings: both are deep trees
    whose interesting parts (`closure.surface`, `closure.conflicts`) are still
    moving upstream, and modelling a tree we would have to re-model on the next
    ocx release buys nothing v0.1 needs.

    Attributes:
        name: Binding name, or the requested identifier at the package tier.
        identifier: The identifier as requested.
        candidates: One entry per platform ocx found.
        pinned_identifier: `--resolve`: the identifier with its digest.
        pinned_digest: `--resolve`: the digest alone.
        platform: `--resolve`: the resolved platform object.
        metadata: `--resolve`: the package's `metadata.json`.
        layers: `--resolve`: the layer descriptors.
        resolution: `--resolve`: the pin and the walk that produced it.
        closure: `--closure`: dependencies, surface, and conflicts.
    """

    name: str
    identifier: str
    candidates: tuple[Candidate, ...] = ()
    pinned_identifier: str | None = None
    pinned_digest: str | None = None
    platform: Mapping[str, Any] | None = None
    metadata: Mapping[str, Any] | None = None
    layers: tuple[Mapping[str, Any], ...] = ()
    resolution: Mapping[str, Any] | None = None
    closure: Mapping[str, Any] | None = None

    @property
    def ref(self) -> PackageRef:
        """The pinned identifier when resolved, else the requested one."""
        return PackageRef(self.pinned_identifier or self.identifier, self.metadata or _EMPTY)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> InspectedPackage:
        """Build from one decoded entry of the `packages` array."""
        return cls(
            name=_need(data, "name", "inspect"),
            identifier=_need(data, "identifier", "inspect"),
            candidates=tuple(Candidate.from_dict(row) for row in _rows(data.get("candidates"))),
            pinned_identifier=data.get("pinned_identifier"),
            pinned_digest=data.get("pinned_digest"),
            platform=_opt_table(data.get("platform")),
            metadata=_opt_table(data.get("metadata")),
            layers=_rows(data.get("layers")),
            resolution=_opt_table(data.get("resolution")),
            closure=_opt_table(data.get("closure")),
        )


@dataclass(frozen=True, slots=True)
class EnvEntry:
    """One `[env]` contribution from `ocx env`.

    Attributes:
        key: The variable name.
        type: How the value folds onto the parent environment.
        value: The contribution, verbatim. ocx has already resolved its own
            `${...}` grammar by the time it reaches us.
        separator: The separator a `list` entry folds with, when its project
            declared one. `None` — always, for the other two types — takes
            `_envmodel`'s default, which is ocx's own.
    """

    key: str
    type: EnvEntryType
    value: str
    separator: str | None = None

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> EnvEntry:
        """Build from one decoded entry of an `entries` array.

        Raises:
            ValueError: The entry declares a type this SDK cannot fold.
        """
        key = _need(data, "key", "env")
        declared = _need(data, "type", "env")
        if declared not in _ENV_VALUES:
            raise ValueError(
                f"`ocx env` declared type {declared!r} for {key!r}; this SDK folds only "
                f"{', '.join(sorted(_ENV_VALUES))}. Refusing rather than guessing — a variable "
                "merged the wrong way is worse than a call that fails. Upgrade ocx-sdk if ocx "
                "grew a new type."
            )
        return cls(
            key=key,
            type=declared,
            value=_need(data, "value", "env"),
            separator=data.get("separator"),
        )


@dataclass(frozen=True, slots=True)
class PackageBinding:
    """A name a package provides — the row shape `binaries` and `entrypoints` share."""

    name: str
    package: str

    @property
    def ref(self) -> PackageRef:
        """The providing package, as a `PackageRef`."""
        return PackageRef(self.package)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> PackageBinding:
        """Build from one decoded `binaries` or `entrypoints` entry."""
        return cls(name=_need(data, "name", "env"), package=_need(data, "package", "env"))


@dataclass(frozen=True, slots=True)
class Integration:
    """A tool integration a package declares, with its payload carried untyped."""

    namespace: str
    package: str
    payload: Mapping[str, Any] = _EMPTY

    @property
    def ref(self) -> PackageRef:
        """The declaring package, as a `PackageRef`."""
        return PackageRef(self.package)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Integration:
        """Build from one decoded `integrations` entry."""
        return cls(
            namespace=_need(data, "namespace", "env"),
            package=_need(data, "package", "env"),
            payload=_table(data.get("payload")),
        )


@dataclass(frozen=True, slots=True)
class Advisory:
    """Something ocx wants the caller to know about a composed environment."""

    kind: str
    package: str
    key: str
    message: str

    @property
    def ref(self) -> PackageRef:
        """The package the advisory is about, as a `PackageRef`."""
        return PackageRef(self.package)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Advisory:
        """Build from one decoded `advisories` entry."""
        return cls(
            kind=_need(data, "kind", "env"),
            package=_need(data, "package", "env"),
            key=_need(data, "key", "env"),
            message=_need(data, "message", "env"),
        )


@dataclass(frozen=True, slots=True)
class EnvReport:
    """`ocx env` and `ocx package env` — the same five-array envelope, both tiers.

    Iterating the report iterates `entries`: composing an environment is what
    it is for, and the other four arrays are description.

    Attributes:
        entries: The `[env]` contributions, in declaration order.
        binaries: Executables the packages put on PATH. Pre-1.0, may break.
        entrypoints: Named entrypoints the packages declare. Pre-1.0, may break.
        integrations: Tool integrations, payloads untyped. Pre-1.0, may break.
        advisories: Warnings about the composition. Pre-1.0, may break.

    The four non-entry arrays carry ocx's `package which`-style caveat: they
    are not on ocx's durable-anchor list, so their shape may change in a minor
    release. `entries` is the part the SDK depends on.

    Example:
        ```pycon
        >>> report = EnvReport.from_json('{"entries": [{"key": "TOOL", "type": "constant", "value": "1"}]}')
        >>> [entry.key for entry in report]
        ['TOOL']

        ```
    """

    entries: tuple[EnvEntry, ...] = ()
    binaries: tuple[PackageBinding, ...] = ()
    entrypoints: tuple[PackageBinding, ...] = ()
    integrations: tuple[Integration, ...] = ()
    advisories: tuple[Advisory, ...] = ()
    _base: Mapping[str, str] = field(default=_EMPTY, repr=False)
    """The environment the producing call ran under — `compose`'s default base.

    Carried as a value, not a reference to `os.environ`: a report produced
    under `HostEnv.clean()` has to compose hermetically, or the hermetic path
    silently regains whatever the ambient environment held.
    """

    def __iter__(self) -> Iterator[EnvEntry]:
        return iter(self.entries)

    def compose(self, base: Mapping[str, str] | None = None) -> ComposedEnv:
        """Fold these entries onto an environment.

        Args:
            base: What to fold onto. Defaults to the environment the producing
                call ran under, so composing a report from a hermetic handle
                stays hermetic.

        Returns:
            The merged environment. `.mapping` is the non-invasive form;
            `.activate()` applies it to the whole process.

        Raises:
            ValueError: Two list contributions to one key disagree about the
                separator, or a value is edged by the one it folds with
                (`_envmodel.merge`). ocx validates its own `[env]` before
                emitting it, so a report parsed from `ocx env` should not
                reach that; a hand-built one can.
        """
        # Function-local: the recorded `_results -> _envmodel` edge, kept out of
        # import time so neither module has to be the other's prerequisite.
        from ._envmodel import merge

        entries = ((entry.key, _ENV_VALUES[entry.type](entry.value, entry.separator)) for entry in self.entries)
        return merge(self._base if base is None else base, entries)

    @classmethod
    def from_json(cls, raw: str, *, base: Mapping[str, str] = _EMPTY) -> EnvReport:
        """Parse `ocx --format json env` (or `package env`) output.

        Args:
            raw: Captured stdout.
            base: The environment the call ran under, carried for `compose`.
                Snapshotted, so a later mutation of the source cannot rewrite
                what an already-parsed report composes onto.
        """
        data = _object(raw, "env")
        return cls(
            entries=tuple(EnvEntry.from_dict(row) for row in _rows(data.get("entries"))),
            binaries=tuple(PackageBinding.from_dict(row) for row in _rows(data.get("binaries"))),
            entrypoints=tuple(PackageBinding.from_dict(row) for row in _rows(data.get("entrypoints"))),
            integrations=tuple(Integration.from_dict(row) for row in _rows(data.get("integrations"))),
            advisories=tuple(Advisory.from_dict(row) for row in _rows(data.get("advisories"))),
            _base=MappingProxyType(dict(base)),
        )


@dataclass(frozen=True, slots=True)
class InspectReport:
    """`ocx inspect` and `ocx package inspect` — the same envelope, both tiers.

    Attributes:
        packages: One row per requested package or declared binding.
        env: The `[env]` entries those packages contribute, in order.
            Duplicates are allowed and meaningful — the merge folds them.
        platform: The platform resolution ran against, under `--resolve`.
    """

    packages: tuple[InspectedPackage, ...] = ()
    env: tuple[EnvEntry, ...] = ()
    platform: str | None = None

    @classmethod
    def from_json(cls, raw: str) -> InspectReport:
        """Parse `ocx --format json inspect` (or `package inspect`) output."""
        data = _object(raw, "inspect")
        return cls(
            packages=tuple(InspectedPackage.from_dict(row) for row in _rows(data.get("packages"))),
            env=tuple(EnvEntry.from_dict(row) for row in _rows(data.get("env"))),
            platform=data.get("platform"),
        )


@dataclass(frozen=True, slots=True)
class WhichResult:
    """Where one identifier resolves on disk, per `package which`.

    Attributes:
        path: The resolved location.
        kind: `"package"` for a store directory, `"shim"` for a shim.
    """

    path: str
    kind: str

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> WhichResult:
        """Build from one decoded value of the keyed object."""
        return cls(path=_need(data, "path", "package which"), kind=_need(data, "kind", "package which"))


@dataclass(frozen=True, slots=True)
class PullReport:
    """`ocx pull` at the **project** tier — paths plus advisories.

    The wire object mixes both: identifiers key `{path, kind}` values, and a
    sibling `advisories` key sits among them. An identifier is always at least
    `<repo>/<name>`, so it can never collide with that key.

    Attributes:
        packages: Pinned identifier to where it landed.
        advisories: Warnings, carried untyped — no probe has seen a populated
            one, and `ocx env`'s advisory shape is not known to apply here.
    """

    packages: Mapping[str, WhichResult] = _EMPTY
    advisories: tuple[Mapping[str, Any], ...] = ()

    @classmethod
    def from_json(cls, raw: str) -> PullReport:
        """Parse `ocx --format json pull` output."""
        data = _object(raw, "pull")
        packages = {key: WhichResult.from_dict(_table(value)) for key, value in data.items() if key != _ADVISORIES}
        return cls(packages=MappingProxyType(packages), advisories=_rows(data.get(_ADVISORIES)))


@dataclass(frozen=True, slots=True)
class InstalledPackage:
    """One package `package install` or `package select` put in place.

    Attributes:
        identifier: The pinned identifier, `<as given>@sha256:...`.
        path: The symlink that now points at it — `candidates/<tag>` after an
            install, `current` after a select.
        metadata: The package's `metadata.json`, carried untyped.
    """

    identifier: str
    path: str
    metadata: Mapping[str, Any] = _EMPTY

    @property
    def ref(self) -> PackageRef:
        """The pinned identifier, carrying its metadata."""
        return PackageRef(self.identifier, self.metadata)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> InstalledPackage:
        """Build from one decoded value of the keyed object."""
        return cls(
            identifier=_need(data, "identifier", "package install"),
            path=_need(data, "path", "package install"),
            metadata=_table(data.get("metadata")),
        )


@dataclass(frozen=True, slots=True)
class InstallReport:
    """`ocx package install` and `ocx package select` — the same keyed object.

    Keys are the identifiers **as given**; the values carry the pinned form.
    That asymmetry is the point: a caller can look up what it asked for
    without knowing what it resolved to.

    Attributes:
        packages: Requested identifier to what got installed.
    """

    packages: Mapping[str, InstalledPackage] = _EMPTY

    @classmethod
    def from_json(cls, raw: str) -> InstallReport:
        """Parse `ocx --format json package install` (or `select`) output."""
        data = _object(raw, "package install")
        packages = {key: InstalledPackage.from_dict(_table(value)) for key, value in data.items()}
        return cls(packages=MappingProxyType(packages))


@dataclass(frozen=True, slots=True)
class RemovalResult:
    """One row of `package uninstall` or `package deselect`.

    Both commands return a bare array rather than a keyed object — the only
    two package-tier mutators that do.

    Attributes:
        package: The identifier as given.
        status: What happened, e.g. `"removed"`.
        path: The symlink that went away.
    """

    package: str
    status: str
    path: str

    @property
    def ref(self) -> PackageRef:
        """The removed package, as a `PackageRef`."""
        return PackageRef(self.package)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> RemovalResult:
        """Build from one decoded array entry."""
        return cls(
            package=_need(data, "package", "package uninstall"),
            status=_need(data, "status", "package uninstall"),
            path=_need(data, "path", "package uninstall"),
        )


@dataclass(frozen=True, slots=True)
class DepNode:
    """One node of the `package deps` tree.

    Attributes:
        identifier: The pinned identifier.
        repeated: This node already appeared elsewhere in the tree; its
            children are not repeated under it.
        visibility: How the parent exposes it, when the parent says.
        dependencies: Child nodes, carried untyped. The only probe available
            had no dependencies, so the populated child shape is UNCONFIRMED —
            re-model this as `tuple[DepNode, ...]` once a real dependency edge
            is recorded.
    """

    identifier: str
    repeated: bool = False
    visibility: str | None = None
    dependencies: tuple[Mapping[str, Any], ...] = ()

    @property
    def ref(self) -> PackageRef:
        """This node's package, as a `PackageRef`."""
        return PackageRef(self.identifier)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> DepNode:
        """Build from one decoded node of the `roots` array."""
        return cls(
            identifier=_need(data, "identifier", "package deps"),
            repeated=bool(data.get("repeated")),
            visibility=data.get("visibility"),
            dependencies=_rows(data.get("dependencies")),
        )


@dataclass(frozen=True, slots=True)
class DepsReport:
    """`ocx package deps` — a `{"roots": [...]}` wrapper, not a bare array."""

    roots: tuple[DepNode, ...] = ()

    @classmethod
    def from_json(cls, raw: str) -> DepsReport:
        """Parse `ocx --format json package deps` output."""
        data = _object(raw, "package deps")
        roots = _rows(_need(data, "roots", "package deps"))
        return cls(roots=tuple(DepNode.from_dict(row) for row in roots))


@dataclass(frozen=True, slots=True)
class ToolRow:
    """One tool entry `add`, `lock`, `update`, or `remove` reports.

    All four emit the same bare array — effectively "the lock entries that
    changed", not a per-command envelope. `remove` reports an empty one.

    Attributes:
        binding: The binding name in `ocx.toml`.
        group: The group it belongs to.
        digest: The digest for the current platform.
        platforms: Every platform's digest.
    """

    binding: str
    group: str
    digest: str
    platforms: Mapping[str, str] = _EMPTY

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ToolRow:
        """Build from one decoded array entry."""
        return cls(
            binding=_need(data, "binding", "lock"),
            group=_need(data, "group", "lock"),
            digest=_need(data, "digest", "lock"),
            platforms=_table(data.get("platforms")),
        )


@dataclass(frozen=True, slots=True)
class Assertion:
    """Why `package test` decided a run failed.

    Attributes:
        kind: The assertion that fired, e.g. `"exit_code"`.
        message: Its human-readable form.
    """

    kind: str
    message: str

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Assertion:
        """Build from the decoded `assertion` object."""
        return cls(kind=_need(data, "kind", "package test"), message=_need(data, "message", "package test"))


@dataclass(frozen=True, slots=True)
class TestRun:
    """What the tested command actually did.

    Attributes:
        exit_code: The command's exit status.
        stdout: Its stdout, truncated if `truncated`.
        stderr: Its stderr, same.
        duration_ms: Wall time in milliseconds.
        truncated: Output was cut to fit the envelope.
    """

    __test__ = False
    """Not a pytest test class, despite the name ocx's command gives it."""

    exit_code: int = 0
    stdout: str = ""
    stderr: str = ""
    duration_ms: int = 0
    truncated: bool = False

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> TestRun:
        """Build from the decoded `run` object."""
        return cls(
            exit_code=int(data.get("exit_code", 0)),
            stdout=str(data.get("stdout", "")),
            stderr=str(data.get("stderr", "")),
            duration_ms=int(data.get("duration_ms", 0)),
            truncated=bool(data.get("truncated")),
        )


@dataclass(frozen=True, slots=True)
class TestResult:
    """`ocx package test --script` — the stable v1 envelope.

    **Only the `--script` form produces this.** The trailing `-- CMD` form
    prints the child's raw stdout verbatim, even under `--format json`, so
    parsing that output here would either fail loudly or — worse — succeed on
    a child that happened to print JSON. The client layer never routes `-- CMD`
    output into this parser.

    `status` decides pass or fail; `assertion.kind` says why a failure failed.

    Attributes:
        status: `"passed"` or the failure ocx named.
        run: What the tested command did.
        assertion: The assertion that fired, or `None` when it passed.
    """

    __test__ = False
    """Not a pytest test class, despite the name ocx's command gives it."""

    status: str
    run: TestRun = field(default_factory=TestRun)
    assertion: Assertion | None = None

    @property
    def passed(self) -> bool:
        """Whether the test passed — `status` decides, never the exit code."""
        return self.status == _PASSED

    @classmethod
    def from_json(cls, raw: str) -> TestResult:
        """Parse `ocx --format json package test --script ...` output."""
        data = _object(raw, "package test")
        assertion = data.get("assertion")
        return cls(
            status=_need(data, "status", "package test"),
            run=TestRun.from_dict(_table(data.get("run"))),
            assertion=Assertion.from_dict(_table(assertion)) if assertion else None,
        )


@dataclass(frozen=True, slots=True)
class PushResult:
    """`ocx package push` — what landed in the registry.

    Attributes:
        identifier: What was pushed.
        status: What happened, e.g. `"pushed"`.
        manifest_digest: Digest of the manifest that was written.
        cascade_tags_written: Cascading tags updated by the push.
        canonical_tags_written: Canonical `sha256.<hex>` tags written.
        layers: Layer counts (`mounted`, `uploaded`, `verified`).
    """

    identifier: str
    status: str
    manifest_digest: str
    cascade_tags_written: tuple[str, ...] = ()
    canonical_tags_written: tuple[str, ...] = ()
    layers: Mapping[str, Any] = _EMPTY

    @property
    def ref(self) -> PackageRef:
        """The pushed package, as a `PackageRef`."""
        return PackageRef(self.identifier)

    @classmethod
    def from_json(cls, raw: str) -> PushResult:
        """Parse `ocx --format json package push` output."""
        data = _object(raw, "package push")
        return cls(
            identifier=_need(data, "identifier", "package push"),
            status=_need(data, "status", "package push"),
            manifest_digest=_need(data, "manifest_digest", "package push"),
            cascade_tags_written=_texts(data.get("cascade_tags_written")),
            canonical_tags_written=_texts(data.get("canonical_tags_written")),
            layers=_table(data.get("layers")),
        )


@dataclass(frozen=True, slots=True)
class LoginResult:
    """`ocx login` — which registry, as whom."""

    registry: str
    username: str

    @classmethod
    def from_json(cls, raw: str) -> LoginResult:
        """Parse `ocx --format json login` output."""
        data = _object(raw, "login")
        return cls(registry=_need(data, "registry", "login"), username=_need(data, "username", "login"))


@dataclass(frozen=True, slots=True)
class LogoutResult:
    """`ocx logout` — which registry. Always succeeds, even if nothing was stored."""

    registry: str

    @classmethod
    def from_json(cls, raw: str) -> LogoutResult:
        """Parse `ocx --format json logout` output."""
        return cls(registry=_need(_object(raw, "logout"), "registry", "logout"))


@dataclass(frozen=True, slots=True)
class ConfigUpdateReport:
    """`ocx config update` — the managed-config tier's state.

    `status` is the only field ocx always emits; the rest describe a tier that
    is actually configured, and a `not_configured` answer carries none of them.

    Attributes:
        status: `not_configured`, `already_current`, `updated`, `checked`, or
            `check_unavailable`.
        source: The OCI reference the snapshot follows.
        digest: The snapshot's digest.
        policy: `apply`, `notify`, or `manual`.
        tag: The tag the source resolved to.
        fetched_at: When the snapshot was fetched.
        paused_until: When an active pause expires.
        pinned: The version a pin holds it at.
        drift: How the on-disk state diverged, carried untyped.
        kill_switches: Kill switches the snapshot activates.
    """

    status: str
    source: str | None = None
    digest: str | None = None
    policy: str | None = None
    tag: str | None = None
    fetched_at: str | None = None
    paused_until: str | None = None
    pinned: str | None = None
    drift: Any = None
    kill_switches: tuple[str, ...] = ()

    @classmethod
    def from_json(cls, raw: str) -> ConfigUpdateReport:
        """Parse `ocx --format json config update` output."""
        data = _object(raw, "config update")
        return cls(
            status=_need(data, "status", "config update"),
            source=data.get("source"),
            digest=data.get("digest"),
            policy=data.get("policy"),
            tag=data.get("tag"),
            fetched_at=data.get("fetched_at"),
            paused_until=data.get("paused_until"),
            pinned=data.get("pinned"),
            drift=data.get("drift"),
            kill_switches=_texts(data.get("kill_switches")),
        )


@dataclass(frozen=True, slots=True)
class ConfigSetupReport:
    """`ocx config setup` — the `managed_config` block of `self setup`'s report.

    ocx wraps the block in an envelope; the SDK flattens it, because the
    envelope has exactly one key and a `ConfigSetupReport.managed_config.status`
    chain says nothing `.status` does not.

    Attributes:
        status: What the run did or would do, e.g. `"would_adopt"`.
    """

    status: str

    @classmethod
    def from_json(cls, raw: str) -> ConfigSetupReport:
        """Parse `ocx --format json config setup` output."""
        data = _object(raw, "config setup")
        managed = _table(_need(data, "managed_config", "config setup"))
        return cls(status=_need(managed, "status", "config setup"))


def parse_which(raw: str) -> Mapping[str, WhichResult]:
    """Parse `ocx --format json package which` output.

    Returns:
        Requested identifier to where it resolves. Never downloads, so an
        identifier that is not installed is simply absent.
    """
    data = _object(raw, "package which")
    return MappingProxyType({key: WhichResult.from_dict(_table(value)) for key, value in data.items()})


def parse_info(raw: str) -> InfoResult:
    """Parse `ocx --format json package info` output.

    Returns:
        Requested identifier to its description metadata, or `None` when the
        registry holds none. Keyed even for a single identifier.
    """
    data = _object(raw, "package info")
    return MappingProxyType({key: _opt_table(value) for key, value in data.items()})


def parse_package_pull(raw: str) -> Mapping[str, str]:
    """Parse `ocx --format json package pull` output.

    Returns:
        Requested identifier to the store path, as a **bare string** — the
        package tier's shape, unlike project-tier `pull` and `package which`,
        which both return `{path, kind}` objects.
    """
    data = _object(raw, "package pull")
    return MappingProxyType({key: str(value) for key, value in data.items()})


def parse_removals(raw: str) -> tuple[RemovalResult, ...]:
    """Parse `ocx --format json package uninstall` (or `deselect`) output."""
    return tuple(RemovalResult.from_dict(row) for row in _array(raw, "package uninstall"))


def parse_tool_rows(raw: str) -> tuple[ToolRow, ...]:
    """Parse `ocx --format json add` (or `lock`, `update`, `remove`) output.

    Note:
        `lock --check` and `update --check` emit **no body at all** on success.
        They never reach this function — the client layer answers `None` for
        them instead of asking a parser to interpret emptiness.
    """
    return tuple(ToolRow.from_dict(row) for row in _array(raw, "lock"))
