# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The OCX Authors

"""The `[env]` carve-out, checked against the binary that owns it (design §8, §14).

The SDK reimplements exactly one merge algorithm, and this file is the reason
it is allowed to: every fold `ComposedEnv` performs is diffed against what a
child actually sees under `ocx run -- printenv`. Nothing else in the suite can
catch a divergence here — the unit tier tests the SDK's model of the fold, and
the model is precisely what could be wrong.

Also here, because they are the same mechanism seen from the other side: the
`OCX_*` key rejection that stops a project reconfiguring ocx itself, the
`OCX_AUTH_*` propagation the compatibility checklist pins, and the slug port
those variable names are built from.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from ocx_sdk import BearerAuth, HostEnv, Ocx, OcxConfig, OcxError, UsageError

from _helpers import child_env, project_file  # isort: skip  — sys.path is this directory under pytest

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from pathlib import Path

    from ocx_sdk import Project

_PLAIN_ENV = """
[tools]

[env]
WP10_CONST = "replaced"
WP10_PATH = { type = "path", value = "/opt/wp10/bin" }
WP10_LIST = { type = "list", value = "appended" }
"""
"""Const, path, and default-separator list — the three folds that declare no separator."""

_SEPARATED_ENV = """
[tools]

[env]
WP10_CSV = { type = "list", value = "beta", separator = "," }
WP10_COLON = { type = "list", value = "gamma", separator = ":" }
"""
"""List entries declaring their own separator, which is what `ocx env` puts on the wire."""

_PARENT = {
    # Every key is already set in the parent, so each fold has something to
    # fold onto: replace; prepend with move-to-front, where the duplicate
    # segment in the middle must not survive; and append-unique.
    "WP10_CONST": "original",
    "WP10_PATH": "/first:/opt/wp10/bin:/last",
    "WP10_LIST": "already",
    "WP10_CSV": "b0",
    "WP10_COLON": "c0",
}


@pytest.fixture
def merging_ocx(ocx_exe: Path, host_env: HostEnv, config: OcxConfig) -> Ocx:
    """A handle whose children inherit the values `_PARENT` seeds."""
    return Ocx(exe=ocx_exe, host_env=HostEnv({**host_env.source, **_PARENT}), config=config)


@pytest.fixture
def merging_project(merging_ocx: Ocx, tmp_path: Path) -> Callable[[str], Project]:
    """Return a factory for locked projects spawned from `merging_ocx`."""
    made = 0

    def make(body: str) -> Project:
        nonlocal made
        made += 1
        root = tmp_path / f"merge-{made}"
        root.mkdir()
        (root / "ocx.toml").write_text(body, encoding="utf-8")
        project = merging_ocx.project(project_file(root))
        project.lock()
        return project

    return make


def _divergence(project: Project) -> Mapping[str, tuple[str | None, str]]:
    """Return every declared key where `compose()` and the real child disagree.

    Scoped to the keys the report declares: a child additionally carries the
    `OCX_*` payload ocx composes for itself, which is opaque pass-through the
    SDK must never reproduce. Values are `(composed, actual)` so a failure
    reads as a diff rather than as a boolean.
    """
    report = project.env()
    composed = report.compose().mapping
    actual = child_env(project.run(["printenv"]).stdout)
    return {
        entry.key: (composed.get(entry.key), actual[entry.key])
        for entry in report.entries
        if composed.get(entry.key) != actual[entry.key]
    }


def test_env_compose_matches_printenv(merging_project: Callable[[str], Project]) -> None:
    """`ComposedEnv` reproduces the environment ocx hands a child, fold for fold."""
    project = merging_project(_PLAIN_ENV)

    composed = project.env().compose().mapping

    assert _divergence(project) == {}
    assert composed["WP10_CONST"] == "replaced"
    assert composed["WP10_PATH"] == "/opt/wp10/bin:/first:/last"
    assert composed["WP10_LIST"] == "already appended"


def test_env_compose_matches_printenv_for_declared_separators(
    merging_project: Callable[[str], Project],
) -> None:
    """A list entry folds with the separator its project declared, not with a space."""
    project = merging_project(_SEPARATED_ENV)

    assert _divergence(project) == {}


def test_env_wire_format_carries_declared_separators(
    merging_ocx: Ocx,
    merging_project: Callable[[str], Project],
) -> None:
    """Pin the evidence behind the row above: ocx really does put `separator` on the wire.

    Read through `invoke` rather than the typed parser, so this keeps holding
    once the parser learns to keep the field.
    """
    project = merging_project(_SEPARATED_ENV)

    raw = merging_ocx.invoke(["--format", "json", "--project", str(project.path), "env"]).stdout

    declared = {entry["key"]: entry.get("separator") for entry in json.loads(raw)["entries"]}
    assert declared == {"WP10_CSV": ",", "WP10_COLON": ":"}


def test_ocx_keys_rejected_in_project_env(project_factory: Callable[..., Project], ocx: Ocx) -> None:
    """An `OCX_*` `[env]` key is refused by the SDK exactly as ocx refuses it.

    ocx exits 64 for a key in its own namespace, because a project that could
    set `OCX_OFFLINE` could reconfigure how ocx resolves. The SDK rejects the
    same key before spawning. Both halves are asserted, so the pre-check
    cannot drift into refusing something ocx accepts, or the reverse.
    """
    project = project_factory(_PLAIN_ENV)

    with pytest.raises(OcxError, match="reserved"):
        project.run(["true"], env={"OCX_SMUGGLED": "1"})

    smuggled = ["--project", str(project.path), "run", "--env", "OCX_SMUGGLED=1", "--", "true"]
    with pytest.raises(UsageError, match="exited 64"):
        ocx.invoke(smuggled)


def test_auth_env_propagation_pinned(
    ocx_exe: Path,
    host_env: HostEnv,
    config: OcxConfig,
    tmp_path: Path,
) -> None:
    """`OCX_AUTH_*` reaches a tool ocx spawns — ocx does not scrub it (design §9).

    A durable-anchor row. The credential-free pattern the docs recommend —
    pull first, then run through `with_config(auth={})` — exists only because
    this is true, so the SDK has to notice if ocx ever starts scrubbing.
    """
    secrets = {"OCX_AUTH_WP10FAKE_TYPE": "token", "OCX_AUTH_WP10FAKE_TOKEN": "s3cr3t"}
    root = tmp_path / "auth-project"
    root.mkdir()
    (root / "ocx.toml").write_text(_PLAIN_ENV, encoding="utf-8")
    project = Ocx(exe=ocx_exe, host_env=HostEnv({**host_env.source, **secrets}), config=config).project(
        project_file(root)
    )
    project.lock()

    seen = child_env(project.run(["printenv"]).stdout)

    assert {key: seen.get(key) for key in secrets} == secrets


def test_registry_slug_fixtures(
    slug_corpus: list[Mapping[str, str]],
    project_factory: Callable[..., Project],
) -> None:
    """Every corpus registry reaches a child under the `OCX_AUTH_<SLUG>_*` names ocx reads.

    Carve-out (b): the SDK ports ocx's strict `to_slug` because the variable
    names must exist before ocx runs. The corpus is the oracle for the
    transform, the child is the oracle for those names surviving the spawn.
    One child covers the lowercase corpus; distinct tokens make a mix-up
    visible. Mixed-case registries are refused at config time (the exported
    name would be one ocx never reads — silent-anonymous, forbidden), so
    those corpus rows assert the refusal instead of spawning.
    """
    named = [case for case in slug_corpus if case["slug"]]
    lowercase = [case for case in named if case["registry"] == case["registry"].lower()]
    mixed = [case for case in named if case["registry"] != case["registry"].lower()]
    auth = {case["registry"]: BearerAuth(f"token-for-{case['slug']}") for case in lowercase}
    project = project_factory(_PLAIN_ENV)

    seen = child_env(project.with_config(auth=auth).run(["printenv"]).stdout)

    expected: dict[str, str] = {}
    for case in lowercase:
        expected[f"OCX_AUTH_{case['slug']}_TYPE"] = "token"
        expected[f"OCX_AUTH_{case['slug']}_TOKEN"] = f"token-for-{case['slug']}"
    assert {key: seen.get(key) for key in expected} == expected
    for case in mixed:
        with pytest.raises(ValueError, match="not lowercase"):
            project.with_config(auth={case["registry"]: BearerAuth("unreachable")})


def test_registry_slug_empty_fails_closed(project_factory: Callable[..., Project]) -> None:
    """A registry that slugs to nothing is refused, never sent anonymously."""
    project = project_factory(_PLAIN_ENV)

    with pytest.raises(OcxError, match="empty slug"):
        project.with_config(auth={"": BearerAuth("unreachable")}).run(["true"])
