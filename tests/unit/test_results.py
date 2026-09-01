# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The OCX Authors

"""Contract tests for `ocx_sdk._results` (v0.1 C-003, v0.1 S-002).

Every parser has one **recorded-fixture** test: it parses a file in
`tests/fixtures/results/` that came out of a real ocx run and asserts the
field values that file actually holds. Those fixtures are the specification —
if a parser disagrees with one, the parser is wrong.

Provenance of `tests/fixtures/results/*.json`:

- Most are the WP00 captures in `tests/fixtures/cli/`, with the ocx tracing
  lines stripped from the head. WP00 recorded stdout and stderr merged; the SDK
  reads them as separate pipes, so a `results/` fixture is the stdout half.
- `version`, `login`, `logout`, `config_update`, and `config_setup` had no WP00
  capture and were probed live against the same ocx 0.5.8 binary under an
  isolated `OCX_HOME`/`DOCKER_CONFIG`.
- `push` is the stdout half of `cli/package_push.json`, itself a live ocx
  0.6.0 capture: 0.6 renamed the `canonical_tags_written` key to
  `keep_tags_written` and changed the tag form, so the 0.5.8 recording
  specified a payload no supported binary emits.
- `version`, `about`, and `status` were **recaptured against ocx 0.6.0** for
  0.2.0: `MIN_SUPPORTED` is 0.6.0, so a fixture recording a binary the SDK now
  refuses would specify a version it can never meet. Same shape, new values —
  the recapture changed no key. `status` came from a one-tool scratch project
  under `OCX_HOME=/tmp/ocx-recapture-home`, per the plan's fixture-capture
  runbook. The rest still record 0.5.8, which the D7 absent-vs-null contract
  they pin is unaffected by.
- The 0.6 signing surface — `sign`, `verify`, `attest`, `sbom*`, `copy*`,
  `sweep*`, `push_signed`, `info_populated`, and both `partial/` fixtures —
  is a **live ocx 0.6.0 capture**, taken against a throwaway `zot:v2.1.18`
  registry on loopback and the golden cosign key ocx's own suite ships
  (`test/tests/fixtures/golden/keys/cosign.key`, password `ocxtest`), driven
  with `--key file://… --no-rekor-upload`. That is the offline key-mode path
  the plan's contract-tier section names; a hermetic run has no Fulcio, which
  is why `certificate_identity` reads `""` rather than a keyless identity
  throughout. `zot`, not `registry:2`: distribution 2.8.3 answers 404 to a
  manifest fetch that does not name the index media type in `Accept`, so the
  sign pipeline cannot resolve its own subject there.

A handful of shapes could not be recorded because no probe produced one: an
`ocx env` advisory or integration, a failing `package test`, a broken lock, a
configured managed-config tier, and — new in 0.2.0 — a signing leg that fails
while its sibling lands, a push whose inline signing fails after the push
lands, a `copy` refused on a sidecar conflict, and a keyless `verify` entry
carrying its four certificate optionals. Those tests are marked *doc-derived*
and build their payload from the serde definitions at `v0.6.0` — "could not
capture" is not "cannot test", and every shape listed above has a test. They
are the parts of this module most likely to need revisiting.
"""

from __future__ import annotations

import ast
import inspect
import json
import os
import textwrap
from pathlib import Path

import pytest

from ocx_sdk import _results
from ocx_sdk._errors import OcxProcessError
from ocx_sdk._results import (
    AboutInfo,
    Advisory,
    AttestationReport,
    CommandResult,
    ConfigSetupReport,
    ConfigUpdateReport,
    CopyReport,
    DepsReport,
    DryRunEntry,
    EnvEntry,
    EnvReport,
    InspectReport,
    InstallReport,
    LoginResult,
    LogoutResult,
    PullReport,
    PushResult,
    SbomListingReport,
    SignatureReport,
    StatusReport,
    SweepReport,
    TestResult,
    VerificationReport,
    VersionInfo,
    parse_description_pull,
    parse_package_pull,
    parse_pull_dry_run,
    parse_removals,
    parse_tool_rows,
    parse_which,
    partial_report,
)
from ocx_sdk._types import HostEnv, PackageRef

FIXTURES = Path(__file__).parent.parent / "fixtures" / "results"

TASK_DIGEST = "sha256:df420d1c99d7e80ce6366104af4e6b57135e95940763e235c907525c438effee"
TASK_PINNED = f"ocx.sh/go-task/task:3@{TASK_DIGEST}"
HELLO_IDENTIFIER = "127.0.0.1:5099/wp3/hello:2.0.0"
HELLO_DIGEST = "a9ab5a514e512a6f4497403b42f7888c494eca23e07145485e9eff826c12a36d"


def load(name: str) -> str:
    """Return a recorded stdout fixture verbatim."""
    return (FIXTURES / name).read_text(encoding="utf-8")


_LOCK_STATUS = {"present": True, "declaration_hash_expected": "sha256:h"}
_STATUS_HEAD: dict = {"project": "/p", "lock": _LOCK_STATUS, "groups": {}, "package_settings": {}}
_ENV_HEAD: dict = {"entries": [], "binaries": [], "entrypoints": [], "integrations": [], "advisories": []}


def _status_doc(**fields: object) -> str:
    """An `ocx status` document — the four keys it always writes, overridden as given.

    Every parser test needs the full always-present set now that `StatusReport`
    reads it with `_need` (D7), and spelling the filler out per test would bury
    the one key each is about.
    """
    return json.dumps({**_STATUS_HEAD, **fields})


def _env_doc(**arrays: object) -> str:
    """An `ocx env` document — the five arrays it always writes, overridden as given."""
    return json.dumps({**_ENV_HEAD, **arrays})


# --- version / about --------------------------------------------------------


def test_version_info_from_recorded_fixture():
    info = VersionInfo.from_json(load("version.json"))

    assert info.version == "0.6.0"
    assert info.commit["describe"] == "v0.6.0"
    assert info.commit["dirty"] is False
    assert info.build["target"] == "x86_64-unknown-linux-musl"
    assert info.ci["provider"] == "github-actions"
    # This build baked in neither, and absent stays absent.
    assert info.channel is None
    assert info.cargo_pkg_version is None


def test_version_info_needs_only_the_version():
    info = VersionInfo.from_json('{"version": "9.9.9"}')

    assert info.version == "9.9.9"
    assert info.commit == {}
    assert info.build == {}
    assert info.ci == {}


def test_about_info_from_recorded_fixture():
    about = AboutInfo.from_json(load("about.json"))

    assert about.version == "0.6.0"
    assert about.registry == "ocx.sh"
    assert about.home == "/tmp/ocx-recapture-home"
    assert about.shell == "Zsh"
    assert about.platforms == ("linux/amd64",)
    assert about.libc == ("libc.glibc",)
    assert about.channel is None
    assert about.commit["sha"] == "e48ef73cbd92ae1972868b1037bcabe1094d2180"


# --- status -----------------------------------------------------------------


def test_status_report_from_recorded_fixture():
    status = StatusReport.from_json(load("status.json"))

    assert status.project == "/tmp/ocx-recapture-prj/ocx.toml"
    assert status.lock.present is True
    assert status.lock.current is True
    assert status.lock.lock_version == 3
    assert status.lock.declaration_hash == status.lock.declaration_hash_expected
    assert status.lock.generated_by == "ocx 0.6.0"
    assert status.lock.error is None

    binding = status.groups["default"].tools["task"]
    assert binding.declared == "ocx.sh/go-task/task:3"
    assert binding.platforms is not None
    assert binding.platforms["linux/amd64"] == TASK_DIGEST
    assert status.groups["default"].env == {}
    assert status.package_settings == {}


def test_status_binding_state_is_key_presence():
    """Declared-only, lock-only, and fully-bound differ by which keys exist."""
    status = StatusReport.from_json(
        _status_doc(
            groups={
                "default": {
                    "tools": {
                        "declared_only": {"declared": "ocx.sh/a/b:1"},
                        "lock_only": {"platforms": {"linux/amd64": TASK_DIGEST}},
                    },
                    "env": {},
                }
            }
        )
    )

    tools = status.groups["default"].tools
    assert tools["declared_only"].platforms is None
    assert tools["declared_only"].ref == PackageRef("ocx.sh/a/b:1")
    assert tools["lock_only"].declared is None
    assert tools["lock_only"].ref is None


def test_status_reports_a_broken_lock_as_payload():
    """Doc-derived: `status` exits 0 and puts the failure in `lock.error`."""
    status = StatusReport.from_json(
        _status_doc(
            lock={
                "present": True,
                "declaration_hash_expected": "sha256:h",
                "current": False,
                "error": "checksum drift",
            }
        )
    )

    assert status.lock.current is False
    assert status.lock.error == "checksum drift"
    assert status.groups == {}


# --- inspect ----------------------------------------------------------------


def test_inspect_report_from_recorded_fixture():
    report = InspectReport.from_json(load("inspect.json"))

    (package,) = report.packages
    assert package.name == "task"
    assert package.identifier == "ocx.sh/go-task/task:3"
    assert len(package.candidates) == 6
    assert report.platform is None
    assert report.env == ()

    linux = next(candidate for candidate in package.candidates if candidate.platform == "linux/amd64")
    assert linux.digest == TASK_DIGEST
    assert str(linux.ref) == TASK_PINNED
    # The toolchain tier omits both; the package tier carries them.
    assert linux.media_type is None
    assert linux.size is None
    # Unresolved: `ref` falls back to what was requested.
    assert str(package.ref) == "ocx.sh/go-task/task:3"


def test_package_inspect_candidates_carry_media_type_and_size():
    report = InspectReport.from_json(load("package_inspect.json"))

    (package,) = report.packages
    # Package tier names the row by the full requested identifier.
    assert package.name == "ocx.sh/go-task/task:3"
    linux = next(candidate for candidate in package.candidates if candidate.platform == "linux/amd64")
    assert linux.media_type == "application/vnd.oci.image.manifest.v1+json"
    assert linux.size == 453


def test_inspect_resolve_carries_the_pin_and_the_resolution_chain():
    report = InspectReport.from_json(load("inspect_resolve.json"))

    assert report.platform == "linux/amd64+libc.glibc"
    (package,) = report.packages
    assert package.pinned_identifier == TASK_PINNED
    assert package.pinned_digest == TASK_DIGEST
    assert package.platform is not None
    assert package.platform["architecture"] == "amd64"
    assert package.metadata is not None
    assert package.metadata["binaries"] == ["task"]
    assert [layer["size"] for layer in package.layers] == [12522124]
    assert package.resolution is not None
    # Toolchain tier starts from a lock-pinned digest, so there is no index hop.
    assert [hop["role"] for hop in package.resolution["chain"]] == ["manifest", "config"]
    # Resolved: `ref` prefers the pin, ready for the next call's argv.
    assert str(package.ref) == TASK_PINNED


