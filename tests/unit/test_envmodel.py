# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The OCX Authors

"""Contract tests for `ocx_sdk._envmodel` (C-008, S-002).

Named rows from the design's mechanism matrix: `test_activate_diff_revert`
(absent-key delete, unrelated mutation survives, exception inside the block)
and the `test_env_merge_*` family pinning the ported ocx fold — const replaces,
path moves to front, list moves to back under one agreed separator, `${...}`
carried verbatim.

The oracle for every expectation here is ocx itself (`Env::apply_entries`,
`move_to_front`, `append_unique`, `reconcile_list_separators`); the contract
tier re-checks the same fold against `ocx run -- printenv`.
"""

from __future__ import annotations

import os

import pytest

from ocx_sdk import _envmodel
from ocx_sdk._envmodel import DEFAULT_LIST_SEPARATOR, ComposedEnv, merge
from ocx_sdk._types import ConstVar, ListVar, PathVar


@pytest.fixture(autouse=True)
def _owned_by_nobody(monkeypatch):
    """Start every test with `activate()` unowned, and never leak ownership out of one."""
    monkeypatch.setattr(_envmodel, "_active", None)


def _path(*segments: str) -> str:
    """Join segments the way the host's PATH does."""
    return os.pathsep.join(segments)


@pytest.mark.parametrize(
    "declared",
    [pytest.param("2.0", id="bare-str"), pytest.param(ConstVar("2.0"), id="constvar")],
)
def test_env_merge_const_replaces(declared):
    env = merge({"UV_VERSION": "1.0"}, [("UV_VERSION", declared)])

    assert env.mapping["UV_VERSION"] == "2.0"


def test_env_merge_path_prepends_to_an_existing_value():
    env = merge({"PATH": _path("/usr/bin", "/bin")}, [("PATH", PathVar("/opt/uv/bin"))])

    assert env.mapping["PATH"] == _path("/opt/uv/bin", "/usr/bin", "/bin")


def test_env_merge_path_creates_an_absent_key():
    env = merge({}, [("LD_LIBRARY_PATH", PathVar("/opt/uv/lib"))])

    assert env.mapping["LD_LIBRARY_PATH"] == "/opt/uv/lib"


def test_env_merge_path_moves_a_present_segment_to_the_front():
    env = merge({"PATH": _path("/usr/bin", "/opt/uv/bin", "", "/bin")}, [("PATH", PathVar("/opt/uv/bin"))])

    assert env.mapping["PATH"] == _path("/opt/uv/bin", "/usr/bin", "/bin")


def test_env_merge_path_with_an_empty_value_prepends_nothing():
    env = merge({"PATH": _path("/usr/bin", "", "/bin")}, [("PATH", PathVar(""))])

    assert env.mapping["PATH"] == _path("/usr/bin", "/bin")


def test_env_merge_list_appends_with_the_declared_separator():
    env = merge({"GODEBUG": "gctrace=1"}, [("GODEBUG", ListVar("madvdontneed=1", separator=","))])

    assert env.mapping["GODEBUG"] == "gctrace=1,madvdontneed=1"


def test_env_merge_list_moves_an_existing_contribution_to_the_back():
    env = merge({"JDK_JAVA_OPTIONS": "-ea -Xmx1g -ea"}, [("JDK_JAVA_OPTIONS", ListVar("-ea", separator=" "))])

    assert env.mapping["JDK_JAVA_OPTIONS"] == "-Xmx1g -ea"


def test_env_merge_list_collapses_to_the_bare_value_when_nothing_survives():
    env = merge({"GOFLAGS": "-mod=mod"}, [("GOFLAGS", ListVar("-mod=mod", separator=" "))])

    assert env.mapping["GOFLAGS"] == "-mod=mod"


def test_env_merge_list_defaults_to_a_space_separator():
    env = merge({"JDK_JAVA_OPTIONS": "-Xmx1g"}, [("JDK_JAVA_OPTIONS", ListVar("-ea"))])

    assert env.mapping["JDK_JAVA_OPTIONS"] == f"-Xmx1g{DEFAULT_LIST_SEPARATOR}-ea"


