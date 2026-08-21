# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The OCX Authors

"""Contract tests for `ocx_sdk._env` (C-009, S-006).

Named rows from the design's mechanism matrix live here:
`test_secrets_absent_from_repr_logs_errors` (S-006 — the secret-hygiene
scenario), `test_registry_slug_fixtures` (the §9 slug carve-out, pinned
against the WP00 corpus and failing closed on mismatch), and
`test_ocx_keys_rejected_in_project_env` (the reserved-namespace gate, here
at the SDK's own serializer rather than ocx's).

The rest specifies the three transformations `build_spawn_env` performs:
neutralize the ambient project/global/quiet triple, apply the config onto
the host snapshot, and let explicit credentials beat ambient ones per slug.
"""

from __future__ import annotations

import base64
import dataclasses
import inspect
import json
from collections.abc import Mapping
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

import ocx_sdk
from ocx_sdk._config import OcxConfig
from ocx_sdk._env import SpawnEnv, _registry_slug, build_spawn_env, serialize_env_value
from ocx_sdk._errors import OcxError
from ocx_sdk._types import (
    MANAGED_CONFIG_DISABLED,
    BasicAuth,
    BearerAuth,
    ConstVar,
    EnvValue,
    HostEnv,
    ListVar,
    PathVar,
    RetryPolicy,
)

_SLUG_CORPUS = json.loads((Path(__file__).parent.parent / "fixtures" / "slug_fixtures.json").read_text())

_HOME = Path("/opt/ocx-home")
_CONFIG_FILE = Path("/etc/ocx/config.toml")
_INDEX = Path("/var/ocx/index")
_DOCKER = Path("/run/docker")


def _spawn(config: OcxConfig | None = None, /, **ambient: str) -> Mapping[str, str]:
    """Build a spawn env from an explicit ambient snapshot and return its mapping."""
    return build_spawn_env(HostEnv(ambient), config or OcxConfig()).mapping


# ── the slug carve-out (§9) ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("registry", "expected"),
    [
        pytest.param(case["registry"], case["slug"], id=case["registry"] or "empty-string")
        for case in _SLUG_CORPUS["cases"]
    ],
)
def test_registry_slug_fixtures(registry: str, expected: str) -> None:
    assert _registry_slug(registry) == expected


def test_registry_slug_is_strict_not_the_relaxed_path_slug() -> None:
    # ocx has a second, permissive slug for on-disk paths that keeps `.`, `-`
    # and `_`. Porting that one instead would silently build the wrong
    # OCX_AUTH_* name, so the difference is pinned rather than assumed.
    assert _registry_slug("foo-bar.baz") == "foo_bar_baz"
    assert _registry_slug("GHCR.io") == "GHCR_io", "to_slug does not case-fold"


def test_empty_registry_is_refused_rather_than_authenticating_anonymously() -> None:
    config = OcxConfig(auth={"": BearerAuth("t0ken")})

    with pytest.raises(OcxError, match=r"canonicalizes to an empty slug|cannot name an environment variable"):
        build_spawn_env(HostEnv({}), config)


def test_registries_sharing_one_slug_are_refused() -> None:
    # Both slugify to `ghcr_io`, so they address one OCX_AUTH_ghcr_io_* triple
    # and the later entry would silently take over the earlier one's
    # credentials. Fail closed rather than pick a winner by dict order.
    config = OcxConfig(auth={"ghcr.io": BearerAuth("first"), "ghcr-io": BearerAuth("second")})

    with pytest.raises(OcxError, match=r"both canonicalize to the slug 'ghcr_io'"):
        build_spawn_env(HostEnv({}), config)


# ── neutralization (§6) ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "key",
    [
        pytest.param("OCX_PROJECT", id="project"),
        pytest.param("OCX_GLOBAL", id="global"),
        pytest.param("OCX_QUIET", id="quiet"),
    ],
)
def test_neutralized_keys_never_reach_the_child(key: str) -> None:
    mapping = _spawn(**{key: "1"})

    assert key not in mapping


def test_neutralization_leaves_unrelated_ambient_variables_alone() -> None:
    # Only the three neutralized keys are dropped; everything else the caller
    # chose to inherit survives verbatim.
    mapping = _spawn(PATH="/usr/bin", CI="true", OCX_PROJECT="/elsewhere")

    assert {"PATH": "/usr/bin", "CI": "true"}.items() <= mapping.items()
    assert "OCX_PROJECT" not in mapping


# ── config → wire variables (§7) ─────────────────────────────────────────────


