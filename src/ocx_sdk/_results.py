# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The OCX Authors

"""Typed results parsed from ocx's JSON output (contract v0.1 C-003).

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

# _EXIT_CODE_ERRORS is package-internal; `_process` and `tolerated_report`
# below are its two consumers, both raising for an exit the caller did not
# tolerate.
from ._errors import (
    _EXIT_CODE_ERRORS,  # pyright: ignore[reportPrivateUsage]
    ExitCode,
    OcxProcessError,
)
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

_ATTESTATION_SUCCEEDED: Final = "succeeded"
"""The `AttestationOutcome` variant that carries `predicate_type` and `signed`."""

_ATTESTATION_FAILED: Final = "failed"
"""The `AttestationOutcome` variant that carries `kind` and `message`."""

_ERROR: Final = "error"
"""The C-S1-1 envelope's failure payload. Never a report: `partial_report` answers `None` for it."""

_ENVELOPE: Final = "error envelope"
"""What `ErrorEnvelope` names when a required field is missing — no command produces it, every failing one may."""

_SIGN: Final = "package sign"
_VERIFY: Final = "package verify"
_ATTEST: Final = "package attest"
_SBOM: Final = "package sbom"
_PUSH: Final = "package push"
_COPY: Final = "package copy"
_DESCRIPTION_PULL: Final = "package description pull"
_SWEEP: Final = "package sign/attest --tags"
_ANNOUNCE: Final = "package announce"
_CLAIM: Final = "package claim"
_CASCADE: Final = "package cascade check/repair"
_CASCADE_CHECK: Final = "package cascade check"
_CASCADE_REPAIR: Final = "package cascade repair"
_CLEAN: Final = "clean"
_RECEIPT: Final = "package receipt"
"""The commands each parser names when a required field is missing.

`_SWEEP` names both, because one `SweepReport` serves `sign --tags` and
`attest --tags` alike (C-017) and the row itself carries nothing that says
which — the caller's row parser is the only thing that knows. `_CASCADE`
names both cascade commands for the same reason: one `CascadeReport` is the
`check` document and the `report` inside every `repair` entry.
"""

type EnvEntryType = Literal["constant", "path", "list"]
"""The `type` an `ocx env` entry declares. Mirrors ocx's `--env KEY:TYPE=VALUE`."""

