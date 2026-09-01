# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The OCX Authors

"""Contract tests for `ocx_sdk._client` (v0.1 C-011; C-001 to C-004, C-007 to C-015, S-001 to S-003, S-005, S-008).

The seam under test is `_process`: every test fakes `run_command` and friends
and then asserts on what the client *composed* — the argv, the child
environment, and the retry and timeout knobs. That makes this file the
signature-alignment evidence, one row per method against the WP00 `--help`
fixtures.

Named rows from the design's mechanism matrix (§14):
`test_compat_gate_below_min_raises`, `test_compat_gate_newer_debug_note`,
`test_mutating_commands_retry_disabled`, `test_leading_dash_rejected`,
`test_double_dash_emitted`.

No test here starts a real process, and none needs an ocx binary: the `exe`
fixture is an empty file, which is all discovery checks for.
"""

from __future__ import annotations

import io
import logging
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from ocx_sdk import _client, _process
from ocx_sdk._client import UNSET, MaybeRetry, Ocx, Project
from ocx_sdk._config import OcxConfig
from ocx_sdk._errors import AuthError, OcxNotFoundError, OcxProcessError, VersionCompatError
from ocx_sdk._process import Completed
from ocx_sdk._results import (
    AttestationReport,
    CopyReport,
    SbomListingReport,
    SignatureReport,
    SweepReport,
    VerificationReport,
)
from ocx_sdk._types import MIN_SUPPORTED, TESTED_OCX_VERSION, BearerAuth, HostEnv, PackageRef, PathVar, RetryPolicy

_JSON = ["--format", "json", "--color", "never"]
"""Presentation the SDK pins on every typed call that parses stdout."""

_PLAIN = ["--color", "never"]
"""Presentation where stdout belongs to a plain contract or a hosted child."""

_PROJECT = Path("/srv/build/ocx.toml")
# What the handle stores and argv carries: `Ocx.project()` resolves, which
# prepends a drive on Windows — expectations must match that form.
_PROJECT_ARG = str(_PROJECT.resolve())
"""What a `Project` holds: ocx's `--project` names the file, not its directory."""
"""The project every project-tier case targets."""

_ENVELOPE = '{"entries":[],"binaries":[],"entrypoints":[],"integrations":[],"advisories":[]}'
"""The five-array `env` envelope, all keys always present."""

_INSTALLED = '{"a":{"identifier":"a@sha256:x","path":"/p","metadata":{}}}'
"""What `package install` and `package select` return, keyed as given."""

_REMOVALS = '[{"package":"a","status":"removed","path":"/p"}]'
"""The array `package uninstall` and `package deselect` return."""

_TOOL_ROWS = '[{"binding":"task","group":"default","digest":"sha256:d","platforms":{}}]'
"""The lock rows `add`, `remove`, `lock`, and `update` return."""

_TOKEN = "ghp_supersecret"
"""A credential, for the tests that prove it never surfaces."""

_ABOUT = '{"version":"0.5.8","registry":"ocx.sh","home":"/h","shell":"sh","platforms":[],"libc":[]}'
"""The `about` payload — the fake's default, since most behaviour tests use it."""

_RUN = '{"exit_code":0,"stdout":"","stderr":"","duration_ms":1,"truncated":false}'
"""A `RunSummary` with every field ocx always writes (D7)."""

_DRY_RUN = '[{"package":"ocx.sh/task:3@sha256:d","status":"would-fetch","path":null}]'
"""What `pull --dry-run` writes: a bare root array, not `pull`'s keyed object."""

_TEST_PASSED = '{"status":"passed","assertion":null,"run":' + _RUN + "}"
"""The `package test --script` envelope for a run that passed."""

_STATUS = (
    '{"project":"/srv/build","lock":{"present":false,"declaration_hash_expected":"h"},'
    '"groups":{},"package_settings":{}}'
)
"""The `status` payload — every key `status.rs` writes unconditionally."""


def _pushed(identifier):
    """A `package push` payload carrying the six keys `PushResult` requires.

    `cascade_tags_written`, `keep_tags_written` and `layers` have no
    `skip_serializing_if` upstream, so they are always present and read with
    `_need` (D7) — a payload without them is not one ocx can emit.
    """
    return (
        f'{{"identifier":"{identifier}","status":"pushed","manifest_digest":"sha256:x",'
        '"cascade_tags_written":[],"keep_tags_written":[],"layers":{}}'
    )


@dataclass(frozen=True, slots=True)
class _Spawn:
    """One recorded call into the process layer."""

    argv: tuple[str, ...]
    env: dict[str, str]
    kwargs: dict[str, Any]
    is_async: bool = False
    """Whether it arrived through the `_async` driver — what the gate rows read."""

    @property
    def command(self) -> list[str]:
        """Everything the client composed after the binary."""
        return list(self.argv[1:])


@dataclass
class _Process:
    """A stand-in for `_process`, recording what the client hands it.

    A `version` probe always answers `version`, whatever the test asked the
    rest to return, so the compatibility gate never has to be worked around.
    """

    stdout: str = _ABOUT
    stderr: str = ""
    exit_code: int = 0
    version: str = TESTED_OCX_VERSION
    raises: Exception | None = None
    calls: list[_Spawn] = field(default_factory=list[_Spawn])
    popen: object = "popen-handle"
    process: object = "async-handle"

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(_client, "run_command", self.run_command)
        monkeypatch.setattr(_client, "run_command_async", self.run_command_async)
        monkeypatch.setattr(_client, "spawn", self.spawn)
        monkeypatch.setattr(_client, "spawn_async", self.spawn_async)

    def _record(self, argv: Any, env: Any, kwargs: dict[str, Any], *, is_async: bool = False) -> Completed:
        self.calls.append(_Spawn(tuple(argv), dict(env), kwargs, is_async))
        if argv[-1] == "version":
            return Completed(0, f"{self.version}\n", "")
        if self.raises is not None:
            raise self.raises
        return Completed(self.exit_code, self.stdout, self.stderr)

    def run_command(self, argv: Any, env: Any, **kwargs: Any) -> Completed:
        return self._record(argv, env, kwargs)

    async def run_command_async(self, argv: Any, env: Any, **kwargs: Any) -> Completed:
        return self._record(argv, env, kwargs, is_async=True)

    def spawn(self, argv: Any, env: Any, **kwargs: Any) -> object:
        self._record(argv, env, kwargs)
        return self.popen

    async def spawn_async(self, argv: Any, env: Any, **kwargs: Any) -> object:
        self._record(argv, env, kwargs, is_async=True)
        return self.process

    @property
    def last(self) -> _Spawn:
        return self.calls[-1]

    @property
    def probes(self) -> list[_Spawn]:
        return [call for call in self.calls if call.argv[-1] == "version"]


class _ScriptedChild:
    """A `Popen` stand-in handing out one scripted result per spawn.

    Used by the tests that must run through the *real* `_process.run_command`
    rather than the fake below — the redaction widening lives in there.
    """

    def __init__(self, *results: tuple[int, str, str]) -> None:
        self._results = list(results)
        self._exit = 0
        self.pid = 4321
        self.returncode: int | None = None
        self.stdout = io.StringIO()
        self.stderr = io.StringIO()
        self.stdin = io.StringIO()

    def __call__(self, argv: Any, **kwargs: Any) -> Any:
        self._exit, out, err = self._results.pop(0)
        self.stdout, self.stderr, self.stdin = io.StringIO(out), io.StringIO(err), io.StringIO()
        self.returncode = None
        return self

    def wait(self, timeout: float | None = None) -> int:
        self.returncode = self._exit
        return self._exit

    def terminate(self) -> None:
        """Never reached: `wait` always sets a returncode first."""

    def kill(self) -> None:
        """Never reached: `wait` always sets a returncode first."""


@pytest.fixture
def exe(tmp_path: Path) -> Path:
    """A file standing in for the ocx binary — discovery only checks existence."""
    binary = tmp_path / "ocx"
    binary.write_text("")
    return binary


@pytest.fixture
def process(monkeypatch: pytest.MonkeyPatch) -> _Process:
    """The faked process layer, installed over `_client`'s imported names."""
    fake = _Process()
    fake.install(monkeypatch)
    return fake


@pytest.fixture
def ocx(exe: Path, process: _Process) -> Ocx:
    """A handle on the fake binary with a clean, deterministic host env."""
    return Ocx(exe, host_env=HostEnv.clean())


@pytest.fixture
def project(ocx: Ocx) -> Project:
    """A project-tier handle on the same fake binary."""
    return ocx.project(_PROJECT)


# --------------------------------------------------------------------------
# Construction and discovery
# --------------------------------------------------------------------------


def test_construction_pins_the_binary_as_a_realpath(tmp_path: Path, process: _Process) -> None:
    real = tmp_path / "ocx-0.5.8"
    real.write_text("")
    link = tmp_path / "ocx"
    link.symlink_to(real)

    handle = Ocx(link, host_env=HostEnv.clean())

    assert handle.exe == real.resolve()
    assert process.calls == []


def test_construction_discovers_through_the_bootstrap_order(tmp_path: Path) -> None:
    binary = tmp_path / "ocx"
    binary.write_text("")

    handle = Ocx(host_env=HostEnv({"OCX_SDK_EXE": str(binary)}))

    assert handle.exe == binary.resolve()


def test_construction_reports_a_missing_binary_with_the_bootstrap_hint(tmp_path: Path) -> None:
    with pytest.raises(OcxNotFoundError, match=r"bootstrap\.ensure"):
        Ocx(host_env=HostEnv({"OCX_HOME": str(tmp_path)}))


