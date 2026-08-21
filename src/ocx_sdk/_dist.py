# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The OCX Authors

"""Dist manifest sourcing, validation, and artifact transport (contract C-005).

The manifest published at `setup.ocx.sh` decides which bytes end up on disk, so
everything in this module treats it as untrusted input: fields that flow into a
path or a URL are regex-validated before use, reads are size-capped, and the
sha256 that verifies the manifest body is derived from the *caller-supplied* URL
string before any network I/O — never from a post-redirect URL.

Transport is stdlib `urllib` with two hardening seams:

- **Credentials are bound to an origin.** `Authorization` and caller-supplied
  headers are attached with `add_unredirected_header`, and attached to a request
  at all only when its (scheme, host, port) matches the origin they were given
  for. urllib forwards ordinary headers across hosts on redirect; unredirected
  ones it drops on *every* redirect, same-origin included, so a redirected
  request is always anonymous (CWE-522). A mirror that answers with a redirect
  therefore has to serve the artifact without authentication, or not redirect.
- **Redirects stay on https and are capped.** `_HttpsOnlyRedirectHandler`
  refuses a non-https target — urllib's default permits https→http and even ftp
  downgrades (CWE-319) — and stops after `MAX_REDIRECTS` hops.

Every request identifies itself as `ocx-sdk/<version>`. Not decoration: the
CDN in front of `setup.ocx.sh` answers urllib's default `Python-urllib/3.x`
with a 403, so an unnamed client cannot reach the canonical dist host at all.
It is an ordinary header, so it survives redirects the way a user agent
should.

Retrying is a transport-condition decision made here and executed by `_retry`:
connection resets, timeouts, truncated or malformed responses, 429, and 5xx are
worth another attempt. 401, 403, 404, size-cap aborts, and every checksum
mismatch are not. Every attempt starts by discarding whatever the last one
wrote, so a retry after a half-read body cannot append to it.
"""

from __future__ import annotations

import base64
import hashlib
import http.client
import importlib.metadata
import json
import os
import re
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import IO, Final, cast
from urllib.parse import urlsplit

from ._errors import ChecksumMismatchError, DistManifestError, DownloadError, UnsupportedPlatformError
from ._retry import run_with_retry
from ._types import Auth, BasicAuth, Channel, InstallEnv, RetryPolicy

DEFAULT_DIST_URL: Final = "https://setup.ocx.sh/dist.json"
"""Rolling manifest of the canonical dist host."""

CANONICAL_DIST_HOST: Final = "setup.ocx.sh"
"""The one host whose TLS identity the SDK accepts as manifest authenticity."""

MANIFEST_MAX_BYTES: Final = 1 << 20
"""Read cap for a manifest body (1 MiB); the live manifest is ~60 KiB."""

ARTIFACT_MAX_BYTES: Final = 256 << 20
"""Read cap for a release artifact (256 MiB), and for anything unpacked from one."""

MAX_REDIRECTS: Final = 5
"""Redirect hops allowed per request. urllib's own default is 10."""

SCHEMA_VERSION: Final = 1
"""Manifest envelope schema this SDK reads. Anything else fails closed."""

_NO_HEADERS: Final[Mapping[str, str]] = MappingProxyType({})
"""Shared empty header mapping for sources that carry none."""

_CHUNK: Final = 1 << 16
"""Read size for streamed bodies."""

_HTTPS_PORT: Final = 443
"""Port an https URL means when it names none."""

_TOO_MANY_REQUESTS: Final = 429
"""The one 4xx worth retrying."""

_NAME_RE: Final = re.compile(r"^[A-Za-z0-9._+-]+$")
"""Manifest fields that become a path component. Separators, backslashes, and
CRLF fall outside the class; `..` is rejected separately, since the class
matches it happily."""

_VERSION_RE: Final = re.compile(r"^[0-9]+(?:\.[0-9]+){1,2}(?:[-+][A-Za-z0-9.-]+)?$")
"""Semver-shaped version, with an optional pre-release or build suffix."""

_SHA256_RE: Final = re.compile(r"^[0-9a-f]{64}$")
"""A digest as the manifest spells it: lowercase hex, no prefix."""

_SNAPSHOT_RE: Final = re.compile(r"/dist/([0-9a-f]{64})\.json$")
"""Content-addressed snapshot path, whose name *is* the expected digest."""