type InfoResult = Mapping[str, PackageDescription | None]
"""`package description pull`, keyed by identifier as given.

The value is `None` whenever the registry holds no description metadata at
all — distinct from a `PackageDescription` whose every field is `None`, which
is a description the registry holds and has left blank.
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


def _shaped(value: Any, container: type, expected: str) -> None:
    """Refuse a value that is not the container the wire carries there.

    Every field these four helpers read is an object or an array on the wire.
    A bare string is the dangerous case — `_texts` would shred it into
    one-character entries — but any other scalar is a `dict() argument` or
    `not iterable` **`TypeError`** escaping a parser whose contract is
    `ValueError`, which `Integration.payload` reached through
    `serde_json::Value` (arbitrary JSON, so a number is a legal document).
    All four refuse the same way and name the shape they wanted, so a payload
    that changed shape reads as that rather than as a result full of
    plausible nonsense.

    `None` is absence, not a wrong shape — every caller answers it with an
    empty result. `str` is excluded by construction: it is neither a `Mapping`
    nor a `list`.

    Raises:
        ValueError: `value` is present and is not a `container`.
    """
    if value is not None and not isinstance(value, container):
        raise ValueError(
            f"expected {expected}, got the {type(value).__name__} {_excerpt(str(value))!r}. This SDK "
            f"is tested against ocx {TESTED_OCX_VERSION} — an older or newer binary may spell the "
            "field differently."
        )


def _table(value: Any) -> Mapping[str, Any]:
    """Return a read-only copy of a JSON object, or an empty one if it is absent.

    Frozen results should not hand out a mapping a caller can write through.

    Raises:
        ValueError: `value` is present and is not an object.
    """
    _shaped(value, Mapping, "a JSON object")
    return MappingProxyType(dict(value)) if value else _EMPTY


def _opt_table(value: Any) -> Mapping[str, Any] | None:
    """Return a read-only copy of a JSON object, or `None` if it is absent.

    Raises:
        ValueError: `value` is present and is not an object.
    """
    _shaped(value, Mapping, "a JSON object")
    return MappingProxyType(dict(value)) if value is not None else None


def _rows(value: Any) -> tuple[Mapping[str, Any], ...]:
    """Return a JSON array of objects as read-only rows, or empty if it is absent.

    Raises:
        ValueError: `value` is present and is not an array.
    """
    _shaped(value, list, "a JSON array of objects")
    return tuple(MappingProxyType(dict(row)) for row in value or ())


def _texts(value: Any) -> tuple[str, ...]:
    """Return a JSON array of strings as a tuple, or empty if it is absent.

    Raises:
        ValueError: `value` is present and is not an array. `tuple(str)`
            would happily shred it into one-character entries.
    """
    _shaped(value, list, "a JSON array of strings")
    return tuple(str(item) for item in value or ())


def _envelope(raw: str, what: str) -> Mapping[str, Any]:
    """Return the `data` payload of an enveloped command's JSON output (D11).

    `sign`, `attest`, `verify`, `sbom`, and any swept `sign`/`attest` wrap
    their report in `{"schema_version": 1, "command": ..., "exit_code": ...,
    "data": {...}}` rather than writing the report at the JSON root the way
    `copy` and `push` do. Handing that envelope straight to `_need` would
    read `schema_version`/`command`/`exit_code` as if they were report
    fields — this unwraps it first, one level up from that failure.

    Args:
        raw: Captured stdout.
        what: The command that produced it, for the error message.

    Returns:
        The `data` sub-object.

    Raises:
        ValueError: `raw` is empty, is not JSON, is not an object, or has no
            `data` key.
    """
    return _table(_need(_object(raw, what), "data", what))


def _is_envelope(payload: Any) -> bool:
    """Whether a decoded stdout is the C-S1-1 error envelope rather than a report.

    Tested before any report shape, never after: an error envelope is a JSON
    object too, and reading it as one would hand the failure's own message
    back as if a command had reported it.
    """
    return _is_object(payload) and _ERROR in payload


@dataclass(frozen=True, slots=True)
class ErrorEnvelope:
    """The structured failure ocx prints under `--format json` (C-S1-1).

    `{schema_version, command, exit_code, error: {kind, detail?, message,
    remediation?, context}}` — a contract frozen separately from the reports
    schema, and printed on stdout only when the failing command wrote no
    report of its own. `error_envelope(err)` is how a caller reaches it.

    The exit code is still the category (`_errors`): `kind` restates it as
    ocx's own vocabulary, and `detail` — when present — is the fine-grained
    slug to branch on. `message` is prose, free to be reworded.

    Attributes:
        schema_version: The envelope's own version; `1` for every 0.6 binary.
        command: The canonical command string, e.g. `"package claim"`.
        exit_code: The exit status the process returned.
        kind: The coarse category, snake_case (`"data_error"`, `"auth_error"`, ...).
        message: The outermost message of the error chain.
        detail: The fine-grained variant slug, when ocx assigned one.
        remediation: A remediation hint. Reserved upstream and never emitted
            by a 0.6 binary; carried so a consumer treating it as optional
            keeps working when it appears.
        context: Structured context (identifiers, digests, URLs), untyped.
    """

    schema_version: int
    command: str
    exit_code: int
    kind: str
    message: str
    detail: str | None = None
    remediation: str | None = None
    context: Mapping[str, Any] = _EMPTY

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ErrorEnvelope:
        """Build from a decoded envelope, reading across its two levels."""
        error = _table(_need(data, _ERROR, _ENVELOPE))
        return cls(
            schema_version=_need(data, "schema_version", _ENVELOPE),
            command=_need(data, "command", _ENVELOPE),
            exit_code=_need(data, "exit_code", _ENVELOPE),
            kind=_need(error, "kind", _ENVELOPE),
            message=_need(error, "message", _ENVELOPE),
            detail=error.get("detail"),
            remediation=error.get("remediation"),
            context=_table(_need(error, "context", _ENVELOPE)),
        )


def error_envelope(error: OcxProcessError) -> ErrorEnvelope | None:
    """Return the structured failure a non-zero exit carried, else `None`.

    The dual of `partial_report`: where that recovers the *report* a
    report-then-fail command wrote, this recovers the *envelope* a hard
    failure wrote instead. A failure never carries both, so for any one
    error at most one of the two answers.

    ```python
    try:
        ocx.package.claim("acme/widget", repository="oci://ghcr.io/acme/widget")
    except DataError as exc:
        envelope = error_envelope(exc)
        if envelope is None or "already claimed" not in envelope.message:
            raise
    ```

    Args:
        error: The caught process failure.

    Returns:
        `None` when stdout is empty, is not JSON, or is a report rather than
        an envelope. Otherwise the parsed envelope.

    Raises:
        ValueError: stdout is an envelope missing one of its frozen keys —
            a binary outside the tested window, not a recoverable state.
    """
    try:
        payload = json.loads(error.stdout)
    except json.JSONDecodeError:
        return None
    if not _is_envelope(payload):
        return None
    return ErrorEnvelope.from_dict(payload)


def partial_report(error: OcxProcessError) -> str | None:
    """Return the report a non-zero-exit stdout carried, else `None` (D10).

    Several ocx failures still print a full JSON report before exiting
    non-zero: a `--signature-format both` sign or attest losing one leg, a
    partially-failed tag sweep, a `copy` refused on a sidecar conflict, and a
    push whose inline signing failed. `OcxProcessError` never parses its own
    stdout, so a caller recovers the report explicitly:

    ```python
    try:
        result = ocx.package.sign(ref, key="file://k.pem", rekor_upload=False)
    except OcxProcessError as exc:
        raw = partial_report(exc)
        if raw is not None:
            result = SignatureReport.from_json(raw)
        else:
            raise
    ```

    Args:
        error: The caught process failure.

    Returns:
        `None` for a hard failure (an error envelope carrying an `error` key
        and no `data`, or stdout that is empty or unparseable as JSON) —
        there is nothing to recover. Otherwise `error.stdout` verbatim, ready
        for the matching `from_json`.

    Note:
        **The enveloped shapes come back enveloped**, not pre-unwrapped. D10's
        text called for handing back the envelope's `data` sub-document, which
        cannot work: `SignatureReport`/`AttestationReport`/`SweepReport`'s own
        `from_json` calls `_envelope` (D11), so a pre-unwrapped payload makes
        it unwrap twice and raise on a missing `data` key — the example above
        would not run. Returning stdout whole is what makes one `from_json`
        serve the success path and the recovery path, which is the property
        D10 and S-009 both ask for; the discriminator is unchanged.
    """
    try:
        payload = json.loads(error.stdout)
    except json.JSONDecodeError:
        return None
    if not _is_object(payload) or _is_envelope(payload):
        return None
    return error.stdout


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

    def __repr__(self) -> str:
        """Report the sizes, never the bytes.

        `Completed.__repr__`'s decision (D10) applied one layer up, to the
        struct that carries the same two streams out of `_process`. The
        dataclass default renders both verbatim, and `stdout` is never
        redacted at all while `stderr` is scrubbed only for the secrets the
        SDK was given — so any renderer that walks frame locals (`pytest
        --showlocals`, Sentry's `with_locals`, `cgitb`) would print a payload
        nobody opted into. `argv` stays whole: `_process` redacted it already.

        Only one local reaches this today — `probed` in `_Runner.gate_async`,
        holding a version string. That was equally true of `Completed` right
        up until something rendered frame locals, which is the argument, not
        an exception to it.
        """
        return (
            f"CommandResult(argv={self.argv!r}, exit_code={self.exit_code}, "
            f"stdout=<{len(self.stdout)} chars>, stderr=<{len(self.stderr)} chars>)"
        )


def tolerated_report(result: CommandResult) -> str:
    """Return the report a report-then-fail command wrote, or raise its failure.

    `package test`, `cascade check` and `cascade repair` exit non-zero *with*
    their report on stdout when the outcome is a finding rather than a fault —
    the client tolerates that code (`ok_codes`) so the report comes back as a
    result. The same code with an error envelope, or with no JSON at all, is a
    fault the tolerance must not swallow: this raises it as the exception the
    exit code maps to, stdout preserved for `error_envelope(err)`.

    Args:
        result: The finished call, exit code and both streams.

    Returns:
        `result.stdout`, for the matching `from_json`.

    Raises:
        OcxProcessError: The exit was non-zero and stdout carried no report —
            the subclass `_errors` maps the code to, or the base class for a
            code ocx never assigns.
    """
    if result.exit_code == 0:
        return result.stdout
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        raise _exit_error(result) from None
    if _is_envelope(payload):
        raise _exit_error(result)
    return result.stdout


def _exit_error(result: CommandResult) -> OcxProcessError:
    """Map a tolerated-but-reportless exit to the error class it means.

    `argv` and `stderr` arrive already redacted by `_process`; `stdout` is
    carried verbatim, per D10.
    """
    try:
        cls = _EXIT_CODE_ERRORS.get(ExitCode(result.exit_code), OcxProcessError)
    except ValueError:
        # A signal-killed ocx exits with a status ocx never assigns (137).
        cls = OcxProcessError
    return cls(result.exit_code, result.argv, result.stderr, stdout=result.stdout)


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

    `channel` appears only when it was baked into the build. `shell` is
    always on the wire and may be `null` (`about.rs:30`) — ocx detected no
    shell — so it is the only one of the four leading fields that can be
    `None`.
    """

    version: str
    registry: str
    home: str
    shell: str | None
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
            platforms=_texts(_need(data, "platforms", "about")),
            libc=_texts(_need(data, "libc", "about")),
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
            present=bool(_need(data, "present", "status")),
            current=bool(data.get("current")),
            lock_version=data.get("lock_version"),
            declaration_hash=data.get("declaration_hash"),
            declaration_hash_expected=_need(data, "declaration_hash_expected", "status"),
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
        tools = {name: ToolBinding.from_dict(entry) for name, entry in _table(_need(data, "tools", "status")).items()}
        env = {key: _table(entry) for key, entry in _table(_need(data, "env", "status")).items()}
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
        groups = {name: GroupStatus.from_dict(entry) for name, entry in _table(_need(data, "groups", "status")).items()}
        settings = {name: _table(entry) for name, entry in _table(_need(data, "package_settings", "status")).items()}
        return cls(
            project=_need(data, "project", "status"),
            lock=LockStatus.from_dict(_table(_need(data, "lock", "status"))),
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
    """A name a package provides — the row shape `binaries` and `entrypoints` share.

    Attributes:
        name: The binary or entrypoint name the package claims.
        package: The package that claimed it, or `None` when ocx could not
            attribute the claim. Absent-when-unattributed (D7): `env.rs:100`
            skips the key, and its own doc reserves `None` for a future
            source with no clean attribution.
    """

    name: str
    package: str | None = None

    @property
    def ref(self) -> PackageRef | None:
        """The providing package, as a `PackageRef`, or `None` when unattributed."""
        return PackageRef(self.package) if self.package is not None else None

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> PackageBinding:
        """Build from one decoded `binaries` or `entrypoints` entry."""
        return cls(name=_need(data, "name", "env"), package=data.get("package"))


@dataclass(frozen=True, slots=True)
class Integration:
    """A tool integration a package declares, with its payload carried untyped.

    Attributes:
        namespace: The integration namespace the package declares.
        package: The declaring package, or `None` when ocx could not attribute
            the contribution — `env.rs:137` skips the key for the same reason
            `PackageBinding.package` does (D7).
        payload: The interpolated payload, uninterpreted.
    """

    namespace: str
    package: str | None = None
    payload: Mapping[str, Any] = _EMPTY

    @property
    def ref(self) -> PackageRef | None:
        """The declaring package, as a `PackageRef`, or `None` when unattributed."""
        return PackageRef(self.package) if self.package is not None else None

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Integration:
        """Build from one decoded `integrations` entry."""
        return cls(
            namespace=_need(data, "namespace", "env"),
            package=data.get("package"),
            payload=_table(_need(data, "payload", "env")),
        )


@dataclass(frozen=True, slots=True)
class Advisory:
    """Something ocx wants the caller to know about a composed environment.

    Attributes:
        kind: The machine discriminator to branch on, never `message`.
        package: The package the advisory is about.
        message: The human rendering of the same fact.
        key: The environment variable named, or `None` for an advisory that
            names none. Absent-when-unused (D7): `env.rs:175` skips the key,
            and the `undeclared-binaries` variant (`env.rs:189`) emits no key
            at all, so reading it as required crashes `env()` outright.
    """

    kind: str
    package: str
    message: str
    key: str | None = None

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
            message=_need(data, "message", "env"),
            key=data.get("key"),
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
        >>> wire = '{"entries": [{"key": "TOOL", "type": "constant", "value": "1"}],'
        >>> wire += ' "binaries": [], "entrypoints": [], "integrations": [], "advisories": []}'
        >>> [entry.key for entry in EnvReport.from_json(wire)]
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
            entries=tuple(EnvEntry.from_dict(row) for row in _rows(_need(data, "entries", "env"))),
            binaries=tuple(PackageBinding.from_dict(row) for row in _rows(_need(data, "binaries", "env"))),
            entrypoints=tuple(PackageBinding.from_dict(row) for row in _rows(_need(data, "entrypoints", "env"))),
            integrations=tuple(Integration.from_dict(row) for row in _rows(_need(data, "integrations", "env"))),
            advisories=tuple(Advisory.from_dict(row) for row in _rows(_need(data, "advisories", "env"))),
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
            packages=tuple(InspectedPackage.from_dict(row) for row in _rows(_need(data, "packages", "inspect"))),
            env=tuple(EnvEntry.from_dict(row) for row in _rows(_need(data, "env", "inspect"))),
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
class DryRunEntry:
    """One row of `pull(dry_run=True)` — what a real pull would do to a package.

    `pull_dry_run.rs:76` serializes the preview as a **bare root array**, so
    there is no envelope object and no wrapper struct here: the rows are the
    document (D11).

    Attributes:
        package: The pinned identifier the row is about.
        status: `"cached"` or `"would-fetch"`, carried as a raw `str` (D8).
        path: Where the package already sits, or `None` when nothing is
            cached — always-present-nullable (D7), and `null` on exactly the
            `would-fetch` rows this preview exists to surface.
    """

    package: str
    status: str
    path: str | None

    @property
    def ref(self) -> PackageRef:
        """The previewed package, as a `PackageRef`."""
        return PackageRef(self.package)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> DryRunEntry:
        """Build from one decoded array entry."""
        return cls(
            package=_need(data, "package", "pull"),
            status=_need(data, "status", "pull"),
            path=_need(data, "path", "pull"),
        )


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
        return cls(packages=MappingProxyType(packages), advisories=_rows(_need(data, _ADVISORIES, "pull")))


@dataclass(frozen=True, slots=True)
class InstalledPackage:
    """One package `package install` or `package select` put in place.

    Attributes:
        identifier: The pinned identifier, `<as given>@sha256:...`.
        path: The symlink that now points at it — `candidates/<tag>` after an
            install, `current` after a select — or `None` when nothing was
            materialized. Always-present-nullable (`install.rs:23`).
        metadata: The package's `metadata.json`, carried untyped.
    """

    identifier: str
    path: str | None
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
            metadata=_table(_need(data, "metadata", "package install")),
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
        path: The symlink that went away, or `None` when there was none —
            always-present-nullable (`removed.rs:40`), which every
            `status: "absent"` row answers with.
    """

    package: str
    status: str
    path: str | None

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
            repeated=bool(_need(data, "repeated", "package deps")),
            visibility=_need(data, "visibility", "package deps"),
            dependencies=_rows(_need(data, "dependencies", "package deps")),
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
class PackageDescription:
    """One package's description metadata, from `package description pull` (C-021, D4).

    `api/data/package_description.rs`'s `Inner` — every field is optional
    upstream, but carries no `skip_serializing_if`, so all three keys are
    **always present and `null` when unset** — not absent-when-empty. D7
    therefore wants `str | None` with no Python default, parsed via `_need`,
    not `data.get()`. An all-`None` instance is still a real, valid value
    either way: it is distinct from the `None` entry `InfoResult` uses when
    the registry holds no description at all.

    Attributes:
        title: The package's display title.
        description: Its prose description.
        keywords: A single delimited string upstream, **not** a list — do
            not split it into a collection here.
    """

    title: str | None
    description: str | None
    keywords: str | None

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> PackageDescription:
        """Build from one non-`null` value of `package description pull`'s keyed object."""
        return cls(
            title=_need(data, "title", _DESCRIPTION_PULL),
            description=_need(data, "description", _DESCRIPTION_PULL),
            keywords=_need(data, "keywords", _DESCRIPTION_PULL),
        )


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
            platforms=_table(_need(data, "platforms", "lock")),
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

    exit_code: int
    stdout: str
    stderr: str
    duration_ms: int
    truncated: bool

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> TestRun:
        """Build from the decoded `run` object."""
        return cls(
            exit_code=int(_need(data, "exit_code", "package test")),
            stdout=str(_need(data, "stdout", "package test")),
            stderr=str(_need(data, "stderr", "package test")),
            duration_ms=int(_need(data, "duration_ms", "package test")),
            truncated=bool(_need(data, "truncated", "package test")),
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
        run: What the tested command did, or `None` when nothing ran.
            Always-present-nullable (D7): `script_run.rs:74` carries no
            `skip_serializing_if`, so a `null` here is ocx saying the script
            reached no terminal `ocx.run` — not a run whose fields defaulted.
        assertion: The assertion that fired, or `None` when it passed.
            Always-present-nullable for the same reason (`script_run.rs:73`).
    """

    __test__ = False
    """Not a pytest test class, despite the name ocx's command gives it."""

    status: str
    run: TestRun | None = None
    assertion: Assertion | None = None

    @property
    def passed(self) -> bool:
        """Whether the test passed — `status` decides, never the exit code."""
        return self.status == _PASSED

    @classmethod
    def from_json(cls, raw: str) -> TestResult:
        """Parse `ocx --format json package test --script ...` output."""
        data = _object(raw, "package test")
        assertion = _need(data, "assertion", "package test")
        run = _need(data, "run", "package test")
        return cls(
            status=_need(data, "status", "package test"),
            run=TestRun.from_dict(_table(run)) if run is not None else None,
            assertion=Assertion.from_dict(_table(assertion)) if assertion is not None else None,
        )


@dataclass(frozen=True, slots=True)
class PushResult:
    """`ocx package push` — what landed in the registry.

    Attributes:
        identifier: What was pushed.
        status: What happened, e.g. `"pushed"`.
        manifest_digest: Digest of the manifest that was written.
        cascade_tags_written: Cascading tags updated by the push.
        keep_tags_written: The `__ocx.keep.sha256-<hex>` tags written, one
            per platform manifest — `push`'s `--keep-tag` flag, which is on
            by default. Named for the wire key ocx 0.6 emits (C-005); 0.5
            called it `canonical_tags_written` and wrote a `sha256.<hex>`
            tag, so a 0.1 call site reading the old attribute must be edited.
        layers: Layer counts (`mounted`, `uploaded`, `verified`).
        platform_digests: Manifest digest per pushed platform (C-005).
            Absent-when-unused (D7).
        signatures: One row per platform's signing outcome, when
            `push(..., sign=True)` was requested (C-005, C-018).
            Absent-when-unused (D7).
        attestation: The push's attestation outcome, when one was requested
            (C-005, C-019). Absent-when-unused (D7).
    """

    identifier: str
    status: str
    manifest_digest: str
    cascade_tags_written: tuple[str, ...]
    keep_tags_written: tuple[str, ...]
    layers: Mapping[str, Any]
    platform_digests: Mapping[str, str] = _EMPTY
    signatures: tuple[SignedPlatformReport, ...] = ()
    attestation: AttestationOutcome | None = None

    @property
    def ref(self) -> PackageRef:
        """The pushed package, as a `PackageRef`."""
        return PackageRef(self.identifier)

    @classmethod
    def from_json(cls, raw: str) -> PushResult:
        """Parse `ocx --format json package push` output.

        The payload is **bare** (D11) — the report sits at the JSON root, with
        no envelope to unwrap. A push that lands and then fails to sign exits
        non-zero carrying this same document, so `partial_report(err)` feeds
        straight back into this parser (D10, C-009).
        """
        data = _object(raw, _PUSH)
        attestation = data.get("attestation")
        return cls(
            identifier=_need(data, "identifier", _PUSH),
            status=_need(data, "status", _PUSH),
            manifest_digest=_need(data, "manifest_digest", _PUSH),
            cascade_tags_written=_texts(_need(data, "cascade_tags_written", _PUSH)),
            keep_tags_written=_texts(_need(data, "keep_tags_written", _PUSH)),
            layers=_table(_need(data, "layers", _PUSH)),
            platform_digests=_table(data.get("platform_digests")),
            signatures=tuple(SignedPlatformReport.from_dict(row) for row in _rows(data.get("signatures"))),
            attestation=AttestationOutcome.from_dict(_table(attestation)) if attestation is not None else None,
        )


@dataclass(frozen=True, slots=True)
class SignatureLegReport:
    """One signature format's outcome within a `SignatureReport` (C-011).

    Attributes:
        format: The signature format this leg produced — `"bundle"` or
            `"simplesigning"` (`signature.rs:89`; `"both"` is write-side
            only — it requests two legs, never labels one), carried as raw
            `str` (D8).
        payload_digest: Digest of the signed payload, when this leg landed.
        manifest_digest: Digest of the manifest the signature attaches to,
            when this leg landed.
        error: Why this leg failed, when it did. A `--signature-format both`
            run where one leg lands and one fails names the failed leg here
            (D10) — recover the whole report with `partial_report(err)`.
    """

    format: str
    payload_digest: str | None = None
    manifest_digest: str | None = None
    error: str | None = None

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> SignatureLegReport:
        """Build from one decoded entry of `SignatureReport.legs`."""
        return cls(
            format=_need(data, "format", _SIGN),
            payload_digest=data.get("payload_digest"),
            manifest_digest=data.get("manifest_digest"),
            error=data.get("error"),
        )


@dataclass(frozen=True, slots=True)
class SignatureReport:
    """`ocx package sign` on a single reference — no tag sweep (C-011).

    **Payload is enveloped** (D11): `from_json` unwraps `data` via
    `_envelope` before parsing. A `--signature-format both` run where one leg
    lands and one fails exits non-zero carrying a full report — recover it
    with `partial_report(err)` and this same `from_json` (D10); `legs` names
    which leg died through its `error` field.

    Per D7's field-order rule, `public_key_hint` moves to the end even
    though upstream orders it before `transparency_log_index` — transcribing
    that order verbatim would put a defaulted field before a required one.

    Attributes:
        identifier: The identifier that was signed.
        subject_digest: The manifest digest the signature covers.
        legs: One row per signature format requested.
        platform: The signed platform.
        signer: The signing mode label — never an identity and never a key
            reference. `"keyless-fulcio"` under keyless signing
            (`signature.rs:159-162`), else `key_backend`'s own backend
            label (`awskms`, `file`, ...). Deliberately spelled differently
            from `key_backend`, which reads the plain `"keyless"` in that
            same case (`signature.rs:155-157`): upstream's own comment
            notes that reusing `"keyless"` here too would leave no field
            that actually names the mechanism.
        certificate_identity: The Fulcio certificate's identity. Plain
            `String` upstream (`signature.rs:59`) — always emitted, on both
            the keyless and key-based paths.
        certificate_oidc_issuer: The Fulcio certificate's OIDC issuer.
            Always emitted (`signature.rs:61`), same as above.
        key_backend: Which key backend produced the signature, carried as
            raw `str` (D8; irregular KMS spellings live here unmodified).
            Always emitted (`signature.rs:68`); under keyless signing the
            value is literally `"keyless"`, not absent.
        transparency_log_index: The Rekor entry index. Always present in the
            JSON, but `None` when nothing was logged.
        public_key_hint: A hint identifying the verifying key, when ocx
            supplied one.
    """

    identifier: str
    subject_digest: str
    legs: tuple[SignatureLegReport, ...]
    platform: str
    signer: str
    certificate_identity: str
    certificate_oidc_issuer: str
    key_backend: str
    transparency_log_index: int | None
    public_key_hint: str | None = None

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> SignatureReport:
        """Build from a decoded sign report.

        Used both for the envelope's `data` (a bare `sign`) and, through
        C-017's row-parser seam, for one `SweptTagReport.report` row of a
        swept `sign`.
        """
        return cls(
            identifier=_need(data, "identifier", _SIGN),
            subject_digest=_need(data, "subject_digest", _SIGN),
            legs=tuple(SignatureLegReport.from_dict(row) for row in _rows(_need(data, "legs", _SIGN))),
            platform=_need(data, "platform", _SIGN),
            signer=_need(data, "signer", _SIGN),
            certificate_identity=_need(data, "certificate_identity", _SIGN),
            certificate_oidc_issuer=_need(data, "certificate_oidc_issuer", _SIGN),
            key_backend=_need(data, "key_backend", _SIGN),
            transparency_log_index=_need(data, "transparency_log_index", _SIGN),
            public_key_hint=data.get("public_key_hint"),
        )

    @classmethod
    def from_json(cls, raw: str) -> SignatureReport:
        """Parse `ocx --format json package sign` output (no tag sweep)."""
        return cls.from_dict(_envelope(raw, _SIGN))


@dataclass(frozen=True, slots=True)
class SignatureEntry:
    """One signature `verify` found on the inspected object (C-012).

    Field list verified against `verification.rs:50-85`.

    Attributes:
        signature_format: Which format this entry is, carried as raw `str`
            (D8).
        discovery_method: How ocx found it — referrer API or sidecar tag,
            carried as raw `str` (D8).
        key_backend: Which key backend verified it, carried as raw `str`
            (D8).
        referrer_digest: This signature's own digest. **Not always a
            manifest digest** — for a simplesigning sidecar it is a layer
            blob digest; feeding it to a manifest fetch 404s.
        certificate_identity: The Fulcio certificate's identity, when the
            signature is keyless.
        certificate_oidc_issuer: The Fulcio certificate's OIDC issuer, when
            the signature is keyless.
        signed_at: When the signature was produced, as ocx spelled it, when
            present.
        rekor_log_index: The Rekor entry index, when present. **Named
            differently from `SignatureReport`/`AttestationReport`'s
            `transparency_log_index`** — same concept, two wire names, and
            unlike those two structs this field is absent rather than
            always-present-nullable. Do not unify the handling.
    """

    signature_format: str
    discovery_method: str
    key_backend: str
    referrer_digest: str
    certificate_identity: str | None = None
    certificate_oidc_issuer: str | None = None
    signed_at: str | None = None
    rekor_log_index: int | None = None

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> SignatureEntry:
        """Build from one decoded entry of `VerificationReport.signatures`."""
        return cls(
            signature_format=_need(data, "signature_format", _VERIFY),
            discovery_method=_need(data, "discovery_method", _VERIFY),
            key_backend=_need(data, "key_backend", _VERIFY),
            referrer_digest=_need(data, "referrer_digest", _VERIFY),
            certificate_identity=data.get("certificate_identity"),
            certificate_oidc_issuer=data.get("certificate_oidc_issuer"),
            signed_at=data.get("signed_at"),
            rekor_log_index=data.get("rekor_log_index"),
        )


@dataclass(frozen=True, slots=True)
class VerificationReport:
    """`ocx package verify` (C-012).

    **Payload is enveloped** (D11): `from_json` unwraps `data`.

    Attributes:
        subject_digest: The manifest digest that was verified.
        referrer_digest: The verified object's own digest. **Not always a
            manifest digest** — see `SignatureEntry.referrer_digest`.
        certificate_identity: The keyless identity pinned for verification.
            Plain `String` upstream (`verification.rs:115`) — always
            emitted, on both the keyless and key-based paths.
        certificate_oidc_issuer: The keyless OIDC issuer pinned for
            verification. Always emitted (`verification.rs:117`), same as
            above.
        signed_at: When the signature was produced, as ocx spelled it.
            Always emitted (`verification.rs:119`).
        signatures: Matching signatures found. Absent, not empty, when there
            were none (D7).
    """

    subject_digest: str
    referrer_digest: str
    certificate_identity: str
    certificate_oidc_issuer: str
    signed_at: str
    signatures: tuple[SignatureEntry, ...] = ()

    @classmethod
    def from_json(cls, raw: str) -> VerificationReport:
        """Parse `ocx --format json package verify` output."""
        data = _envelope(raw, _VERIFY)
        return cls(
            subject_digest=_need(data, "subject_digest", _VERIFY),
            referrer_digest=_need(data, "referrer_digest", _VERIFY),
            certificate_identity=_need(data, "certificate_identity", _VERIFY),
            certificate_oidc_issuer=_need(data, "certificate_oidc_issuer", _VERIFY),
            signed_at=_need(data, "signed_at", _VERIFY),
            signatures=tuple(SignatureEntry.from_dict(row) for row in _rows(data.get("signatures"))),
        )


@dataclass(frozen=True, slots=True)
class AttestationReport:
    """`ocx package attest` on a single reference — no tag sweep (C-013).

    **Payload is enveloped** (D11): `from_json` unwraps `data`. Flat,
    thirteen fields; per D7's field-order rule the seven optionals move to
    the end even though upstream interleaves `public_key_hint` earlier.

    Attributes:
        identifier: The identifier that was attested.
        platform: The attested platform.
        subject_digest: The manifest digest the attestation covers.
        predicate_type: The **resolved** predicate type URI — not the
            `--type` spelling the caller gave `attest(predicate_type=...)`.
        signed: Whether the attestation itself was signed.
        transparency_log_index: The Rekor entry index. Always present in the
            JSON, but `None` when nothing was logged.
        bundle_digest: Digest of the attestation bundle, when produced.
        referrer_digest: Digest under the OCI referrers API, when attached
            that way.
        sidecar_digest: Digest of the sidecar tag, when attached that way.
            `--signature-format both` populates both digest fields.
        certificate_identity: The Fulcio certificate's identity, for keyless
            signing.
        certificate_oidc_issuer: The Fulcio certificate's OIDC issuer, for
            keyless signing.
        key_backend: Which key backend produced the signature, carried as
            raw `str` (D8).
        public_key_hint: A hint identifying the verifying key, when
            supplied.
    """

    identifier: str
    platform: str
    subject_digest: str
    predicate_type: str
    signed: bool
    transparency_log_index: int | None
    bundle_digest: str | None = None
    referrer_digest: str | None = None
    sidecar_digest: str | None = None
    certificate_identity: str | None = None
    certificate_oidc_issuer: str | None = None
    key_backend: str | None = None
    public_key_hint: str | None = None

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> AttestationReport:
        """Build from a decoded attest report.

        Used both for the envelope's `data` (a bare `attest`) and, through
        C-017's row-parser seam, for one `SweptTagReport.report` row of a
        swept `attest`.
        """
        return cls(
            identifier=_need(data, "identifier", _ATTEST),
            platform=_need(data, "platform", _ATTEST),
            subject_digest=_need(data, "subject_digest", _ATTEST),
            predicate_type=_need(data, "predicate_type", _ATTEST),
            signed=_need(data, "signed", _ATTEST),
            transparency_log_index=_need(data, "transparency_log_index", _ATTEST),
            bundle_digest=data.get("bundle_digest"),
            referrer_digest=data.get("referrer_digest"),
            sidecar_digest=data.get("sidecar_digest"),
            certificate_identity=data.get("certificate_identity"),
            certificate_oidc_issuer=data.get("certificate_oidc_issuer"),
            key_backend=data.get("key_backend"),
            public_key_hint=data.get("public_key_hint"),
        )

    @classmethod
    def from_json(cls, raw: str) -> AttestationReport:
        """Parse `ocx --format json package attest` output (no tag sweep)."""
        return cls.from_dict(_envelope(raw, _ATTEST))


type _SweepRow = SignatureReport | AttestationReport
"""What a swept row's `report` field holds — from `sign --tags` or `attest --tags`."""


@dataclass(frozen=True, slots=True)
class SweptTagReport:
    """One tag's outcome within a `SweepReport` (C-017).

    `sweep.rs:65-89`. `report`, `kind`, and `message` are all
    absent-when-none (D7) —
    `#[serde(skip_serializing_if = "Option::is_none")]` upstream, not
    `null`.

    `report` is present on a **failed** row too, when a
    `--signature-format both` tag lands one leg and loses the other: hiding
    the leg that landed would leave an operator re-signing what is already
    published.

    Attributes:
        tag: The tag this row describes.
        status: `"completed"`, `"skipped"`, or `"failed"`, carried as raw
            `str` (D8).
        report: The tag's own report, when one was produced — a
            `SignatureReport` under a swept `sign`, an `AttestationReport`
            under a swept `attest`. Which one it is follows from which
            command called `from_dict`, not from anything in the row
            itself, which is why parsing it takes a row parser rather than
            guessing.
        kind: The machine-branchable failure slug, when this tag failed.
        message: Sanitized prose describing the failure, free to be
            reworded — the same rule `RefusedEntry.reason` follows.
    """

    tag: str
    status: str
    report: _SweepRow | None = None
    kind: str | None = None
    message: str | None = None

    @classmethod
    def from_dict(cls, data: Mapping[str, Any], row: Callable[[Mapping[str, Any]], _SweepRow]) -> SweptTagReport:
        """Build from one decoded entry of `SweepReport.tags`.

        Args:
            data: The decoded row.
            row: Parses this row's `report` object —
                `SignatureReport.from_dict` for a swept `sign`,
                `AttestationReport.from_dict` for a swept `attest`. The one
                place this module's "no new parsing infrastructure" rule is
                deliberately relaxed (C-017): one struct serves both
                commands rather than duplicating it.
        """
        report = data.get("report")
        return cls(
            tag=_need(data, "tag", _SWEEP),
            status=_need(data, "status", _SWEEP),
            report=row(_table(report)) if report is not None else None,
            kind=data.get("kind"),
            message=data.get("message"),
        )


@dataclass(frozen=True, slots=True)
class SweepReport:
    """`ocx package sign --tags` / `attest --tags` (or `--tags-file`) (C-017, D2).

    Upstream is generic (`sweep.rs:152`,
    `SweepReport<R> { tags: Vec<SweptTagReport<R>> }`); this is its Python
    shape for both instantiations. Carries no `command` or `exit_code`
    field — both are `#[serde(skip)]` upstream.

    **Payload is enveloped** (D11): the envelope's `data` holds
    `{"tags": [...]}`, and its `exit_code` — not part of `data` — is the
    process's own. A partially-failed sweep exits non-zero, so a `failed`
    row usually arrives through `partial_report(err)` rather than a normal
    return (D10); both paths parse through this same `from_json`.

    Attributes:
        tags: One row per swept tag. A plain `Vec` upstream with no `skip`,
            so always present, not absent-when-empty.
    """

    tags: tuple[SweptTagReport, ...]

    @classmethod
    def from_json(cls, raw: str, row: Callable[[Mapping[str, Any]], _SweepRow]) -> SweepReport:
        """Parse an enveloped sweep payload from a swept `sign` or `attest`.

        Args:
            raw: Captured stdout.
            row: Parses each row's `report` object — see
                `SweptTagReport.from_dict`.
        """
        data = _envelope(raw, _SWEEP)
        tags = _rows(_need(data, "tags", _SWEEP))
        return cls(tags=tuple(SweptTagReport.from_dict(entry, row) for entry in tags))


@dataclass(frozen=True, slots=True)
class SignedPlatformReport:
    """One platform's signing outcome, nested in `PushResult.signatures` (C-018).

    `push.rs:115`. The three optionals are absent-when-none (D7): a
    platform whose signing needed no more than the default leg carries
    `report` alone, and `kind`/`message` populate only when that platform's
    signing failed.

    Attributes:
        platform: The platform this row describes.
        status: A `SweptStatus` value (`completed`/`skipped`/`failed`),
            carried as raw `str` (D8).
        report: The platform's own `SignatureReport`, when produced.
        kind: The machine-branchable failure slug, when this platform
            failed.
        message: Sanitized prose describing the failure, free to be
            reworded.
    """

    platform: str
    status: str
    report: SignatureReport | None = None
    kind: str | None = None
    message: str | None = None

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> SignedPlatformReport:
        """Build from one decoded entry of `PushResult.signatures`."""
        report = data.get("report")
        return cls(
            platform=_need(data, "platform", _PUSH),
            status=_need(data, "status", _PUSH),
            report=SignatureReport.from_dict(_table(report)) if report is not None else None,
            kind=data.get("kind"),
            message=data.get("message"),
        )


@dataclass(frozen=True, slots=True)
class AttestationOutcome:
    """The attestation outcome of one `push(..., sign=True)` call (C-019).

    `push.rs:161`, internally tagged on `status`. A bare `str` (the usual D8
    treatment for a scalar enum) would silently drop `predicate_type` and
    `signed`, so this carries every field instead, all but `status`
    optional: `succeeded` populates `predicate_type`/`signed` and at least
    one of `referrer_digest`/`sidecar_digest`; `failed` populates
    `kind`/`message`.

    Attributes:
        status: `"succeeded"` or `"failed"`.
        referrer_digest: The attestation's referrer-API digest, on success.
        sidecar_digest: The attestation's sidecar-tag digest, on success.
            `--signature-format both` populates both digest fields.
        predicate_type: The resolved predicate type URI, on success.
        signed: Whether the attestation was itself signed, on success.
        kind: The machine-branchable failure slug, on failure.
        message: Sanitized prose describing the failure, on failure.
    """

    status: str
    referrer_digest: str | None = None
    sidecar_digest: str | None = None
    predicate_type: str | None = None
    signed: bool | None = None
    kind: str | None = None
    message: str | None = None

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> AttestationOutcome:
        """Build from the decoded `PushResult.attestation` object.

        `status` selects the variant, and each variant's own fields carry no
        `skip_serializing_if` — so they are read with `_need` under the status
        that emits them. `signed` matters most: reading it with `.get` failed
        open, turning "nothing vouches for this document" into `None`.
        """
        status = _need(data, "status", _PUSH)
        if status not in (_ATTESTATION_SUCCEEDED, _ATTESTATION_FAILED):
            raise ValueError(
                f"`ocx {_PUSH}` reported attestation status {status!r}; this SDK knows "
                f"{_ATTESTATION_SUCCEEDED!r} and {_ATTESTATION_FAILED!r}. Refusing rather than "
                "reading it as a failure — a third variant carries fields this SDK would drop."
            )
        succeeded = status == _ATTESTATION_SUCCEEDED
        return cls(
            status=status,
            referrer_digest=data.get("referrer_digest"),
            sidecar_digest=data.get("sidecar_digest"),
            predicate_type=_need(data, "predicate_type", _PUSH) if succeeded else None,
            signed=_need(data, "signed", _PUSH) if succeeded else None,
            kind=None if succeeded else _need(data, "kind", _PUSH),
            message=None if succeeded else _need(data, "message", _PUSH),
        )


@dataclass(frozen=True, slots=True)
class ListingSummary:
    """The `summary` block of an `SbomListingReport` (C-014).

    `sbom.rs:75`. All seven fields are always present.

    Attributes:
        status: `"success"` or `"partial_failure"` — a listing can refuse
            individual candidates and still exit 0 (D10's note that this
            differs from a swept `sign`/`attest`, which exits non-zero on a
            partial failure).
        verification: `"verified"` or `"unverified"`, carried as raw `str`
            (D8) — ocx's `ListingVerification`, lowercase on the wire.
        exit_code: The process's own exit code.
        total: Candidates examined.
        verified: Candidates whose signature verified.
        unverified: Candidates attached without a verifying signature.
        refused: Candidates rejected outright — see `RefusedEntry`.
    """

    status: str
    verification: str
    exit_code: int
    total: int
    verified: int
    unverified: int
    refused: int

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ListingSummary:
        """Build from the decoded `summary` object."""
        return cls(
            status=_need(data, "status", _SBOM),
            verification=_need(data, "verification", _SBOM),
            exit_code=_need(data, "exit_code", _SBOM),
            total=_need(data, "total", _SBOM),
            verified=_need(data, "verified", _SBOM),
            unverified=_need(data, "unverified", _SBOM),
            refused=_need(data, "refused", _SBOM),
        )


@dataclass(frozen=True, slots=True)
class SbomEntry:
    """One SBOM document `sbom` listed (C-014).

    Field list verified against `sbom.rs:104-162`.

    Attributes:
        predicate_type: The SBOM's predicate type URI (CycloneDX, SPDX,
            ...).
        verified: Whether a signature was verified over this document.
            `False` means it is attached raw, with no identity behind it —
            this is ocx policy, not a cosign or CycloneDX term.
        shadowed: Whether a platform-level SBOM of the *same*
            `predicate_type` supersedes this index-level one. Always
            present; `False` is a true claim, not an absence.
        subject_digest: The manifest digest this SBOM describes.
        referrer_digest: This SBOM document's own digest. **Not always a
            manifest digest** — for a verified `.att` sidecar it is a layer
            blob digest (`sbom.rs:128-148`); feeding it to a manifest fetch
            404s. Same caveat `SignatureEntry`/`VerificationReport` carry
            for their own `referrer_digest`.
        certificate_identity: The Fulcio certificate's identity, when
            `verified` and the signature is keyless.
        certificate_oidc_issuer: The Fulcio certificate's OIDC issuer, when
            `verified` and the signature is keyless.
        signed_at: When the signature was produced, as ocx spelled it, when
            `verified`.
        summary: Component counts, when the call carried `--summary` and
            this entry parsed successfully as CycloneDX 1.5-1.7 — see
            `SbomSummaryOut`. `None` otherwise; the listing itself still
            works without `--summary`, it just leaves this unpopulated.
    """

    predicate_type: str
    verified: bool
    shadowed: bool
    subject_digest: str
    referrer_digest: str
    certificate_identity: str | None = None
    certificate_oidc_issuer: str | None = None
    signed_at: str | None = None
    summary: SbomSummaryOut | None = None

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> SbomEntry:
        """Build from one decoded entry of `SbomListingReport.entries`."""
        summary = data.get("summary")
        return cls(
            predicate_type=_need(data, "predicate_type", _SBOM),
            verified=_need(data, "verified", _SBOM),
            shadowed=_need(data, "shadowed", _SBOM),
            subject_digest=_need(data, "subject_digest", _SBOM),
            referrer_digest=_need(data, "referrer_digest", _SBOM),
            certificate_identity=data.get("certificate_identity"),
            certificate_oidc_issuer=data.get("certificate_oidc_issuer"),
            signed_at=data.get("signed_at"),
            summary=SbomSummaryOut.from_dict(_table(summary)) if summary is not None else None,
        )


@dataclass(frozen=True, slots=True)
class RefusedEntry:
    """One candidate `sbom` examined and rejected (C-014).

    All three fields always present (`sbom.rs:192-205`).

    Attributes:
        referrer_digest: The rejected candidate's own digest.
        reason: English prose explaining the refusal — free to be reworded
            upstream (PKG-25); branch on `reason_kind`, never on this text.
        reason_kind: The frozen, machine-branchable slug to switch on.
    """

    referrer_digest: str
    reason: str
    reason_kind: str

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> RefusedEntry:
        """Build from one decoded entry of `SbomListingReport.refused`."""
        return cls(
            referrer_digest=_need(data, "referrer_digest", _SBOM),
            reason=_need(data, "reason", _SBOM),
            reason_kind=_need(data, "reason_kind", _SBOM),
        )


@dataclass(frozen=True, slots=True)
class SbomSummaryOut:
    """CycloneDX component counts for one SBOM entry, under `--summary` (C-014, `sbom.rs:166-177`).

    **Not a second top-level shape.** `--summary` does not change `sbom`'s
    JSON root — it populates this struct per-entry, at
    `SbomEntry.summary`, for each entry that parsed successfully as
    CycloneDX 1.5-1.7. Nothing like `ListingSummary`'s seven fields:
    `sbom()` needs no overload, and this is built with `from_dict`, never
    `from_json`, because it never sits at a JSON root.

    Attributes:
        spec_version: The CycloneDX spec version this document declares.
        component_count: How many components the document lists.
        serial_number: The document's serial number, when it declares one.
        top_level_component: The root component's name, when the document
            declares one.
    """

    spec_version: str
    component_count: int
    serial_number: str | None = None
    top_level_component: str | None = None

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> SbomSummaryOut:
        """Build from the decoded `summary` object of one `SbomEntry`."""
        return cls(
            spec_version=_need(data, "spec_version", _SBOM),
            component_count=_need(data, "component_count", _SBOM),
            serial_number=data.get("serial_number"),
            top_level_component=data.get("top_level_component"),
        )


@dataclass(frozen=True, slots=True)
class SbomListingReport:
    """`ocx package sbom` — the full listing (C-014).

    **Payload is enveloped** (D11): `from_json` unwraps `data`. Exits 0 even
    when candidates were refused — check `summary.status`, not the exit
    code.

    Attributes:
        summary: Counts and overall status.
        entries: SBOM documents ocx listed. A plain `Vec` upstream with no
            `skip`, so always present, not absent-when-empty.
        refused: Candidates ocx rejected outright. Same as `entries`.
    """

    summary: ListingSummary
    entries: tuple[SbomEntry, ...]
    refused: tuple[RefusedEntry, ...]

    @classmethod
    def from_json(cls, raw: str) -> SbomListingReport:
        """Parse `ocx --format json package sbom` output (full listing)."""
        data = _envelope(raw, _SBOM)
        return cls(
            summary=ListingSummary.from_dict(_table(_need(data, "summary", _SBOM))),
            entries=tuple(SbomEntry.from_dict(row) for row in _rows(_need(data, "entries", _SBOM))),
            refused=tuple(RefusedEntry.from_dict(row) for row in _rows(_need(data, "refused", _SBOM))),
        )


@dataclass(frozen=True, slots=True)
class CopiedPlatformRow:
    """One platform `copy` carried over to the target (C-015, `package_copy.rs:128-136`).

    Attributes:
        platform: The platform this row describes.
        digest: The platform manifest's digest at the target.
        disposition: What happened to this platform's manifest at the
            target — a D8 kebab-case scalar enum: `"added"`, `"unchanged"`,
            `"replaced"`, or `"kept-not-in-source"`
            (`ocx_lib/src/publisher/copy.rs:208-219`). Always present.
    """

    platform: str
    digest: str
    disposition: str

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> CopiedPlatformRow:
        """Build from one decoded entry of `CopyReport.platforms`."""
        return cls(
            platform=_need(data, "platform", _COPY),
            digest=_need(data, "digest", _COPY),
            disposition=_need(data, "disposition", _COPY),
        )


@dataclass(frozen=True, slots=True)
class BlobSummary:
    """Blob transfer counts for one `copy` (C-015, `package_copy.rs:140-147`).

    **Not** push's `layers` — that is a different struct (`LayerCounts`,
    `oci/client.rs:75-84`) with a different field set,
    `{mounted, uploaded, verified}`. Copy tracks transfer method including
    blobs that needed no transfer at all; push tracks mount/upload/verify-
    by-digest. Reusing push's shape here would look for a `verified` key
    that copy never sends and silently drop `present` — the D7 failure
    class this module exists to avoid.

    Attributes:
        present: Blobs already present at the target — no transfer needed.
        mounted: Blobs mounted from another repository without a re-upload.
        uploaded: Blobs actually transferred.
    """

    present: int
    mounted: int
    uploaded: int

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> BlobSummary:
        """Build from the decoded `blobs` object."""
        return cls(
            present=_need(data, "present", _COPY),
            mounted=_need(data, "mounted", _COPY),
            uploaded=_need(data, "uploaded", _COPY),
        )


@dataclass(frozen=True, slots=True)
class CopyReport:
    """`ocx package copy` (C-015).

    **Payload is bare** (D11) — `from_json` reads the JSON root directly,
    no envelope to unwrap. Non-empty `sidecar_conflicts` means exit 65 and
    therefore raises; because the payload is bare, `partial_report(err)`
    returns the same document this `from_json` parses, so the conflict list
    is readable from the failure itself.

    All eleven fields verified always present against `package_copy.rs:97-124`
    — no `skip_serializing_if` anywhere on this struct, `description`
    included (upstream comment: "serializes as `null`, so the key is always
    there to branch on"). No D7 reordering needed: this is upstream's own
    field order, verbatim.

    Attributes:
        source: The identifier that was copied.
        target: Where it was copied to.
        status: What happened, e.g. `"copied"`, or `"planned"` under
            `dry_run=True`.
        platforms: One row per platform manifest carried over.
        cascade_tags_written: Cascading tags updated at the target.
        keep_tags_written: `__ocx.keep.sha256-<hex>` tags written at the
            target, one per platform manifest.
        referrers_copied: Referrer-API attachments carried over.
        sidecars_copied: Sidecar-tag attachments carried over.
        sidecar_conflicts: Sidecar tags that already existed at the target
            with different content. Non-empty means the call raised.
        blobs: Blob transfer counts.
        description: A `DescriptionOutcome` value — `"copied"`, `"absent"`,
            or `"skipped-dry-run"` — **not** the description text itself.
            Always present (`null` when there is nothing to report), not
            absent-when-unset.
    """

    source: str
    target: str
    status: str
    platforms: tuple[CopiedPlatformRow, ...]
    cascade_tags_written: tuple[str, ...]
    keep_tags_written: tuple[str, ...]
    referrers_copied: int
    sidecars_copied: int
    sidecar_conflicts: tuple[str, ...]
    blobs: BlobSummary
    description: str | None

    @classmethod
    def from_json(cls, raw: str) -> CopyReport:
        """Parse `ocx --format json package copy` output."""
        data = _object(raw, _COPY)
        return cls(
            source=_need(data, "source", _COPY),
            target=_need(data, "target", _COPY),
            status=_need(data, "status", _COPY),
            platforms=tuple(CopiedPlatformRow.from_dict(row) for row in _rows(_need(data, "platforms", _COPY))),
            cascade_tags_written=_texts(_need(data, "cascade_tags_written", _COPY)),
            keep_tags_written=_texts(_need(data, "keep_tags_written", _COPY)),
            referrers_copied=_need(data, "referrers_copied", _COPY),
            sidecars_copied=_need(data, "sidecars_copied", _COPY),
            sidecar_conflicts=_texts(_need(data, "sidecar_conflicts", _COPY)),
            blobs=BlobSummary.from_dict(_table(_need(data, "blobs", _COPY))),
            description=_need(data, "description", _COPY),
        )


@dataclass(frozen=True, slots=True)
class CapabilityCheck:
    """One forge capability `announce`/`claim` probed before writing.

    Attributes:
        name: The capability — `git-version`, `push-access`, `job-token-push`
            or `job-token-allowlist` — carried as raw `str` (D8).
        status: What the probe found: `ok`, `skipped` for a check the
            transport made inapplicable, or a failure status. Raw `str`.
        detail: What the probe saw, when it recorded anything. Always on the
            wire, `null` when empty.
    """

    name: str
    status: str
    detail: str | None

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> CapabilityCheck:
        """Build from one decoded `capability_checks` entry."""
        return cls(
            name=_need(data, "name", _ANNOUNCE),
            status=_need(data, "status", _ANNOUNCE),
            detail=_need(data, "detail", _ANNOUNCE),
        )


@dataclass(frozen=True, slots=True)
class AnnounceReport:
    """`ocx package announce` — what reached the index (C-061).

    **Payload is bare** (D11). Every one of the fourteen keys is always on
    the wire; the nullable ones are `null` rather than absent, so all are
    read with `_need` and typed `| None` where upstream is `Option`.

    Attributes:
        package: The announced `<namespace>/<package>`.
        status: `"updated"` when a rebuilt root was committed, `"unchanged"`
            when it was byte-identical to the committed one. An unchanged run
            can still report a pull request it ensured.
        desc_status: `"updated"` when the package's `__ocx.desc` artifact
            moved, else `"unchanged"`.
        forge: `"github"` or `"gitlab"`, as resolved.
        transport: `"api"` or `"git"`.
        credential_kind: `"job-token"`, `"token"` or `"none"` — what the
            identity ladder resolved for the API credential.
        push_credential_kind: `"job-token"`, `"token"`, `"git-helper"`, or
            `None` under the `api` transport, which pushes nothing.
        branch: The announce branch, or `None` under `--out`.
        pull_request_url: The request's URL, when one was opened or ensured.
        pull_request_number: Its number, likewise.
        fork: The fork the request came from, when `--fork` was given.
        written_paths: Files written under `--out`; empty otherwise.
        capability_checks: The forge probes, in `CapabilityName` order.
        reserved_tags_dropped: `__ocx` and legacy `sha256.<hex>` tags named
            on the command line and dropped: a reported fact of a
            successful run, never a failure.
    """

    package: str
    status: str
    desc_status: str
    forge: str
    transport: str
    credential_kind: str
    push_credential_kind: str | None
    branch: str | None
    pull_request_url: str | None
    pull_request_number: int | None
    fork: str | None
    written_paths: tuple[str, ...]
    capability_checks: tuple[CapabilityCheck, ...]
    reserved_tags_dropped: tuple[str, ...]

    @classmethod
    def from_json(cls, raw: str) -> AnnounceReport:
        """Parse `ocx --format json package announce` output."""
        data = _object(raw, _ANNOUNCE)
        return cls(
            package=_need(data, "package", _ANNOUNCE),
            status=_need(data, "status", _ANNOUNCE),
            desc_status=_need(data, "desc_status", _ANNOUNCE),
            forge=_need(data, "forge", _ANNOUNCE),
            transport=_need(data, "transport", _ANNOUNCE),
            credential_kind=_need(data, "credential_kind", _ANNOUNCE),
            push_credential_kind=_need(data, "push_credential_kind", _ANNOUNCE),
            branch=_need(data, "branch", _ANNOUNCE),
            pull_request_url=_need(data, "pull_request_url", _ANNOUNCE),
            pull_request_number=_need(data, "pull_request_number", _ANNOUNCE),
            fork=_need(data, "fork", _ANNOUNCE),
            written_paths=_texts(_need(data, "written_paths", _ANNOUNCE)),
            capability_checks=tuple(
                CapabilityCheck.from_dict(row) for row in _rows(_need(data, "capability_checks", _ANNOUNCE))
            ),
            reserved_tags_dropped=_texts(_need(data, "reserved_tags_dropped", _ANNOUNCE)),
        )


@dataclass(frozen=True, slots=True)
class ClaimOwner:
    """One forge account on a `claim` report, as author or owner.

    Attributes:
        login: The forge's canonical login spelling.
        id: The forge's immutable numeric account id.
    """

    login: str
    id: int

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ClaimOwner:
        """Build from a decoded `author` or `owners` entry."""
        return cls(login=_need(data, "login", _CLAIM), id=_need(data, "id", _CLAIM))


@dataclass(frozen=True, slots=True)
class ClaimReport:
    """`ocx package claim` — the index entry a claim rendered (C-060).

    **Payload is bare** (D11), seventeen keys, every one always on the wire.
    A package that is already claimed never produces this: ocx exits 65 with
    an error envelope instead, which `error_envelope(err)` recovers.

    Attributes:
        package: The claimed `<namespace>/<package>`, as given.
        name: The logical name written into the root.
        status: `"updated"` or `"unchanged"` — against the open claim
            branch, not the committed root, so a `--out` run is always
            `"updated"`.
        forge: `"github"` or `"gitlab"`.
        transport: `"api"` or `"git"`.
        credential_kind: `"job-token"`, `"token"` or `"none"`.
        push_credential_kind: `"job-token"`, `"token"`, `"git-helper"`, or
            `None` under the `api` transport.
        author: The identity that authored the request, or `None` when
            neither the token nor the CI environment named one. **Not
            attested** — see `author_identity_source`.
        author_identity_source: `"resolved"` when the forge's own answer
            about the credential produced `author`, `"ci-environment"` when
            an ordinary environment read did; `None` exactly when `author` is.
        owners: The recorded owners, in order.
        owner_identity_source: `"resolved"`, `"asserted"` or
            `"ci-environment"`.
        branch: The claim branch.
        pull_request_url: The request's URL, when one was opened.
        pull_request_number: Its number, likewise.
        fork: The fork the request came from, when `--fork` was given.
        written_paths: Files written under `--out`; empty otherwise.
        capability_checks: The forge probes, in `CapabilityName` order.
    """

    package: str
    name: str
    status: str
    forge: str
    transport: str
    credential_kind: str
    push_credential_kind: str | None
    author: ClaimOwner | None
    author_identity_source: str | None
    owners: tuple[ClaimOwner, ...]
    owner_identity_source: str
    branch: str
    pull_request_url: str | None
    pull_request_number: int | None
    fork: str | None
    written_paths: tuple[str, ...]
    capability_checks: tuple[CapabilityCheck, ...]

    @classmethod
    def from_json(cls, raw: str) -> ClaimReport:
        """Parse `ocx --format json package claim` output."""
        data = _object(raw, _CLAIM)
        author = _need(data, "author", _CLAIM)
        return cls(
            package=_need(data, "package", _CLAIM),
            name=_need(data, "name", _CLAIM),
            status=_need(data, "status", _CLAIM),
            forge=_need(data, "forge", _CLAIM),
            transport=_need(data, "transport", _CLAIM),
            credential_kind=_need(data, "credential_kind", _CLAIM),
            push_credential_kind=_need(data, "push_credential_kind", _CLAIM),
            author=ClaimOwner.from_dict(_table(author)) if author is not None else None,
            author_identity_source=_need(data, "author_identity_source", _CLAIM),
            owners=tuple(ClaimOwner.from_dict(row) for row in _rows(_need(data, "owners", _CLAIM))),
            owner_identity_source=_need(data, "owner_identity_source", _CLAIM),
            branch=_need(data, "branch", _CLAIM),
            pull_request_url=_need(data, "pull_request_url", _CLAIM),
            pull_request_number=_need(data, "pull_request_number", _CLAIM),
            fork=_need(data, "fork", _CLAIM),
            written_paths=_texts(_need(data, "written_paths", _CLAIM)),
            capability_checks=tuple(
                CapabilityCheck.from_dict(row) for row in _rows(_need(data, "capability_checks", _CLAIM))
            ),
        )


@dataclass(frozen=True, slots=True)
class SlotRow:
    """One (rolling tag, platform) slot `cascade check` examined.

    Attributes:
        tag: The rolling tag — `latest`, `3`, `3.28`, or a variant name.
        platform: The OCI platform object of the slot, carried untyped: the
            published schema types it as `true` (anything), and no dispatch
            here needs its fields.
        status: `ok`, `missing`, `stale`, `orphan` or `duplicate` — the
            finding, as raw `str` (D8). Anything but `ok` is what makes
            `check` exit 65.
        observed: The digest the alias carries for this platform, if any.
        expected: The digest the fold expects, if any.
        source: The version the expectation was folded from, e.g.
            `"1.0.1"`, or `None`. Published as `PackageVersion` (a string)
            since 0.6.2; 0.6.1's schema named an integer `Version` here, a
            generator artifact the wire never bore out (ocx-sh/ocx#460).
        observed_source: The version the observed digest belongs to, or
            `None` when the alias points at content no observed leaf carries.
    """

    tag: str
    platform: Mapping[str, Any]
    status: str
    observed: str | None
    expected: str | None
    source: str | None
    observed_source: str | None

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> SlotRow:
        """Build from one decoded `rows` entry."""
        return cls(
            tag=_need(data, "tag", _CASCADE),
            platform=_table(_need(data, "platform", _CASCADE)),
            status=_need(data, "status", _CASCADE),
            observed=_need(data, "observed", _CASCADE),
            expected=_need(data, "expected", _CASCADE),
            source=_need(data, "source", _CASCADE),
            observed_source=_need(data, "observed_source", _CASCADE),
        )


@dataclass(frozen=True, slots=True)
class IndexFinding:
    """Where the public index disagrees with the registry about an alias.

    Internally tagged on `finding`: `stale` carries the two digests, and
    `not-committed` carries only the tag. Reported for `ocx.sh/...` packages
    alone, and never fixed by `repair` — announcing the tag is what fixes it.

    Attributes:
        tag: The rolling tag.
        finding: `"stale"` or `"not-committed"`, raw `str` (D8).
        committed: The digest the index committed, under `stale`.
        live: The digest the alias points at today, under `stale`.
    """

    tag: str
    finding: str
    committed: str | None = None
    live: str | None = None

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> IndexFinding:
        """Build from one decoded `index_findings` entry."""
        return cls(
            tag=_need(data, "tag", _CASCADE),
            finding=_need(data, "finding", _CASCADE),
            committed=data.get("committed"),
            live=data.get("live"),
        )


@dataclass(frozen=True, slots=True)
class CascadeReport:
    """One package's rolling-tag audit — the `check` document, and the `report` inside a `repair` entry.

    First-cut depth: the rows, the index findings and the tag lists are
    typed; `aliases` and `unrepairable` are carried as opaque mappings
    (`Integration.payload`'s precedent) until a consumer needs their fields.

    Attributes:
        identifier: The physical repository the graph was read from.
        logical: The logical name the caller gave, when it differed.
        aliases: Rolling tag to its alias state (`{"state": "present"}`,
            `{"state": "absent"}`, or `{"state": "not-an-index", "digest":
            ...}`), untyped.
        rows: One slot per (rolling tag, platform).
        index_findings: Public-index disagreements; empty off `ocx.sh`.
        ignored_tags: Tags the audit skipped — the keep tags among them.
        unrepairable: Findings `repair` refuses to fix, each `{tag, reason,
            digest?}`, untyped.
    """

    identifier: str
    logical: str | None
    aliases: Mapping[str, Mapping[str, Any]]
    rows: tuple[SlotRow, ...]
    index_findings: tuple[IndexFinding, ...]
    ignored_tags: tuple[str, ...]
    unrepairable: tuple[Mapping[str, Any], ...]

    @property
    def ref(self) -> PackageRef:
        """The audited repository, as a `PackageRef`."""
        return PackageRef(self.identifier)

    @property
    def clean(self) -> bool:
        """Whether nothing disagrees: every row `ok`, no index finding, nothing unrepairable."""
        return all(row.status == "ok" for row in self.rows) and not self.index_findings and not self.unrepairable

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> CascadeReport:
        """Build from one decoded `reports` entry (or a repair entry's `report`)."""
        aliases = {tag: _table(state) for tag, state in _table(_need(data, "aliases", _CASCADE)).items()}
        return cls(
            identifier=_need(data, "identifier", _CASCADE),
            logical=_need(data, "logical", _CASCADE),
            aliases=MappingProxyType(aliases),
            rows=tuple(SlotRow.from_dict(row) for row in _rows(_need(data, "rows", _CASCADE))),
            index_findings=tuple(IndexFinding.from_dict(row) for row in _rows(_need(data, "index_findings", _CASCADE))),
            ignored_tags=_texts(_need(data, "ignored_tags", _CASCADE)),
            unrepairable=_rows(_need(data, "unrepairable", _CASCADE)),
        )


@dataclass(frozen=True, slots=True)
class CascadeCheckReport:
    """`ocx package cascade check` — one audit per package, in input order.

    **Report-then-fail**: a finding makes the process exit 65 *with* this
    document on stdout, so the client tolerates that code and hands the
    report back as a result (`tolerated_report`). `exit_code` is the process's
    own, carried so a caller can branch without re-deriving it from the rows.

    Attributes:
        reports: One entry per package audited.
        exit_code: `0` when everything agreed, `65` when anything did not.
    """

    reports: tuple[CascadeReport, ...]
    exit_code: int = 0

    @property
    def clean(self) -> bool:
        """Whether the audit found nothing — the exit code's answer."""
        return self.exit_code == 0

    @classmethod
    def from_json(cls, raw: str, *, exit_code: int = 0) -> CascadeCheckReport:
        """Parse `ocx --format json package cascade check` output.

        Args:
            raw: Captured stdout.
            exit_code: The process's exit status, carried onto the result.
        """
        data = _object(raw, _CASCADE_CHECK)
        reports = _rows(_need(data, "reports", _CASCADE_CHECK))
        return cls(reports=tuple(CascadeReport.from_dict(row) for row in reports), exit_code=exit_code)


@dataclass(frozen=True, slots=True)
class RepairOutcome:
    """What `cascade repair` did to one rolling tag.

    The wire nests a `WriteOutcome` object under `outcome`, internally
    tagged on its own `outcome` key: `written` (with `digest`, `verified`,
    and `dropped` when child digests were pruned), `refused` (an
    unrepairable finding's `tag`/`reason`/`digest`), `raced` (`expected`,
    `live`) or `failed` (`message`). First-cut depth: the discriminator is
    typed and the variant's fields ride along untyped.

    Attributes:
        tag: The rolling tag.
        outcome: `"written"`, `"refused"`, `"raced"` or `"failed"`, raw
            `str` (D8).
        detail: The whole `WriteOutcome` object, `outcome` key included.
    """

    tag: str
    outcome: str
    detail: Mapping[str, Any]

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> RepairOutcome:
        """Build from one decoded `outcomes` entry."""
        detail = _table(_need(data, "outcome", _CASCADE_REPAIR))
        return cls(
            tag=_need(data, "tag", _CASCADE_REPAIR), outcome=_need(detail, "outcome", _CASCADE_REPAIR), detail=detail
        )


@dataclass(frozen=True, slots=True)
class RepairEntry:
    """One package's `cascade repair` run.

    Attributes:
        report: The audit the plan was computed from — the same shape
            `check` reports.
        planned: The alias indexes the run would write (or wrote), each
            `{tag, index, observed_digest, referenced_digests, reasons}`,
            carried untyped: the `index` is a whole OCI image index.
        outcomes: What happened per tag. Empty on a dry run.
        announce_tags: The rolling tags this run moved or created — the
            lines `--announce-tags` writes, echoed for a JSON consumer.
    """

    report: CascadeReport
    planned: tuple[Mapping[str, Any], ...]
    outcomes: tuple[RepairOutcome, ...]
    announce_tags: tuple[str, ...]

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> RepairEntry:
        """Build from one decoded `entries` entry."""
        return cls(
            report=CascadeReport.from_dict(_table(_need(data, "report", _CASCADE_REPAIR))),
            planned=_rows(_need(data, "planned", _CASCADE_REPAIR)),
            outcomes=tuple(RepairOutcome.from_dict(row) for row in _rows(_need(data, "outcomes", _CASCADE_REPAIR))),
            announce_tags=_texts(_need(data, "announce_tags", _CASCADE_REPAIR)),
        )


@dataclass(frozen=True, slots=True)
class CascadeRepairReport:
    """`ocx package cascade repair` — what was re-pointed, or would be.

    **Report-then-fail** like `check`: exit 65 with this document when a
    finding remains — including on a `dry_run`, where "planned anything"
    is the finding — so `exit_code` rather than an exception says whether
    the registry now agrees with itself.

    Attributes:
        entries: One entry per package, in input order.
        dry_run: `True` when nothing was written because the run was a preview.
        announce_tags_path: Where `--announce-tags` was written, when given.
        exit_code: `0` when every attempted write succeeded and nothing
            remains, `65` otherwise.
    """

    entries: tuple[RepairEntry, ...]
    dry_run: bool
    announce_tags_path: str | None
    exit_code: int = 0

    @property
    def clean(self) -> bool:
        """Whether no finding remains — the exit code's answer."""
        return self.exit_code == 0

    @classmethod
    def from_json(cls, raw: str, *, exit_code: int = 0) -> CascadeRepairReport:
        """Parse `ocx --format json package cascade repair` output.

        Args:
            raw: Captured stdout.
            exit_code: The process's exit status, carried onto the result.
        """
        data = _object(raw, _CASCADE_REPAIR)
        return cls(
            entries=tuple(RepairEntry.from_dict(row) for row in _rows(_need(data, "entries", _CASCADE_REPAIR))),
            dry_run=_need(data, "dry_run", _CASCADE_REPAIR),
            announce_tags_path=_need(data, "announce_tags_path", _CASCADE_REPAIR),
            exit_code=exit_code,
        )


@dataclass(frozen=True, slots=True)
class CleanEntry:
    """One thing `ocx clean` removed, or would remove.

    Attributes:
        kind: `"object"` for a store object, `"temp"` for a temp directory,
            or `"consent"` for a `state/projects/<key>/` directory whose
            consent stamp was swept — the entry that revokes a project's
            shell activation, which is why a preview names it. Raw `str` (D8).
        dry_run: Whether this was a preview.
        path: What was (or would be) removed.
        held_by: Project `ocx.lock` paths holding the entry; empty when no
            registered project protects it, or under `--force`.
    """

    kind: str
    dry_run: bool
    path: str
    held_by: tuple[str, ...]

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> CleanEntry:
        """Build from one decoded array entry."""
        return cls(
            kind=_need(data, "kind", _CLEAN),
            dry_run=_need(data, "dry_run", _CLEAN),
            path=_need(data, "path", _CLEAN),
            held_by=_texts(_need(data, "held_by", _CLEAN)),
        )


@dataclass(frozen=True, slots=True)
class BuildReceipt:
    """The `<bundle-stem>-receipt.json` sidecar `ocx package create` writes.

    `ocx package receipt <bundle>` prints what `create` was told — the
    platform it resolved against and the identifier the bundle will publish
    under — so `push` and `test` need not restate either. Both keys are
    absent when the build recorded neither, never `null`, so both are read
    with `.get`.

    The receipt file itself (`<stem>-receipt.json`, `build_receipt.rs`) is
    ocx's; this SDK never reads it directly. Its format version stays behind
    the command, which answers exit 65 for a file it cannot read and exit 79
    for a bundle with no receipt beside it.

    Attributes:
        platform: The `--platform` recorded, canonical grammar, when given.
        identifier: The `--identifier` recorded, resolved against the
            default registry, when given.
    """

    platform: str | None = None
    identifier: str | None = None

    @property
    def ref(self) -> PackageRef | None:
        """The recorded identifier as a `PackageRef`, or `None` when none was recorded."""
        return PackageRef(self.identifier) if self.identifier is not None else None

    @classmethod
    def from_json(cls, raw: str) -> BuildReceipt:
        """Parse `ocx --format json package receipt` output."""
        return cls.from_dict(_object(raw, _RECEIPT))

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> BuildReceipt:
        """Build from the decoded report."""
        return cls(platform=data.get("platform"), identifier=data.get("identifier"))


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


def parse_description_pull(raw: str) -> InfoResult:
    """Parse `ocx --format json package description pull` output.

    Returns:
        Requested identifier to its description metadata, or `None` when the
        registry holds none. Keyed even for a single identifier.
    """
    data = _object(raw, _DESCRIPTION_PULL)
    return MappingProxyType(
        {
            key: PackageDescription.from_dict(entry) if (entry := _opt_table(value)) is not None else None
            for key, value in data.items()
        }
    )


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


def parse_clean(raw: str) -> tuple[CleanEntry, ...]:
    """Parse `ocx --format json clean` output — a bare root array, one row per removal."""
    return tuple(CleanEntry.from_dict(row) for row in _array(raw, _CLEAN))


def parse_pull_dry_run(raw: str) -> tuple[DryRunEntry, ...]:
    """Parse `ocx --format json pull --dry-run` output.

    A separate parser, not a `PullReport` branch: `--dry-run` reports a
    different upstream type at a different root — an array of previews, not
    the keyed object of materialized paths a real pull writes.
    """
    return tuple(DryRunEntry.from_dict(row) for row in _array(raw, "pull"))


def parse_tool_rows(raw: str) -> tuple[ToolRow, ...]:
    """Parse `ocx --format json add` (or `lock`, `update`, `remove`) output.

    Note:
        `lock --check` and `update --check` emit **no body at all** on success.
        They never reach this function — the client layer answers `None` for
        them instead of asking a parser to interpret emptiness.
    """
    return tuple(ToolRow.from_dict(row) for row in _array(raw, "lock"))