def test_construction_snapshots_the_ambient_environment(
    exe: Path, process: _Process, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CI_TOKEN_HOLDER", "present")

    Ocx(exe).about()

    assert process.last.env["CI_TOKEN_HOLDER"] == "present"


def test_repr_names_the_binary_and_nothing_else(ocx: Ocx, exe: Path) -> None:
    assert repr(ocx) == f"Ocx(exe={str(exe.resolve())!r})"


# --------------------------------------------------------------------------
# The two reserved names
# --------------------------------------------------------------------------


def test_ocx_run_carries_the_only_migration_signal_for_the_rename(ocx: Ocx) -> None:
    """C-001: `ocx.run` is where a 0.1 caller lands after `run` became `exec`.

    A pointed `AttributeError`, not a shim — so the hint has to name the
    rename *and* both replacements, because nothing else in 0.2.0 does.
    """
    with pytest.raises(AttributeError, match=r"renamed to ocx exec") as caught:
        _ = ocx.run  # pyright: ignore[reportAttributeAccessIssue]

    message = str(caught.value)
    assert "ocx run was renamed to ocx exec in 0.6." in message
    assert "ocx.project(path).exec(argv) for the project toolchain" in message
    assert "ocx.package.exec(refs, argv) for installed packages" in message


def test_ocx_exec_points_at_both_tier_handles(ocx: Ocx) -> None:
    """C-001: `exec` exists on two handles now, and the machine tier is neither."""
    with pytest.raises(AttributeError, match=r"exec lives on a tier handle") as caught:
        _ = ocx.exec  # pyright: ignore[reportAttributeAccessIssue]

    message = str(caught.value)
    assert "ocx.project(path).exec(argv) for the project toolchain" in message
    assert "ocx.package.exec(refs, argv) for installed packages" in message


def test_an_ordinary_missing_attribute_stays_ordinary(ocx: Ocx) -> None:
    with pytest.raises(AttributeError, match="nonesuch"):
        _ = ocx.nonesuch  # pyright: ignore[reportAttributeAccessIssue]


@pytest.mark.parametrize(
    ("name", "replacement"),
    [
        pytest.param("run", "project.exec(argv)", id="run"),
        pytest.param("run_async", "project.exec_async(argv)", id="run-async"),
    ],
)
def test_project_run_carries_the_migration_signal_where_callers_land(ocx: Ocx, name: str, replacement: str) -> None:
    """C-001: the v0.1 toolchain runner was `Project.run`, not `Ocx.run`.

    `Ocx.run` was already a trap in v0.1, so the hint there fires on the
    handle nobody used. Without one here a 0.1 call site gets a bare
    `AttributeError` naming no replacement — and C-001 calls this the only
    migration signal the release ships.
    """
    project = ocx.project(_PROJECT)

    with pytest.raises(AttributeError, match=r"renamed to ocx exec") as caught:
        _ = getattr(project, name)

    assert replacement in str(caught.value)


def test_an_ordinary_missing_attribute_on_a_project_stays_ordinary(ocx: Ocx) -> None:
    with pytest.raises(AttributeError, match="nonesuch"):
        _ = ocx.project(_PROJECT).nonesuch  # pyright: ignore[reportAttributeAccessIssue]


# --------------------------------------------------------------------------
# Derivation
# --------------------------------------------------------------------------


def test_with_config_replaces_only_the_named_fields(ocx: Ocx) -> None:
    derived = ocx.with_config(offline=True)

    derived.about()

    assert derived is not ocx
    assert derived.exe == ocx.exe


def test_with_config_never_rediscovers_the_binary(ocx: Ocx, exe: Path) -> None:
    exe.unlink()

    derived = ocx.with_config(frozen=True)

    assert derived.exe == ocx.exe


def test_with_config_shares_the_compat_memo(ocx: Ocx, process: _Process) -> None:
    ocx.about()

    ocx.with_config(offline=True).about()

    assert len(process.probes) == 1


def test_with_config_rejects_a_field_that_does_not_exist(ocx: Ocx) -> None:
    with pytest.raises(TypeError, match="nonesuch"):
        ocx.with_config(nonesuch=1)  # pyright: ignore[reportCallIssue]


def test_handles_expose_the_config_they_spawn_under(ocx: Ocx) -> None:
    # Named `session_config`, not `config`: `ocx.config` is the `ocx config`
    # command group, and command alignment wins over the shorter name.
    derived = ocx.with_config(offline=True, timeout=30.0)

    assert ocx.session_config.offline is False
    assert derived.session_config.offline is True
    assert derived.session_config.timeout == 30.0
    assert derived.project(_PROJECT).session_config is derived.session_config


def test_project_with_config_keeps_the_path(project: Project) -> None:
    derived = project.with_config(offline=True)

    assert derived.path == project.path
    assert derived is not project


def test_namespaces_are_throwaway_wrappers_over_the_same_handle(ocx: Ocx) -> None:
    assert ocx.package is not ocx.package
    assert ocx.package == ocx.package
    assert ocx.config == ocx.config
    assert ocx.patch == ocx.patch


def test_project_resolves_its_path_once(ocx: Ocx) -> None:
    handle = ocx.project(".")

    assert handle.path.is_absolute()


def test_project_resolves_a_directory_to_its_project_file(ocx: Ocx, tmp_path: Path) -> None:
    handle = ocx.project(tmp_path)

    assert handle.path == tmp_path.resolve() / "ocx.toml"


def test_project_takes_an_explicit_project_file_as_given(ocx: Ocx, tmp_path: Path) -> None:
    manifest = tmp_path / "toolchain.toml"
    manifest.write_text("", encoding="utf-8")

    assert ocx.project(manifest).path == manifest.resolve()


def test_project_reads_a_nonexistent_extensionless_path_as_a_directory(
    ocx: Ocx, process: _Process, tmp_path: Path
) -> None:
    # `init()` is called on a directory that does not exist yet, so "is it a
    # directory?" cannot decide on its own. Falling through to "then it is the
    # file" made `project('/srv/build').init()` write `/srv/ocx.toml`, one
    # level above where the caller pointed.
    process.stdout = "{}"
    target = tmp_path / "unborn" / "build"

    handle = ocx.project(target)
    handle.init()

    assert handle.path == target / "ocx.toml"
    assert process.last.kwargs["cwd"] == target


def test_project_reads_a_nonexistent_toml_path_as_the_project_file(ocx: Ocx, tmp_path: Path) -> None:
    manifest = tmp_path / "unborn" / "toolchain.toml"

    assert ocx.project(manifest).path == manifest


# --------------------------------------------------------------------------
# The compatibility gate (§3, §14)
# --------------------------------------------------------------------------


def test_compat_gate_below_min_raises(ocx: Ocx, process: _Process) -> None:
    process.version = "0.4.0"

    with pytest.raises(VersionCompatError, match=r"0\.4\.0"):
        ocx.about()


def test_compat_gate_newer_debug_note(ocx: Ocx, process: _Process, caplog: pytest.LogCaptureFixture) -> None:
    process.version = "99.0.0"

    with caplog.at_level(logging.DEBUG, logger="ocx_sdk"):
        ocx.about()

    assert "99.0.0" in caplog.text


def test_compat_gate_refuses_a_version_it_cannot_read(ocx: Ocx, process: _Process) -> None:
    process.version = "not-a-version"

    with pytest.raises(VersionCompatError, match="not-a-version"):
        ocx.about()


def test_compat_gate_accepts_the_minimum_itself(ocx: Ocx, process: _Process) -> None:
    process.version = MIN_SUPPORTED

    ocx.about()

    assert len(process.probes) == 1


def test_compat_gate_probes_once_per_handle_family(ocx: Ocx, process: _Process) -> None:
    ocx.about()
    ocx.about()
    ocx.with_config(offline=True).about()

    assert len(process.probes) == 1


def test_version_is_the_probe_and_never_gates_itself(ocx: Ocx, process: _Process) -> None:
    process.version = "0.4.0"

    assert ocx.version() == "0.4.0"
    assert len(process.calls) == 1


def test_raw_invoke_is_never_gated(ocx: Ocx, process: _Process) -> None:
    ocx.invoke(["index", "list"])

    assert process.probes == []


async def test_the_async_gate_reads_the_same_memo(ocx: Ocx, process: _Process) -> None:
    ocx.about()

    await ocx.project(_PROJECT).exec_async(["true"])

    assert len(process.probes) == 1


async def test_the_async_gate_probes_through_the_async_driver(ocx: Ocx, process: _Process) -> None:
    # An unprimed memo means the first async call does the probing, and it has
    # to do it on the loop: a blocking `ocx version` here would stall every
    # other task while the binary starts.
    await ocx.project(_PROJECT).exec_async(["true"])

    assert [call.is_async for call in process.probes] == [True]


async def test_compat_gate_probes_without_blocking_the_loop(ocx: Ocx, process: _Process) -> None:
    process.version = "0.4.0"

    with pytest.raises(VersionCompatError, match=r"0\.4\.0"):
        await ocx.project(_PROJECT).exec_async(["true"])


def test_a_0_5_binary_is_rejected_before_the_renamed_argv_composes(ocx: Ocx, process: _Process) -> None:
    """S-001, C-007: the gate answers, never a bare exit 64 from `ocx exec`.

    `ocx exec` does not exist below 0.6, so a caller on 0.5.8 would otherwise
    read an unknown-subcommand usage error instead of a version mismatch. The
    probe is the only argv that may reach the binary.
    """
    process.version = "0.5.8"

    with pytest.raises(VersionCompatError, match=r"0\.5\.8") as caught:
        ocx.project(_PROJECT).exec(["true"])

    # As a pair: asserting each half separately cannot tell the message apart
    # from its inverse, "ocx 0.6.0 is older than the minimum supported 0.5.8".
    assert (caught.value.found, caught.value.minimum) == ("0.5.8", MIN_SUPPORTED)
    assert [call.command[-1] for call in process.calls] == ["version"]


async def test_a_0_5_binary_is_rejected_before_the_renamed_async_argv_composes(ocx: Ocx, process: _Process) -> None:
    """S-001, C-007: the async gate refuses the same binary on the same evidence."""
    process.version = "0.5.8"

    with pytest.raises(VersionCompatError, match=r"0\.5\.8") as caught:
        await ocx.project(_PROJECT).exec_async(["true"])

    assert (caught.value.found, caught.value.minimum) == ("0.5.8", MIN_SUPPORTED)
    assert [call.command[-1] for call in process.calls] == ["version"]


# --------------------------------------------------------------------------
# Global flags: what reaches argv, and what reaches the environment
# --------------------------------------------------------------------------


def test_global_flags_come_before_the_subcommand(ocx: Ocx, process: _Process) -> None:
    ocx.about()

    assert process.last.command == [*_JSON, "about"]


def test_version_reads_the_plain_contract(ocx: Ocx, process: _Process) -> None:
    ocx.version()

    assert process.last.command == [*_PLAIN, "version"]


def test_invoke_pins_no_presentation_at_all(ocx: Ocx, process: _Process) -> None:
    ocx.invoke(["index", "list"])

    assert process.last.command == ["index", "list"]


def test_child_hosting_verbs_keep_stdout_for_the_child(project: Project, process: _Process) -> None:
    """C-001, S-002: the project tier hosts a child under `ocx exec`, not `ocx run`."""
    project.exec(["printenv"])

    assert process.last.command == [*_PLAIN, "--project", _PROJECT_ARG, "exec", "--", "printenv"]


def test_log_level_is_the_one_config_knob_that_reaches_argv(exe: Path, process: _Process) -> None:
    Ocx(exe, config=OcxConfig(log_level="debug"), host_env=HostEnv.clean()).about()

    assert process.last.command == [*_JSON, "--log-level", "debug", "about"]


def test_config_knobs_travel_as_environment_not_argv(exe: Path, process: _Process) -> None:
    config = OcxConfig(offline=True, frozen=True, jobs=4, home=Path("/opt/ocx"))

    Ocx(exe, config=config, host_env=HostEnv.clean()).about()

    assert process.last.command == [*_JSON, "about"]
    assert process.last.env["OCX_OFFLINE"] == "1"
    assert process.last.env["OCX_FROZEN"] == "1"
    assert process.last.env["OCX_JOBS"] == "4"
    assert process.last.env["OCX_HOME"] == str(Path("/opt/ocx"))


def test_ambient_project_targeting_is_neutralized(exe: Path, process: _Process) -> None:
    Ocx(exe, host_env=HostEnv({"OCX_PROJECT": "/elsewhere", "OCX_QUIET": "1"})).about()

    assert "OCX_PROJECT" not in process.last.env
    assert "OCX_QUIET" not in process.last.env


def test_every_project_call_carries_the_project_flag(project: Project, process: _Process) -> None:
    process.stdout = _STATUS

    project.status()

    assert process.last.command == [*_JSON, "--project", _PROJECT_ARG, "status"]


def test_package_tier_takes_no_project_path(ocx: Ocx, process: _Process) -> None:
    process.stdout = _INSTALLED

    ocx.package.install("a")

    assert "--project" not in process.last.command


# --------------------------------------------------------------------------
# Signature alignment: machine tier
# --------------------------------------------------------------------------

_MACHINE_CASES = [
    pytest.param(
        lambda o: o.about(),
        _ABOUT,
        ["about"],
        id="about",
    ),
    pytest.param(
        lambda o: o.login("ghcr.io", username="ci", token="hunter2"),
        '{"registry":"ghcr.io","username":"ci"}',
        ["login", "--username", "ci", "--password-stdin", "ghcr.io"],
        id="login",
    ),
    pytest.param(
        lambda o: o.login(username="ci", token="hunter2", allow_insecure_store=True, verify=False),
        '{"registry":"ocx.sh","username":"ci"}',
        ["login", "--username", "ci", "--password-stdin", "--allow-insecure-store", "--no-verify"],
        id="login-flags",
    ),
    pytest.param(
        lambda o: o.logout("ghcr.io"),
        '{"registry":"ghcr.io"}',
        ["logout", "ghcr.io"],
        id="logout",
    ),
    pytest.param(lambda o: o.logout(), '{"registry":"ocx.sh"}', ["logout"], id="logout-default-registry"),
    pytest.param(
        lambda o: o.package.install("a", "b"),
        _INSTALLED,
        ["package", "install", "a", "b"],
        id="package-install",
    ),
    pytest.param(
        lambda o: o.package.install("a", platform="linux/amd64", select=True),
        _INSTALLED,
        ["package", "install", "--select", "--platform", "linux/amd64", "a"],
        id="package-install-flags",
    ),
    pytest.param(lambda o: o.package.select("a"), _INSTALLED, ["package", "select", "a"], id="package-select"),
    pytest.param(
        lambda o: o.package.uninstall("a", deselect=True, purge=True),
        _REMOVALS,
        ["package", "uninstall", "--deselect", "--purge", "a"],
        id="package-uninstall",
    ),
    pytest.param(lambda o: o.package.deselect("a"), _REMOVALS, ["package", "deselect", "a"], id="package-deselect"),
    pytest.param(lambda o: o.package.env("a"), _ENVELOPE, ["package", "env", "a"], id="package-env"),
    pytest.param(
        lambda o: o.package.env("a", resolve="candidate", private=True, show_patches=True, lazy_mode="always"),
        _ENVELOPE,
        ["package", "env", "--candidate", "--self", "--lazy-mode", "always", "--show-patches", "a"],
        id="package-env-flags",
    ),
    pytest.param(
        lambda o: o.package.which("a", resolve="current"),
        '{"a":{"path":"/p","kind":"package"}}',
        ["package", "which", "--current", "a"],
        id="package-which",
    ),
    pytest.param(
        lambda o: o.package.which("a"),
        '{"a":{"path":"/p","kind":"package"}}',
        ["package", "which", "a"],
        id="package-which-unresolved",
    ),
    pytest.param(
        lambda o: o.package.inspect("a", resolve=True, closure=True),
        '{"packages":[],"env":[]}',
        ["package", "inspect", "--resolve", "--closure", "a"],
        id="package-inspect",
    ),
    pytest.param(
        lambda o: o.package.description_pull("a"),
        '{"a":null}',
        ["package", "description", "pull", "a"],
        id="package-description-pull",
    ),
    pytest.param(lambda o: o.package.deps("a"), '{"roots":[]}', ["package", "deps", "a"], id="package-deps"),
    pytest.param(
        lambda o: o.package.deps("a", private=True, why="b", depth=2, platform="linux/amd64"),
        '{"roots":[]}',
        ["package", "deps", "--self", "--why", "b", "--depth", "2", "--platform", "linux/amd64", "a"],
        id="package-deps-flags",
    ),
    pytest.param(
        lambda o: o.package.description_pull("a", save_readme="/r.md", save_logo="/l.png"),
        '{"a":null}',
        ["package", "description", "pull", "--save-readme", "/r.md", "--save-logo", "/l.png", "a"],
        id="package-description-pull-save",
    ),
    pytest.param(lambda o: o.package.pull("a"), '{"a":"/p"}', ["package", "pull", "a"], id="package-pull"),
    pytest.param(
        lambda o: o.package.create("/src/pkg", identifier="repo:1.0.0", platform="linux/amd64"),
        "",
        ["package", "create", "--identifier", "repo:1.0.0", "--platform", "linux/amd64", "/src/pkg"],
        id="package-create",
    ),
    pytest.param(
        lambda o: o.package.create("/src/pkg", output="/out", metadata="/m.json", force=True),
        "",
        ["package", "create", "--output", "/out", "--metadata", "/m.json", "--force", "/src/pkg"],
        id="package-create-flags",
    ),
    pytest.param(
        lambda o: o.package.create("/src/pkg", compression_level="best", threads=4, bin_scan=True, libc_lint=False),
        "",
        [
            "package",
            "create",
            "--compression-level",
            "best",
            "--threads",
            "4",
            "--bin-scan",
            "--no-libc-lint",
            "/src/pkg",
        ],
        id="package-create-build-flags",
    ),
    pytest.param(
        lambda o: o.package.create("/src/pkg", bin_scan=False),
        "",
        ["package", "create", "--no-bin-scan", "/src/pkg"],
        id="package-create-no-bin-scan",
    ),
    pytest.param(
        lambda o: o.package.test("repo:1.0.0", script="/t.star"),
        _TEST_PASSED,
        ["package", "test", "--identifier", "repo:1.0.0", "--script", "/t.star"],
        id="package-test",
    ),
    pytest.param(
        lambda o: o.package.test("repo:1.0.0", script="-", layers=["a.tar.gz"], keep=True, clean=True, private=True),
        _TEST_PASSED,
        [
            "package",
            "test",
            "--identifier",
            "repo:1.0.0",
            "--script",
            "-",
            "--keep",
            "--clean",
            "--self",
            "a.tar.gz",
        ],
        id="package-test-flags",
    ),
    pytest.param(
        lambda o: o.package.test("repo:1.0.0", script="/t.star", output="/out"),
        _TEST_PASSED,
        ["package", "test", "--identifier", "repo:1.0.0", "--script", "/t.star", "--output", "/out"],
        id="package-test-output",
    ),
    pytest.param(
        lambda o: o.package.push("a.tar.gz", identifier="repo:1.0.0"),
        _pushed("repo:1.0.0"),
        ["package", "push", "--identifier", "repo:1.0.0", "a.tar.gz"],
        id="package-push",
    ),
    pytest.param(
        lambda o: o.package.push("a.tar.gz", identifier="repo:1.0.0", keep_tag=True),
        _pushed("repo:1.0.0"),
        ["package", "push", "--identifier", "repo:1.0.0", "--keep-tag", "a.tar.gz"],
        id="package-push-keep-tag",
    ),
    pytest.param(
        lambda o: o.package.push(
            cascade=True,
            keep_tag=False,
            metadata="/m.json",
            annotations={"org.opencontainers.image.source": "https://example.test"},
        ),
        _pushed("repo:1.0.0"),
        [
            "package",
            "push",
            "--cascade",
            "--no-keep-tag",
            "--metadata",
            "/m.json",
            "--annotation",
            "org.opencontainers.image.source=https://example.test",
        ],
        id="package-push-flags",
    ),
    pytest.param(
        lambda o: o.package.push("a.tar.gz", identifier="r:1", build_timestamp="date", tags_file="/tmp/tags"),
        _pushed("r:1"),
        [
            "package",
            "push",
            "--identifier",
            "r:1",
            "--build-timestamp=date",
            "--tags-file",
            "/tmp/tags",
            "a.tar.gz",
        ],
        id="package-push-cd-flags",
    ),
    pytest.param(
        lambda o: o.package.push("a.tar.gz", identifier="r:1", sign=True, sbom="/sbom.json"),
        _pushed("r:1"),
        ["package", "push", "--identifier", "r:1", "--sign", "--sbom", "/sbom.json", "a.tar.gz"],
        id="package-push-sign-inline",
    ),
    pytest.param(
        lambda o: o.package.push(
            "a.tar.gz",
            identifier="r:1",
            sign=True,
            key="file://k.pem",
            signature_format="simplesigning",
            rekor_upload=False,
        ),
        _pushed("r:1"),
        [
            "package",
            "push",
            "--identifier",
            "r:1",
            "--sign",
            "--signature-format",
            "simplesigning",
            "--key",
            "file://k.pem",
            "--no-rekor-upload",
            "a.tar.gz",
        ],
        id="package-push-sign-with-key",
    ),
    pytest.param(
        lambda o: o.package.install("a", verify=True),
        _INSTALLED,
        ["package", "install", "--verify", "a"],
        id="package-install-verify",
    ),
    pytest.param(
        lambda o: o.package.install("a", verify=False),
        _INSTALLED,
        ["package", "install", "--no-verify", "a"],
        id="package-install-no-verify",
    ),
    pytest.param(
        lambda o: o.package.pull("a", verify=True),
        '{"a":"/p"}',
        ["package", "pull", "--verify", "a"],
        id="package-pull-verify",
    ),
    pytest.param(
        lambda o: o.package.push("a.tar.gz", identifier="r:1", sbom="/sbom.json", key="file://k.pem"),
        _pushed("r:1"),
        ["package", "push", "--identifier", "r:1", "--key", "file://k.pem", "--sbom", "/sbom.json", "a.tar.gz"],
        id="package-push-sbom-admits-a-modifier",
    ),
    pytest.param(
        lambda o: o.config.setup(managed_config="ocx.sh/corp/config:1"),
        '{"managed_config":{"status":"completed"}}',
        ["config", "setup", "--managed-config", "ocx.sh/corp/config:1"],
        id="config-setup",
    ),
    pytest.param(
        lambda o: o.config.setup(dry_run=True, force=True),
        '{"managed_config":{"status":"no_op"}}',
        ["config", "setup", "--dry-run", "--force"],
        id="config-setup-flags",
    ),
    pytest.param(
        lambda o: o.config.update("1.2.0", pause="4h"),
        '{"status":"updated"}',
        ["config", "update", "--pause", "4h", "1.2.0"],
        id="config-update",
    ),
    pytest.param(
        lambda o: o.config.update(check_only=True, resume=True),
        '{"status":"checked"}',
        ["config", "update", "--check", "--resume"],
        id="config-update-flags",
    ),
    pytest.param(
        lambda o: o.patch.sync(platform="linux/amd64"),
        "{}",
        ["patch", "sync", "--platform", "linux/amd64"],
        id="patch-sync",
    ),
]


@pytest.mark.parametrize(("call", "stdout", "expected"), _MACHINE_CASES)
def test_machine_tier_argv(ocx: Ocx, process: _Process, call: Any, stdout: str, expected: list[str]) -> None:
    process.stdout = stdout

    call(ocx)

    assert process.last.command == [*_JSON, *expected]


# --------------------------------------------------------------------------
# Signature alignment: project tier
# --------------------------------------------------------------------------

_PROJECT_CASES = [
    # `init` is deliberately absent: it is the one project-tier call that
    # carries no `--project`. Its argv and cwd are asserted on their own, in
    # test_init_runs_in_the_project_directory_without_the_project_flag.
    pytest.param(lambda p: p.status(), _STATUS, ["status"], id="status"),
    pytest.param(lambda p: p.add("ocx.sh/task:3"), _TOOL_ROWS, ["add", "ocx.sh/task:3"], id="add"),
    pytest.param(
        lambda p: p.add("ocx.sh/task:3", group="ci", pull=True, platform="linux/amd64"),
        _TOOL_ROWS,
        ["add", "--group", "ci", "--pull", "--platform", "linux/amd64", "ocx.sh/task:3"],
        id="add-flags",
    ),
    pytest.param(lambda p: p.remove("task", group="ci"), "[]", ["remove", "--group", "ci", "task"], id="remove"),
    pytest.param(lambda p: p.lock(), _TOOL_ROWS, ["lock"], id="lock"),
    pytest.param(lambda p: p.lock(pull=False), _TOOL_ROWS, ["lock", "--no-pull"], id="lock-no-pull"),
    pytest.param(lambda p: p.update("task"), _TOOL_ROWS, ["update", "task"], id="update"),
    pytest.param(
        lambda p: p.update(groups=["ci", "lint"]),
        _TOOL_ROWS,
        ["update", "--group", "ci", "--group", "lint"],
        id="update-groups",
    ),
    pytest.param(
        lambda p: p.pull(),
        '{"ocx.sh/task:3":{"path":"/p","kind":"package"},"advisories":[]}',
        ["pull"],
        id="pull",
    ),
    # `_DRY_RUN` is a root array because that is what ocx writes. This row used
    # to carry a hand-written `{"advisories":[]}`, which exercised the flag and
    # the argv while agreeing with the wrong parser — hiding a crash on every
    # real preview. A hand-written payload is evidence about what its author
    # believed, never about the wire.
    pytest.param(
        lambda p: p.pull(dry_run=True, groups=["ci"], lazy_mode="never"),
        _DRY_RUN,
        ["pull", "--dry-run", "--group", "ci", "--lazy-mode", "never"],
        id="pull-flags",
    ),
    pytest.param(
        lambda p: p.inspect("task", resolve=True),
        '{"packages":[],"env":[]}',
        ["inspect", "--resolve", "task"],
        id="inspect",
    ),
    pytest.param(lambda p: p.env(), _ENVELOPE, ["env"], id="env"),
    pytest.param(
        lambda p: p.env(groups=["ci"], pull=False, show_patches=True),
        _ENVELOPE,
        ["env", "--group", "ci", "--no-pull", "--show-patches"],
        id="env-flags",
    ),
]


@pytest.mark.parametrize(("call", "stdout", "expected"), _PROJECT_CASES)
def test_project_tier_argv(project: Project, process: _Process, call: Any, stdout: str, expected: list[str]) -> None:
    process.stdout = stdout

    call(project)

    assert process.last.command == [*_JSON, "--project", _PROJECT_ARG, *expected]


def test_init_runs_in_the_project_directory_without_the_project_flag(
    project: Project,
    process: _Process,
) -> None:
    process.stdout = "{}"

    project.init()

    assert process.last.command == [*_JSON, "init"]
    assert process.last.kwargs["cwd"] == project.path.parent


# --------------------------------------------------------------------------
# Flag rendering details
# --------------------------------------------------------------------------


def test_env_entries_render_as_repeated_env_flags(project: Project, process: _Process) -> None:
    process.stdout = _ENVELOPE

    project.env(env={"CC": "clang", "PATH": PathVar("bin")})

    assert process.last.command[-4:] == ["--env", "CC=clang", "--env", "PATH:path=bin"]


def test_package_references_are_carried_not_parsed(ocx: Ocx, process: _Process) -> None:
    process.stdout = _INSTALLED
    ref = PackageRef("ocx.sh/go-task/task@sha256:abc")

    ocx.package.install(ref)

    assert process.last.command[-1] == "ocx.sh/go-task/task@sha256:abc"


def test_unset_toggles_emit_nothing(project: Project, process: _Process) -> None:
    process.stdout = _TOOL_ROWS

    project.lock()

    assert "--pull" not in process.last.command
    assert "--no-pull" not in process.last.command


def test_managed_config_disabled_is_an_explicit_empty_value(ocx: Ocx, process: _Process) -> None:
    process.stdout = '{"managed_config":{"status":"completed"}}'

    ocx.config.setup(managed_config="")

    assert process.last.command[-2:] == ["--managed-config", ""]


# --------------------------------------------------------------------------
# C-004: the 0.6 push flag surface
# --------------------------------------------------------------------------


def test_push_no_longer_accepts_the_dropped_new_flag(ocx: Ocx, process: _Process) -> None:
    """C-004: `--new` is gone from ocx 0.6, and 0.2.0 ships no accept-and-ignore shim."""
    with pytest.raises(TypeError, match=r"unexpected keyword argument 'new'"):
        ocx.package.push("a.tar.gz", identifier="repo:1.0.0", new=True)  # pyright: ignore[reportCallIssue]


@pytest.mark.parametrize(
    "dropped",
    [
        pytest.param("canonical_tag", id="canonical-tag-became-keep-tag"),
        pytest.param("announce_file", id="announce-file-became-tags-file"),
    ],
)
def test_push_refuses_the_0_5_spelling_of_a_renamed_parameter(ocx: Ocx, process: _Process, dropped: str) -> None:
    """C-004: renamed, not aliased — a 0.1 call site must be edited, not silently accepted.

    No push payload is staged: the `TypeError` fires when Python binds the
    call, before any body runs, so nothing here ever reaches the process
    layer or a parser.
    """
    removed: dict[str, Any] = {dropped: True}
    with pytest.raises(TypeError, match=rf"unexpected keyword argument '{dropped}'"):
        ocx.package.push("a.tar.gz", identifier="repo:1.0.0", **removed)


# --------------------------------------------------------------------------
# Argv safety (§14)
# --------------------------------------------------------------------------


def test_leading_dash_rejected(ocx: Ocx) -> None:
    with pytest.raises(ValueError, match="may not start with '-'"):
        ocx.package.install("-rf")


def test_double_dash_emitted(project: Project, process: _Process) -> None:
    project.exec(["ls", "-la"])

    assert process.last.command[-3:] == ["--", "ls", "-la"]


def test_package_exec_emits_two_positional_groups(ocx: Ocx, process: _Process) -> None:
    ocx.package.exec(["a", "b"], ["ls", "-la"])

    assert process.last.command == [*_PLAIN, "package", "exec", "a", "b", "--", "ls", "-la"]


def test_package_exec_guards_the_package_group(ocx: Ocx) -> None:
    with pytest.raises(ValueError, match="may not start with '-'"):
        ocx.package.exec(["-rf"], ["ls"])


@pytest.mark.parametrize(
    "call",
    [
        pytest.param(lambda p: p.exec([]), id="exec"),
        pytest.param(lambda p: p.spawn([]), id="spawn"),
    ],
)
def test_child_hosting_verbs_refuse_an_empty_argv(project: Project, call: Any) -> None:
    with pytest.raises(ValueError, match="needs a command"):
        call(project)


async def test_exec_async_refuses_an_empty_argv(project: Project) -> None:
    with pytest.raises(ValueError, match="needs a command"):
        await project.exec_async([])


async def test_spawn_async_refuses_an_empty_argv(project: Project) -> None:
    with pytest.raises(ValueError, match="needs a command"):
        await project.spawn_async([])


def test_package_exec_refuses_an_empty_argv(ocx: Ocx) -> None:
    with pytest.raises(ValueError, match="needs a command"):
        ocx.package.exec(["a"], [])


# --------------------------------------------------------------------------
# Retry and timeout tiers (§10, §14)
# --------------------------------------------------------------------------


def test_typed_calls_take_the_session_retry_policy(exe: Path, process: _Process) -> None:
    policy = RetryPolicy(attempts=5)

    Ocx(exe, config=OcxConfig(retry=policy, timeout=30.0), host_env=HostEnv.clean()).about()

    assert process.last.kwargs["retry"] is policy
    assert process.last.kwargs["timeout"] == 30.0


def test_a_per_call_policy_beats_the_session(exe: Path, process: _Process) -> None:
    per_call = RetryPolicy(attempts=9)

    Ocx(exe, config=OcxConfig(retry=RetryPolicy()), host_env=HostEnv.clean()).about(retry=per_call, timeout=1.5)

    assert process.last.kwargs["retry"] is per_call
    assert process.last.kwargs["timeout"] == 1.5


def test_an_explicit_none_opts_out_of_retries(exe: Path, process: _Process) -> None:
    Ocx(exe, config=OcxConfig(retry=RetryPolicy()), host_env=HostEnv.clean()).about(retry=None, timeout=None)

    assert process.last.kwargs["retry"] is None
    assert process.last.kwargs["timeout"] is None


@pytest.mark.parametrize(
    ("call", "stdout"),
    [
        pytest.param(
            lambda o: o.login("ghcr.io", username="ci", token="t"),
            '{"registry":"ghcr.io","username":"ci"}',
            id="login",
        ),
        pytest.param(
            lambda o: o.package.push("a.tar.gz", identifier="repo:1"),
            _pushed("repo:1"),
            id="package-push",
        ),
    ],
)
def test_mutating_commands_retry_disabled(exe: Path, process: _Process, call: Any, stdout: str) -> None:
    process.stdout = stdout

    call(Ocx(exe, config=OcxConfig(retry=RetryPolicy()), host_env=HostEnv.clean()))

    assert process.last.kwargs["retry"] is None


def test_a_mutating_command_still_honors_an_explicit_policy(exe: Path, process: _Process) -> None:
    policy = RetryPolicy(attempts=2)
    process.stdout = '{"registry":"ghcr.io","username":"ci"}'
    handle = Ocx(exe, host_env=HostEnv.clean())

    handle.login("ghcr.io", username="ci", token="t", retry=policy)

    assert process.last.kwargs["retry"] is policy


def test_child_processes_are_never_retried(exe: Path, process: _Process) -> None:
    handle = Ocx(exe, config=OcxConfig(retry=RetryPolicy()), host_env=HostEnv.clean())

    handle.project(_PROJECT).exec(["true"])

    assert process.last.kwargs["retry"] is None


def test_the_compat_probe_uses_the_session_defaults(exe: Path, process: _Process) -> None:
    policy = RetryPolicy(attempts=4)

    Ocx(exe, config=OcxConfig(retry=policy, timeout=7.0), host_env=HostEnv.clean()).about()

    assert process.probes[0].kwargs["retry"] is policy
    assert process.probes[0].kwargs["timeout"] == 7.0


# --------------------------------------------------------------------------
# Process-layer wiring
# --------------------------------------------------------------------------


def test_the_composed_environment_and_redactor_reach_the_process_layer(exe: Path, process: _Process) -> None:
    config = OcxConfig(auth={"ghcr.io": BearerAuth("ghp_secret")})

    Ocx(exe, config=config, host_env=HostEnv.clean()).about()

    assert process.last.env["OCX_AUTH_ghcr_io_TOKEN"] == "ghp_secret"
    assert process.last.kwargs["redact"]("saw ghp_secret") == "saw ***"


def test_a_secret_in_argv_is_redacted_off_the_result(exe: Path, process: _Process) -> None:
    config = OcxConfig(auth={"ghcr.io": BearerAuth("ghp_secret")})
    handle = Ocx(exe, config=config, host_env=HostEnv.clean())

    result = handle.invoke(["package", "info", "ghp_secret"])

    assert "ghp_secret" not in " ".join(result.argv)
    assert result.argv[-1] == "***"


def test_login_writes_the_token_to_stdin_never_to_argv(ocx: Ocx, process: _Process) -> None:
    process.stdout = '{"registry":"ghcr.io","username":"ci"}'

    ocx.login("ghcr.io", username="ci", token="hunter2")

    assert process.last.kwargs["input"] == "hunter2"
    assert "hunter2" not in process.last.argv


def test_the_log_callback_reaches_the_pump(exe: Path, process: _Process) -> None:
    lines: list[str] = []
    callback = lines.append

    Ocx(exe, host_env=HostEnv.clean(), on_log=callback).about()

    assert process.last.kwargs["on_log"] is callback


def test_invoke_returns_the_raw_result(ocx: Ocx, process: _Process) -> None:
    process.stdout = "payload"
    process.stderr = "log line"

    result = ocx.invoke(["index", "list"])

    assert result.stdout == "payload"
    assert result.stderr == "log line"
    assert result.exit_code == 0
    assert result.argv[0] == str(ocx.exe)


def test_a_failing_child_surfaces_its_exit_code(project: Project, process: _Process) -> None:
    process.exit_code = 2

    result = project.exec(["false"], check=False)

    assert result.exit_code == 2
    assert process.last.kwargs["check"] is False


def test_a_failing_typed_call_raises(ocx: Ocx, process: _Process) -> None:
    process.raises = OcxProcessError(79, ["ocx", "about"], "not found")

    with pytest.raises(OcxProcessError, match="79"):
        ocx.about()


def test_capture_false_passes_through(project: Project, process: _Process) -> None:
    project.exec(["make"], capture=False)

    assert process.last.kwargs["capture"] is False


# --------------------------------------------------------------------------
# Empty-body outcomes (WP00 addendum)
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "call",
    [
        pytest.param(lambda p: p.lock(check_only=True), id="lock"),
        pytest.param(lambda p: p.update(check_only=True), id="update"),
    ],
)
def test_a_current_lock_reports_nothing(project: Project, process: _Process, call: Any) -> None:
    process.stdout = ""

    assert call(project) is None


