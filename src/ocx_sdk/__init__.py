# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The OCX Authors

"""Public API for `ocx-sdk`.

Top-level imports are the only stable contract. Everything reachable via
underscored module paths is package-private and may change without notice.
"""

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version


def _resolve_version() -> str:
    """Return the installed package version, or a fallback when running from source."""
    try:
        return _pkg_version("ocx-sdk")
    except PackageNotFoundError:
        return "0.0.0+unknown"


__version__ = _resolve_version()

__all__ = [
    "__version__",
]
