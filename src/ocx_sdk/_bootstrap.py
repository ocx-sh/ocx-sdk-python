# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The OCX Authors

"""Binary provisioning and discovery (contract C-006).

`ensure()` turns a version request into a verified ocx binary on disk;
`discover()` finds one that is already there. Neither ever mutates the machine:
no `self setup`, no profile edits, no PATH rewriting. Installing ocx for a
human is ocx's own job — this module installs it for a program.

The cache is the one piece of persistent state the SDK owns, so it is treated
as such: the root is refused outright if it is a symlink, owned by somebody
else, or writable by group or other (CWE-732/59); directories are created at
`0o700` a level at a time rather than inheriting a umask; and every install goes
`mkstemp` in the destination directory → write → hash the open descriptor →
`fchmod` → `os.replace`, so the digest covers the exact bytes being installed
and a concurrent `ensure()` cannot publish a half-written file (CWE-367).

**Where that trust stops** (stated, because a boundary nobody wrote down gets
assumed to be somewhere else): the cache *root* is the boundary. It is
`lstat`-checked for a symlink, a foreign owner, and group or other write, and
every level created below it is checked for a symlink too. The root's *parent*
is not, and cannot usefully be — by default it is the per-user cache directory,
whose safety is the caller's to own. Somebody who can write to that parent can
swap the root out from under a running `ensure()`, and no check inside this
module would notice. Hand `cache_dir=` a directory on a path you control if
that matters where you deploy.

Archives are never `extractall`-ed. `tarfile`'s extraction filter still defaults
to `fully_trusted` on our floor and `zipfile` has no filter at all, so after the
archive digest verifies, exactly one regular-file member whose base name matches
the expected binary is streamed to a descriptor we opened. No member name ever
reaches the filesystem (CWE-22).

Two deliberate departures from the `OCX_INSTALL_*` grammar the setup script
defines: `OCX_INSTALL_REPO` has no effect, because artifact URLs come from the
manifest rather than from a GitHub repository guess, and `OCX_INSTALL_QUIET`
has none either, because this module prints nothing to quiet.
"""

from __future__ import annotations

import ctypes
import hashlib
import os
import platform
import shutil
import stat
import tarfile
import tempfile
import zipfile
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import IO, Final

from . import _dist
from ._errors import (
    BootstrapError,
    ChecksumMismatchError,
    DistManifestError,
    DownloadError,
    OcxNotFoundError,
    UnsupportedPlatformError,
)
from ._types import Channel, HostEnv, InstallEnv, RetryPolicy

OCX_SDK_EXE: Final = "OCX_SDK_EXE"
"""Environment variable naming the ocx binary `discover()` should use.

The recorded exception to the SDK's no-public-runtime-variable rule: it exists
so CI can point the SDK at a provisioned binary without touching PATH.
"""

OCX_HOME: Final = "OCX_HOME"
"""Environment variable naming ocx's own store."""

INSTALL_SYMLINK: Final = ("symlinks", "ocx.sh", "ocx", "cli", "current", "content", "bin")
"""Path under `$OCX_HOME` of the stable install symlink ocx maintains for itself."""

CACHE_DIR_NAME: Final = "ocx-sdk"
"""Directory the SDK owns inside the user cache."""

DIGEST_SUFFIX: Final = ".sha256"
"""Extension of the sidecar recording an installed binary's own digest."""

_BINARY_STEM: Final = "ocx"
"""Base name of the binary inside a release archive."""

_CHUNK: Final = 1 << 16
"""Read size for hashing and unpacking."""

_CACHE_MODE: Final = 0o700
"""Mode for everything the SDK creates under the cache root."""

_ARCHITECTURES: Final = {"x86_64": "x86_64", "amd64": "x86_64", "aarch64": "aarch64", "arm64": "aarch64"}
"""uname machine names, folded onto the two architectures ocx publishes."""

_TAR_SUFFIXES: Final = (".tar", ".tar.gz", ".tgz", ".tar.xz", ".tar.bz2")
"""Archive names `tarfile` handles; anything else is refused rather than guessed."""

_FALSE_VALUES: Final = ("", "0", "false", "no")
"""Values an `OCX_INSTALL_*` flag can take without meaning yes."""