def test_inspect_closure_carries_the_closure_untyped():
    report = InspectReport.from_json(load("inspect_closure.json"))

    (package,) = report.packages
    assert package.closure is not None
    assert package.closure["deps"] == []
    assert package.closure["conflicts"] == {"entrypoints": [], "repositories": []}
    assert package.closure["surface"]["interface"]["binaries_complete"] is True
    # `--closure` replaces the candidate listing entirely.
    assert package.candidates == ()


# --- env --------------------------------------------------------------------


def test_env_report_from_recorded_fixture():
    report = EnvReport.from_json(load("env.json"))

    (entry,) = report.entries
    assert entry.key == "PATH"
    assert entry.type == "path"
    assert entry.value.endswith("/content")

    (binary,) = report.binaries
    assert binary.name == "task"
    assert str(binary.ref) == f"ocx.sh/go-task/task@{TASK_DIGEST}"
    # All five arrays are always present; four of them were empty here.
    assert report.entrypoints == ()
    assert report.integrations == ()
    assert report.advisories == ()


def test_env_report_iterates_its_entries():
    report = EnvReport.from_json(load("env.json"))

    assert [entry.key for entry in report] == ["PATH"]


def test_env_report_carries_integrations_and_advisories():
    """Doc-derived: no probe produced either, the design pins both shapes."""
    report = EnvReport.from_json(
        _env_doc(
            integrations=[{"namespace": "vscode", "package": TASK_PINNED, "payload": {"tasks": 1}}],
            advisories=[{"kind": "shadowed", "package": TASK_PINNED, "key": "PATH", "message": "shadowed by /usr/bin"}],
        )
    )

    (integration,) = report.integrations
    assert integration.namespace == "vscode"
    assert integration.payload["tasks"] == 1
    assert str(integration.ref) == TASK_PINNED

    (advisory,) = report.advisories
    assert advisory == Advisory(kind="shadowed", package=TASK_PINNED, key="PATH", message="shadowed by /usr/bin")
    assert str(advisory.ref) == TASK_PINNED


def test_env_report_parses_an_advisory_that_names_no_variable():
    """`undeclared-binaries` carries no `key` at all (`env.rs:189`).

    The whole report fails to parse if `key` is read as required, so this is
    `env()` — and therefore `compose`/`activate` — going down on a payload ocx
    emits routinely for a deferred tool with no `binaries` claim.
    """
    report = EnvReport.from_json(
        _env_doc(
            advisories=[{"kind": "undeclared-binaries", "package": TASK_PINNED, "message": "declares no binaries"}]
        )
    )

    (advisory,) = report.advisories
    assert advisory.kind == "undeclared-binaries"
    assert advisory.key is None
    assert str(advisory.ref) == TASK_PINNED


def test_env_report_parses_an_unattributed_binary_and_integration():
    """`package` is skipped when ocx cannot attribute the claim (`env.rs:100,137`).

    Upstream writes `Some` for every row today and reserves `None` for a
    source with no clean attribution — so this pins the shape before the
    first payload that carries it, rather than after.
    """
    report = EnvReport.from_json(
        _env_doc(binaries=[{"name": "task"}], integrations=[{"namespace": "vscode", "payload": {}}])
    )

    (binary,) = report.binaries
    assert binary.name == "task"
    assert binary.package is None
    assert binary.ref is None

    (integration,) = report.integrations
    assert integration.namespace == "vscode"
    assert integration.package is None
    assert integration.ref is None


def test_env_entry_rejects_a_type_it_cannot_fold():
    """Fail closed: a silently mis-folded variable is worse than a refusal."""
    with pytest.raises(ValueError, match="secret"):
        EnvReport.from_json(_env_doc(entries=[{"key": "K", "type": "secret", "value": "v"}]))


def test_env_compose_maps_each_entry_type_onto_the_merge():
    report = EnvReport.from_json(
        _env_doc(
            entries=[
                {"key": "TOOL", "type": "constant", "value": "task"},
                {"key": "PATH", "type": "path", "value": "/pkg/bin"},
                {"key": "FLAGS", "type": "list", "value": "-v"},
            ]
        ),
        base={"TOOL": "old", "PATH": f"/usr/bin{os.pathsep}/pkg/bin", "FLAGS": "-q"},
    )

    merged = report.compose().mapping

    assert merged["TOOL"] == "task"  # constant replaces
    assert merged["PATH"] == f"/pkg/bin{os.pathsep}/usr/bin"  # path moves to front
    assert merged["FLAGS"] == "-q -v"  # list appends under ocx's default separator


def test_env_entry_carries_the_separator_ocx_declared():
    """`ocx env` puts a `separator` on a list entry whose project declared one."""
    report = EnvReport.from_json(load("env_separators.json"))

    declared = {entry.key: entry.separator for entry in report}

    assert declared == {
        "WP10_COLON": ":",
        "WP10_CONST": None,
        "WP10_CSV": ",",
        "WP10_LIST": None,
        "WP10_PATH": None,
    }


def test_env_compose_folds_a_list_with_its_declared_separator():
    """A comma-separated variable is joined with a comma, not with a space.

    Folding a declared separator as the default would produce `b0 beta` where
    ocx produces `b0,beta` — a silently wrong value rather than a failure,
    which is why the printenv contract diff exists.
    """
    report = EnvReport.from_json(
        load("env_separators.json"),
        base={"WP10_CSV": "b0", "WP10_COLON": "c0", "WP10_LIST": "zero"},
    )

    merged = report.compose().mapping

    assert merged["WP10_CSV"] == "b0,beta"
    assert merged["WP10_COLON"] == "c0:gamma"
    assert merged["WP10_LIST"] == "zero alpha"  # undeclared still takes ocx's default


def test_env_compose_hermetic_under_clean_base(monkeypatch):
    """v0.1 S-002: a report produced hermetically composes hermetically."""
    monkeypatch.setenv("AMBIENT_SECRET", "leaked")
    report = EnvReport.from_json(load("env.json"), base=HostEnv.clean().source)

    merged = report.compose().mapping

    assert list(merged) == ["PATH"]
    assert "AMBIENT_SECRET" not in merged


def test_env_compose_accepts_an_explicit_base():
    report = EnvReport.from_json(load("env.json"), base={"NOT": "used"})

    merged = report.compose(base={"HOME": "/home/ci"}).mapping

    assert merged["HOME"] == "/home/ci"
    assert "NOT" not in merged


# --- the three path-shaped results ------------------------------------------


def test_which_from_recorded_fixture():
    results = parse_which(load("which.json"))

    assert results["ocx.sh/go-task/task:3"].kind == "package"
    assert results["ocx.sh/go-task/task:3"].path.endswith("/420d1c99d7e80ce6366104af4e6b57")


def test_project_pull_separates_advisories_from_packages():
    """`pull` mixes a sibling `advisories` key in among the identifiers."""
    report = PullReport.from_json(load("pull.json"))

    assert list(report.packages) == [f"ocx.sh/go-task/task@{TASK_DIGEST}"]
    assert report.packages[f"ocx.sh/go-task/task@{TASK_DIGEST}"].kind == "package"
    assert report.advisories == ()


def test_pull_dry_run_rows_carry_a_null_path_for_what_is_not_cached():
    """Doc-derived: `--dry-run` reports a **bare root array**, not `pull`'s object.

    `path` is always-present-nullable (`pull_dry_run.rs:47`), and `null` lands
    on exactly the `would-fetch` rows the preview exists to surface — so a
    parser that defaulted it would report a cached path for a package that is
    not there.
    """
    rows = parse_pull_dry_run(
        json.dumps(
            [
                {"package": TASK_PINNED, "status": "cached", "path": "/store/task"},
                {"package": HELLO_IDENTIFIER, "status": "would-fetch", "path": None},
            ]
        )
    )

    assert [row.status for row in rows] == ["cached", "would-fetch"]
    assert rows[0].path == "/store/task"
    assert rows[1].path is None
    assert str(rows[0].ref) == TASK_PINNED
    assert isinstance(rows[0], DryRunEntry)


def test_package_pull_returns_bare_path_strings():
    """The third path shape: no `{path, kind}` object, just the path."""
    paths = parse_package_pull(load("package_pull.json"))

    assert paths["ocx.sh/go-task/task:3"].endswith("/420d1c99d7e80ce6366104af4e6b57")


# --- package install / select / removal / info / deps -----------------------


def test_install_report_from_recorded_fixture():
    report = InstallReport.from_json(load("install.json"))

    # Keyed by what was asked for; the value carries what it resolved to.
    installed = report.packages["ocx.sh/go-task/task:3"]
    assert installed.identifier == TASK_PINNED
    assert installed.path is not None
    assert installed.path.endswith("/candidates/3")
    assert installed.metadata["binaries"] == ["task"]
    assert str(installed.ref) == TASK_PINNED
    assert installed.ref.metadata["type"] == "bundle"


def test_removals_from_recorded_fixture():
    (removal,) = parse_removals(load("uninstall.json"))

    assert removal.package == "ocx.sh/go-task/task:3"
    assert removal.status == "removed"
    assert removal.path is not None
    assert removal.path.endswith("/candidates/3")
    assert str(removal.ref) == "ocx.sh/go-task/task:3"


def test_info_passes_through_a_null_description():
    results = parse_description_pull(load("info.json"))

    assert results == {"ocx.sh/go-task/task:3": None}


def test_info_names_the_renamed_command_when_stdout_is_not_a_payload():
    """C-002: `package info` became `package description pull` in ocx 0.6.

    The command string is the parser's only user-visible identity — it is what
    the error tells an operator to re-run. Left at `package info` it names a
    subcommand the supported binary no longer has.
    """
    with pytest.raises(ValueError, match=r"ocx package description pull"):
        parse_description_pull("not json at all")


def test_description_pull_types_a_populated_entry():
    """C-021/D4: a described package comes back as a struct, not a raw mapping.

    Captured live against ocx 0.6.0 after a `description push` — the first
    populated sample this repo has held. `keywords` is a single delimited
    string upstream, so a parser that split it into a collection would fail
    the last assertion rather than merely look different.
    """
    results = parse_description_pull(load("info_populated.json"))

    entry = results["127.0.0.1:5198/wp4/hello:1.0.0"]
    assert entry is not None
    assert entry.title == "Hello"
    assert entry.description == "A throwaway WP4 fixture package."
    assert entry.keywords == "hello,fixture,wp4"


