# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The OCX Authors

"""Contract tests for `ocx_sdk._config` (C-007).

Named row from the design's mechanism matrix: `test_config_fail_closed_insecure_registries`
(§14) — the field-level half, where an explicit `()` is a different answer than
`None`. The spawn half, where `()` beats an ambient `OCX_INSECURE_REGISTRIES`,
belongs to `_env`.

The rest pins the §7 sketch field for field, the container snapshots that keep
a frozen config actually frozen, and the CWE-319 warning.
"""

from __future__ import annotations

import warnings
from dataclasses import FrozenInstanceError, fields

import pytest

from ocx_sdk._config import ConfigOverrides, OcxConfig
from ocx_sdk._types import BasicAuth, BearerAuth

_DEFAULTS = {
    "home": None,
    "offline": False,
    "frozen": False,
    "config": None,
    "no_config": False,
    "managed_config": None,
    "auth": {},
    "insecure_registries": None,
    "docker_config": None,
    "index": None,
    "jobs": None,
    "log_level": None,
    "mirrors": None,
    "no_update_check": True,
    "no_config_refresh": None,
    "retry": None,
    "timeout": None,
}
"""The §7 sketch, field for field: the names it pins and the defaults it pins."""


def test_config_exposes_exactly_the_fields_the_design_names():
    assert [f.name for f in fields(OcxConfig)] == list(_DEFAULTS)


def test_config_overrides_mirrors_every_config_field():
    # `with_config(**overrides)` type-checks against this TypedDict, so a
    # field missing here is a field a type checker would refuse to override.
    assert set(ConfigOverrides.__annotations__) == {f.name for f in fields(OcxConfig)}


def test_every_config_override_is_optional():
    assert ConfigOverrides.__required_keys__ == frozenset()


@pytest.mark.parametrize(("name", "expected"), list(_DEFAULTS.items()), ids=list(_DEFAULTS))
def test_config_default(name, expected):
    assert getattr(OcxConfig(), name) == expected


def test_config_is_frozen_and_slotted():
    config = OcxConfig()

    assert "__slots__" in vars(OcxConfig)
    with pytest.raises(FrozenInstanceError, match="offline"):
        config.offline = True  # pyright: ignore[reportAttributeAccessIssue]


def test_config_fail_closed_insecure_registries():
    inherit_ambient = OcxConfig()
    replace_with_nothing = OcxConfig(insecure_registries=())

    assert inherit_ambient.insecure_registries is None
    assert replace_with_nothing.insecure_registries == ()
    assert inherit_ambient != replace_with_nothing


def test_config_snapshots_insecure_registries_as_a_tuple():
    hosts = ["reg.internal:5000"]

    config = OcxConfig(insecure_registries=hosts)
    hosts.append("evil.example")

    assert config.insecure_registries == ("reg.internal:5000",)


def test_config_snapshots_its_mappings():
    auth = {"reg.internal:5000": BearerAuth("ghp_secret")}
    mirrors = {"ocx.sh": "mirror.internal"}

    config = OcxConfig(auth=auth, mirrors=mirrors)
    auth["evil.example"] = BearerAuth("ghp_other")
    mirrors["evil.example"] = "evil.internal"

    assert dict(config.auth) == {"reg.internal:5000": BearerAuth("ghp_secret")}
    assert config.mirrors == {"ocx.sh": "mirror.internal"}


def test_config_mappings_refuse_mutation():
    config = OcxConfig(auth={"reg.internal:5000": BearerAuth("ghp_secret")}, mirrors={"ocx.sh": "mirror.internal"})

    with pytest.raises(TypeError, match="does not support item assignment"):
        config.auth["evil.example"] = BearerAuth("ghp_other")  # pyright: ignore[reportIndexIssue]
    with pytest.raises(TypeError, match="does not support item assignment"):
        config.mirrors["evil.example"] = "evil.internal"  # pyright: ignore[reportIndexIssue, reportOptionalSubscript]


def test_config_rejects_a_single_string_of_insecure_registries():
    with pytest.raises(TypeError, match="not a single string"):
        OcxConfig(insecure_registries="reg.internal:5000")


def test_config_warns_when_credentials_meet_a_plaintext_registry():
    with pytest.warns(UserWarning, match="CWE-319") as caught:
        OcxConfig(
            auth={"reg.internal:5000": BasicAuth("ci", "hunter2")},
            insecure_registries=("reg.internal:5000",),
        )

    message = str(caught[0].message)
    assert "reg.internal:5000" in message
    assert "hunter2" not in message


def test_config_warns_when_the_plaintext_registry_differs_only_in_case():
    # auth keys must be lowercase (enforced below); the case-insensitive
    # comparison still matters for the insecure_registries side.
    with pytest.warns(UserWarning, match="CWE-319"):
        OcxConfig(auth={"reg.internal:5000": BearerAuth("ghp_secret")}, insecure_registries=("Reg.Internal:5000",))


def test_config_refuses_an_auth_registry_that_is_not_lowercase():
    # ocx slugs the registry as *it* spelled it — lowercase, in practice — so a
    # mixed-case key names an OCX_AUTH_* variable ocx never looks up, and the
    # pull would run anonymously: the silent-anonymous outcome the slug
    # carve-out forbids. Refused, not warned.
    with pytest.raises(ValueError, match="not lowercase") as caught:
        OcxConfig(auth={"GHCR.io": BearerAuth("ghp_secret")})

    message = str(caught.value)
    assert "GHCR.io" in message
    assert "ghp_secret" not in message


def test_config_stays_quiet_when_credentials_and_plaintext_are_disjoint():
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        config = OcxConfig(auth={"secure.example": BearerAuth("ghp_secret")}, insecure_registries=("plain.example",))

    assert config.insecure_registries == ("plain.example",)
