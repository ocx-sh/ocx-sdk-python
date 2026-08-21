# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The OCX Authors

"""The `[env]` merge (contract C-008) — carve-out (a) of the wrapper rule.

`ocx env --format json` emits typed entries and leaves composing them to the
consumer, so the SDK folds them exactly once, here. The algorithm is ported
from ocx (`ocx_lib::env::Env::apply_entries`, `utility::path::move_to_front`,
`utility::list::append_unique`, `env::reconcile_list_separators`) and is pinned
by the printenv contract diff:

- a bare `str` or a `ConstVar` **replaces** the value,
- a `PathVar` **prepends** with move-to-front — every earlier occurrence of the
  same segment is dropped, so re-applying an entry never duplicates it,
- a `ListVar` **appends** with move-to-back under one separator per key: the
  first entry that spells a separator establishes the key's, a later entry
  inherits it by leaving `separator=None`, and a second, different explicit
  separator is a conflict rather than a silently mis-joined value. A separator
  ocx cannot fold with, and a value edged by the separator it ends up with,
  are refused for the same reason — both would fuse with the fold's own
  wrapper and quietly degrade the dedup.

Values are folded **verbatim**. `${...}` is ocx's grammar and ocx resolves it
before the entries reach us; interpolating here would invent values ocx never
produced (v0.1 decision, plan round).

Two deliberate limitations, both about how keys and segments are spelled rather
than about the fold: keys compare case-sensitively, where ocx folds case on
Windows; and `PATH` splits on `os.pathsep` alone, where ocx's `split_paths()`
additionally strips a Windows segment's surrounding double quotes.
"""

from __future__ import annotations

import os
import threading
from collections.abc import Generator, Iterable, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Final

from ._types import ConstVar, EnvValue, ListVar, PathVar

DEFAULT_LIST_SEPARATOR: Final = " "
"""Separator for a list key no entry declared one for — ocx's own default."""

_UNUSABLE_SEPARATOR_CHARS: Final = "=\n\r"
"""What a list separator may not contain — ocx's `separator_is_valid`, mirrored in `_env`."""

_REDACTED: Final = "***"
"""What a value shows as in `__repr__` — the same masking `SpawnEnv` applies."""

_active: ComposedEnv | None = None
"""The `ComposedEnv` currently owning `os.environ`, if any (see `activate`)."""

_ownership = threading.Lock()
"""Guards the `_active` check-and-set only — never held across an active block."""


@dataclass(frozen=True, slots=True)
class ComposedEnv:
    """A merged environment, ready for a child process.

    Build one with `merge()`; `EnvReport.compose()` is the caller-facing route.

    The values stay off every human-readable surface: a composed environment
    inherits whatever `OCX_AUTH_*` its base carried, and a `repr` is exactly
    what a traceback or a pytest diff prints (CWE-532). `__repr__` names the
    keys and masks every value, the same shape `SpawnEnv` uses.
    """

    _values: Mapping[str, str] = field(repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "_values", dict(self._values))

    def __repr__(self) -> str:
        return f"ComposedEnv(keys=[{', '.join(sorted(self._values))}], values={_REDACTED})"

    @property
    def mapping(self) -> dict[str, str]:
        """Return the merged environment as a fresh `dict`.

        The non-invasive form, and the documented default: pass it as
        `subprocess.run(..., env=...)` instead of mutating the process.
        """
        return dict(self._values)

    @contextmanager
    def activate(self) -> Generator[None]:
        """Apply this environment to `os.environ` for the duration of the block.

        Process-global and single-owner: it changes what every thread and every
        subprocess sees, and a second, overlapping `activate()` raises instead
        of nesting. Concurrent code passes `mapping` to the subprocess call.

        The revert is diff-based — only the keys this environment sets are
        recorded on entry and restored on exit, keys that were absent are
        deleted again, and unrelated changes made inside the block survive.
        It runs in `finally`; `os._exit`, `execve`, and SIGKILL leave no chance
        to run it at all.

        Raises:
            RuntimeError: Another `activate()` block already owns `os.environ`.
        """
        global _active
        # Ownership spans the entry snapshot, the body, and the restore. The
        # lock itself covers only the check-and-set and the release, never any
        # of that: holding it across the body would serialize callers and fake
        # a nesting safety the process-global contract deliberately does not
        # offer.
        with _ownership:
            if _active is not None:
                raise RuntimeError(
                    "another ComposedEnv.activate() block already owns os.environ; activate() is "
                    "process-global and does not nest — pass `.mapping` to the subprocess call instead."
                )
            _active = self
        # Read before writing: the prior state of exactly the keys about to
        # change, with `None` standing for "was not set" (an env value never is).
        restore = {key: os.environ.get(key) for key in self._values}
        try:
            os.environ.update(self._values)
            yield
        finally:
            # Ownership is released only once the environment is back the way
            # it was found. Clearing it first would let a waiting thread's
            # activate() succeed mid-revert, and this restore would then
            # overwrite the values that thread had just applied.
            try:
                for key, previous in restore.items():
                    if previous is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = previous
            finally:
                with _ownership:
                    _active = None