def test_description_pull_keeps_a_blank_description_distinct_from_no_description():
    """Doc-derived: all three keys are always present, `null` when unset.

    `package_description.rs:11-16` carries no `skip_serializing_if`, so an
    all-`null` struct is a description the registry holds and has left blank —
    a different fact from the `None` entry that means it holds none at all.
    """
    results = parse_description_pull(
        '{"blank:1": {"title": null, "description": null, "keywords": null}, "none:1": null}'
    )

    assert results["blank:1"] == _results.PackageDescription(title=None, description=None, keywords=None)
    assert results["none:1"] is None


def test_deps_report_from_recorded_fixture():
    report = DepsReport.from_json(load("deps.json"))

    (root,) = report.roots
    assert root.identifier == TASK_PINNED
    assert root.repeated is False
    assert root.visibility is None
    assert root.dependencies == ()
    assert str(root.ref) == TASK_PINNED


# --- lock-writing commands --------------------------------------------------


def test_tool_rows_from_recorded_fixture():
    (row,) = parse_tool_rows(load("lock.json"))

    assert row.binding == "task"
    assert row.group == "default"
    assert row.digest == TASK_DIGEST
    assert row.platforms["windows/arm64"].startswith("sha256:")


def test_tool_rows_of_an_empty_change_set():
    """`remove` reported `[]` on the case WP00 exercised."""
    assert parse_tool_rows("[]") == ()


# --- package test / push ----------------------------------------------------


def test_package_test_envelope():
    """Named matrix row (§14): the stable v1 `--script` envelope."""
    result = TestResult.from_json(load("test.json"))

    assert result.status == "passed"
    assert result.passed is True
    assert result.assertion is None
    assert result.run is not None
    assert result.run.exit_code == 0
    assert result.run.stdout == "hello from wp00 spike\n"
    assert result.run.stderr == ""
    assert result.run.duration_ms == 1
    assert result.run.truncated is False


def test_package_test_failure_names_the_assertion_that_fired():
    """Doc-derived: `status` decides, `assertion.kind` says why."""
    result = TestResult.from_json(
        json.dumps(
            {
                "status": "failed",
                "assertion": {"kind": "exit_code", "message": "expected 0, got 1"},
                "run": {"exit_code": 1, "stdout": "", "stderr": "boom", "duration_ms": 7, "truncated": False},
            }
        )
    )

    assert result.passed is False
    assert result.assertion is not None
    assert result.assertion.kind == "exit_code"
    assert result.run is not None
    assert result.run.stderr == "boom"


def test_package_test_reports_no_run_rather_than_a_fabricated_one():
    """`run: null` means nothing ran — not a run whose every field is a zero.

    Defaulting the object made `result.run.exit_code == 0` for a script that
    never invoked `ocx.run`, which reads as a command that succeeded. `passed`
    was always right (it reads `status`); `run` was the part that lied.
    """
    result = TestResult.from_json(_bare({"status": "script_error", "assertion": None, "run": None}))

    assert result.passed is False
    assert result.run is None


def test_push_result_from_recorded_fixture():
    """The keep tag is `__ocx.keep.sha256-<hex>`, and the hex is the platform digest.

    0.5 emitted `canonical_tags_written` holding a `sha256.<hex>` tag; 0.6
    emits `keep_tags_written` holding one `__ocx.keep.sha256-<hex>` per
    platform manifest. Reading the old key against a 0.6 binary yields an
    empty tuple with no error, so the value is asserted here, never just the
    attribute's presence.
    """
    result = PushResult.from_json(load("push.json"))

    assert result.identifier == HELLO_IDENTIFIER
    assert result.status == "pushed"
    assert result.manifest_digest.startswith("sha256:")
    assert result.cascade_tags_written == ()
    assert result.keep_tags_written == (f"__ocx.keep.sha256-{HELLO_DIGEST}",)
    assert result.layers == {"mounted": 0, "uploaded": 1, "verified": 0}
    assert result.platform_digests == {"linux/amd64": f"sha256:{HELLO_DIGEST}"}
    assert str(result.ref) == HELLO_IDENTIFIER


# --- auth / config ----------------------------------------------------------


def test_login_result_from_recorded_fixture():
    assert LoginResult.from_json(load("login.json")) == LoginResult(registry="localhost:5099", username="ci")


def test_logout_result_from_recorded_fixture():
    assert LogoutResult.from_json(load("logout.json")) == LogoutResult(registry="localhost:5099")


def test_config_update_report_from_recorded_fixture():
    """An unconfigured tier reports `status` and nothing else."""
    report = ConfigUpdateReport.from_json(load("config_update.json"))

    assert report.status == "not_configured"
    assert report.source is None
    assert report.policy is None
    assert report.kill_switches == ()
    assert report.drift is None


def test_config_update_report_of_a_configured_tier():
    """Doc-derived: the fields a configured tier adds (CLI contracts research)."""
    report = ConfigUpdateReport.from_json(
        json.dumps(
            {
                "status": "updated",
                "source": "ocx.sh/acme/config:stable",
                "digest": "sha256:abc",
                "policy": "apply",
                "tag": "stable",
                "fetched_at": "2026-08-21T00:00:00Z",
                "paused_until": None,
                "pinned": None,
                "drift": {"keys": ["registry"]},
                "kill_switches": ["updates"],
            }
        )
    )

    assert report.status == "updated"
    assert report.source == "ocx.sh/acme/config:stable"
    assert report.policy == "apply"
    assert report.kill_switches == ("updates",)
    assert report.drift == {"keys": ["registry"]}


def test_config_setup_report_flattens_the_managed_config_block():
    assert ConfigSetupReport.from_json(load("config_setup.json")).status == "would_adopt"


# --- cross-cutting parser contracts -----------------------------------------


def _bare(payload: dict) -> str:
    """Render a payload the way `push` and `copy` write it: at the JSON root (D11)."""
    return json.dumps(payload)


def _enveloped(data: dict) -> str:
    """Render a payload the way `sign`/`verify`/`attest`/`sbom` write it: under `data` (D11)."""
    return json.dumps({"schema_version": 1, "command": "package sign", "exit_code": 0, "data": data})


def _parse_sweep(raw: str) -> SweepReport:
    """A swept `sign`, with the row parser C-017's seam takes."""
    return SweepReport.from_json(raw, SignatureReport.from_dict)


def _without(payload: dict, key: str) -> dict:
    """The payload minus one key — a wire document that dropped a required field."""
    return {name: value for name, value in payload.items() if name != key}


_SIGN_HEAD = {"identifier": "i", "subject_digest": "sha256:d"}
_VERIFY_HEAD = {
    "subject_digest": "sha256:d",
    "referrer_digest": "sha256:r",
    "certificate_identity": "",
    "certificate_oidc_issuer": "",
    "signed_at": "",
}
_SIGNATURE_ENTRY = {
    "signature_format": "bundle",
    "discovery_method": "referrers_api",
    "key_backend": "file",
    "referrer_digest": "sha256:r",
}
_SIGN_REPORT = {
    **_SIGN_HEAD,
    "legs": [{"format": "bundle"}],
    "platform": "linux/amd64",
    "signer": "file",
    "certificate_identity": "",
    "certificate_oidc_issuer": "",
    "key_backend": "file",
    "transparency_log_index": None,
}
_ATTEST_REPORT = {
    "identifier": "i",
    "platform": "linux/amd64",
    "subject_digest": "sha256:d",
    "predicate_type": "https://cyclonedx.org/bom",
    "signed": True,
    "transparency_log_index": None,
}
_PUSH_HEAD = {
    "identifier": "i",
    "status": "pushed",
    "manifest_digest": "sha256:d",
    "cascade_tags_written": [],
    "keep_tags_written": [],
    "layers": {},
}
_SBOM_HEAD = {
    "summary": {
        "status": "success",
        "verification": "verified",
        "exit_code": 0,
        "total": 0,
        "verified": 0,
        "unverified": 0,
        "refused": 0,
    },
    "entries": [],
    "refused": [],
}
_SBOM_ENTRY = {
    "predicate_type": "https://cyclonedx.org/bom",
    "verified": True,
    "shadowed": False,
    "subject_digest": "sha256:d",
    "referrer_digest": "sha256:r",
}
_COPY_HEAD = {
    "source": "s",
    "target": "t",
    "status": "copied",
    "platforms": [],
    "cascade_tags_written": [],
    "keep_tags_written": [],
    "referrers_copied": 0,
    "sidecars_copied": 0,
    "sidecar_conflicts": [],
    "blobs": {"present": 0, "mounted": 0, "uploaded": 0},
    "description": None,
}
_LISTING_SUMMARY = _SBOM_HEAD["summary"]
_PACKAGE_DESCRIPTION = {"title": "t", "description": "d", "keywords": "k"}
_SIGN_LEG = {"format": "bundle"}
_SWEPT_ROW = {"tag": "1.0", "status": "completed"}
_SWEEP_DOC = {"tags": []}
_SIGNED_PLATFORM = {"platform": "linux/amd64", "status": "completed"}
_ATTESTATION_OUTCOME = {"status": "succeeded", "predicate_type": "https://cyclonedx.org/bom", "signed": True}
_REFUSED_ENTRY = {"referrer_digest": "sha256:r", "reason": "unreadable", "reason_kind": "sbom_summary_failed"}
_SBOM_SUMMARY_OUT = {"spec_version": "1.6", "component_count": 1}
_COPIED_ROW = {"platform": "linux/amd64", "digest": "sha256:d", "disposition": "added"}
_BLOB_SUMMARY = {"present": 0, "mounted": 0, "uploaded": 0}
_ATTESTATION_FAILED = {"status": "failed", "kind": "sign_no_identity", "message": "no identity"}
_VERSION_HEAD = {"version": "0.6.0"}
_ABOUT_HEAD = {
    "version": "0.6.0",
    "registry": "ocx.sh",
    "home": "/h",
    "shell": "sh",
    "platforms": [],
    "libc": [],
}
_GROUP_STATUS: dict = {"tools": {}, "env": {}}
_CANDIDATE = {"digest": "sha256:d", "pinned": "p", "platform": "linux/amd64"}
_INSPECTED = {"name": "n", "identifier": "i"}
_INSPECT_HEAD: dict = {"packages": [], "env": []}
_ENV_ENTRY = {"key": "K", "type": "constant", "value": "1"}
_PACKAGE_BINDING = {"name": "task"}
_INTEGRATION: dict = {"namespace": "vscode", "payload": {}}
_ADVISORY = {"kind": "undeclared-binaries", "package": "p", "message": "m"}
_WHICH = {"path": "/p", "kind": "package"}
_PULL_HEAD: dict = {"advisories": []}
_DRY_RUN_ENTRY: dict = {"package": TASK_PINNED, "status": "would-fetch", "path": None}
_INSTALLED: dict = {"identifier": "i", "path": "/p", "metadata": {}}
_REMOVAL = {"package": "p", "status": "removed", "path": "/p"}
_DEP_NODE: dict = {"identifier": "i", "repeated": False, "visibility": None, "dependencies": []}
_DEPS_HEAD: dict = {"roots": []}
_TOOL_ROW: dict = {"binding": "task", "group": "default", "digest": "sha256:d", "platforms": {}}
_ASSERTION = {"kind": "exit_code", "message": "m"}
_TEST_RUN = {"exit_code": 0, "stdout": "", "stderr": "", "duration_ms": 1, "truncated": False}
_TEST_HEAD: dict = {"status": "passed", "assertion": None, "run": None}
_LOGIN = {"registry": "r", "username": "u"}
_LOGOUT = {"registry": "r"}
_CONFIG_UPDATE = {"status": "not_configured"}
_MANAGED_CONFIG = {"status": "would_adopt"}
"""One minimal payload per struct, holding its **required** keys and nothing else.

Every key here earns a missing-field row below by being dropped in turn, so an
optional key in one of these dicts would generate a row asserting a raise that
never comes. Written as dicts rather than JSON literals because the rows differ
by one key each, and a literal per row would be a hundred near-copies drifting
apart.

Required means **always written by `ocx 0.6.0`'s serde** — a field with no
`skip_serializing_if`, whether or not its Rust type is `Option` (D7's
always-present-nullable third category). The `.get` half of the same contract
is `_OPTIONAL_KEYS` below.
"""


