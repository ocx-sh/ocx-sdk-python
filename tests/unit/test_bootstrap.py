# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The OCX Authors

"""Specification for `_bootstrap` (contract C-006, design §5).

Three properties carry the module and most of this file: the cache is only
trusted after it has been re-verified, an archive never gets to name a path on
disk, and the platform mapping is a pure function so the whole table runs on
every operating system.

Nothing here reaches the network. The manifest arrives through
`DistSource.data()` — the real parse and validation path — and the artifact
transport is replaced by `_FakeFetch`, which writes the archive bytes a test
built into the descriptor `ensure()` opened.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import platform
import re
import stat
import tarfile
import zipfile
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import pytest

from ocx_sdk import _dist
from ocx_sdk._bootstrap import (
    CACHE_DIR_NAME,
    INSTALL_SYMLINK,
    OCX_HOME,
    OCX_SDK_EXE,
    _default_cache_dir,
    _extract_member,
    _translated_by_rosetta,
    _verify_root,
    _version_key,
    _which,
    discover,
    ensure,
    uname_to_triple,
)
from ocx_sdk._dist import DistSource
from ocx_sdk._errors import (
    BootstrapError,
    ChecksumMismatchError,
    DistManifestError,
    DownloadError,
    OcxNotFoundError,
    UnsupportedPlatformError,
)
from ocx_sdk._types import Channel, HostEnv, InstallEnv, RetryPolicy

_POSIX = os.name != "nt"
_EXE = "ocx" if _POSIX else "ocx.exe"
_BINARY = b"#!/bin/sh\nexit 0\n"
_TARGETS = (
    "x86_64-unknown-linux-musl",
    "aarch64-unknown-linux-musl",
    "x86_64-unknown-linux-gnu",
    "aarch64-unknown-linux-gnu",
    "x86_64-apple-darwin",
    "aarch64-apple-darwin",
    "x86_64-pc-windows-msvc",
    "aarch64-pc-windows-msvc",
)


def _sha(raw: bytes) -> str:
    """Return a digest the way the manifest spells it."""
    return hashlib.sha256(raw).hexdigest()


def _tar_archive(
    entries: Sequence[tuple[str, bytes]] = (("ocx", _BINARY),),
    *,
    mode: Literal["w:gz", "w:xz"] = "w:gz",
    symlinks: Sequence[tuple[str, str]] = (),
    directories: Sequence[str] = (),
) -> bytes:
    """Return a tar archive holding exactly the entries a test asks for."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode=mode) as tar:
        for name in directories:
            info = tarfile.TarInfo(name)
            info.type = tarfile.DIRTYPE
            tar.addfile(info)
        for name, link_target in symlinks:
            info = tarfile.TarInfo(name)
            info.type = tarfile.SYMTYPE
            info.linkname = link_target
            tar.addfile(info)
        for name, payload in entries:
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            tar.addfile(info, io.BytesIO(payload))
    return buffer.getvalue()