def test_check_only_reaches_argv_as_the_cli_check_flag(project: Project, process: _Process) -> None:
    # Renamed away from `check=`, which on `run` means the subprocess sense of
    # "raise on a non-zero exit". The wire flag is still ocx's `--check`.
    process.stdout = ""

    project.lock(check_only=True)

    assert process.last.command[-2:] == ["lock", "--check"]


def test_a_written_lock_still_reports_its_rows(project: Project, process: _Process) -> None:
    process.stdout = _TOOL_ROWS

    rows = project.lock()

    assert rows is not None
    assert rows[0].binding == "task"


# --------------------------------------------------------------------------
# Live handles
# --------------------------------------------------------------------------


def test_spawn_returns_the_stdlib_handle(ocx: Ocx, process: _Process) -> None:
    handle = ocx.spawn(["index", "list"], stdout=subprocess.PIPE)

    assert handle is process.popen
    assert process.last.kwargs["stdout"] is subprocess.PIPE
    assert process.last.command == ["index", "list"]


def test_project_spawn_carries_the_project_flag(project: Project, process: _Process) -> None:
    project.spawn(["watch"])

    assert process.last.command == [*_PLAIN, "--project", _PROJECT_ARG, "exec", "--", "watch"]


def test_package_spawn_composes_both_groups(ocx: Ocx, process: _Process) -> None:
    ocx.package.spawn(["a"], ["serve"])

    assert process.last.command == [*_PLAIN, "package", "exec", "a", "--", "serve"]