_REQUIRED_FIELD_SOURCES = [
    # (struct, parser, its required-keys payload, where that payload sits in the document)
    ("VersionInfo", VersionInfo.from_json, _VERSION_HEAD, _bare),
    ("AboutInfo", AboutInfo.from_json, _ABOUT_HEAD, _bare),
    ("LockStatus", StatusReport.from_json, _LOCK_STATUS, lambda sub: _bare({**_STATUS_HEAD, "lock": sub})),
    (
        "GroupStatus",
        StatusReport.from_json,
        _GROUP_STATUS,
        lambda sub: _bare({**_STATUS_HEAD, "groups": {"default": sub}}),
    ),
    ("StatusReport", StatusReport.from_json, _STATUS_HEAD, _bare),
    (
        "Candidate",
        InspectReport.from_json,
        _CANDIDATE,
        lambda sub: _bare({**_INSPECT_HEAD, "packages": [{**_INSPECTED, "candidates": [sub]}]}),
    ),
    ("InspectedPackage", InspectReport.from_json, _INSPECTED, lambda sub: _bare({**_INSPECT_HEAD, "packages": [sub]})),
    ("InspectReport", InspectReport.from_json, _INSPECT_HEAD, _bare),
    ("EnvEntry", EnvReport.from_json, _ENV_ENTRY, lambda sub: _bare({**_ENV_HEAD, "entries": [sub]})),
    ("PackageBinding", EnvReport.from_json, _PACKAGE_BINDING, lambda sub: _bare({**_ENV_HEAD, "binaries": [sub]})),
    ("Integration", EnvReport.from_json, _INTEGRATION, lambda sub: _bare({**_ENV_HEAD, "integrations": [sub]})),
    ("Advisory", EnvReport.from_json, _ADVISORY, lambda sub: _bare({**_ENV_HEAD, "advisories": [sub]})),
    ("EnvReport", EnvReport.from_json, _ENV_HEAD, _bare),
    ("WhichResult", parse_which, _WHICH, lambda sub: _bare({"a:1": sub})),
    ("PullReport", PullReport.from_json, _PULL_HEAD, _bare),
    ("DryRunEntry", parse_pull_dry_run, _DRY_RUN_ENTRY, lambda sub: json.dumps([sub])),
    ("InstalledPackage", InstallReport.from_json, _INSTALLED, lambda sub: _bare({"a:1": sub})),
    ("RemovalResult", parse_removals, _REMOVAL, lambda sub: json.dumps([sub])),
    ("DepNode", DepsReport.from_json, _DEP_NODE, lambda sub: _bare({"roots": [sub]})),
    ("DepsReport", DepsReport.from_json, _DEPS_HEAD, _bare),
    ("ToolRow", parse_tool_rows, _TOOL_ROW, lambda sub: json.dumps([sub])),
    ("Assertion", TestResult.from_json, _ASSERTION, lambda sub: _bare({**_TEST_HEAD, "assertion": sub})),
    ("TestRun", TestResult.from_json, _TEST_RUN, lambda sub: _bare({**_TEST_HEAD, "run": sub})),
    ("TestResult", TestResult.from_json, _TEST_HEAD, _bare),
    ("LoginResult", LoginResult.from_json, _LOGIN, _bare),
    ("LogoutResult", LogoutResult.from_json, _LOGOUT, _bare),
    ("ConfigUpdateReport", ConfigUpdateReport.from_json, _CONFIG_UPDATE, _bare),
    # Two rows: `ConfigSetupReport` reads one key from the envelope and one from
    # the block inside it, so no single flat payload holds both.
    ("ConfigSetupReport", ConfigSetupReport.from_json, {"managed_config": _MANAGED_CONFIG}, _bare),
    ("ConfigSetupReport", ConfigSetupReport.from_json, _MANAGED_CONFIG, lambda sub: _bare({"managed_config": sub})),
    ("PackageDescription", parse_description_pull, _PACKAGE_DESCRIPTION, lambda sub: json.dumps({"a:1": sub})),
    (
        "SignatureLegReport",
        SignatureReport.from_json,
        _SIGN_LEG,
        lambda sub: _enveloped({**_SIGN_REPORT, "legs": [sub]}),
    ),
    ("SignatureReport", SignatureReport.from_json, _SIGN_REPORT, _enveloped),
    (
        "SignatureEntry",
        VerificationReport.from_json,
        _SIGNATURE_ENTRY,
        lambda sub: _enveloped({**_VERIFY_HEAD, "signatures": [sub]}),
    ),
    ("VerificationReport", VerificationReport.from_json, _VERIFY_HEAD, _enveloped),
    ("AttestationReport", AttestationReport.from_json, _ATTEST_REPORT, _enveloped),
    ("SweptTagReport", _parse_sweep, _SWEPT_ROW, lambda sub: _enveloped({"tags": [sub]})),
    ("SweepReport", _parse_sweep, _SWEEP_DOC, _enveloped),
    (
        "SignedPlatformReport",
        PushResult.from_json,
        _SIGNED_PLATFORM,
        lambda sub: _bare({**_PUSH_HEAD, "signatures": [sub]}),
    ),
    # Two rows: `AttestationOutcome` is an internally-tagged enum, and each
    # variant's own fields are required only under the status that emits them.
    (
        "AttestationOutcome",
        PushResult.from_json,
        _ATTESTATION_OUTCOME,
        lambda sub: _bare({**_PUSH_HEAD, "attestation": sub}),
    ),
    (
        "AttestationOutcome",
        PushResult.from_json,
        _ATTESTATION_FAILED,
        lambda sub: _bare({**_PUSH_HEAD, "attestation": sub}),
    ),
    (
        "ListingSummary",
        SbomListingReport.from_json,
        _LISTING_SUMMARY,
        lambda sub: _enveloped({**_SBOM_HEAD, "summary": sub}),
    ),
    ("SbomEntry", SbomListingReport.from_json, _SBOM_ENTRY, lambda sub: _enveloped({**_SBOM_HEAD, "entries": [sub]})),
    (
        "RefusedEntry",
        SbomListingReport.from_json,
        _REFUSED_ENTRY,
        lambda sub: _enveloped({**_SBOM_HEAD, "refused": [sub]}),
    ),
    (
        "SbomSummaryOut",
        SbomListingReport.from_json,
        _SBOM_SUMMARY_OUT,
        lambda sub: _enveloped({**_SBOM_HEAD, "entries": [{**_SBOM_ENTRY, "summary": sub}]}),
    ),
    ("SbomListingReport", SbomListingReport.from_json, _SBOM_HEAD, _enveloped),
    ("CopiedPlatformRow", CopyReport.from_json, _COPIED_ROW, lambda sub: _bare({**_COPY_HEAD, "platforms": [sub]})),
    ("BlobSummary", CopyReport.from_json, _BLOB_SUMMARY, lambda sub: _bare({**_COPY_HEAD, "blobs": sub})),
    ("CopyReport", CopyReport.from_json, _COPY_HEAD, _bare),
    ("PushResult", PushResult.from_json, _PUSH_HEAD, _bare),
]
"""Every struct in `_results`, with the payload whose keys become its missing-field rows.

A table rather than seventy hand-written rows: the rows are mechanical (drop
one key, expect that key named back), and generating them means a field added
upstream gets its row the moment it joins the payload above — which is the
drift `keep_tags_written` slipped through.
"""


PARSERS = {
    "version": VersionInfo.from_json,
    "about": AboutInfo.from_json,
    "status": StatusReport.from_json,
    "inspect": InspectReport.from_json,
    "env": EnvReport.from_json,
    "pull": PullReport.from_json,
    "pull --dry-run": parse_pull_dry_run,
    "package install": InstallReport.from_json,
    "package deps": DepsReport.from_json,
    "package test": TestResult.from_json,
    "package push": PushResult.from_json,
    "package sign": SignatureReport.from_json,
    "package verify": VerificationReport.from_json,
    "package attest": AttestationReport.from_json,
    "package sbom": SbomListingReport.from_json,
    "package copy": CopyReport.from_json,
    "package sign/attest --tags": _parse_sweep,
    "login": LoginResult.from_json,
    "logout": LogoutResult.from_json,
    "config update": ConfigUpdateReport.from_json,
    "config setup": ConfigSetupReport.from_json,
    "package which": parse_which,
    "package description pull": parse_description_pull,
    "package pull": parse_package_pull,
    "package uninstall": parse_removals,
    "lock": parse_tool_rows,
}


@pytest.mark.parametrize("parse", list(PARSERS.values()), ids=list(PARSERS))
@pytest.mark.parametrize("raw", ["", "   \n"], ids=["empty", "blank"])
def test_every_parser_refuses_an_empty_body(parse, raw):
    """Empty is a fact about the command, not the payload.

    `lock --check` and `update --check` exit 0 with no body at all; the client
    layer answers `None` for those, so a parser seeing emptiness means stdout
    came from somewhere it should not have.
    """
    with pytest.raises(ValueError, match="--check"):
        parse(raw)


@pytest.mark.parametrize("parse", list(PARSERS.values()), ids=list(PARSERS))
def test_every_parser_names_the_command_on_garbage(parse):
    with pytest.raises(ValueError, match="ocx "):
        parse("not json at all")


