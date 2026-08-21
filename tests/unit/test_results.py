# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The OCX Authors

"""Contract tests for `ocx_sdk._results` (C-003, S-002).

Every parser has one **recorded-fixture** test: it parses a file in
`tests/fixtures/results/` that came out of a real ocx 0.5.8 run and asserts the
field values that file actually holds. Those fixtures are the specification —
if a parser disagrees with one, the parser is wrong.

Provenance of `tests/fixtures/results/*.json`:

- Most are the WP00 captures in `tests/fixtures/cli/`, with the ocx tracing
  lines stripped from the head. WP00 recorded stdout and stderr merged; the SDK
  reads them as separate pipes, so a `results/` fixture is the stdout half.
- `version`, `login`, `logout`, `config_update`, and `config_setup` had no WP00
  capture and were probed live against the same ocx 0.5.8 binary under an
  isolated `OCX_HOME`/`DOCKER_CONFIG`.

A handful of shapes could not be recorded because no probe produced one: an
`ocx env` advisory or integration, a failing `package test`, a broken lock, a
configured managed-config tier. Those tests are marked *doc-derived* and build
their payload from the shape the design and the CLI contracts research record.
They are the parts of this module most likely to need revisiting.
"""

from __future__ import annotations

import inspect
import json
import os
from pathlib import Path

import pytest

from ocx_sdk import _results
from ocx_sdk._results import (
    AboutInfo,
    Advisory,
    ConfigSetupReport,
    ConfigUpdateReport,
    DepsReport,
    EnvEntry,
    EnvReport,
    InspectReport,
    InstallReport,
    LoginResult,
    LogoutResult,
    PullReport,
    PushResult,
    StatusReport,
    TestResult,
    VersionInfo,
    parse_info,
    parse_package_pull,
    parse_removals,
    parse_tool_rows,
    parse_which,
)
from ocx_sdk._types import HostEnv, PackageRef

FIXTURES = Path(__file__).parent.parent / "fixtures" / "results"

TASK_DIGEST = "sha256:df420d1c99d7e80ce6366104af4e6b57135e95940763e235c907525c438effee"
TASK_PINNED = f"ocx.sh/go-task/task:3@{TASK_DIGEST}"
HELLO_IDENTIFIER = "127.0.0.1:5099/wp00/hello:1.0.0"
HELLO_DIGEST = "70e90fbac7643ea0daa7c692e593c3cc951316d30d22e017af39d51a9d294d4e"


def load(name: str) -> str:
    """Return a recorded stdout fixture verbatim."""
    return (FIXTURES / name).read_text(encoding="utf-8")


# --- version / about --------------------------------------------------------


def test_version_info_from_recorded_fixture():
    info = VersionInfo.from_json(load("version.json"))

    assert info.version == "0.5.8"
    assert info.commit["describe"] == "v0.5.8"
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

    assert about.version == "0.5.8"
    assert about.registry == "ocx.sh"
    assert about.home == "/tmp/wp00-home"
    assert about.shell == "Zsh"
    assert about.platforms == ("linux/amd64",)
    assert about.libc == ("libc.glibc",)
    assert about.channel is None
    assert about.commit["sha"] == "b8b5658d11dfb05fff8ffdce6b7cfa5f0d5cc2bf"


# --- status -----------------------------------------------------------------


