# CLAUDE.md

Guide for Claude Code when working in this repo.

## What this is

`ocx-sdk` is the Python SDK for [OCX](https://github.com/ocx-sh/ocx), the
OCI-registry-backed binary package manager. The repo is currently scaffolding
only — `src/ocx_sdk/__init__.py` exposes `__version__` and nothing else. The
toolchain, quality gate, docs site, and release pipeline are wired and green;
the API lands on top.

Repo name carries the language (`ocx-sdk-python`) so `ocx-sdk-rust` /
`ocx-sdk-go` can sit beside it. The PyPI distribution is `ocx-sdk`, the import
is `ocx_sdk`.

## Public API

| Symbol | Purpose |
|---|---|
| `__version__` | Installed package version, resolved via `importlib.metadata` |

Anything reachable through an underscored module path is package-private. When
real modules land, add an `architecture.md` rule with the module map and keep
this table in sync.

## Toolchain bootstrap (OCX dogfood)

This repo manages its own dev toolchain through OCX. Install OCX once:

```bash
curl -sSL https://setup.ocx.sh | sh
```

`ocx.toml` declares `task`, `uv`, and `git-cliff`. Run every command through
`ocx run`:

```bash
ocx run -- task verify    # format check + lint + types + tests + coverage
ocx run -- task test
ocx run -- task format    # apply ruff formatter
```

Python linters (`ruff`, `pyright`) live in `[project.optional-dependencies] dev`
in `pyproject.toml` — `uv` pulls them at sync time.

Optional shell convenience: `eval "$(ocx env --shell=sh)"` once per session,
then drop the `ocx run --` prefix.

## Build & dev commands

| Task | Purpose |
|---|---|
| `task verify` | Full quality gate — run before commit |
| `task test` | pytest under coverage |
| `task lint` | ruff check |
| `task types` | pyright |
| `task format` | apply ruff formatter |
| `task format:check` | check formatting (used by verify) |
| `task docs:serve` | live-reload docs at localhost:8000 |
| `task docs:build` | strict MkDocs build into `site/` |
| `task changelog` | Regenerate `CHANGELOG.md` from git history (git-cliff) |
| `task release:prepare` | Next version via git-cliff + bump + changelog + verify |

## Distribution

Not published yet. On the first `vX.Y.Z` tag, `.github/workflows/release.yml`
validates tag ↔ `pyproject.toml`, runs verify, builds wheel + sdist via
`uv build`, uploads both as GitHub Release assets, and publishes to PyPI via
Trusted Publishing (OIDC, GitHub `pypi` environment, no stored tokens). The
PyPI trusted publisher must be registered before that first tag.

Release flow: `ocx run -- task release:prepare` (interactive menu, or
`BUMP=auto|patch|minor|major`, or `VERSION=X.Y.Z`). It computes the next version
from conventional commits via git-cliff, bumps `pyproject.toml` (`uv version`)
and the `~=` install snippets, regenerates `CHANGELOG.md`, and runs
`task verify`. Review, commit `chore(release): vX.Y.Z`, tag,
`git push --atomic origin main vX.Y.Z`.

`CHANGELOG.md` is git-cliff-generated (`cliff.toml`) — never edit it by hand.
`docs/changelog.md` embeds it into the docs site via `include-markdown`.

## Docs

MkDocs Material, deployed to GitHub Pages at
<https://ocx-sh.github.io/ocx-sdk-python/> by `.github/workflows/docs.yml` on
every push to `main`. No custom domain.

## Stability

Pre-1.0. Breaking changes ship without migration shims. No deprecation
warnings, no compatibility layers.

## Rule catalog

- `.claude/rules/quality-core.md` — universal design principles (SOLID, DRY, KISS, YAGNI)
- `.claude/rules/quality-python.md` — Python 3.13+ quality
- `.claude/rules/quality-tests.md` — pytest, fixtures, mocking standards (auto-loads on `tests/**`)
- `.claude/rules/quality-security.md` — security-sensitive change checklist
- `.claude/rules/subsystem-ci.md` — GitHub Actions conventions
- `.claude/rules/subsystem-taskfiles.md` — Taskfile conventions
- `.claude/rules/workflow-intent.md` — work-type router (start every task here)
- `.claude/rules/workflow-{feature,bugfix,refactor,git}.md` — workflow recipes

## Core principles

1. **Understand first** — read before write, grep before create.
2. **Prove it works** — failing test before fix; `task verify` green before commit.
3. **Keep it safe** — no secrets in code; validate external input at boundaries.
4. **Keep it simple** — small functions, single responsibility, delete dead code.
5. **Don't repeat yourself** — extract only when duplication is real, not incidental.
6. **Ship it** — work on branches, never push to main without review.
7. **Leave a trail** — name things so the next person understands.
8. **Learn and adapt** — turn recurring feedback into rule updates.
