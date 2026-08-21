# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The OCX Authors

"""Every T1 method against real state, once (design §14).

Signature alignment is the point: WP00's recorded fixtures are the reference
for *shape*, and this file checks the shapes still describe the binary — that
each typed method runs, parses, and comes back with its documented fields
populated rather than defaulted into emptiness. A parser that silently reads
nothing passes its unit tests against a recorded fixture and fails here.

One published package carries the package tier (`ocx.sh/go-task/task:3` —
small, dependency-free, and already what WP00 probed). The author tier runs
entirely locally: `create` then `test --script`, no registry. `push` and
`login` write to one, so they belong to the acceptance tier and are named at
the bottom of this module rather than skipped silently.
"""

from __future__ import annotations

import json
import subprocess
from typing import TYPE_CHECKING

import pytest

from ocx_sdk import MANAGED_CONFIG_DISABLED, TESTED_OCX_VERSION, Ocx, PackageRef, VersionInfo

from _helpers import SMOKE_PACKAGE, project_file  # isort: skip  — sys.path is this directory under pytest

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from ocx_sdk import Project

_PROJECT = """
[tools]

[env]
WP10_SMOKE = "on"
"""

_METADATA = {
    "type": "bundle",
    "version": 1,
    "binaries": ["wp10-hello"],
    "env": [{"key": "PATH", "type": "path", "value": "${installPath}/bin", "visibility": "public"}],
}
"""The minimal authoring sidecar `package create` compiles."""

_SCRIPT = """
result = ocx.run("wp10-hello")
expect.ok(result)
expect.contains(result.stdout, "hello-from-wp10")
"""
"""A Starlark assertion script — the only `package test` form with a pinned envelope."""


def test_version_plain_output(ocx: Ocx) -> None:
    """`version` reads the bare semver line, ocx's documented script contract.

    A durable anchor and the recorded exception to the `--format json`
    pinning rule: the compatibility probe has to work against a binary the
    SDK has not yet agreed to support, so it cannot depend on an envelope.
    """
    plain = ocx.version()

    assert plain == plain.strip()
    assert plain.split(".")[0].isdigit()
    assert VersionInfo.from_json(ocx.invoke(["--format", "json", "version"]).stdout).version == plain


def test_version_matches_the_tested_pin(ocx: Ocx, ocx_exe: Path) -> None:
    """The default session runs the version the SDK claims to be tested against.

    Guards the pin itself: `TESTED_OCX_VERSION` drifting away from what CI
    installs would make every other row in this tier a statement about some
    other binary. Skipped when the version seam deliberately asked for
    another build.
    """
    if ocx_exe != Ocx().exe:
        pytest.skip("the version seam provisioned a binary other than the discovered one")

    assert ocx.version() == TESTED_OCX_VERSION


def test_t1_result_shape_smoke(
    ocx: Ocx,
    project_factory: Callable[..., Project],
    tmp_path: Path,
) -> None:
    """Every T1 method runs once against real state and parses into populated fields.

    Written as one sequence because the state is one sequence: a package has
    to be installed before it can be selected, and selected before it can be
    deselected. Splitting it into per-method tests would either re-download
    per test or hide the ordering in a session fixture.
    """
    ref = PackageRef(SMOKE_PACKAGE)

    # --- machine tier -----------------------------------------------------
    about = ocx.about()
    assert about.version == ocx.version()
    assert about.platforms and about.home

    assert ocx.logout("wp10.invalid.example").registry == "wp10.invalid.example"
    assert ocx.config.update(check_only=True).status == "not_configured"
    assert ocx.config.setup(managed_config=MANAGED_CONFIG_DISABLED).status
    assert ocx.patch.sync().exit_code == 0

    # --- package tier, consumer ------------------------------------------
    installed = ocx.package.install(ref, select=True)
    row = installed.packages[SMOKE_PACKAGE]
    assert "@sha256:" in row.identifier
    assert row.path and row.metadata["binaries"] == ["task"]

    assert ocx.package.which(ref)[SMOKE_PACKAGE].kind == "package"
    assert ocx.package.pull(ref)[SMOKE_PACKAGE].startswith(str(about.home))

    inspected = ocx.package.inspect(ref).packages[0]
    assert inspected.name == SMOKE_PACKAGE
    assert {candidate.platform for candidate in inspected.candidates}

    resolved = ocx.package.inspect(ref, resolve=True).packages[0]
    assert resolved.pinned_digest and resolved.layers
    assert resolved.ref.identifier == resolved.pinned_identifier

    assert list(ocx.package.info(ref)) == [SMOKE_PACKAGE]
    assert ocx.package.deps(ref).roots[0].identifier.startswith(SMOKE_PACKAGE)

    package_env = ocx.package.env(ref)
    assert [binding.name for binding in package_env.binaries] == ["task"]
    assert any(entry.key == "PATH" for entry in package_env.entries)

    assert ocx.package.exec([ref], ["task", "--version"]).stdout.strip()

    with ocx.package.spawn([ref], ["task", "--version"], stdout=subprocess.PIPE, text=True) as child:
        assert child.communicate()[0].strip()

    assert ocx.package.select(ref).packages[SMOKE_PACKAGE].path.endswith("current")
    assert ocx.package.deselect(ref)[0].status == "removed"
    assert {removal.status for removal in ocx.package.uninstall(ref, purge=True)} == {"removed", "purged"}

    # --- package tier, author (local only — no registry) ------------------
    bundle, sidecar = _authored(ocx, tmp_path)
    outcome = ocx.package.test(
        "wp10.local/hello:1.0.0",
        script=_write(tmp_path / "smoke.star", _SCRIPT),
        layers=[bundle],
        platform="linux/amd64",
        metadata=sidecar,
    )
    assert outcome.status == "passed"
    assert outcome.assertion is None
    assert outcome.run.exit_code == 0

    # --- project tier -----------------------------------------------------
    project = project_factory(_PROJECT)
    assert project.status().lock.present is True
    assert project.lock(check_only=True) is None
    assert project.update(check_only=True) is None
    assert project.pull().packages == {}
    assert project.inspect().env
    assert [entry.key for entry in project.env().entries] == ["WP10_SMOKE"]
    assert project.run(["printenv", "WP10_SMOKE"]).stdout.strip() == "on"

    added = project.add(f"task={SMOKE_PACKAGE}", pull=False)
    assert [tool.binding for tool in added] == ["task"]
    assert added[0].digest and added[0].platforms
    assert project.remove("task") == ()

    assert project_factory(_PROJECT, lock=False).lock() == ()