@pytest.mark.parametrize(
    ("config", "expected"),
    [
        pytest.param(OcxConfig(home=_HOME), {"OCX_HOME": str(_HOME)}, id="home"),
        pytest.param(OcxConfig(offline=True), {"OCX_OFFLINE": "1"}, id="offline"),
        pytest.param(OcxConfig(frozen=True), {"OCX_FROZEN": "1"}, id="frozen"),
        pytest.param(OcxConfig(config=_CONFIG_FILE), {"OCX_CONFIG": str(_CONFIG_FILE)}, id="config"),
        pytest.param(OcxConfig(no_config=True), {"OCX_NO_CONFIG": "1"}, id="no-config"),
        pytest.param(
            OcxConfig(managed_config="ocx.sh/acme/cfg:1"),
            {"OCX_MANAGED_CONFIG": "ocx.sh/acme/cfg:1"},
            id="managed-config",
        ),
        pytest.param(OcxConfig(index=_INDEX), {"OCX_INDEX": str(_INDEX)}, id="index"),
        pytest.param(OcxConfig(jobs=4), {"OCX_JOBS": "4"}, id="jobs"),
        pytest.param(OcxConfig(docker_config=_DOCKER), {"DOCKER_CONFIG": str(_DOCKER)}, id="docker-config"),
        pytest.param(OcxConfig(no_config_refresh=True), {"OCX_NO_CONFIG_REFRESH": "1"}, id="no-config-refresh"),
        pytest.param(
            OcxConfig(insecure_registries=("localhost:5000", "127.0.0.1:5099")),
            {"OCX_INSECURE_REGISTRIES": "localhost:5000,127.0.0.1:5099"},
            id="insecure-registries",
        ),
    ],
)
def test_config_fields_reach_the_child_as_wire_variables(config: OcxConfig, expected: dict[str, str]) -> None:
    mapping = _spawn(config)

    assert expected.items() <= mapping.items()


def test_update_check_is_suppressed_by_default() -> None:
    # An SDK call is a program step, not an interactive session, so the
    # default config already carries the suppression.
    assert _spawn()["OCX_NO_UPDATE_CHECK"] == "1"


def test_update_check_opt_in_beats_an_ambient_suppression() -> None:
    # This flag defaults True, so opting back in has to be written rather than
    # merely skipped — otherwise an ambient =1 would survive and silently win.
    # ocx parses "0" as false through env::flag.
    assert _spawn(OcxConfig(no_update_check=False), OCX_NO_UPDATE_CHECK="1")["OCX_NO_UPDATE_CHECK"] == "0"


@pytest.mark.parametrize(
    ("config", "key"),
    [
        pytest.param(OcxConfig(offline=False), "OCX_OFFLINE", id="offline"),
        pytest.param(OcxConfig(frozen=False), "OCX_FROZEN", id="frozen"),
        pytest.param(OcxConfig(no_config=False), "OCX_NO_CONFIG", id="no-config"),
    ],
)
def test_a_false_flag_leaves_the_ambient_value_alone(config: OcxConfig, key: str) -> None:
    # A plain `bool` has no `None` tier, so `False` is "not requested" rather
    # than "force off" — the ambient value survives. The fields that CAN
    # neutralize an ambient value are typed `| None` and say so.
    assert _spawn(config, **{key: "1"})[key] == "1"


@pytest.mark.parametrize(
    ("config", "key"),
    [
        pytest.param(OcxConfig(), "OCX_HOME", id="home"),
        pytest.param(OcxConfig(), "OCX_CONFIG", id="config"),
        pytest.param(OcxConfig(), "OCX_INDEX", id="index"),
        pytest.param(OcxConfig(), "OCX_JOBS", id="jobs"),
        pytest.param(OcxConfig(), "OCX_MIRRORS", id="mirrors"),
        pytest.param(OcxConfig(), "OCX_MANAGED_CONFIG", id="managed-config"),
        pytest.param(OcxConfig(), "OCX_NO_CONFIG_REFRESH", id="no-config-refresh"),
        pytest.param(OcxConfig(), "DOCKER_CONFIG", id="docker-config"),
    ],
)
def test_an_unset_optional_field_inherits_the_ambient_value(config: OcxConfig, key: str) -> None:
    assert _spawn(config, **{key: "inherited"})[key] == "inherited"


