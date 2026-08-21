# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The OCX Authors

"""S-002: a real project's `[env]`, composed, activated, and reverted.

End to end with nothing faked: ocx reads a project off disk and reports what
its `[env]` contributes, the SDK folds those entries onto the environment the
report was produced under, `activate()` puts the result on `os.environ`, and
an ordinary child process — one that knows nothing about ocx — sees it.

The revert is the half that is easy to get wrong and expensive to get wrong:
`activate()` records only the keys it is about to touch, restores their prior
values on the way out, and deletes the ones that were not set before. A key
the block itself introduced is none of its business and survives.

This row needs a real binary and a real project, but no registry — the
composition never leaves the machine. It is here rather than in the contract
tier because what it asserts is the user's journey, not the SDK's model of
ocx: the contract tier already diffs the fold against `ocx run -- printenv`.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from typing import TYPE_CHECKING

import pytest

from ocx_sdk import HostEnv, Ocx

if TYPE_CHECKING:
    from pathlib import Path

    from ocx_sdk import OcxConfig, Project

_PROJECT = """
[tools]

[env]
WP13_CONST = "composed"
WP13_PATH = { type = "path", value = "/opt/wp13/bin" }
WP13_CSV = { type = "list", value = "beta", separator = "," }
"""
"""One entry per fold: replace, prepend, and append with a declared separator.

The declared separator is the load-bearing one. A list entry that leaves it
out folds with a space, so a project that appends to a comma-separated
variable is exactly where a wrong default would corrupt the value.
"""

_PARENT = {
    # Set in the environment the report is produced under, so every fold has
    # something to fold onto rather than composing from nothing.
    "WP13_PATH": "/usr/local/wp13/bin",
    "WP13_CSV": "alpha",
}

_WATCHED = ("WP13_CONST", "WP13_PATH", "WP13_CSV")
"""The keys the child is asked about — the three the project declares."""

_PROBE = "import json, os, sys; print(json.dumps({k: os.environ.get(k) for k in sys.argv[1:]}))"
"""Report the named variables as JSON. Keys arrive as argv, not baked into the source."""


@pytest.fixture
def env_ocx(ocx_exe: Path, host_env: HostEnv, config: OcxConfig) -> Ocx:
    """A handle whose environment already carries the values the project folds onto."""
    return Ocx(exe=ocx_exe, host_env=HostEnv({**host_env.source, **_PARENT}), config=config)


@pytest.fixture
def project(env_ocx: Ocx, tmp_path: Path) -> Project:
    """A locked throwaway project declaring the three `[env]` entries."""
    root = tmp_path / "project"
    root.mkdir()
    (root / "ocx.toml").write_text(_PROJECT, encoding="utf-8")
    handle = env_ocx.project(root)
    handle.lock()
    return handle


def _child_view() -> dict[str, str | None]:
    """Return what a plain child process sees for the watched keys.

    A subprocess rather than a read of `os.environ`: `activate()` promises the
    composed values reach whatever the process launches, and only a real child
    can testify to that.
    """
    done = subprocess.run(
        (sys.executable, "-c", _PROBE, *_WATCHED),
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )
    return json.loads(done.stdout)


def test_project_env_composes_the_declared_folds(project: Project) -> None:
    """Each `[env]` type folds onto the producing environment the way it declares."""
    composed = project.env().compose().mapping

    assert composed["WP13_CONST"] == "composed"
    assert composed["WP13_PATH"] == "/opt/wp13/bin:/usr/local/wp13/bin"
    assert composed["WP13_CSV"] == "alpha,beta"


def test_activate_reaches_a_child_and_reverts(project: Project, monkeypatch: pytest.MonkeyPatch) -> None:
    """A child sees the composed values inside the block, and the process env survives it.

    The process environment deliberately disagrees with the composed one going
    in: `WP13_CSV` holds an ambient value that is not what the fold produced,
    and `WP13_CONST` is not set at all. Those are the two revert cases —
    restore a prior value, delete a key that had none.
    """
    monkeypatch.setenv("WP13_CSV", "ambient")
    monkeypatch.delenv("WP13_CONST", raising=False)
    composed = project.env().compose()

    with composed.activate():
        seen = _child_view()
        monkeypatch.setenv("WP13_UNRELATED", "set-inside-the-block")

    assert seen == {
        "WP13_CONST": "composed",
        "WP13_PATH": "/opt/wp13/bin:/usr/local/wp13/bin",
        "WP13_CSV": "alpha,beta",
    }
    assert os.environ["WP13_CSV"] == "ambient"
    assert "WP13_CONST" not in os.environ
    assert os.environ["WP13_UNRELATED"] == "set-inside-the-block"