@pytest.mark.parametrize(
    ("parse", "wrong"),
    [
        pytest.param(VersionInfo.from_json, "[]", id="array-for-object"),
        pytest.param(StatusReport.from_json, '"a string"', id="scalar-for-object"),
        pytest.param(parse_which, "[]", id="array-for-keyed-object"),
        pytest.param(parse_tool_rows, "{}", id="object-for-array"),
        pytest.param(parse_removals, "{}", id="object-for-removals"),
        pytest.param(parse_pull_dry_run, "{}", id="object-for-dry-run"),
    ],
)
def test_a_payload_of_the_wrong_shape_is_refused(parse, wrong):
    with pytest.raises(ValueError, match="expected a JSON"):
        parse(wrong)


_MISSING_FIELD_CASES = [
    # (struct whose `_need` fires, parser to call, payload, the key it wants)
    # Not a missing-field row: it pins `SweptTagReport`'s `is not None` guard,
    # which mutates to a truthiness test that swallows an empty report object.
    (
        "SignatureReport",
        _parse_sweep,
        _enveloped({"tags": [{**_SWEPT_ROW, "report": {}}]}),
        "identifier",
    ),
    *(
        (name, parse, place(_without(payload, key)), key)
        for name, parse, payload, place in _REQUIRED_FIELD_SOURCES
        for key in payload
    ),
]
"""One row per required field this suite pins — several structs carry more than one.

`test_every_struct_with_a_required_field_has_a_missing_field_row` is
set-equality over the names, so extra rows for a struct are free; a struct
with **no** row is what it catches.
"""


def _required_keys() -> dict[str, set[str]]:
    """Fold `_REQUIRED_FIELD_SOURCES` into one required-key set per struct.

    A union across rows, because two structs need more than one payload:
    `AttestationOutcome`'s variants carry different fields, and
    `ConfigSetupReport` reads one key per nesting level.
    """
    keys: dict[str, set[str]] = {}
    for name, _parse, payload, _place in _REQUIRED_FIELD_SOURCES:
        keys.setdefault(name, set()).update(payload)
    return keys


_REQUIRED_KEYS = _required_keys()


_OPTIONAL_KEYS: dict[str, set[str]] = {
    "VersionInfo": {"channel", "cargo_pkg_version", "commit", "build", "ci"},
    "AboutInfo": {"channel", "commit", "build", "ci"},
    "LockStatus": {"current", "lock_version", "declaration_hash", "generated_by", "generated_at", "error"},
    "ToolBinding": {"declared", "platforms"},
    "GroupStatus": set(),
    "StatusReport": set(),
    "Candidate": {"media_type", "size"},
    "InspectedPackage": {
        "candidates",
        "pinned_identifier",
        "pinned_digest",
        "platform",
        "metadata",
        "layers",
        "resolution",
        "closure",
    },
    "EnvEntry": {"separator"},
    "PackageBinding": {"package"},
    "Integration": {"package"},
    "Advisory": {"key"},
    "EnvReport": set(),
    "InspectReport": {"platform"},
    "WhichResult": set(),
    "PullReport": set(),
    "DryRunEntry": set(),
    "InstalledPackage": set(),
    "RemovalResult": set(),
    "DepNode": set(),
    "DepsReport": set(),
    "PackageDescription": set(),
    "ToolRow": set(),
    "Assertion": set(),
    "TestRun": set(),
    "TestResult": set(),
    "PushResult": {"platform_digests", "signatures", "attestation"},
    "SignatureLegReport": {"payload_digest", "manifest_digest", "error"},
    "SignatureReport": {"public_key_hint"},
    "SignatureEntry": {"certificate_identity", "certificate_oidc_issuer", "signed_at", "rekor_log_index"},
    "VerificationReport": {"signatures"},
    "AttestationReport": {
        "bundle_digest",
        "referrer_digest",
        "sidecar_digest",
        "certificate_identity",
        "certificate_oidc_issuer",
        "key_backend",
        "public_key_hint",
    },
    "SweptTagReport": {"report", "kind", "message"},
    "SweepReport": set(),
    "SignedPlatformReport": {"report", "kind", "message"},
    "AttestationOutcome": {"referrer_digest", "sidecar_digest"},
    "ListingSummary": set(),
    "SbomEntry": {"summary", "certificate_identity", "certificate_oidc_issuer", "signed_at"},
    "RefusedEntry": set(),
    "SbomSummaryOut": {"serial_number", "top_level_component"},
    "SbomListingReport": set(),
    "CopiedPlatformRow": set(),
    "BlobSummary": set(),
    "CopyReport": set(),
    "LoginResult": set(),
    "LogoutResult": set(),
    "ConfigUpdateReport": {
        "source",
        "digest",
        "policy",
        "tag",
        "fetched_at",
        "paused_until",
        "pinned",
        "drift",
        "kill_switches",
    },
    "ConfigSetupReport": set(),
}
"""Struct name to the wire keys ocx 0.6.0 may omit — the `.get` half of D7.

Optional means the field carries a `skip_serializing_if` upstream, or sits in a
manual `Serialize` impl that writes it only for some shapes (`InspectedPackage`'s
body variants, `InspectReport.platform`). Everything else the parser touches is
in `_REQUIRED_KEYS`, including fields whose Rust type is `Option` but whose key
is always written — D7's third category, where `null` is the answer rather than
absence.

Read together the two tables say, per struct, which operator each key it reads
must use. They do **not** claim the parser reads every key on the wire: a key
ocx writes and the SDK ignores appears in neither.
"""


_PARSER_METHODS = frozenset({"from_dict", "from_json"})
"""The two method names a struct parses its wire payload in."""


def _wire_reads(struct: type) -> tuple[set[str], set[str]]:
    """Return the keys `struct`'s parsers read with `_need` and with `.get`.

    Read off the source rather than by calling the parser: the point is to pin
    the *operator* each key is read with, which no payload can observe — a
    `.get` on a key ocx always writes answers `None` instead of raising, and
    every fixture in this file would still pass.

    Scoped to `from_dict`/`from_json` so a helper property that happens to call
    `.get` on something other than the wire payload cannot land in the table.
    """
    need: set[str] = set()
    get: set[str] = set()
    tree = ast.parse(textwrap.dedent(inspect.getsource(struct)))
    parsers = [node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name in _PARSER_METHODS]
    for node in (child for parser in parsers for child in ast.walk(parser)):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        if isinstance(node.func, ast.Name) and node.func.id == "_need":
            need.add(_wire_key(node.args[1]))
        elif isinstance(node.func, ast.Attribute) and node.func.attr == "get":
            get.add(_wire_key(node.args[0]))
    return need, get


def _wire_key(node: ast.expr) -> str:
    """Resolve a key argument — a string literal, or a `_results` constant."""
    if isinstance(node, ast.Constant):
        return str(node.value)
    assert isinstance(node, ast.Name), f"a wire key must be a literal or a constant, not {ast.dump(node)}"
    return str(getattr(_results, node.id))


def _parsing_structs() -> set[str]:
    """Return every `_results` struct that reads a wire key at all."""
    return {
        name
        for name, member in vars(_results).items()
        if inspect.isclass(member) and member.__module__ == _results.__name__ and any(_wire_reads(member))
    }


def _structs_with_required_fields() -> set[str]:
    """Return every `_results` struct whose parser calls `_need`."""
    return {name for name in _parsing_structs() if _wire_reads(getattr(_results, name))[0]}


def test_every_struct_with_a_required_field_has_a_missing_field_row():
    """A struct that gains a required field must gain a row here with it."""
    assert {name for name, *_ in _MISSING_FIELD_CASES} == _structs_with_required_fields()


def test_every_parsing_struct_declares_its_wire_contract():
    """A new struct must join both tables — neither half is optional."""
    assert set(_OPTIONAL_KEYS) == _parsing_structs()


@pytest.mark.parametrize(
    ("struct", "required"),
    [pytest.param(getattr(_results, name), keys, id=name) for name, keys in _REQUIRED_KEYS.items()],
)
def test_every_required_field_is_pinned(struct: type, required: set[str]):
    """Every key `_REQUIRED_FIELD_SOURCES` calls required must be read with `_need`.

    `test_every_struct_with_a_required_field_has_a_missing_field_row` is
    set-equality over *struct names*, so a `_need` added to a struct that
    already has rows slips through it — and the payload is a separate literal,
    which is exactly what gets missed. This compares the two key sets directly,
    so the row set stays complete by construction rather than by whoever last
    ran a mutation sweep.
    """
    assert _wire_reads(struct)[0] == required


@pytest.mark.parametrize(
    ("struct", "optional"),
    [pytest.param(getattr(_results, name), keys, id=name) for name, keys in _OPTIONAL_KEYS.items()],
)
def test_every_optional_field_is_pinned(struct: type, optional: set[str]):
    """Every key the table calls optional must be read with `.get`, and only those.

    The `_need` half above is one-directional: a field that ocx always writes
    but the SDK reads with `.get` is invisible to it, because no payload can
    distinguish "absent, defaulted" from "present". That inversion fails open —
    `AttestationOutcome.signed` returned `None` for a document nothing had
    signed — so the `.get` set is pinned against the serde too.
    """
    assert _wire_reads(struct)[1] == optional


@pytest.mark.parametrize(
    ("parse", "payload", "missing"),
    [
        pytest.param(parse, payload, missing, id=f"{name}.{missing}")
        for name, parse, payload, missing in _MISSING_FIELD_CASES
    ],
)
def test_a_missing_required_field_names_itself_and_the_command(parse, payload, missing):
    with pytest.raises(ValueError, match=f"no '{missing}' field") as caught:
        parse(payload)

    assert str(caught.value).startswith("`ocx "), "the message must name the command that produced the payload"


@pytest.mark.parametrize(
    ("parse", "payload", "expected"),
    [
        # Each payload is complete but for the one field under test, so the
        # shape check is what fires — not a `_need` on some other key.
        pytest.param(
            AboutInfo.from_json,
            _bare({**_ABOUT_HEAD, "platforms": "linux/amd64"}),
            "a JSON array of strings",
            id="texts-shredded-a-bare-string",
        ),
        pytest.param(
            PushResult.from_json,
            _bare({**_PUSH_HEAD, "cascade_tags_written": "v1"}),
            "a JSON array of strings",
            id="texts-on-push",
        ),
        pytest.param(
            InspectReport.from_json,
            _bare({**_INSPECT_HEAD, "packages": "task"}),
            "a JSON array of objects",
            id="rows",
        ),
        pytest.param(StatusReport.from_json, _status_doc(lock="broken"), "a JSON object", id="table"),
        # `payload` is `serde_json::Value` — arbitrary JSON, so a number is a
        # legal document, and it used to escape as a bare `TypeError`.
        pytest.param(
            EnvReport.from_json,
            _env_doc(integrations=[{**_INTEGRATION, "payload": 5}]),
            "a JSON object",
            id="scalar-where-a-table-belongs",
        ),
        pytest.param(
            InspectReport.from_json,
            _bare({**_INSPECT_HEAD, "packages": [{**_INSPECTED, "platform": "linux/amd64"}]}),
            "a JSON object",
            id="optional-table",
        ),
    ],
)
def test_a_field_of_the_wrong_type_is_refused_rather_than_coerced(parse, payload, expected):
    """A string where an array belongs used to shred into one-character entries.

    `tuple("linux/amd64")` is eleven plausible-looking values, and nothing
    downstream can tell that from a real list — so the parsers refuse the
    shape instead, and say which shape they wanted.
    """
    with pytest.raises(ValueError, match=expected):
        parse(payload)


