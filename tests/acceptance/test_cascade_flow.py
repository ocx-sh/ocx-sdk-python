# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The OCX Authors

"""The rolling-tag audit and repair, end to end against a real registry.

`cascade check` and `cascade repair` are report-then-fail commands: a finding
exits 65 *with* the report, and the SDK tolerates that code so the finding
comes back as a result. The unit tier proves the tolerance against a fake;
this is the binary agreeing about which exits carry a report — driven the
only way the finding can be produced, by publishing a version without
`--cascade` and watching the rolling tags fall behind.

The four `cascade_*.json` fixtures in `tests/fixtures/results/` were recorded
from exactly this sequence.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from ocx_sdk import Ocx

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


@pytest.fixture
def platform(ocx: Ocx) -> str:
    """The platform this ocx builds for, asked of the binary rather than hardcoded."""
    return ocx.about().platforms[0]


@pytest.fixture
def author(ocx: Ocx, platform: str, tmp_path: Path) -> Callable[[str, str], Path]:
    """Return a factory bundling one version of the fixture package.

    Each version prints its own greeting, so two pushes carry different
    content and the second one actually moves the rolling tags.
    """

    def build(identifier: str, greeting: str) -> Path:
        version = identifier.rsplit(":", 1)[1]
        source = tmp_path / version / "package"
        (source / "bin").mkdir(parents=True)
        script = source / "bin" / "hello"
        script.write_text(f"#!/bin/sh\necho '{greeting}'\n", encoding="utf-8")
        script.chmod(0o755)
        metadata = tmp_path / version / "metadata.json"
        metadata.write_text(_METADATA, encoding="utf-8")
        bundle = tmp_path / version / f"hello-{version}.tar.xz"
        ocx.package.create(source, identifier=identifier, platform=platform, metadata=metadata, output=bundle)
        return bundle

    return build


def test_cascade_check_and_repair_round_trip(
    ocx: Ocx, author: Callable[[str, str], Path], registry: str, tmp_path: Path
) -> None:
    """Push with cascade → clean; push without → stale rows at 65; repair → clean again.

    The dry run is asserted to plan every stale tag and write none, and the
    real repair to write every one — `announce_tags` on the dry run is the
    hand-off `announce(tags_file=...)` would consume.
    """
    repository = f"{registry}/wp16/hello"
    first = ocx.package.push(author(f"{repository}:1.0.0", "first"), cascade=True)
    assert set(first.cascade_tags_written) == {"1", "1.0", "latest"}

    clean = ocx.package.cascade_check(repository)
    assert clean.clean is True
    assert [row.status for row in clean.reports[0].rows] == ["ok"] * 3

    ocx.package.push(author(f"{repository}:1.0.1", "second"))

    stale = ocx.package.cascade_check(repository)
    assert (stale.exit_code, stale.clean) == (65, False)
    (audit,) = stale.reports
    assert audit.identifier == repository
    assert {(row.tag, row.status, row.source, row.observed_source) for row in audit.rows} == {
        ("latest", "stale", "1.0.1", "1.0.0"),
        ("1.0", "stale", "1.0.1", "1.0.0"),
        ("1", "stale", "1.0.1", "1.0.0"),
    }

    tags_file = tmp_path / "announce-tags.txt"
    planned = ocx.package.cascade_repair(repository, dry_run=True, announce_tags=tags_file)
    assert (planned.exit_code, planned.dry_run) == (65, True)
    assert planned.announce_tags_path == str(tags_file)
    assert {plan["tag"] for plan in planned.entries[0].planned} == {"1", "1.0", "latest"}
    assert planned.entries[0].outcomes == ()
    assert set(tags_file.read_text(encoding="utf-8").split()) == {"1", "1.0", "latest"}

    repaired = ocx.package.cascade_repair(repository)
    assert repaired.clean is True
    assert {(outcome.tag, outcome.outcome) for outcome in repaired.entries[0].outcomes} == {
        ("1", "written"),
        ("1.0", "written"),
        ("latest", "written"),
    }

    assert ocx.package.cascade_check(repository).clean is True
