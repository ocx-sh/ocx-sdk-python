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
    IoError,
    NotFoundError,
    Ocx,
    OcxProcessError,
    PolicyBlockedError,
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
            ExitCode.IO_ERR,
            IoError,
            lambda ocx, factory, tmp_path: ocx.invoke(["--project", str(tmp_path), "status"]),
            id="74-io-project-is-a-directory",
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


# Codes this tier cannot reach, and where they are covered instead:
#
#   1  FAILURE        — generic; ocx assigns it to spawn failures inside `run`.
#   69 UNAVAILABLE    — needs a registry that answers non-transiently; acceptance.
#   75 TEMP_FAIL      — needs a registry returning 429/5xx; acceptance + unit retry tests.
#   77 NO_PERM        — needs an unwritable $OCX_HOME; refused here as machine mutation.
#   80 AUTH           — needs the htpasswd registry; acceptance (`test_login_password_stdin`).
#   82 DIRTY_RC_BLOCK — needs a managed-config fence carrying local edits; acceptance.
