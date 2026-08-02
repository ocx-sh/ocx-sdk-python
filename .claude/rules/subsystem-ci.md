---
paths:
  - .github/workflows/**
  - .github/actions/**
  - .github/dependabot.yml
---

# CI Subsystem

GitHub Actions workflows for `ocx-sdk`. Lint, types, tests, and release wheel artifacts.

## Design Principles

### 1. Taskfile is the single source of truth

Every check dev runs locally MUST be a Taskfile task. CI calls `task`, never raw commands — kills drift between local and CI.

```yaml
# CORRECT
- run: ocx run -- task verify

# WRONG — will drift from taskfile
- run: ocx run -- uv run ruff check src tests
```

Raw commands in CI OK only for CI-only glue (artifact paths, GitHub annotations, `${{ steps.*.outcome }}`).

**Merge rule**: CI + Taskfile diverge → adopt stricter flags in Taskfile, CI calls task.

### 2. OCX bootstraps the toolchain

This repo dogfoods OCX. CI uses `ocx-sh/setup-ocx@v1` exactly like local dev:

```yaml
steps:
  - uses: actions/checkout@v4
  - uses: ocx-sh/setup-ocx@v1
  - run: ocx run -- task verify
```

`ocx.toml` resolves `task` and `uv` from `ocx.sh`; `uv` resolves Python linters from `pyproject.toml`'s `dev` extra. Three layers, one bootstrap.

### 3. Lint never blocks test results

Test results > lint results. Pattern: `continue-on-error: true` on linters + final gate step. Apply when separating lint/test into distinct steps:

```yaml
- name: Lint
  id: lint
  run: ocx run -- task lint
  continue-on-error: true

- name: Test
  run: ocx run -- task test

- name: Check lint outcome
  if: ${{ !cancelled() }}
  run: |
    if [[ "${{ steps.lint.outcome }}" == "failure" ]]; then
      echo "::error::Lint checks failed"
      exit 1
    fi
```

For this small repo a single `task verify` step is usually fine; split only when needed.

### 4. Security

- **SHA-pin every action** — `uses: owner/action@<full-sha>  # vX.Y.Z`
- **Minimal permissions** — declare at workflow level, elevate per-job
- **No secrets in `run:` steps** — use `env:` intermediary to block script injection
- **OIDC for cloud auth** — not static credentials (relevant once PyPI publish lands)
- **No self-hosted runners**

### 5. Concurrency

Every workflow:

```yaml
concurrency:
  group: ${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: ${{ github.ref != 'refs/heads/main' }}
```

## Cost Factors

| Factor | Impact | Guidance |
|--------|--------|----------|
| **Job count** | 1 min minimum per job + ~8s startup | Combine short steps; split only when parallelism saves wall-clock |
| **Runner OS** | Linux cheapest by ~10x | Linux default; gate macOS/Windows behind Linux passing |
| **Artifact retention** | Default 90 days | `retention-days: 1` inter-job, `7` for debugging |

## Recommended Actions (2026)

| Action | Version | Purpose |
|--------|---------|---------|
| `actions/checkout` | v4 | Repository checkout |
| `actions/upload-artifact` | v4 | Share files between jobs |
| `actions/download-artifact` | v4 | Receive shared files |
| `ocx-sh/setup-ocx` | v1 | Install OCX + bootstrap project toolchain |
| `softprops/action-gh-release` | v2 | Create GitHub Releases |

No deprecated v3 artifact/cache actions. Require Node 24+ runtime.

## Review Checklist

- [ ] Taskfile wrapping — every check callable via `task <name>`
- [ ] No command duplication — CI calls tasks
- [ ] SHA pinning — every `uses:` has full commit SHA + version comment
- [ ] Permissions — explicit, minimal, at workflow and/or job level
- [ ] Concurrency — group + `cancel-in-progress` on non-main
- [ ] Lint doesn't block tests when split into separate steps
- [ ] No deprecated actions

## Anti-Patterns

| Anti-Pattern | Fix |
|--------------|-----|
| Raw `uv run` in CI duplicating Taskfile | Call `task <name>` |
| `@v4` tag without SHA | Pin by full commit SHA |
| Lint failure blocks test execution | `continue-on-error` + final gate (when split) |
| Default 90-day artifact retention | `retention-days: 1` inter-job |
| No concurrency control | Add `concurrency:` block |
| `pull_request_target` + checkout of PR head | Use `pull_request` trigger for untrusted code |
