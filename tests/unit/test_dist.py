# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The OCX Authors

"""Specification for `_dist` (contract C-005, design §5).

The manifest is untrusted input and the transport carries credentials, so most
of this file is about what the module *refuses*: unpinned off-canonical hosts,
traversal in a field that becomes a path, a redirect that downgrades to http or
hops forever, and an `Authorization` header following a redirect to a host the
caller never named.

No test here reaches the network. `_FakeTransport` replaces urllib's https
transport inside a real `OpenerDirector`, so the production redirect handler,
error processor, and header plumbing all run for real.
"""

from __future__ import annotations

import hashlib
import http.client
import importlib.metadata
import io
import json
import re
import urllib.error
import urllib.request
import urllib.response
from dataclasses import dataclass, field
from typing import IO, Any, cast

import pytest

from ocx_sdk._dist import (
    ARTIFACT_MAX_BYTES,
    CANONICAL_DIST_HOST,
    DEFAULT_DIST_URL,
    MANIFEST_MAX_BYTES,
    MAX_REDIRECTS,
    Credentials,
    DistManifest,
    DistSource,
    Release,
    _HttpsOnlyRedirectHandler,
    _opener,
    _user_agent,
    fetch_artifact,
    load_manifest,
    parse_manifest,
)
from ocx_sdk._errors import ChecksumMismatchError, DistManifestError, DownloadError, UnsupportedPlatformError
from ocx_sdk._types import BasicAuth, BearerAuth, Channel, InstallEnv, RetryPolicy

_TARGET = "x86_64-unknown-linux-musl"
_OTHER_TARGET = "aarch64-apple-darwin"
_ROW_SHA = "0" * 64
_CANONICAL = DEFAULT_DIST_URL
_MIRROR = "https://mirror.example/dist.json"


def _row(**overrides: object) -> dict[str, object]:
    """Return one manifest row, with overrides applied."""
    row: dict[str, object] = {
        "version": "0.5.8",
        "channel": "stable",
        "tag": "v0.5.8",
        "target": _TARGET,
        "filename": f"ocx-{_TARGET}.tar.gz",
        "sha256": _ROW_SHA,
        "url": f"https://github.com/ocx-sh/ocx/releases/download/v0.5.8/ocx-{_TARGET}.tar.gz",
    }
    row.update(overrides)
    return row


def _envelope(rows: list[dict[str, object]] | None = None, **overrides: object) -> dict[str, object]:
    """Return a manifest envelope shaped like the live one, with overrides."""
    envelope: dict[str, object] = {
        "latest": {"channel": "stable", "version": "0.5.8"},
        "latest_next": None,
        "releases": [_row()] if rows is None else rows,
        "schema": 1,
    }
    envelope.update(overrides)
    return envelope


def _body(envelope: dict[str, object] | None = None) -> bytes:
    """Return an encoded manifest body."""
    return json.dumps(_envelope() if envelope is None else envelope).encode()


def _digest(raw: bytes) -> str:
    """Return the sha256 of a body, as the manifest spells it."""
    return hashlib.sha256(raw).hexdigest()


def _message(values: dict[str, str]) -> http.client.HTTPMessage:
    """Return response headers."""
    message = http.client.HTTPMessage()
    for name, value in values.items():
        message[name] = value
    return message


@dataclass
class _Reply:
    """One canned https response."""

    body: bytes = b""
    status: int = 200
    headers: dict[str, str] = field(default_factory=dict[str, str])
    stall: Exception | None = None


class _StallingBody(io.RawIOBase):
    """A body that hands out one chunk and then fails mid-stream."""

    def __init__(self, chunk: bytes, error: Exception) -> None:
        self._chunk = chunk
        self._error = error
        self._sent = False

    def readable(self) -> bool:
        return True

    def read(self, size: int = -1) -> bytes:
        if self._sent:
            raise self._error
        self._sent = True
        return self._chunk