@pytest.mark.parametrize(
    ("parse", "payload", "read"),
    [
        pytest.param(
            parse_removals,
            json.dumps([{**_REMOVAL, "status": "absent", "path": None}]),
            lambda result: result[0].path,
            id="removal-of-what-was-never-there",
        ),
        pytest.param(
            InstallReport.from_json,
            _bare({"a:1": {**_INSTALLED, "path": None}}),
            lambda result: result.packages["a:1"].path,
            id="install-that-materialized-nothing",
        ),
        pytest.param(
            AboutInfo.from_json,
            _bare({**_ABOUT_HEAD, "shell": None}),
            lambda result: result.shell,
            id="host-with-no-detected-shell",
        ),
    ],
)
def test_an_always_present_nullable_field_carries_none(parse, payload, read):
    """`null` is the answer, not a missing key — so the annotation must allow it.

    The key is always on the wire, so `_need` is right and no missing-field
    row catches this; the hazard is the *type*, which claimed `str` while the
    parser handed back `None`.
    """
    assert read(parse(payload)) is None


@pytest.mark.parametrize(
    ("parse", "payload", "read", "expected"),
    [
        pytest.param(
            VersionInfo.from_json,
            '{"version": "1", "brand_new": 1}',
            lambda result: result.version,
            "1",
            id="on-a-struct",
        ),
        pytest.param(
            LogoutResult.from_json,
            '{"registry": "r", "brand_new": 1}',
            lambda result: result.registry,
            "r",
            id="on-a-one-field-struct",
        ),
        pytest.param(
            parse_removals,
            '[{"package": "p", "status": "removed", "path": "/p", "brand_new": 1}]',
            lambda result: [(row.package, row.status, row.path) for row in result],
            [("p", "removed", "/p")],
            id="on-an-array-row",
        ),
        pytest.param(
            EnvReport.from_json,
            _env_doc(entries=[{"key": "K", "type": "constant", "value": "v", "brand_new": 1}]),
            lambda result: [(entry.key, entry.type, entry.value) for entry in result],
            [("K", "constant", "v")],
            id="on-a-nested-row",
        ),
    ],
)
def test_unknown_keys_are_ignored(parse, payload, read, expected):
    """Forward compatibility: a newer ocx that adds a field keeps working.

    The known fields must come through with their real values — a parser that
    returned something merely truthy would pass a bare `assert parse(...)`.
    """
    assert read(parse(payload)) == expected


@pytest.mark.parametrize(
    ("read", "key"),
    [
        pytest.param(lambda: VersionInfo.from_json(load("version.json")).commit, "sha", id="sub-object"),
        pytest.param(lambda: StatusReport.from_json(load("status.json")).groups, "default", id="keyed-structs"),
        pytest.param(
            lambda: InstallReport.from_json(load("install.json")).packages,
            "ocx.sh/go-task/task:3",
            id="install-report",
        ),
        pytest.param(lambda: parse_which(load("which.json")), "ocx.sh/go-task/task:3", id="which"),
        pytest.param(lambda: parse_description_pull(load("info.json")), "ocx.sh/go-task/task:3", id="info"),
    ],
)
def test_results_do_not_hand_out_writable_mappings(read, key):
    """A frozen result whose mapping a caller can write through is not frozen."""
    with pytest.raises(TypeError, match="does not support item assignment"):
        read()[key] = "mutated"


def test_result_refs_roundtrip_identifiers_byte_for_byte():
    """Every package-referencing row yields a ref usable as the next argv.

    Checked against the literal identifiers the fixtures hold, not against
    each ref's own `identifier`: `str(ref) == ref.identifier` is true of any
    `PackageRef` whatever the parser put in it, so it proves nothing about
    the carrying.
    """
    inspected = InspectReport.from_json(load("inspect.json")).packages[0]
    refs = {
        "install": InstallReport.from_json(load("install.json")).packages["ocx.sh/go-task/task:3"].ref,
        "uninstall": parse_removals(load("uninstall.json"))[0].ref,
        "deps": DepsReport.from_json(load("deps.json")).roots[0].ref,
        "env-binary": EnvReport.from_json(load("env.json")).binaries[0].ref,
        "inspect-candidate": next(c for c in inspected.candidates if c.platform == "linux/amd64").ref,
        "push": PushResult.from_json(load("push.json")).ref,
    }

    assert all(isinstance(ref, PackageRef) for ref in refs.values())
    assert {name: str(ref) for name, ref in refs.items()} == {
        "install": TASK_PINNED,
        "uninstall": "ocx.sh/go-task/task:3",
        "deps": TASK_PINNED,
        "env-binary": f"ocx.sh/go-task/task@{TASK_DIGEST}",
        "inspect-candidate": TASK_PINNED,
        "push": HELLO_IDENTIFIER,
    }


def test_entry_type_is_the_wire_spelling():
    """The `type` field carries ocx's own token, not a re-spelled enum."""
    entry = EnvEntry.from_dict({"key": "K", "type": "list", "value": "v"})

    assert entry.type == "list"


def test_command_result_repr_reports_sizes_not_captured_bytes() -> None:
    """`Completed.__repr__`'s decision (D10), one layer up.

    `CommandResult` carries unredacted stdout, and a renderer that walks frame
    locals reprs whatever a frame holds — so the default dataclass repr would
    print the payload verbatim. `argv` survives: `_process` redacted it.
    """
    result = CommandResult(argv=("ocx", "version"), exit_code=0, stdout="s3cret-payload", stderr="s3cret-log")

    rendered = repr(result)

    assert "s3cret" not in rendered
    assert rendered == "CommandResult(argv=('ocx', 'version'), exit_code=0, stdout=<14 chars>, stderr=<10 chars>)"


# --- the 0.6 signing surface (C-011 - C-019, D7, D10, D11) ------------------
#
# Every fixture below is a live ocx 0.6.0 capture against a throwaway zot
# registry and the golden cosign key ocx's own test suite ships. The
# key-mode/offline path is the one a hermetic run can drive (the plan's
# contract-tier section); keyless Fulcio identities are what leaves
# `certificate_identity` empty rather than absent here.

WP4_HELLO = "127.0.0.1:5198/wp4/hello:1.0.0"
WP4_INDEX = "sha256:3ca7e955071fb4053711b99239a34030c2c805226b348ef51a1d6c81d4835088"
WP4_PLATFORM = "sha256:8e19360014b32617d87ddf40052d0b1b5298f897e68c2a9595813d60d712bf7f"
WP4_SIG_REFERRER = "sha256:92a0526ef6bdc73a89946d66e98a1bbda9eb046fa7bc8c264460073eedbf44f4"
WP4_ATT_REFERRER = "sha256:fc3ca39575742c6f3fa204557681c7f1d8489fa50979c73fe1395d654b594ad6"
KEY_HINT = "IPToq8s+AghvzWpOChKmKD7Pi00bHB6zR/NABNmDFtA="
CYCLONEDX = "https://cyclonedx.org/bom"
KEYLESS_IDENTITY = "https://github.com/ocx-sh/ocx/.github/workflows/release.yml@refs/tags/v0.6.0"
KEYLESS_ISSUER = "https://token.actions.githubusercontent.com"


def test_signature_report_unwraps_the_envelope_and_reads_every_field():
    """C-011 + D11: `sign` writes its report under `data`, not at the root.

    `transparency_log_index` is asserted `is None` rather than falsy: it is
    **present and null** under `--no-rekor-upload` (D7), and a parser that
    reached for it with `data.get()` would produce the same `None` from a key
    that was never there — which this field's own missing-field row is what
    separates. The two leg digests are asserted to differ because they are
    different objects (the signed payload vs the manifest it hangs from), and
    a parser that read one key twice would still pass an existence check.
    """
    report = SignatureReport.from_json(load("sign.json"))

    assert report.identifier == WP4_HELLO
    assert report.subject_digest == WP4_INDEX
    assert report.platform == "any"
    assert report.signer == "file"
    assert report.key_backend == "file"
    assert report.certificate_identity == ""
    assert report.certificate_oidc_issuer == ""
    assert report.public_key_hint == KEY_HINT
    assert report.transparency_log_index is None
    (leg,) = report.legs
    assert leg.format == "bundle"
    assert leg.manifest_digest == WP4_SIG_REFERRER
    assert leg.payload_digest == "sha256:155064cd4c315fda3f9b38f14043b2c04f93494de70b4509df2ec8f16d7bf5fb"
    assert leg.error is None


def test_an_enveloped_parser_refuses_a_bare_report():
    """D11's whole point: the split follows no rule the SDK can infer.

    Handing `sign`'s parser the document `copy` writes must fail loudly. A
    parser that shrugged and read the root would return a report full of
    `None` from a payload that describes something else entirely.
    """
    with pytest.raises(ValueError, match="no 'data' field"):
        SignatureReport.from_json(load("copy.json"))


def test_signature_leg_names_the_leg_that_failed():
    """Doc-derived (C-011, D10): a `--signature-format both` run losing one leg.

    Not capturable offline — both legs land against a registry that takes
    them, and there is no flag that fails one. The landed leg is asserted to
    survive alongside the failed one: hiding it would leave an operator
    re-signing what is already published (`signature.rs`'s own reasoning).
    """
    report = SignatureReport.from_json(
        _enveloped(
            {
                "identifier": WP4_HELLO,
                "subject_digest": WP4_INDEX,
                "legs": [
                    {"format": "bundle", "payload_digest": "sha256:p", "manifest_digest": "sha256:m"},
                    {"format": "simplesigning", "error": "sidecar tag already exists with other content"},
                ],
                "platform": "any",
                "signer": "file",
                "certificate_identity": "",
                "certificate_oidc_issuer": "",
                "key_backend": "file",
                "transparency_log_index": None,
            }
        )
    )

    landed, failed = report.legs
    assert (landed.format, landed.manifest_digest, landed.error) == ("bundle", "sha256:m", None)
    assert failed.format == "simplesigning"
    assert failed.error == "sidecar tag already exists with other content"
    assert failed.manifest_digest is None