def test_no_config_wins_over_managed_config() -> None:
    # ocx's own precedence, not an SDK invention: OCX_NO_CONFIG suppresses the
    # managed tier on the read side, so emitting a source alongside it would
    # describe a tier that is already switched off.
    mapping = _spawn(OcxConfig(no_config=True, managed_config="ocx.sh/acme/cfg:1"))

    assert mapping["OCX_NO_CONFIG"] == "1"
    assert "OCX_MANAGED_CONFIG" not in mapping


def test_managed_config_disabled_sentinel_travels_as_an_empty_string() -> None:
    # ocx reads OCX_MANAGED_CONFIG="" as "unset", which is how a caller
    # force-disables a managed tier a config file would otherwise turn on.
    mapping = _spawn(OcxConfig(managed_config=MANAGED_CONFIG_DISABLED), OCX_MANAGED_CONFIG="ocx.sh/acme/cfg:1")

    assert mapping["OCX_MANAGED_CONFIG"] == ""


def test_no_config_refresh_false_clears_an_ambient_suppression() -> None:
    # The `| None` tier is what makes False meaningful: None defers to the
    # ambient policy owner, False actively asks for the refresh back.
    mapping = _spawn(OcxConfig(no_config_refresh=False), OCX_NO_CONFIG_REFRESH="1")

    assert "OCX_NO_CONFIG_REFRESH" not in mapping


def test_mirrors_travel_as_one_json_object() -> None:
    mapping = _spawn(OcxConfig(mirrors={"ghcr.io": "mirror.corp/ghcr"}))

    assert json.loads(mapping["OCX_MIRRORS"]) == {"ghcr.io": "mirror.corp/ghcr"}


def test_insecure_registries_none_inherits_the_ambient_set() -> None:
    mapping = _spawn(OcxConfig(insecure_registries=None), OCX_INSECURE_REGISTRIES="legacy.corp")

    assert mapping["OCX_INSECURE_REGISTRIES"] == "legacy.corp"


@pytest.mark.parametrize(
    ("registries", "expected"),
    [
        pytest.param((), "", id="empty-blocks-ambient-reenable"),
        pytest.param(("only.corp",), "only.corp", id="explicit-replaces-ambient"),
    ],
)
def test_explicit_insecure_registries_replace_the_ambient_set(registries: tuple[str, ...], expected: str) -> None:
    # Fail-closed: `()` means "plaintext nowhere", so it must overwrite what a
    # CI image exported rather than falling back to inheriting it.
    mapping = _spawn(OcxConfig(insecure_registries=registries), OCX_INSECURE_REGISTRIES="legacy.corp")

    assert mapping["OCX_INSECURE_REGISTRIES"] == expected


def test_an_insecure_registry_entry_may_not_smuggle_a_comma() -> None:
    # ',' is what ocx splits OCX_INSECURE_REGISTRIES on, so one entry carrying
    # one would widen the plaintext allowlist to hosts nobody listed.
    config = OcxConfig(insecure_registries=("localhost:5000,evil.corp",))

    with pytest.raises(OcxError, match="contains ','"):
        build_spawn_env(HostEnv({}), config)


def test_sdk_side_policy_never_reaches_the_child_environment() -> None:
    # log_level rides on argv; retry and timeout are driven entirely inside
    # the SDK. None of the three has a wire variable to leak into.
    mapping = _spawn(OcxConfig(log_level="trace", retry=RetryPolicy(), timeout=30.0))

    assert not [key for key in mapping if "LOG" in key or "RETRY" in key or "TIMEOUT" in key]


# ── auth (§9) ────────────────────────────────────────────────────────────────


def test_ambient_auth_passes_through_untouched() -> None:
    # 12-factor: a caller who already exported credentials needs no config.
    mapping = _spawn(OCX_AUTH_ghcr_io_TYPE="token", OCX_AUTH_ghcr_io_TOKEN="ambient-token")

    assert mapping["OCX_AUTH_ghcr_io_TYPE"] == "token"
    assert mapping["OCX_AUTH_ghcr_io_TOKEN"] == "ambient-token"


def test_basic_auth_emits_the_full_triple_under_the_slugified_registry() -> None:
    mapping = _spawn(OcxConfig(auth={"ghcr.io": BasicAuth("ci", "hunter2")}))

    assert mapping["OCX_AUTH_ghcr_io_TYPE"] == "basic"
    assert mapping["OCX_AUTH_ghcr_io_USER"] == "ci"
    assert mapping["OCX_AUTH_ghcr_io_TOKEN"] == "hunter2"