class _FakeTransport(urllib.request.HTTPSHandler):
    """In-memory https transport, driven by a per-URL queue of replies."""

    def __init__(self, routes: dict[str, _Reply | list[_Reply | Exception]]) -> None:
        self.routes: dict[str, list[_Reply | Exception]] = {
            url: [value] if isinstance(value, _Reply) else list(value) for url, value in routes.items()
        }
        self.requests: list[urllib.request.Request] = []

    def https_open(self, req: urllib.request.Request) -> Any:
        return self._reply(req)

    def http_open(self, req: urllib.request.Request) -> Any:
        return self._reply(req)

    def _reply(self, req: urllib.request.Request) -> Any:
        self.requests.append(req)
        queue = self.routes.get(req.full_url)
        if not queue:
            raise urllib.error.URLError(f"no route for {req.full_url}")
        reply = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(reply, Exception):
            raise reply
        streamed = io.BytesIO(reply.body) if reply.stall is None else _StallingBody(reply.body, reply.stall)
        body = cast("IO[bytes]", streamed)
        response = urllib.response.addinfourl(body, _message(reply.headers), req.full_url, reply.status)
        response.msg = "canned"  # pyright: ignore[reportAttributeAccessIssue]
        return response


def _opener_for(transport: _FakeTransport) -> urllib.request.OpenerDirector:
    """Return a production opener whose https transport is `transport`."""
    return _opener(transport)


def _authorization(request: urllib.request.Request) -> str | None:
    """Return the Authorization header a request carries, from either bucket."""
    merged = {name.lower(): value for name, value in {**request.headers, **request.unredirected_hdrs}.items()}
    return merged.get("authorization")


# --- source construction --------------------------------------------------


def test_url_source_defaults_to_the_canonical_manifest():
    source = DistSource.url()

    assert source.resolve_url({}) == DEFAULT_DIST_URL


def test_default_url_source_honors_install_dist_url():
    source = DistSource.url()

    assert source.resolve_url({InstallEnv.DIST_URL: _MIRROR}) == _MIRROR


def test_explicit_url_source_ignores_install_dist_url():
    source = DistSource.url(_CANONICAL)

    assert source.resolve_url({InstallEnv.DIST_URL: _MIRROR}) == _CANONICAL


@pytest.mark.parametrize(
    "source",
    [
        pytest.param(DistSource.path("/tmp/dist.json"), id="path-source"),
        pytest.param(DistSource.data(b"{}"), id="data-source"),
    ],
)
def test_local_sources_resolve_no_url(source: DistSource):
    assert source.resolve_url({InstallEnv.DIST_URL: _MIRROR}) is None


def test_source_rejects_two_locations():
    with pytest.raises(DistManifestError, match="exactly one of"):
        DistSource(manifest_url=_CANONICAL, manifest_data=b"{}")


@pytest.mark.parametrize(
    "sha256",
    [
        pytest.param("A" * 64, id="uppercase"),
        pytest.param("0" * 63, id="too-short"),
        pytest.param("g" * 64, id="not-hex"),
        pytest.param("", id="empty"),
    ],
)
def test_source_rejects_malformed_sha256(sha256: str):
    with pytest.raises(DistManifestError, match="64 lowercase hex"):
        DistSource.url(_CANONICAL, sha256)


def test_source_repr_hides_credentials_and_body():
    source = DistSource.data(b"secret-manifest-bytes", _digest(b"x"))
    bearer = DistSource.url(_CANONICAL, None, BearerAuth("ghp_topsecret"), {"X-Proxy-Token": "shibboleth"})

    text = f"{source!r} {bearer!r}"

    assert "secret-manifest-bytes" not in text
    assert "ghp_topsecret" not in text
    assert "shibboleth" not in text


# --- the pin requirement (S-003, fail closed before any I/O) --------------


def test_sha256_required_off_canonical():
    transport = _FakeTransport({_MIRROR: _Reply(_body())})

    with pytest.raises(DistManifestError, match="sha256"):
        load_manifest(DistSource.url(_MIRROR), opener=_opener_for(transport))

    assert transport.requests == []


def test_sha256_required_when_mirrored():
    transport = _FakeTransport({_CANONICAL: _Reply(_body())})

    with pytest.raises(DistManifestError, match="sha256"):
        load_manifest(DistSource.url(), mirrored=True, opener=_opener_for(transport))

    assert transport.requests == []


def test_sha256_required_when_env_points_off_canonical():
    transport = _FakeTransport({_MIRROR: _Reply(_body())})

    with pytest.raises(DistManifestError, match="sha256"):
        load_manifest(DistSource.url(), env={InstallEnv.DIST_URL: _MIRROR}, opener=_opener_for(transport))

    assert transport.requests == []


