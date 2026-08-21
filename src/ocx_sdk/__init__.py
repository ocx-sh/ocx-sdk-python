# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The OCX Authors

"""Python SDK for [OCX](https://github.com/ocx-sh/ocx).

`ocx-sdk` drives the ocx binary rather than reimplementing it: ocx owns
resolution, verification, and the identifier grammar, and this package gives
you typed, CWD-independent handles over the commands it exposes.

```python
from ocx_sdk import Ocx, bootstrap

ocx = Ocx(exe=bootstrap.ensure())
project = ocx.project("/srv/build")
project.pull()
project.run(["task", "verify"])
```

**This module is the API.** Everything listed in `__all__` is the stable
surface; every other module is underscored and package-private, and the one
public submodule is `ocx_sdk.bootstrap`. Reaching into an underscored path
means the next release may move it without notice — pre-1.0, breaking
changes ship without shims.

Start at `Ocx` for the runtime API and `bootstrap.ensure` for provisioning.
"""

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version

from . import bootstrap
from ._client import (
    UNSET,
    ConfigCommands,
    LazyMode,
    MaybeRetry,
    MaybeTimeout,
    Ocx,
    PackageCommands,
    PatchCommands,
    Project,
    Resolve,
)
from ._config import ConfigOverrides, OcxConfig
from ._envmodel import ComposedEnv
from ._errors import (
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
from ._results import (
    AboutInfo,
    Advisory,
    Assertion,
    Candidate,
    CommandResult,
    ConfigSetupReport,
    ConfigUpdateReport,
    DepNode,
    DepsReport,
    EnvEntry,
    EnvEntryType,
    EnvReport,
    GroupStatus,
    InfoResult,
    InspectedPackage,
    InspectReport,
    InstalledPackage,
    InstallReport,
    Integration,
    LockStatus,
    LoginResult,
    LogoutResult,
    PackageBinding,
    PullReport,
    PushResult,
    RemovalResult,
    StatusReport,
    TestResult,
    TestRun,
    ToolBinding,
    ToolRow,
    VersionInfo,
    WhichResult,
)
from ._types import (
    MANAGED_CONFIG_DISABLED,
    MIN_SUPPORTED,
    TESTED_OCX_VERSION,
    Auth,
    BasicAuth,
    BearerAuth,
    Channel,
    ConstVar,
    EnvValue,
    HostEnv,
    InstallEnv,
    ListVar,
    LogLevel,
    PackageLike,
    PackageRef,
    PathVar,
    RetryPolicy,
)
from .bootstrap import DistSource, ensure


def _resolve_version() -> str:
    """Return the installed package version, or a fallback when running from source."""
    try:
        return _pkg_version("ocx-sdk")
    except PackageNotFoundError:
        return "0.0.0+unknown"


__version__ = _resolve_version()

__all__ = [
    "MANAGED_CONFIG_DISABLED",
    "MIN_SUPPORTED",
    "TESTED_OCX_VERSION",
    "UNSET",
    "AboutInfo",
    "Advisory",
    "Assertion",
    "Auth",
    "AuthError",
    "BasicAuth",
    "BearerAuth",
    "BootstrapError",
    "Candidate",
    "Channel",
    "ChecksumMismatchError",
    "CommandResult",
    "ComposedEnv",
    "ConfigCommands",
    "ConfigError",
    "ConfigOverrides",
    "ConfigSetupReport",
    "ConfigUpdateReport",
    "ConstVar",
    "DataError",
    "DepNode",
    "DepsReport",
    "DirtyRcBlockError",
    "DistManifestError",
    "DistSource",
    "DownloadError",
    "EnvEntry",
    "EnvEntryType",
    "EnvReport",
    "EnvValue",
    "ExitCode",
    "GroupStatus",
    "HostEnv",
    "InfoResult",
    "InspectReport",
    "InspectedPackage",
    "InstallEnv",
    "InstallReport",
    "InstalledPackage",
    "Integration",
    "IoError",
    "LazyMode",
    "ListVar",
    "LockStatus",
    "LogLevel",
    "LoginResult",
    "LogoutResult",
    "MaybeRetry",
    "MaybeTimeout",
    "NotFoundError",
    "Ocx",
    "OcxConfig",
    "OcxError",
    "OcxExecutionError",
    "OcxNotFoundError",
    "OcxProcessError",
    "OcxTimeoutError",
    "PackageBinding",
    "PackageCommands",
    "PackageLike",
    "PackageRef",
    "PatchCommands",
    "PathVar",
    "PermissionDeniedError",
    "PolicyBlockedError",
    "Project",
    "PullReport",
    "PushResult",
    "RemovalResult",
    "Resolve",
    "RetryPolicy",
    "StatusReport",
    "TempFailError",
    "TestResult",
    "TestRun",
    "ToolBinding",
    "ToolRow",
    "UnavailableError",
    "UnsupportedPlatformError",
    "UsageError",
    "VersionCompatError",
    "VersionInfo",
    "WhichResult",
    "__version__",
    "bootstrap",
    "ensure",
]