def merge(base: Mapping[str, str], entries: Iterable[tuple[str, EnvValue]]) -> ComposedEnv:
    """Fold `[env]` entries onto a copy of `base`.

    Args:
        base: The environment the entries layer onto. Copied, never mutated.
        entries: `(key, value)` pairs in declaration order. A bare `str` value
            is a `ConstVar`, matching ocx's own grammar.

    Returns:
        The composed environment.

    Raises:
        ValueError: Two `ListVar` entries for one key spell different
            separators, a separator is one ocx cannot fold with, or a list
            value is edged by the separator it ends up with. ocx classifies all
            three as exit 65; no process ran here, so the SDK raises rather
            than fabricate a process error.
        TypeError: An entry carries something outside the `EnvValue` grammar.

    Example:
        >>> env = merge({"JDK_JAVA_OPTIONS": "-Xmx1g"}, [("JDK_JAVA_OPTIONS", ListVar("-ea", separator=" "))])
        >>> env.mapping["JDK_JAVA_OPTIONS"]
        '-Xmx1g -ea'
    """
    ordered = list(entries)
    separators = _reconcile_separators(ordered)
    merged = dict(base)
    for key, declared in ordered:
        match declared:
            case PathVar():
                current = merged.get(key)
                merged[key] = declared.value if current is None else _move_to_front(current, declared.value)
            case ListVar(value=""):
                continue  # An empty contribution must not bring a variable into existence.
            case ListVar():
                separator = separators.get(key, DEFAULT_LIST_SEPARATOR)
                merged[key] = _append_unique(merged.get(key, ""), declared.value, separator)
            case ConstVar():
                merged[key] = declared.value
            case str():
                merged[key] = declared
            case _:
                raise TypeError(
                    f"env var {key!r} carries {declared!r}, which is not an `[env]` value. Pass a "
                    "`str` (or `ConstVar`) to replace, a `PathVar` to prepend, or a `ListVar` to append."
                )
    return ComposedEnv(merged)


def _reconcile_separators(entries: Sequence[tuple[str, EnvValue]]) -> dict[str, str]:
    """Return the separator each list key folds with, established in declaration order.

    The first entry that spells a separator establishes the key's; entries that
    leave it `None` inherit that one, and keys nobody spelled are absent from
    the result, which `merge` reads as `DEFAULT_LIST_SEPARATOR`.

    Ports ocx's `reconcile_list_separators`, including its last pass: only once
    a key's separator is settled can a value edged by it be recognized, and a
    parse boundary upstream could only ever check the separator an author wrote
    — never one that arrived by inheritance or by default.

    Raises:
        ValueError: Separators conflict, a separator is unusable, or a value is
            edged by the separator it ends up folding with.
    """
    settled: dict[str, str] = {}
    for key, declared in entries:
        if not isinstance(declared, ListVar) or declared.separator is None:
            continue
        if not declared.separator or any(char in declared.separator for char in _UNUSABLE_SEPARATOR_CHARS):
            raise ValueError(
                f"env var {key!r} declares a list separator {declared.separator!r} ocx cannot fold "
                "with: it must be non-empty and free of '=', newline and carriage return. Use the "
                "separator the variable itself uses, such as ',' or ':'."
            )
        first = settled.setdefault(key, declared.separator)
        if first != declared.separator:
            raise ValueError(
                f"env var {key!r} is contributed as a list with conflicting separators "
                f"{first!r} and {declared.separator!r}; every contributor to one key must agree — "
                "spell the same separator, or omit it to inherit the one already established."
            )
    for key, declared in entries:
        if not isinstance(declared, ListVar):
            continue
        separator = settled.get(key, DEFAULT_LIST_SEPARATOR)
        if declared.value.startswith(separator) or declared.value.endswith(separator):
            raise ValueError(
                f"env var {key!r} contributes {declared.value!r}, which starts or ends with the "
                f"separator {separator!r} it folds with; that edge fuses with the fold's own and "
                "breaks the dedup — trim the value, or spell the separator the value really uses."
            )
    return settled


def _move_to_front(existing: str, value: str) -> str:
    """Return `existing` with `value` at the front and its earlier occurrences dropped.

    Segments compare exactly, empty ones are dropped, and an empty `value` is
    not prepended — a leading empty segment would mean "the current directory"
    to everything downstream.
    """
    survivors = [segment for segment in existing.split(os.pathsep) if segment and segment != value]
    return os.pathsep.join([value, *survivors] if value else survivors)


def _append_unique(existing: str, value: str, separator: str) -> str:
    """Return `existing` with `value` at the back and its earlier occurrences dropped.

    Wrap `existing` in the separator, collapse every separator-flanked
    occurrence of `value` to a fixpoint, unwrap, and append. Removing *every*
    occurrence — not just the first — is what keeps re-applying idempotent when
    the inherited value already carried duplicates. Elements stay opaque:
    their grammar belongs to the consuming tool, never to ocx.
    """
    wrapped = f"{separator}{existing}{separator}"
    occurrence = f"{separator}{value}{separator}"
    while occurrence in wrapped:
        wrapped = wrapped.replace(occurrence, separator)
    # Each replacement leaves a separator where a flanked match stood, so the
    # wrapper survives — unless everything collapsed and the two ends fused.
    inner = wrapped.removeprefix(separator)
    survivors = inner[: -len(separator)] if inner.endswith(separator) else ""
    return f"{survivors}{separator}{value}" if survivors else value
