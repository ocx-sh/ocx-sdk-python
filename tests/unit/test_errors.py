# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The OCX Authors

"""Contract tests for `ocx_sdk._errors` (C-001, S-004).

Pins four things the rest of the SDK builds on: the exit-code taxonomy, the
exit-code-to-subclass map `_process` dispatches through, the fixed `retryable`
semantics of S-004, and the blanket rule that every message names an
actionable next step.

Also guards the layering invariant that makes `retryable` policy-independent:
this module imports nothing from `ocx_sdk`.
"""

from __future__ import annotations

import ast
import pickle
from pathlib import Path

import pytest

from ocx_sdk import _errors
from ocx_sdk._errors import (
    AuthError,
    BootstrapError,
    ChecksumMismatchError,
    ConfigError,
    DataError,
    DirtyRcBlockError,
    DistManifestError,
    DownloadError,
    ExitCode,
    IoError,
    NotFoundError,
    OcxError,
    OcxExecutionError,
    OcxNotFoundError,
    OcxProcessError,
    OcxTimeoutError,
    PermissionDeniedError,
    PolicyBlockedError,
    TempFailError,
    UnavailableError,
    UnsupportedPlatformError,
    UsageError,
    VersionCompatError,
)

_ABSTRACT = (OcxError, OcxExecutionError)


def _concrete_errors() -> list[type[OcxError]]:
    """Return every error class of `_errors` a caller can actually be handed.

    Walks a fixed root and keeps only classes declared in `_errors`, so a
    subclass defined by another test module cannot drift into the corpus.
    """
    found: list[type[OcxError]] = []
    stack: list[type[OcxError]] = [OcxError]
    while stack:
        cls = stack.pop()
        stack.extend(cls.__subclasses__())
        if cls not in _ABSTRACT and cls.__module__ == _errors.__name__:
            found.append(cls)
    return sorted(found, key=lambda cls: cls.__name__)


def _instances() -> dict[type[OcxError], OcxError]:
    """Build one real instance of every concrete error, keyed by class."""
    built: dict[type[OcxError], OcxError] = {
        cls: cls(code, ["ocx", "package", "pull", "app"], "boom", attempts=2)
        for code, cls in _errors._EXIT_CODE_ERRORS.items()
    }
    built[OcxProcessError] = OcxProcessError(ExitCode.FAILURE, ["ocx", "status"], "boom")
    built[OcxTimeoutError] = OcxTimeoutError(30.0, ["ocx", "pull"], "resolving...")
    built[OcxNotFoundError] = OcxNotFoundError("No ocx on PATH.")
    built[VersionCompatError] = VersionCompatError("0.4.0", "0.5.8")
    for cls in (BootstrapError, DownloadError, ChecksumMismatchError, DistManifestError, UnsupportedPlatformError):
        built[cls] = cls("could not fetch https://setup.ocx.sh/dist.json")
    return built


_ERROR_INSTANCES = _instances()


@pytest.mark.parametrize(
    ("member", "value"),
    [
        pytest.param(ExitCode.OK, 0, id="ok"),
        pytest.param(ExitCode.FAILURE, 1, id="generic-failure"),
        pytest.param(ExitCode.USAGE, 64, id="usage"),
        pytest.param(ExitCode.DATA_ERR, 65, id="data"),
        pytest.param(ExitCode.UNAVAILABLE, 69, id="unavailable"),
        pytest.param(ExitCode.IO_ERR, 74, id="io"),
        pytest.param(ExitCode.TEMP_FAIL, 75, id="tempfail"),
        pytest.param(ExitCode.NO_PERM, 77, id="noperm"),
        pytest.param(ExitCode.CONFIG, 78, id="config"),
        pytest.param(ExitCode.NOT_FOUND, 79, id="notfound"),
        pytest.param(ExitCode.AUTH, 80, id="auth"),
        pytest.param(ExitCode.POLICY_BLOCKED, 81, id="policy-blocked"),
        pytest.param(ExitCode.DIRTY_RC_BLOCK, 82, id="dirty-rc-block"),
    ],
)
def test_exit_code_taxonomy(member: ExitCode, value: int) -> None:
    assert member == value


