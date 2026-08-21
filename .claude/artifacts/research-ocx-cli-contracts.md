# Research: ocx CLI contracts (v0.1 command set)

Date: 2026-08-21. Source: explorer worker over `../ocx` docs
(command-line.md, environment.md, installation.md) + orchestrator live probes.
Consumed by: WP result parsers, signature-alignment checklist, contract tests.
ocx version probed: 0.5.8.

## Load-bearing corrections vs naive assumptions

1. **Global flags go BEFORE the subcommand**: `ocx --format json package inspect X`
   works; `ocx package inspect X --format json` exits 64 (live-verified). The SDK
   argv builder emits `[exe, *global_flags, *command, *args]`.
2. **No bare `install`/`exec`/`which`/`select`/`uninstall`** — moved to package
   tier; bare forms exit 64 with redirect message. No `ocx list` (use
   `index list`/`index catalog`, both T2). No `ocx printenv` — the design's
   printenv contract diff runs the SYSTEM `printenv` via `ocx run -- printenv`
   (mechanism unaffected).
3. `--json` = shorthand for `--format json`; last one wins.
4. `--global` ⋈ `--project` mutually exclusive (64). `--frozen` ⋈ `--remote` (64).

## Global flags (before subcommand)

`--log-level off|error|warn|info|debug|trace` (default warn) · `--format
plain|json` · `--json` · `--offline` · `--remote/-r` · `--frozen` · `--index
<PATH>` · `--quiet/-q` (suppresses stdout report — typed calls must NOT set;
neutralize ambient OCX_QUIET) · `--jobs <N>` (0=all cores) · `--color
auto|always|never` · `--project <PATH>` · `--global/-g` · `--config <PATH>`.

## Exit codes (docs general table)

0 · 1 generic · 64 usage · 65 data/platform-mismatch/ambiguous · 69 unavailable
(non-transient) · 74 io · 75 tempfail (RETRY signal; 75-vs-69 is the
retry-safety split) · 77 noperm · 78 config · 79 notfound(OCX) · 80 auth(OCX) ·
81 policy-blocked(OCX; offline/frozen refusal). **82 dirty-rc-block exists only
on `self setup`/`config setup`** (managed fence carried user edits, no --force).
No 83+.

## Per-command JSON shapes (v0.1 set)

- **`version`**: plain = bare semver (stable script contract). JSON:
  `{version}` required; optional `cargo_pkg_version` (only when differing),
  `channel`, `commit{sha,short,describe,dirty,timestamp}`,
  `build{timestamp,profile,target,rustc}`, `ci{provider,run_url,workflow,ref,sha}`.
  Schema open for extension.
- **`about`**: `{version, registry, platforms[], libc[], shell, home,
  commit{}, build{}, ci{}}`; `channel` only when baked in.
- **`status`** (project-scoped, no NAME/`-g`): `{project, lock{present,
  current, lock_version, declaration_hash, declaration_hash_expected,
  generated_by, generated_at, error?}, groups{<g>{tools{<name>{declared?,
  platforms?{<plat>: digest}}}, env{<KEY>{type,value}}}},
  package_settings{<id>{...}}}`. Binding state = key presence
  (declared+platforms/declared-only/platforms-only). Exit 0 ALWAYS (broken
  lock = payload, not failure); 64 = no ocx.toml or any selector passed.
- **`inspect`** (toolchain) / **`package inspect`** (OCI) — same envelope:
  `{platform?, packages[], env[]}`. Package entry default:
  `{name, identifier, candidates[{digest, pinned, platform}]}` (`package
  inspect` candidates add `media_type`, `size`). `--resolve` adds
  `pinned_identifier, pinned_digest, metadata, layers[{digest,media_type,size}],
  resolution{pinned, chain[{digest,role∈index|manifest|config,media_type,size}]}`.
  `--closure` adds `closure{deps[], surface{interface{binaries,entrypoints,env,
  integrations,binaries_complete}, private{...}}, conflicts{entrypoints[],
  repositories[]}}`; non-empty conflicts → exit 65 WITH payload. env entries
  `{key,type,value}` ordered, dupes allowed. Toolchain inspect flags:
  `-g/--group` (repeatable, `all`), `-p/--platform`, `--resolve`, `--closure`,
  `--env`. Exits: toolchain 0/64/65/78(no lock)/81; package 79/81/65.