def test_verification_report_from_recorded_fixture():
    """C-012: what `verify` found, and the four optionals it left out (D7).

    The entry's `referrer_digest` is asserted equal to the digest `sign`
    reported writing — same object, two commands — so the assertion fails if
    the parser reads the report-level digest into the entry.
    """
    report = VerificationReport.from_json(load("verify.json"))

    assert report.subject_digest == WP4_INDEX
    assert report.referrer_digest == WP4_SIG_REFERRER
    assert report.certificate_identity == ""
    assert report.certificate_oidc_issuer == ""
    assert report.signed_at == ""
    (entry,) = report.signatures
    assert entry.signature_format == "bundle"
    assert entry.discovery_method == "referrers_api"
    assert entry.key_backend == "file"
    assert entry.referrer_digest == WP4_SIG_REFERRER
    assert entry.certificate_identity is None
    assert entry.certificate_oidc_issuer is None
    assert entry.signed_at is None
    assert entry.rekor_log_index is None


def test_signature_entry_reads_rekor_log_index_not_transparency_log_index():
    """C-012's named trap: same concept, two wire names.

    `SignatureReport`/`AttestationReport` spell it `transparency_log_index`;
    a `verify` entry spells it `rekor_log_index`. The payload carries **both**
    with different values, so a parser that copied either struct's handling
    onto the other reads 999 and fails here — where a payload carrying only
    the right key would let the wrong parser pass with `None`.
    """
    report = VerificationReport.from_json(
        _enveloped(
            {**_VERIFY_HEAD, "signatures": [{**_SIGNATURE_ENTRY, "rekor_log_index": 7, "transparency_log_index": 999}]}
        )
    )

    (entry,) = report.signatures
    assert entry.rekor_log_index == 7


def test_verification_report_without_signatures_reads_an_empty_tuple():
    """Doc-derived: `signatures` is `skip_serializing_if = "Vec::is_empty"` (D7)."""
    report = VerificationReport.from_json(_enveloped(_VERIFY_HEAD))

    assert report.signatures == ()


def test_attestation_report_from_recorded_fixture():
    """C-013: flat, thirteen fields, `transparency_log_index` present and null.

    `predicate_type` is the **resolved URI**, not the `cyclonedx` alias the
    capture passed to `--type` — asserted as the URI so an SDK that echoed the
    caller's spelling back would fail.
    """
    report = AttestationReport.from_json(load("attest.json"))

    assert report.identifier == WP4_HELLO
    assert report.platform == "any"
    assert report.subject_digest == WP4_INDEX
    assert report.predicate_type == CYCLONEDX
    assert report.signed is True
    assert report.transparency_log_index is None
    assert report.bundle_digest == "sha256:fb0cee218c18d122dbb44f33760224b330b8fc2ffea65963e422e8c781118eac"
    assert report.referrer_digest == WP4_ATT_REFERRER
    assert report.sidecar_digest is None
    assert report.key_backend == "file"
    assert report.public_key_hint == KEY_HINT
    # `""`, not `None`: key-mode signing emits the keys empty rather than
    # dropping them, so this reads a value a renamed key could not produce.
    assert report.certificate_identity == ""
    assert report.certificate_oidc_issuer == ""


def test_sweep_report_carries_one_row_per_tag_with_its_own_report():
    """S-008 + C-017: `sign --tags` returns per-tag rows.

    Each row's nested identifier is asserted against **its own** tag, so a
    parser that reused the first row's report for every tag fails rather than
    returning a plausible-looking sweep.
    """
    report = SweepReport.from_json(load("sweep.json"), SignatureReport.from_dict)

    assert [(row.tag, row.status) for row in report.tags] == [("1.0.0", "completed"), ("2.0.0", "completed")]
    signed = [row.report for row in report.tags]
    # The `isinstance` filter is the assertion, not a formality: a row whose
    # report went missing or came back as the wrong type shortens the list.
    assert [row.identifier for row in signed if isinstance(row, SignatureReport)] == [
        WP4_HELLO,
        "127.0.0.1:5198/wp4/hello:2.0.0",
    ]
    assert all(row.kind is None and row.message is None for row in report.tags)


def test_sweep_report_serves_attest_through_the_same_row_parser():
    """C-017's seam: one `SweepReport`, two row types.

    The rows come back as `AttestationReport`, which is what proves the row
    parser decides the type — nothing in the wire row says which command
    produced it.
    """
    report = SweepReport.from_json(load("sweep_attest.json"), AttestationReport.from_dict)

    assert [row.tag for row in report.tags] == ["1.0.0", "2.0.0"]
    attested = [row.report for row in report.tags]
    assert [row.predicate_type for row in attested if isinstance(row, AttestationReport)] == [CYCLONEDX, CYCLONEDX]


def test_sweep_row_that_failed_carries_its_kind_and_message():
    """C-017: `kind` is the frozen slug to branch on, `message` is prose.

    Captured live: a sweep over one real tag and one that was never pushed.
    The failed row carries no `report` at all — absent, not null — and the
    completed row beside it still does.
    """
    report = SweepReport.from_json(load("partial/sweep_failed.json"), SignatureReport.from_dict)

    completed, failed = report.tags
    assert completed.status == "completed"
    assert completed.report is not None
    assert failed.tag == "does-not-exist"
    assert failed.status == "failed"
    assert failed.kind == "target_not_found"
    assert failed.message == "127.0.0.1:5198/wp4/hello:does-not-exist: no manifest for platform any"
    assert failed.report is None


def test_push_result_reports_per_platform_signing_and_the_attestation():
    """S-004 + C-005/C-018/C-019: a signed push, captured live.

    The signature row's subject is the **platform** manifest, not the index
    the push reported — `--sign` covers the platform manifest whose digest is
    final, so asserting it against `platform_digests` is what separates a
    correct parser from one that reached for `manifest_digest`.
    """
    result = PushResult.from_json(load("push_signed.json"))

    assert result.platform_digests == {"linux/amd64": WP4_PLATFORM}
    (row,) = result.signatures
    assert row.platform == "linux/amd64"
    assert row.status == "completed"
    assert row.kind is None
    assert row.message is None
    assert row.report is not None
    assert row.report.subject_digest == WP4_PLATFORM
    assert (
        row.report.legs[0].manifest_digest == "sha256:6bab2912a73a0ba95bc8ef61b1a7cf5ed667dcf605e8849bb85faf8d4d643d42"
    )
    assert result.attestation is not None
    assert result.attestation.status == "succeeded"
    assert result.attestation.predicate_type == CYCLONEDX
    assert result.attestation.signed is True
    assert (
        result.attestation.referrer_digest == "sha256:7386e70923f5479ab43ea3f71404488d611d66910dafa23689e93041b9bbdd08"
    )
    assert result.attestation.sidecar_digest is None


def test_push_result_of_an_unsigned_push_reports_neither():
    """C-005/D7: both keys are absent-when-unused, so absent must read as nothing.

    The recorded unsigned push is the same fixture the pre-0.6 contract used;
    reading `signatures` out of it must not invent a row.
    """
    result = PushResult.from_json(load("push.json"))

    assert result.signatures == ()
    assert result.attestation is None


def test_signed_platform_row_that_failed_names_the_platform_that_died():
    """Doc-derived (C-018, D10): a push that lands and then fails to sign.

    Not capturable here — every registry the capture could reach accepted the
    signature. The landed platform is kept beside the failed one for the same
    reason a sweep keeps its completed rows.
    """
    result = PushResult.from_json(
        _bare(
            {
                **_PUSH_HEAD,
                "signatures": [
                    {"platform": "linux/amd64", "status": "completed", "report": {**_SIGN_REPORT}},
                    {
                        "platform": "linux/arm64",
                        "status": "failed",
                        "kind": "referrers_unsupported",
                        "message": "registry implements neither the Referrers API nor a tag index",
                    },
                ],
            }
        )
    )

    landed, failed = result.signatures
    assert landed.platform == "linux/amd64"
    assert landed.report is not None
    assert failed.platform == "linux/arm64"
    assert failed.status == "failed"
    assert failed.kind == "referrers_unsupported"
    assert failed.message == "registry implements neither the Referrers API nor a tag index"
    assert failed.report is None


def test_attestation_outcome_that_failed_carries_kind_and_message_not_digests():
    """Doc-derived (C-019): the `failed` arm of an internally-tagged enum.

    Typing this as a bare `str` (D8's usual treatment) would have dropped
    every field but `status`, so the asserted fields are exactly the ones that
    would vanish.
    """
    result = PushResult.from_json(
        _bare(
            {
                **_PUSH_HEAD,
                "attestation": {"status": "failed", "kind": "rekor_unavailable", "message": "Rekor timed out"},
            }
        )
    )

    assert result.attestation is not None
    assert result.attestation.status == "failed"
    assert result.attestation.kind == "rekor_unavailable"
    assert result.attestation.message == "Rekor timed out"
    assert result.attestation.referrer_digest is None
    assert result.attestation.predicate_type is None
    assert result.attestation.signed is None


def test_attestation_outcome_carries_an_unsigned_attestation_as_false():
    """`signed: false` means nothing vouches for the document — carry it, don't lose it.

    Read with `.get`, a missing key answered `None` instead of raising, so the
    field failed open: a caller checking `signed is False` saw neither the
    claim nor an error. `push.rs:190` writes it unconditionally under
    `succeeded`, so `_need` is what turns a dropped key into a refusal.
    """
    result = PushResult.from_json(
        _bare(
            {
                **_PUSH_HEAD,
                "attestation": {"status": "succeeded", "predicate_type": CYCLONEDX, "signed": False},
            }
        )
    )

    assert result.attestation is not None
    assert result.attestation.signed is False
    assert result.attestation.predicate_type == CYCLONEDX


def test_attestation_outcome_refuses_a_status_it_does_not_know():
    """A third variant would carry fields this SDK drops — say so, don't guess.

    Unreachable against 0.6.0's closed two-variant enum. Without the guard the
    `failed` arm swallows it and the caller sees `has no 'kind' field`, which
    names neither the status nor the real problem.
    """
    with pytest.raises(ValueError, match="attestation status 'rolled_back'"):
        PushResult.from_json(_bare({**_PUSH_HEAD, "attestation": {"status": "rolled_back"}}))


def test_sbom_listing_from_recorded_fixture():
    """C-014: the listing, and `SbomEntry.summary` absent without `--summary`.

    `shadowed` is asserted `is False`, not merely falsy: it is always present
    upstream and `false` is a true claim (nothing supersedes this document),
    so `None` from a missing key would be a different fact. The key is present
    in this fixture, so `is False` alone cannot tell a required read from an
    optional one — `shadowed`'s missing-field row is what does.
    """
    report = SbomListingReport.from_json(load("sbom.json"))

    assert report.summary.status == "success"
    assert report.summary.verification == "verified"
    assert (report.summary.total, report.summary.verified, report.summary.unverified) == (1, 1, 0)
    assert report.summary.refused == 0
    assert report.summary.exit_code == 0
    assert report.refused == ()
    (entry,) = report.entries
    assert entry.predicate_type == CYCLONEDX
    assert entry.verified is True
    assert entry.shadowed is False
    assert entry.subject_digest == WP4_INDEX
    assert entry.referrer_digest == WP4_ATT_REFERRER
    assert entry.summary is None
    assert entry.certificate_identity is None
    assert entry.signed_at is None