def test_exit_code_has_no_members_beyond_the_documented_table() -> None:
    assert {int(code) for code in ExitCode} == {0, 1, 64, 65, 69, 74, 75, 77, 78, 79, 80, 81, 82}


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        pytest.param(ExitCode.USAGE, UsageError, id="64-usage"),
        pytest.param(ExitCode.DATA_ERR, DataError, id="65-data"),
        pytest.param(ExitCode.UNAVAILABLE, UnavailableError, id="69-unavailable"),
        pytest.param(ExitCode.IO_ERR, IoError, id="74-io"),
        pytest.param(ExitCode.TEMP_FAIL, TempFailError, id="75-tempfail"),
        pytest.param(ExitCode.NO_PERM, PermissionDeniedError, id="77-noperm"),
        pytest.param(ExitCode.CONFIG, ConfigError, id="78-config"),
        pytest.param(ExitCode.NOT_FOUND, NotFoundError, id="79-notfound"),
        pytest.param(ExitCode.AUTH, AuthError, id="80-auth"),
        pytest.param(ExitCode.POLICY_BLOCKED, PolicyBlockedError, id="81-policy-blocked"),
        pytest.param(ExitCode.DIRTY_RC_BLOCK, DirtyRcBlockError, id="82-dirty-rc-block"),
    ],
)
def test_exit_code_maps_to_its_subclass(code: ExitCode, expected: type[OcxProcessError]) -> None:
    assert _errors._EXIT_CODE_ERRORS[code] is expected


def test_success_and_generic_failure_have_no_subclass() -> None:
    unmapped = {code for code in ExitCode if code not in _errors._EXIT_CODE_ERRORS}

    assert unmapped == {ExitCode.OK, ExitCode.FAILURE}


@pytest.mark.parametrize("code", list(ExitCode), ids=lambda code: code.name.lower())
def test_retryable_is_true_only_for_tempfail(code: ExitCode) -> None:
    assert OcxProcessError(code, ["ocx", "pull"]).retryable is (code == ExitCode.TEMP_FAIL)


def test_retryable_holds_for_an_unmapped_exit_code() -> None:
    assert OcxProcessError(137, ["ocx", "pull"]).retryable is False


def test_unmapped_exit_code_does_not_break_the_error_path() -> None:
    err = OcxProcessError(137, ["ocx", "pull"])

    assert err.exit_code == 137
    assert "137" in str(err)


def test_process_error_message_names_argv_exit_code_and_next_step() -> None:
    err = AuthError(ExitCode.AUTH, ["ocx", "package", "pull", "app"], "401 Unauthorized")

    message = str(err)

    assert "ocx package pull app" in message
    assert "80" in message
    assert "401 Unauthorized" in message
    assert "ocx.login" in message
    assert "never retried" in message


def test_process_error_message_reports_attempts_when_a_policy_retried() -> None:
    err = TempFailError(ExitCode.TEMP_FAIL, ["ocx", "pull"], attempts=3)

    assert "3 attempts" in str(err)


def test_process_error_message_omits_attempts_for_a_single_try() -> None:
    assert "attempt" not in str(TempFailError(ExitCode.TEMP_FAIL, ["ocx", "pull"]))


def test_process_error_defaults_to_one_attempt() -> None:
    assert OcxProcessError(ExitCode.FAILURE, ["ocx", "status"]).attempts == 1


def test_process_error_keeps_full_stderr_on_the_attribute_and_truncates_the_message() -> None:
    noise = "x" * 5000 + "\nthe actual failure"
    err = OcxProcessError(ExitCode.FAILURE, ["ocx", "status"], noise)

    message = str(err)

    assert err.stderr == noise
    assert "the actual failure" in message
    assert len(message) < len(noise)


def test_process_error_message_omits_blank_stderr() -> None:
    message = str(OcxProcessError(ExitCode.FAILURE, ["ocx", "status"], "   \n"))

    assert "stderr:" not in message
    assert message.startswith("`ocx status` exited 1. ")


def test_process_error_quotes_argv_so_it_can_be_pasted_back() -> None:
    err = OcxProcessError(ExitCode.USAGE, ["ocx", "run", "--", "sh", "-c", "echo hi there"])

    assert "'echo hi there'" in str(err)


def test_execution_error_snapshots_argv_as_a_tuple() -> None:
    argv = ["ocx", "status"]
    err = OcxProcessError(ExitCode.FAILURE, argv)
    argv.append("--json")

    assert err.argv == ("ocx", "status")


def test_timeout_error_names_the_budget_and_keeps_partial_stderr() -> None:
    err = OcxTimeoutError(30.0, ["ocx", "pull"], "resolving...")

    message = str(err)

    assert err.timeout == 30.0
    assert err.stderr == "resolving..."
    assert "30.0" in message
    assert "resolving..." in message
    assert "timeout=" in message