def test_canonical_source_needs_no_pin():
    body = _body()
    transport = _FakeTransport({_CANONICAL: _Reply(body)})

    manifest = load_manifest(DistSource.url(), opener=_opener_for(transport))

    assert manifest.latest == "0.5.8"
    assert CANONICAL_DIST_HOST in transport.requests[0].full_url


def test_snapshot_url_pins_itself_off_canonical():
    body = _body()
    url = f"https://mirror.example/dist/{_digest(body)}.json"
    transport = _FakeTransport({url: _Reply(body)})

    manifest = load_manifest(DistSource.url(url), opener=_opener_for(transport))

    assert manifest.releases[0].version == "0.5.8"


def test_snapshot_url_digest_is_enforced():
    url = f"https://mirror.example/dist/{_digest(b'other bytes')}.json"
    transport = _FakeTransport({url: _Reply(_body())})

    with pytest.raises(ChecksumMismatchError, match="manifest"):
        load_manifest(DistSource.url(url), opener=_opener_for(transport))


def test_snapshot_url_conflicting_with_explicit_sha256_is_refused():
    body = _body()
    url = f"https://mirror.example/dist/{_digest(body)}.json"
    transport = _FakeTransport({url: _Reply(body)})

    with pytest.raises(DistManifestError, match="disagrees"):
        load_manifest(DistSource.url(url, _digest(b"other")), opener=_opener_for(transport))

    assert transport.requests == []


def test_manifest_digest_mismatch_is_a_checksum_error():
    transport = _FakeTransport({_MIRROR: _Reply(_body())})

    with pytest.raises(ChecksumMismatchError, match="manifest"):
        load_manifest(DistSource.url(_MIRROR, _digest(b"other")), opener=_opener_for(transport))


def test_manifest_digest_mismatch_is_never_retried():
    slept: list[float] = []
    transport = _FakeTransport({_MIRROR: _Reply(_body())})
    policy = RetryPolicy(attempts=3, backoff=0.1, jitter=False, sleep=slept.append)

    with pytest.raises(ChecksumMismatchError):
        load_manifest(DistSource.url(_MIRROR, _digest(b"other")), retry=policy, opener=_opener_for(transport))

    assert len(transport.requests) == 1
    assert slept == []


# --- URL hardening --------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "reason"),
    [
        pytest.param("http://setup.ocx.sh/dist.json", "https", id="plaintext"),
        pytest.param("ftp://setup.ocx.sh/dist.json", "https", id="ftp"),
        pytest.param("https://user:pass@setup.ocx.sh/dist.json", "userinfo", id="userinfo"),
        pytest.param("https:///dist.json", "host", id="no-host"),
    ],
)
def test_unusable_manifest_url_is_refused_before_any_request(url: str, reason: str):
    transport = _FakeTransport({url: _Reply(_body())})

    with pytest.raises(DistManifestError, match=reason):
        load_manifest(DistSource.url(url, _digest(_body())), opener=_opener_for(transport))

    assert transport.requests == []


# --- credential binding (S-003, S-006) -----------------------------------


def test_every_request_identifies_itself_as_ocx_sdk():
    """The CDN in front of setup.ocx.sh 403s urllib's default `Python-urllib/3.x`.

    Ordinary, not unredirected: a user agent should survive a redirect the way
    credentials must not.
    """
    body = _body()
    transport = _FakeTransport({DEFAULT_DIST_URL: _Reply(body)})

    load_manifest(DistSource.url(), opener=_opener_for(transport))

    request = transport.requests[0]
    assert request.get_header("User-agent", "").startswith("ocx-sdk/")
    assert "User-agent" not in request.unredirected_hdrs


def test_user_agent_falls_back_when_the_package_metadata_is_missing(monkeypatch: pytest.MonkeyPatch):
    """A source checkout with nothing installed still names itself."""

    def _raise(name: str) -> str:
        raise importlib.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(importlib.metadata, "version", _raise)

    assert _user_agent() == "ocx-sdk/0.0.0+unknown"


def test_auth_travels_as_an_unredirected_header():
    body = _body()
    transport = _FakeTransport({_MIRROR: _Reply(body)})

    load_manifest(DistSource.url(_MIRROR, _digest(body), BasicAuth("ci", "hunter2")), opener=_opener_for(transport))

    request = transport.requests[0]
    assert "Authorization" in request.unredirected_hdrs
    assert "Authorization" not in request.headers


