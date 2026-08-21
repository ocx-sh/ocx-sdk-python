---
paths:
  - "src/**"
  - "tests/**"
  - "pyproject.toml"
---

# ocx-sdk Architecture Invariants

Distilled from `.claude/artifacts/sdk-design.md` (full rationale there).
Every change and every review is checked against these.

## Positioning

- **CLI wrapper, never reimplementation.** ocx owns resolution,
  verification, identifier grammar. **Exactly two bounded, contract-tested
  exceptions** (goal 4 carve-outs): the `[env]` merge algorithm (guarded by
  the printenv contract diff) and `registry_slug` canonicalization (guarded
  by CLI fixtures; mismatch **fails closed**, never silent-anonymous). No
  third carve-out without a design-doc amendment.
- **Carry, don't parse**: identifiers (`PackageRef`), digests, pin forms
  carried verbatim from ocx JSON. `str(PackageRef)` round-trips
  byte-for-byte (tested).
- **Never write ocx-owned files** (`ocx.toml`, `ocx.lock`, `config.toml`).
  Read via tomllib + published schemas. Write side waits on
  [ocx#326](https://github.com/ocx-sh/ocx/issues/326).
- `OCX_ENV`, `OCX_PATCHES`, `OCX_BINARY_PIN`: opaque pass-through. Never
  parse, rewrite, or synthesize.

## Dependencies

- **Zero runtime dependencies.** `dependencies = []`, stdlib only. Enforced
  by metadata test + import-walk test. New runtime dep = design change,
  only ever as an extra. Dev/test deps unrestricted.

## Layering

- Modules are underscored (package-private); **the public API is the
  curated `ocx_sdk/__init__.py` re-export list** — the only guaranteed
  import path.
- ALL subprocess code in `_process.py`; it receives **primitives only**
  (argv, finished env mapping, timeout, policy, on_log) — never an
  `OcxConfig`.
- ALL spawn-env assembly AND secret redaction in `_env.build_spawn_env()` —
  the single choke point. No other module composes child env or touches
  secret values.
- ALL JSON decode in `_results.py`. Shared vocabulary in `_types.py`, which
  imports `_errors` (for `ExitCode`) and nothing else — it is not itself a
  leaf. `_errors.py` is the one leaf: stdlib-only, and it must never import
  `_types`/`_config` (`OcxProcessError.retryable` stays policy-independent:
  fixed True for 75).
- Retry driver in `_retry.py` (pure delay generator + sync/async loops);
  callers pass their own transport classifier. `_bootstrap`/`_dist` never
  import runtime modules (`_process`, `_env`, `_client`).
- Presentation (`--format json --color never`) pinned on typed methods
  only — recorded exception: `version()` reads the plain stable contract.
  Raw `invoke`/`spawn` paths leave presentation to the caller.

## Bootstrap security (non-negotiable)

- sha256 from the manifest is the artifact trust boundary; checksum
  mismatch is never retried. Manifest `sha256=` is **required (fail
  closed)** when the host is not canonical `setup.ocx.sh` or `mirror_url`
  is set.
- Manifest fields consumed into paths/URLs are regex-validated
  (`..`/separators/backslash/CRLF rejected). Reads are size-capped.
- **Never `extractall`** — stream the single expected member to a temp fd
  after hash verification.
- Cache: root refused if symlinked, foreign-uid, or group/other-writable;
  dirs `0o700`; `mkstemp` in the final dir → write → hash from fd →
  `fchmod 0o700` → `os.replace`. Cache hits re-hash (opt-out
  `trust_cache=True`).
- Bootstrap auth bound to (scheme, host, port): `add_unredirected_header`,
  https-only redirect handler with hop cap, userinfo URLs rejected.
- No machine mutation: never `self setup`, profiles, or PATH.

## API conventions

- **Command alignment**: one ocx command = one method, named after the
  command (`prj.run` ↔ `ocx run`, `ocx.package.exec` ↔ `ocx package
  exec`). Command groups = stateless namespace `@property` wrappers:
  `package`, `config`, `patch` ship in v0.1; `index` and `self_` are **T2,
  post-v0.1** (trailing underscore on `self_` per PEP 8 name-clash
  convention — an attribute literally named `self` reads as a bug; note:
  `self` is NOT a Python keyword, do not claim it is). Raw escape hatch is
  `invoke`/`invoke_async` — command names are reserved.
- **Full flag fidelity** scoped to **stable, resolution-affecting flags**
  (experimental surfaces like `--ci=gitlab` excluded): repeatable flags →
  collections, `NAME...` → `*names`/sequences. No capability-dropping
  subsets.
- One rich frozen result struct per command; unknown JSON keys ignored;
  hand-written `from_dict`, no serialization frameworks.
- Enums only where runtime iterates/maps values; `Literal` aliases for
  argv-only sets; open sets stay `str`. Single obvious arg positional, rest
  keyword-only (recorded exceptions live in the design doc §7).
- All dataclasses `frozen=True, slots=True` unless a stated reason.
- Async: `_async` suffix on execution verbs only; `spawn`/`spawn_async`
  return stdlib `Popen`/`asyncio.subprocess.Process`. `spawn` kwargs are
  filtered: `args`/`shell`/`executable`/`env` rejected; `shell=False`
  invariant.
- Argv safety: positional identifiers starting with `-` rejected on every
  typed positional group; `--` emitted before child-argv groups (`run`,
  `package exec`) — the only groups the CLI documents it for (amended WP09;
  the leading-dash guard covers typed ref groups).
- Errors: exit code → exception subclass; never classify by stderr text.
  Every `OcxError.__str__` carries an actionable next step.
- `py.typed` ships in the wheel.

## State & concurrency

- **No state caching.** Handles frozen and cache-free; the only snapshot is
  the exe realpath at construction (discovery runs in a factory, never in
  `__init__` — `with_config` derivation must never re-discover), the only
  memo the compat gate (benign-race, shared across derived handles).
- Per-call env snapshot at spawn; no loop references stored; `on_log` may
  fire from concurrent pump threads (rejected on async paths in v0.1).
- `ComposedEnv.activate()` is process-global, single-owner, diff-based
  revert (touched keys only, absent-key delete, `finally`-guaranteed), never
  a full-snapshot-swap. The ownership lock spans only the check-and-set on
  entry and the release-after-restore on exit — never the block body, since
  holding it there would serialize callers and fake a nesting safety the
  API deliberately doesn't offer. Concurrent code uses `.mapping`.
- Always neutralize ambient `OCX_PROJECT`/`OCX_GLOBAL`/`OCX_QUIET` on spawn.

## Security

- Credentials: env-only per spawn (`OCX_AUTH_<SLUG>_*`), never persisted,
  never in argv. Secret dataclass fields are `field(repr=False)` with
  masked `__repr__`. Every secret value is exact-string-redacted from
  captured stderr, `on_log`, logged argv, and exception text — inside the
  `build_spawn_env`/`_process` choke path. A secrecy test asserts absence
  from `repr()`/logs/errors.
- Explicit config wins over ambient env (auth per slug;
  `insecure_registries=()` blocks ambient re-enable — fail-closed).
- `OCX_AUTH_*` reaches tools spawned via `run`/`exec` (ocx does not scrub —
  pinned by contract test). Credential-free pattern: pull first, then run
  through `with_config(auth={})`.
- Retry: default `retry_on` = `TEMP_FAIL(75)` only. Auth errors (80),
  checksum mismatches, 401/403/404 never retryable. **Mutating commands
  (`push`, `publish`, `announce`, `describe`, `login`) default
  `retry=None`** regardless of session config. Never retry
  `run`/`exec`/`spawn` children. `Retry-After` honored up to its own
  ceiling (`max_retry_after`), never truncated to `max_backoff`.

## Testing

- **Python floor 3.12**; unit matrix 3.12/3.13/3.14 × Linux/macOS/Windows;
  contract tier Linux-first (other OS gated). Pyright checks the floor.
- **100% unit coverage, use-case-first.** `fail_under = 100`; coverage is
  the floor, never the goal; a test written solely to color a line is a
  defect. Exclusions use `# pragma: no cover` with a reason (named
  platform branches only). **Recorded deviation**: the gate is enforced on
  the Linux CI legs only — platform-specific branches (POSIX process
  groups, the Windows kill path) make 100% unattainable on macOS/Windows;
  those legs still run the full suite to prove functional correctness.
- **Contract-first TDD, always**: stub (signatures + `raise
  NotImplementedError`) → specify (tests that fail on the stub) →
  implement. Never reversed.
- **Every bug starts with a regression test** confirmed to FAIL on the
  unfixed code before the fix. No validated-failing test, no merge.
- **Mechanism → named-test matrix** (design doc §14): every design
  mechanism has a named test row; a mechanism without one fails review.
- Timing code takes an injectable sleep/clock seam — no `time.sleep` in
  unit tests (repo rule).
- **Every doc code snippet is tested**: doctest for docstrings; Sybil for
  markdown fences (README included) with four markers — default (unit) /
  `contract` (pinned binary) / `acceptance` (compose stack) / `no-run` +
  reason (compile-check only). Unmarked-unrunnable fails CI.

## Compatibility

- Tested window, not promised range: `TESTED_OCX_VERSION` (CI pin — not a
  selection default) + `MIN_SUPPORTED`; latest-ocx canary. Compat gate
  raises below MIN, DEBUG-notes above the window (never warns). `ensure()`
  default = latest stable from live dist.json. The §3 durable-anchor
  checklist (incl. `OCX_AUTH_*` propagation behavior) is re-verified on
  every version bump.