def test_bearer_auth_emits_type_and_token_only() -> None:
    mapping = _spawn(OcxConfig(auth={"ghcr.io": BearerAuth("ghp_secret")}))

    assert mapping["OCX_AUTH_ghcr_io_TYPE"] == "token"
    assert mapping["OCX_AUTH_ghcr_io_TOKEN"] == "ghp_secret"
    assert "OCX_AUTH_ghcr_io_USER" not in mapping


def test_explicit_auth_wins_over_ambient_for_the_same_slug() -> None:
    mapping = _spawn(
        OcxConfig(auth={"ghcr.io": BearerAuth("explicit")}),
        OCX_AUTH_ghcr_io_TYPE="token",
        OCX_AUTH_ghcr_io_TOKEN="ambient",
    )

    assert mapping["OCX_AUTH_ghcr_io_TOKEN"] == "explicit"


def test_replacing_basic_with_bearer_removes_the_stale_user() -> None:
    # The whole triple is replaced rather than merged. A written _TYPE is
    # authoritative today — ocx's get_env_auth reads _USER only under
    # AuthType::Basic — so the stale _USER is inert, not a live confusion.
    # Popping it is defense in depth against that precedence changing.
    mapping = _spawn(
        OcxConfig(auth={"ghcr.io": BearerAuth("explicit")}),
        OCX_AUTH_ghcr_io_TYPE="basic",
        OCX_AUTH_ghcr_io_USER="stale-user",
        OCX_AUTH_ghcr_io_TOKEN="stale-password",
    )

    assert "OCX_AUTH_ghcr_io_USER" not in mapping
    assert mapping["OCX_AUTH_ghcr_io_TYPE"] == "token"


def test_a_case_variant_ambient_triple_loses_to_the_configured_one() -> None:
    # Registry hosts are case-insensitive, so an ambient OCX_AUTH_GHCR_IO_* is
    # a credential for the same registry a configured `ghcr.io` covers.
    # Leaving it in place would ship two credential sets for one registry and
    # hand the outcome to whichever spelling ocx slugs. Fail closed: an
    # explicit config clears the ambient triple whatever its case.
    mapping = _spawn(
        OcxConfig(auth={"ghcr.io": BearerAuth("explicit")}),
        OCX_AUTH_GHCR_IO_TYPE="token",
        OCX_AUTH_GHCR_IO_TOKEN="ambient",
    )

    assert mapping["OCX_AUTH_ghcr_io_TOKEN"] == "explicit"
    assert [key for key in mapping if key.startswith("OCX_AUTH_")] == [
        "OCX_AUTH_ghcr_io_TYPE",
        "OCX_AUTH_ghcr_io_TOKEN",
    ]


def test_a_case_variant_ambient_triple_for_another_registry_survives() -> None:
    # The sweep is scoped to the slugs the config claims: an unrelated
    # registry's ambient credentials are the caller's business, whatever case
    # they were exported in.
    mapping = _spawn(
        OcxConfig(auth={"ghcr.io": BearerAuth("explicit")}),
        OCX_AUTH_DOCKER_IO_TOKEN="untouched",
    )

    assert mapping["OCX_AUTH_DOCKER_IO_TOKEN"] == "untouched"


def test_auth_for_one_registry_leaves_another_registrys_ambient_credentials() -> None:
    mapping = _spawn(
        OcxConfig(auth={"ghcr.io": BearerAuth("explicit")}),
        OCX_AUTH_docker_io_TYPE="token",
        OCX_AUTH_docker_io_TOKEN="untouched",
    )

    assert mapping["OCX_AUTH_docker_io_TOKEN"] == "untouched"


# ── secret hygiene (S-006, §12) ──────────────────────────────────────────────


def _env_carrying_exports() -> list[type]:
    """Return exported types built from one environment-shaped mapping.

    Found by introspecting `__all__` rather than listed by hand: any future
    export that carries an environment joins the repr sweep below on the day
    it is added, instead of the day somebody remembers to extend a list.
    """
    carriers: list[type] = []
    for name in ocx_sdk.__all__:
        candidate = getattr(ocx_sdk, name)
        if not (inspect.isclass(candidate) and dataclasses.is_dataclass(candidate)):
            continue
        required = [
            entry
            for entry in dataclasses.fields(candidate)
            if entry.default is dataclasses.MISSING and entry.default_factory is dataclasses.MISSING
        ]
        if len(required) == 1 and str(required[0].type) == "Mapping[str, str]":
            carriers.append(candidate)
    return carriers


_ENV_CARRIERS = _env_carrying_exports()


