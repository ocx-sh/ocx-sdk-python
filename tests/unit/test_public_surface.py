# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The OCX Authors

"""Contract tests for the public surface (C-012).

`ocx_sdk/__init__.py` is the only import path the SDK guarantees, so the
export list is a contract in its own right: `_EXPECTED` below is that
contract written down, and changing the surface means changing both.

This file also owns the zero-runtime-dependency gates from §11 — the
metadata half (`dependencies = []`) and the import-walk half (importing the
package pulls in nothing outside the standard library).
"""

from __future__ import annotations

import inspect
import subprocess
import sys
import textwrap
from importlib import metadata

import pytest

import ocx_sdk
import ocx_sdk.bootstrap

_EXPECTED = {
    # Handles and command groups.
    "Ocx", "Project", "PackageCommands", "ConfigCommands", "PatchCommands",
    # Configuration and environment.
    "OcxConfig", "ConfigOverrides", "HostEnv", "ComposedEnv",
    # Shared vocabulary.
    "Auth", "BasicAuth", "BearerAuth", "PackageRef", "PackageLike", "RetryPolicy",
    "ConstVar", "PathVar", "ListVar", "EnvValue", "Channel", "InstallEnv",
    "LogLevel", "LazyMode", "Resolve", "MaybeRetry", "MaybeTimeout", "UNSET",
    # Bootstrap.
    "bootstrap", "ensure", "DistSource",
    # Constants.
    "TESTED_OCX_VERSION", "MIN_SUPPORTED", "MANAGED_CONFIG_DISABLED", "__version__",
    # Results.
    "CommandResult", "VersionInfo", "AboutInfo", "LockStatus", "ToolBinding", "GroupStatus",
    "StatusReport", "Candidate", "InspectedPackage", "InspectReport", "EnvEntry", "EnvEntryType",
    "PackageBinding", "Integration", "Advisory", "EnvReport", "WhichResult", "PullReport",
    "InstalledPackage", "InstallReport", "RemovalResult", "DepNode", "DepsReport", "ToolRow",
    "Assertion", "TestRun", "TestResult", "PushResult", "LoginResult", "LogoutResult",
    "ConfigUpdateReport", "ConfigSetupReport", "InfoResult",
    # Errors.
    "ExitCode", "OcxError", "OcxExecutionError", "OcxProcessError", "OcxTimeoutError",
    "UsageError", "DataError", "UnavailableError", "IoError", "TempFailError",
    "PermissionDeniedError", "ConfigError", "NotFoundError", "AuthError", "PolicyBlockedError",
    "DirtyRcBlockError", "BootstrapError", "DownloadError", "ChecksumMismatchError",
    "DistManifestError", "UnsupportedPlatformError", "OcxNotFoundError", "VersionCompatError",
}  # fmt: skip
"""The curated public surface, grouped the way the design record groups it."""

_PRIVATE_NAMES = [
    "SpawnEnv",
    "Completed",
    "compose_argv",
    "run_command",
    "spawn",
    "merge",
    "serialize_env_value",
    "discover",
    "uname_to_triple",
    "DistManifest",
    "Release",
]
"""Package-internal names that exist but must never become public API."""


def test_all_matches_the_curated_surface() -> None:
    assert set(ocx_sdk.__all__) == _EXPECTED


def test_all_has_no_duplicates() -> None:
    assert len(ocx_sdk.__all__) == len(set(ocx_sdk.__all__))


def test_every_exported_name_resolves() -> None:
    missing = [name for name in ocx_sdk.__all__ if not hasattr(ocx_sdk, name)]

    assert missing == []


def test_no_underscored_name_is_exported() -> None:
    leaked = [name for name in ocx_sdk.__all__ if name.startswith("_") and name != "__version__"]

    assert leaked == []


@pytest.mark.parametrize("name", _PRIVATE_NAMES)
def test_package_internals_stay_private(name: str) -> None:
    assert name not in ocx_sdk.__all__


def test_every_exported_class_is_documented() -> None:
    undocumented = [
        name
        for name in ocx_sdk.__all__
        if inspect.isclass(getattr(ocx_sdk, name))
        and getattr(ocx_sdk, name).__module__.startswith("ocx_sdk")
        and not getattr(ocx_sdk, name).__doc__
    ]

    assert undocumented == []


def test_bootstrap_is_the_one_public_submodule() -> None:
    assert ocx_sdk.bootstrap.__all__ == ["DistSource", "ensure"]
    assert ocx_sdk.bootstrap.ensure is ocx_sdk.ensure
    assert ocx_sdk.bootstrap.DistSource is ocx_sdk.DistSource


def test_the_distribution_declares_no_runtime_dependencies() -> None:
    runtime = [requirement for requirement in (metadata.requires("ocx-sdk") or []) if "extra ==" not in requirement]

    assert runtime == []


def test_importing_the_sdk_pulls_in_nothing_outside_the_stdlib() -> None:
    probe = textwrap.dedent(
        """
        import sys

        before = set(sys.modules)
        import ocx_sdk
        import ocx_sdk.bootstrap

        roots = {name.split(".")[0] for name in set(sys.modules) - before}
        print(sorted(roots - set(sys.stdlib_module_names) - {"ocx_sdk"}))
        """
    )

    walk = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, check=True)

    assert walk.stdout.strip() == "[]"
