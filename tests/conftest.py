# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The OCX Authors

"""Shared fixtures: the pinned-binary version seam (design §14).

`ocx_exe` is the one seam every tier that needs a real binary goes through.
Provisioning is `bootstrap.ensure()` itself — the SDK installs the ocx its own
contract tests run against, so a green contract run is also evidence the
bootstrap layer works.

`OCX_TEST_VERSIONS` parametrizes the fixture: a comma-separated list of
versions (or the literal `latest`, which the nightly canary sets). Unset means
one session against whatever `Ocx()` discovers — on CI the pinned
`TESTED_OCX_VERSION` that `setup-ocx` put on `PATH`.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pytest

from ocx_sdk import Ocx, bootstrap

if TYPE_CHECKING:
    from pathlib import Path

OCX_TEST_VERSIONS = "OCX_TEST_VERSIONS"
"""Comma-separated versions to run a binary-backed tier against."""

LATEST = "latest"
"""The `OCX_TEST_VERSIONS` entry meaning "whatever the stable channel has"."""


def _requested_versions() -> list[str | None]:
    """Read the version seam, defaulting to the discovered binary."""
    raw = os.environ.get(OCX_TEST_VERSIONS, "").strip()
    if not raw:
        return [None]
    return [part.strip() for part in raw.split(",") if part.strip()]


@pytest.fixture(scope="session", params=_requested_versions(), ids=lambda version: version or "discovered")
def ocx_exe(request: pytest.FixtureRequest, tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Return the ocx binary this session runs against.

    `None` (the default param) discovers one the way a consumer would; any
    other value provisions it through `bootstrap.ensure()` into a cache that
    lives and dies with the session.
    """
    wanted: str | None = request.param
    if wanted is None:
        return Ocx().exe
    return bootstrap.ensure(
        version=None if wanted == LATEST else wanted,
        cache_dir=tmp_path_factory.mktemp("ocx-dist"),
    )