def _zip_archive(
    entries: Sequence[tuple[str, bytes]] = (("ocx.exe", _BINARY),),
    *,
    symlinks: Sequence[tuple[str, str]] = (),
    directories: Sequence[str] = (),
) -> bytes:
    """Return a zip archive holding exactly the entries a test asks for."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name in directories:
            archive.writestr(f"{name}/", b"")
        for name, link_target in symlinks:
            info = zipfile.ZipInfo(name)
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(info, link_target)
        for name, payload in entries:
            archive.writestr(name, payload)
    return buffer.getvalue()


def _host_target() -> str:
    """Return the triple `ensure()` resolves on the machine running the tests."""
    system = platform.system()
    return uname_to_triple(system, platform.machine(), rosetta=_translated_by_rosetta(system))


def _filename(target: str, version: str) -> str:
    """Return the archive name the manifest publishes for a target."""
    suffix = "zip" if target.endswith("windows-msvc") else "tar.gz"
    return f"ocx-{target}-{version}.{suffix}"


def _manifest_body(
    *,
    tar_sha: str,
    zip_sha: str,
    versions: Sequence[str] = ("0.5.8",),
    latest: str | None = "0.5.8",
    latest_next: str | None = None,
    targets: Sequence[str] = _TARGETS,
) -> bytes:
    """Return a manifest body covering every target, for the given versions."""
    releases = [
        {
            "version": version,
            "channel": "next" if version == latest_next else "stable",
            "tag": f"v{version}",
            "target": target,
            "filename": _filename(target, version),
            "sha256": zip_sha if target.endswith("windows-msvc") else tar_sha,
            "url": f"https://cdn.example/v{version}/{_filename(target, version)}",
        }
        for version in versions
        for target in targets
    ]
    envelope: dict[str, object] = {
        "latest": None if latest is None else {"channel": "stable", "version": latest},
        "latest_next": None if latest_next is None else {"channel": "next", "version": latest_next},
        "releases": releases,
        "schema": 1,
    }
    return json.dumps(envelope).encode()


class _FakeFetch:
    """Stand-in for `_dist.fetch_artifact` that writes bytes a test chose."""

    def __init__(self, payload: bytes) -> None:
        self.payload = payload
        self.calls: list[str] = []

    def __call__(self, url: str, fd: int, **kwargs: object) -> None:
        self.calls.append(url)
        os.write(fd, self.payload)


class _ExplodingFetch:
    """Stand-in that fails the test if the network is touched at all."""

    def __call__(self, url: str, fd: int, **kwargs: object) -> None:
        raise AssertionError(f"unexpected download of {url}")


@dataclass
class _Installer:
    """Runs `ensure()` against a fake artifact transport."""

    fetch: _FakeFetch
    tar_bytes: bytes = field(default_factory=_tar_archive)
    zip_bytes: bytes = field(default_factory=_zip_archive)

    def body(self, **overrides: object) -> bytes:
        """Return a manifest body matching the archives this installer serves."""
        return _manifest_body(tar_sha=_sha(self.tar_bytes), zip_sha=_sha(self.zip_bytes), **overrides)  # pyright: ignore[reportArgumentType]

    def __call__(self, cache_dir: Path, *, manifest: bytes | None = None, **kwargs: object) -> Path:
        """Install into `cache_dir` from a manifest the caller may override."""
        version = kwargs.pop("version", None)
        env = kwargs.pop("env", HostEnv({}))
        source = DistSource.data(self.body() if manifest is None else manifest)
        return ensure(version, dist=source, cache_dir=cache_dir, env=env, **kwargs)  # pyright: ignore[reportArgumentType, reportCallIssue]


@pytest.fixture
def install(monkeypatch) -> _Installer:
    """Return an installer whose artifact transport writes canned archives."""
    tar_bytes, zip_bytes = _tar_archive(), _zip_archive()
    payload = zip_bytes if _host_target().endswith("windows-msvc") else tar_bytes
    fetch = _FakeFetch(payload)
    monkeypatch.setattr(_dist, "fetch_artifact", fetch)
    return _Installer(fetch, tar_bytes, zip_bytes)


def _artifact_url(version: str = "0.5.8", mirror: str | None = None) -> str:
    """Return the URL `ensure()` should pull this host's archive from."""
    filename = _filename(_host_target(), version)
    base = f"https://cdn.example/v{version}" if mirror is None else f"{mirror}/v{version}"
    return f"{base}/{filename}"


# --- platform mapping -----------------------------------------------------


