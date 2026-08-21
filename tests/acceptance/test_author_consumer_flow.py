# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The OCX Authors

"""S-005: author a package, publish it, consume it back.

The journey the SDK exists to make scriptable — build a bundle, prove it runs
before it is published, push it, install it out of the registry as a consumer
who has never seen the build tree, and execute what came back. Run twice: once
against the plaintext registry, which only answers because the caller opted in
through `insecure_registries`, and once against the TLS registry, which only
answers because the caller supplied credentials.

The two postures are the point. A single anonymous registry would let a
credential bug and a transport bug both pass unnoticed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from ocx_sdk import AuthError, BasicAuth

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from ocx_sdk import Ocx

_GREETING = "hello from the ocx-sdk acceptance tier"
"""What the packaged script prints — the marker that proves the round trip."""

_METADATA = """{
  "$schema": "https://ocx.sh/schemas/metadata/v1.json",
  "type": "bundle",
  "version": 1,
  "env": [
    {"key": "PATH", "type": "path", "required": true, "value": "${installPath}/bin", "visibility": "public"}
  ],
  "binaries": ["hello"]
}
"""
"""The smallest metadata that puts one binary on a consumer's PATH."""

_PASSING_SCRIPT = f"""
r = ocx.run("hello")
expect.ok(r)
expect.contains(r.stdout, "{_GREETING}")
"""

_FAILING_SCRIPT = """
r = ocx.run("hello")
expect.contains(r.stdout, "text the package never prints")
"""


@pytest.fixture
def platform(ocx: Ocx) -> str:
    """The platform this ocx builds for.

    Asked of the binary rather than hardcoded: `linux/amd64` would turn every
    arm64 developer machine and runner into a red suite.
    """
    return ocx.about().platforms[0]


@pytest.fixture
def author(ocx: Ocx, platform: str, tmp_path: Path) -> Callable[[str], Path]:
    """Return a factory that bundles the fixture package under one identifier.

    The identifier is baked into the build receipt `create` writes beside the
    bundle, and `push` reads it back from there — which is why the registry
    has to be decided at build time, not at push time.
    """

    def build(identifier: str) -> Path:
        binaries = tmp_path / "package" / "bin"
        binaries.mkdir(parents=True, exist_ok=True)
        script = binaries / "hello"
        script.write_text(f"#!/bin/sh\necho '{_GREETING}'\n", encoding="utf-8")
        script.chmod(0o755)

        metadata = tmp_path / "metadata.json"
        metadata.write_text(_METADATA, encoding="utf-8")

        bundle = tmp_path / "hello-1.0.0.tar.xz"
        ocx.package.create(
            binaries.parent,
            identifier=identifier,
            platform=platform,
            metadata=metadata,
            output=bundle,
            force=True,
        )
        return bundle

    return build


def _script(tmp_path: Path, source: str) -> Path:
    """Write a Starlark test script and return its path."""
    path = tmp_path / "smoke.star"
    path.write_text(source, encoding="utf-8")
    return path


def test_author_flow_round_trips_through_a_registry(
    ocx: Ocx,
    author: Callable[[str], Path],
    registry: str,
    platform: str,
    tmp_path: Path,
) -> None:
    """Create, test, push, install and execute — the whole S-005 journey, per registry."""
    identifier = f"{registry}/wp13/hello:1.0.0"
    bundle = author(identifier)

    tested = ocx.package.test(
        "wp13/hello:1.0.0-test",
        script=_script(tmp_path, _PASSING_SCRIPT),
        layers=[bundle],
        platform=platform,
    )
    pushed = ocx.package.push(bundle, new=True)
    installed = ocx.package.install(identifier)
    executed = ocx.package.exec([identifier], ["hello"])

    assert tested.status == "passed"
    assert pushed.status == "pushed"
    assert pushed.identifier == identifier
    assert installed.packages[identifier].identifier.startswith(f"{identifier}@sha256:")
    assert executed.stdout.strip() == _GREETING


def test_package_test_envelope(
    ocx: Ocx,
    author: Callable[[str], Path],
    platform: str,
    tmp_path: Path,
) -> None:
    """The `--script` form's v1 envelope, both ways round (design §3 anchor).

    A passing script returns the parsed envelope with no assertion attached.
    A failing one is a RESULT too: ocx exits 1 with the envelope on stdout,
    and the SDK tolerates that exit so `status`/`assertion.kind` stay
    observable through the typed API (S-005).
    """
    bundle = author("wp13/hello:1.0.0")

    passed = ocx.package.test(
        "wp13/hello:1.0.0-test",
        script=_script(tmp_path, _PASSING_SCRIPT),
        layers=[bundle],
        platform=platform,
    )

    assert passed.status == "passed"
    assert passed.assertion is None
    assert passed.run.exit_code == 0
    assert _GREETING in passed.run.stdout

    failed = ocx.package.test(
        "wp13/hello:1.0.0-test",
        script=_script(tmp_path, _FAILING_SCRIPT),
        layers=[bundle],
        platform=platform,
    )

    assert failed.status == "failed"
    assert failed.assertion is not None and failed.assertion.kind


def test_authed_registry_refuses_wrong_credentials(ocx: Ocx, authed_registry: str, credentials: BasicAuth) -> None:
    """A bad password is exit 80 — `AuthError`, and never retried.

    The identifier names nothing that was ever pushed, deliberately: the
    registry refuses before it will say whether the repository exists, so the
    row proves the credential check rather than a lookup, and stays independent
    of what any other test published.
    """
    wrong = ocx.with_config(auth={authed_registry: BasicAuth(credentials.user, "not-the-password")})

    with pytest.raises(AuthError, match="exited 80"):
        wrong.package.install(f"{authed_registry}/wp13/never-published:9.9.9")


def test_login_password_stdin(ocx: Ocx, authed_registry: str, credentials: BasicAuth, tmp_path: Path) -> None:
    """`login --password-stdin` against the htpasswd registry (design §3 anchor).

    Verification is real: `login` checks the credentials against the registry
    before storing them, so this only passes over working TLS.

    Its own `DOCKER_CONFIG`, not the session's: `login` persists credentials,
    and a stored entry would leak into every later test's spawn as an
    alternative to the `OCX_AUTH_*` path they are meant to exercise.
    `--allow-insecure-store` is what makes the plaintext fallback legal, and it
    is legal here because the store is a directory pytest deletes.
    """
    store = tmp_path / "docker-config"
    store.mkdir()
    isolated = ocx.with_config(docker_config=store)

    logged_in = isolated.login(
        authed_registry, username=credentials.user, token=credentials.password, allow_insecure_store=True
    )
    logged_out = isolated.logout(authed_registry)

    assert logged_in.registry == authed_registry
    assert logged_in.username == credentials.user
    assert logged_out.registry == authed_registry
    assert (store / "config.json").exists()
