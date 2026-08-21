# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The OCX Authors

"""`bootstrap.ensure()` against the real dist host (design §5, §14).

The bootstrap layer's whole promise is that a consumer with no ocx installed
can get a verified one. Every other test in this repo runs against a binary
somebody else provisioned, so nothing else would notice that promise breaking
— and it is the one path that talks to a host the SDK does not control.

Network is expected here: the contract job has it, and the version seam
(`OCX_TEST_VERSIONS`) provisions through this same function.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from ocx_sdk import TESTED_OCX_VERSION, BootstrapError, Ocx, bootstrap

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def cache_dir(tmp_path: Path) -> Path:
    """A cache root `ensure()` will accept: ours, private, and not a symlink."""
    root = tmp_path / "dist-cache"
    root.mkdir(mode=0o700)
    return root


def test_ensure_installs_real_binary(cache_dir: Path) -> None:
    """`ensure()` provisions a runnable ocx of the version it was asked for.

    The end-to-end bootstrap contract: resolve the manifest, pick this
    platform's release, verify the sha256, extract the single member, and
    hand back a `0o700` binary that answers `version`.
    """
    installed = bootstrap.ensure(version=TESTED_OCX_VERSION, cache_dir=cache_dir)

    assert installed.is_file()
    assert installed.stat().st_mode & 0o777 == 0o700
    assert Ocx(exe=installed).version() == TESTED_OCX_VERSION


def test_ensure_is_idempotent_and_offline_on_a_warm_cache(cache_dir: Path) -> None:
    """A second `ensure()` re-hashes the cached artifact instead of re-downloading.

    Cache hits are re-verified by default (`trust_cache=False`), so this also
    pins that the stored bytes still match the manifest digest.
    """
    first = bootstrap.ensure(version=TESTED_OCX_VERSION, cache_dir=cache_dir)

    second = bootstrap.ensure(version=TESTED_OCX_VERSION, cache_dir=cache_dir)

    assert second == first
    assert Ocx(exe=second).version() == TESTED_OCX_VERSION


def test_ensure_refuses_a_cache_root_it_does_not_trust(tmp_path: Path) -> None:
    """A world-writable cache root is refused before any byte is fetched.

    Fail-closed and pre-network: the cache is trusted persistent state, so a
    root somebody else can write through would let them choose the binary.
    """
    exposed = tmp_path / "exposed"
    exposed.mkdir()
    exposed.chmod(0o777)  # `mode=` on mkdir goes through the umask; chmod does not.

    with pytest.raises(BootstrapError, match="writable"):
        bootstrap.ensure(version=TESTED_OCX_VERSION, cache_dir=exposed)


def test_discovery_finds_the_binary_the_session_runs(ocx_exe: Path) -> None:
    """`Ocx()` with no `exe=` resolves a binary, and the handle pins it.

    Discovery walks `exe=`, `OCX_SDK_EXE`, `PATH`, then ocx's own install
    symlink — the last of which is a durable anchor. Which rung answers
    depends on the machine, so the assertion is that one of them does and
    that the result is the frozen, resolved path.
    """
    handle = Ocx(exe=ocx_exe)

    assert handle.exe == ocx_exe.resolve()
    assert handle.exe.is_file()
    assert Ocx().exe.is_file()
