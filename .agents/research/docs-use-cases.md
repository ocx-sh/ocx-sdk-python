# Docs use cases and page inventory (ocx-sdk-python)

Plan decision D10 of [plan_phase3-pilots](https://github.com/ocx-sh/website/blob/main/.agents/plans/plan_phase3-pilots.md).
Product shape (docs-plan): **library, wrapper over a CLI**. A first-steps page needs a
precondition sentence (an `ocx` binary, or network for `bootstrap.ensure()`) above its first fence.
Refactor, not rewrite: reference stays, how-to/tutorial/explanation pages are added around it.

## 1. Comparable sites surveyed (fetched 2026-10-07)

| Site | What it does well | Take for ocx-sdk |
|---|---|---|
| [boto3](https://docs.aws.amazon.com/boto3/latest/guide/) | Quickstart + one sample tutorial, then a "Code Examples" section organised per task (create alarm, send message), then a concept guide (credentials, session, retries, error handling, paginators), migration pages | Per-task how-tos named by job; separate concept guide for credentials/errors/retries (already exist as explanation) |
| [httpx](https://www.python-httpx.org/) | Sections Introduction / Advanced / Guides (async, logging, troubleshooting, requests-compat) / API; leads with "sync and async, strict timeouts, fully typed" | Lead the landing with the same three traits (typed, async, timeouts); add a troubleshooting page |
| [uv](https://docs.astral.sh/uv/) | Getting started / Guides / Concepts / Reference; guides are task-named ("Running scripts", "Using tools"), a migration group ("From pip to a uv project"), integration guides (Docker, GitHub Actions, FastAPI) | Group by Guides (tasks) vs Concepts; per-integration guides (GitHub Actions, pytest); a "from subprocess" migration page |
| [pydantic](https://pydantic.dev/docs/validation/latest/get-started/) | Get started, Concepts, Integrations, a migration guide; redirect moved the docs, so keep old URLs redirecting (Bunny legacy.json) | Migration page for people shelling out; keep old GitHub Pages URLs redirecting |
| [pip-tools](https://pip-tools.readthedocs.io/en/stable/) | Reproducibility is a recurring concern: a "Maximizing reproducibility" section, hash-checking mode, pre-commit/CI integration, "results differ per environment" honesty | Reproducible CI is the headline use case; state limits (ocx.lock digests, platform) as pip-tools does |
| [mise](https://mise.jdx.dev/) | Pitch is tools + env + tasks in one file; "Coming from asdf?" page; CI via `mise-action` | A "Coming from ..." entry for scripts that call `ocx` via subprocess; link `setup-ocx` for CI |
| [asdf](https://asdf-vm.com/guide/getting-started.html) | Precondition per install method, shell-by-shell setup, version-lookup hierarchy explained, working Node example | Spell out the binary-discovery order (OCX_SDK_EXE, PATH, bootstrap) as an explanation, with one worked example |

Not usable: nixos.org/guides returned 404; Nix is therefore not cited.

Patterns across the set: (a) task-named guides beat feature-named ones (uv, boto3 examples);
(b) one short quickstart plus a concepts group; (c) a migration/"coming from" page where
users already have a workaround; (d) reproducibility and CI get their own pages (pip-tools,
mise, uv); (e) sync+async+typed stated on the landing (httpx).

## 2. Real problems the SDK solves

Derived from the public API (`src/ocx_sdk/_client.py`, `__init__.py`) and the existing guides/tests.

1. **Drive ocx from Python without parsing CLI output.** One typed method per command
   (`Ocx.version/about/package.*`, `Project.lock/pull/exec/env/status`), frozen result structs
   (`EnvReport`, `StatusReport`, `InstallReport`), exit-code-mapped exceptions (`OcxError` tree). Alternative today: `subprocess.run(["ocx", ...])` plus hand-rolled JSON/exit handling.
2. **Run a pinned toolchain from a script or test.** `ocx.project(dir).pull()` then `.exec([...])`
   or `.env().compose().mapping` to hand the composed `[env]` to your own subprocess. No host
   install of task, uv, node, etc.
3. **Reproducible CI.** `bootstrap.ensure()` downloads, verifies and caches a pinned `ocx`; `ocx.lock` pins tools by digest;
   `lock(check_only=True)` fails the build on drift; `HostEnv.clean()`, `Ocx(exe=...)`, `DistSource(sha256=)` harden trust inputs (existing [hermetic-ci](../../docs/guide/hermetic-ci.md)).
4. **Cross-platform binaries.** `lock/update/pull(platform=...)` resolve for a target other than the host; `bootstrap` picks the right archive per OS/arch; vendored `dist.json` for air-gapped mirrors (`mirror_url`, `ca_bundle`).
5. **Typed, async, safe to share.** `invoke_async/exec_async/spawn_async`, frozen thread-safe handles, `py.typed`, zero runtime dependencies, 100% unit coverage. Fits asyncio services and pytest fixtures.
6. **Publish packages from Python.** `package.create/push/sign/verify/attest/sbom`, `cascade_check/repair` for release automation (existing [authoring](../../docs/guide/authoring.md)).

## 3. User needs (top tasks, in reader words)

- "Run my build/test tool at the exact pinned version, inside pytest or a script."
- "Make CI install ocx itself reproducibly, then my toolchain, and fail if the lock drifted."
- "Get the composed environment as a dict to pass to my own subprocess."
- "Resolve/pull for linux-arm64 from a macOS laptop."
- "Use it from asyncio without blocking; time out and retry sanely."
- "Know what went wrong: which exception, which exit code, what to do next."
- "Replace my `subprocess.run(['ocx', ...])` wrapper."
- Friction to verify by a walk-through (docs-plan friction log): the binary must exist before the first call; `ocx.exec` does not exist (use `project.exec`); `Project` is not constructed directly.

## 4. Proposed page inventory (doc_type / doc_tier)

Existing pages keep their declared type/tier unless noted. Sidebar groups: Start / Guides / Concepts / Reference, so tier one and everyday sit in different nav groups (docs-plan "nav break").

| Page | Path | doc_type | doc_tier | Action |
|---|---|---|---|---|
| Landing: what, why, three traits (typed, async, no deps), precondition, links by task | `index.md` | landing | first-steps | rewrite lead; keep Pre-1.0 notice |
| Quickstart: install, bootstrap, `ocx.version()` visible result, <=4 fences | `guide/quickstart.md` | tutorial | first-steps | trim; the CI journey moves to a use-case page. Re-type to `quickstart`-style how-to only if the walk-through fails the tutorial contract (no "or with pip" branch) |
| Run a pinned toolchain from a script | `guide/run-pinned-tools.md` | how-to | everyday | NEW (problem 2) |
| Test against a pinned tool in pytest | `guide/pytest-fixture.md` | how-to | everyday | NEW (problems 2, 5) |
| Reproducible CI with GitHub Actions | `guide/reproducible-ci.md` | how-to | integration | NEW (problem 3); absorbs the "canonical CI journey"; links hermetic-ci |
| Resolve and pull for another platform | `guide/cross-platform.md` | how-to | everyday | NEW (problem 4) |
| Use the async API | `guide/async.md` | how-to | everyday | NEW (problem 5) |
| Replace a hand-rolled `subprocess` wrapper | `guide/from-subprocess.md` | how-to | everyday | NEW (migration, pydantic/uv pattern) |
| Projects & toolchains | `guide/projects.md` | how-to | everyday | keep; shrink the lifecycle table into reference |
| Bootstrap | `guide/bootstrap.md` | how-to | everyday | keep; add binary-discovery order |
| Hermetic CI | `guide/hermetic-ci.md` | how-to | integration | keep |
| Authoring packages | `guide/authoring.md` | how-to | everyday | keep |
| Vendoring a `dist.json` | `guide/vendoring.md` | how-to | integration | keep |
| Troubleshooting (errors by exit code, with next step) | `guide/troubleshooting.md` | how-to | everyday | NEW (httpx pattern); links errors-and-security |
| Compatibility, Concurrency & timeouts, Errors & credentials | `guide/concepts/*.md` | explanation | none (types only) | keep |
| Why a wrapper, not a reimplementation | `guide/concepts/why-a-wrapper.md` | explanation | none | NEW; moves "Why a wrapper" off the landing |
| API reference, Command map, Environment & exit codes, Compatibility checklist | `reference/*.md` | reference | none | keep (generated; edit generators only) |
| Contributing pages | `contributing/*.md` | how-to | everyday | keep |
| Changelog | `changelog.md` | reference | none | keep (included from CHANGELOG.md) |

Counts: 6 new use-case/migration how-tos (>=3 required, C-004), 1 new troubleshooting, 1 new explanation, 0 new tutorials (a tutorial is required only when two or more concepts interact before the tool is useful; the quickstart covers it).

## 5. Delete / merge list

- "Why a wrapper" section of `index.md`: move to `concepts/why-a-wrapper.md`, leave a one-line link.
- "The canonical CI journey" block of `quickstart.md`: move to `reproducible-ci.md` (quickstart keeps only the visible `ocx.version()` result).
- "Two things to unlearn from the CLI" admonitions: keep the `ocx.exec` note, move the package-tier note to `command-map.md`.
- Lifecycle method table in `projects.md` duplicates `command-map.md`: replace with a link.
- No page is deleted outright; no stale page found.

## 6. Constraints for step B

- Edit sources in `docs/` (Sybil collects them, `site/src/content/docs/` is generated and gitignored); use ```` ```python-no-run ```` with a reason or a bound runnable fence; 100% coverage must hold.
- Every page: `<!-- doc_type -->` and `<!-- doc_tier -->` declaration; run `docs-review`.
- Every use-case page cites this note.