- **`run`** (toolchain exec): `run [-g GROUP]... [--clean] [--lazy-mode M]
  [--env K[:T[:S]]=V]... [NAME...] -- ARGV...` — `--` mandatory. `--env` TYPE ∈
  constant|path|list, first-= split, **rejects `OCX_*`/`__OCX_*` keys at 64**.
  Child exit code forwarded byte-for-byte; 1 spawn failure; 64/65/78 setup
  errors; 69/75/79/80 during auto-install.
- **`env`** (toolchain): flags `-g`, `--shell[=NAME]` (equals-form REQUIRED),
  `--ci[=PROVIDER]`, `--export-file` (gitlab-only), `-p`, `--lazy-mode`,
  `--pull/--no-pull` (default pull), `--show-patches`, `--env`. JSON = same
  envelope as `package env`. Exits: 0 (global tier always lenient), 64, 65,
  78.
- **`package env`**: `{entries[{key,type,value}], binaries[{name,package}],
  entrypoints[{name,package}], integrations[{namespace,package,payload{}}],
  advisories[{kind,package,key,message}]}` — all 5 arrays ALWAYS present.
  Flags: `-p`, `--candidate|--current`, `--self`, shell/ci/export-file,
  `--show-patches`, `--env`.
- **`package which`**: JSON per identifier `{path, kind: "package"|"shim"}`
  (doc-flagged breaking pre-1.0). Never downloads. Flags: `-p`,
  `--candidate|--current`, `--lazy-mode`.
- **`package exec`**: `<PACKAGES>... -- <COMMAND> [ARGS...]`; flags `-p`,
  `--clean`, `--self`. Unix: execvp REPLACEMENT (inherits PID — SDK spawn
  wraps it, child exit forwarded verbatim). No JSON.
- **`package install`**: `[-p PLATFORM] [-s/--select] <PACKAGE>...`. **NO
  documented JSON envelope** — InstallReport shape must be pinned by live
  probe during execution (UNCONFIRMED; progress/table output).
- **`package info`**: `{"<id>": {...}|null}` keyed even for single id; inner
  shape UNCONFIRMED. Flags `--save-readme`, `--save-logo` (single-id only).
- **`login`**: `[-u USER] [--password-stdin] [--allow-insecure-store]
  [--verify/--no-verify] [REGISTRY]` — NO `-p/--password` flag (by design);
  `--auth-type` reserved → 64. JSON `{registry, username}`. Exits
  0/64/74/75(cred-helper timeout)/78(no store w/o --allow-insecure-store)/80.
  Store: docker config.json tiers credHelpers > credsStore > plaintext auths.
- **`logout`**: `[REGISTRY]`, always exit 0 (noop ok). JSON `{registry}`.
- **`config setup`**: `[--managed-config REF] [--dry-run] [--force]`; output =
  `self setup`'s `managed_config` object; exits 0/64/65/69/74/78/79/80/82.
- **`config update`**: `[VERSION] [--pause DUR(max 7d)] [--resume] [--check]`;
  JSON `{status∈not_configured|already_current|updated|checked|
  check_unavailable, source, digest, policy∈apply|notify|manual, tag,
  fetched_at, kill_switches[], drift, paused_until, pinned}`. Exits
  0/64/65/69/74/78/79/80.
- **`self setup`** (T2, recorded): `[VERSION] [--no-modify-path] [--profile
  PATH]... [--dry-run] [--force] [--managed-config REF]`; VERSION = tag |
  sha256:hex | tag@sha256. JSON: `{status∈completed|no_op|skipped|migrated,
  bootstrap{status∈already_present|pulled|would_pull, version, digest},
  shims[], profiles[{path,outcome}], dirty_profiles?, exec_policy_warning?,
  conflicting_ocx?, reload_hint?, managed_config{status}}`. Exits incl. 82.
- **`self update`** (T2): `[--check]`; JSON discriminated on `status` ∈
  up_to_date | update_available{identifier} | installed{from,to} |
  skipped{skipped_reason{reason∈bootstrap|offline|throttled|
  registry_probe_failed|not_found|unparseable_current|unparseable_latest|
  no_release_tag, detail?}}. Exits 0/69/74/75/79/80.
