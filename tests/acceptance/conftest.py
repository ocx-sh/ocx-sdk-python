# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The OCX Authors

"""Acceptance tier: the real binary against real registries (design §14).

Where the contract tier pins the SDK's model of ocx, this tier pins the
journeys a user actually takes — authoring a package and consuming it back
(S-005), and composing a project environment into a child process (S-002).
Nothing is stubbed: two `registry:2` containers, a real push over the wire, a
real install out of it.

The pair exists because the SDK has two registry postures to prove:

- **plain** (`127.0.0.1:5010`) is plaintext HTTP, which ocx refuses unless the
  caller opts in through `insecure_registries` — so it exercises the opt-in.
- **authed** (`127.0.0.1:5011`) is htpasswd basic auth behind self-signed TLS.
  registry:2 refuses htpasswd auth over plaintext, and ocx's insecure-registry
  list covers transport only — it never disables certificate verification — so
  the credential path forces real TLS.

Custom-CA trust is `SSL_CERT_FILE`: ocx bundles the Mozilla roots and *merges*
the host trust store on top, so naming the fixture CA there is enough to make
the leaf verify. It has to travel in the `HostEnv`, because that snapshot is
the only thing a spawned ocx inherits.

Everything is gated on `OCX_SDK_ACCEPTANCE=1` (`task test:acceptance`) and
skips — never fails — without it, so `task test` stays a pure unit run.

Hermetic by construction, like the contract tier: throwaway `$OCX_HOME`,
throwaway `DOCKER_CONFIG` (the ambient one carries credential helpers), and an
explicit `HostEnv` rather than the ambient environment.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Final

import pytest

from ocx_sdk import BasicAuth, HostEnv, Ocx, OcxConfig

if TYPE_CHECKING:
    from collections.abc import Iterator

ACCEPTANCE_GATE = "OCX_SDK_ACCEPTANCE"
"""Environment flag that arms this tier; `task test:acceptance` sets it."""

PLAIN_REGISTRY: Final = "127.0.0.1:5010"
"""The plaintext registry, reachable only through the `insecure_registries` opt-in."""

AUTHED_REGISTRY: Final = "127.0.0.1:5011"
"""The htpasswd registry behind self-signed TLS."""

USERNAME: Final = "testuser"
"""The account in `docker/auth/htpasswd`."""

PASSWORD: Final = "acceptance-only-not-a-secret"
"""Its password. Named for what it is: a loopback-only fixture credential.

The plaintext has to exist somewhere for the tests to authenticate at all, so
the checked-in bcrypt hash beside it discloses nothing this line does not.
"""

_HERE = Path(__file__).parent
_DOCKER = _HERE.parents[1] / "docker"
_COMPOSE = _DOCKER / "compose.yml"
_GENERATE = _DOCKER / "certs" / "generate.sh"
_CA_CERT = _DOCKER / "certs" / "ca.crt"

_SKIP = pytest.mark.skip(reason=f"acceptance tier: set {ACCEPTANCE_GATE}=1 (`task test:acceptance`)")
_STACK_TIMEOUT = 300.0
"""Seconds for a compose command — an image pull on a cold runner is the slow case."""


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Skip this directory's tests unless the acceptance gate is armed.

    Scoped by path rather than applied to everything pytest collected: the
    hook is session-wide, and a unit run must keep its own items untouched.
    """
    if os.environ.get(ACCEPTANCE_GATE) == "1":
        return
    for item in items:
        if _HERE in Path(item.path).parents:
            item.add_marker(_SKIP)


