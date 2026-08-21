# ocx-sdk Design

Status: REVISED — adversarial review round 1 applied (8-reviewer panel,
2026-08-21; all Block/High/Warn/Suggest findings resolved or explicitly
decided). Next: `/hex-plan`.
Date: 2026-08-20/21. Sources: conversation decisions + research reports
(www-setup dist.json, rules_ocx/find_ocx bootstrappers, ocx CLI machine
interface, wrapper-SDK survey, full 65-command inventory, stability
analysis) + review-panel findings.

## 1. Goals

Two personas, both first-class:

**Consumer** — bootstrap and drive ocx toolchains from Python.
**Author** — create, test, and publish ocx packages from Python instead of shell.

1. **Bootstrap anywhere.** Pin + verify + cache an exact ocx binary in any
   environment — CI, corporate mirror, air-gapped — with auth, without machine
   mutation by default. The artifact sha256 from the manifest is always
   enforced; manifest authenticity rests on TLS to the canonical host or an
   explicit caller-supplied pin (§5) until upstream signing lands
   ([www-setup#14](https://github.com/ocx-sh/www-setup/issues/14)).
2. **Drive a known-good subset.** Typed, CWD-independent interface to commands
   proven to work (tested against the pinned ocx in CI); `invoke()`
   passthrough for the rest — possible but explicitly unsupported.
3. **Embed anywhere.** Zero runtime dependencies, Python 3.12+, stateless
   thread-safe handles. Adoptable regardless of the host project's dep tree.
4. **Fidelity over convenience.** ocx owns all semantics. The SDK never
   reimplements them, **with exactly two bounded, contract-tested
   exceptions**: (a) the `[env]` merge algorithm (§8 — ocx emits typed
   entries and delegates composition to consumers by design; guarded by the
   printenv contract diff), and (b) `registry_slug` canonicalization (§9 —
   guarded by CLI fixtures; a fixture mismatch **fails closed**, never falls
   back to anonymous). The SDK never caches ocx state and waits for CLI
   interfaces rather than editing ocx-owned files
   ([ocx#326](https://github.com/ocx-sh/ocx/issues/326) filed).
5. **Corporate first-class.** Managed config, mirrors, dist pinning, registry
   auth are primary paths, not afterthoughts.
6. **Default just works.** ocx already on PATH / in `$OCX_HOME` → `Ocx()`
   works with no bootstrap. Bootstrap is one optional producer of the binary
   path, not the entry gate.

**Non-goals**: pure-Python OCI client; machine setup by default; TOML mutation
(until [ocx#326](https://github.com/ocx-sh/ocx/issues/326)); wrapping
removed/dead commands; orphan-proofing `spawn` children against a
SIGKILL'd parent (§12 — documented non-goal, testcontainers' Ryuk exists
because in-process cleanup can't solve this).

## 2. Positioning

CLI wrapper, not a spec reimplementation. Rationale: ocx is pre-1.0 and
fast-moving; resolution/lockfile/verification logic lives in the binary;
reimplementation guarantees drift and duplicates security-sensitive
verification. Precedent: terraform-exec/hc-install, Pulumi Automation API,
playwright-python, GitPython (cautionary), binary-in-wheel (rejected — ocx
releases on its own cadence).

Future PyO3 option, scoped honestly: **typed one-shot commands** could route
to a native `ocx_lib` backend (the exception hierarchy survives — subclasses
are semantic, the exit-code→class map is data; JSON decode lives in
`_results.py`, not the process layer, precisely so a native backend replaces
transport, not parsing). **Process-hosting verbs stay CLI forever**:
`spawn`/`spawn_async` returning stdlib handles, `capture=False` stdio
inheritance, SIGINT forwarding, and `invoke(argv)` are subprocess semantics
by contract. No backend abstraction until a second backend exists.

## 3. Compatibility policy

Pre-1.0 on both sides → **tested window, not promised range**.

- SDK ships `TESTED_OCX_VERSION` (exact version CI e2e-tests against — NOT a
  selection default; `ensure()` defaults to latest stable from dist.json, §5)
  and `MIN_SUPPORTED` (initial value = the `TESTED_OCX_VERSION` at v0.1
  release; both live in `_types.py`; MIN may be raised freely by SDK minors —
  repo policy: no shims pre-1.0).
- Lazy runtime check via `version()` on first typed call: **raise
  `VersionCompatError` below `MIN_SUPPORTED`; newer-than-tested logs a
  DEBUG-level note, never a warning** — the canary keeps the window current,
  so newer is expected, not alarming. (Decision 2026-08-21; replaces
  warn-outside-window, which made the default happy path warn by
  construction.)
- CI runs the pinned version on every PR **plus a latest-ocx canary job** —
  upstream drift surfaces as a red canary, not a user bug report.

Evidence this is necessary (stability analysis): ocx's own policy is "even
interfaces break pre-1.0, announced in changelog only". Within
v0.5.0→v0.5.8: exit-code semantics moved (69→75, v0.5.3), JSON envelopes
reshaped (v0.5.0), lock v2→v3 hard break, metadata fields renamed.

**Re-verified on every version bump** (the durable-anchor checklist — each
has a named contract test, §14): exit-code taxonomy · `version` plain output ·
file-schema URLs (project/v1, project-lock/v3, metadata/v1, config/v1) ·
`launcher exec` wire ABI · `package test --script` JSON ("stable v1
contract") · the `$OCX_HOME/…/current/content/bin/ocx` stable install
symlink path · `ocx env --format json` typed-entry envelope ·
`OCX_AUTH_<SLUG>_*` grammar · `login --password-stdin` · the global
`--project` flag · **`OCX_AUTH_*` child-propagation behavior** (§9).

**Opaque pass-through rule**: `OCX_ENV`, `OCX_PATCHES`, `OCX_BINARY_PIN` are
unversioned fail-closed payloads composed by ocx. The SDK never parses,
rewrites, or synthesizes them.

### Threat model (trusted inputs, stated)

Three defensible-for-a-dev-library defaults jointly mean **the ambient
environment is a trusted input**: `OCX_INSTALL_*` honored from ambient env
(chooses what gets downloaded and from where), `OCX_AUTH_*` passed through
(chooses which credentials attach), and PATH-based discovery (chooses which
binary runs). The cache directory is trusted persistent state (hardened in
§5). Hardening levers, all opt-in: `HostEnv.clean()`/`.only()`/`.minimal()`
for env; `exe=` for discovery; `trust_cache=False` semantics are default-on
verification (§5). Runtime precedence is **explicit config wins over
ambient**: an `OcxConfig` value always beats the corresponding ambient var,
and `insecure_registries` is fail-closed — an explicit `()` blocks ambient
`OCX_INSECURE_REGISTRIES` from re-enabling plaintext. The hermetic CI recipe
ships in the §17 CI guide.

## 4. Architecture

All modules are underscored (package-private by CLAUDE.md's own rule); the
**public API is the curated `ocx_sdk/__init__.py` re-export list** — the
only import path the SDK guarantees. `from ocx_sdk import Ocx, HostEnv, …`.

```
ocx_sdk/
  __init__.py   # THE public surface: curated re-exports + __version__
  _types.py     # shared vocabulary leaf: PackageRef, BasicAuth/BearerAuth/Auth,
                #   RetryPolicy, HostEnv, ConstVar/PathVar/ListVar/EnvValue,
                #   InstallEnv, Channel, TESTED_OCX_VERSION/MIN_SUPPORTED,
                #   MANAGED_CONFIG_DISABLED. Imports: errors only.
  _results.py   # CommandResult, EnvReport/EnvEntry, InstallReport, TestResult,
                #   per-command structs + from_dict parsers (ALL JSON decode).
  _errors.py    # ExitCode IntEnum + exception hierarchy. Leaf, stdlib-only.
  _dist.py      # DistSource, DistManifest, fetch + validation (imports _types, _errors, _retry)
  _bootstrap.py # ensure() (imports _dist, _types, _retry — never runtime modules)
  _config.py    # OcxConfig (imports _types, _errors)
  _envmodel.py  # ComposedEnv: the [env] merge + activate (imports _types)
  _env.py       # build_spawn_env(): filter + serialize + neutralize + auth
                #   passthrough + redaction — THE spawn-env choke point
  _retry.py     # RetryPolicy driver: _delays() generator + sync/async loops
                #   (~30 lines; transport classifiers passed in by callers)
  _process.py   # child-process lifecycle ONLY: spawn, timeout enforcement,
                #   kill escalation, pump, signal forwarding
  _client.py    # Ocx, Project, command-group namespaces
```

Import graph is acyclic with one leaf (`_errors`); `_types` imports
`_errors` only. Bootstrap never imports runtime modules (`_process`, `_env`,
`_client`). Layer rules:

- ALL subprocess code in `_process.py`; it receives **primitives only**
  (argv, finished env mapping, timeout, policy, on_log) — never an
  `OcxConfig`.
- ALL spawn-env assembly + secret redaction in `_env.build_spawn_env()` —
  the single choke point (§9, §12).
- ALL JSON decode in `_results.py`.
- `OcxProcessError.retryable` is policy-independent (fixed: True for 75) —
  `_errors` must never import `_types`/`_config`; this line is the guard.

## 5. Bootstrap layer

### dist.json (source of truth: setup.ocx.sh)

Flat rows `{version, channel, tag, target, filename, sha256, url}`; channels
`stable`/`next`; rolling `/dist.json` + immutable content-addressed
`/dist/<sha256>.json` snapshots (a snapshot URL pins the whole closure).
Upstream asks filed: signature
([www-setup#14](https://github.com/ocx-sh/www-setup/issues/14)), `size`
field ([www-setup#15](https://github.com/ocx-sh/www-setup/issues/15)).

```python
DistSource.url(url, sha256=None, auth=None, headers=None)  # default https://setup.ocx.sh/dist.json
DistSource.path(path, sha256=None)
DistSource.data(raw, sha256=None)    # bytes from anywhere — enables user-side
                                     # vendoring via importlib.resources (zip-safe)
```

- `sha256` verifies the manifest body. Auto-derived for `dist/<sha256>.json`
  URLs — **from the caller-supplied URL string before any network I/O**,
  never from a post-redirect URL (a redirect must not choose the expected
  digest). **`sha256=` is REQUIRED (fail closed, refuse to fetch) whenever
  the manifest host is not canonical `setup.ocx.sh` or `mirror_url` is
  set** — on non-canonical hosts TLS alone does not establish manifest
  authenticity (CWE-345).
- `auth: BasicAuth | BearerAuth | None` → `Authorization` header. **Bound to
  the (scheme, host, port) of the caller-supplied URL**: attached via
  `add_unredirected_header` (urllib's redirect handler forwards ordinary
  headers cross-host — verified CPython 3.14.5), never re-attached after a
  redirect or a `mirror_url` rewrite. A custom redirect handler rejects any
  non-`https` redirect target and caps hops (urllib otherwise permits
  https→http and even ftp downgrades). URLs carrying userinfo
  (`https://user:pass@host/`) are rejected. `headers` remains the low-level
  escape for exotic proxy schemes. Never persisted.
- **Manifest field validation** (the manifest is untrusted input, CWE-22):
  every field consumed into a path or URL is validated before use —
  `filename`/`tag`/`target` against `^[A-Za-z0-9._+-]+$`, `version` against
  semver, `sha256` against `^[0-9a-f]{64}$`; separators, `..`, backslashes,
  CRLF rejected. Same defense class as rules_ocx's dist-snapshot rule
  (documented bypass history there).
- **Size caps**: manifest read capped at 1 MiB, artifact at 256 MiB, with
  abort — exact caps switch to the row's `size` once
  [www-setup#15](https://github.com/ocx-sh/www-setup/issues/15) lands.
- Artifact sha256 from the row is always enforced — no knob. `mirror_url`
  (optional, `ensure()` kwarg — single home) rewrites the artifact host to
  `<mirror>/<tag>/<filename>`; hash still enforced. Mirrors relocate bytes,
  never revalidate them.
- HTTPS only, stdlib urllib. Platform detection: uname → cargo-dist triple
  (parameterized on the uname values for unit-testability): musl-first on
  Linux, Rosetta redirect to native arm64 on macOS, Windows →
  `{x86_64,aarch64}-pc-windows-msvc`.
- **Archive handling — never `extractall`** (zip-slip, CWE-22: `tarfile`'s
  `filter=` defaults to `fully_trusted` on 3.12/3.13, our own floor;
  `zipfile` has no filter at all). After the artifact hash verifies, open
  the archive, select the single member matching the expected binary name,
  assert it is a regular file, stream it to the temp fd. No member name
  ever touches the filesystem.

**Default source decision**: live `setup.ocx.sh/dist.json` (floating latest
stable) with docs pushing `dist/<sha256>.json` pins for CI. The SDK is a
library — callers own reproducibility. The compat gate does not punish the
default (§3: newer-than-tested is a DEBUG note).

### ensure()

```python
bootstrap.ensure(
    version: str | None = None,        # exact pin; None = channel latest
    *,
    channel: Channel = Channel.STABLE,
    dist: DistSource | None = None,
    mirror_url: str | None = None,
    min_version: str | None = None,    # operator floor: resolved version below this fails loudly
    cache_dir: Path | None = None,     # default: user cache /<version>/<triple>/ocx
    env: HostEnv | None = None,        # None → HostEnv.ambient()
    trust_cache: bool = False,         # True skips the re-hash on cache hit
    retry: RetryPolicy | None = None,
    timeout: float | None = None,      # per network request
) -> Path
```

- Precedence per knob: explicit kwarg > `OCX_INSTALL_*` from `env` (a
  default-constructed `DistSource` also honors `OCX_INSTALL_DIST_URL` from
  `env`; an explicitly constructed one does not) > default.
- Honors the existing grammar verbatim: `OCX_INSTALL_VERSION`, `_DIST_URL`,
  `_MIRROR_URL`, `_REPO`, `_FORCE`, `_QUIET` (`InstallEnv` StrEnum = the
  catalog).
- Never runs `self setup`, never touches profiles/PATH. Machine setup is
  explicit + post-v0.1: `ocx.self_.setup(...)` (§7).
- **Cache trust** (the one persistent state the SDK owns — hardened,
  CWE-732/59/349): cache root refused if it is a symlink, not owned by the
  current uid, or group/other-writable (`os.lstat`). Directories created
  `mode=0o700` per level (never umask-inherited `makedirs`). **Cache hit
  re-hashes the binary against the manifest row** before returning
  (single-digit ms for ~50 MB); `trust_cache=True` is the explicit opt-out.
  `OCX_INSTALL_FORCE`/kwarg forces re-download.
  **Amendments (WP07)**: (a) the release row's sha256 covers the *archive*,
  so cache-hit re-hash verifies the extracted binary against a digest
  sidecar (`ocx.sha256`, written atomically at install; missing/stale
  sidecar = cache miss) — same trust boundary, the hardened cache root;
  (b) the mandatory `sha256=` applies to URL sources only — `path()`/
  `data()` bytes are caller-held provenance and a self-derived digest
  proves nothing (keeps §17 vendoring usable); (c) `OCX_INSTALL_REPO`/
  `_QUIET` are documented no-ops (honoring `_REPO` would reimplement the
  setup script's URL derivation).
- **Write ordering** (TOCTOU-safe, CWE-367): `tempfile.mkstemp(dir=<final
  dir>)` (0600, unpredictable name, `O_EXCL`, same filesystem) → write →
  **hash from the open fd** → `os.fchmod(fd, 0o700)` → `os.replace`. The
  digest is computed on the exact bytes being installed. Concurrent
  `ensure()` across processes cannot corrupt the cache.
- Idempotent + offline-friendly: verified cache hit needs no network.

### Binary discovery (no bootstrap needed)

`Ocx()` resolution order: explicit `exe` arg > **`OCX_SDK_EXE`** env var
(named here; the recorded exception to §6's no-public-runtime-catalog rule)
> `PATH` lookup (**Windows: CWD excluded** — `shutil.which` inserts it
first by default, CWE-426/427; on all platforms a resolved exe whose parent
directory is group/other-writable is refused) > `$OCX_HOME` stable install
symlink (`…/current/content/bin/ocx`) > raise `OcxNotFoundError` whose
message names `bootstrap.ensure()` as the fix. `exe=` is the hardened form.

### User-side snapshot vendoring

Document vendoring the content-addressed `dist/<sha256>.json` as package
data + `DistSource.data()` (§17 how-to). A snapshot-refresh helper stays
deferred; if built it inherits the full §5 validation set above.

## 6. Host environment model

`HostEnv` lives in `_types.py` (used by both layers):

```python
@dataclass(frozen=True, slots=True)
class HostEnv:
    source: Mapping[str, str]
    @classmethod ambient() / clean() / minimal()
    def only(self, *keys) -> HostEnv
    def without(self, *keys) -> HostEnv
```

`minimal()` = clean + platform-essential passthrough (`PATH`, `HOME`,
`TMPDIR`; `SYSTEMROOT`/`TEMP` on Windows) — the documented recovery for the
`clean()` footgun where spawned tools lose `PATH` and fail mysteriously
(the §17 bootstrap guide shows `clean().only(...)` recipes).

Runtime spawns: composed env = filtered ambient (via `HostEnv`) +
`OcxConfig`-derived vars + `OCX_AUTH_*` prefix passthrough, with
`OCX_PROJECT`/`OCX_GLOBAL`/`OCX_QUIET` always neutralized. Assembly happens
in exactly one function — `_env.build_spawn_env(host, config) ->
dict[str, str]` — which is also the redaction choke point (§12). Typed knobs
live on `OcxConfig`; no public runtime env-var catalog (`OCX_SDK_EXE` is the
sole, recorded exception).

## 7. Runtime layer

### OcxConfig

```python
@dataclass(frozen=True, slots=True)
class OcxConfig:
    home: Path | None = None
    offline: bool = False
    frozen: bool = False
    config: Path | None = None
    no_config: bool = False              # hermetic: kills discovered chain + managed tier
                                         #   (wins over managed_config when both set — ocx's own rule)
    managed_config: str | None = None    # OCI ref; MANAGED_CONFIG_DISABLED (named
                                         #   constant for the wire's "" sentinel) force-disables
                                         #   ambient; None = leave ambient alone
    auth: Mapping[str, Auth] = ...       # wins over ambient OCX_AUTH_* for the same slug
    insecure_registries: Collection[str] | None = None
                                         # None = inherit ambient; explicit value (incl. ())
                                         #   replaces ambient entirely — fail-closed
    docker_config: Path | None = None    # → DOCKER_CONFIG (docs: keep the dir 0700)
    index: Path | None = None
    jobs: int | None = None
    log_level: LogLevel | None = None    # docs: ocx trace level may surface secrets (§12)
    mirrors: Mapping[str, str] | None = None   # → OCX_MIRRORS JSON
    no_update_check: bool = True         # → OCX_NO_UPDATE_CHECK (docker-image precedent)
    no_config_refresh: bool | None = None  # → OCX_NO_CONFIG_REFRESH; None = policy-owner's tier
    retry: RetryPolicy | None = None     # §10
    timeout: float | None = None         # §10; per attempt
```

Construction-time warning when a registry appears in both `auth` and
`insecure_registries` (credentials over plaintext, CWE-319).

### Ocx, Project handle, command-group namespaces

```python
ocx = Ocx(exe=None, config=OcxConfig(), env=HostEnv.ambient(), on_log=None)
# exe=None → discovered once at construction (§5 order), resolved to
# realpath, then FIXED for the handle's lifetime. Construction semantics
# (pinned for with_config correctness): __init__ delegates discovery to a
# factory; derivation uses a private alt-constructor that reuses the
# already-resolved exe — a derived handle can NEVER re-run discovery.
# Namespaces (package/config/index/patch/self_) are @property returning
# throwaway two-field wrappers (parent ref + argv prefix) — no stored
# fields, so no stale-parent bug and nothing for derivation to copy wrong.
# Tradeoff (recorded): a long-lived handle keeps executing a binary that
# `self update` just replaced — security fixes reach NEW handles; daemon-
# shaped consumers reconstruct Ocx() periodically.

ocx.version()        # ocx command → method; reads the PLAIN output — the
                     # documented stable contract (recorded exception to the
                     # --format json pinning rule, §11). Amendment (WP09):
                     # returns str (bare semver) and never trips the compat
                     # gate — probing an unsupported binary must work.
ocx.about()          # typed one-shot (status is project-scoped → Project)
ocx.login(registry, *, username, token)   # wraps login --password-stdin
ocx.logout(registry)
ocx.with_config(**overrides) -> Ocx  # dataclasses.replace on config +
                     # alt-constructor; shares exe snapshot, env, on_log AND
                     # the compat-gate memo cell. exe/env/on_log are not
                     # config: changing those means a fresh Ocx() (stated).

# command groups = stateless namespace @properties mirroring the CLI 1:1:
ocx.config.setup(ref) .update() .test(path) .push(...)
ocx.index.update("a", "b") .catalog() .list() .sync() .regenerate(...)
ocx.patch.sync(platform=...) .publish(...) .test(...) .freeze() .why(...)
ocx.self_.setup(...) .update(check=...)   # trailing underscore: an attribute
                     # literally named `self` reads as a bug against the
                     # universal parameter convention (PEP 8 name-clash
                     # convention; `self` is NOT a Python keyword — prior
                     # rationale corrected)

# raw escape hatch — OcxConfig globals composed in, presentation NOT pinned.
# Named `invoke`: command names are reserved for methods wrapping that command
# (`run` is the toolchain-tier command → prj.run):
ocx.invoke(argv, *, check=True, capture=True, timeout=None, retry=UNSET) -> CommandResult
await ocx.invoke_async(argv, ...) -> CommandResult
ocx.spawn(argv, **popen_kw) -> subprocess.Popen
await ocx.spawn_async(argv, **kw) -> asyncio.subprocess.Process
# Ocx.__getattr__ raises AttributeError with a pointed hint for the two
# known traps: `ocx.run` → "toolchain run is ocx.project(...).run(...); raw
# argv is ocx.invoke(...)"; `ocx.exec` → ocx.package.exec.

prj = ocx.project(path)                # THE stateful handle; every call injects --project <path>
prj.with_config(**overrides)           # delegates to parent derivation, re-wraps
prj.init() prj.add(ref, *, group=...) prj.remove(name)
prj.lock(check=False) prj.update(...) prj.pull(dry_run=False)
prj.status() prj.inspect(...)
prj.env(*names, groups=(), ...) -> EnvReport   # ONE method, full flag surface (§8)
# child execution — wraps `ocx run --project … --`; stdlib split
# (run ≙ subprocess.run one-shot; spawn ≙ Popen live handle):
prj.run(argv, *, env=..., capture=True, timeout=None)  / await prj.run_async(...)
prj.spawn(argv, **popen_kw) -> subprocess.Popen
await prj.spawn_async(argv, **kw) -> asyncio.subprocess.Process

# NO Package object — package ops are namespace methods, multi-identifier
# native (CLI takes PKG... with one shared resolution). Package tier is
# MACHINE-tier: it operates on the $OCX_HOME store (candidate/current
# symlinks), takes no project path, and is CWD-independent by construction —
# ocx's own machine-vs-project split, stated so nobody reads it as
# CWD-dependent. Path appears only where the CLI takes one (create):
ocx.package.install("a", "b", *, select=False) -> InstallReport
ocx.package.select(...) .uninstall(..., purge=False) .deselect(...)
ocx.package.which(...) .env("a", "b") -> EnvReport   .inspect(...) .info(...)
ocx.package.deps(...) .pull(...)
ocx.package.exec(["a", "b"], argv) / .exec_async / .spawn / .spawn_async
ocx.package.create(path, *, platform=..., metadata=..., output=...)
ocx.package.test(identifier, *, script=..., env=...) -> TestResult
ocx.package.push(...) .describe(...) .announce(...)
```

Signature conventions: single obvious arg positional, everything else
keyword-only — the sketches above are normative (`login`'s credentials are
keyword-only; `invoke`'s flags are keyword-only; `package.exec`'s two
sequences are the recorded two-positional exception, mirroring the CLI's
`PKG... -- CMD...`). Per-command arity (`PKG...` vs exactly one) and full
flag surfaces are pinned by the hex-plan signature-alignment checklist
against the inventory, **which also enumerates each command's result-struct
shape** (flags AND returns). `CommandResult` is sketched in §12; other
result structs live in `_results.py` per the checklist.

Rules: stateless frozen handles — **no state caching** (GitPython staleness
as the cautionary tale); the only construction-time snapshot is the resolved
exe realpath, and the only memo is the internal once-per-handle compat gate
(shared across derived handles). Dead aliases are never emitted. Async:
execution verbs only; other typed one-shots stay sync.

## 8. Project/package env composition (ocx's `[env]` grammar)

ocx wire types, mirrored 1:1, living in `_types.py`:

```python
@dataclass(frozen=True, slots=True) class ConstVar: value: str
@dataclass(frozen=True, slots=True) class PathVar:  value: str        # unique prepend
@dataclass(frozen=True, slots=True) class ListVar:  value: str; _: KW_ONLY; separator: str | None = None
type EnvValue = str | ConstVar | PathVar | ListVar   # bare str ≡ ConstVar
```

- Serialize to `--env KEY[:TYPE[:SEP]]=VALUE`. The serializer **rejects `:`
  and `=` in keys and separators** before emitting — the CLI validates
  *values* it can parse, but a hostile key would *misparse* into a different
  variable, and the ambiguity would be created by our own encoder. ocx's
  rejection of `OCX_*`/`__OCX_*` keys is a **load-bearing security
  property** (it stops project-declared env from overriding `OCX_AUTH_*` /
  `OCX_HOME` / `DOCKER_CONFIG` in children) — named as such and
  contract-tested, not treated as saved validation effort.
- `ocx env --format json` emits typed entries — **amendment (WP10): list
  entries carry a `separator` field on the wire** (live-verified 0.5.8;
  earlier fixture captures simply declared none) and `EnvEntry` carries it
  into `compose()`; composition is the consumer's
  job → the SDK implements the merge once (const replaces, path prepends,
  list appends with separator agreement + dedupe) — **carve-out (a) of goal
  4**, guarded by the printenv contract diff. ⚠ Interpolation: v1 docs said
  "every value literal", v0.5.8 unified a `${…}` grammar (BREAKING) —
  **v0.1 decision (plan round): the SDK merges values verbatim and never
  interpolates `${…}`**; the printenv diff arbitrates — a diff failure on
  `${…}` values is a doc-flagged limitation + upstream question, never
  silent SDK-side interpolation. The merge algorithm lives in `_envmodel`;
  `EnvReport.compose()` is a thin delegator (function-local import) — the
  recorded `_results → _envmodel` edge.

One method mirroring the command, one rich result:

```python
report = prj.env(groups=("dev", "ci"))   # amendment (WP09): `ocx env` takes
# no positional NAME... (live-verified); narrowing is groups/flags only
report.entries        # tuple[EnvEntry, ...]; iterating the report iterates entries
report.binaries report.entrypoints report.integrations report.advisories
# full envelope ships in v0.1 (decision 2026-08-21) — the envelope is NOT on
# ocx's durable-anchor list, so binaries/entrypoints/integrations/advisories
# are doc-flagged "pre-1.0, may break" exactly like package which's JSON.

env = report.compose(base=None)   # ComposedEnv. base default = the HostEnv
# snapshot of the PRODUCING call (a value copy carried on the report) — so a
# report produced under HostEnv.clean() composes hermetically; os.environ is
# the base only for reports produced by ambient handles. (Closes the hole
# where compose() silently reintroduced ambient env into a hermetic path.)
env.mapping                            # merged dict — non-invasive, the documented default
with env.activate():                   # process-global convenience
    subprocess.run(["task", "verify"])
```

`activate()` contract (informed by direnv#1112's proven failure mode):
**diff-based revert** — records only the keys it is about to set (prior
value or an absent-sentinel) at `activate()` time, restores exactly those on
exit, **deleting keys that were absent**; unrelated concurrent mutations
survive (contract test: mutate an unrelated var inside the block, assert it
survives exit). Revert runs in `finally`. Process-global and single-owner
(§12); cannot revert across `os._exit`/`execve`/SIGKILL; while active,
every subprocess started by any thread inherits the composed env —
`.mapping` + explicit `env=` is the documented-default form in all examples.
`ocx.package.env(...)` returns the same `EnvReport`.

## 9. Auth

- One vocabulary, both surfaces, in `_types.py`: `BasicAuth(user, password)`
  / `BearerAuth(token)` (`type Auth = BasicAuth | BearerAuth`; None =
  anonymous — ocx's own type set). Secret fields are `field(repr=False)`
  with a masked `__repr__` (`BearerAuth(token=***)`) — the default dataclass
  repr would leak into logs/tracebacks/pytest diffs (CWE-532).
- Registry auth: `OcxConfig.auth` → `OCX_AUTH_<SLUG>_{TYPE,USER,TOKEN}` env
  at spawn, composed only inside `_env.build_spawn_env()`. Slug
  canonicalization ported from ocx `registry_slug` — **carve-out (b) of goal
  4**: one function, contract-tested against CLI fixtures, **fails closed on
  fixture mismatch** (never silent-anonymous). Env-only, never persisted.
- **Blast radius, pinned by fact**: ocx does not scrub non-forwarded vars
  from child envs (ocx_lib env.rs: "non-forwarded is not the same as
  scrubbed") — so a tool under `prj.run` **inherits `OCX_AUTH_*`**. Design
  response: documented prominently in the §17 CI guide; the credential-free
  pattern is `prj.pull()` first, then `run` through
  `with_config(auth={})`; and a contract test asserts the propagation
  behavior so an upstream change to scrubbing breaks loudly (§3 anchor
  list).
- Value sourcing is the caller's job (12-factor): ambient `OCX_AUTH_*`
  passes through with zero config; explicit values come from wherever the
  host app keeps secrets. No env-var-mapping DSL. Explicit `OcxConfig.auth`
  wins over ambient for the same slug.
- Persistent credentials: Docker's domain via `ocx.login()` (ocx writes the
  store; helpers do the securing); `docker_config=` for isolated stores
  (docs: 0700).
- Bootstrap auth: `DistSource.auth` (§5) — same `Auth` types, redirect-safe
  binding.

## 10. Error model

Exit code IS the category — exceptions are the dispatch. No stderr regex.

```python
class OcxError(Exception)              # blanket rule: every __str__ carries an
                                       #   actionable next step, not just a fact
class OcxExecutionError(OcxError)      # shared parent for anything a spawn raised:
    argv: tuple[str, ...]; stderr: str # full text on the attribute; __str__ truncates
class OcxProcessError(OcxExecutionError)
    exit_code: int   # amendment (WP02): int, not ExitCode — a signal-killed
                     #   ocx exits 137 etc.; error construction must not raise.
                     #   Subclass dispatch + retryable use the ExitCode map.
    attempts: int                      # >1 when a retry policy was active
    @property retryable -> bool        # fixed semantics: True for TEMP_FAIL(75) —
                                       #   policy-independent by design (§4 layering guard)
class OcxTimeoutError(OcxExecutionError)   # carries partial stderr; not retried by default
# per-code subclasses of OcxProcessError:
UsageError(64) DataError(65) UnavailableError(69) IoError(74) TempFailError(75)
PermissionDeniedError(77) ConfigError(78) NotFoundError(79) AuthError(80)
PolicyBlockedError(81) DirtyRcBlockError(82)      # exit 1 → OcxProcessError
# non-process branch under OcxError:
BootstrapError: DownloadError, ChecksumMismatchError, DistManifestError,
                UnsupportedPlatformError
OcxNotFoundError, VersionCompatError
```

`except OcxExecutionError` catches process failures AND timeouts — the catch
shape a caller actually wants. Exit-code assignments have moved pre-1.0
(v0.5.3) — the map is re-verified per pinned version (§3 checklist).

### Retry policy (opt-in failover)

Grounding: botocore standard mode / google-api-core `if_transient_error` /
Azure pipeline — retry ONLY transport-transient failures; **auth errors are
never retried anywhere**. ocx already classified (v0.5.3: 429/5xx/timeouts
→ 75; 69 stays non-retryable) — the SDK inherits that taxonomy.
Considered and **rejected as inapplicable** (not deferred): circuit
breakers and retry-budget token buckets — they coordinate persistent
clients against one shared backend and would reintroduce the ambient state
this design bans; a spawn-per-call wrapper has no trip-state scope.

```python
@dataclass(frozen=True, slots=True)
class RetryPolicy:                     # lives in _types.py; driver in _retry.py
    attempts: int = 3
    backoff: float = 1.0; multiplier: float = 2.0; max_backoff: float = 30.0
    max_retry_after: float = 300.0     # server Retry-After honored up to THIS
                                       #   ceiling (not truncated to max_backoff —
                                       #   truncation would hammer a struggling
                                       #   registry early); beyond it: fail, don't wait
    jitter: bool = True                # full jitter (AWS)
    retry_on: frozenset[ExitCode] = frozenset({ExitCode.TEMP_FAIL})
    sleep: Callable[[float], None] | None = None   # injectable clock seam —
                                       #   repo rule: no time.sleep in unit tests
```

- `_retry.py`: a pure `_delays(policy)` generator + two ~15-line drivers
  (sync `time.sleep` / async `asyncio.sleep` — duplicated deliberately, no
  sans-io bridge). Callers pass their own `classify(failure) -> bool`:
  `_process` classifies by `ExitCode` via `retry_on`; `_dist` classifies by
  transport condition (connect/read timeout, connection reset, HTTP
  429/500/502/503/504) — **`retry_on` does not apply to the bootstrap path
  and the docs on `ensure(retry=)` say so** (shared backoff config,
  domain-specific predicate).
- Scoping: session default `OcxConfig(retry=...)` → derived-handle scope via
  `with_config` → per-call `retry=` kwarg (sentinel-default; `None` opts
  out). **Mutating commands (`package push/announce/describe`, `config
  push`, `patch publish`, `login`) default to `retry=None` regardless of
  session config** — a timed-out `login` exits 75, not 80, and re-sending
  credentials on a timeout is the exact auth-retry mistake the taxonomy
  argument warns about; per-call explicit `retry=` still wins for callers
  who know their registry is idempotent-safe.
- Never applies to `run`/`exec`/`spawn` child processes. Checksum mismatch
  and 401/403/404 never retried. `timeout` is **per attempt** — worst case
  `attempts × timeout + Σbackoff`, stated in docs.
- Exhaustion re-raises the final error with `attempts` set.

### Timeouts & cancellation

- `timeout: float | None` per call + `OcxConfig(timeout=...)` default.
  POSIX enforcement: `communicate(timeout)` → `terminate()` → grace →
  `kill()` on the child's process group. Expiry raises `OcxTimeoutError`
  (argv + partial stderr). Not retried by default.
- **Windows branch (stated — the two goals genuinely conflict there)**:
  `capture=False` passthrough does NOT set `CREATE_NEW_PROCESS_GROUP`, so
  Ctrl-C reaches the child naturally (CTRL_C_EVENT cannot be scoped to a
  group — it broadcasts). Timeout enforcement on Windows is the degraded
  path: `TerminateProcess` only, no graceful phase. `capture=False` +
  `timeout` together on Windows = documented degraded combination.
- Async cancellation: task cancelled inside `*_async` terminates the child
  before propagating (CPython asyncio does not — gh-88050); the raised
  `CancelledError` context carries the same partial-stderr capture as the
  timeout path.
- SIGINT forwarding applies to `capture=False` one-shots (POSIX process
  group; Windows per above). `spawn`/`spawn_async`: no SDK timeout — caller
  owns the handle. **Orphan non-goal**: children of a SIGKILL'd parent keep
  running (no `PR_SET_PDEATHSIG`/Job Objects in v0.1 — documented, §1).

## 11. Typing & parsing conventions

- **Zero runtime dependencies** — enforced: `dependencies = []`; metadata
  test; import-walk test (subprocess import, all modules ∈
  `sys.stdlib_module_names` ∪ `ocx_sdk`). Future exceptions only as extras.
- JSON results: hand-written frozen `slots` dataclasses + `from_dict` in
  `_results.py`, unknown keys ignored, one recorded-fixture test per parser.
  No pydantic/msgspec/attrs.
- File reads via tomllib/json + published schemas. Write side: never
  ([ocx#326](https://github.com/ocx-sh/ocx/issues/326)). `ocx.lock` never
  touched.
- **Identifier interop**: `PackageRef` (in `_types.py`) — verbatim
  identifier + metadata **carried from ocx JSON, never parsed/synthesized**.
  Every package-referencing result row exposes `.ref`; every identifier
  parameter is `PackageLike = str | PackageRef` coerced via `str()`.
  **Stated, tested guarantee: `str(PackageRef)` round-trips the identifier
  byte-for-byte** (a result ref flows into the next call's argv unchanged —
  named round-trip test, §14). Argv safety: a positional identifier starting
  with `-` is rejected on every typed positional group, and `--` is emitted
  before child-argv groups (`run`, `package exec` — the only groups the CLI
  documents it for; amended WP09) (clap argument-injection guard, CWE-88) —
  one helper in `_process` argv composition. No collection classes.
- Enums only where runtime iterates/maps (`ExitCode`, `Channel`,
  `InstallEnv`); `Literal` aliases when the value only travels into argv
  (`LogLevel`). Open sets stay `str`. (`Color` dropped — `--color never` is
  pinned, nothing consumes a color type.)
- Kwargs: single obvious arg positional, rest keyword-only (recorded
  exceptions in §7).
- Presentation (`--format json --color never`) pinned on typed methods
  only — with the recorded exception of `version()`, which reads the plain
  stable contract. Raw `invoke`/`spawn` paths leave presentation alone.
- `py.typed` marker ships in the wheel (PEP 561).
- Experimental CLI surfaces (`env --ci=gitlab`, `--export-file`) are
  excluded from typing — the "full flag fidelity" rule is scoped to
  **stable, resolution-affecting** flags, in both this doc and
  architecture.md (reconciled).

## 12. Logging & execution interface

stdout = JSON payload; stderr = ocx tracing log. Three layers:

1. Default: capture; full stderr on `CommandResult` AND every
   `OcxExecutionError` (attribute holds all of it; `__str__` truncates);
   each line at DEBUG to `logging.getLogger("ocx_sdk.process")`; SDK events
   (argv, exit, duration) at DEBUG on `"ocx_sdk"`.
2. Streaming: `Ocx(on_log=callable)` — line-buffered stderr pump thread per
   spawn (sync paths). **v0.1 rejects `on_log` on `*_async` paths with an
   error** — the async pump would fire the callback on the event loop where
   a blocking callback stalls everything; a deliberate async contract comes
   later rather than an accidental one now.
3. Passthrough: `prj.run(..., capture=False)` / `package.exec(...,
   capture=False)` inherit stdio; `spawn`/`spawn_async` return stdlib
   `Popen`/`asyncio.subprocess.Process` — pipes/signals/wait/kill belong to
   the caller (classic PIPE-without-draining deadlock called out in docs).
   **`spawn` kwargs are filtered, not passed blind**: `args`, `shell`,
   `executable`, `env` are rejected (they would defeat env neutralization
   and the frozen exe); `shell=False` is an SDK invariant.

**Secret hygiene** (with §9): `_env.build_spawn_env()` is the redaction
choke point — every configured secret value is exact-string-redacted from
captured stderr, `on_log` lines, and exception text before they leave
`_process`. argv is logged redacted the same way (a caller putting a token
into `invoke` argv is documented-against but still scrubbed). Docs state
`log_level` at ocx trace verbosity may surface secrets ocx itself prints.
**Seam (pinned, plan round)**: `build_spawn_env(host, config) ->
SpawnEnv(mapping: dict[str, str], redact: Callable[[str], str])`; `_process`
takes `redact` as an explicit parameter (identity default) — no
module-global redaction state.

```python
@dataclass(frozen=True, slots=True)
class CommandResult:
    argv: tuple[str, ...]
    exit_code: int      # amendment (WP02): int, not ExitCode — see §10
    stdout: str        # raw JSON text; typed methods hand you parsed objects
    stderr: str        # full captured log (redacted)
```

### Concurrency guarantees

- Handles frozen + cache-free → safe across threads, tasks, loops. Every
  call composes its own argv/env; env source snapshotted per call
  (`dict(source)` at spawn).
- Compat memo: benign-race double-check, shared cell across derived handles.
  No loop references stored.
- `on_log` fires from concurrent pump threads, unserialized — callback must
  be thread-safe (stdlib `logging` qualifies).
- `ComposedEnv.activate()` is process-global and single-owner (§8 contract);
  concurrent code uses `.mapping`. No lock — serializing would fake safety
  while nesting semantics stay wrong.
- `$OCX_HOME` store concurrency across processes is ocx's locking domain.

Execution verb scheme (uniform): one-shot named after the CLI command it
wraps (`invoke` for the raw hatch — deliberately not a command name),
`_async` twin, plus `spawn`/`spawn_async` for live handles — the stdlib's
`subprocess.run` vs `Popen` split. `.aio` namespace is the escape if the
async surface outgrows a handful of methods.

## 13. Support matrix (from full command inventory)

Tier legend — **T1**: typed method, v0.1, CI-covered · **T2**: typed, post-v0.1
· **T3**: `invoke()` passthrough only, unsupported · **✗**: never wrapped.

| Area | T1 | T2 | T3 / ✗ |
|---|---|---|---|
| Meta | `version`, `about`, `status` | — | dead stubs (`shell hook/env/init`, `ci*`, bare aliases) ✗ |
| Toolchain | `lock(--check)`, `update`, `pull`, `env`, `run`(+async/spawn), `init`, `add`, `remove`, `inspect` | `clean` | `direnv*` T3 (needs human `direnv allow`) |
| Package (consumer) | `package install/select/uninstall`, `package env`, `package exec`, `package which`†, `package inspect`, `package info`, `package pull`, `package deps`, `package deselect` | — | — |
| Package (author) | `package test --script` (stable v1 JSON), `package create`, `package push` | `package describe`, `package announce` | `package cascade *` T3 (not a frozen wire contract) |
| Config | `config setup`, `config update` | `config test`, `config push` | — |
| Index | — | `index update/catalog/list/sync/regenerate` (whole group) | — |
| Auth | `login`(--password-stdin), `logout` | — | — |
| Self | — | `self_.setup`, `self_.update` | `self activate`, `shell completion` T3 (shell-session tools) |
| Patch | `patch sync` | `patch publish`, `patch test`, `patch freeze`, `patch why` | — |

† `package which` JSON marked "breaking, pre-1.0" — typed, doc-flagged.
`EnvReport`'s non-entry accessors carry the same flag (§8).
`--dry-run`/`--check` flags surface as kwargs wherever the CLI has them.

## 14. Testing strategy

| Tier | Needs | Runs |
|---|---|---|
| Unit | no binary; recorded JSON fixtures per parser; zero-dep gates (§11); secrecy tests; clock-injected retry/timeout tests; mocked-spawn composition thread-safety | every PR, Python 3.12/3.13/3.14 × Linux/macOS/Windows |
| Contract | pinned binary, no network | every PR, Linux; macOS/Windows gated behind Linux (CI cost rule) |
| Acceptance | docker compose: `registry:2` plain + **htpasswd-auth registry (v0.1** — the auth surface is T1, so an authenticating fixture ships with it**)**; real pull/publish flows | main + nightly + pre-release |

### Mechanism → named-test matrix

Every design mechanism has a named test; a mechanism without a row fails
design review (systemic rule from this round). v0.1 rows:

| Mechanism | Test (named) | Tier |
|---|---|---|
| Env merge (§8) | `test_env_compose_matches_printenv` — diff vs `ocx run -- printenv` | contract |
| `OCX_*` key rejection (load-bearing) | `test_ocx_keys_rejected_in_project_env` | contract |
| `OCX_AUTH_*` child propagation (§9) | `test_auth_env_propagation_pinned` | contract |
| Exit-code map (§10) | `test_exit_code_taxonomy_fixtures` | contract |
| Slug port (§9) | `test_registry_slug_fixtures` (mismatch fails closed) | contract |
| Typed-result shapes vs pinned binary | `test_t1_result_shape_smoke` — every T1 method once, parsed field set asserted | contract |
| Shared-handle concurrency | `test_shared_handle_spawn_smoke` (N threads × M calls, real binary) | contract |
| Kill escalation | `test_timeout_kill_ladder` (hanging-subprocess fixture) | contract |
| SIGINT forward | `test_sigint_forwarding` (injected signal seam, `tests/unit/test_process.py`) | unit (contract: permanent skip, reason recorded) |
| Log-pump completeness | `test_pump_captures_all_lines` | contract |
| `activate()` revert (§8) | `test_activate_diff_revert` (absent-key delete; unrelated mutation survives; exception inside block) | unit |
| Retry backoff/jitter/`Retry-After` | `test_backoff_sequence`, `test_retry_after_ceiling` (injected sleep — no real `time.sleep`, repo rule) | unit |
| Secrets never leak | `test_secrets_absent_from_repr_logs_errors` | unit |
| `PackageRef` round-trip | `test_packageref_byte_roundtrip` | unit |
| Argv injection guard | `test_leading_dash_rejected`, `test_double_dash_emitted` | unit |
| Cache trust (§5) | `test_cache_rehash_on_hit`, `test_cache_refuses_untrusted_root` | unit |
| Manifest validation (§5) | `test_manifest_field_validation` (traversal corpus) | unit |
| Redirect auth binding (§5) | `test_auth_not_forwarded_cross_host` (local HTTP fixture) | unit |
| Archive single-member extraction | `test_archive_streams_single_member` (slip corpus) | unit |
| Triple detection | `test_triple_mapping` (uname parameterized — no platform pragma needed) | unit |
| Zero-dep + concurrency composition | §11 gates; mocked-spawn thread test | unit |
| `insecure_registries` fail-closed (§3,§7) | `test_config_fail_closed_insecure_registries` (explicit `()` beats ambient) | unit |
| Compat gate (§3) | `test_compat_gate_below_min_raises`, `test_compat_gate_newer_debug_note` | unit |
| Off-canonical `sha256=` required (§5) | `test_sha256_required_off_canonical` (refuses pre-network) | unit |
| Mutating commands retry-off (§10) | `test_mutating_commands_retry_disabled` (session policy ignored) | unit |
| `version` plain contract (§3 anchor) | `test_version_plain_output` | contract |
| `--project` global flag (§3 anchor) | covered by `test_t1_result_shape_smoke` (every Project call injects it) | contract |
| `login --password-stdin` (§3 anchor) | `test_login_password_stdin` (htpasswd registry) | acceptance |
| `package test --script` JSON (§3 anchor) | `test_package_test_envelope` (recorded WP00 fixture + parser) | unit+acceptance |
| Install-symlink discovery (§3 anchor) | `test_discovery_ocx_home_symlink` (fake `$OCX_HOME` tree) | unit |

§3 anchors not exercised by the v0.1 surface (`launcher exec` wire ABI,
file-schema URLs — no file-read features ship in v0.1) are marked n/a on the
checklist until the consuming feature lands.

Platform pragmas named up front: the wrong-OS branches of signal handling
(§10 Windows path) and Rosetta redirection carry `# pragma: no cover` with
reasons; triple detection avoids pragmas entirely via parameterization.

- Version seam: `OCX_TEST_VERSIONS` env → parametrized session fixture;
  provisioning = `bootstrap.ensure()` itself. Canary sets `latest`.
- Signing/OIDC registry stack: out of scope, compose `--profile signing`
  seam reserved.
- Test-package factory built on the author API (create → push local →
  consume) — author e2e + realistic consumer fixtures.

### Doc-snippet testing (cargo-doctest style)

Every code block in the docs is tested; docs rot is a test failure.
Docstring examples run via Sybil's `DocTestParser` — not stdlib doctest's
`--doctest-modules`, which would double-collect the same blocks and is not
enabled here; a ` ```python ` fence inside a `src/**/*.py` docstring is
illustrative API-doc content and is compile-checked only, never run against
a real binary (WP11 decision). Markdown fences via Sybil (dev dep). **Four
markers**: default = runs pure (unit);
`contract` = runs with the pinned-binary fixture (e.g. `prj.status()`,
`ensure()` examples); `acceptance` = needs the compose stack; `no-run` +
reason = compile-check only. Unmarked-but-unrunnable = failure. The
corporate-mirror and managed-config guide pages will lean `no-run`
(unreachable corporate refs) — stated expectation, not a rigor failure.
README fences are included in the Sybil net explicitly (§17).

### CI integration

Per `.claude/rules/subsystem-ci.md` (Taskfile single source; SHA-pinned
actions; minimal permissions; concurrency blocks):

| Workflow | Trigger | Jobs |
|---|---|---|
| `ci.yml` | every PR + main | `task verify` + unit tier matrix 3.12/3.13/3.14 × 3 OS (uv pin per entry; coverage `fail_under = 100` inside `task test`) + contract tier (Linux; macOS/Windows contract gated behind Linux) |
| `acceptance.yml` | main + nightly + pre-release tags | compose stack (plain + htpasswd), `task test:acceptance`; `OCX_TEST_VERSIONS` seam |
| canary (job in `acceptance.yml`) | nightly | `OCX_TEST_VERSIONS=latest`, allow-fail |
| 3.15-dev (job in `ci.yml`) | every PR, allow-fail | once 3.15 betas ship |
| `release.yml`, `docs.yml` | existing scaffold | unchanged |

Tiers as Taskfile tasks: `test`, `test:contract`, `test:acceptance`.

## 15. Scaffold changes (with floor decision)

`requires-python = ">=3.12"`; pyright `pythonVersion = "3.12"`; CI matrix
per §14; coverage `fail_under = 100`; `.claude/rules/quality-python.md`
bumped to 3.12 **and its CLAUDE.md catalog line** ("Python 3.13+" → 3.12);
`architecture.md` gets `paths:` frontmatter matching the other rules
(`src/**`, `tests/**`, `pyproject.toml`); `py.typed` in the package;
3.15-dev allow-fail job once betas start.

## 16. v0.1 scope (walking skeleton, both personas)

`bootstrap.ensure()` + `DistSource` (url/path/data) + discovery + cache
hardening · `HostEnv` (ambient/clean/minimal) + `InstallEnv` · errors +
`ExitCode` + retry (`RetryPolicy`, `_retry.py`) + timeouts · `Ocx`
(`version`, `about`, `invoke`, `invoke_async`, `spawn`, `spawn_async`,
`login`, `logout`, `with_config`) · `ocx.config` namespace (`setup`,
`update`) · `Project` (`init`, `add`, `remove`, `lock`, `update`, `pull`,
`status`, `inspect`, `env`, `run`, `run_async`, `spawn`, `spawn_async`,
`with_config`) · `ocx.package` namespace — consumer (`install`, `select`,
`uninstall`, `deselect`, `env`, `exec`, `exec_async`, `which`, `inspect`,
`info`, `deps`, `pull`) + author (`create`, `test`, `push`) ·
`ocx.patch.sync` · `EnvReport` full envelope (non-entry accessors
doc-flagged) + `ComposedEnv` · logging layers 1–3 · unit + contract tiers +
acceptance compose (plain **+ htpasswd**).

Deferred (non-exhaustive; §13 is the authority): `self_` group, `clean`,
`config test/push`, `package describe/announce`, the whole `index` group,
`patch` group except `sync`, pytest plugin (note below), PyO3 exploration,
snapshot-refresh helper, async `on_log`, orphan-proofing opt-in.

### Deferred feature note: pytest plugin

Surface `ocx package test` runs as first-class pytest items, so package
authors run their ocx assertion scripts inside a normal Python test suite
instead of shell loops.

- Shape: entry-point plugin (pytest stays the user's dev dependency —
  zero-dep policy untouched). Author declares package dirs/platforms;
  plugin collects one pytest item per ocx package test.
- Each item invokes `ocx.package.test()`; pass/fail from the stable v1
  envelope — `status` decides, `assertion.kind` is the reason (the stable
  machine field; `assertion.message` prose is not stable).
- Value: `-k` selection, parametrize over platforms, fixtures composing
  test registries, JUnit XML.
- Why deferred: needs typed `test()`/`TestResult` first; collection API
  deserves its own pass; nobody blocked — `ocx.package.test()` in a plain
  pytest function delivers most of the value day one.

## 17. Documentation deliverables (v0.1, MkDocs site on GitHub Pages)

One site, two Material tabs. `docs.yml` deploys on push to main; **Pages
(source: GitHub Actions) is already enabled and deploying** — CLAUDE.md's
description is current, no owner action pending.

**Guide** (task-oriented, persona-split):
- Quickstart — **the canonical CI journey as one worked snippet** (ensure →
  project → install → run), including the two vocabulary callouts: raw argv
  is `invoke()` (there is no `ocx.run`), and package-tier commands are
  machine-tier (no project path).
- Consumer guides: bootstrap (pinning, corporate mirror + auth — sha256
  required off-canonical, `HostEnv` tiers incl. the `clean()`/`minimal()`
  recovery pattern); project toolchains (`Project`, env composition,
  `run`/`spawn`); managed config in CI; **hermetic CI recipe** (threat-model
  levers, §3).
- Author guides: create → test → push; the test envelope.
- How-to: vendor a dist.json snapshot in your Python package
  (content-addressed snapshot → package data → `DistSource.data()`; the
  filename is the refresh integrity check).
- Concepts: **ocx compatibility** (tested window, canary, what "stable"
  means pre-1.0) and — separately — **ocx-sdk's own compatibility**
  (pre-1.0, no shims, breaks announced via the git-cliff CHANGELOG);
  **concurrency & thread-safety** (frozen handles, `activate()`
  single-owner, `spawn` orphan non-goal, PIPE-draining); **timeouts,
  retries & cancellation** (per-attempt semantics, mutating-command
  defaults, Windows degraded path); `with_config` scoping; `PackageRef`
  interop; error model (every error names its next step); credential
  handling (blast radius of `OCX_AUTH_*` under `run`, `docker_config`
  0700, trace-level caveat).

**Reference** (SDK-shaped, exhaustive):
- API reference via mkdocstrings (google-style docstrings,
  doctest-verified examples).
- Command ↔ method mapping table generated from §13 (tier per command).
- Env-var and exit-code tables as the SDK sees them.
- Contributing + Changelog (kept from the current nav, placed here).

**README** (the PyPI landing page — a deliverable, not an afterthought):
quickstart snippet + link farm; the "scaffolding only" line drops when
v0.1 ships; **README code fences are collected by the Sybil suite** (§14)
so the landing example can never rot.

All snippets under the four-marker doc-snippet net (§14).

## 18. Open questions

1. ~~Default DistSource~~ → live fetch + pins (§5, decided).
2. ~~Upstream `env set`/`config set`~~ → filed:
   [ocx#326](https://github.com/ocx-sh/ocx/issues/326); write-side lands
   when it does.
3. ~~Package handle~~ → resolved: no Package object (§7).
4. ~~`compose()` base semantics~~ → resolved: producing call's HostEnv
   snapshot (§8).
5. Upstream signing + size:
   [www-setup#14](https://github.com/ocx-sh/www-setup/issues/14),
   [www-setup#15](https://github.com/ocx-sh/www-setup/issues/15) — SDK
   adopts both when they land (drop the off-canonical sha256 requirement in
   favor of signature verification; switch size caps to exact).
6. `ocx env` envelope stabilization upstream — until then the non-entry
   `EnvReport` accessors stay doc-flagged (§8).

## 19. Decision log (chronology)

Wrapper over reimplementation → installer/executor split → kwargs over
builders → Project handle (Pulumi Workspace/Stack analog) → no state caching
→ StrEnum-vs-Literal rule → env-only auth + docker delegation → exit-code
exception map → HostEnv unification → ConstVar naming + kw-only separator →
3.12 floor → logging three layers → stdlib process handles → ComposedEnv
activate/revert → discovery-first (bootstrap optional) → author persona →
acceptance tiers + version-matrix seam → inspection commands to T1 → `patch
sync` T1 → raw quartet + `run→invoke` rename (command names reserved) →
`_async` suffix → one-command-one-method + full flag fidelity + EnvReport →
`PackageRef` carry-don't-parse → unified `Auth` vocabulary +
`insecure_registries` → retry primitive (transport-transient only; circuit
breakers/retry budgets **rejected as inapplicable**, not deferred) →
per-call/handle/session retry tiers via `with_config` → concurrency
guarantees → zero-dep enforcement → TDD + regression-test-first +
`fail_under=100` → doc-snippet suite (now four markers) → Guide/Reference
split → timeouts/cancellation + Windows degraded path → `py.typed` →
cache hardening + manifest validation + redirect-safe auth (review round 1)
→ secrets `repr=False` + redaction choke point → shared vocabulary leaf
(`_types`/`_results`) + underscored modules + curated `__init__` →
`with_config` alt-constructor + namespace properties → `self_` namespace
(keyword claim corrected — PEP 8 name-clash convention) → mutating commands
retry-off by default → `Retry-After` own ceiling → diff-based `activate()`
revert (direnv#1112) → `compose()` hermetic base → EnvReport full envelope
doc-flagged (decision) → latest-default + warn-below-MIN-only (decision) →
htpasswd into v0.1 → mechanism→test matrix instituted →
`compose().activate()` two-hop chain kept (recorded tradeoff: the chain is
two distinct concepts — a report and a live env — collapsing them would
violate one-struct-per-command in the other direction).