def test_sbom_entry_summary_populates_only_under_the_summary_flag():
    """C-014: `--summary` fills a **per-entry** field, not a second root shape."""
    report = SbomListingReport.from_json(load("sbom_summary.json"))

    (entry,) = report.entries
    assert entry.summary is not None
    assert entry.summary.spec_version == "1.5"
    assert entry.summary.component_count == 2
    assert entry.summary.serial_number == "urn:uuid:3e671687-395b-41f5-a30f-a58921a69b79"
    assert entry.summary.top_level_component == "hello"


def test_sbom_summary_optionals_are_absent_when_the_document_omits_them():
    """C-014/D7: a CycloneDX document with no serial number and no root component."""
    report = SbomListingReport.from_json(load("sbom_minimal.json"))

    (entry,) = report.entries
    assert entry.summary is not None
    assert entry.summary.spec_version == "1.6"
    assert entry.summary.component_count == 1
    assert entry.summary.serial_number is None
    assert entry.summary.top_level_component is None


def test_sbom_listing_refuses_a_candidate_without_failing():
    """S-007: refusals stay exit 0 — the caller must read `summary.status`.

    `reason_kind` is the frozen slug to branch on and `reason` is prose free
    to be reworded (PKG-25), so they are asserted to differ: a parser that
    read one key into both fields would otherwise pass.
    """
    report = SbomListingReport.from_json(load("sbom_refused.json"))

    assert report.summary.status == "partial_failure"
    assert report.summary.exit_code == 0
    assert report.summary.verification == "unverified"
    assert (report.summary.total, report.summary.refused) == (1, 1)
    assert report.entries == ()
    (refused,) = report.refused
    assert refused.reason_kind == "sbom_summary_failed"
    assert refused.referrer_digest == "sha256:78c2f8b24a171fb0643b312516d39a6ca91b0de062d2c937776465811f7580e5"
    assert refused.reason.startswith("cannot summarize the https://cyclonedx.org/bom predicate")


def test_copy_report_from_recorded_fixture():
    """C-015: bare at the root (D11), and `blobs` is copy's shape, not push's.

    `blobs.present` is the field push's `LayerCounts` does not have and copy's
    `BlobSummary` does — asserting its value is what fails a parser that
    reused push's `{mounted, uploaded, verified}` shape here.
    """
    report = CopyReport.from_json(load("copy.json"))

    assert report.source == WP4_HELLO
    assert report.target == "127.0.0.1:5198/wp4/copy:1.0.0"
    assert report.status == "copied"
    assert report.cascade_tags_written == ()
    assert report.keep_tags_written == (f"__ocx.keep.{WP4_PLATFORM.replace(':', '-')}",)
    assert report.referrers_copied == 0
    assert report.sidecars_copied == 0
    assert report.sidecar_conflicts == ()
    assert report.blobs.present == 2
    assert report.blobs.mounted == 0
    assert report.blobs.uploaded == 0
    assert report.description is None
    (row,) = report.platforms
    assert row.platform == "linux/amd64"
    assert row.digest == WP4_PLATFORM
    assert row.disposition == "added"


def test_copy_report_description_is_an_outcome_not_the_description_text():
    """C-015: `description` carries a `DescriptionOutcome` value.

    The copied package's description reads "A throwaway WP4 fixture package.";
    the field reads `copied`. Asserting the outcome pins that the SDK is not
    handing back prose a caller might display.
    """
    report = CopyReport.from_json(load("copy_described.json"))

    assert report.description == "copied"
    assert report.target == "127.0.0.1:5198/wp4/described:1.0.0"


# --- partial-failure recovery (D10, S-009) ----------------------------------


def _failed(stdout: str, exit_code: int = 79) -> OcxProcessError:
    """The exception a non-zero exit raises, carrying stdout as `_exit_error` sets it."""
    return OcxProcessError(exit_code, ("ocx", "package", "sign"), "boom", stdout=stdout)


def test_partial_report_recovers_an_enveloped_report():
    """S-009: a partially-failed sweep surrenders its rows.

    The recovered string goes through the **same** `from_json` the success
    path uses, which is the property D10 asks for — so it must come back
    enveloped, not pre-unwrapped.
    """
    recovered = partial_report(_failed(load("partial/sweep_failed.json")))

    assert recovered is not None
    report = SweepReport.from_json(recovered, SignatureReport.from_dict)
    assert [row.status for row in report.tags] == ["completed", "failed"]


def test_partial_report_recovers_a_bare_report():
    """S-009 + C-015: `copy` and `push` write their report at the root (D11)."""
    recovered = partial_report(_failed(load("copy.json"), exit_code=65))

    assert recovered is not None
    assert CopyReport.from_json(recovered).target == "127.0.0.1:5198/wp4/copy:1.0.0"


def test_partial_report_answers_none_for_a_hard_failure():
    """S-009's first error case: an error envelope is never a report.

    Captured live from `verify` against a reference that does not exist. The
    `error` branch is tested before the bare fall-through, so this must not
    come back as a document — a caller would otherwise parse the failure's own
    message as if a command had reported it.
    """
    assert partial_report(_failed(load("partial/error_envelope.json"))) is None


@pytest.mark.parametrize(
    "stdout",
    [
        pytest.param("", id="no-stdout"),
        pytest.param("   \n", id="blank-stdout"),
        pytest.param("error: something went wrong\n", id="plain-text"),
        pytest.param("[1, 2]", id="json-array"),
        pytest.param('"a string"', id="json-scalar"),
    ],
)
def test_partial_report_answers_none_when_there_is_nothing_to_parse(stdout):
    """S-009: unparseable or absent stdout answers `None` rather than raising.

    A recovery helper that raised would turn "there was no report" into a
    second failure on top of the one the caller is already handling.
    """
    assert partial_report(_failed(stdout)) is None


# --- optional wire keys, read against a populated value ----------------------
#
# An optional key asserted only as absent is an unread key: rename it upstream
# and `data.get` answers `None` either way. Each test below is the doc-derived
# payload that makes one struct's optionals observable — built from the serde
# definitions at `v0.6.0`, because no hermetic run can produce a keyless
# Fulcio identity or a sidecar conflict.


def test_verification_report_carries_the_certificate_optionals_of_a_keyless_signature():
    """C-012/D7: the four absent-when-none fields, populated.

    Keyless verification is what fills them, and it needs a live Fulcio the
    capture had no access to — so this is doc-derived from
    `verification.rs:50-85`.
    """
    report = VerificationReport.from_json(
        _enveloped(
            {
                **_VERIFY_HEAD,
                "signatures": [
                    {
                        **_SIGNATURE_ENTRY,
                        "certificate_identity": KEYLESS_IDENTITY,
                        "certificate_oidc_issuer": KEYLESS_ISSUER,
                        "signed_at": "2026-08-31T20:59:33Z",
                        "rekor_log_index": 421337,
                    }
                ],
            }
        )
    )

    (entry,) = report.signatures
    assert entry.certificate_identity == KEYLESS_IDENTITY
    assert entry.certificate_oidc_issuer == KEYLESS_ISSUER
    assert entry.signed_at == "2026-08-31T20:59:33Z"
    assert entry.rekor_log_index == 421337


def test_sbom_entry_carries_the_certificate_optionals_of_a_verified_keyless_sbom():
    """C-014/D7: `sbom`'s three absent-when-none fields, populated.

    Same reason as the verify entry above — `sbom.rs:151-158` fills these
    only when the listed document carries a keyless signature.
    """
    report = SbomListingReport.from_json(
        _enveloped(
            {
                **_SBOM_HEAD,
                "entries": [
                    {
                        **_SBOM_ENTRY,
                        "certificate_identity": KEYLESS_IDENTITY,
                        "certificate_oidc_issuer": KEYLESS_ISSUER,
                        "signed_at": "2026-08-31T20:59:33Z",
                    }
                ],
            }
        )
    )

    (entry,) = report.entries
    assert entry.certificate_identity == KEYLESS_IDENTITY
    assert entry.certificate_oidc_issuer == KEYLESS_ISSUER
    assert entry.signed_at == "2026-08-31T20:59:33Z"


def test_attestation_report_carries_both_digests_under_signature_format_both():
    """C-013: `--signature-format both` writes a referrer *and* a sidecar.

    The live capture used the default `bundle`, which writes no sidecar, so
    `sidecar_digest` is absent there. Asserting the two digests differ is what
    separates reading both keys from reading one twice.
    """
    report = AttestationReport.from_json(
        _enveloped({**_ATTEST_REPORT, "referrer_digest": "sha256:ref", "sidecar_digest": "sha256:side"})
    )

    assert report.referrer_digest == "sha256:ref"
    assert report.sidecar_digest == "sha256:side"


def test_attestation_outcome_carries_both_digests_on_success():
    """C-019: same two addresses, in `push`'s own attestation outcome.

    `push.rs:177-185` — one vocabulary for both reports, so both keys must be
    read here too.
    """
    result = PushResult.from_json(
        _bare(
            {
                **_PUSH_HEAD,
                "attestation": {
                    "status": "succeeded",
                    "referrer_digest": "sha256:ref",
                    "sidecar_digest": "sha256:side",
                    "predicate_type": CYCLONEDX,
                    "signed": True,
                },
            }
        )
    )

    assert result.attestation is not None
    assert result.attestation.referrer_digest == "sha256:ref"
    assert result.attestation.sidecar_digest == "sha256:side"


def test_partial_report_hands_back_a_copy_refused_on_a_sidecar_conflict():
    """C-015 + S-009: the conflict list is readable from the failure itself.

    Exit 65, and the report is bare (D11), so `partial_report` returns it
    verbatim and `CopyReport.from_json` parses it. Doc-derived: four
    arrangements against a live registry — including a hand-planted divergent
    sidecar tag — all copied cleanly, so the refusal could not be captured.
    """
    conflict = "sha256-3ca7e955071fb4053711b99239a34030c2c805226b348ef51a1d6c81d4835088.sig"

    recovered = partial_report(_failed(_bare({**_COPY_HEAD, "sidecar_conflicts": [conflict]}), exit_code=65))

    assert recovered is not None
    assert CopyReport.from_json(recovered).sidecar_conflicts == (conflict,)