async def test_t1_async_twins_smoke(ocx: Ocx, project_factory: Callable[..., Project]) -> None:
    """The `_async` execution verbs reach the same binary as their sync twins."""
    project = project_factory(_PROJECT)

    assert (await ocx.invoke_async(["version"])).stdout.strip() == ocx.version()
    assert (await project.run_async(["printenv", "WP10_SMOKE"])).stdout.strip() == "on"
    assert (await ocx.package.exec_async([SMOKE_PACKAGE], ["true"], check=False)).exit_code in (0, 1)

    child = await ocx.spawn_async(["version"])
    assert await child.wait() == 0


def test_project_calls_carry_the_project_flag(ocx: Ocx, project_factory: Callable[..., Project]) -> None:
    """Every project-tier call names its project explicitly (a durable anchor).

    The ambient `OCX_PROJECT` is neutralized on every spawn, so `--project` is
    the only thing keeping a call pointed at the right tree — and the reason
    no method depends on the working directory.
    """
    project = project_factory(_PROJECT)

    argv = project.run(["true"]).argv

    assert "--project" in argv
    assert argv[argv.index("--project") + 1] == str(project.path)


def test_project_handle_accepts_a_directory(ocx: Ocx, tmp_path: Path) -> None:
    """`Ocx.project(directory)` works, as its docstring and the design examples promise."""
    root = tmp_path / "by-directory"
    root.mkdir()
    (root / "ocx.toml").write_text(_PROJECT, encoding="utf-8")
    ocx.project(project_file(root)).lock()

    assert ocx.project(root).status().lock.present is True


def test_project_init_writes_into_the_project_directory(project_factory: Callable[..., Project]) -> None:
    """`prj.init()` creates `ocx.toml` in the project it was asked about."""
    project = project_factory(_PROJECT, lock=False)
    project.path.unlink()

    assert project.init().exit_code == 0
    assert project.path.is_file()


def _authored(ocx: Ocx, tmp_path: Path) -> tuple[Path, Path]:
    """Build a one-binary package locally and return its bundle and sidecar."""
    source = tmp_path / "hello"
    (source / "bin").mkdir(parents=True)
    script = _write(source / "bin" / "wp10-hello", "#!/bin/sh\necho hello-from-wp10\n")
    script.chmod(0o755)
    metadata = _write(tmp_path / "metadata.json", json.dumps(_METADATA))
    output = tmp_path / "dist"
    output.mkdir()

    ocx.package.create(
        source,
        identifier="wp10.local/hello:1.0.0",
        platform="linux/amd64",
        output=output,
        metadata=metadata,
        force=True,
    )

    built = sorted(output.iterdir())
    bundle = next(path for path in built if path.suffixes[-2:] == [".tar", ".xz"])
    sidecar = next(path for path in built if path.name.endswith("-metadata.json"))
    return bundle, sidecar


def _write(path: Path, text: str) -> Path:
    """Write `text` to `path` and hand the path back for chaining."""
    path.write_text(text, encoding="utf-8")
    return path


# T1 methods this tier cannot reach, and where they are covered instead:
#
#   ocx.login            — writes credentials to a registry; acceptance
#                          (`test_login_password_stdin`, htpasswd stack).
#   ocx.package.push     — writes to a registry; acceptance (author round-trip).
#   ocx.config.setup     — the adopting form needs a managed-config artifact;
#                          only the clearing form runs here.