async def test_spawn_async_returns_the_asyncio_handle(ocx: Ocx, process: _Process) -> None:
    handle = await ocx.spawn_async(["index", "list"])

    assert handle is process.process


async def test_project_spawn_async_carries_the_project_flag(project: Project, process: _Process) -> None:
    await project.spawn_async(["watch"])

    assert process.last.command[:3] == [*_PLAIN, "--project"]


async def test_package_spawn_async_composes_both_groups(ocx: Ocx, process: _Process) -> None:
    await ocx.package.spawn_async(["a"], ["serve"])

    assert process.last.command[-4:] == ["exec", "a", "--", "serve"]


# --------------------------------------------------------------------------
# Async twins
# --------------------------------------------------------------------------


async def test_invoke_async_matches_its_sync_twin(ocx: Ocx, process: _Process) -> None:
    process.stdout = "payload"

    result = await ocx.invoke_async(["index", "list"])

    assert result.stdout == "payload"
    assert process.last.command == ["index", "list"]


async def test_exec_async_hosts_the_child(project: Project, process: _Process) -> None:
    await project.exec_async(["pytest"], capture=False)

    assert process.last.command[-2:] == ["--", "pytest"]
    assert process.last.kwargs["capture"] is False


async def test_package_exec_async_hosts_the_child(ocx: Ocx, process: _Process) -> None:
    await ocx.package.exec_async(["a"], ["ls"])

    assert process.last.command[-4:] == ["exec", "a", "--", "ls"]