@pytest.mark.parametrize(
    ("cls", "args"),
    [
        pytest.param(OcxProcessError, (ExitCode.FAILURE, ["ocx"]), id="process-exit"),
        pytest.param(OcxTimeoutError, (30.0, ["ocx"]), id="timeout"),
    ],
)
def test_process_failures_and_timeouts_share_one_catch_shape(cls: type[OcxExecutionError], args: tuple) -> None:
    with pytest.raises(OcxExecutionError, match="ocx"):
        raise cls(*args)


@pytest.mark.parametrize(
    ("cls", "needle"),
    [
        pytest.param(DownloadError, "mirror_url", id="download-names-mirror"),
        pytest.param(ChecksumMismatchError, "Never retried", id="checksum-never-retried"),
        pytest.param(DistManifestError, "sha256", id="manifest-names-pinning"),
        pytest.param(UnsupportedPlatformError, "exe=", id="platform-names-manual-install"),
    ],
)
def test_bootstrap_error_messages_name_a_next_step(cls: type[BootstrapError], needle: str) -> None:
    err = cls("could not fetch https://setup.ocx.sh/dist.json")

    assert isinstance(err, BootstrapError)
    assert "could not fetch https://setup.ocx.sh/dist.json" in str(err)
    assert needle in str(err)


def test_not_found_error_names_the_bootstrap_fix() -> None:
    message = str(OcxNotFoundError("No ocx on PATH or in $OCX_HOME."))

    assert "No ocx on PATH or in $OCX_HOME." in message
    assert "bootstrap.ensure()" in message
    assert "OCX_SDK_EXE" in message


def test_exit_79_covers_both_things_ocx_can_fail_to_find() -> None:
    # A project-tier call resolves a `--project` path as well as an
    # identifier, and exit 79 is what a missing ocx.toml comes back as. A hint
    # that only mentions the registry sends that caller looking in the wrong
    # place entirely.
    # A neutral argv on purpose: with `--project /srv/build/ocx.toml` on the
    # command line, `ocx.toml` shows up in the message whatever the hint says.
    message = str(NotFoundError(79, ["ocx", "package", "inspect", "acme/tool:1"]))

    assert "identifier" in message
    assert "registry" in message
    assert "Ocx.project(...)" in message


def test_version_compat_error_reports_both_bounds() -> None:
    err = VersionCompatError("0.4.0", "0.5.8")

    message = str(err)

    assert (err.found, err.minimum) == ("0.4.0", "0.5.8")
    assert "0.4.0" in message
    assert "0.5.8" in message
    assert "bootstrap.ensure(version=...)" in message


def test_every_concrete_error_has_a_test_instance() -> None:
    assert set(_ERROR_INSTANCES) == set(_concrete_errors())


@pytest.mark.parametrize("err", _ERROR_INSTANCES.values(), ids=lambda err: type(err).__name__)
def test_every_concrete_error_names_an_actionable_next_step(err: OcxError) -> None:
    message = str(err)

    assert err._hint, f"{type(err).__name__} must name a next step, not just state the failure"
    assert message.endswith(err._hint), f"{type(err).__name__} message drops its next step: {message}"


@pytest.mark.parametrize("err", _ERROR_INSTANCES.values(), ids=lambda err: type(err).__name__)
def test_every_concrete_error_survives_a_pickle_round_trip(err: OcxError) -> None:
    restored = pickle.loads(pickle.dumps(err))

    assert type(restored) is type(err)
    assert str(restored) == str(err)
    assert restored.__dict__ == err.__dict__


def test_pickled_process_error_keeps_its_retry_state() -> None:
    err = AuthError(ExitCode.AUTH, ["ocx", "package", "pull", "app"], "401 Unauthorized", attempts=4)

    restored: AuthError = pickle.loads(pickle.dumps(err))

    assert (restored.exit_code, restored.argv, restored.stderr, restored.attempts) == (
        ExitCode.AUTH,
        ("ocx", "package", "pull", "app"),
        "401 Unauthorized",
        4,
    )
    assert restored.retryable is False


def test_pickled_timeout_error_keeps_its_budget() -> None:
    restored: OcxTimeoutError = pickle.loads(pickle.dumps(OcxTimeoutError(30.0, ["ocx", "pull"], "partial")))

    assert (restored.timeout, restored.argv, restored.stderr) == (30.0, ("ocx", "pull"), "partial")


def test_errors_stays_a_stdlib_only_leaf() -> None:
    tree = ast.parse(Path(_errors.__file__).read_text(encoding="utf-8"))

    offenders = [
        node.module or "."
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and (node.level > 0 or (node.module or "").startswith("ocx_sdk"))
    ] + [
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
        if alias.name.startswith("ocx_sdk")
    ]

    assert offenders == [], "_errors must not import from ocx_sdk — retryable stays policy-independent"