@pytest.mark.parametrize(
    ("system", "machine", "libc_hint", "rosetta", "expected"),
    [
        pytest.param("Linux", "x86_64", None, False, "x86_64-unknown-linux-musl", id="linux-x86_64-musl-first"),
        pytest.param("Linux", "amd64", None, False, "x86_64-unknown-linux-musl", id="linux-amd64-alias"),
        pytest.param("Linux", "aarch64", None, False, "aarch64-unknown-linux-musl", id="linux-aarch64-musl-first"),
        pytest.param("Linux", "arm64", None, False, "aarch64-unknown-linux-musl", id="linux-arm64-alias"),
        pytest.param("Linux", "x86_64", "gnu", False, "x86_64-unknown-linux-gnu", id="linux-x86_64-gnu-hint"),
        pytest.param("Linux", "aarch64", "gnu", False, "aarch64-unknown-linux-gnu", id="linux-aarch64-gnu-hint"),
        pytest.param("Linux", "x86_64", "musl", False, "x86_64-unknown-linux-musl", id="linux-explicit-musl-hint"),
        pytest.param("Darwin", "arm64", None, False, "aarch64-apple-darwin", id="macos-arm64"),
        pytest.param("Darwin", "x86_64", None, False, "x86_64-apple-darwin", id="macos-intel"),
        pytest.param("Darwin", "x86_64", None, True, "aarch64-apple-darwin", id="macos-rosetta-redirects-to-native"),
        pytest.param("Darwin", "arm64", None, True, "aarch64-apple-darwin", id="macos-arm64-ignores-rosetta"),
        pytest.param("Windows", "AMD64", None, False, "x86_64-pc-windows-msvc", id="windows-amd64"),
        pytest.param("Windows", "ARM64", None, False, "aarch64-pc-windows-msvc", id="windows-arm64"),
    ],
)
def test_triple_mapping(system: str, machine: str, libc_hint: str | None, rosetta: bool, expected: str):
    assert uname_to_triple(system, machine, libc_hint=libc_hint, rosetta=rosetta) == expected


@pytest.mark.parametrize(
    ("system", "machine"),
    [
        pytest.param("Linux", "riscv64", id="unknown-machine"),
        pytest.param("Linux", "i686", id="32-bit-x86"),
        pytest.param("FreeBSD", "x86_64", id="unknown-system"),
        pytest.param("Darwin", "ppc", id="unknown-mac"),
        pytest.param("Windows", "x86", id="32-bit-windows"),
    ],
)
def test_triple_mapping_refuses_unsupported_platforms(system: str, machine: str):
    with pytest.raises(UnsupportedPlatformError, match=machine):
        uname_to_triple(system, machine)


def test_every_mapped_triple_is_a_published_target():
    mapped = {
        uname_to_triple(system, machine, libc_hint=hint)
        for system, machine in (("Linux", "x86_64"), ("Linux", "aarch64"))
        for hint in (None, "gnu")
    }
    mapped |= {uname_to_triple("Darwin", machine) for machine in ("x86_64", "arm64")}
    mapped |= {uname_to_triple("Windows", machine) for machine in ("AMD64", "ARM64")}

    assert mapped == set(_TARGETS)


def test_rosetta_probe_is_false_off_macos():
    assert _translated_by_rosetta("Linux") is False


# --- ensure: the happy path ----------------------------------------------


def test_ensure_installs_a_verified_binary(tmp_path, install):
    binary = install(tmp_path / "cache")

    assert binary.read_bytes() == _BINARY
    assert binary.name == ("ocx.exe" if _host_target().endswith("windows-msvc") else "ocx")


def test_ensure_lays_the_cache_out_by_version_and_triple(tmp_path, install):
    root = tmp_path / "cache"

    binary = install(root)

    assert binary.parent == root / "0.5.8" / _host_target()