# --------------------------------------------------------------------------
# EnvReport composition (§8)
# --------------------------------------------------------------------------


def test_env_reports_compose_against_the_producing_host_snapshot(exe: Path, process: _Process) -> None:
    process.stdout = (
        '{"entries":[{"key":"CC","type":"constant","value":"clang"}],'
        '"binaries":[],"entrypoints":[],"integrations":[],"advisories":[]}'
    )
    handle = Ocx(exe, host_env=HostEnv({"HOME": "/home/ci"}))

    composed = handle.project(_PROJECT).env().compose().mapping

    assert composed["CC"] == "clang"
    assert composed["HOME"] == "/home/ci"


def test_a_clean_handle_composes_hermetically(exe: Path, process: _Process, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LEAKY", "ambient")
    process.stdout = _ENVELOPE

    composed = Ocx(exe, host_env=HostEnv.clean()).package.env("a").compose().mapping

    assert "LEAKY" not in composed


# --------------------------------------------------------------------------
# v0.1 S-001: the CI quickstart journey
# --------------------------------------------------------------------------


def test_ci_quickstart_journey(exe: Path, process: _Process) -> None:
    handle = Ocx(exe, host_env=HostEnv.clean())
    project = handle.project(_PROJECT)

    process.stdout = _INSTALLED
    installed = handle.package.install("ocx.sh/go-task/task:3")
    process.stdout = _TOOL_ROWS
    project.lock()
    process.stdout = "{}"
    verify = project.exec(["task", "verify"])

    assert installed.packages["a"].path == "/p"
    assert verify.exit_code == 0
    assert [call.command[-1] for call in process.calls] == [
        "version",
        "ocx.sh/go-task/task:3",
        "lock",
        "verify",
    ]


# --------------------------------------------------------------------------
# Secret hygiene, end to end through the real process layer
# --------------------------------------------------------------------------


def test_login_token_is_scrubbed_from_the_error_and_the_logs(
    exe: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    child = _ScriptedChild((0, f"{TESTED_OCX_VERSION}\n", ""), (80, "", f"denied for token {_TOKEN}\n"))
    monkeypatch.setattr(
        _client,
        "run_command",
        lambda argv, env, **kwargs: _process.run_command(argv, env, popen_factory=child, **kwargs),
    )
    handle = Ocx(exe, host_env=HostEnv.clean())

    with caplog.at_level(logging.DEBUG), pytest.raises(AuthError) as caught:
        handle.login("ghcr.io", username="ci", token=_TOKEN)

    assert "***" in caught.value.stderr
    assert _TOKEN not in caught.value.stderr
    assert _TOKEN not in str(caught.value)
    assert _TOKEN not in caplog.text


# --------------------------------------------------------------------------
# Caller errors are not masked by the compatibility gate
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "call",
    [
        pytest.param(lambda o: o.project(_PROJECT).exec([]), id="project-exec"),
        pytest.param(lambda o: o.project(_PROJECT).spawn([]), id="project-spawn"),
        pytest.param(lambda o: o.package.exec(["a"], []), id="package-exec"),
        pytest.param(lambda o: o.package.spawn(["a"], []), id="package-spawn"),
    ],
)
def test_an_empty_argv_is_refused_before_the_binary_is_probed(ocx: Ocx, process: _Process, call: Any) -> None:
    process.version = "0.4.0"

    with pytest.raises(ValueError, match="needs a command"):
        call(ocx)

    assert process.calls == []


async def test_an_empty_argv_is_refused_before_the_async_probe(ocx: Ocx, process: _Process) -> None:
    process.version = "0.4.0"

    with pytest.raises(ValueError, match="needs a command"):
        await ocx.project(_PROJECT).exec_async([])

    assert process.calls == []


def test_leading_dash_rejected_on_a_project_positional(project: Project) -> None:
    with pytest.raises(ValueError, match="may not start with '-'"):
        project.add("-rf")


# --------------------------------------------------------------------------
# Full-flag argv for the two child-hosting verbs
# --------------------------------------------------------------------------


def test_package_exec_composes_every_flag_before_its_two_groups(ocx: Ocx, process: _Process) -> None:
    ocx.package.exec(
        ["a", "b"],
        ["make", "-j4"],
        platform="linux/amd64",
        clean=True,
        private=True,
        lazy_mode="always",
        env={"CC": "clang", "PATH": PathVar("bin")},
    )

    assert process.last.command == [
        *_PLAIN,
        "package",
        "exec",
        "--clean",
        "--self",
        "--platform",
        "linux/amd64",
        "--lazy-mode",
        "always",
        "--env",
        "CC=clang",
        "--env",
        "PATH:path=bin",
        "a",
        "b",
        "--",
        "make",
        "-j4",
    ]


def test_project_exec_composes_every_flag_before_its_two_groups(project: Project, process: _Process) -> None:
    project.exec(
        ["pytest", "-q"],
        names=["uv"],
        groups=["dev", "ci"],
        clean=True,
        lazy_mode="never",
        env={"CC": "clang"},
    )

    assert process.last.command == [
        *_PLAIN,
        "--project",
        _PROJECT_ARG,
        "exec",
        "--group",
        "dev",
        "--group",
        "ci",
        "--clean",
        "--lazy-mode",
        "never",
        "--env",
        "CC=clang",
        "uv",
        "--",
        "pytest",
        "-q",
    ]


# --------------------------------------------------------------------------
# Public per-call knobs
# --------------------------------------------------------------------------


def test_unset_forwards_the_session_default_through_a_wrapper(exe: Path, process: _Process) -> None:
    policy = RetryPolicy(attempts=5)
    handle = Ocx(exe, config=OcxConfig(retry=policy), host_env=HostEnv.clean())

    def deploy(*, retry: MaybeRetry = UNSET) -> None:
        handle.about(retry=retry)

    deploy()

    assert process.last.kwargs["retry"] is policy


def test_save_targets_refuse_more_than_one_package(ocx: Ocx) -> None:
    """C-002: the ValueError survives the `info` -> `description_pull` rename."""
    with pytest.raises(ValueError, match="single package only"):
        ocx.package.description_pull("a", "b", save_readme="/tmp/readme.md")


def test_handles_report_themselves_without_dumping_the_runner(ocx: Ocx, project: Project) -> None:
    assert repr(project) == f"Project(path={_PROJECT_ARG!r}, exe={str(ocx.exe)!r})"
    assert repr(ocx.package) == f"PackageCommands(exe={str(ocx.exe)!r})"
    assert repr(ocx.config) == f"ConfigCommands(exe={str(ocx.exe)!r})"
    assert repr(ocx.patch) == f"PatchCommands(exe={str(ocx.exe)!r})"


def test_pull_dry_run_parses_the_root_array_the_preview_actually_writes(project: Project, process: _Process) -> None:
    """`--dry-run` reports a different upstream type at a different JSON root.

    Routing it into `PullReport.from_json` raised `expected a JSON object` on
    every real preview — the whole flag was unusable. The overload picks the
    row parser instead, so the two shapes never meet.
    """
    process.stdout = _DRY_RUN

    rows = project.pull(dry_run=True)

    assert [(row.package, row.status, row.path) for row in rows] == [("ocx.sh/task:3@sha256:d", "would-fetch", None)]


def test_pull_without_dry_run_still_answers_the_keyed_report(project: Project, process: _Process) -> None:
    """The other arm of the overload — the shape a real pull writes."""
    process.stdout = '{"ocx.sh/task:3":{"path":"/p","kind":"package"},"advisories":[]}'

    assert project.pull().packages["ocx.sh/task:3"].path == "/p"


def test_package_test_tolerates_a_failing_script_and_returns_the_result(ocx: Ocx, process: _Process) -> None:
    process.exit_code = 1
    process.stdout = (
        '{"status": "failed", "assertion": {"kind": "binary_missing", "message": "no such binary"},'
        ' "run": {"exit_code": 1, "stdout": "", "stderr": "", "duration_ms": 2, "truncated": false}}'
    )

    outcome = ocx.package.test("repo:1.0.0", script="/t.star")

    assert outcome.status == "failed"
    assert outcome.assertion is not None and outcome.assertion.kind == "binary_missing"
    # The tolerance is scoped: the call hands _process exactly {0, 1}.
    assert process.last.kwargs["ok_codes"] == (0, 1)


# --------------------------------------------------------------------------
# The 0.6 signing surface: C-003, C-008-C-015 bindings, S-003, S-005, S-008
# --------------------------------------------------------------------------

_SIGNED = object()
"""What a bare `sign` parse yields."""

_ATTESTED = object()
"""What a bare `attest` parse yields."""

_SWEPT = object()
"""What a swept `sign` or `attest` parse yields."""

_VERIFIED = object()
"""What a `verify` parse yields."""

_LISTED = object()
"""What an `sbom` parse yields."""

_COPIED = object()
"""What a `copy` parse yields."""


@dataclass
class _Parsers:
    """Records what the client handed each faked result parser."""

    raw: list[str] = field(default_factory=list[str])
    rows: list[Any] = field(default_factory=list[Any])
    """The row parser a swept call chose — the seam C-017 declares."""


@pytest.fixture
def parsers(monkeypatch: pytest.MonkeyPatch) -> _Parsers:
    """Fake the `_results` decode seam, leaving these rows about argv.

    The six new bindings hand their stdout to `_results`, whose wire shapes
    and `from_json` bodies are a sibling work package's. Faking the seam
    keeps this file what it has always been — evidence about what the client
    *composed* — and leaves the JSON to `tests/unit/test_results.py`.
    """
    seam = _Parsers()

    def bare(parsed: object) -> Any:
        def parse(raw: str) -> object:
            seam.raw.append(raw)
            return parsed

        return parse

    def sweep(raw: str, row: Any) -> object:
        seam.raw.append(raw)
        seam.rows.append(row)
        return _SWEPT

    monkeypatch.setattr(SignatureReport, "from_json", bare(_SIGNED))
    monkeypatch.setattr(AttestationReport, "from_json", bare(_ATTESTED))
    monkeypatch.setattr(VerificationReport, "from_json", bare(_VERIFIED))
    monkeypatch.setattr(SbomListingReport, "from_json", bare(_LISTED))
    monkeypatch.setattr(CopyReport, "from_json", bare(_COPIED))
    monkeypatch.setattr(SweepReport, "from_json", sweep)
    return seam


_SIGNING_CASES = [
    pytest.param(lambda o: o.package.sign("r:1"), ["package", "sign", "r:1"], id="sign"),
    pytest.param(
        lambda o: o.package.sign(
            "r:1",
            platform="linux/amd64",
            signature_format="both",
            rekor_url="https://rekor.test",
            no_cache=True,
        ),
        [
            "package",
            "sign",
            "--platform",
            "linux/amd64",
            "--signature-format",
            "both",
            "--rekor-url",
            "https://rekor.test",
            "--no-cache",
            "r:1",
        ],
        id="sign-keyless-flags",
    ),
    pytest.param(
        lambda o: o.package.sign("r:1", key="file://k.pem", rekor_upload=False),
        ["package", "sign", "--key", "file://k.pem", "--no-rekor-upload", "r:1"],
        id="sign-key",
    ),
    pytest.param(
        lambda o: o.package.sign(
            "r:1",
            rekor_upload=True,
            fulcio_url="https://fulcio.test",
            identity_token_file="/run/token",
            no_tty=True,
        ),
        [
            "package",
            "sign",
            "--rekor-upload",
            "--fulcio-url",
            "https://fulcio.test",
            "--identity-token-file",
            "/run/token",
            "--no-tty",
            "r:1",
        ],
        id="sign-keyless-ci",
    ),
    pytest.param(
        lambda o: o.package.sign("r:1", identity_token_stdin=True),
        ["package", "sign", "--identity-token-stdin", "r:1"],
        id="sign-token-stdin",
    ),
    pytest.param(
        lambda o: o.package.sign("r:1", tags=["1.2", "1"], tags_file="/tmp/tags"),
        ["package", "sign", "--tags", "1.2", "--tags", "1", "--tags-file", "/tmp/tags", "r:1"],
        id="sign-sweep-union",
    ),
    pytest.param(
        lambda o: o.package.attest("r:1", predicate="/sbom.json", predicate_type="cyclonedx"),
        ["package", "attest", "--predicate", "/sbom.json", "--type", "cyclonedx", "r:1"],
        id="attest",
    ),
    pytest.param(
        lambda o: o.package.attest(
            "r:1",
            predicate="/sbom.json",
            predicate_type="cyclonedx",
            platform="linux/arm64",
            signature_format="bundle",
            key="env://OCX_SIGNING_KEY",
            rekor_upload=True,
            rekor_url="https://rekor.test",
            no_cache=True,
        ),
        [
            "package",
            "attest",
            "--predicate",
            "/sbom.json",
            "--type",
            "cyclonedx",
            "--platform",
            "linux/arm64",
            "--signature-format",
            "bundle",
            "--key",
            "env://OCX_SIGNING_KEY",
            "--rekor-upload",
            "--rekor-url",
            "https://rekor.test",
            "--no-cache",
            "r:1",
        ],
        id="attest-flags",
    ),
    pytest.param(
        lambda o: o.package.attest("r:1", predicate="/p.json", predicate_type="spdx", tags_file="/tmp/tags"),
        ["package", "attest", "--predicate", "/p.json", "--type", "spdx", "--tags-file", "/tmp/tags", "r:1"],
        id="attest-sweep",
    ),
    pytest.param(lambda o: o.package.verify("r:1"), ["package", "verify", "r:1"], id="verify"),
    pytest.param(
        lambda o: o.package.verify(
            "r:1",
            platform="linux/amd64",
            certificate_identity="ci@example.test",
            certificate_oidc_issuer="https://token.example.test",
            signature_format="bundle",
            rekor_url="https://rekor.test",
            attestation=True,
            predicate_type="cyclonedx",
            allow_unlogged_signature=True,
            no_cache=True,
            sigstore_trusted_root="/trust/root.json",
        ),
        [
            "package",
            "verify",
            "--platform",
            "linux/amd64",
            "--certificate-identity",
            "ci@example.test",
            "--certificate-oidc-issuer",
            "https://token.example.test",
            "--signature-format",
            "bundle",
            "--rekor-url",
            "https://rekor.test",
            "--attestation",
            "--type",
            "cyclonedx",
            "--allow-unlogged-signature",
            "--no-cache",
            "--sigstore-trusted-root",
            "/trust/root.json",
            "r:1",
        ],
        id="verify-flags",
    ),
    pytest.param(
        lambda o: o.package.verify("r:1", key="file://pub.pem"),
        ["package", "verify", "--key", "file://pub.pem", "r:1"],
        id="verify-key",
    ),
    pytest.param(
        lambda o: o.package.verify("r:1", signature_format="simplesigning"),
        ["package", "verify", "--signature-format", "simplesigning", "r:1"],
        id="verify-pins-one-format",
    ),
    pytest.param(lambda o: o.package.sbom("r:1"), ["package", "sbom", "r:1"], id="sbom"),
    pytest.param(
        lambda o: o.package.sbom(
            "r:1",
            platform="linux/amd64",
            summary=True,
            predicate_type="cyclonedx",
            certificate_identity="ci@example.test",
            certificate_oidc_issuer="https://token.example.test",
            signature_format="bundle",
            sigstore_trusted_root="/trust/root.json",
            rekor_url="https://rekor.test",
            no_cache=True,
            verify=True,
        ),
        [
            "package",
            "sbom",
            "--platform",
            "linux/amd64",
            "--summary",
            "--type",
            "cyclonedx",
            "--certificate-identity",
            "ci@example.test",
            "--certificate-oidc-issuer",
            "https://token.example.test",
            "--signature-format",
            "bundle",
            "--sigstore-trusted-root",
            "/trust/root.json",
            "--rekor-url",
            "https://rekor.test",
            "--no-cache",
            "--verify",
            "r:1",
        ],
        id="sbom-flags",
    ),
    pytest.param(
        lambda o: o.package.sbom("r:1", verify=False),
        ["package", "sbom", "--no-verify", "r:1"],
        id="sbom-unverified",
    ),
    pytest.param(
        lambda o: o.package.copy("dev.test/team/tool:1.4.2"),
        ["package", "copy", "dev.test/team/tool:1.4.2"],
        id="copy-default-registry",
    ),
    pytest.param(
        lambda o: o.package.copy("dev.test/team/tool:1.4.2", to="prod.test"),
        ["package", "copy", "--to", "prod.test", "dev.test/team/tool:1.4.2"],
        id="copy-promote",
    ),
    pytest.param(
        lambda o: o.package.copy(
            "dev.test/team/tool:1.4.2",
            identifier="prod.test/tool:1.4.2",
            platforms=["linux/amd64", "linux/arm64"],
            cascade=True,
            keep_tag=False,
            referrers=True,
            description=True,
            annotations={"org.opencontainers.image.source": "https://example.test"},
            dry_run=True,
        ),
        [
            "package",
            "copy",
            "--identifier",
            "prod.test/tool:1.4.2",
            "--platform",
            "linux/amd64",
            "--platform",
            "linux/arm64",
            "--cascade",
            "--no-keep-tag",
            "--referrers",
            "--description",
            "--annotation",
            "org.opencontainers.image.source=https://example.test",
            "--dry-run",
            "dev.test/team/tool:1.4.2",
        ],
        id="copy-flags",
    ),
    pytest.param(
        lambda o: o.package.copy("dev.test/tool@sha256:abc", identifier="prod.test/tool:1", platforms=["linux/amd64"]),
        [
            "package",
            "copy",
            "--identifier",
            "prod.test/tool:1",
            "--platform",
            "linux/amd64",
            "dev.test/tool@sha256:abc",
        ],
        id="copy-from-digest",
    ),
    pytest.param(
        lambda o: o.package.copy("dev.test/team/tool:1.4.2@sha256:abc", to="prod.test"),
        ["package", "copy", "--to", "prod.test", "dev.test/team/tool:1.4.2@sha256:abc"],
        id="copy-from-a-pinned-tag",
    ),
    pytest.param(
        lambda o: o.package.description_push("r:1", from_="staging.test/tool:1"),
        ["package", "description", "push", "--from", "staging.test/tool:1", "r:1"],
        id="description-push-from",
    ),
    pytest.param(
        lambda o: o.package.description_push(
            "r:1",
            readme="/README.md",
            logo="/logo.png",
            title="Tool",
            description="Does a thing",
            keywords="build,ci",
        ),
        [
            "package",
            "description",
            "push",
            "--readme",
            "/README.md",
            "--logo",
            "/logo.png",
            "--title",
            "Tool",
            "--description",
            "Does a thing",
            "--keywords",
            "build,ci",
            "r:1",
        ],
        id="description-push-fields",
    ),
]


@pytest.mark.parametrize(("call", "expected"), _SIGNING_CASES)
def test_signing_tier_argv(ocx: Ocx, process: _Process, parsers: _Parsers, call: Any, expected: list[str]) -> None:
    """One row per new binding against its 0.6 `--help` fixture."""
    call(ocx)

    assert process.last.command == [*_JSON, *expected]


def test_verify_and_install_leave_the_toggle_unset_by_default(ocx: Ocx, process: _Process) -> None:
    """C-008: `None` is the third state — ocx's own default decides."""
    process.stdout = _INSTALLED

    ocx.package.install("a")

    assert "--verify" not in process.last.command
    assert "--no-verify" not in process.last.command


def test_package_test_still_binds_the_env_override(ocx: Ocx, process: _Process) -> None:
    """C-010: `--env` on `package test` is unchanged in 0.6 — nothing to build."""
    process.stdout = _TEST_PASSED

    ocx.package.test("repo:1.0.0", script="/t.star", env={"CC": "clang"})

    assert process.last.command[-2:] == ["--env", "CC=clang"]


# --------------------------------------------------------------------------
# Sweep discrimination (D2, S-008) and the C-017 row seam
# --------------------------------------------------------------------------


def test_a_tag_sweep_returns_per_tag_rows(ocx: Ocx, process: _Process, parsers: _Parsers) -> None:
    """S-008: `tags=` sweeps, and the rows parse as signature reports."""
    assert ocx.package.sign("r:1", tags=["1.2", "1"]) is _SWEPT
    assert parsers.rows == [SignatureReport.from_dict]


def test_a_tags_file_sweeps_on_its_own(ocx: Ocx, process: _Process, parsers: _Parsers) -> None:
    assert ocx.package.sign("r:1", tags_file="/tmp/tags") is _SWEPT


def test_an_unswept_sign_returns_the_bare_report(ocx: Ocx, process: _Process, parsers: _Parsers) -> None:
    assert ocx.package.sign("r:1") is _SIGNED
    assert parsers.rows == []


def test_an_explicit_none_signs_the_reference_itself(ocx: Ocx, process: _Process, parsers: _Parsers) -> None:
    """`tags=None` is the spelling the overload set types honestly."""
    assert ocx.package.sign("r:1", tags=None) is _SIGNED
    assert "--tags" not in process.last.command
    assert parsers.rows == []


def test_a_swept_attest_parses_its_rows_as_attestations(ocx: Ocx, process: _Process, parsers: _Parsers) -> None:
    """C-017: the row seam must not hand `sign`'s parser to `attest`."""
    assert ocx.package.attest("r:1", predicate="/p.json", predicate_type="spdx", tags=["1"]) is _SWEPT
    assert parsers.rows == [AttestationReport.from_dict]


def test_an_unswept_attest_returns_the_bare_report(ocx: Ocx, process: _Process, parsers: _Parsers) -> None:
    assert ocx.package.attest("r:1", predicate="/p.json", predicate_type="spdx") is _ATTESTED


@pytest.mark.parametrize(
    ("call", "parsed"),
    [
        pytest.param(lambda o: o.package.verify("r:1"), _VERIFIED, id="verify"),
        pytest.param(lambda o: o.package.sbom("r:1"), _LISTED, id="sbom"),
        pytest.param(lambda o: o.package.copy("dev.test/t:1", to="prod.test"), _COPIED, id="copy"),
    ],
)
def test_each_read_returns_its_own_report(
    ocx: Ocx, process: _Process, parsers: _Parsers, call: Any, parsed: object
) -> None:
    process.stdout = '{"payload":"whatever"}'

    assert call(ocx) is parsed
    assert parsers.raw == ['{"payload":"whatever"}']


# --------------------------------------------------------------------------
# Guards: every one refuses before the binary is reached
# --------------------------------------------------------------------------

_GUARD_CASES = [
    pytest.param(
        lambda o: o.package.sign("r:1", rekor_upload=False),
        "only key signing may do",
        id="sign-no-rekor-upload-without-key",
    ),
    pytest.param(
        lambda o: o.package.attest("r:1", predicate="/p.json", predicate_type="spdx", rekor_upload=False),
        "only key signing may do",
        id="attest-no-rekor-upload-without-key",
    ),
    pytest.param(
        lambda o: o.package.push("a.tar.gz", identifier="r:1", sign=True, rekor_upload=False),
        "only key signing may do",
        id="push-no-rekor-upload-without-key",
    ),
    pytest.param(
        lambda o: o.package.sign("r:1", key="file://k.pem", fulcio_url="https://fulcio.test"),
        "fulcio_url",
        id="sign-key-with-fulcio-url",
    ),
    pytest.param(
        lambda o: o.package.sign("r:1", key="file://k.pem", identity_token_file="/run/token"),
        "identity_token_file",
        id="sign-key-with-token-file",
    ),
    pytest.param(
        lambda o: o.package.sign("r:1", key="file://k.pem", identity_token_stdin=True),
        "identity_token_stdin",
        id="sign-key-with-token-stdin",
    ),
    pytest.param(
        lambda o: o.package.sign("r:1", key="file://k.pem", no_tty=True),
        "no_tty",
        id="sign-key-with-no-tty",
    ),
    pytest.param(
        lambda o: o.package.attest("r:1", predicate="/p.json", predicate_type="spdx", key="file://k.pem", no_tty=True),
        "no_tty",
        id="attest-key-with-no-tty",
    ),
    pytest.param(
        lambda o: o.package.sign("r:1", identity_token_file="/run/token", identity_token_stdin=True),
        "two ways to supply one OIDC token",
        id="sign-both-token-sources",
    ),
    pytest.param(
        lambda o: o.package.sign("r:1", tags=[]),
        "there is nothing to sweep",
        id="sign-empty-tags",
    ),
    pytest.param(
        lambda o: o.package.attest("r:1", predicate="/p.json", predicate_type="spdx", tags=[]),
        "there is nothing to sweep",
        id="attest-empty-tags",
    ),
    pytest.param(
        lambda o: o.package.sign("r:1", platform="linux/amd64", tags=["1"]),
        "acts on indices by definition",
        id="sign-platform-with-tags",
    ),
    pytest.param(
        lambda o: o.package.sign("r:1", platform="linux/amd64", tags_file="/tmp/tags"),
        "acts on indices by definition",
        id="sign-platform-with-tags-file",
    ),
    pytest.param(
        lambda o: o.package.attest(
            "r:1", predicate="/p.json", predicate_type="spdx", platform="linux/amd64", tags=["1"]
        ),
        "acts on indices by definition",
        id="attest-platform-with-tags",
    ),
    pytest.param(
        lambda o: o.package.verify("r:1", certificate_identity="ci@example.test"),
        "certificate_oidc_issuer is missing",
        id="verify-half-an-identity",
    ),
    pytest.param(
        lambda o: o.package.verify("r:1", certificate_oidc_issuer="https://token.example.test"),
        "certificate_identity is missing",
        id="verify-half-an-issuer",
    ),
    pytest.param(
        lambda o: o.package.verify("r:1", key="file://pub.pem", certificate_identity="ci@example.test"),
        "no SAN or issuer",
        id="verify-identity-with-key",
    ),
    pytest.param(
        lambda o: o.package.verify("r:1", predicate_type="cyclonedx"),
        "no predicate to narrow",
        id="verify-type-without-attestation",
    ),
    pytest.param(
        lambda o: o.package.sbom("r:1", certificate_identity="ci@example.test"),
        "certificate_oidc_issuer is missing",
        id="sbom-half-an-identity",
    ),
    pytest.param(
        lambda o: o.package.sbom("r:1", key="file://pub.pem", certificate_oidc_issuer="https://token.example.test"),
        "no SAN or issuer",
        id="sbom-issuer-with-key",
    ),
    pytest.param(
        lambda o: o.package.sbom(
            "r:1",
            verify=False,
            certificate_identity="ci@example.test",
            certificate_oidc_issuer="https://token.example.test",
        ),
        "certificate_identity, certificate_oidc_issuer names material",
        id="sbom-no-verify-with-identity",
    ),
    pytest.param(
        lambda o: o.package.sbom("r:1", verify=False, key="file://pub.pem"),
        "key names material",
        id="sbom-no-verify-with-key",
    ),
    pytest.param(
        lambda o: o.package.copy("dev.test/t:1", to="prod.test", identifier="prod.test/t:1"),
        "identifier already says everything",
        id="copy-to-with-identifier",
    ),
    pytest.param(
        lambda o: o.package.copy("dev.test/t@sha256:abc", identifier="prod.test/t:1"),
        "carries no platform of its own",
        id="copy-digest-without-a-platform",
    ),
    pytest.param(
        lambda o: o.package.copy(
            "dev.test/t@sha256:abc", identifier="prod.test/t:1", platforms=["linux/amd64", "linux/arm64"]
        ),
        "carries no platform of its own",
        id="copy-digest-with-two-platforms",
    ),
    pytest.param(
        lambda o: o.package.copy("dev.test/t@sha256:abc", platforms=["linux/amd64"]),
        "carries no tag for the target",
        id="copy-digest-without-an-identifier",
    ),
    pytest.param(
        lambda o: o.package.copy("localhost:5000/t@sha256:abc", identifier="prod.test/t:1"),
        "carries no platform of its own",
        id="copy-digest-behind-a-registry-port",
    ),
    pytest.param(
        lambda o: o.package.description_push("r:1", from_="staging.test/t:1", title="Tool"),
        "title came with from_",
        id="description-push-from-with-a-field",
    ),
    pytest.param(
        lambda o: o.package.push("a.tar.gz", identifier="r:1", key="file://k.pem"),
        "key modifies signing",
        id="push-key-without-a-signing-target",
    ),
    pytest.param(
        lambda o: o.package.push("a.tar.gz", identifier="r:1", signature_format="bundle"),
        "signature_format modifies signing",
        id="push-signature-format-without-a-signing-target",
    ),
    pytest.param(
        lambda o: o.package.push("a.tar.gz", identifier="r:1", rekor_upload=True),
        "rekor_upload modifies signing",
        id="push-rekor-upload-without-a-signing-target",
    ),
    pytest.param(
        lambda o: o.package.push("a.tar.gz", identifier="r:1", key="file://k.pem", rekor_upload=False),
        "key, rekor_upload modifies signing",
        id="push-two-modifiers-without-a-signing-target",
    ),
    pytest.param(
        lambda o: o.package.verify("r:1", signature_format="both"),
        "selects what to write",
        id="verify-signature-format-both",
    ),
    pytest.param(
        lambda o: o.package.sbom("r:1", signature_format="both"),
        "selects what to write",
        id="sbom-signature-format-both",
    ),
    pytest.param(
        lambda o: o.package.description_push("r:1"),
        "there is nothing to publish",
        id="description-push-with-nothing-to-publish",
    ),
]


@pytest.mark.parametrize(("call", "expected"), _GUARD_CASES)
def test_a_refused_combination_never_reaches_the_binary(ocx: Ocx, process: _Process, call: Any, expected: str) -> None:
    """Every guard raises `ValueError` before argv composes, as C-001 does."""
    with pytest.raises(ValueError, match=re.escape(expected)):
        call(ocx)

    assert process.calls == []


def test_the_key_conflict_names_every_flag_it_refused(ocx: Ocx) -> None:
    """The five-flag set is invisible in `--help`; the message must not be."""
    with pytest.raises(ValueError) as caught:
        ocx.package.sign(
            "r:1",
            key="file://k.pem",
            fulcio_url="https://fulcio.test",
            identity_token_file="/run/token",
            no_tty=True,
        )

    assert "fulcio_url, identity_token_file, no_tty" in str(caught.value)


def test_a_key_signature_in_headless_ci_is_refused_not_composed(ocx: Ocx, process: _Process) -> None:
    """`--key` with `--no-tty` is exit 64 upstream and `--help` never says so."""
    with pytest.raises(ValueError, match="no_tty"):
        ocx.package.sign("r:1", key="file://k.pem", no_tty=True)

    assert process.calls == []


@pytest.mark.parametrize(
    "call",
    [
        pytest.param(lambda o: o.package.sign("r:1", tags=["1"], tags_file="/t"), id="sign"),
        pytest.param(
            lambda o: o.package.attest("r:1", predicate="/p.json", predicate_type="spdx", tags=["1"], tags_file="/t"),
            id="attest",
        ),
    ],
)
def test_tags_and_tags_file_union_rather_than_conflict(
    ocx: Ocx, process: _Process, parsers: _Parsers, call: Any
) -> None:
    """ocx concatenates the two sources — the SDK must not invent an exclusion."""
    assert call(ocx) is _SWEPT
    assert "--tags" in process.last.command
    assert "--tags-file" in process.last.command


@pytest.mark.parametrize(
    "call",
    [
        pytest.param(lambda o: o.package.sign("-rf"), id="sign"),
        pytest.param(lambda o: o.package.attest("-rf", predicate="/p.json", predicate_type="spdx"), id="attest"),
        pytest.param(lambda o: o.package.verify("-rf"), id="verify"),
        pytest.param(lambda o: o.package.sbom("-rf"), id="sbom"),
        pytest.param(lambda o: o.package.copy("-rf", to="prod.test"), id="copy"),
        pytest.param(lambda o: o.package.description_push("-rf", title="Tool"), id="description-push"),
    ],
)
def test_leading_dash_rejected_on_every_new_positional(ocx: Ocx, process: _Process, call: Any) -> None:
    with pytest.raises(ValueError, match="may not start with '-'"):
        call(ocx)


# --------------------------------------------------------------------------
# Retry disposition (D5)
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "call",
    [
        pytest.param(lambda o: o.package.sign("r:1"), id="sign"),
        pytest.param(lambda o: o.package.attest("r:1", predicate="/p.json", predicate_type="spdx"), id="attest"),
        pytest.param(lambda o: o.package.description_push("r:1", title="Tool"), id="description-push"),
        pytest.param(lambda o: o.package.copy("dev.test/t:1", to="prod.test"), id="copy"),
    ],
)
def test_the_new_writes_disable_retry_by_default(exe: Path, process: _Process, parsers: _Parsers, call: Any) -> None:
    """D5: a registry write is never re-sent on a transient failure."""
    call(Ocx(exe, config=OcxConfig(retry=RetryPolicy()), host_env=HostEnv.clean()))

    assert process.last.kwargs["retry"] is None


@pytest.mark.parametrize(
    "call",
    [
        pytest.param(lambda o: o.package.copy("dev.test/t:1", to="prod.test", dry_run=True), id="copy-dry-run"),
        pytest.param(lambda o: o.package.verify("r:1"), id="verify"),
        pytest.param(lambda o: o.package.sbom("r:1"), id="sbom"),
    ],
)
def test_the_new_reads_keep_the_session_retry_policy(
    exe: Path, process: _Process, parsers: _Parsers, call: Any
) -> None:
    """D5: `copy(dry_run=True)` writes nothing, so a blip is worth retrying."""
    policy = RetryPolicy()

    call(Ocx(exe, config=OcxConfig(retry=policy), host_env=HostEnv.clean()))

    assert process.last.kwargs["retry"] is policy
