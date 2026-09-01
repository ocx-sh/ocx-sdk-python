# hex memory — ocx-sdk-python

Created by `/hex-plan` on 2026-08-31. Run `/hex-init` to instantiate the full
model matrix, preferences, and pointer set; this file currently carries only
what the first planning run discovered.

## Pointers

- Plan / artifact home: `.claude/artifacts/` (project convention, stated in
  `.claude/rules/workflow-intent.md` § Shared Gates). Not the `.agents/plans/`
  default.
- Artifact templates: `.claude/templates/artifacts/` is referenced by
  `workflow-intent.md` but **does not exist on disk** — plans use the shipped
  hex scaffold until it does.
- Verification command: `task verify` (format:check → lint → types → test →
  cov:report). Under an activated toolchain it runs bare; otherwise
  `ocx exec -- task verify`. Coverage gate is `fail_under = 100`, branch+line,
  Linux-only.
- Project rules catalog: `CLAUDE.md` § Rule catalog → `.claude/rules/*.md`.
  `architecture.md` is the governing design document for SDK changes.
- Upstream source of truth: sibling repo `/home/mherwig/dev/ocx` (Rust). Wire
  shapes live in `crates/ocx_cli/src/api/data/`, exit codes in
  `crates/ocx_lib/src/cli/exit_code.rs`.
- Constitution: none declared.

## Preferences

Not instantiated — no `/hex-init` run yet. Shipped class defaults apply
(`fast-balanced` → Sonnet, `deep-reasoning` → Opus). No adversary skill is
configured; the 0.2.0 planning run used `codex:rescue` as the cross-model seat
and announced the substitution.

## Memory

- **Active plan:** `.claude/artifacts/plan-0.2.0-ocx-0.6-adoption.md` —
  ocx-sdk-python 0.2.0 adopting ocx 0.6.0. Executed 2026-08-31 at tier high:
  9 work packages (WP8 added mid-run), all merged onto
  `hex/0.2.0-ocx-0.6-adoption` at `cfa24dc`. Unit 1325 / 100% coverage,
  contract 67, acceptance 13, strict docs clean. **10 packages delivered, not 8** —
  WP8 for D14, WP9 for the adversary gate. Final head `7130412`: unit 1475 / 100%
  (2556 stmts), contract 67, acceptance 13, strict docs clean.
  **Not pushed, not merged to `main`** — that is the owner's call.
  **The cross-model gate did not run cross-model**: the `codex:rescue` seat executed as
  Claude Sonnet and disclosed it. Findings were real and are fixed; the tier-high
  requirement is still unmet.

Durable facts, updated after the 0.2.0 execution:

- **ocx emits a full JSON report alongside a non-zero exit** (per-leg sign
  failure, partial tag sweep, `copy` refused on a sidecar conflict, `push`
  whose inline signing failed). **Fixed in 0.2.0**: `OcxProcessError.stdout`
  carries it, never in the message, and `partial_report()` recovers it.
  `partial_report` returns stdout **verbatim** — the enveloped parsers unwrap
  for themselves, so handing them a pre-unwrapped `data` would unwrap twice.
- **`--format json` payloads come in two root shapes.** `sign`, `attest`,
  `verify`, `sbom` and swept sign/attest wrap the report in the C-S1-1
  envelope under `data`; `copy`, `push` and every pre-0.6 command emit it bare.
  Nothing in the CLI signals which. Assuming one shape yields a parser that
  reads nothing and still passes its fixture test.
- **`--help` is not the refusal set.** Three separate defects this run came
  from reading help text: `--key` conflicts with five flags not one; `sbom`'s
  fourth guard (`--no-verify` with `--key`) is refused in `mode()` not clap;
  `push`'s four signing modifiers sit in an `ArgGroup` with `.requires`. And
  `signature_format` advertises `both` on all five commands while the read
  side refuses it. **Always read the clap definitions and the command body.**
- **Doc fences ARE enforced now** *(was: they are not)*. WP5 put `docs README.md`
  into the contract leg of `taskfile.yml`, taking it 33 → 59 collected.
  Docstring fences remain compile-only via `conftest.py`'s `_compile_only`, so
  a stale example in a *docstring* still passes — that blind spot hid three
  stale `project.run(...)` examples this run.
- The fixture tree splits into two kinds: `tests/fixtures/cli/**` is provenance
  loaded by no test; `tests/fixtures/results/**` is `test_results.py`'s
  specification. A reviewer treating them alike will mis-scope fixture work.
- **Fixtures must come from the binary.** A hand-written fixture certified the
  `canonical_tags_written` bug — the parser read a key 0.6.0 no longer emits
  and its test passed. 0.2.0's fixtures are live captures against ocx 0.6.0 and
  a loopback `zot:v2.1.18`. `registry:2` (distribution 2.8.3) cannot back
  signing captures: it 404s a manifest fetch whose `Accept` omits the index
  media type, so ocx cannot resolve its own subject.
- **Exits 83 and 84 have no contract-tier test and cannot get one here.** ocx
  accepts only cosign's scrypt-wrapped `ENCRYPTED SIGSTORE PRIVATE KEY`, which
  neither openssl nor the stdlib writes, and `cosign` is not in `ocx.toml`. 84
  is additionally write-path only — the read path maps an absent Referrers API
  onto 79. Reasons are recorded in `tests/contract/test_exit_codes_contract.py`.
- **Review agents must not mutate a live worktree.** A quality reviewer
  mutation-tested in place with an unstable cwd and overwrote a builder's
  concurrent fix. Mutation testing goes against a scratchpad copy, with
  `git -C`/`env -C` absolute everywhere. And a copied `.venv`'s editable install
  still resolves the package to the ORIGINAL `src/` — pin `PYTHONPATH` and
  assert `m.__file__`, or a survived mutation reads as success.
- **A hand-written fixture is evidence about its author, not about the wire.** Three
  instances this release: `canonical_tags_written`'s fixture certified a parser reading a
  key 0.6.0 no longer emits; ten optional keys were renameable because every assertion on
  them was `is None`; and a `pull(dry_run=True)` row carried a hand-written object no
  preview emits, hiding a hard crash behind a passing argv test. Capture from the binary,
  and assert at least one *populated* value per optional key.
- **Pin requiredness by generation, in both directions.** `test_results.py` now carries
  `_REQUIRED_FIELD_SOURCES` (every struct → its required keys) and `_OPTIONAL_KEYS`, both
  checked against the operators each parser actually uses, read off its AST. This was built
  after the adversary gate found 4 presence-rule inversions; widening it immediately
  surfaced **22 more**, all in v0.1 parsers shipped long before. Deferring it as "scope,
  not principle" was wrong — do not re-defer it.
- **`_need` vs `.get` is a three-way distinction, not two.** `skip_serializing_if` ⇒ absent
  ⇒ `.get`. No attribute ⇒ always present ⇒ `_need` — and if the Rust type is `Option<T>`
  the value can still be `null`, so the Python annotation needs `| None` while the operator
  stays `_need`. Missing that third case is what let `TestRun` fabricate a whole record out
  of a legitimate `"run": null`.
- Note for the next `/hex-init`: no adversary skill is configured. `codex:rescue`
  worked well in the `plan-artifact` seat and earned its slot — it caught a
  wire-contract error (an invented JSON field on a public struct) that no
  same-model reviewer found.