def uname_to_triple(system: str, machine: str, *, libc_hint: str | None = None, rosetta: bool = False) -> str:
    """Map uname values to the cargo-dist triple ocx publishes builds for.

    A pure function of its arguments: the caller reads the platform, this maps
    it, and the mapping is exercised from a table instead of from a mocked host.

    Linux resolves to musl by default. The musl builds are static and run on
    every distribution including the glibc ones, so preferring them removes an
    entire class of "GLIBC_2.38 not found" failures; `libc_hint='gnu'` opts back
    into the dynamically linked build. macOS under Rosetta reports an x86_64
    machine on arm64 hardware, so a translated interpreter is redirected to the
    native build rather than installing an emulated one.

    Args:
        system: `platform.system()` value, e.g. `Linux`.
        machine: `platform.machine()` value, e.g. `x86_64`.
        libc_hint: `musl` or `gnu` on Linux; `None` picks musl.
        rosetta: Whether the interpreter is translated (macOS only).

    Returns:
        The cargo-dist triple, e.g. `x86_64-unknown-linux-musl`.

    Raises:
        UnsupportedPlatformError: No ocx build targets this platform.
    """
    architecture = _ARCHITECTURES.get(machine.lower())
    if architecture is None:
        raise UnsupportedPlatformError(
            f"ocx publishes no build for machine {machine!r} on {system}; install ocx by hand and pass `exe=`."
        )
    if system == "Linux":
        return f"{architecture}-unknown-linux-{'gnu' if libc_hint == 'gnu' else 'musl'}"
    if system == "Darwin":
        return "aarch64-apple-darwin" if rosetta else f"{architecture}-apple-darwin"
    if system == "Windows":
        return f"{architecture}-pc-windows-msvc"
    raise UnsupportedPlatformError(
        f"ocx publishes no build for {system}/{machine}; install ocx by hand and pass `exe=`."
    )


def ensure(
    version: str | None = None,
    *,
    channel: Channel = Channel.STABLE,
    dist: _dist.DistSource | None = None,
    mirror_url: str | None = None,
    min_version: str | None = None,
    cache_dir: Path | None = None,
    env: HostEnv | None = None,
    trust_cache: bool = False,
    retry: RetryPolicy | None = None,
    timeout: float | None = None,
) -> Path:
    """Provision a verified ocx binary and return its path.

    Idempotent and offline-friendly: a cache hit that still hashes correctly
    needs no network at all. Every knob resolves explicit argument first, then
    the matching `OCX_INSTALL_*` variable from `env`, then the default.

    Args:
        version: Exact version to install. `None` takes the channel's latest.
        channel: Channel consulted when `version` is `None`.
        dist: Where the manifest comes from. `None` builds the default source,
            which honors `OCX_INSTALL_DIST_URL`; an explicitly constructed
            source does not.
        mirror_url: Base URL that replaces the artifact host, as
            `<mirror_url>/<tag>/<filename>`. The manifest digest is still
            enforced — a mirror relocates bytes, it never revalidates them.
        min_version: Operator floor. A resolved version below it fails loudly
            instead of installing something older than the caller allows.
        cache_dir: Cache root. `None` uses the per-user cache directory.
        env: Environment snapshot. `None` reads the ambient one.
        trust_cache: Skip the digest re-check on a cache hit.
        retry: Policy for transient transport failures; `None` tries once.
        timeout: Per-attempt network budget in seconds.

    Returns:
        Path to the installed binary, mode `0o700`.

    Raises:
        BootstrapError: The cache root is untrusted, or the resolved version is
            below `min_version`.
        DistManifestError: The manifest is unusable, or its archive is.
        UnsupportedPlatformError: No release matches this platform.
        ChecksumMismatchError: Downloaded bytes do not match the manifest.
            Never retried — the bytes are wrong, not late.
        DownloadError: The manifest or artifact could not be fetched.
    """
    values = (env or HostEnv.ambient()).source
    wanted = version or values.get(InstallEnv.VERSION) or None
    mirror = mirror_url or values.get(InstallEnv.MIRROR_URL) or None
    source = dist or _dist.DistSource()
    system = platform.system()
    target = uname_to_triple(system, platform.machine(), rosetta=_translated_by_rosetta(system))
    root = Path(cache_dir or _default_cache_dir(values, system))
    _verify_root(root, posix=os.name != "nt")

    manifest = _dist.load_manifest(source, env=values, mirrored=mirror is not None, timeout=timeout, retry=retry)
    release = manifest.select(version=wanted, channel=channel, target=target)
    if min_version is not None and _version_key(release.version) < _version_key(min_version):
        raise BootstrapError(
            f"the manifest resolved ocx {release.version}, below the min_version={min_version} floor this caller "
            "requires; raise the floor or pin a newer version."
        )

    directory = root / release.version / release.target
    _secure_mkdir(directory, root)
    binary = directory / _binary_name(release.target)
    sidecar = binary.with_suffix(DIGEST_SUFFIX)
    if not _flag(values, InstallEnv.FORCE) and _cached(binary, sidecar, trust_cache=trust_cache):
        return binary
    _install(
        release,
        binary,
        sidecar,
        mirror_url=mirror,
        credentials=source.credentials(values),
        timeout=timeout,
        retry=retry,
    )
    return binary


