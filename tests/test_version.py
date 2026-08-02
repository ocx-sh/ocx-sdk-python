# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The OCX Authors

"""Smoke test for the package-level ``__version__`` attribute.

The value is resolved at import time via :func:`importlib.metadata.version`,
so this test guards against the lookup name drifting away from the
``[project] name`` declared in ``pyproject.toml``.
"""

from __future__ import annotations

import re
from importlib.metadata import PackageNotFoundError

import pytest

import ocx_sdk


def test_version_is_non_empty_semver_prefix() -> None:
    assert isinstance(ocx_sdk.__version__, str)
    assert ocx_sdk.__version__, "__version__ must be a non-empty string"
    assert re.match(r"^\d+\.\d+\.\d+", ocx_sdk.__version__), (
        f"__version__ must start with X.Y.Z, got: {ocx_sdk.__version__!r}"
    )


def test_version_exported_in_all() -> None:
    assert "__version__" in ocx_sdk.__all__


def test_resolve_version_falls_back_when_metadata_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise(name: str) -> str:
        raise PackageNotFoundError(name)

    monkeypatch.setattr(ocx_sdk, "_pkg_version", _raise)
    assert ocx_sdk._resolve_version() == "0.0.0+unknown"