def _docker_unusable() -> str | None:
    """Return why docker compose cannot run here, or `None` when it can.

    A missing docker is a reason to skip, not to fail: the tier is armed by an
    environment flag a developer may set on a machine that has no daemon.
    """
    try:
        probe = subprocess.run(
            ("docker", "compose", "version"),
            capture_output=True,
            text=True,
            timeout=_STACK_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return f"acceptance tier needs docker compose: {exc}"
    if probe.returncode != 0:
        return f"acceptance tier needs docker compose: {probe.stderr.strip() or probe.stdout.strip()}"
    return None


@pytest.fixture(scope="session")
def registry_stack() -> Iterator[None]:
    """Bring the registry pair up for the session and tear it down after.

    Orchestration lives here rather than in the taskfile so that
    `task test:acceptance` stays one command, and so a developer running
    pytest directly gets the same stack the CI job gets.

    Output is left uncaptured: when `--wait` gives up, compose's own report of
    which container went unhealthy is the only useful diagnosis.
    """
    reason = _docker_unusable()
    if reason:
        pytest.skip(reason)

    # Through `sh` rather than by its shebang: a checkout that lost the exec
    # bit would otherwise fail here instead of generating the certificates.
    subprocess.run(("sh", str(_GENERATE)), check=True, timeout=_STACK_TIMEOUT)
    compose = ("docker", "compose", "-f", str(_COMPOSE))
    try:
        # Inside the `try`: `--wait` failing on one unhealthy container leaves
        # the other one running, and a leaked registry is a port conflict for
        # the next run.
        subprocess.run((*compose, "up", "-d", "--wait"), check=True, timeout=_STACK_TIMEOUT)
        yield
    finally:
        # `check=False`: a teardown that raises would replace a real test
        # failure with a compose error in the report.
        subprocess.run((*compose, "down", "-v"), check=False, timeout=_STACK_TIMEOUT)


@pytest.fixture(scope="session")
def ocx_home(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A throwaway `$OCX_HOME` shared by the whole acceptance session.

    Session-scoped on purpose: the store is content-addressed, so nothing here
    touches the developer's real `~/.ocx` and one install serves every reader.
    """
    return tmp_path_factory.mktemp("ocx-home")


@pytest.fixture(scope="session")
def docker_config(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A throwaway `DOCKER_CONFIG`, so no test can read the developer's credentials."""
    return tmp_path_factory.mktemp("docker-config")


@pytest.fixture
def host_env() -> HostEnv:
    """The parent environment an acceptance spawn inherits.

    `minimal()` plus the one addition this tier needs: `SSL_CERT_FILE` naming
    the fixture CA, which is how the authed registry's self-signed leaf
    verifies. ocx merges it with its bundled roots rather than replacing them.
    """
    return HostEnv({**HostEnv.minimal().source, "SSL_CERT_FILE": str(_CA_CERT)})


@pytest.fixture
def config(ocx_home: Path, docker_config: Path) -> OcxConfig:
    """Session policy: throwaway state, plaintext for exactly one registry.

    The two registries are named in different fields on purpose. Credentials
    go to the TLS registry; the plaintext opt-in goes to the anonymous one.
    Naming one registry in both would be the CWE-319 pairing `OcxConfig`
    warns about — credentials travelling in the clear.
    """
    return OcxConfig(
        home=ocx_home,
        docker_config=docker_config,
        insecure_registries=(PLAIN_REGISTRY,),
        auth={AUTHED_REGISTRY: BasicAuth(USERNAME, PASSWORD)},
    )


@pytest.fixture
def ocx(ocx_exe: Path, host_env: HostEnv, config: OcxConfig, registry_stack: None) -> Ocx:
    """A handle on the pinned binary, pointed at throwaway state and a live stack."""
    return Ocx(exe=ocx_exe, host_env=host_env, config=config)


@pytest.fixture(params=[PLAIN_REGISTRY, AUTHED_REGISTRY], ids=["plain", "authed"])
def registry(request: pytest.FixtureRequest) -> str:
    """Each registry in turn, so one flow is asserted against both postures."""
    return str(request.param)


@pytest.fixture(scope="session")
def authed_registry() -> str:
    """The TLS + htpasswd registry, for the rows that are about credentials."""
    return AUTHED_REGISTRY


@pytest.fixture(scope="session")
def credentials() -> BasicAuth:
    """The account the authed registry accepts."""
    return BasicAuth(USERNAME, PASSWORD)
