# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The OCX Authors

"""Doc-snippet testing (S-007): every code fence in `docs/**/*.md` and
`README.md`, and every code example inside a `src/**/*.py` docstring, runs as
a pytest item via Sybil.

`docs/**/*.md` + `README.md` get the full four-marker net (design §14) —
Sybil matches by exact fence language, so each is a distinct parser:

- ` ```python ` — runs unconditionally (unit tier).
- ` ```python-contract ` — needs a live, pinned ocx binary; skipped unless
  `OCX_SDK_CONTRACT=1`.
- ` ```python-acceptance ` — needs the compose stack; skipped unless
  `OCX_SDK_ACCEPTANCE=1`.
- ` ```python-no-run ` — compile-checked only (`ast.parse`), for snippets that
  reference unreachable infrastructure (a corporate mirror, a fictional path,
  a real network call) and carry a comment saying why.

`src/**/*.py` docstrings get a narrower net (WP09 review decision): a
` ```python ` fence there is illustrative API-doc content, not a runnable
recipe — it never needs a real binary, so it is compile-checked only
(`ast.parse`), the same evaluator the `python-no-run` marker above uses.
Runnable coverage for the flows those examples sketch lives in `docs/` pages
under `python-contract` markers instead. `>>>` doctest-style examples in a
docstring's `Example:` section still run for real, via Sybil's own
`DocTestParser` — not stdlib doctest's `--doctest-modules`, which would
double-collect the same blocks and is explicitly not enabled here.

A fence in an unrecognized language is simply not collected, so an unmarked
snippet that intends to be runnable is invisible to this net rather than
failing it.
"""

import ast
import os
from collections.abc import Callable

import pytest
from sybil import Sybil
from sybil.evaluators.python import PythonEvaluator
from sybil.parsers.doctest import DocTestParser
from sybil.parsers.markdown import CodeBlockParser, PythonCodeBlockParser


def _gated(env_var: str) -> Callable[[object], None]:
    """Build an evaluator that skips unless `env_var` is set to `"1"`."""
    run = PythonEvaluator()

    def evaluate(example: object) -> None:
        if os.environ.get(env_var) != "1":
            pytest.skip(f"set {env_var}=1 to run this example")
        run(example)

    return evaluate


def _compile_only(example: object) -> None:
    """Parse the example without running it — the `python-no-run` marker."""
    ast.parse(example.parsed, filename=example.path)


_DOC_PARSERS = [
    PythonCodeBlockParser(),
    DocTestParser(),
    CodeBlockParser("python-contract", _gated("OCX_SDK_CONTRACT")),
    CodeBlockParser("python-acceptance", _gated("OCX_SDK_ACCEPTANCE")),
    CodeBlockParser("python-no-run", _compile_only),
]

# src docstrings: >>> examples run for real, but a ```python fence is
# illustrative API-doc content (may reference a fictional path or need a
# real binary) — compile-check it instead of running it. No contract/
# acceptance/no-run markers here; there is nothing to gate when nothing
# in this instance ever executes a `python` fence.
_DOCSTRING_PARSERS = [DocTestParser(), CodeBlockParser("python", _compile_only)]

# Sybil's own docs say `patterns` matches like `fnmatch.fnmatch` (which treats
# `**` as spanning any number of path segments), but `Sybil.should_parse`
# actually calls `pathlib.Path.match()`, whose `**` only ever absorbs exactly
# one segment — so `"docs/**/*.md"` silently misses anything nested two or
# more levels under docs/ (e.g. docs/guide/concepts/*.md). `Path.match()`
# matches from the right, so a *prefix-free* pattern like `"**/*.md"` matches
# at any depth instead; it is scoped safely here because pytest only ever
# walks files under `testpaths` (tests, docs, README.md, src) in the first
# place, and README.md itself is handled separately via `filenames`.
docs = Sybil(parsers=_DOC_PARSERS, patterns=["**/*.md"], filenames=["README.md"])
docstrings = Sybil(parsers=_DOCSTRING_PARSERS, patterns=["**/*.py"])

pytest_collect_file = (docs + docstrings).pytest()