@dataclass(frozen=True, slots=True)
class Release:
    """One validated row of the dist manifest.

    Every field arrived from the network and passed `_validate_row` before this
    object existed: `filename`, `tag`, and `target` are plain names, `version`
    is semver-shaped, `sha256` is 64 lowercase hex digits, and `url` is an
    https URL without userinfo.

    Attributes:
        version: Release version, without the `v` prefix.
        channel: `stable` or `next`.
        tag: Git tag the release was cut from, e.g. `v0.5.8`.
        target: cargo-dist triple, e.g. `x86_64-unknown-linux-musl`.
        filename: Archive file name; its extension picks the unpacker.
        sha256: Digest of the archive — the artifact trust boundary.
        url: Where the archive lives, before any `mirror_url` rewrite.
    """

    version: str
    channel: str
    tag: str
    target: str
    filename: str
    sha256: str
    url: str


@dataclass(frozen=True, slots=True)
class DistManifest:
    """A parsed, fully validated dist manifest.

    Attributes:
        releases: Every row, in manifest order.
        latest: Version the manifest calls latest on the stable channel.
        latest_next: Version the manifest calls latest on the next channel.
        schema: Envelope schema number.
    """

    releases: tuple[Release, ...]
    latest: str | None = None
    latest_next: str | None = None
    schema: int = 1

    def select(self, *, version: str | None, channel: Channel, target: str) -> Release:
        """Return the row for a version (or the channel's latest) on a platform.

        Args:
            version: Exact version to pin. `None` takes the channel's latest.
            channel: Channel consulted when `version` is `None`.
            target: cargo-dist triple the caller runs on.

        Returns:
            The single row matching both version and target.

        Raises:
            DistManifestError: The channel names no latest version, or the
                manifest holds no row for the requested version at all.
            UnsupportedPlatformError: The version exists but ships no build for
                `target`.
        """
        wanted = version or (self.latest if channel is Channel.STABLE else self.latest_next)
        if wanted is None:
            raise DistManifestError(
                f"the manifest names no latest release on the {channel} channel; "
                "pin one with `ensure(version=...)`, or switch channels."
            )
        rows = [release for release in self.releases if release.version == wanted]
        if not rows:
            offered = ", ".join(sorted({release.version for release in self.releases})) or "nothing"
            raise DistManifestError(f"the manifest holds no release {wanted}; it offers {offered}.")
        match = next((release for release in rows if release.target == target), None)
        if match is None:
            targets = ", ".join(sorted({release.target for release in rows}))
            raise UnsupportedPlatformError(f"ocx {wanted} ships no build for {target}; the manifest offers {targets}.")
        return match


@dataclass(frozen=True, slots=True)
class Credentials:
    """Request headers bound to the origin they were supplied for.

    A credential is only ever sent back to the (scheme, host, port) the caller
    named. Anything else — a redirect to another host, a `mirror_url` pointing
    somewhere new — gets an anonymous request instead of a leaked token.

    Attributes:
        origin: The (scheme, host, port) the headers belong to; `None` for a
            source that reaches no network at all.
        auth: Credentials rendered into an `Authorization` header.
        headers: Extra caller-supplied headers, bound to the same origin
            because they are just as likely to carry a secret.
    """

    origin: tuple[str, str, int] | None = None
    auth: Auth | None = None
    headers: Mapping[str, str] = field(default=_NO_HEADERS, repr=False)

    def headers_for(self, url: str) -> dict[str, str]:
        """Return the headers to attach to a request for `url`.

        Args:
            url: The request target.

        Returns:
            The bound headers when `url`'s origin matches, otherwise an empty
            mapping.
        """
        if self.origin is None or _origin_of(url) != self.origin:
            return {}
        headers = dict(self.headers)
        if self.auth is not None:
            headers["Authorization"] = _authorization(self.auth)
        return headers