@pytest.mark.parametrize(
    ("auth", "expected"),
    [
        pytest.param(BasicAuth("ci", "hunter2"), "Basic Y2k6aHVudGVyMg==", id="basic"),
        pytest.param(BearerAuth("ghp_x"), "Bearer ghp_x", id="bearer"),
    ],
)
def test_authorization_header_encodes_the_credentials(auth: BasicAuth | BearerAuth, expected: str):
    body = _body()
    transport = _FakeTransport({_MIRROR: _Reply(body)})

    load_manifest(DistSource.url(_MIRROR, _digest(body), auth), opener=_opener_for(transport))

    assert _authorization(transport.requests[0]) == expected


def test_auth_not_forwarded_cross_host():
    body = _body()
    elsewhere = "https://cdn.example/dist.json"
    transport = _FakeTransport(
        {
            _MIRROR: _Reply(status=302, headers={"Location": elsewhere}),
            elsewhere: _Reply(body),
        }
    )

    manifest = load_manifest(
        DistSource.url(_MIRROR, _digest(body), BasicAuth("ci", "hunter2"), {"X-Proxy-Token": "shibboleth"}),
        opener=_opener_for(transport),
    )

    followed = transport.requests[1]
    assert manifest.latest == "0.5.8"
    assert _authorization(followed) is None
    assert not any(name.lower() == "x-proxy-token" for name in {**followed.headers, **followed.unredirected_hdrs})


def test_auth_dropped_on_a_same_origin_redirect():
    body = _body()
    elsewhere = "https://mirror.example/snapshot/dist.json"
    transport = _FakeTransport(
        {
            _MIRROR: _Reply(status=302, headers={"Location": elsewhere}),
            elsewhere: _Reply(body),
        }
    )

    load_manifest(DistSource.url(_MIRROR, _digest(body), BasicAuth("ci", "hunter2")), opener=_opener_for(transport))

    assert _authorization(transport.requests[0]) is not None
    assert _authorization(transport.requests[1]) is None


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        pytest.param("https://mirror.example/a.tar.gz", {"Authorization": "Bearer t"}, id="same-origin"),
        pytest.param("https://other.example/a.tar.gz", {}, id="other-host"),
        pytest.param("https://mirror.example:8443/a.tar.gz", {}, id="other-port"),
        pytest.param("http://mirror.example/a.tar.gz", {}, id="other-scheme"),
    ],
)
def test_credentials_are_bound_to_one_origin(url: str, expected: dict[str, str]):
    credentials = DistSource.url(_MIRROR, _digest(b""), BearerAuth("t")).credentials()

    assert credentials.headers_for(url) == expected


def test_local_source_credentials_are_unbound():
    credentials = DistSource.data(b"{}").credentials()

    assert credentials == Credentials()
    assert credentials.headers_for(_MIRROR) == {}


# --- redirect policy ------------------------------------------------------


def test_redirect_to_plaintext_is_refused():
    transport = _FakeTransport({_CANONICAL: _Reply(status=302, headers={"Location": "http://evil.example/dist.json"})})

    with pytest.raises(DownloadError, match="https"):
        load_manifest(DistSource.url(), opener=_opener_for(transport))


def test_redirect_carrying_userinfo_is_refused():
    target = "https://user:pass@evil.example/dist.json"
    transport = _FakeTransport({_CANONICAL: _Reply(status=302, headers={"Location": target})})

    with pytest.raises(DownloadError, match="userinfo"):
        load_manifest(DistSource.url(), opener=_opener_for(transport))


def test_redirect_hops_are_capped():
    hops = {f"https://hop{index}.example/dist.json": index for index in range(MAX_REDIRECTS + 3)}
    routes: dict[str, _Reply | list[_Reply | Exception]] = {
        _CANONICAL: _Reply(status=302, headers={"Location": "https://hop0.example/dist.json"})
    }
    for url, index in hops.items():
        routes[url] = _Reply(status=302, headers={"Location": f"https://hop{index + 1}.example/dist.json"})
    transport = _FakeTransport(routes)

    with pytest.raises(DownloadError):
        load_manifest(DistSource.url(), opener=_opener_for(transport))

    assert len(transport.requests) <= MAX_REDIRECTS + 1


