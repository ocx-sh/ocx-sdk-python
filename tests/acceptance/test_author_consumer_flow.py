# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The OCX Authors

"""S-005 and S-003: author a package, publish it, consume it back, describe it.

The journey the SDK exists to make scriptable — build a bundle, prove it runs
before it is published, push it, install it out of the registry as a consumer
who has never seen the build tree, and execute what came back. Run twice: once
against the plaintext registry, which only answers because the caller opted in
through `insecure_registries`, and once against the TLS registry, which only
answers because the caller supplied credentials.

The two postures are the point. A single anonymous registry would let a
credential bug and a transport bug both pass unnoticed.

0.2.0 adds S-003's catalog-description round trip to the same pair. It lives
here rather than in the contract tier for the same reason `push` does: it
writes to a registry.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from ocx_sdk import AuthError, BasicAuth, NotFoundError, PackageDescription

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

_README = "# hello\n\nWhat the acceptance tier publishes as a catalog page.\n"
"""The README `description push` uploads and `description pull --save-readme` writes back."""

_TITLE = "hello"
_DESCRIPTION = "The one-binary package the acceptance tier builds."
_KEYWORDS = "acceptance,hello,ocx-sdk"
"""One delimited string, not a list — ocx's own field is a bare `str` (C-021)."""

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
    """Create, test, push, install and execute — the whole v0.1 S-005 journey, per registry."""
    identifier = f"{registry}/wp13/hello:1.0.0"
    bundle = author(identifier)

    tested = ocx.package.test(
        "wp13/hello:1.0.0-test",
        script=_script(tmp_path, _PASSING_SCRIPT),
        layers=[bundle],
        platform=platform,
    )
    pushed = ocx.package.push(bundle)
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
    observable through the typed API (v0.1 S-005).
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
    assert passed.run is not None
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


def test_description_round_trips_through_a_registry(ocx: Ocx, registry: str, tmp_path: Path) -> None:
    """S-003: what `description_push` publishes is what `description_pull` reads back.

    The whole point of `InfoResult` becoming `Mapping[str, PackageDescription | None]`
    (D4) is that the two arms are distinguishable, so both are asserted in one
    run: a described repository comes back as a populated struct, an
    undescribed one as `None` — not as an empty struct, and not as a missing
    key.

    `keywords` is asserted as the delimited string it was pushed as. Upstream
    types it `Option<String>` and the SDK carries it verbatim; a parser that
    "helpfully" split it on commas would pass a shape test and quietly change
    the value.
    """
    described = f"{registry}/wp7/described:1.0.0"
    undescribed = f"{registry}/wp7/undescribed:1.0.0"
    readme = tmp_path / "README.md"
    readme.write_text(_README, encoding="utf-8")

    pushed = ocx.package.description_push(
        described,
        readme=readme,
        title=_TITLE,
        description=_DESCRIPTION,
        keywords=_KEYWORDS,
    )
    pulled = ocx.package.description_pull(described, undescribed)
    saved = tmp_path / "pulled"
    saved.mkdir()
    ocx.package.description_pull(described, save_readme=saved)

    assert pushed.exit_code == 0
    assert pulled[described] == PackageDescription(title=_TITLE, description=_DESCRIPTION, keywords=_KEYWORDS)
    assert pulled[undescribed] is None
    assert next(saved.iterdir()).read_text(encoding="utf-8") == _README


def test_description_push_refuses_from_alongside_the_field_arguments(ocx: Ocx, registry: str) -> None:
    """S-003's first error case: `--from` copies, the fields author, and ocx refuses both.

    Raised before the spawn, so the registry is never asked to arbitrate — the
    argument that decides is named in the message.
    """
    with pytest.raises(ValueError, match="title"):
        ocx.package.description_push(f"{registry}/wp7/described:1.0.0", from_=f"{registry}/wp7/source:1", title="t")


def test_description_push_from_an_undescribed_source_is_not_found(ocx: Ocx, registry: str) -> None:
    """S-003's second error case: `--from` a repository carrying no description is exit 79.

    Distinct from a missing *package*: the source repository need not exist at
    all, and what ocx reports missing is the `__ocx.desc` tag rather than the
    package the identifier names.
    """
    with pytest.raises(NotFoundError, match="exited 79"):
        ocx.package.description_push(f"{registry}/wp7/promoted:1.0.0", from_=f"{registry}/wp7/never-described:1.0.0")


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
