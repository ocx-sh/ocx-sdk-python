# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The OCX Authors

"""Contract tier: the real binary, throwaway state, no fakes (design §14).

These tests are re-verification, never the coverage vehicle — every mechanism
here is already covered at the unit tier through injected seams. What the
contract tier adds is the one thing a fake cannot: agreement with the binary.
A row that goes red is a statement about ocx, or about the SDK's model of it,
not about a mocking mistake.

Everything is gated on `OCX_SDK_CONTRACT=1` (`task test:contract`) and skips —
never fails — without it, so `task test` stays a pure unit run.

Hermetic by construction: a throwaway `$OCX_HOME`, a throwaway
`DOCKER_CONFIG` (the ambient one can carry a credential helper that turns an
offline `logout` into an exit 80), projects under `tmp_path`, and an explicit
`HostEnv` rather than the ambient environment.
"""

from __future__ import annotations

import itertools
import json
import os
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ocx_sdk import HostEnv, Ocx, OcxConfig

from _helpers import project_file  # isort: skip  — sys.path is this directory under pytest

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from ocx_sdk import Project

CONTRACT_GATE = "OCX_SDK_CONTRACT"
"""Environment flag that arms this tier; `task test:contract` sets it."""

_HERE = Path(__file__).parent
_SLUG_FIXTURES = _HERE.parent / "fixtures" / "slug_fixtures.json"
_SKIP = pytest.mark.skip(reason=f"contract tier: set {CONTRACT_GATE}=1 (`task test:contract`)")
_SERIAL = itertools.count()


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Skip this directory's tests unless the contract gate is armed.

    Scoped by path rather than applied to everything pytest collected: the
    hook is session-wide, and a unit run must keep its own items untouched.
    """
    if os.environ.get(CONTRACT_GATE) == "1":
        return
    for item in items:
        if _HERE in Path(item.path).parents:
            item.add_marker(_SKIP)


@pytest.fixture(scope="session")
def ocx_home(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A throwaway `$OCX_HOME` shared by the whole contract session.

    Session-scoped on purpose: the store is content-addressed, so one download
    of the smoke package serves every test that needs it, and nothing here
    touches the developer's real `~/.ocx`.
    """
    return tmp_path_factory.mktemp("ocx-home")


@pytest.fixture
def host_env() -> HostEnv:
    """The parent environment a contract spawn inherits.

    `minimal()` keeps `PATH`, `HOME`, and `TMPDIR` — enough for ocx to find
    the tools a child needs, little enough that an ambient `OCX_*` from the
    developer's shell cannot reach the assertions.
    """
    return HostEnv.minimal()


@pytest.fixture
def config(ocx_home: Path, tmp_path: Path) -> OcxConfig:
    """Session policy: throwaway store, throwaway credential store."""
    docker = tmp_path / "docker"
    docker.mkdir(exist_ok=True)
    return OcxConfig(home=ocx_home, docker_config=docker)


@pytest.fixture
def ocx(ocx_exe: Path, host_env: HostEnv, config: OcxConfig) -> Ocx:
    """A handle on the pinned binary, pointed at throwaway state."""
    return Ocx(exe=ocx_exe, host_env=host_env, config=config)


@pytest.fixture(scope="session")
def slug_corpus() -> list[Mapping[str, str]]:
    """The `registry_slug` corpus WP00 extracted from the ocx source at the tested tag.

    The same file WP06's unit tests read. Here it drives the other half of the
    carve-out: that the names it predicts are the ones a spawned child sees.
    """
    return json.loads(_SLUG_FIXTURES.read_text(encoding="utf-8"))["cases"]


@pytest.fixture
def project_factory(ocx: Ocx, tmp_path: Path) -> Callable[..., Project]:
    """Return a factory building locked throwaway projects from `ocx.toml` bodies.

    The factory takes the file contents and, optionally, `lock=False` to leave
    the project without a lockfile — which is how the exit 78 row is driven.
    """

    def make(body: str, *, lock: bool = True) -> Project:
        root = tmp_path / f"project-{next(_SERIAL)}"
        root.mkdir()
        (root / "ocx.toml").write_text(body, encoding="utf-8")
        project = ocx.project(project_file(root))
        if lock:
            project.lock()
        return project

    return make