def discover(exe: str | Path | None = None, env: HostEnv | None = None) -> Path:
    """Find an ocx binary without provisioning one.

    Resolution order: the explicit `exe` argument, `OCX_SDK_EXE`, `PATH`, then
    ocx's own stable install symlink under `$OCX_HOME`. `exe=` is the hardened
    form — it is the only one that trusts a location without inspecting how it
    was reached.

    Every step reads `env` and nothing else, `$OCX_HOME`'s `HOME` fallback
    included, so a narrowed snapshot really does narrow discovery.

    Args:
        exe: An explicit binary path, taken as given.
        env: Environment snapshot. `None` reads the ambient one.

    Returns:
        Path to the binary.

    Raises:
        OcxNotFoundError: Nothing was found; the message names
            `bootstrap.ensure()` as the fix.
    """
    values = (env or HostEnv.ambient()).source
    if exe is not None:
        return _existing(Path(exe), f"the exe= path {exe}")
    named = values.get(OCX_SDK_EXE)
    if named:
        return _existing(Path(named), f"{OCX_SDK_EXE}={named}")
    search = values.get("PATH")
    if search:
        found = _which(_BINARY_STEM, search, windows=os.name == "nt", cwd=Path.cwd())
        if found is not None:
            return found
    # `HOME` comes off the snapshot, never `Path.home()` — the ambient home
    # would make a deliberately narrowed `env=` find the ambient install
    # anyway, exactly what a hermetic caller passed the snapshot to prevent.
    # No `OCX_HOME` and no `HOME` in the snapshot = no home tier at all.
    ocx_home = values.get(OCX_HOME)
    user_home = values.get("HOME")
    home = Path(ocx_home) if ocx_home else (Path(user_home) / ".ocx" if user_home else None)
    if home is not None:
        for name in (_BINARY_STEM, f"{_BINARY_STEM}.exe"):
            candidate = home.joinpath(*INSTALL_SYMLINK, name)
            if candidate.is_file():
                return candidate
    raise OcxNotFoundError(
        f"no ocx binary was found: no exe= argument, no {OCX_SDK_EXE}, nothing on PATH, and no install under {home}."
    )


def _translated_by_rosetta(system: str) -> bool:  # pragma: no cover - macOS-only sysctl probe
    """Report whether this interpreter runs under Rosetta 2 translation.

    Reads `sysctl.proc_translated` through libc rather than shelling out, since
    every subprocess in this SDK belongs to `_process`. The triple mapping it
    feeds is tested from a table instead, which is why this probe carries a
    coverage pragma.

    Args:
        system: `platform.system()` value; anything but `Darwin` is `False`.

    Returns:
        True when translated.
    """
    if system != "Darwin":
        return False
    try:
        libc = ctypes.CDLL("libc.dylib")
        translated = ctypes.c_int(0)
        size = ctypes.c_size_t(ctypes.sizeof(translated))
        failed = libc.sysctlbyname(b"sysctl.proc_translated", ctypes.byref(translated), ctypes.byref(size), None, 0)
    except (OSError, AttributeError):
        return False
    return failed == 0 and translated.value == 1


def _default_cache_dir(env: Mapping[str, str], system: str) -> Path:
    """Return the per-user cache root for this platform.

    Args:
        env: Environment consulted for `HOME`, `XDG_CACHE_HOME`, and
            `LOCALAPPDATA`.
        system: `platform.system()` value.

    Returns:
        The SDK's directory inside the user cache.
    """
    home = Path(env.get("HOME") or Path.home())
    if system == "Windows":
        return Path(env.get("LOCALAPPDATA") or home / "AppData" / "Local") / CACHE_DIR_NAME
    if system == "Darwin":
        return home / "Library" / "Caches" / CACHE_DIR_NAME
    return Path(env.get("XDG_CACHE_HOME") or home / ".cache") / CACHE_DIR_NAME