- **`config test`** (T2): pure local; JSON keys always present: `{candidate,
  valid, registry_default, registries, mirrors, patches, managed,
  unknown_keys}`. Exits 0/74/77/78/79.

## OCX_* env vars (SDK-relevant subset)

Truthy: `1,y,yes,on,true`; falsy: `0,n,no,off,false` (case-insensitive).

- Forwarded-to-children set (docs: complete list): `OCX_BINARY_PIN`,
  `OCX_OFFLINE`, `OCX_REMOTE`, `OCX_CONFIG`, `OCX_INDEX`, `OCX_GLOBAL`,
  `OCX_ALLOW_YANKED`, `OCX_MIRRORS`, `OCX_PATCHES`, `OCX_ENV`,
  `OCX_MANAGED_CONFIG`, `OCX_PATCH_SNAPSHOT`. NOT forwarded: presentation
  (no OCX_* counterparts for --format/--color/--log-level exist... except
  `OCX_LOG` which maps --log-level but is not in the forward list),
  `OCX_LAZY_MODE`, `OCX_LAZY_REPORT`.
- Auth: `OCX_AUTH_<REG>_{TYPE(basic|token|anonymous), USER, TOKEN}`.
- Behavior: `OCX_HOME` (~/.ocx), `OCX_PROJECT` ⋈ `OCX_GLOBAL` (64 if both),
  `OCX_QUIET`, `OCX_OFFLINE`, `OCX_REMOTE`, `OCX_FROZEN`, `OCX_JOBS` (flag
  wins), `OCX_LOG` (flag wins), `OCX_DEFAULT_REGISTRY` (default ocx.sh),
  `OCX_INSECURE_REGISTRIES` (comma host[:port], union w/ config, never
  revocable ambient — SDK's explicit-() fail-closed replaces it),
  `OCX_MIRRORS` (JSON, malformed = hard startup abort), `OCX_NO_CONFIG`,
  `OCX_NO_CONFIG_REFRESH`, `OCX_NO_UPDATE_CHECK`, `OCX_NO_PROJECT`,
  `OCX_ANNOUNCE_TOKEN` (bearer, never logged). `_OCX_APPLIED` REMOVED.
- Malformed `OCX_MIRRORS`/`OCX_PATCHES`/`OCX_ENV` = hard startup abort.

## dist.json (live-verified 2026-08-21 via setup.ocx.sh)

Envelope object (not bare rows): `{latest{channel, version}, latest_next:
null|..., releases: [{channel, filename, sha256(64 hex), tag, target, url,
version}] (232 rows), schema: <int>}`. Row fields exactly as designed (§5).
Parser reads `releases`; `latest`/`latest_next` resolve channel-latest;
validate `schema` int presence, tolerate unknown keys.

## Not extracted (needed during execution via live `ocx <cmd> --help` probe)

`init`, `add`, `remove`, `lock`, `update`, `pull`, `package
select/deselect/uninstall/deps/pull/create/test/push`, `patch sync` — flag
surfaces + JSON shapes pinned by the signature-alignment checklist during
implementation (probe `ocx --format json <cmd> --help` + live fixture runs).

## WP00 addendum (live probes, ocx 0.5.8)

Fixtures: `tests/fixtures/cli/*.help.txt` (30 commands) + `*.json` (29 live
runs) + `author_flow.sh` (documentation) + `../slug_fixtures.json`. All
commands above are now extracted; nothing in the v0.1 set remains
unobtainable. Full per-command help text and JSON is in the fixture files —
this section is deltas + shapes worth flagging, not a restatement.

### Project-tier commands (`init`/`add`/`remove`/`lock`/`update`/`pull`/`status`/`inspect`/`env`)

- **`add` / `lock` / `update`** (no `--check`) all return the **same shape**
  on success: a bare JSON array of tool rows —
  `[{binding, group, digest, platforms{<plat>: digest, ...}}]` — this is
  effectively "the ocx.lock tool entries that changed", not a per-command
  envelope. `remove` returns the same array shape but empty (`[]`) on the
  single case tested (no multi-group ambiguity triggered).
- **`lock --check` / `update --check`**: on success, exit 0 with **empty
  stdout — no JSON body at all**, even under `--format json`. `CommandResult`
  parsing must treat empty-body + exit-0 as a valid, meaningful outcome for
  these two, not a parse failure.
- **`pull`** (project tier): object keyed by the pinned identifier ->
  `{path, kind}`, plus a top-level `"advisories": []` sibling key. Differs
  from `package pull` (below), which returns a bare path *string*, not an
  object.
- **`status`**: matches the doc-derived shape exactly; live example captured
  in `status.json`.
- **`inspect`** (toolchain, no flags / `--resolve` / `--closure`): matches
  the doc-derived envelope exactly across all three variants; live examples
  in `inspect.json` / `inspect_resolve.json` / `inspect_closure.json`. One
  detail worth flagging for the results parser: under `--resolve`, a
  toolchain-tier binding's `resolution.chain` starts at `role: "manifest"`
  (no `index` hop) because the lock already pins a platform manifest — see
  the *package*-tier contrast below.
- **`env`** (toolchain): matches `package env`'s 5-array envelope exactly
  (`entries`, `binaries`, `entrypoints`, `integrations`, `advisories`, all
  always present). Live example in `env.json`.

### Package-tier commands

- **`package install`** (UNCONFIRMED shape — now pinned): object keyed by
  the **identifier as given** (not the pinned form) ->
  `{identifier: "<pinned form, @sha256:...>", metadata: {...full metadata.json...}, path: "<symlink path>"}`.
  Confirmed via both `ocx.sh/go-task/task:3` (published registry) and the
  WP00-authored `127.0.0.1:<port>/wp00/hello:1.0.0` (throwaway local
  registry) — same shape both times.
- **`package which`**: matches the doc-derived shape exactly — object keyed
  by identifier -> `{path, kind: "package"}`.
- **`package inspect`** / `--resolve`: matches the toolchain `inspect`
  envelope, with one structural difference worth flagging for the results
  parser — package-tier `name` is the **full requested identifier**
  (`"ocx.sh/go-task/task:3"`), not a binding name, and under `--resolve` the
  `resolution.chain` gets an **extra leading `role: "index"` hop** before
  `manifest`/`config` (package tier starts from a tag, so it walks the OCI
  index; toolchain tier starts from a lock-pinned digest and skips it).
- **`package info`**: confirmed the doc-flagged shape — object keyed by
  the identifier **as given**, value `null` when the registry holds no
  description metadata for it (`{"ocx.sh/go-task/task:3": null}`). Inner
  non-null shape is still UNCONFIRMED — no package available during this
  probe carried README/logo/description metadata (`--save-readme`
  /`--save-logo` need a package that has some; none of the packages touched
  here did). Flag for a follow-up probe if a real non-null example is needed
  before WP04 pins `InfoResult`.
- **`package env`**: matches the toolchain `env` envelope exactly (see
  above).
- **`package deps`**: **new shape, not previously recorded** —
  `{"roots": [{"identifier", "repeated": bool, "visibility": null|string, "dependencies": [...]}]}`.
  Not a bare array; wrapped in a `"roots"` key. `dependencies` was empty for
  the single-package, no-deps case tested — nested shape for a populated
  dependency edge is UNCONFIRMED (would need a package with real
  dependencies to probe further).
- **`package select`**: object keyed by identifier ->
  `{identifier: "<pinned>", metadata: {...}, path: ".../current"}` — same
  shape as `install` except `path` points at the `current` symlink instead
  of `candidates/<tag>`.
- **`package pull`**: object keyed by identifier -> a **bare path string**
  (not `{path, kind}` like project-tier `pull` or `package which`) —
  `{"ocx.sh/go-task/task:3": "/…/packages/ocx.sh/sha256/df/…"}`. Three
  different "give me a path" shapes exist across the surface (`pull` ⋈
  `package which` ⋈ `package pull`) — flag this asymmetry explicitly in
  `_results.py` docstrings so a future contributor doesn't assume they're
  interchangeable.
- **`package deselect`** / **`package uninstall`**: **both return a JSON
  array**, not a keyed object — `[{"package", "status": "removed", "path"}]`
  — the only two package-tier mutators that don't key by identifier.
- **`package test`**: confirmed the **stable v1 envelope is `--script`-only**.
  The trailing `-- CMD` form prints the **raw child stdout verbatim**, even
  under `--format json` — no `{status, assertion, run}` wrapper at all. This
  is a signature-alignment trap: `TestResult.from_dict` must never be
  invoked against `-- CMD` output; only `--script` runs produce parseable
  JSON. Confirmed exact schema from docs live: `{"status": "passed", "assertion": null, "run": {"exit_code": 0, "stdout": "...", "stderr": "", "duration_ms": 1, "truncated": false}}`.
- **`package push`**: full shape confirmed —
  `{identifier, status: "pushed", manifest_digest, cascade_tags_written: [], canonical_tags_written: ["sha256.<hex>"], layers: {mounted, uploaded, verified}}`.
- **`package create`**: emits **no stdout JSON at all**, even under
  `--format json` — only INFO logs to stderr and the three output files
  (bundle, `<stem>-metadata.json` sidecar, `<stem>-receipt.json`). The
  receipt shape: `{"version": 1, "platform": "linux/amd64", "identifier": "<as given>"}`.
  Nothing to parse here; `_results.py` needs no `CreateResult`.

### `about`

Matches the doc-derived shape exactly, including the full `commit`/`build`/
`ci` sub-objects — live example in `about.json`.

### registry_slug — resolved

The design doc and plan both cite a single "ocx `registry_slug`" function;
the ocx source has **two distinct slug transforms**, and only one of them is
the one WP06 needs:

- **`StringExt::to_slug`** (`crates/ocx_lib/src/utility/string_ext.rs`) —
  strict: regex `[^a-zA-Z0-9]` -> `_` (dots, colons, dashes all become `_`,
  no case-folding). Consumed at `auth.rs:121`
  (`let registry_slug = registry.to_slug();`) to build the
  `OCX_AUTH_<SLUG>_{TYPE,USER,TOKEN}` env var names — **this is the
  `registry_slug` the architecture rule's carve-out (b) and WP06's slug port
  mean.** `get_env_auth` does **not** run `canonicalize_registry` first — the
  raw registry string a caller passes is slugged as-is (e.g. `docker.io` is
  never rewritten to the `index.docker.io/v1` alias before slugging).
- **`StringExt::to_relaxed_slug`** (same file) — permissive: regex
  `[^a-zA-Z0-9._-]` -> `_` (dots and dashes survive). Used only for **on-disk
  path components** (`package_store`, `layer_store`, `blob_store`,
  `shim_store`, `index_store`, `patch::expand_patch_path`) via the
  `file_structure::slugify()` wrapper — confirmed by our own author-flow
  probe: `127.0.0.1:5099` installed to a symlink path containing
  `127.0.0.1_5099` (colon -> `_`, dots preserved). **Not** what WP06 needs;
  noted in `slug_fixtures.json` so nobody conflates the two later.

Both files are byte-identical between the `v0.5.8` tag and the pinned
`../ocx` commit (`10cc1ef0`, `git diff v0.5.8..HEAD` on these two files is
empty), so the corpus in `tests/fixtures/slug_fixtures.json` is valid
against the live-probed 0.5.8 binary. Corpus: 11 cases — 3 literal Rust-test
assertions (`string_ext.rs`, `auth.rs`), 8 derived by applying the now-fully
-confirmed regex (ports, uppercase, IP:port, empty string, single char,
`docker.io`-not-canonicalized) with provenance recorded per-case.

### Author-flow spike — fully worked, no failures

`create` (local dir, minimal `metadata.json`, no dependencies) ->
`package test --script` (local, no registry) -> `package push -n` (to a
throwaway `registry:2` container) -> `package install` (fresh pull from that
registry) -> `package exec` (runs the installed binary). Every step
succeeded on the first attempt once the registry was reachable and
`OCX_INSECURE_REGISTRIES` was set for the plaintext local registry. Full
reproducible command sequence: `tests/fixtures/cli/author_flow.sh`.

One environmental note, not an ocx-contract finding: the task brief's
suggested port 5001 was already bound by an unrelated container from another
project on this host (`test-mirror-registry-1`) — the spike used 5099
instead. Anyone re-running this flow should pick a free port rather than
assuming 5001 is open.

### CLI-shape corrections found

- **`ocx --version` is not a flag** — exits 64 with a clap usage error
  (`unexpected argument '--version' found`). The version command is the
  `version` subcommand only (`ocx version` / `ocx --format json version`).
  Relevant if any bootstrap/discovery code (`_bootstrap.py`, C-006) is
  tempted to probe with `--version` the way many CLIs support both forms.
