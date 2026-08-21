# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The OCX Authors

"""Contract tests for `ocx_sdk._types` (C-002, S-006).

Named rows from the design's mechanism matrix live here:
`test_packageref_byte_roundtrip` (§14), and the repr half of S-006 — a
credential must not be reconstructable from a log line, a traceback, or a
pytest diff. The spawn-env half of S-006 belongs to `_env`.

Also pins the frozen+slots invariant across the whole vocabulary, the
`HostEnv` recipes of §6, and the `RetryPolicy` defaults of §10.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError, fields, is_dataclass
from typing import get_args

import pytest

from ocx_sdk import _types
from ocx_sdk._errors import ExitCode
from ocx_sdk._types import (
    MANAGED_CONFIG_DISABLED,
    MIN_SUPPORTED,
    TESTED_OCX_VERSION,
    Auth,
    BasicAuth,
    BearerAuth,
    Channel,
    ConstVar,
    EnvValue,
    HostEnv,
    InstallEnv,
    ListVar,
    LogLevel,
    PackageLike,
    PackageRef,
    PathVar,
    RetryPolicy,
)

_PLATFORM_KEYS = ("PATH", "HOME", "TMPDIR", "SYSTEMROOT", "TEMP")


def _dataclasses() -> list[type]:
    """Return every dataclass the vocabulary exports."""
    return [obj for obj in vars(_types).values() if isinstance(obj, type) and is_dataclass(obj)]


@pytest.mark.parametrize("cls", _dataclasses(), ids=lambda cls: cls.__name__)
def test_vocabulary_types_are_frozen_and_slotted(cls: type) -> None:
    assert cls.__dataclass_params__.frozen  # pyright: ignore[reportAttributeAccessIssue]
    assert "__slots__" in vars(cls)


def test_frozen_means_assignment_fails() -> None:
    ref = PackageRef("uv")

    with pytest.raises(FrozenInstanceError, match="identifier"):
        ref.identifier = "other"  # pyright: ignore[reportAttributeAccessIssue]


def test_basic_auth_repr_masks_the_password() -> None:
    assert repr(BasicAuth("ci", "hunter2")) == "BasicAuth(user='ci', password=***)"


def test_bearer_auth_repr_masks_the_token() -> None:
    assert repr(BearerAuth("ghp_secret")) == "BearerAuth(token=***)"


@pytest.mark.parametrize(
    ("kind", "secret"),
    [
        pytest.param("basic", "hunter2", id="basic-password"),
        pytest.param("bearer", "ghp_secret", id="bearer-token"),
    ],
)
def test_secret_never_appears_in_repr_or_str(kind: str, secret: str) -> None:
    auth: Auth = BasicAuth("ci", secret) if kind == "basic" else BearerAuth(secret)

    assert secret not in repr(auth)
    assert secret not in str(auth)
    assert secret not in f"{auth}"


@pytest.mark.parametrize(
    ("cls", "name"),
    [
        pytest.param(BasicAuth, "password", id="basic-password"),
        pytest.param(BearerAuth, "token", id="bearer-token"),
    ],
)
def test_secret_fields_opt_out_of_the_generated_repr(cls: type, name: str) -> None:
    secret_field = next(f for f in fields(cls) if f.name == name)  # pyright: ignore[reportArgumentType]

    assert not secret_field.repr


def test_host_env_repr_lists_key_names_and_never_values() -> None:
    env = HostEnv({"PATH": "/usr/bin", "OCX_AUTH_GHCR_IO_TOKEN": "ghp_secret"})

    rendered = f"{env!r} {env}"

    assert "ghp_secret" not in rendered
    assert "/usr/bin" not in rendered
    assert repr(env) == "HostEnv(keys=['OCX_AUTH_GHCR_IO_TOKEN', 'PATH'])"


def test_host_env_repr_truncates_a_long_key_list() -> None:
    env = HostEnv({f"VAR_{index:02d}": "value" for index in range(20)})

    rendered = repr(env)

    assert "'VAR_00'" in rendered
    assert "+8 more" in rendered
    assert "'VAR_19'" not in rendered


def test_host_env_source_is_read_only() -> None:
    env = HostEnv({"PATH": "/usr/bin"})

    with pytest.raises(TypeError, match="does not support item assignment"):
        env.source["TOKEN"] = "leaked"  # pyright: ignore[reportIndexIssue]


def test_packageref_metadata_is_read_only() -> None:
    ref = PackageRef("uv", {"version": "0.9.7"})

    with pytest.raises(TypeError, match="does not support item assignment"):
        ref.metadata["version"] = "9.9.9"  # pyright: ignore[reportIndexIssue]


def test_packageref_identity_is_the_identifier_alone() -> None:
    plain = PackageRef("ocx.sh/astral-sh/uv:0.9.7")
    decorated = PackageRef("ocx.sh/astral-sh/uv:0.9.7", {"registry": "ocx.sh"})

    assert plain == decorated
    assert hash(plain) == hash(decorated)
    assert len({plain, decorated}) == 1


def test_packageref_differs_by_identifier() -> None:
    assert PackageRef("uv") != PackageRef("task")
    assert PackageRef("uv") != "uv"


@pytest.mark.parametrize(
    ("build", "message"),
    [
        pytest.param(lambda: RetryPolicy(attempts=0), "attempts must be at least 1", id="zero-attempts"),
        pytest.param(lambda: RetryPolicy(attempts=-1), "attempts must be at least 1", id="negative-attempts"),
        pytest.param(lambda: RetryPolicy(multiplier=0.5), "multiplier must be at least 1", id="shrinking-multiplier"),
        pytest.param(lambda: RetryPolicy(backoff=-1.0), "backoff must not be negative", id="negative-backoff"),
        pytest.param(lambda: RetryPolicy(max_backoff=-1.0), "max_backoff must not be negative", id="negative-cap"),
        pytest.param(
            lambda: RetryPolicy(max_retry_after=-1.0), "max_retry_after must not be negative", id="negative-ceiling"
        ),
    ],
)
def test_retry_policy_rejects_a_policy_that_cannot_produce_a_delay(build, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        build()


def test_retry_policy_reports_every_negative_field_at_once() -> None:
    with pytest.raises(ValueError, match="backoff, max_backoff, max_retry_after must not be negative"):
        RetryPolicy(backoff=-1.0, max_backoff=-1.0, max_retry_after=-1.0)


def test_credentials_stay_readable_on_the_attribute() -> None:
    assert BasicAuth("ci", "hunter2").password == "hunter2"
    assert BearerAuth("ghp_secret").token == "ghp_secret"


def test_auth_alias_covers_both_credential_types() -> None:
    assert set(get_args(Auth.__value__)) == {BasicAuth, BearerAuth}


def test_ambient_reads_the_current_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OCX_SDK_PROBE", "1")

    assert HostEnv.ambient().source["OCX_SDK_PROBE"] == "1"


def test_ambient_is_a_snapshot_not_a_live_view(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OCX_SDK_PROBE", "1")
    env = HostEnv.ambient()

    monkeypatch.setenv("OCX_SDK_PROBE", "2")

    assert env.source["OCX_SDK_PROBE"] == "1"


def test_clean_is_empty() -> None:
    assert dict(HostEnv.clean().source) == {}


def test_source_mapping_is_copied_at_construction() -> None:
    raw = {"PATH": "/usr/bin"}
    env = HostEnv(raw)

    raw["OCX_AUTH_X_TOKEN"] = "leaked"

    assert "OCX_AUTH_X_TOKEN" not in env.source


@pytest.mark.parametrize(
    ("windows", "expected"),
    [
        pytest.param(False, {"PATH", "HOME", "TMPDIR"}, id="posix"),
        pytest.param(True, {"PATH", "HOME", "TMPDIR", "SYSTEMROOT", "TEMP"}, id="windows"),
    ],
)
def test_minimal_keeps_only_platform_essentials(
    monkeypatch: pytest.MonkeyPatch, windows: bool, expected: set[str]
) -> None:
    for key in (*_PLATFORM_KEYS, "OCX_AUTH_X_TOKEN"):
        monkeypatch.setenv(key, "value")

    assert set(HostEnv.minimal(windows=windows).source) == expected


def test_minimal_defaults_to_the_host_platform(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", "/usr/bin")

    assert HostEnv.minimal().source["PATH"] == "/usr/bin"


def test_minimal_drops_variables_that_are_not_set(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in _PLATFORM_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("PATH", "/usr/bin")

    assert dict(HostEnv.minimal(windows=True).source) == {"PATH": "/usr/bin"}


def test_only_narrows_to_keys_that_are_set() -> None:
    env = HostEnv({"PATH": "/usr/bin", "HOME": "/home/ci", "TOKEN": "s"})

    assert dict(env.only("PATH", "MISSING").source) == {"PATH": "/usr/bin"}


def test_without_removes_keys() -> None:
    env = HostEnv({"PATH": "/usr/bin", "TOKEN": "s"})

    assert dict(env.without("TOKEN", "MISSING").source) == {"PATH": "/usr/bin"}


@pytest.mark.parametrize(
    "narrow",
    [
        pytest.param(lambda env: env.only("PATH"), id="only"),
        pytest.param(lambda env: env.without("TOKEN"), id="without"),
    ],
)
def test_narrowing_returns_a_new_snapshot(narrow) -> None:
    env = HostEnv({"PATH": "/usr/bin", "TOKEN": "s"})

    narrowed = narrow(env)

    assert narrowed is not env
    assert dict(env.source) == {"PATH": "/usr/bin", "TOKEN": "s"}


def test_listvar_separator_is_keyword_only() -> None:
    with pytest.raises(TypeError, match="positional"):
        ListVar("x", ":")  # pyright: ignore[reportCallIssue]


def test_listvar_separator_defaults_to_agreeing_with_the_variable() -> None:
    assert ListVar("x").separator is None
    assert ListVar("x", separator=";").separator == ";"


def test_env_value_alias_mirrors_the_ocx_wire_types() -> None:
    assert set(get_args(EnvValue.__value__)) == {str, ConstVar, PathVar, ListVar}


def test_log_level_alias_matches_the_cli_flag() -> None:
    assert get_args(LogLevel.__value__) == ("off", "error", "warn", "info", "debug", "trace")


@pytest.mark.parametrize(
    "identifier",
    [
        pytest.param("uv", id="bare-name"),
        pytest.param("ocx.sh/astral-sh/uv:0.9.7", id="registry-name-tag"),
        pytest.param(f"ocx.sh/astral-sh/uv@sha256:{'a' * 64}", id="digest-pin"),
        pytest.param("ocx.sh/org/pkg:1.0.0-rc.1+build.5", id="semver-build-metadata"),
        pytest.param("localhost:5000/org/pkg:1", id="registry-with-port"),
    ],
)
def test_packageref_byte_roundtrip(identifier: str) -> None:
    assert str(PackageRef(identifier)) == identifier


def test_packageref_carries_metadata_without_parsing_the_identifier() -> None:
    ref = PackageRef("ocx.sh/astral-sh/uv:0.9.7", {"version": "0.9.7", "registry": "ocx.sh"})

    assert ref.identifier == "ocx.sh/astral-sh/uv:0.9.7"
    assert ref.metadata["version"] == "0.9.7"


def test_packageref_metadata_defaults_to_empty() -> None:
    assert dict(PackageRef("uv").metadata) == {}


def test_packageref_metadata_is_copied_at_construction() -> None:
    raw: dict[str, object] = {"version": "0.9.7"}
    ref = PackageRef("uv", raw)

    raw["version"] = "9.9.9"

    assert ref.metadata["version"] == "0.9.7"


def test_package_like_accepts_a_string_or_a_ref() -> None:
    assert set(get_args(PackageLike.__value__)) == {str, PackageRef}


def test_retry_policy_defaults() -> None:
    policy = RetryPolicy()

    assert policy.attempts == 3
    assert policy.backoff == 1.0
    assert policy.multiplier == 2.0
    assert policy.max_backoff == 30.0
    assert policy.max_retry_after == 300.0
    assert policy.jitter is True
    assert policy.retry_on == frozenset({ExitCode.TEMP_FAIL})
    assert policy.sleep is None


def test_retry_policy_defaults_to_the_transient_exit_code_only() -> None:
    assert ExitCode.AUTH not in RetryPolicy().retry_on
    assert ExitCode.UNAVAILABLE not in RetryPolicy().retry_on


def test_channel_values() -> None:
    assert (Channel.STABLE, Channel.NEXT) == ("stable", "next")


def test_install_env_catalog() -> None:
    assert {member.value for member in InstallEnv} == {
        "OCX_INSTALL_VERSION",
        "OCX_INSTALL_DIST_URL",
        "OCX_INSTALL_MIRROR_URL",
        "OCX_INSTALL_REPO",
        "OCX_INSTALL_FORCE",
        "OCX_INSTALL_QUIET",
    }


def test_compat_window_is_semver_and_ordered() -> None:
    tested = tuple(int(part) for part in TESTED_OCX_VERSION.split("."))
    minimum = tuple(int(part) for part in MIN_SUPPORTED.split("."))

    assert len(tested) == len(minimum) == 3
    assert minimum <= tested


def test_managed_config_disabled_is_the_empty_wire_sentinel() -> None:
    assert MANAGED_CONFIG_DISABLED == ""