def test_status_report_from_recorded_fixture():
    status = StatusReport.from_json(load("status.json"))

    assert status.project == "/tmp/wp00-prj/ocx.toml"
    assert status.lock.present is True
    assert status.lock.current is True
    assert status.lock.lock_version == 3
    assert status.lock.declaration_hash == status.lock.declaration_hash_expected
    assert status.lock.generated_by == "ocx 0.5.8"
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
        json.dumps(
            {
                "project": "/p/ocx.toml",
                "groups": {
                    "default": {
                        "tools": {
                            "declared_only": {"declared": "ocx.sh/a/b:1"},
                            "lock_only": {"platforms": {"linux/amd64": TASK_DIGEST}},
                        }
                    }
                },
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
        json.dumps({"project": "/p/ocx.toml", "lock": {"present": True, "current": False, "error": "checksum drift"}})
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
        json.dumps(
            {
                "entries": [],
                "integrations": [{"namespace": "vscode", "package": TASK_PINNED, "payload": {"tasks": 1}}],
                "advisories": [
                    {"kind": "shadowed", "package": TASK_PINNED, "key": "PATH", "message": "shadowed by /usr/bin"}
                ],
            }
        )
    )

    (integration,) = report.integrations
    assert integration.namespace == "vscode"
    assert integration.payload["tasks"] == 1
    assert str(integration.ref) == TASK_PINNED

    (advisory,) = report.advisories
    assert advisory == Advisory(kind="shadowed", package=TASK_PINNED, key="PATH", message="shadowed by /usr/bin")
    assert str(advisory.ref) == TASK_PINNED


def test_env_entry_rejects_a_type_it_cannot_fold():
    """Fail closed: a silently mis-folded variable is worse than a refusal."""
    with pytest.raises(ValueError, match="secret"):
        EnvReport.from_json('{"entries": [{"key": "K", "type": "secret", "value": "v"}]}')


def test_env_compose_maps_each_entry_type_onto_the_merge():
    report = EnvReport.from_json(
        json.dumps(
            {
                "entries": [
                    {"key": "TOOL", "type": "constant", "value": "task"},
                    {"key": "PATH", "type": "path", "value": "/pkg/bin"},
                    {"key": "FLAGS", "type": "list", "value": "-v"},
                ]
            }
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
    """S-002: a report produced hermetically composes hermetically."""
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
    assert installed.path.endswith("/candidates/3")
    assert installed.metadata["binaries"] == ["task"]
    assert str(installed.ref) == TASK_PINNED
    assert installed.ref.metadata["type"] == "bundle"


def test_removals_from_recorded_fixture():
    (removal,) = parse_removals(load("uninstall.json"))

    assert removal.package == "ocx.sh/go-task/task:3"
    assert removal.status == "removed"
    assert removal.path.endswith("/candidates/3")
    assert str(removal.ref) == "ocx.sh/go-task/task:3"


def test_info_passes_through_a_null_description():
    results = parse_info(load("info.json"))

    assert results == {"ocx.sh/go-task/task:3": None}


def test_info_passes_through_a_populated_description():
    """Doc-derived: no package reachable in the probe carried description metadata."""
    results = parse_info('{"ocx.sh/a/b:1": {"description": "hi", "readme": "# hi"}}')

    entry = results["ocx.sh/a/b:1"]
    assert entry is not None
    assert entry["description"] == "hi"


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
    assert result.run.stderr == "boom"


def test_push_result_from_recorded_fixture():
    result = PushResult.from_json(load("push.json"))

    assert result.identifier == HELLO_IDENTIFIER
    assert result.status == "pushed"
    assert result.manifest_digest.startswith("sha256:")
    assert result.cascade_tags_written == ()
    assert result.canonical_tags_written == (f"sha256.{HELLO_DIGEST}",)
    assert result.layers == {"mounted": 0, "uploaded": 1, "verified": 0}
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

PARSERS = {
    "version": VersionInfo.from_json,
    "about": AboutInfo.from_json,
    "status": StatusReport.from_json,
    "inspect": InspectReport.from_json,
    "env": EnvReport.from_json,
    "pull": PullReport.from_json,
    "package install": InstallReport.from_json,
    "package deps": DepsReport.from_json,
    "package test": TestResult.from_json,
    "package push": PushResult.from_json,
    "login": LoginResult.from_json,
    "logout": LogoutResult.from_json,
    "config update": ConfigUpdateReport.from_json,
    "config setup": ConfigSetupReport.from_json,
    "package which": parse_which,
    "package info": parse_info,
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
    ],
)
def test_a_payload_of_the_wrong_shape_is_refused(parse, wrong):
    with pytest.raises(ValueError, match="expected a JSON"):
        parse(wrong)


_MISSING_FIELD_CASES = [
    # (struct whose `_need` fires, parser to call, payload, the key it wants)
    ("VersionInfo", VersionInfo.from_json, "{}", "version"),
    ("AboutInfo", AboutInfo.from_json, '{"version": "1"}', "registry"),
    ("StatusReport", StatusReport.from_json, "{}", "project"),
    (
        "Candidate",
        InspectReport.from_json,
        '{"packages": [{"name": "n", "identifier": "i", "candidates": [{"pinned": "p", "platform": "x"}]}]}',
        "digest",
    ),
    ("InspectedPackage", InspectReport.from_json, '{"packages": [{"name": "n"}]}', "identifier"),
    ("EnvEntry", EnvReport.from_json, '{"entries": [{"key": "K", "type": "constant"}]}', "value"),
    ("PackageBinding", EnvReport.from_json, '{"binaries": [{"name": "task"}]}', "package"),
    ("Integration", EnvReport.from_json, '{"integrations": [{"namespace": "vscode"}]}', "package"),
    (
        "Advisory",
        EnvReport.from_json,
        '{"advisories": [{"kind": "shadowed", "package": "p", "key": "PATH"}]}',
        "message",
    ),
    ("WhichResult", parse_which, '{"a": {"path": "/p"}}', "kind"),
    ("InstalledPackage", InstallReport.from_json, '{"a": {"identifier": "i"}}', "path"),
    ("RemovalResult", parse_removals, '[{"package": "p", "status": "removed"}]', "path"),
    ("DepNode", DepsReport.from_json, '{"roots": [{}]}', "identifier"),
    ("DepsReport", DepsReport.from_json, "{}", "roots"),
    ("ToolRow", parse_tool_rows, '[{"binding": "task", "group": "default"}]', "digest"),
    ("Assertion", TestResult.from_json, '{"status": "failed", "assertion": {"kind": "exit_code"}}', "message"),
    ("TestResult", TestResult.from_json, "{}", "status"),
    ("PushResult", PushResult.from_json, '{"identifier": "i", "status": "pushed"}', "manifest_digest"),
    ("LoginResult", LoginResult.from_json, '{"registry": "r"}', "username"),
    ("LogoutResult", LogoutResult.from_json, "{}", "registry"),
    ("ConfigUpdateReport", ConfigUpdateReport.from_json, "{}", "status"),
    ("ConfigSetupReport", ConfigSetupReport.from_json, "{}", "managed_config"),
]
"""One row per struct that declares a required field — see the sweep below."""


def _structs_with_required_fields() -> set[str]:
    """Return every `_results` struct whose parser calls `_need`."""
    return {
        name
        for name, member in vars(_results).items()
        if inspect.isclass(member) and member.__module__ == _results.__name__ and "_need(" in inspect.getsource(member)
    }


def test_every_struct_with_a_required_field_has_a_missing_field_row():
    """A struct that gains a required field must gain a row here with it."""
    assert {name for name, *_ in _MISSING_FIELD_CASES} == _structs_with_required_fields()


@pytest.mark.parametrize(
    ("parse", "payload", "missing"),
    [pytest.param(parse, payload, missing, id=name) for name, parse, payload, missing in _MISSING_FIELD_CASES],
)
def test_a_missing_required_field_names_itself_and_the_command(parse, payload, missing):
    with pytest.raises(ValueError, match=f"no '{missing}' field") as caught:
        parse(payload)

    assert str(caught.value).startswith("`ocx "), "the message must name the command that produced the payload"


@pytest.mark.parametrize(
    ("parse", "payload", "expected"),
    [
        pytest.param(
            AboutInfo.from_json,
            '{"version": "1", "registry": "r", "home": "/h", "shell": "sh", "platforms": "linux/amd64"}',
            "a JSON array of strings",
            id="texts-shredded-a-bare-string",
        ),
        pytest.param(
            PushResult.from_json,
            '{"identifier": "i", "status": "s", "manifest_digest": "d", "cascade_tags_written": "v1"}',
            "a JSON array of strings",
            id="texts-on-push",
        ),
        pytest.param(InspectReport.from_json, '{"packages": "task"}', "a JSON array of objects", id="rows"),
        pytest.param(StatusReport.from_json, '{"project": "/p", "lock": "broken"}', "a JSON object", id="table"),
        pytest.param(
            InspectReport.from_json,
            '{"packages": [{"name": "n", "identifier": "i", "platform": "linux/amd64"}]}',
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
            '{"entries": [{"key": "K", "type": "constant", "value": "v", "brand_new": 1}]}',
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
        pytest.param(lambda: parse_info(load("info.json")), "ocx.sh/go-task/task:3", id="info"),
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