def test_default_opener_replaces_the_permissive_redirect_handler():
    handlers = _opener().handlers  # pyright: ignore[reportAttributeAccessIssue]

    assert any(isinstance(handler, _HttpsOnlyRedirectHandler) for handler in handlers)
    assert not any(type(handler) is urllib.request.HTTPRedirectHandler for handler in handlers)


# --- size caps ------------------------------------------------------------


def test_manifest_read_is_capped():
    transport = _FakeTransport({_CANONICAL: _Reply(b"x" * (MANIFEST_MAX_BYTES + 1))})

    with pytest.raises(DownloadError, match="exceeds"):
        load_manifest(DistSource.url(), opener=_opener_for(transport))


def test_artifact_read_is_capped(tmp_path, monkeypatch):
    monkeypatch.setattr("ocx_sdk._dist.ARTIFACT_MAX_BYTES", 1024)
    url = "https://cdn.example/ocx.tar.gz"
    transport = _FakeTransport({url: _Reply(b"x" * 1025)})
    target = tmp_path / "artifact"
    fd = target.open("wb")

    with pytest.raises(DownloadError, match="exceeds"), fd:
        fetch_artifact(url, fd.fileno(), opener=_opener_for(transport))


def test_artifact_cap_is_the_documented_size():
    assert ARTIFACT_MAX_BYTES == 256 << 20


# --- transport retry classification (S-004) -------------------------------


@pytest.mark.parametrize(
    "failure",
    [
        pytest.param(_Reply(status=500), id="server-error"),
        pytest.param(_Reply(status=503), id="unavailable"),
        pytest.param(_Reply(status=429), id="too-many-requests"),
        pytest.param(urllib.error.URLError(ConnectionResetError("reset")), id="connection-reset"),
        pytest.param(urllib.error.URLError(TimeoutError("timed out")), id="timeout"),
    ],
)
def test_transient_transport_failures_are_retried(failure: _Reply | Exception):
    body = _body()
    slept: list[float] = []
    transport = _FakeTransport({_CANONICAL: [failure, _Reply(body)]})
    policy = RetryPolicy(attempts=2, backoff=0.25, jitter=False, sleep=slept.append)

    manifest = load_manifest(DistSource.url(), retry=policy, opener=_opener_for(transport))

    assert manifest.latest == "0.5.8"
    assert slept == [0.25]


@pytest.mark.parametrize(
    "status", [pytest.param(401, id="401"), pytest.param(403, id="403"), pytest.param(404, id="404")]
)
def test_client_errors_are_never_retried(status: int):
    slept: list[float] = []
    transport = _FakeTransport({_CANONICAL: [_Reply(status=status), _Reply(_body())]})
    policy = RetryPolicy(attempts=3, backoff=0.25, jitter=False, sleep=slept.append)

    with pytest.raises(DownloadError, match=str(status)):
        load_manifest(DistSource.url(), retry=policy, opener=_opener_for(transport))

    assert len(transport.requests) == 1
    assert slept == []


def test_retry_after_header_sets_the_delay():
    body = _body()
    slept: list[float] = []
    transport = _FakeTransport({_CANONICAL: [_Reply(status=429, headers={"Retry-After": "7"}), _Reply(body)]})
    policy = RetryPolicy(attempts=2, backoff=0.25, jitter=False, sleep=slept.append)

    load_manifest(DistSource.url(), retry=policy, opener=_opener_for(transport))

    assert slept == [7.0]


def test_truncated_response_is_retried():
    body = _body()
    slept: list[float] = []
    truncated = http.client.IncompleteRead(b"half-")
    transport = _FakeTransport({_CANONICAL: [_Reply(b"half-", stall=truncated), _Reply(body)]})
    policy = RetryPolicy(attempts=2, backoff=0.25, jitter=False, sleep=slept.append)

    manifest = load_manifest(DistSource.url(), retry=policy, opener=_opener_for(transport))

    assert manifest.latest == "0.5.8"
    assert slept == [0.25]


def test_a_response_that_never_completes_becomes_a_download_error():
    slept: list[float] = []
    truncated = http.client.IncompleteRead(b"half-")
    transport = _FakeTransport({_CANONICAL: _Reply(b"half-", stall=truncated)})
    policy = RetryPolicy(attempts=2, backoff=0.25, jitter=False, sleep=slept.append)

    with pytest.raises(DownloadError, match="IncompleteRead"):
        load_manifest(DistSource.url(), retry=policy, opener=_opener_for(transport))

    assert len(transport.requests) == 2