def test_the_repr_sweep_finds_every_env_carrying_export() -> None:
    # A sweep that silently stops matching becomes zero tests that pass, so
    # what it finds is pinned against what the surface actually exports.
    assert {carrier.__name__ for carrier in _ENV_CARRIERS} == {"HostEnv", "ComposedEnv"}


@pytest.mark.parametrize("carrier", _ENV_CARRIERS, ids=lambda carrier: carrier.__name__)
def test_no_env_carrying_export_reprs_a_value(carrier: type) -> None:
    # An environment holds OCX_AUTH_* whether the SDK put it there or the host
    # did, and a repr is what a traceback and a pytest diff print (CWE-532).
    secret = "sekret-token-123"

    assert secret not in repr(carrier({"OCX_AUTH_ghcr_io_TOKEN": secret}))


def test_secrets_absent_from_repr_logs_errors() -> None:
    token = "sekret-token-123"
    password = "hunter2-password"
    config = OcxConfig(auth={"ghcr.io": BearerAuth(token), "docker.io": BasicAuth("ci", password)})

    spawn_env = build_spawn_env(HostEnv({"PATH": "/usr/bin"}), config)

    # The mapping MUST carry the secrets — the child cannot authenticate
    # without them. Secrecy is about the human-readable surfaces.
    assert spawn_env.mapping["OCX_AUTH_ghcr_io_TOKEN"] == token
    assert spawn_env.mapping["OCX_AUTH_docker_io_TOKEN"] == password

    for surface in (repr(config), str(config), repr(spawn_env), str(spawn_env)):
        assert token not in surface
        assert password not in surface

    log_line = f"pulling with token {token} and password {password}"
    error = OcxError(f"login failed for {token}")
    assert token not in spawn_env.redact(log_line)
    assert password not in spawn_env.redact(log_line)
    assert token not in spawn_env.redact(str(error))

    # A username is not a secret; redacting it would mangle logs for nothing.
    assert "ci" in spawn_env.redact("user ci logged in")


def test_spawn_env_repr_names_its_keys_without_showing_a_value() -> None:
    spawn_env = build_spawn_env(HostEnv({"PATH": "/usr/bin"}), OcxConfig(auth={"ghcr.io": BearerAuth("s3cret")}))

    assert "OCX_AUTH_ghcr_io_TOKEN" in repr(spawn_env)
    assert "s3cret" not in repr(spawn_env)
    assert "/usr/bin" not in repr(spawn_env)


def test_redact_masks_overlapping_secrets_longest_first() -> None:
    # Shortest-first would leave "***-token" and expose the tail of the longer
    # secret, so the redactor orders its replacements by length.
    config = OcxConfig(auth={"a.io": BearerAuth("secret-token"), "b.io": BearerAuth("secret")})

    redact = build_spawn_env(HostEnv({}), config).redact

    assert redact("secret-token") == "***"
    assert redact("secret") == "***"


def test_redact_is_the_identity_without_credentials() -> None:
    redact = build_spawn_env(HostEnv({}), OcxConfig()).redact

    assert redact("nothing to hide") == "nothing to hide"


def test_redact_ignores_an_empty_credential() -> None:
    # An empty secret would otherwise match at every position and turn a log
    # line into a wall of asterisks.
    redact = build_spawn_env(HostEnv({}), OcxConfig(auth={"a.io": BearerAuth("")})).redact

    assert redact("nothing to hide") == "nothing to hide"


def test_redact_scrubs_a_credential_the_host_exported() -> None:
    # The SDK did not choose an ambient OCX_AUTH_*, but it still reaches the
    # child and can surface in ocx's own trace output, so the redactor is
    # seeded from the composed mapping rather than from the config alone.
    host = HostEnv({"OCX_AUTH_ghcr_io_TYPE": "token", "OCX_AUTH_ghcr_io_TOKEN": "ambient-token"})

    redact = build_spawn_env(host, OcxConfig()).redact

    assert redact("pulling with ambient-token") == "pulling with ***"


def test_redact_scrubs_the_basic_authorization_header_form() -> None:
    # `Authorization: Basic <base64>` is how a basic credential appears on the
    # wire, so a plaintext-only scrub would leave it readable in a trace log.
    encoded = base64.b64encode(b"ci:hunter2").decode()

    redact = build_spawn_env(HostEnv({}), OcxConfig(auth={"ghcr.io": BasicAuth("ci", "hunter2")})).redact

    assert redact(f"Authorization: Basic {encoded}") == "Authorization: Basic ***"