@dataclass(frozen=True, slots=True)
class DistSource:
    """Where a dist manifest comes from, and how its body is trusted.

    Build one with `url()`, `path()`, or `data()` rather than the constructor.
    A source with no location at all is the canonical default: it resolves to
    `OCX_INSTALL_DIST_URL` when the environment sets one, and to
    `DEFAULT_DIST_URL` otherwise. A source built with an explicit URL never
    consults the environment.

    Attributes:
        manifest_url: Explicit manifest URL, or `None` for the default source.
        manifest_path: Local manifest file.
        manifest_data: Manifest bytes the caller already holds.
        sha256: Expected digest of the manifest body. Auto-derived from a
            `dist/<sha256>.json` URL, and *required* for any URL source that is
            off-canonical or paired with a mirror.
        auth: Credentials for the manifest host, bound to its origin.
        headers: Extra request headers, bound to the same origin.

    Example:
        >>> DistSource.url().manifest_url is None
        True
    """

    manifest_url: str | None = None
    manifest_path: Path | None = None
    manifest_data: bytes | None = field(default=None, repr=False)
    sha256: str | None = None
    auth: Auth | None = field(default=None, repr=False)
    headers: Mapping[str, str] = field(default=_NO_HEADERS, repr=False)

    def __post_init__(self) -> None:
        located = [self.manifest_url, self.manifest_path, self.manifest_data]
        if sum(location is not None for location in located) > 1:
            raise DistManifestError(
                "a dist source carries exactly one of a URL, a path, or raw data — "
                "build it with DistSource.url(), .path(), or .data()."
            )
        if self.sha256 is not None and not _SHA256_RE.fullmatch(self.sha256):
            raise DistManifestError(
                f"sha256={self.sha256!r} is not 64 lowercase hex digits, which is how a dist manifest spells a digest."
            )
        object.__setattr__(self, "headers", MappingProxyType(dict(self.headers)))

    @classmethod
    def url(
        cls,
        url: str | None = None,
        sha256: str | None = None,
        auth: Auth | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> DistSource:
        """Fetch the manifest over https.

        Args:
            url: Manifest URL. `None` keeps the default source, which honors
                `OCX_INSTALL_DIST_URL` from the environment.
            sha256: Expected digest of the manifest body. Required whenever the
                host is not `setup.ocx.sh` or a mirror is in play; derived
                automatically from a `dist/<sha256>.json` URL.
            auth: Credentials for this origin, and this origin only.
            headers: Extra request headers, bound to the same origin.

        Returns:
            The source.

        Raises:
            DistManifestError: `sha256` is not 64 lowercase hex digits.
        """
        return cls(manifest_url=url, sha256=sha256, auth=auth, headers=headers or _NO_HEADERS)

    @classmethod
    def path(cls, path: Path | str, sha256: str | None = None) -> DistSource:
        """Read the manifest from a local file.

        Args:
            path: File holding the manifest JSON.
            sha256: Expected digest of the file contents.

        Returns:
            The source.

        Raises:
            DistManifestError: `sha256` is not 64 lowercase hex digits.
        """
        return cls(manifest_path=Path(path), sha256=sha256)

    @classmethod
    def data(cls, raw: bytes, sha256: str | None = None) -> DistSource:
        """Use manifest bytes the caller already has.

        The vendoring path: ship a `dist/<sha256>.json` snapshot as package data
        and read it back with `importlib.resources`, which works from a zipped
        wheel where a filesystem path does not.

        Args:
            raw: The manifest body.
            sha256: Expected digest of `raw`.

        Returns:
            The source.

        Raises:
            DistManifestError: `sha256` is not 64 lowercase hex digits.
        """
        return cls(manifest_data=raw, sha256=sha256)

    def resolve_url(self, env: Mapping[str, str] | None = None) -> str | None:
        """Return the URL this source fetches from.

        Args:
            env: Environment consulted for `OCX_INSTALL_DIST_URL`; only a
                source without an explicit location reads it.

        Returns:
            The manifest URL, or `None` for a local or in-memory source.
        """
        if self.manifest_path is not None or self.manifest_data is not None:
            return None
        if self.manifest_url is not None:
            return self.manifest_url
        return (env or {}).get(InstallEnv.DIST_URL) or DEFAULT_DIST_URL

    def credentials(self, env: Mapping[str, str] | None = None) -> Credentials:
        """Return this source's credentials, bound to its resolved origin.

        Args:
            env: Environment used to resolve the URL, as in `resolve_url`.

        Returns:
            Credentials whose origin is the resolved URL's, or an unbound empty
            set for a local or in-memory source.

        Raises:
            DistManifestError: The resolved URL is not usable — not https, or
                carrying userinfo.
        """
        url = self.resolve_url(env)
        if url is None:
            return Credentials()
        return Credentials(validate_url(url, what="manifest URL"), self.auth, self.headers)


def parse_manifest(raw: bytes) -> DistManifest:
    """Decode and validate a manifest body.

    Unknown envelope and row keys are ignored; a row that fails validation
    fails the whole manifest, because a manifest that can carry one hostile
    field can carry the one we were about to use.

    Args:
        raw: The manifest body.

    Returns:
        The validated manifest.

    Raises:
        DistManifestError: The body is not JSON, not an envelope object,
            declares a schema this SDK does not read, or carries a field that
            fails validation.
    """
    try:
        payload: object = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as err:
        raise DistManifestError(f"the manifest body is not valid JSON: {err}") from err
    if not isinstance(payload, dict):
        raise DistManifestError(f"the manifest body is a {type(payload).__name__}, not the expected JSON object.")
    envelope = cast("dict[str, object]", payload)
    schema = envelope.get("schema")
    if not isinstance(schema, int) or isinstance(schema, bool):
        raise DistManifestError(f"the manifest schema field is {schema!r}, not an integer.")
    if schema != SCHEMA_VERSION:
        raise DistManifestError(
            f"the manifest declares schema {schema}, and this SDK only reads schema {SCHEMA_VERSION}. "
            "Upgrade ocx-sdk — guessing at an envelope shape we have never seen is how a parser gets it wrong."
        )
    rows = envelope.get("releases")
    if not isinstance(rows, list):
        raise DistManifestError(f"the manifest releases field is {type(rows).__name__}, not a list of release rows.")
    releases = cast("list[object]", rows)
    return DistManifest(
        releases=tuple(_validate_row(row) for row in releases),
        latest=_channel_version(envelope, "latest"),
        latest_next=_channel_version(envelope, "latest_next"),
        schema=schema,
    )


def load_manifest(
    source: DistSource,
    *,
    env: Mapping[str, str] | None = None,
    mirrored: bool = False,
    timeout: float | None = None,
    retry: RetryPolicy | None = None,
    opener: urllib.request.OpenerDirector | None = None,
) -> DistManifest:
    """Read a manifest from `source`, verify its digest, and validate it.

    The pin requirement is checked before any I/O: an off-canonical host or an
    active mirror without a `sha256` is refused rather than fetched, because on
    a host that is not `setup.ocx.sh` TLS proves reachability, not authenticity
    (CWE-345).

    Args:
        source: Where the manifest comes from.
        env: Environment for `OCX_INSTALL_DIST_URL` resolution.
        mirrored: Whether artifacts will be pulled from a mirror, which makes
            the manifest pin mandatory.
        timeout: Per-attempt budget in seconds.
        retry: Policy for transient transport failures; `None` tries once.
        opener: urllib opener seam. `None` builds the hardened default.

    Returns:
        The validated manifest.

    Raises:
        DistManifestError: A pin is required and missing, the URL is unusable,
            or the body fails validation.
        ChecksumMismatchError: The body does not match the expected digest.
        DownloadError: The manifest could not be read, or exceeded
            `MANIFEST_MAX_BYTES`.
    """
    url = source.resolve_url(env)
    origin = None if url is None else validate_url(url, what="manifest URL")
    expected = _expected_digest(source, url, origin, mirrored=mirrored)
    if url is None:
        raw = source.manifest_data if source.manifest_data is not None else _read_file(source.manifest_path)
    else:
        chunks: list[bytes] = []
        _fetch(
            url,
            headers=Credentials(origin, source.auth, source.headers).headers_for(url),
            limit=MANIFEST_MAX_BYTES,
            sink=chunks.append,
            reset=chunks.clear,
            timeout=timeout,
            retry=retry,
            opener=opener,
        )
        raw = b"".join(chunks)
    if expected is not None and hashlib.sha256(raw).hexdigest() != expected:
        raise ChecksumMismatchError(
            f"the manifest body does not match the expected sha256 {expected}; "
            "something other than the pinned snapshot served these bytes."
        )
    return parse_manifest(raw)


def fetch_artifact(
    url: str,
    fd: int,
    *,
    credentials: Credentials | None = None,
    timeout: float | None = None,
    retry: RetryPolicy | None = None,
    opener: urllib.request.OpenerDirector | None = None,
) -> None:
    """Stream a release artifact into an open file descriptor.

    The descriptor is truncated at the start of every attempt, so a retry after
    a half-written response cannot concatenate two partial bodies.

    Args:
        url: Artifact URL, validated as https before the request is built.
        fd: Descriptor to write to, positioned at the start.
        credentials: Origin-bound headers; attached only if `url` matches.
        timeout: Per-attempt budget in seconds.
        retry: Policy for transient transport failures; `None` tries once.
        opener: urllib opener seam. `None` builds the hardened default.

    Raises:
        DistManifestError: `url` is not a usable https URL.
        DownloadError: The artifact could not be read, or exceeded
            `ARTIFACT_MAX_BYTES`.
    """
    validate_url(url, what="artifact URL")

    def reset() -> None:
        os.ftruncate(fd, 0)
        os.lseek(fd, 0, os.SEEK_SET)

    def sink(chunk: bytes) -> None:
        os.write(fd, chunk)

    _fetch(
        url,
        headers={} if credentials is None else credentials.headers_for(url),
        limit=ARTIFACT_MAX_BYTES,
        sink=sink,
        reset=reset,
        timeout=timeout,
        retry=retry,
        opener=opener,
    )


class _HttpsOnlyRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Redirect handler that refuses downgrades and caps the hop count."""

    max_redirections = MAX_REDIRECTS

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: IO[bytes],
        code: int,
        msg: str,
        headers: http.client.HTTPMessage,
        newurl: str,
    ) -> urllib.request.Request | None:
        """Return the follow-up request for a redirect, or refuse it.

        The follow-up carries no credentials by construction: everything this
        module attaches is an unredirected header, and urllib copies only the
        ordinary ones onto a redirect. That holds for a same-origin redirect
        too — the drop is unconditional, not an origin comparison.

        Args:
            req: The request that was redirected.
            fp: The response body.
            code: The redirect status code.
            msg: The status message.
            headers: The response headers.
            newurl: The redirect target.

        Returns:
            The follow-up request.

        Raises:
            DownloadError: The target is not an https URL, or carries userinfo.
        """
        parts = urlsplit(newurl)
        if parts.scheme != "https":
            raise DownloadError(
                f"refused a redirect from {req.full_url} to {newurl}: only https redirects are followed."
            )
        if parts.username or parts.password:
            raise DownloadError(f"refused a redirect from {req.full_url}: the target URL carries userinfo.")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _origin_of(url: str) -> tuple[str, str, int] | None:
    """Return the (scheme, host, port) a URL names, or `None` if it names none.

    Args:
        url: The URL to split.

    Returns:
        The origin, with the default https port filled in.
    """
    parts = urlsplit(url)
    try:
        port = parts.port
    except ValueError:
        return None
    if not parts.hostname:
        return None
    return (parts.scheme, parts.hostname, port or _HTTPS_PORT)


def validate_url(url: str, *, what: str) -> tuple[str, str, int]:
    """Return the (scheme, host, port) of an https URL, refusing anything else.

    Args:
        url: The URL to check.
        what: Name of the thing being checked, for the error message.

    Returns:
        The origin the URL belongs to.

    Raises:
        DistManifestError: The URL is not https, carries userinfo, or names no
            usable host.
    """
    origin = _origin_of(url)
    if origin is None:
        raise DistManifestError(f"{what} {url!r} names no usable host.")
    if origin[0] != "https":
        raise DistManifestError(f"{what} {url!r} is not https; the SDK downloads over TLS only.")
    parts = urlsplit(url)
    if parts.username or parts.password:
        raise DistManifestError(
            f"{what} carries userinfo; pass credentials as `auth=` so they stay bound to one origin."
        )
    return origin


def _expected_digest(
    source: DistSource, url: str | None, origin: tuple[str, str, int] | None, *, mirrored: bool
) -> str | None:
    """Return the digest the manifest body must have, refusing an unpinned fetch.

    A `dist/<sha256>.json` URL pins itself: the digest comes out of the
    caller-supplied URL string before any request goes out, so a redirect can
    never choose which digest the body is checked against.

    Local sources are exempt from the pin requirement. The rule exists because
    TLS to a non-canonical host proves reachability rather than authenticity;
    bytes the caller already holds have provenance instead, and hashing them
    against a digest derived from themselves would prove nothing.

    Args:
        source: The source being read.
        url: Its resolved URL, or `None` for a local source.
        origin: The URL's origin, or `None` for a local source.
        mirrored: Whether a mirror is in play.

    Returns:
        The expected digest, or `None` when none is required.

    Raises:
        DistManifestError: A pin is required and missing, or the URL and the
            explicit `sha256=` name different digests.
    """
    snapshot = _SNAPSHOT_RE.search(urlsplit(url).path) if url is not None else None
    derived = snapshot.group(1) if snapshot is not None else None
    if source.sha256 is not None and derived is not None and source.sha256 != derived:
        raise DistManifestError(
            f"sha256={source.sha256} disagrees with the digest the snapshot URL names ({derived}); drop one of them."
        )
    expected = source.sha256 or derived
    if url is None or expected is not None:
        return expected
    if mirrored or origin != ("https", CANONICAL_DIST_HOST, _HTTPS_PORT):
        raise DistManifestError(
            f"reading {url} needs an explicit sha256= and refuses to fetch without one: off {CANONICAL_DIST_HOST}, "
            "or with a mirror in play, TLS proves reachability rather than authenticity (CWE-345). Pin a "
            "dist/<sha256>.json snapshot, or pass sha256=."
        )
    return None


def _read_file(path: Path | None) -> bytes:
    """Return the contents of a local manifest file, capped.

    Args:
        path: The file to read.

    Returns:
        The manifest body.

    Raises:
        DownloadError: The file could not be read, or exceeds
            `MANIFEST_MAX_BYTES`.
    """
    try:
        with Path(f"{path}").open("rb") as handle:
            raw = handle.read(MANIFEST_MAX_BYTES + 1)
    except OSError as err:
        raise DownloadError(f"could not read the manifest at {path}: {err}") from err
    if len(raw) > MANIFEST_MAX_BYTES:
        raise DownloadError(f"the manifest at {path} exceeds the {MANIFEST_MAX_BYTES} byte cap.")
    return raw


def _channel_version(envelope: dict[str, object], key: str) -> str | None:
    """Return the version a channel pointer names.

    Args:
        envelope: The manifest envelope.
        key: `latest` or `latest_next`.

    Returns:
        The version, or `None` when the channel has no release yet.

    Raises:
        DistManifestError: The pointer is missing, is not an object, or names a
            version that fails validation.
    """
    if key not in envelope:
        raise DistManifestError(f"the manifest is missing its {key} channel pointer.")
    pointer = envelope[key]
    if pointer is None:
        return None
    if not isinstance(pointer, dict):
        raise DistManifestError(f"the manifest {key} pointer is {pointer!r}, not an object naming a version.")
    fields = cast("dict[str, object]", pointer)
    return _validate_field(fields, "version", _VERSION_RE, what=f"{key} channel pointer")


def _validate_row(row: object) -> Release:
    """Return one validated release row.

    Args:
        row: The decoded JSON row.

    Returns:
        The release.

    Raises:
        DistManifestError: The row is not an object, or any field fails
            validation.
    """
    if not isinstance(row, dict):
        raise DistManifestError(f"a release row is {row!r}, not a JSON object.")
    fields = cast("dict[str, object]", row)
    url = fields.get("url")
    if not isinstance(url, str):
        raise DistManifestError(f"release field url={url!r} is missing or not a string.")
    validate_url(url, what="release url")
    return Release(
        version=_validate_field(fields, "version", _VERSION_RE, what="release"),
        channel=_validate_field(fields, "channel", _NAME_RE, what="release"),
        tag=_validate_field(fields, "tag", _NAME_RE, what="release"),
        target=_validate_field(fields, "target", _NAME_RE, what="release"),
        filename=_validate_field(fields, "filename", _NAME_RE, what="release"),
        sha256=_validate_field(fields, "sha256", _SHA256_RE, what="release"),
        url=url,
    )


def _validate_field(fields: dict[str, object], name: str, pattern: re.Pattern[str], *, what: str) -> str:
    """Return one manifest string field, or refuse the whole manifest.

    `fullmatch` rather than `match`: `$` also matches just before a trailing
    newline, so a value ending in one would pass a pattern written to forbid
    exactly that.

    Args:
        fields: The row or pointer holding the field.
        name: Field name.
        pattern: Pattern the value must match completely.
        what: What is being validated, for the error message.

    Returns:
        The validated value.

    Raises:
        DistManifestError: The field is missing, is not a string, fails the
            pattern, or walks upward.
    """
    value = fields.get(name)
    if not isinstance(value, str) or not pattern.fullmatch(value) or ".." in value:
        raise DistManifestError(
            f"{what} field {name}={value!r} fails validation against {pattern.pattern}; a manifest field that "
            "becomes a path or a URL is untrusted input (CWE-22)."
        )
    return value


def _authorization(auth: Auth) -> str:
    """Render credentials into an `Authorization` header value.

    Args:
        auth: The credentials.

    Returns:
        The header value.
    """
    if isinstance(auth, BasicAuth):
        return f"Basic {base64.b64encode(f'{auth.user}:{auth.password}'.encode()).decode('ascii')}"
    return f"Bearer {auth.token}"


def _transient(err: Exception) -> bool:
    """Report whether a transport failure is worth another attempt.

    Args:
        err: The failure raised by an attempt.

    Returns:
        True for connection resets, timeouts, a truncated or malformed response
        (`http.client.HTTPException`), 429, and 5xx. A 401, 403, or 404 is an
        answer rather than a hiccup, and a size-cap abort is not a transport
        condition at all.
    """
    if isinstance(err, urllib.error.HTTPError):
        return err.code == _TOO_MANY_REQUESTS or 500 <= err.code < 600
    if isinstance(err, http.client.HTTPException):
        return True
    reason = err.reason if isinstance(err, urllib.error.URLError) else err
    return isinstance(reason, TimeoutError | ConnectionError)


def _retry_after(err: Exception) -> float | None:
    """Return the delay a server asked for, in seconds.

    Only the delta-seconds form is honored; the HTTP-date form and a negative
    delay are treated as no answer, which leaves the caller's own backoff in
    charge rather than letting a header turn a retry into a busy loop.

    Args:
        err: The failure raised by an attempt.

    Returns:
        The requested delay, or `None`.
    """
    if not isinstance(err, urllib.error.HTTPError):
        return None
    requested = err.headers.get("Retry-After")
    if requested is None:
        return None
    try:
        seconds = float(requested.strip())
    except ValueError:
        return None
    return seconds if seconds >= 0 else None


def _opener(transport: urllib.request.BaseHandler | None = None) -> urllib.request.OpenerDirector:
    """Build an opener whose redirects are https-only and hop-capped.

    Passing the handler subclass makes `build_opener` drop urllib's permissive
    default rather than stack ours on top of it.

    Args:
        transport: Extra handler, replacing the default HTTPS transport in
            tests.

    Returns:
        The opener.
    """
    if transport is None:
        return urllib.request.build_opener(_HttpsOnlyRedirectHandler)
    return urllib.request.build_opener(_HttpsOnlyRedirectHandler, transport)


def _user_agent() -> str:
    """Return the `User-Agent` every request identifies itself with.

    Resolved rather than hardcoded so the dist host can tell which SDK
    versions are in the wild. Read here instead of from `ocx_sdk.__version__`
    to keep this module a leaf: the package root imports it, not the reverse.
    """
    try:
        return f"ocx-sdk/{importlib.metadata.version('ocx-sdk')}"
    except importlib.metadata.PackageNotFoundError:
        return "ocx-sdk/0.0.0+unknown"


def _fetch(
    url: str,
    *,
    headers: Mapping[str, str],
    limit: int,
    sink: Callable[[bytes], None],
    reset: Callable[[], None],
    timeout: float | None = None,
    retry: RetryPolicy | None = None,
    opener: urllib.request.OpenerDirector | None = None,
) -> None:
    """Read `url` into `sink`, capped, retried, and credential-bound.

    Args:
        url: An already-validated https URL.
        headers: Headers to attach as unredirected headers.
        limit: Maximum number of bytes to accept before aborting.
        sink: Receives each chunk in order.
        reset: Discards a partial body; called before every attempt, so a retry
            after a truncated response cannot append to what the last one left
            behind.
        timeout: Per-attempt budget in seconds.
        retry: Policy for transient failures; `None` tries once.
        opener: urllib opener seam.

    Raises:
        DownloadError: The read failed, or the body exceeded `limit`.
    """
    client = opener or _opener()

    def attempt() -> None:
        reset()
        request = urllib.request.Request(url, method="GET")
        request.add_header("User-Agent", _user_agent())
        for name, value in headers.items():
            request.add_unredirected_header(name, value)
        with client.open(request, timeout=timeout) as response:
            read = 0
            while chunk := response.read(_CHUNK):
                read += len(chunk)
                if read > limit:
                    raise DownloadError(f"{url} exceeds the {limit} byte cap; the download was aborted.")
                sink(chunk)

    try:
        if retry is None:
            attempt()
        else:
            run_with_retry(attempt, retry, _transient, retry_after=_retry_after)
    except (OSError, http.client.HTTPException) as err:
        raise DownloadError(f"could not read {url}: {err}") from err