@pytest.mark.skipif(not _POSIX, reason="POSIX file modes")
def test_ensure_installs_private_files_and_directories(tmp_path, install):
    root = tmp_path / "cache"

    binary = install(root)

    assert stat.S_IMODE(binary.stat().st_mode) == 0o700
    assert stat.S_IMODE(binary.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(binary.parent.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(root.stat().st_mode) == 0o700


def test_ensure_leaves_no_temporary_files_behind(tmp_path, install):
    binary = install(tmp_path / "cache")

    assert {entry.name for entry in binary.parent.iterdir()} == {binary.name, "ocx.sha256"}


def test_ensure_is_idempotent_without_touching_the_network(tmp_path, install, monkeypatch):
    root = tmp_path / "cache"
    first = install(root)
    monkeypatch.setattr(_dist, "fetch_artifact", _ExplodingFetch())

    second = install(root)

    assert second == first
    assert second.read_bytes() == _BINARY


def test_cache_rehash_on_hit(tmp_path, install):
    root = tmp_path / "cache"
    binary = install(root)
    binary.write_bytes(b"tampered")

    restored = install(root)

    assert restored.read_bytes() == _BINARY
    assert install.fetch.calls == [_artifact_url(), _artifact_url()]


def test_ensure_redownloads_when_the_digest_sidecar_is_gone(tmp_path, install):
    root = tmp_path / "cache"
    binary = install(root)
    binary.with_suffix(".sha256").unlink()

    install(root)

    assert len(install.fetch.calls) == 2


def test_trust_cache_skips_the_rehash(tmp_path, install, monkeypatch):
    root = tmp_path / "cache"
    binary = install(root)
    binary.write_bytes(b"tampered")
    monkeypatch.setattr(_dist, "fetch_artifact", _ExplodingFetch())

    returned = install(root, trust_cache=True)

    assert returned.read_bytes() == b"tampered"


def test_install_force_redownloads(tmp_path, install):
    root = tmp_path / "cache"

    install(root)
    install(root, env=HostEnv({InstallEnv.FORCE: "1"}))

    assert len(install.fetch.calls) == 2


@pytest.mark.parametrize(
    "value", [pytest.param("0", id="zero"), pytest.param("", id="empty"), pytest.param("false", id="false")]
)
def test_install_force_is_off_for_falsy_values(tmp_path, install, value: str):
    root = tmp_path / "cache"

    install(root)
    install(root, env=HostEnv({InstallEnv.FORCE: value}))

    assert len(install.fetch.calls) == 1


# --- ensure: refusals -----------------------------------------------------


def test_ensure_never_retries_a_checksum_mismatch(tmp_path, install):
    slept: list[float] = []
    policy = RetryPolicy(attempts=3, backoff=0.1, jitter=False, sleep=slept.append)
    manifest = _manifest_body(tar_sha="0" * 64, zip_sha="0" * 64)

    with pytest.raises(ChecksumMismatchError, match=re.escape("0.5.8")):
        install(tmp_path / "cache", manifest=manifest, retry=policy)

    assert len(install.fetch.calls) == 1
    assert slept == []


def test_ensure_leaves_nothing_installed_after_a_mismatch(tmp_path, install):
    root = tmp_path / "cache"
    manifest = _manifest_body(tar_sha="0" * 64, zip_sha="0" * 64)

    with pytest.raises(ChecksumMismatchError):
        install(root, manifest=manifest)

    assert list((root / "0.5.8" / _host_target()).iterdir()) == []


def test_ensure_reports_an_archive_without_the_binary(tmp_path, monkeypatch):
    windows = _host_target().endswith("windows-msvc")
    junk = _zip_archive([("readme", b"x")]) if windows else _tar_archive([("readme", b"x")])
    monkeypatch.setattr(_dist, "fetch_artifact", _FakeFetch(junk))
    root = tmp_path / "cache"

    with pytest.raises(DistManifestError, match="no member"):
        ensure(
            dist=DistSource.data(_manifest_body(tar_sha=_sha(junk), zip_sha=_sha(junk))),
            cache_dir=root,
            env=HostEnv({}),
        )

    assert list((root / "0.5.8" / _host_target()).iterdir()) == []


def test_ensure_refuses_a_version_below_the_operator_floor(tmp_path, install):
    with pytest.raises(BootstrapError, match=re.escape("0.9.0")):
        install(tmp_path / "cache", min_version="0.9.0")

    assert install.fetch.calls == []


def test_ensure_accepts_a_version_at_the_operator_floor(tmp_path, install):
    binary = install(tmp_path / "cache", min_version="0.5.8")

    assert binary.is_file()


def test_ensure_reports_an_unsupported_platform(tmp_path, install):
    manifest = _manifest_body(tar_sha="0" * 64, zip_sha="0" * 64, targets=("riscv64-unknown-linux-musl",))

    with pytest.raises(UnsupportedPlatformError, match=re.escape(_host_target())):
        install(tmp_path / "cache", manifest=manifest)


def test_ensure_requires_a_pin_when_a_mirror_is_configured(tmp_path):
    with pytest.raises(DistManifestError, match="sha256"):
        ensure(
            dist=DistSource.url("https://mirror.example/dist.json"),
            mirror_url="https://mirror.example/ocx",
            cache_dir=tmp_path / "cache",
            env=HostEnv({}),
        )


def test_cache_refuses_a_symlinked_level_below_the_root(tmp_path, install):
    root = tmp_path / "cache"
    root.mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (root / "0.5.8").symlink_to(elsewhere, target_is_directory=True)

    with pytest.raises(BootstrapError, match="symlink"):
        install(root)

    assert install.fetch.calls == []


@pytest.mark.skipif(not _POSIX, reason="POSIX ownership and mode bits")
def test_cache_refuses_untrusted_root(tmp_path, install):
    root = tmp_path / "cache"
    root.mkdir()
    root.chmod(0o777)

    with pytest.raises(BootstrapError, match="writable"):
        install(root)

    assert install.fetch.calls == []


# --- ensure: precedence ---------------------------------------------------


def test_ensure_prefers_the_version_argument_over_the_environment(tmp_path, install):
    manifest = install.body(versions=("0.5.8", "0.5.7"))

    binary = install(tmp_path / "cache", manifest=manifest, version="0.5.7", env=HostEnv({InstallEnv.VERSION: "0.5.8"}))

    assert binary.parent.parent.name == "0.5.7"


def test_ensure_falls_back_to_the_environment_version(tmp_path, install):
    manifest = install.body(versions=("0.5.8", "0.5.7"))

    binary = install(tmp_path / "cache", manifest=manifest, env=HostEnv({InstallEnv.VERSION: "0.5.7"}))

    assert binary.parent.parent.name == "0.5.7"


def test_ensure_selects_the_next_channel(tmp_path, install):
    manifest = install.body(versions=("0.5.8", "0.6.0"), latest_next="0.6.0")

    binary = install(tmp_path / "cache", manifest=manifest, channel=Channel.NEXT)

    assert binary.parent.parent.name == "0.6.0"


@pytest.mark.parametrize(
    ("kwargs", "env", "mirror"),
    [
        pytest.param({"mirror_url": "https://mirror.example/ocx"}, {}, "https://mirror.example/ocx", id="argument"),
        pytest.param(
            {}, {InstallEnv.MIRROR_URL: "https://mirror.example/ocx"}, "https://mirror.example/ocx", id="environment"
        ),
        pytest.param(
            {"mirror_url": "https://argument.example/ocx"},
            {InstallEnv.MIRROR_URL: "https://environment.example/ocx"},
            "https://argument.example/ocx",
            id="argument-wins",
        ),
        pytest.param({}, {}, None, id="manifest-url-when-unmirrored"),
    ],
)
def test_mirror_rewrites_the_artifact_url(tmp_path, install, kwargs: dict[str, str], env: dict[str, str], mirror):
    install(tmp_path / "cache", env=HostEnv(env), **kwargs)

    assert install.fetch.calls == [_artifact_url(mirror=mirror)]


def test_mirror_rewrite_must_still_be_https(tmp_path, install):
    with pytest.raises(DistManifestError, match="https"):
        install(tmp_path / "cache", mirror_url="http://mirror.example/ocx")


# --- archive handling (never extractall) ----------------------------------


def _extract(archive: bytes, filename: str, member: str, tmp_path: Path) -> bytes:
    """Run `_extract_member` into a file and return what landed there."""
    target = tmp_path / "member"
    with target.open("wb") as handle:
        _extract_member(io.BytesIO(archive), filename, member, handle.fileno())
    return target.read_bytes()


@pytest.mark.parametrize(
    ("archive", "filename", "member"),
    [
        pytest.param(_tar_archive(), "ocx.tar.gz", "ocx", id="tar-gz"),
        pytest.param(_tar_archive(mode="w:xz"), "ocx.tar.xz", "ocx", id="tar-xz"),
        pytest.param(_tar_archive([("ocx-0.5.8/ocx", _BINARY)]), "ocx.tar.gz", "ocx", id="tar-nested-directory"),
        pytest.param(
            _tar_archive([("readme", b"x"), ("ocx", _BINARY)]), "ocx.tar.gz", "ocx", id="tar-alongside-other-files"
        ),
        pytest.param(_zip_archive(), "ocx.zip", "ocx.exe", id="zip"),
        pytest.param(_zip_archive([("ocx-0.5.8/ocx.exe", _BINARY)]), "ocx.zip", "ocx.exe", id="zip-nested-directory"),
    ],
)
def test_archive_streams_single_member(archive: bytes, filename: str, member: str, tmp_path):
    assert _extract(archive, filename, member, tmp_path) == _BINARY


@pytest.mark.parametrize(
    ("archive", "filename", "member", "reason"),
    [
        pytest.param(_tar_archive([("../ocx", _BINARY)]), "ocx.tar.gz", "ocx", "traversal", id="tar-slip-parent"),
        pytest.param(_tar_archive([("/ocx", _BINARY)]), "ocx.tar.gz", "ocx", "absolute", id="tar-slip-absolute"),
        pytest.param(
            _tar_archive([("readme", b"x")], symlinks=[("ocx", "/etc/passwd")]),
            "ocx.tar.gz",
            "ocx",
            "regular file",
            id="tar-symlink-member",
        ),
        pytest.param(
            _tar_archive([("readme", b"x")], directories=["ocx"]),
            "ocx.tar.gz",
            "ocx",
            "regular file",
            id="tar-directory",
        ),
        pytest.param(
            _tar_archive([("a/ocx", _BINARY), ("b/ocx", _BINARY)]),
            "ocx.tar.gz",
            "ocx",
            "more than one",
            id="tar-ambiguous",
        ),
        pytest.param(_tar_archive([("readme", b"x")]), "ocx.tar.gz", "ocx", "no member", id="tar-missing"),
        pytest.param(_zip_archive([("../ocx.exe", _BINARY)]), "ocx.zip", "ocx.exe", "traversal", id="zip-slip-parent"),
        pytest.param(
            _zip_archive([("readme", b"x")], symlinks=[("ocx.exe", "C:/Windows/system32/cmd.exe")]),
            "ocx.zip",
            "ocx.exe",
            "regular file",
            id="zip-symlink-member",
        ),
        pytest.param(
            _zip_archive([("readme", b"x")], directories=["ocx.exe"]),
            "ocx.zip",
            "ocx.exe",
            "regular file",
            id="zip-directory",
        ),
        pytest.param(_zip_archive([("readme", b"x")]), "ocx.zip", "ocx.exe", "no member", id="zip-missing"),
        pytest.param(b"not an archive at all", "ocx.tar.gz", "ocx", "archive", id="corrupt-tar"),
        pytest.param(b"not an archive at all", "ocx.zip", "ocx.exe", "archive", id="corrupt-zip"),
        pytest.param(_tar_archive(), "ocx.rar", "ocx", "unpack", id="unknown-extension"),
    ],
)
def test_archive_refuses_anything_but_one_regular_member(
    archive: bytes, filename: str, member: str, reason: str, tmp_path
):
    with pytest.raises(DistManifestError, match=reason):
        _extract(archive, filename, member, tmp_path)


def test_archive_member_size_is_capped(tmp_path, monkeypatch):
    monkeypatch.setattr(_dist, "ARTIFACT_MAX_BYTES", 1024)
    archive = _tar_archive([("ocx", b"x" * 2048)])

    with pytest.raises(DownloadError, match="exceeds"):
        _extract(archive, "ocx.tar.gz", "ocx", tmp_path)


# --- discovery ------------------------------------------------------------


def _fake_binary(directory: Path, name: str = _EXE) -> Path:
    """Create an executable file that `shutil.which` will accept."""
    directory.mkdir(parents=True, exist_ok=True)
    binary = directory / name
    binary.write_text("#!/bin/sh\n")
    binary.chmod(0o755)
    return binary


def test_discover_prefers_the_explicit_argument(tmp_path):
    chosen = _fake_binary(tmp_path / "chosen")
    ignored = _fake_binary(tmp_path / "ignored")
    env = HostEnv({OCX_SDK_EXE: str(ignored), "PATH": str(ignored.parent)})

    assert discover(chosen, env) == chosen


def test_discover_reports_a_missing_explicit_argument(tmp_path):
    with pytest.raises(OcxNotFoundError, match="nowhere"):
        discover(tmp_path / "nowhere" / "ocx", HostEnv({}))


def test_discover_uses_the_sdk_exe_variable(tmp_path):
    binary = _fake_binary(tmp_path / "provisioned")
    on_path = _fake_binary(tmp_path / "onpath")
    env = HostEnv({OCX_SDK_EXE: str(binary), "PATH": str(on_path.parent)})

    assert discover(env=env) == binary


def test_discover_falls_back_to_the_search_path(tmp_path):
    binary = _fake_binary(tmp_path / "bin")

    assert discover(env=HostEnv({"PATH": str(binary.parent)})) == binary


def test_discovery_ocx_home_symlink(tmp_path):
    home = tmp_path / "ocx-home"
    package = home / "packages" / "sha256" / "ab" / "cdef"
    _fake_binary(package / "content" / "bin")
    stable = home.joinpath(*INSTALL_SYMLINK)
    stable.parent.parent.parent.mkdir(parents=True)
    stable.parent.parent.symlink_to(package, target_is_directory=True)

    found = discover(env=HostEnv({OCX_HOME: str(home), "PATH": str(tmp_path / "empty")}))

    assert found == stable / _EXE
    assert found.read_text() == "#!/bin/sh\n"


def test_discovery_reads_home_from_the_snapshot_not_the_real_user(tmp_path, monkeypatch):
    # `$OCX_HOME` unset falls back to `~/.ocx`, and reading the *real* home
    # there would let a deliberately narrowed snapshot find the ambient
    # install anyway — the opposite of what a hermetic caller asked for.
    ambient = tmp_path / "ambient-home"
    stable = (ambient / ".ocx").joinpath(*INSTALL_SYMLINK)
    _fake_binary(stable)
    monkeypatch.setattr(Path, "home", classmethod(lambda _cls: ambient))

    with pytest.raises(OcxNotFoundError, match="no-such-home"):
        discover(env=HostEnv({"HOME": str(tmp_path / "no-such-home")}))


def test_discovery_without_a_home_in_the_snapshot_fails_closed(tmp_path, monkeypatch):
    # A snapshot with neither OCX_HOME nor HOME has no home tier at all —
    # reading the ambient Path.home() would defeat a hermetic env=.
    ambient = tmp_path / "ambient-home"
    _fake_binary((ambient / ".ocx").joinpath(*INSTALL_SYMLINK))
    monkeypatch.setattr(Path, "home", classmethod(lambda _cls: ambient))

    with pytest.raises(OcxNotFoundError, match="ensure"):
        discover(env=HostEnv({}))


def test_discover_names_the_bootstrap_when_nothing_is_found(tmp_path):
    env = HostEnv({OCX_HOME: str(tmp_path / "ocx-home"), "PATH": str(tmp_path / "empty")})

    with pytest.raises(OcxNotFoundError, match="ensure"):
        discover(env=env)


def test_discover_ignores_an_empty_search_path(tmp_path):
    with pytest.raises(OcxNotFoundError, match="ensure"):
        discover(env=HostEnv({OCX_HOME: str(tmp_path / "ocx-home"), "PATH": ""}))


# --- PATH lookup hardening ------------------------------------------------


def test_which_excludes_the_working_directory_on_windows(tmp_path):
    work = _fake_binary(tmp_path / "work").parent

    assert _which("ocx", str(work), windows=True, cwd=work) is None


def test_which_accepts_a_directory_that_is_not_the_working_one(tmp_path):
    binary = _fake_binary(tmp_path / "work")

    assert _which("ocx", str(binary.parent), windows=True, cwd=tmp_path / "elsewhere") == binary


@pytest.mark.skipif(not _POSIX, reason="POSIX mode bits")
@pytest.mark.parametrize("mode", [pytest.param(0o777, id="other-writable"), pytest.param(0o770, id="group-writable")])
def test_which_refuses_a_shared_directory(tmp_path, mode: int):
    binary = _fake_binary(tmp_path / "shared")
    binary.parent.chmod(mode)

    assert _which("ocx", str(binary.parent), windows=False, cwd=tmp_path) is None


def test_which_excludes_a_relative_entry_naming_the_working_directory(tmp_path, monkeypatch):
    work = _fake_binary(tmp_path / "work").parent
    monkeypatch.chdir(work)

    assert _which("ocx", ".", windows=True, cwd=Path.cwd()) is None


def test_which_returns_an_absolute_path_for_a_relative_entry(tmp_path, monkeypatch):
    binary = _fake_binary(tmp_path / "work")
    monkeypatch.chdir(binary.parent)

    found = _which("ocx", ".", windows=True, cwd=tmp_path / "elsewhere")

    assert found is not None
    assert found.is_absolute()
    assert found.name == _EXE


def test_which_keeps_scanning_past_empty_and_missing_entries(tmp_path):
    binary = _fake_binary(tmp_path / "bin")
    search = os.pathsep.join(["", str(tmp_path / "missing"), str(binary.parent)])

    assert _which("ocx", search, windows=True, cwd=tmp_path) == binary


def test_which_returns_none_when_the_binary_is_absent(tmp_path):
    assert _which("ocx", str(tmp_path), windows=False, cwd=tmp_path) is None


# --- cache root trust -----------------------------------------------------


@pytest.mark.skipif(not _POSIX, reason="POSIX mode bits")
@pytest.mark.parametrize("mode", [pytest.param(0o777, id="other-writable"), pytest.param(0o770, id="group-writable")])
def test_verify_root_refuses_a_shared_directory(tmp_path, mode: int):
    root = tmp_path / "cache"
    root.mkdir()
    root.chmod(mode)

    with pytest.raises(BootstrapError, match="writable"):
        _verify_root(root, posix=True)


def test_verify_root_refuses_a_symlink(tmp_path):
    target = tmp_path / "real"
    target.mkdir(mode=0o700)
    root = tmp_path / "cache"
    root.symlink_to(target, target_is_directory=True)

    with pytest.raises(BootstrapError, match="symlink"):
        _verify_root(root, posix=True)


def test_verify_root_refuses_a_file(tmp_path):
    root = tmp_path / "cache"
    root.write_text("not a directory")

    with pytest.raises(BootstrapError, match="directory"):
        _verify_root(root, posix=True)


@pytest.mark.skipif(not _POSIX, reason="POSIX ownership")
def test_verify_root_refuses_a_foreign_owner(tmp_path, monkeypatch):
    root = tmp_path / "cache"
    root.mkdir(mode=0o700)
    monkeypatch.setattr(os, "getuid", lambda: os.stat(root).st_uid + 1)

    with pytest.raises(BootstrapError, match="owned"):
        _verify_root(root, posix=True)


def test_verify_root_accepts_a_missing_directory(tmp_path):
    _verify_root(tmp_path / "not-yet", posix=True)


def test_verify_root_skips_mode_checks_off_posix(tmp_path):
    root = tmp_path / "cache"
    root.mkdir()
    root.chmod(0o777)

    _verify_root(root, posix=False)


# --- helpers --------------------------------------------------------------


@pytest.mark.parametrize(
    ("env", "system", "expected"),
    [
        pytest.param({"XDG_CACHE_HOME": "/xdg"}, "Linux", Path("/xdg") / CACHE_DIR_NAME, id="linux-xdg"),
        pytest.param({"HOME": "/home/ci"}, "Linux", Path("/home/ci/.cache") / CACHE_DIR_NAME, id="linux-default"),
        pytest.param(
            {"HOME": "/Users/ci"}, "Darwin", Path("/Users/ci/Library/Caches") / CACHE_DIR_NAME, id="macos-default"
        ),
        pytest.param(
            {"LOCALAPPDATA": "C:\\Users\\ci\\AppData\\Local"},
            "Windows",
            Path("C:\\Users\\ci\\AppData\\Local") / CACHE_DIR_NAME,
            id="windows-localappdata",
        ),
    ],
)
def test_default_cache_dir(env: dict[str, str], system: str, expected: Path):
    assert _default_cache_dir(env, system) == expected


def test_default_cache_dir_falls_back_to_the_home_directory():
    assert _default_cache_dir({}, "Linux") == Path.home() / ".cache" / CACHE_DIR_NAME


@pytest.mark.parametrize(
    ("version", "expected"),
    [
        pytest.param("0.5.8", (0, 5, 8), id="release"),
        pytest.param("1.0.0-rc1", (1, 0, 0), id="pre-release-suffix-dropped"),
        pytest.param("0.6.0+build.7", (0, 6, 0), id="build-metadata-dropped"),
    ],
)
def test_version_key(version: str, expected: tuple[int, ...]):
    assert _version_key(version) == expected


def test_version_key_refuses_junk():
    with pytest.raises(BootstrapError, match="latest"):
        _version_key("latest")