def test_spawn_env_is_frozen_and_slotted() -> None:
    spawn_env = build_spawn_env(HostEnv({}), OcxConfig())

    assert not hasattr(spawn_env, "__dict__")
    with pytest.raises(FrozenInstanceError, match="cannot assign to field"):
        spawn_env.mapping = {}  # type: ignore[misc]


def test_spawn_env_mapping_refuses_mutation() -> None:
    # Read-only like HostEnv.source: a consumer cannot rewrite a composed env
    # behind the choke point's back.
    spawn_env = build_spawn_env(HostEnv({"PATH": "/usr/bin"}), OcxConfig())

    with pytest.raises(TypeError, match="does not support item assignment"):
        spawn_env.mapping["PATH"] = "/evil"  # type: ignore[index]


def test_spawn_env_carries_the_pinned_seam() -> None:
    # _process takes exactly these two things and composes nothing itself.
    assert set(SpawnEnv.__dataclass_fields__) == {"mapping", "redact"}


# ── the `--env` serializer (§8) ──────────────────────────────────────────────


@pytest.mark.parametrize(
    ("key", "value", "expected"),
    [
        pytest.param("FOO", "bar", "FOO=bar", id="bare-str-is-constant"),
        pytest.param("FOO", ConstVar("bar"), "FOO=bar", id="constvar-is-unqualified"),
        pytest.param("FOO", ConstVar("a=b"), "FOO=a=b", id="value-may-contain-equals"),
        pytest.param("PATH", PathVar("/opt/bin"), "PATH:path=/opt/bin", id="pathvar"),
        pytest.param("OPTS", ListVar("-Xmx2g"), "OPTS:list=-Xmx2g", id="listvar-without-separator"),
        pytest.param(
            "GODEBUG", ListVar("gctrace=1", separator=","), "GODEBUG:list:,=gctrace=1", id="listvar-with-separator"
        ),
        pytest.param("OPTS", ListVar("x", separator=":"), "OPTS:list::=x", id="colon-is-a-legal-separator"),
        pytest.param("OPTS", ListVar("x", separator="; "), "OPTS:list:; =x", id="multi-character-separator"),
    ],
)
def test_serialize_env_value_renders_the_wire_grammar(key: str, value: EnvValue, expected: str) -> None:
    assert serialize_env_value(key, value) == expected


@pytest.mark.parametrize(
    "key",
    [
        pytest.param("OCX_DEFAULT_REGISTRY", id="ocx-prefix"),
        pytest.param("__OCX_TESTING_X", id="dunder-ocx-prefix"),
        pytest.param("ocx_offline", id="lowercase-still-reserved"),
    ],
)
def test_ocx_keys_rejected_in_project_env(key: str) -> None:
    # ocx rejects these at its own layer (exit 64); the SDK refuses earlier so
    # the caller gets a message naming the rule instead of a usage failure.
    with pytest.raises(OcxError, match="reserved"):
        serialize_env_value(key, "value")


@pytest.mark.parametrize(
    "key",
    [
        pytest.param("A:B", id="colon-would-read-as-a-type-marker"),
        pytest.param("A=B", id="equals-would-end-the-key"),
        pytest.param("A B", id="space"),
        pytest.param("1A", id="leading-digit"),
        pytest.param("", id="empty"),
    ],
)
def test_serialize_rejects_a_key_outside_the_posix_grammar(key: str) -> None:
    with pytest.raises(OcxError, match="not a valid environment variable name"):
        serialize_env_value(key, "value")


@pytest.mark.parametrize(
    "separator",
    [
        pytest.param("", id="empty"),
        pytest.param("=", id="equals-would-split-the-argument-early"),
        pytest.param("\n", id="newline"),
        pytest.param("\r", id="carriage-return"),
    ],
)
def test_serialize_rejects_a_separator_ocx_cannot_use(separator: str) -> None:
    with pytest.raises(OcxError, match="separator"):
        serialize_env_value("OPTS", ListVar("x", separator=separator))


@pytest.mark.parametrize(
    "value",
    [
        pytest.param(",gctrace=1", id="leading"),
        pytest.param("gctrace=1,", id="trailing"),
    ],
)
def test_serialize_rejects_a_separator_edged_value(value: str) -> None:
    # The separator the fold adds would fuse with the value's own flank, so
    # every ocx surface refuses it — this one before ocx has to.
    with pytest.raises(OcxError, match="starts or ends with its own separator"):
        serialize_env_value("GODEBUG", ListVar(value, separator=","))
