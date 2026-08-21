# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The OCX Authors

"""Process mechanics against a real child (design §10, §12, §14).

The unit tier drives all of this through a fake `Popen` and an injected
clock, which is what gets it to 100% coverage. That leaves one thing
unproven: that the seams describe a real process. These rows re-verify the
same mechanisms with nothing injected — a real timeout on a real hanging
child, real pump threads on a real pipe, real threads sharing one handle.
"""

from __future__ import annotations

import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING

import pytest

from ocx_sdk import HostEnv, Ocx, OcxConfig, OcxTimeoutError

from _helpers import project_file  # isort: skip  — sys.path is this directory under pytest

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from ocx_sdk import Project

_PROJECT = """
[tools]
"""

_TIMEOUT = 0.5
"""Short enough to keep the tier quick, long enough that ocx has started the child."""

_LINES = 500
"""Enough stderr to cross a pipe buffer, so a pump that reads once loses some."""

_THREADS = 8
_CALLS = 3


def test_timeout_kill_ladder(project_factory: Callable[..., Project]) -> None:
    """A hanging child is terminated at the deadline, not waited on.

    `sleep 60` under a half-second budget: the assertion is that control
    returns in roughly the budget rather than in a minute, which only holds
    if the ladder really signalled the session ocx's child was put in.
    """
    project = project_factory(_PROJECT)

    started = time.monotonic()
    with pytest.raises(OcxTimeoutError, match=f"timed out after {_TIMEOUT}s") as caught:
        project.run(["sh", "-c", "sleep 60"], timeout=_TIMEOUT)
    elapsed = time.monotonic() - started

    assert caught.value.timeout == _TIMEOUT
    assert elapsed < 30, f"the ladder did not reap the child: returned after {elapsed:.1f}s"


def test_pump_captures_all_lines(ocx_exe: Path, host_env: HostEnv, config: OcxConfig, tmp_path: Path) -> None:
    """Every stderr line a child writes reaches both `on_log` and the captured result.

    The pump runs on its own thread and `on_log` fires from it, so a line
    dropped here is a log line lost in production — the failure a fake pipe
    cannot reproduce, because a fake never fills.
    """
    collected: list[str] = []
    root = tmp_path / "pumped"
    root.mkdir()
    (root / "ocx.toml").write_text(_PROJECT, encoding="utf-8")
    project = Ocx(exe=ocx_exe, host_env=host_env, config=config, on_log=collected.append).project(project_file(root))
    project.lock()

    result = project.run(["sh", "-c", f"for i in $(seq 1 {_LINES}); do echo wp10-line-$i >&2; done"])

    expected = [f"wp10-line-{index}" for index in range(1, _LINES + 1)]
    assert [line for line in result.stderr.splitlines() if line.startswith("wp10-line-")] == expected
    assert [line.strip() for line in collected if line.startswith("wp10-line-")] == expected


def test_shared_handle_spawn_smoke(ocx: Ocx) -> None:
    """One handle, many threads, consistent answers — no state to race on.

    `Ocx` is frozen and cache-free apart from the compatibility memo, whose
    race is benign because both writers write the same answer. This drives
    that memo concurrently from a cold start.
    """
    with ThreadPoolExecutor(max_workers=_THREADS) as pool:
        answers = list(pool.map(lambda _: ocx.version(), range(_THREADS * _CALLS)))

    assert len(answers) == _THREADS * _CALLS
    assert set(answers) == {ocx.version()}


def test_spawn_returns_a_live_child(ocx: Ocx) -> None:
    """`spawn` hands back a real `Popen` the caller owns end to end."""
    with ocx.spawn(["version"], stdout=subprocess.PIPE, text=True) as child:
        stdout, _ = child.communicate()

    assert child.returncode == 0
    assert stdout.strip() == ocx.version()


def test_spawn_refuses_kwargs_the_sdk_owns(ocx: Ocx) -> None:
    """Argv, environment, and `shell=False` belong to the SDK, not the caller."""
    with pytest.raises(ValueError, match="cannot be set on a spawn"):
        ocx.spawn(["version"], shell=True)


@pytest.mark.skip(
    reason=(
        "SIGINT forwarding needs a SIGINT delivered to *this* process while a "
        "capture=False run is blocking. Doing that inside the test session means a "
        "KeyboardInterrupt escaping the SDK's handler tears down pytest itself, so this "
        "row is a permanent skip at this tier — real coverage is the unit tier's "
        "injected signal seam (tests/unit/test_process.py)."
    )
)
def test_sigint_forwarding() -> None:
    """Ctrl-C reaches the hosted child rather than only the SDK."""
    raise AssertionError("documented in the skip reason; never executed")
