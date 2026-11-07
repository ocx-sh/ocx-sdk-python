# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The OCX Authors

"""`build_spawn_env` against the spawn corpus ocx publishes.

`tests/fixtures/contract/spawn/cases.json` is a vendored copy of
`crates/ocx_schema/contract/conformance/spawn/cases.json` in `ocx-sh/ocx`.
Each case names the parent environment, the pairs the caller passed, the
binary the SDK runs and the exact child environment expected. The SDK removes
nothing from the parent; it adds the caller's pairs and writes
`OCX_BINARY_PIN` last.

A caller pair reaches `build_spawn_env` as a host entry, since a caller key
replaces the parent entry of the same key. The two variables an `OcxConfig`
writes on every spawn (`OCX_NO_UPDATE_CHECK`, `OCX_NO_CONSENT`) belong to the
config layer, not the corpus, so they are checked once and set aside.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from ocx_sdk._config import OcxConfig
from ocx_sdk._env import build_spawn_env
from ocx_sdk._types import HostEnv

_VENDORED = Path(__file__).parents[1] / "fixtures" / "contract" / "spawn" / "cases.json"
_UPSTREAM_PATH = Path("crates") / "ocx_schema" / "contract" / "conformance" / "spawn" / "cases.json"
_DEFAULT_OCX_REPO = Path(__file__).parents[3].parent / "ocx"
_CASE_FLOOR = 8
"""Fewer cases than this means the file read is not the spawn corpus."""

_CONFIG_DEFAULTS = {"OCX_NO_UPDATE_CHECK": "1", "OCX_NO_CONSENT": "1"}
"""What a default `OcxConfig` writes on every spawn, outside the corpus."""

_CASES = json.loads(_VENDORED.read_text(encoding="utf-8"))["cases"]


def test_the_corpus_read_is_the_spawn_corpus() -> None:
    assert len(_CASES) >= _CASE_FLOOR


@pytest.mark.parametrize("case", _CASES, ids=[case["name"] for case in _CASES])
def test_build_spawn_env_builds_the_corpus_child_environment(case: dict) -> None:
    host = HostEnv({**dict(case["parent"]), **dict(case["caller"])})

    mapping = dict(build_spawn_env(host, OcxConfig(), Path(case["binary"])).mapping)
    for key, value in _CONFIG_DEFAULTS.items():
        assert mapping.pop(key) == value

    assert sorted(mapping.items()) == sorted(map(tuple, case["child"]))


def test_the_vendored_corpus_matches_the_ocx_copy() -> None:
    """Drift between the vendored corpus and ocx's own.

    `OCX_REPO_DIR` points at an ocx checkout; the default is the `ocx` directory beside this repository.
    """
    upstream = Path(os.environ.get("OCX_REPO_DIR", _DEFAULT_OCX_REPO)) / _UPSTREAM_PATH
    if not upstream.is_file():
        pytest.skip(f"no ocx checkout at {upstream}: set OCX_REPO_DIR")

    assert json.loads(upstream.read_text(encoding="utf-8")) == json.loads(_VENDORED.read_text(encoding="utf-8"))
