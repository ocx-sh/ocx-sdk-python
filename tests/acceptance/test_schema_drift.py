# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The OCX Authors

"""The vendored report contract against the one ocx publishes (design §3).

`tests/fixtures/contract/reports.v1.json` pins every parser at the tested
version. The published copy at <https://ocx.sh/schemas/reports/v1.json>
tracks ocx `main`, not the release — so this comparison belongs to the nightly
canary alone: on the per-PR path it would go red on every unreleased upstream
change, the opposite of what the tested-window design asks for. It runs only
when the version seam asks for `latest`, which is exactly the canary job.

What is compared is what the SDK reads: the set of report roots and, for
every definition a parser is mapped to in `test_wire_contract.py`, its
`properties` and `required` sets. Descriptions and enum wording are free to
move without a signal here.
"""

from __future__ import annotations

import importlib
import json
import os
import sys
import urllib.request
from pathlib import Path

import pytest

import ocx_sdk

_TESTS = Path(__file__).parents[1]
_VENDORED = _TESTS / "fixtures" / "contract" / "reports.v1.json"
_PUBLISHED = "https://ocx.sh/schemas/reports/v1.json"
_TIMEOUT = 30.0


def _parser_map() -> dict[str, tuple[str, ...]]:
    """Return `test_wire_contract._DEFS` — the one place that says what the SDK reads.

    `tests/unit` is not on the path of an acceptance-only run, so it is added
    here rather than by a `conftest` every acceptance test would pay for.
    """
    sys.path.insert(0, str(_TESTS / "unit"))
    return importlib.import_module("test_wire_contract")._DEFS


def _fetch_published() -> dict:
    """Return the published contract, decoded.

    Identified the way `bootstrap` identifies itself: the host answers 403 to
    urllib's default `User-Agent`, so a bare `urlopen` reads as drift.
    """
    request = urllib.request.Request(_PUBLISHED, headers={"User-Agent": f"ocx-sdk/{ocx_sdk.__version__} schema-canary"})
    with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:
        return json.loads(response.read().decode("utf-8"))


def _shape(contract: dict, names: set[str]) -> dict[str, tuple[set[str], set[str]]]:
    """Return `(properties, required)` per named definition."""
    defs = contract["$defs"]
    return {name: (set(defs[name].get("properties", {})), set(defs[name].get("required", []))) for name in names}


def test_vendored_reports_schema_matches_the_published_one() -> None:
    """The definitions the SDK parses still read the same on ocx `main`.

    Nightly only: `OCX_TEST_VERSIONS=latest` is the canary's own setting, and
    the check is meaningless against a pinned binary.
    """
    if "latest" not in os.environ.get("OCX_TEST_VERSIONS", ""):
        pytest.skip("schema drift is the nightly canary's signal: set OCX_TEST_VERSIONS=latest")

    vendored = json.loads(_VENDORED.read_text(encoding="utf-8"))
    published = _fetch_published()
    mapped = {name for names in _parser_map().values() for name in names}

    assert set(published["reports"]) >= set(vendored["reports"]), "a report root the SDK types left the contract"
    assert _shape(published, mapped) == _shape(vendored, mapped)