def _verify_root(root: Path, *, posix: bool) -> None:
    """Refuse a cache root that somebody else could have written through.

    Args:
        root: The cache root; a missing one is fine, it gets created `0o700`.
        posix: Whether ownership and mode bits are meaningful. Windows reports
            synthesized POSIX bits that say nothing about the real ACL, so the
            check is skipped there rather than failing every path.

    Raises:
        BootstrapError: The root is a symlink, is not a directory, belongs to
            another user, or is group- or other-writable.
    """
    try:
        info = os.lstat(root)
    except FileNotFoundError:
        return
    if stat.S_ISLNK(info.st_mode):
        raise BootstrapError(
            f"the cache root {root} is a symlink; the SDK refuses to install through one (CWE-59). "
            "Pass `cache_dir=` a real directory you own."
        )
    if not stat.S_ISDIR(info.st_mode):
        raise BootstrapError(f"the cache root {root} is not a directory; pass `cache_dir=` a directory you own.")
    if posix:  # pragma: no cover - ownership and mode bits are meaningless on Windows
        _verify_ownership(root, info)


def _verify_ownership(root: Path, info: os.stat_result) -> None:  # pragma: no cover - POSIX-only
    """Refuse a cache root owned by, or writable by, somebody else.

    Excluded from coverage because it never runs on the Windows leg of the
    matrix; the POSIX-marked cache-root tests are what assert it.

    Args:
        root: The cache root, for the error message.
        info: Its `lstat` result.

    Raises:
        BootstrapError: The root belongs to another user, or is group- or
            other-writable.
    """
    if info.st_uid != os.getuid():
        raise BootstrapError(
            f"the cache root {root} is owned by uid {info.st_uid} rather than {os.getuid()}; "
            "the SDK will not install into another user's directory."
        )
    if info.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise BootstrapError(
            f"the cache root {root} is group- or other-writable (mode {stat.S_IMODE(info.st_mode):#o}), so anything "
            f"it holds is untrusted (CWE-732). Run `chmod {_CACHE_MODE:o} {root}`, or pass a private `cache_dir=`."
        )


def _secure_mkdir(path: Path, root: Path) -> None:
    """Create `path` and any missing parents, each at mode `0o700`.

    `mkdir(parents=True)` would let the umask decide what the intermediate
    levels look like, which is how a cache ends up world-readable on a host
    whose umask is 0.

    Every level between `root` and `path` is `lstat`-checked first: `_verify_root`
    refuses a symlinked root, and this refuses a symlinked level below it, so no
    part of the path the install writes through can point somewhere else.

    Args:
        path: The directory to create.
        root: The verified cache root `path` sits under.

    Raises:
        BootstrapError: A level below the root is a symlink.
    """
    node = root
    for part in path.relative_to(root).parts:
        node = node / part
        if node.is_symlink():
            raise BootstrapError(
                f"the cache level {node} is a symlink; the SDK installs through real directories only (CWE-59). "
                "Remove it, or point `cache_dir=` somewhere you own outright."
            )
    missing: list[Path] = []
    node = path
    while not node.exists():
        missing.append(node)
        node = node.parent
    for node in reversed(missing):
        node.mkdir(mode=_CACHE_MODE)
        os.chmod(node, _CACHE_MODE)


def _which(name: str, search_path: str, *, windows: bool, cwd: Path) -> Path | None:
    """Look `name` up on a PATH, entry by entry, refusing unsafe hits.

    `shutil.which` inserts the current directory ahead of the search path on
    Windows, which turns a working directory into a binary-injection vector
    (CWE-426). Scanning entry by entry lets a hit inside the working directory
    be dropped instead of trusted. Off Windows, a hit whose parent directory is
    group- or other-writable is refused for the same reason.

    Both the hit and `cwd` are resolved before either check: a relative PATH
    entry like `.` otherwise slips past the working-directory comparison, and
    the caller would be handed a path that stops meaning anything the moment
    something calls `chdir`.

    Args:
        name: Binary name to find.
        search_path: The PATH value to scan.
        windows: Whether the working-directory exclusion applies.
        cwd: The working directory to exclude.

    Returns:
        The first acceptable hit, as an absolute path, or `None`.
    """
    working = cwd.resolve()
    for entry in search_path.split(os.pathsep):
        if not entry:
            continue
        found = shutil.which(name, path=entry)
        if found is None:
            continue
        candidate = Path(found).resolve()
        if not _trusted_location(candidate.parent, windows=windows, cwd=working):
            continue
        return candidate
    return None


