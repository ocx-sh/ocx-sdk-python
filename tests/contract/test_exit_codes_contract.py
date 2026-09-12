# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The OCX Authors

"""Exit code to exception, driven by real failures (design §10, §14).

The exit code *is* the error category — the SDK never classifies by stderr
text — so the mapping is only as good as the codes ocx actually assigns. The
unit tier asserts the table; this file asserts the table describes reality, by
provoking each failure against the binary and catching what comes back.

Every row here is reachable **offline**: no registry, no network, no fixture
server. Codes that need one are named at the bottom of this module, so the
gap is a recorded decision rather than an oversight.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from ocx_sdk import (
    ConfigError,
    DataError,
    ExitCode,
    NotFoundError,
    Ocx,
    OcxProcessError,
    PolicyBlockedError,
    UnsupportedKeyBackendError,
    UsageError,
)

from _helpers import SMOKE_PACKAGE, project_file  # isort: skip  — sys.path is this directory under pytest

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from ocx_sdk import Project

_TOOLS_ONLY = """
[tools]
"""

_WITH_TOOL = """
[tools]
task = "ocx.sh/go-task/task:3"
"""

_UNRESOLVABLE = "ocx.sh/ocx-sdk-python/no-such-package:0.0.0"
"""An identifier no store can hold, so `--offline` refuses before any lookup."""

_KMS_KEY = "awskms://alias/ocx-sdk-python-has-no-such-key"
"""A key backend ocx recognizes by name and refuses (exit 85).