def test_env_merge_list_inherits_an_established_separator():
    entries = [("GODEBUG", ListVar("gctrace=1", separator=",")), ("GODEBUG", ListVar("madvdontneed=1"))]

    env = merge({}, entries)

    assert env.mapping["GODEBUG"] == "gctrace=1,madvdontneed=1"


def test_env_merge_list_separator_conflict_raises():
    entries = [
        ("GODEBUG", ListVar("gctrace=1", separator=",")),
        ("GODEBUG", ListVar("madvdontneed=1", separator=" ")),
    ]

    with pytest.raises(ValueError, match="conflicting separators"):
        merge({}, entries)


def test_empty_value_asymmetry_matches_ocx():
    # ocx's `add_list` returns early on an empty contribution, while `add_path`
    # inserts on an absent key before it ever looks at the value.
    env = merge({}, [("GOFLAGS", ListVar("", separator=" ")), ("TOOL_PATH", PathVar(""))])

    assert "GOFLAGS" not in env.mapping
    assert env.mapping["TOOL_PATH"] == ""


@pytest.mark.parametrize(
    "separator",
    [pytest.param("", id="empty"), pytest.param("=", id="equals"), pytest.param("\n", id="newline")],
)
def test_env_merge_list_rejects_an_unusable_separator(separator):
    with pytest.raises(ValueError, match="ocx cannot fold with"):
        merge({}, [("GOFLAGS", ListVar("-mod=mod", separator=separator))])


@pytest.mark.parametrize(
    "entries",
    [
        pytest.param([("JDK_JAVA_OPTIONS", ListVar(" -ea"))], id="leading-default-separator"),
        pytest.param([("GODEBUG", ListVar("gctrace=1,", separator=","))], id="trailing-declared-separator"),
        pytest.param(
            [("GODEBUG", ListVar("gctrace=1", separator=",")), ("GODEBUG", ListVar(",madvdontneed=1"))],
            id="leading-inherited-separator",
        ),
    ],
)
def test_env_merge_list_rejects_a_separator_edged_value(entries):
    with pytest.raises(ValueError, match="starts or ends with the separator"):
        merge({}, entries)


def test_env_merge_rejects_a_value_outside_the_grammar():
    with pytest.raises(TypeError, match="not an `\\[env\\]` value"):
        merge({}, [("JOBS", 4)])  # pyright: ignore[reportArgumentType]


def test_env_merge_keeps_interpolation_tokens_verbatim():
    entries = [("TOOL_HOME", "${installPath}"), ("TOOL_BIN", PathVar("${self.env.TOOL_HOME}/bin"))]

    env = merge({}, entries)

    assert env.mapping["TOOL_HOME"] == "${installPath}"
    assert env.mapping["TOOL_BIN"] == "${self.env.TOOL_HOME}/bin"


def test_env_merge_applies_entries_in_declaration_order():
    env = merge({}, [("UV_VERSION", "1.0"), ("UV_VERSION", ConstVar("2.0"))])

    assert env.mapping["UV_VERSION"] == "2.0"


def test_env_merge_leaves_the_base_untouched():
    base = {"PATH": "/usr/bin"}

    merge(base, [("PATH", PathVar("/opt/uv/bin"))])

    assert base == {"PATH": "/usr/bin"}


def test_composed_env_mapping_hands_back_a_copy():
    env = merge({"UV_VERSION": "1.0"}, [])

    env.mapping["UV_VERSION"] = "tampered"

    assert env.mapping["UV_VERSION"] == "1.0"


def test_composed_env_snapshots_the_values_it_was_given():
    values = {"UV_VERSION": "1.0"}

    env = ComposedEnv(values)
    values["UV_VERSION"] = "2.0"

    assert env.mapping == {"UV_VERSION": "1.0"}


def test_activate_applies_the_mapping_to_the_process(monkeypatch):
    monkeypatch.delenv("OCX_SDK_WP05_NEW", raising=False)
    env = merge({}, [("OCX_SDK_WP05_NEW", "during")])

    with env.activate():
        assert os.environ["OCX_SDK_WP05_NEW"] == "during"