def _trusted_location(directory: Path, *, windows: bool, cwd: Path) -> bool:  # pragma: no cover - platform split
    """Report whether a directory a binary was found in may be trusted.

    Excluded from coverage because each half only ever runs on one platform;
    the parameterized `_which` tests are what assert both.

    Args:
        directory: The directory holding the candidate binary.
        windows: Whether to apply the working-directory exclusion instead of
            the POSIX mode check.
        cwd: The working directory to exclude.

    Returns:
        True when the location is acceptable.
    """
    if windows:
        return directory != cwd
    return not os.lstat(directory).st_mode & (stat.S_IWGRP | stat.S_IWOTH)


def _existing(candidate: Path, what: str) -> Path:
    """Return a path that names an existing file, or refuse it.

    Args:
        candidate: The path to check.
        what: How the path was named, for the error message.

    Returns:
        The path.

    Raises:
        OcxNotFoundError: The path does not name a file.
    """
    if candidate.is_file():
        return candidate
    # No fix sentence here: `OcxNotFoundError._hint` already appends one, and
    # two in a row read as a stutter in a CI log.
    raise OcxNotFoundError(f"{what} does not name a file.")


def _flag(values: Mapping[str, str], name: str) -> bool:
    """Report whether an `OCX_INSTALL_*` flag is set to something meaning yes.

    Args:
        values: The environment snapshot.
        name: The variable name.

    Returns:
        True unless the variable is absent, empty, or spells a negative.
    """
    return values.get(name, "").strip().lower() not in _FALSE_VALUES


def _cached(binary: Path, sidecar: Path, *, trust_cache: bool) -> bool:
    """Report whether the cached binary can be handed back as it stands.

    The manifest digest covers the *archive*, so a cache hit is re-verified
    against the digest of the extracted binary that the install recorded beside
    it. A missing or stale sidecar is a cache miss, not a failure.

    Args:
        binary: The installed binary.
        sidecar: The file recording its digest.
        trust_cache: Skip the re-hash and accept whatever is there.

    Returns:
        True when the caller can use `binary` without downloading anything.
    """
    if not binary.is_file():
        return False
    if trust_cache:
        return True
    if not sidecar.is_file():
        return False
    return _hash_path(binary) == sidecar.read_text().strip()


def _install(
    release: _dist.Release,
    binary: Path,
    sidecar: Path,
    *,
    mirror_url: str | None,
    credentials: _dist.Credentials,
    timeout: float | None,
    retry: RetryPolicy | None,
) -> None:
    """Download, verify, and publish one release into the cache.

    Args:
        release: The manifest row being installed.
        binary: Where the binary belongs.
        sidecar: Where its digest belongs.
        mirror_url: Mirror base, or `None` for the manifest's own URL.
        credentials: Origin-bound credentials from the manifest source.
        timeout: Per-attempt network budget in seconds.
        retry: Policy for transient transport failures.

    Raises:
        ChecksumMismatchError: The archive does not match the manifest digest.
    """
    url = _artifact_url(release, mirror_url)
    fd, name = tempfile.mkstemp(dir=binary.parent, prefix=".archive-")
    archive = Path(name)
    try:
        _dist.fetch_artifact(url, fd, credentials=credentials, timeout=timeout, retry=retry)
        digest = _hash_fd(fd)
        if digest != release.sha256:
            raise ChecksumMismatchError(
                f"the archive for ocx {release.version} ({release.filename}) hashes to {digest}, not the "
                f"{release.sha256} the manifest declares. Nothing was installed, and this is never retried."
            )
        os.lseek(fd, 0, os.SEEK_SET)
        with open(fd, "rb", closefd=False) as body:
            _publish(body, release, binary, sidecar)
    finally:
        os.close(fd)
        archive.unlink(missing_ok=True)