def test_negative_retry_after_falls_back_to_backoff():
    body = _body()
    slept: list[float] = []
    transport = _FakeTransport({_CANONICAL: [_Reply(status=429, headers={"Retry-After": "-5"}), _Reply(body)]})
    policy = RetryPolicy(attempts=2, backoff=0.25, jitter=False, sleep=slept.append)

    load_manifest(DistSource.url(), retry=policy, opener=_opener_for(transport))

    assert slept == [0.25]


def test_unparsable_retry_after_falls_back_to_backoff():
    body = _body()
    slept: list[float] = []
    stamp = "Wed, 21 Oct 2026 07:28:00 GMT"
    transport = _FakeTransport({_CANONICAL: [_Reply(status=429, headers={"Retry-After": stamp}), _Reply(body)]})
    policy = RetryPolicy(attempts=2, backoff=0.25, jitter=False, sleep=slept.append)

    load_manifest(DistSource.url(), retry=policy, opener=_opener_for(transport))

    assert slept == [0.25]


def test_a_failure_without_a_policy_is_reported_once():
    transport = _FakeTransport({_CANONICAL: [_Reply(status=503), _Reply(_body())]})

    with pytest.raises(DownloadError, match="503"):
        load_manifest(DistSource.url(), opener=_opener_for(transport))

    assert len(transport.requests) == 1


def test_unreachable_host_raises_download_error():
    transport = _FakeTransport({})

    with pytest.raises(DownloadError, match=re.escape("dist.json")):
        load_manifest(DistSource.url(), opener=_opener_for(transport))


# --- artifact transport ---------------------------------------------------


def test_fetch_artifact_streams_into_the_descriptor(tmp_path):
    url = "https://cdn.example/ocx.tar.gz"
    payload = b"archive-bytes" * 1000
    transport = _FakeTransport({url: _Reply(payload)})
    target = tmp_path / "artifact"

    with target.open("wb") as handle:
        fetch_artifact(url, handle.fileno(), opener=_opener_for(transport))

    assert target.read_bytes() == payload


def test_fetch_artifact_discards_a_partial_body_before_retrying(tmp_path):
    url = "https://cdn.example/ocx.tar.gz"
    payload = b"the-whole-archive"
    slept: list[float] = []
    transport = _FakeTransport({url: [_Reply(b"half-", stall=ConnectionResetError("reset")), _Reply(payload)]})
    policy = RetryPolicy(attempts=2, backoff=0.1, jitter=False, sleep=slept.append)
    target = tmp_path / "artifact"

    with target.open("wb") as handle:
        fetch_artifact(url, handle.fileno(), retry=policy, opener=_opener_for(transport))

    assert target.read_bytes() == payload


def test_fetch_artifact_attaches_bound_credentials(tmp_path):
    url = "https://mirror.example/v0.5.8/ocx.tar.gz"
    transport = _FakeTransport({url: _Reply(b"bytes")})
    credentials = DistSource.url(_MIRROR, _digest(b""), BearerAuth("t")).credentials()

    with (tmp_path / "artifact").open("wb") as handle:
        fetch_artifact(url, handle.fileno(), credentials=credentials, opener=_opener_for(transport))

    assert _authorization(transport.requests[0]) == "Bearer t"


def test_fetch_artifact_stays_anonymous_off_origin(tmp_path):
    url = "https://cdn.example/v0.5.8/ocx.tar.gz"
    transport = _FakeTransport({url: _Reply(b"bytes")})
    credentials = DistSource.url(_MIRROR, _digest(b""), BearerAuth("t")).credentials()

    with (tmp_path / "artifact").open("wb") as handle:
        fetch_artifact(url, handle.fileno(), credentials=credentials, opener=_opener_for(transport))

    assert _authorization(transport.requests[0]) is None


def test_fetch_artifact_refuses_a_plaintext_url(tmp_path):
    transport = _FakeTransport({})

    with pytest.raises(DistManifestError, match="https"), (tmp_path / "artifact").open("wb") as handle:
        fetch_artifact("http://cdn.example/ocx.tar.gz", handle.fileno(), opener=_opener_for(transport))

    assert transport.requests == []