def test_activate_diff_revert(monkeypatch):
    monkeypatch.setenv("OCX_SDK_WP05_PRIOR", "before")
    monkeypatch.delenv("OCX_SDK_WP05_NEW", raising=False)
    env = merge({}, [("OCX_SDK_WP05_PRIOR", "during"), ("OCX_SDK_WP05_NEW", "during")])

    with env.activate():
        monkeypatch.setenv("OCX_SDK_WP05_FOREIGN", "untouched")

    assert os.environ["OCX_SDK_WP05_PRIOR"] == "before"
    assert "OCX_SDK_WP05_NEW" not in os.environ
    assert os.environ["OCX_SDK_WP05_FOREIGN"] == "untouched"


def test_activate_diff_revert_when_the_block_raises(monkeypatch):
    monkeypatch.delenv("OCX_SDK_WP05_NEW", raising=False)
    env = merge({}, [("OCX_SDK_WP05_NEW", "during")])

    with pytest.raises(RuntimeError, match="boom"), env.activate():
        raise RuntimeError("boom")

    assert "OCX_SDK_WP05_NEW" not in os.environ
    assert _envmodel._active is None


def test_activate_refuses_a_second_owner(monkeypatch):
    monkeypatch.delenv("OCX_SDK_WP05_NEW", raising=False)
    env = merge({}, [("OCX_SDK_WP05_NEW", "during")])

    with env.activate(), pytest.raises(RuntimeError, match="already owns"), env.activate():
        pass


def test_activate_releases_ownership_after_its_block(monkeypatch):
    monkeypatch.delenv("OCX_SDK_WP05_NEW", raising=False)
    env = merge({}, [("OCX_SDK_WP05_NEW", "during")])

    with env.activate():
        pass

    with env.activate():
        assert os.environ["OCX_SDK_WP05_NEW"] == "during"


class _WatchingEnviron(dict):
    """An `os.environ` stand-in recording who owned `activate()` on each write."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.owners = []

    def __setitem__(self, key, value):
        self.owners.append(_envmodel._active)
        super().__setitem__(key, value)

    def pop(self, key, default=None):
        self.owners.append(_envmodel._active)
        return super().pop(key, default)


def test_activate_owns_the_environment_until_the_restore_finishes(monkeypatch):
    # Clearing ownership before the restore loop lets a second thread's
    # activate() succeed mid-revert, after which this block's restore
    # overwrites the values that thread just applied. Ownership must span
    # entry-snapshot → body → restore, so every restoring write still sees
    # this env as the owner.
    watcher = _WatchingEnviron({"OCX_SDK_WP05_PRIOR": "before"})
    monkeypatch.setattr(_envmodel.os, "environ", watcher)
    env = merge({}, [("OCX_SDK_WP05_PRIOR", "during"), ("OCX_SDK_WP05_NEW", "during")])

    with env.activate():
        watcher.owners.clear()  # keep only what the restore phase records

    assert watcher.owners == [env, env]
    assert _envmodel._active is None


def test_activate_releases_ownership_even_if_the_restore_raises(monkeypatch):
    # The restore now runs inside the ownership span, so a write that blows up
    # must not strand `_active` and lock out every later activate().
    class _Exploding(dict):
        def pop(self, key, default=None):
            raise OSError("environ write refused")

    monkeypatch.setattr(_envmodel.os, "environ", _Exploding())
    env = merge({}, [("OCX_SDK_WP05_NEW", "during")])

    with pytest.raises(OSError, match="environ write refused"), env.activate():
        pass

    assert _envmodel._active is None


def test_composed_env_repr_names_its_keys_without_showing_a_value():
    # A composed env carries whatever credentials its base held, and a repr is
    # exactly what a traceback or a pytest diff prints (CWE-532).
    env = merge({"OCX_AUTH_ghcr_io_TOKEN": "s3cret"}, [("PATH", PathVar("/opt/uv/bin"))])

    assert "OCX_AUTH_ghcr_io_TOKEN" in repr(env)
    assert "s3cret" not in repr(env)
    assert "/opt/uv/bin" not in repr(env)