def _publish(archive: IO[bytes], release: _dist.Release, binary: Path, sidecar: Path) -> None:
    """Unpack the verified archive into place, atomically.

    Args:
        archive: The verified archive.
        release: The manifest row, for the archive name.
        binary: Where the binary belongs.
        sidecar: Where its digest belongs.
    """
    fd, name = tempfile.mkstemp(dir=binary.parent, prefix=".ocx-")
    staged = Path(name)
    closed = False
    try:
        _extract_member(archive, release.filename, binary.name, fd)
        digest = _hash_fd(fd)
        if os.name != "nt":  # pragma: no cover - fchmod is POSIX-only; Windows keeps mkstemp's private mode
            os.fchmod(fd, _CACHE_MODE)
        os.close(fd)
        closed = True
        os.replace(staged, binary)
        _write_digest(sidecar, digest)
    finally:
        if not closed:
            os.close(fd)
        staged.unlink(missing_ok=True)


def _write_digest(sidecar: Path, digest: str) -> None:
    """Record an installed binary's own digest beside it, atomically.

    Args:
        sidecar: The file to write.
        digest: The digest of the installed binary.
    """
    fd, name = tempfile.mkstemp(dir=sidecar.parent, prefix=".sha256-")
    try:
        os.write(fd, digest.encode())
    finally:
        os.close(fd)
    os.replace(name, sidecar)


def _version_key(version: str) -> tuple[int, ...]:
    """Return the comparable release numbers of a version string.

    Args:
        version: A semver-shaped version; any pre-release or build suffix is
            dropped, so `0.6.0-rc1` and `0.6.0` compare equal.

    Returns:
        The numeric components.

    Raises:
        BootstrapError: The version is not numeric.
    """
    parts = version.split("-")[0].split("+")[0].split(".")
    if not all(part.isdigit() for part in parts):
        raise BootstrapError(f"{version!r} is not a numeric version, so it cannot be compared against a floor.")
    return tuple(int(part) for part in parts)


def _artifact_url(release: _dist.Release, mirror_url: str | None) -> str:
    """Return the URL to pull a release archive from.

    Args:
        release: The manifest row.
        mirror_url: Mirror base, or `None` to use the row's own URL.

    Returns:
        The artifact URL.

    Raises:
        DistManifestError: The resulting URL is not a usable https URL.
    """
    if mirror_url is None:
        return release.url
    url = f"{mirror_url.rstrip('/')}/{release.tag}/{release.filename}"
    _dist.validate_url(url, what="mirror artifact URL")
    return url


def _binary_name(target: str) -> str:
    """Return the binary's file name for a target triple.

    Args:
        target: The cargo-dist triple.

    Returns:
        `ocx.exe` on Windows targets, `ocx` elsewhere.
    """
    return f"{_BINARY_STEM}.exe" if target.endswith("windows-msvc") else _BINARY_STEM


def _hash_fd(fd: int) -> str:
    """Return the sha256 of everything an open descriptor holds.

    Hashing through the descriptor rather than re-opening the path is what
    makes the digest cover the exact bytes being installed: no second lookup
    means no window for a swap between verification and use.

    Args:
        fd: The descriptor; it is rewound first and left at end of file.

    Returns:
        Lowercase hex digest.
    """
    os.lseek(fd, 0, os.SEEK_SET)
    digest = hashlib.sha256()
    while chunk := os.read(fd, _CHUNK):
        digest.update(chunk)
    return digest.hexdigest()


def _hash_path(path: Path) -> str:
    """Return the sha256 of a file's contents.

    Args:
        path: The file to read.

    Returns:
        Lowercase hex digest.
    """
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def _extract_member(archive: IO[bytes], filename: str, member: str, fd: int) -> None:
    """Stream the one archive member named `member` into `fd`.

    The unpacker is chosen by `filename`'s extension. Exactly one regular-file
    member whose base name matches is accepted; a member name that is absolute
    or walks upward is refused even though nothing here would write to it, and
    a link member is refused outright. Unpacked bytes are capped like
    downloaded ones, so a decompression bomb aborts instead of filling the
    cache.

    Args:
        archive: The verified archive, positioned anywhere; it gets rewound.
        filename: The archive's file name, which picks tar or zip.
        member: Base name of the wanted member, e.g. `ocx`.
        fd: Descriptor the member's bytes are written to.

    Raises:
        DistManifestError: The archive is unreadable, holds no such member,
            holds more than one, or holds it as something other than a regular
            file.
        DownloadError: The member exceeds `_dist.ARTIFACT_MAX_BYTES`.
    """
    archive.seek(0)
    if filename.endswith(".zip"):
        _extract_zip_member(archive, member, fd)
        return
    if not filename.endswith(_TAR_SUFFIXES):
        raise DistManifestError(
            f"the SDK cannot unpack {filename!r}; it handles .zip and {', '.join(_TAR_SUFFIXES)} archives."
        )
    _extract_tar_member(archive, member, fd)