# --- local sources --------------------------------------------------------


def test_path_source_reads_the_file(tmp_path):
    manifest_file = tmp_path / "dist.json"
    body = _body()
    manifest_file.write_bytes(body)

    manifest = load_manifest(DistSource.path(manifest_file, _digest(body)))

    assert manifest.latest == "0.5.8"


def test_path_source_reports_a_missing_file(tmp_path):
    with pytest.raises(DownloadError, match=re.escape("dist.json")):
        load_manifest(DistSource.path(tmp_path / "dist.json"))


def test_path_source_is_capped(tmp_path):
    manifest_file = tmp_path / "dist.json"
    manifest_file.write_bytes(b"x" * (MANIFEST_MAX_BYTES + 1))

    with pytest.raises(DownloadError, match="exceeds"):
        load_manifest(DistSource.path(manifest_file))


def test_data_source_uses_the_bytes_it_was_given():
    manifest = load_manifest(DistSource.data(_body()))

    assert manifest.latest == "0.5.8"


def test_data_source_verifies_its_digest():
    with pytest.raises(ChecksumMismatchError, match="manifest"):
        load_manifest(DistSource.data(_body(), _digest(b"other")))


def test_local_sources_need_no_pin_under_a_mirror():
    manifest = load_manifest(DistSource.data(_body()), mirrored=True)

    assert manifest.latest == "0.5.8"


# --- manifest validation (CWE-22 corpus) ----------------------------------


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        pytest.param({"filename": "../evil.tar.gz"}, "filename", id="filename-traversal"),
        pytest.param({"filename": "sub/ocx.tar.gz"}, "filename", id="filename-separator"),
        pytest.param({"filename": "sub\\ocx.tar.gz"}, "filename", id="filename-backslash"),
        pytest.param({"filename": "ocx.tar.gz\r\nX: 1"}, "filename", id="filename-crlf"),
        pytest.param({"filename": ".."}, "filename", id="filename-dotdot"),
        pytest.param({"filename": ""}, "filename", id="filename-empty"),
        pytest.param({"filename": 42}, "filename", id="filename-not-a-string"),
        # A homoglyph: the first letter is CYRILLIC SMALL LETTER O, escaped so the source stays ASCII.
        pytest.param({"filename": "\u043ecx.tar.gz"}, "filename", id="filename-cyrillic-homoglyph"),
        pytest.param({"filename": "ocx\x00.tar.gz"}, "filename", id="filename-embedded-nul"),
        pytest.param({"tag": "../../etc"}, "tag", id="tag-traversal"),
        pytest.param({"target": "x86_64/../.."}, "target", id="target-traversal"),
        pytest.param({"version": "0.5.8; rm -rf /"}, "version", id="version-junk"),
        pytest.param({"version": "latest"}, "version", id="version-not-semver"),
        pytest.param({"sha256": "A" * 64}, "sha256", id="sha256-uppercase"),
        pytest.param({"sha256": "0" * 63}, "sha256", id="sha256-short"),
        pytest.param({"url": "http://cdn.example/ocx.tar.gz"}, "url", id="url-plaintext"),
        pytest.param({"url": "https://u:p@cdn.example/ocx.tar.gz"}, "url", id="url-userinfo"),
        pytest.param({"url": "file:///etc/passwd"}, "url", id="url-file-scheme"),
        pytest.param({"url": "https://cdn.example:notaport/ocx.tar.gz"}, "url", id="url-unparsable-port"),
        pytest.param({"url": 42}, "url", id="url-not-a-string"),
        pytest.param({"channel": "sta ble"}, "channel", id="channel-space"),
    ],
)
def test_manifest_field_validation(overrides: dict[str, object], reason: str):
    with pytest.raises(DistManifestError, match=reason):
        parse_manifest(_body(_envelope([_row(**overrides)])))


def test_manifest_field_validation_rejects_a_missing_field():
    row = _row()
    del row["sha256"]

    with pytest.raises(DistManifestError, match="sha256"):
        parse_manifest(_body(_envelope([row])))


