# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The OCX Authors

"""Contract tests for `ocx_sdk._errors` (v0.1 C-001, v0.1 S-004; C-006, S-006, D6, D10).

Every release plan restarts numbering at C-001/S-001, so a citation to a
superseded plan carries its version and a bare ID means the current plan.

Pins four things the rest of the SDK builds on: the exit-code taxonomy, the
exit-code-to-subclass map `_process` dispatches through, the fixed `retryable`
semantics of v0.1 S-004, and the blanket rule that every message names an
actionable next step.

The 0.2.0 additions: C-006's three signing exit codes with the distinct
remedies S-006 requires of each, D6's rule that none of them is retried by
default, and D10 part 1 — `OcxProcessError.stdout` carries a partial-failure
report that must never reach any rendered form of the error.

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
    ReferrersUnsupportedError,
    TempFailError,
    TransparencyLogUnavailableError,
    UnavailableError,
    UnsupportedKeyBackendError,
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
    built[VersionCompatError] = VersionCompatError("0.5.8", "0.6.0")
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
        pytest.param(ExitCode.TRANSPARENCY_LOG_UNAVAILABLE, 83, id="transparency-log-unavailable"),
        pytest.param(ExitCode.REFERRERS_UNSUPPORTED, 84, id="referrers-unsupported"),
        pytest.param(ExitCode.UNSUPPORTED_KEY_BACKEND, 85, id="unsupported-key-backend"),
    ],
)
def test_exit_code_taxonomy(member: ExitCode, value: int) -> None:
    assert member == value


def test_exit_code_has_no_members_beyond_the_documented_table() -> None:
    assert {int(code) for code in ExitCode} == {0, 1, 64, 65, 69, 74, 75, 77, 78, 79, 80, 81, 82, 83, 84, 85}


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
        pytest.param(ExitCode.TRANSPARENCY_LOG_UNAVAILABLE, TransparencyLogUnavailableError, id="83-transparency-log"),
        pytest.param(ExitCode.REFERRERS_UNSUPPORTED, ReferrersUnsupportedError, id="84-referrers"),
        pytest.param(ExitCode.UNSUPPORTED_KEY_BACKEND, UnsupportedKeyBackendError, id="85-key-backend"),
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


# D10 part 1 — a partial-failure report lands on stdout alongside a non-zero
# exit, and `_process` deliberately never redacts stdout: substituting inside it
# would corrupt the JSON the caller is about to parse. The exemption is only
# safe while stdout stays off every rendered form of the error, so a
# secret-shaped token is planted here to prove it.
_PARTIAL_REPORT_SECRET = "ghp_wp1PartialReportToken"
_PARTIAL_REPORT_STDOUT = (
    '{"schema_version": 1, "command": "package sign", "exit_code": 83, '
    f'"data": {{"registry_token": "{_PARTIAL_REPORT_SECRET}"}}}}'
)


def test_process_error_carries_stdout_on_the_attribute_and_never_in_a_rendered_form() -> None:
    # D10 part 1 (regression lock, S-009's last error case). This is the test
    # that stops a future reviewer from "fixing" the redaction exemption by
    # folding stdout into `_summary()`.
    err = OcxProcessError(
        ExitCode.TRANSPARENCY_LOG_UNAVAILABLE,
        ["ocx", "package", "sign", "acme/tool:1"],
        "rekor: connection refused",
        stdout=_PARTIAL_REPORT_STDOUT,
    )

    assert err.stdout == _PARTIAL_REPORT_STDOUT
    assert _PARTIAL_REPORT_SECRET not in str(err)
    assert _PARTIAL_REPORT_SECRET not in repr(err)


def test_process_error_defaults_stdout_to_empty() -> None:
    # D10 part 1: every process error has the attribute, so a caller reaching
    # for the report on an error raised before there was one reads "" rather
    # than raising AttributeError.
    assert OcxProcessError(ExitCode.FAILURE, ["ocx", "status"]).stdout == ""


def test_pickled_process_error_keeps_stdout_across_a_process_boundary() -> None:
    # D10 part 1 (regression lock): `__reduce__` walks `__dict__`, so the
    # report rides along with no new code. A ProcessPoolExecutor worker that
    # raises has to hand the report back intact — and still not in the message.
    err = OcxProcessError(
        ExitCode.TRANSPARENCY_LOG_UNAVAILABLE,
        ["ocx", "package", "sign", "acme/tool:1"],
        "rekor: connection refused",
        stdout=_PARTIAL_REPORT_STDOUT,
    )

    restored: OcxProcessError = pickle.loads(pickle.dumps(err))

    assert restored.stdout == _PARTIAL_REPORT_STDOUT
    assert _PARTIAL_REPORT_SECRET not in str(restored)


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


# C-006's three new rows, as one spec table.
#
# Do not delete the five tests below as redundant with
# `test_every_concrete_error_names_an_actionable_next_step`: that one asserts
# only that `_hint` is *truthy*, which an inherited base hint satisfies while
# the caller is handed no remedy at all. It looks like coverage of S-006 and
# is not. These assert that each class overrides the base hint, and that the
# override names the specific remedy C-006's table assigns it.
#
# Argv in every test below is
# deliberately neutral — a realistic `--key awskms://...` or a stderr line
# quoting Rekor would satisfy the hint assertions without the hint saying
# anything, the same trap `test_exit_79_...` sidesteps.
_SIGNING_EXIT_CODES = [
    pytest.param(ExitCode.TRANSPARENCY_LOG_UNAVAILABLE, TransparencyLogUnavailableError, id="83-transparency-log"),
    pytest.param(ExitCode.REFERRERS_UNSUPPORTED, ReferrersUnsupportedError, id="84-referrers"),
    pytest.param(ExitCode.UNSUPPORTED_KEY_BACKEND, UnsupportedKeyBackendError, id="85-key-backend"),
]


@pytest.mark.parametrize(("code", "cls"), _SIGNING_EXIT_CODES)
def test_signing_error_declares_a_hint_of_its_own(code: ExitCode, cls: type[OcxProcessError]) -> None:
    # C-006: "three OcxProcessError subclasses (docstring + one `_hint` each)".
    # Inheriting the generic "inspect .stderr" hint hands the caller no remedy
    # at all — S-006 is that each of these failures names its own. Rendering the
    # message too, so an override that never reaches `__str__` still fails.
    assert cls._hint != OcxProcessError._hint
    assert str(cls(code, ["ocx", "package", "sign", "acme/tool:1"])).endswith(cls._hint)


def test_transparency_log_error_says_rekor_is_unreachable_not_untrusted() -> None:
    # S-006 / C-006 row 83. C-006's table originally said "supply an offline
    # bundle"; there is no such flag. A reader chasing it through `--help` finds
    # `--signature-format bundle` (a wire format, no help here), comes back to 83,
    # and the only remaining text that reads like a way past a missing log entry
    # is `--allow-unlogged-signature`. So the hint names the real, non-downgrading
    # remedy — `--rekor-url` at a reachable instance — and labels the tempting flag
    # as the trust downgrade it is rather than leaving the caller to infer it is
    # sanctioned.
    message = str(TransparencyLogUnavailableError(83, ["ocx", "package", "sign", "acme/tool:1"]))

    assert "Rekor" in message
    assert "not a claim that the signature is untrusted" in message
    assert "--rekor-url" in message
    assert "--allow-unlogged-signature" in message
    assert any(word in message for word in ("Retry", "try again", "later")), message


def test_referrers_error_names_the_registry_capability_that_is_missing() -> None:
    # S-006 / C-006 row 84. Both mechanisms by their spec names, because the
    # fallback is the half a caller can act on. The cause framing is asserted
    # too: the Referrers Tag Schema needs no server support, so "point at a
    # newer registry" is the wrong diagnosis — write-restricted or
    # misconfigured is the likely one.
    message = str(ReferrersUnsupportedError(84, ["ocx", "package", "push", "acme/tool:1"]))

    assert "Referrers API" in message
    assert "Referrers Tag Schema" in message
    assert any(word in message for word in ("write-restricted", "misconfigured")), message


def test_unsupported_key_backend_error_names_every_unimplemented_scheme() -> None:
    # S-006 / C-006 row 85: recognized but unimplemented, not misconfigured.
    # An unreadable trusted root exits 74, and the two remediations are
    # opposite — this hint must not send the caller looking at trust material.
    message = str(UnsupportedKeyBackendError(85, ["ocx", "package", "sign", "acme/tool:1"]))

    for scheme in ("file://", "env://", "awskms://", "gcpkms://", "azurekms://", "hashivault://", "k8s://"):
        assert scheme in message, f"{scheme} is one of ocx's recognized schemes; the hint must name it: {message}"
    assert "not implemented" in message
    assert "keyless" in message
    assert "trusted root" not in message


@pytest.mark.parametrize(("code", "cls"), _SIGNING_EXIT_CODES)
def test_signing_error_is_not_retryable(code: ExitCode, cls: type[OcxProcessError]) -> None:
    # D6 (regression lock): retrying Rekor-unavailable amplifies rate limiting
    # on `sign` and risks reading "eventually reached the log" as "trust
    # re-established" on `verify`. Callers opt in explicitly.
    assert cls(code, ["ocx", "package", "sign", "acme/tool:1"]).retryable is False


def test_version_compat_error_reports_both_bounds() -> None:
    err = VersionCompatError("0.5.8", "0.6.0")

    message = str(err)

    assert (err.found, err.minimum) == ("0.5.8", "0.6.0")
    assert "0.5.8" in message
    assert "0.6.0" in message
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