def _extract_tar_member(archive: IO[bytes], member: str, fd: int) -> None:
    """Stream one member out of a tar archive.

    Args:
        archive: The verified archive.
        member: Base name of the wanted member.
        fd: Descriptor the member's bytes are written to.

    Raises:
        DistManifestError: The archive is unreadable, or its `member` is
            missing, ambiguous, or not a regular file.
        DownloadError: The member exceeds `_dist.ARTIFACT_MAX_BYTES`.
    """
    try:
        with tarfile.open(fileobj=archive, mode="r:*") as bundle:
            info = _only_member([item.name for item in bundle.getmembers()], member)
            entry = bundle.getmember(info)
            if not entry.isfile():
                raise DistManifestError(_not_regular(info))
            stream = bundle.extractfile(entry)
            if stream is None:  # pragma: no cover - tarfile returns a stream for every regular-file member
                raise DistManifestError(_not_regular(info))
            _copy_capped(stream, fd)
    except tarfile.TarError as err:
        raise DistManifestError(f"the release archive is not a readable tar archive: {err}") from err


def _extract_zip_member(archive: IO[bytes], member: str, fd: int) -> None:
    """Stream one member out of a zip archive.

    Args:
        archive: The verified archive.
        member: Base name of the wanted member.
        fd: Descriptor the member's bytes are written to.

    Raises:
        DistManifestError: The archive is unreadable, or its `member` is
            missing, ambiguous, or not a regular file.
        DownloadError: The member exceeds `_dist.ARTIFACT_MAX_BYTES`.
    """
    try:
        with zipfile.ZipFile(archive) as bundle:
            info = _only_member([item.filename for item in bundle.infolist()], member)
            entry = bundle.getinfo(info)
            if entry.is_dir() or stat.S_ISLNK(entry.external_attr >> 16):
                raise DistManifestError(_not_regular(info))
            with bundle.open(entry) as stream:
                _copy_capped(stream, fd)
    except zipfile.BadZipFile as err:
        raise DistManifestError(f"the release archive is not a readable zip archive: {err}") from err


def _only_member(names: list[str], member: str) -> str:
    """Return the single archive entry whose base name is `member`.

    Selection is by base name and the name is never used as a path, so a
    hostile entry cannot steer the write. It is still refused when it is
    absolute or walks upward: an archive that ships one is not the archive we
    were promised.

    Args:
        names: Every entry name in the archive.
        member: Base name of the wanted member.

    Returns:
        The matching entry name.

    Raises:
        DistManifestError: There is no match, more than one, or the match walks
            outside the archive.
    """
    matches = [name for name in names if PurePosixPath(name).name == member]
    if not matches:
        raise DistManifestError(
            f"the release archive holds no member named {member!r}; it holds {', '.join(names) or 'nothing'}."
        )
    if len(matches) > 1:
        raise DistManifestError(
            f"the release archive holds more than one member named {member!r} ({', '.join(matches)}); "
            "the SDK refuses to guess which one is the binary."
        )
    name = matches[0]
    if name.startswith("/") or "\\" in name or ".." in PurePosixPath(name).parts:
        raise DistManifestError(
            f"the release archive member {name!r} is absolute or walks upward (path traversal, CWE-22); "
            "refused even though the SDK never writes an archive name to disk."
        )
    return name


def _not_regular(name: str) -> str:
    """Return the message for an archive member that is not a plain file.

    Args:
        name: The member name.

    Returns:
        The message.
    """
    return (
        f"the release archive member {name!r} is not a regular file; a link or directory where the binary belongs "
        "is a repackaging attack, not a release."
    )


def _copy_capped(stream: IO[bytes], fd: int) -> None:
    """Stream an archive member into `fd`, aborting past the artifact cap.

    Args:
        stream: The member's bytes.
        fd: Descriptor to write to.

    Raises:
        DownloadError: The member exceeds `_dist.ARTIFACT_MAX_BYTES`.
    """
    written = 0
    while chunk := stream.read(_CHUNK):
        written += len(chunk)
        if written > _dist.ARTIFACT_MAX_BYTES:
            raise DownloadError(
                f"the archive member exceeds the {_dist.ARTIFACT_MAX_BYTES} byte cap; "
                "a decompression bomb was aborted before it filled the cache."
            )
        os.write(fd, chunk)