@pytest.mark.parametrize(
    ("raw", "reason"),
    [
        pytest.param(b"not json at all", "JSON", id="not-json"),
        pytest.param(b"[]", "object", id="not-an-object"),
        pytest.param(b"\xff\xfe", "JSON", id="not-utf8"),
        pytest.param(json.dumps({"releases": [], "schema": 1}).encode(), "latest", id="latest-missing"),
        pytest.param(json.dumps({"latest": None, "schema": 1}).encode(), "releases", id="releases-missing"),
        pytest.param(
            json.dumps({"latest": None, "releases": {}, "schema": 1}).encode(), "releases", id="releases-not-a-list"
        ),
        pytest.param(
            json.dumps({"latest": None, "releases": ["x"], "schema": 1}).encode(), "release", id="row-not-an-object"
        ),
        pytest.param(json.dumps({"latest": None, "releases": []}).encode(), "schema", id="schema-missing"),
        pytest.param(
            json.dumps({"latest": None, "releases": [], "schema": "1"}).encode(), "schema", id="schema-not-an-int"
        ),
        pytest.param(json.dumps({"latest": None, "releases": [], "schema": True}).encode(), "schema", id="schema-bool"),
        pytest.param(
            json.dumps({"latest": None, "releases": [], "schema": 2}).encode(), "schema 2", id="schema-from-the-future"
        ),
        pytest.param(
            json.dumps({"latest": "0.5.8", "releases": [], "schema": 1}).encode(), "latest", id="latest-not-an-object"
        ),
        pytest.param(
            json.dumps({"latest": {"version": "x"}, "releases": [], "schema": 1}).encode(),
            "version",
            id="latest-bad-version",
        ),
    ],
)
def test_manifest_envelope_validation(raw: bytes, reason: str):
    with pytest.raises(DistManifestError, match=reason):
        parse_manifest(raw)


def test_manifest_parse_ignores_unknown_keys():
    envelope = _envelope([_row(size=1234, signature="future")], announcement="hello")

    manifest = parse_manifest(_body(envelope))

    assert manifest.releases[0].filename == f"ocx-{_TARGET}.tar.gz"


def test_manifest_parse_accepts_a_null_next_channel():
    manifest = parse_manifest(_body())

    assert manifest.latest_next is None
    assert manifest.schema == 1


def test_manifest_parse_reads_the_next_channel():
    envelope = _envelope(latest_next={"channel": "next", "version": "0.6.0"})

    manifest = parse_manifest(_body(envelope))

    assert manifest.latest_next == "0.6.0"


# --- release selection ----------------------------------------------------


def _manifest(**overrides: object) -> DistManifest:
    """Return a manifest with two versions across two targets."""
    rows = [
        _row(),
        _row(target=_OTHER_TARGET, filename=f"ocx-{_OTHER_TARGET}.tar.gz"),
        _row(version="0.6.0", channel="next", tag="v0.6.0"),
    ]
    return parse_manifest(_body(_envelope(rows, **overrides)))


def test_select_returns_the_stable_latest():
    release = _manifest().select(version=None, channel=Channel.STABLE, target=_TARGET)

    assert release.version == "0.5.8"
    assert release.target == _TARGET


def test_select_returns_the_next_latest():
    manifest = _manifest(latest_next={"channel": "next", "version": "0.6.0"})

    release = manifest.select(version=None, channel=Channel.NEXT, target=_TARGET)

    assert release.version == "0.6.0"


def test_select_pins_an_exact_version():
    release = _manifest().select(version="0.6.0", channel=Channel.STABLE, target=_TARGET)

    assert release.version == "0.6.0"


def test_select_reports_an_empty_channel():
    with pytest.raises(DistManifestError, match="next"):
        _manifest().select(version=None, channel=Channel.NEXT, target=_TARGET)


def test_select_reports_an_unknown_version():
    with pytest.raises(DistManifestError, match=re.escape("9.9.9")):
        _manifest().select(version="9.9.9", channel=Channel.STABLE, target=_TARGET)


def test_select_reports_an_unsupported_platform():
    with pytest.raises(UnsupportedPlatformError, match="riscv64"):
        _manifest().select(version=None, channel=Channel.STABLE, target="riscv64-unknown-linux-musl")


def test_release_is_frozen():
    release = _manifest().releases[0]

    with pytest.raises(AttributeError):
        release.sha256 = "0" * 64  # pyright: ignore[reportAttributeAccessIssue]

    assert isinstance(release, Release)
