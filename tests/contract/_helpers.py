# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The OCX Authors

"""Small shared pieces for the contract tier."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

SMOKE_PACKAGE: Final = "ocx.sh/go-task/task:3"
"""The tiny published package the T1 smoke resolves, installs, and executes."""


def project_file(root: Path) -> Path:
    """Return the path a `Project` handle must be built from.

    ocx's `--project` takes the `ocx.toml` **file** (`--project <FILE>`, the
    shape Cargo's `--manifest-path` has), not the directory holding it.
    `Ocx.project` documents a directory and forwards whatever it was given
    verbatim, so a directory reaches the binary and exits 74. The contract
    fixtures therefore hand over the file, and
    `test_project_handle_accepts_a_directory` is the row that tracks the gap.
    """
    return root / "ocx.toml"


def child_env(stdout: str) -> Mapping[str, str]:
    """Parse a captured `printenv` run into a mapping.

    Splits on the first `=` and drops continuation lines, so a value with an
    embedded newline is truncated rather than mis-attributed to the next key.
    Contract projects declare single-line values, which keeps that harmless.
    """
    return dict(line.split("=", 1) for line in stdout.splitlines() if "=" in line)