The scheme is checked before the reference is resolved, so no registry, no
network and no key material are needed to reach the code — which is what makes
85 the one of C-006's three that this tier can provoke.
"""


def _stale_lock(project_factory: Callable[..., Project]) -> Project:
    """Return a project whose `ocx.toml` moved on after its lock was written."""
    project = project_factory(_TOOLS_ONLY)
    project.path.write_text(_WITH_TOOL, encoding="utf-8")
    return project


@pytest.mark.parametrize(
    ("code", "error", "provoke"),
    [
        pytest.param(
            ExitCode.USAGE,
            UsageError,
            lambda ocx, factory, tmp_path: ocx.invoke(["--no-such-flag", "version"]),
            id="64-usage-unknown-flag",
        ),
        pytest.param(
            ExitCode.DATA_ERR,
            DataError,
            lambda ocx, factory, tmp_path: _stale_lock(factory).with_config(offline=True).inspect(),
            id="65-data-stale-lock",
        ),
        pytest.param(
            ExitCode.USAGE,
            UsageError,
            lambda ocx, factory, tmp_path: ocx.invoke(["--project", str(tmp_path), "status"]),
            id="64-usage-project-dir-without-toml",
        ),
        pytest.param(
            ExitCode.CONFIG,
            ConfigError,
            lambda ocx, factory, tmp_path: factory(_TOOLS_ONLY, lock=False).with_config(offline=True).inspect(),
            id="78-config-missing-lock",
        ),
        pytest.param(
            ExitCode.NOT_FOUND,
            NotFoundError,
            lambda ocx, factory, tmp_path: ocx.invoke(["--project", str(project_file(tmp_path)), "status"]),
            id="79-notfound-missing-ocx-toml",
        ),
        pytest.param(
            ExitCode.POLICY_BLOCKED,
            PolicyBlockedError,
            lambda ocx, factory, tmp_path: ocx.with_config(offline=True).package.inspect(_UNRESOLVABLE),
            id="81-policy-offline-uncached",
        ),
        pytest.param(
            ExitCode.UNSUPPORTED_KEY_BACKEND,
            UnsupportedKeyBackendError,
            lambda ocx, factory, tmp_path: ocx.package.sign(SMOKE_PACKAGE, key=_KMS_KEY),
            id="85-unsupported-key-backend",
        ),
    ],
)
def test_exit_code_taxonomy_fixtures(
    code: ExitCode,
    error: type[OcxProcessError],
    provoke: Callable[[Ocx, Callable[..., Project], Path], object],
    ocx: Ocx,
    project_factory: Callable[..., Project],
    tmp_path: Path,
) -> None:
    """Each provoked failure exits the documented code and raises its subclass.

    The parametrize table is the taxonomy: every id names the scenario that
    reaches the code, so an ocx that moves one — as 0.5.3 moved 69 to 75 —
    fails on the row that changed rather than somewhere downstream.
    """
    with pytest.raises(error, match=f"exited {int(code)}") as caught:
        provoke(ocx, project_factory, tmp_path)

    assert caught.value.exit_code == int(code)
    assert type(caught.value) is error


def test_process_error_reports_ocx_own_retryability(ocx: Ocx) -> None:
    """A real usage failure reports itself as not worth retrying.

    `retryable` is policy-independent by design: it says what ocx called the
    failure — only exit 75 is transient — not what a caller chose to do
    about it.
    """
    with pytest.raises(UsageError) as caught:
        ocx.invoke(["--no-such-flag", "version"])

    assert caught.value.exit_code != ExitCode.TEMP_FAIL
    assert caught.value.retryable is False


def test_status_reports_a_broken_lock_as_payload(project_factory: Callable[..., Project]) -> None:
    """`status` resolves nothing, so a stale lock is data on the report, not a failure.

    The one project-tier command that is exit-0-always; a caller inspecting a
    broken checkout must not have to catch an exception to see the state.
    """
    project = project_factory(_TOOLS_ONLY)
    project.path.write_text(_WITH_TOOL, encoding="utf-8")

    report = project.with_config(offline=True).status()

    assert report.lock.present is True
    assert report.lock.current is False


def test_package_inspect_reports_a_missing_package_when_online(ocx: Ocx) -> None:
    """With the registry reachable, an unknown identifier is 79, not the offline 81.

    Pins the pair: `--offline` shadows the real answer with a policy refusal,
    so the two codes have to be provoked separately to stay distinguishable.
    """
    with pytest.raises(NotFoundError, match="exited 79"):
        ocx.package.inspect(_UNRESOLVABLE)

    assert ocx.package.inspect(SMOKE_PACKAGE).packages


def test_consent_stamp_is_refused_by_default(project_factory: Callable[..., Project], tmp_path: Path) -> None:
    """A project-tier mutator leaves no `consent.json` behind unless asked to.

    ocx stamps `state/projects/<key>/consent.json` on every `add`/`lock`/
    `pull`/`exec`/`update`/`init` unless `OCX_NO_CONSENT` says otherwise, and
    the stamp is what lets the developer's next shell prompt activate the
    project. The SDK writes the variable either way; this is the binary
    agreeing that `"1"` refuses and `"0"` consents. A throwaway home per
    half, so the session store's other projects cannot color the answer.
    """
    project = project_factory(_TOOLS_ONLY, lock=False)
    refused = project.with_config(home=tmp_path / "refused")
    consented = project.with_config(home=tmp_path / "consented", consent=True)

    refused.lock()
    consented.lock()

    assert list((tmp_path / "refused").rglob("consent.json")) == []
    assert [stamp.name for stamp in (tmp_path / "consented").rglob("consent.json")] == ["consent.json"]


# Codes this tier cannot reach, and where they are covered instead:
#
#   1  FAILURE        — generic; ocx assigns it to spawn failures inside `run`.
#   69 UNAVAILABLE    — needs a registry that answers non-transiently; acceptance.
#   74 IO_ERR         — needs a real filesystem failure. Up to 0.6.0 a `--project`
#                      naming a directory (or a directory called `ocx.toml`)
#                      reached it; 0.6.1 answers both with 64, so the code is no
#                      longer provokable offline. Unit: `test_exit_code_maps_to_its_subclass`.
#   75 TEMP_FAIL      — needs a registry returning 429/5xx; acceptance + unit retry tests.
#   77 NO_PERM        — needs an unwritable $OCX_HOME; refused here as machine mutation.
#   80 AUTH           — needs the htpasswd registry; acceptance (`test_login_password_stdin`).
#   82 DIRTY_RC_BLOCK — needs a managed-config fence carrying local edits; acceptance.
#   83 TRANSPARENCY_LOG_UNAVAILABLE — Rekor is contacted only while a signature is
#                      being written; acceptance at best, and blocked by the key
#                      below.
#   84 REFERRERS_UNSUPPORTED — write-path only, by upstream design; unreachable at
#                      any tier for the reason below.
#
# 84 does not fall to the obvious probe. A registry with no Referrers API looks
# like the way to provoke it from the read side, and `registry:2` (distribution
# 2.8.3) is one — it answers 404 on `/v2/<name>/referrers/<digest>`. It does not
# work, and the reason is deliberate: `list_referrers_with_fallback`
# (`ocx_lib/src/oci/client/transport.rs:568-598`) turns that 404 into an empty
# listing tagged `DiscoveryMethod::FallbackTag`, and `verify`'s
# `map_client_error` (`ocx_lib/src/oci/verify/pipeline.rs:3524-3531`) maps the
# unsupported verdict onto `NoSignaturesFound` with the comment "84 is now
# write-path only". Probed against 2.8.3 at the tag: `verify` exits 79
# `no_signatures_found` and `sbom` exits 79 `attestation_not_found`, with and
# without `--no-cache`. On the write side 84 means "the Referrers API is absent
# **and** the fallback write was refused" (`ocx_lib/src/oci/sign/referrers.rs:86-89`).
#
# Which puts 83 and 84 behind the same door: both need a signing run, and ocx
# accepts exactly one private-key format — cosign's scrypt-wrapped
# `ENCRYPTED SIGSTORE PRIVATE KEY` envelope
# (`ocx_lib/src/oci/sign/key_backend.rs:130-135`). `openssl` cannot write one and
# neither can the standard library, so a throwaway key means adding `cosign` to
# the toolchain — a decision above this file. 85 is reachable only because its
# check runs before any key is read.
#
#   86 FORGE_CAPABILITY_UNAVAILABLE — raised only after a forge was reached and
#                      refused a job-token push; needs a live GitLab. Unit:
#                      `test_exit_code_maps_to_its_subclass`.
