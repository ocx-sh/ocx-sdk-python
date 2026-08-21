# Research: CI tooling (annotation, dependabot, guard tools)

Date: 2026-08-21. Source: researcher worker + orchestrator registry verification.
Consumed by: WP-ci. All SHAs pulled live via `gh api`.

## 1. PR test-report annotation — decision

**mikepenz/action-junit-report@v6.4.2**, SHA
`d9f48fc87bc235f7e214acf696ca5abc0a986f16`. Rationale: healthiest maintenance
(vs dorny/test-reporter 110 open issues; EnricoMi = multi-format overkill).

- pytest emits `--junitxml=test-results.xml`; pass through Taskfile via
  `{{.CLI_ARGS}}` on the `test` task (`task test -- --junitxml=...`).
- Job needs `checks: write`; fork PRs have read-only token → guard with
  `if: ${{ !cancelled() && github.event.pull_request.head.repo.full_name == github.repository }}`.

## 2. Coverage on PR — decision

**Keep Codecov** (already wired: codecov.yml PR comment, patch gate, ci.yml
upload step). No second coverage commenter. Patch target goes 80 → 100 with the
fail_under bump. No-account fallback recorded:
py-cov-action/python-coverage-comment-action@v4.3
(`a05be3d2e8a6272d3ef5fb2840ab20368bb2eb71`).

## 3. dependabot.yml — exact file

```yaml
version: 2
updates:
  - package-ecosystem: "github-actions"
    directory: "/"
    schedule:
      interval: "weekly"
    groups:
      github-actions:
        patterns: ["*"]
  - package-ecosystem: "uv"
    directory: "/"
    schedule:
      interval: "weekly"
```

`uv` is a native dependabot ecosystem (reads pyproject + uv.lock; `pip` would
desync). Dependabot rewrites SHA + version comment on pinned `uses:` lines.

## 4. actionlint / lychee / gitleaks

**Registry refs VERIFIED via `ocx package inspect`** (orchestrator, 2026-08-21 —
researcher's org/repo guess was wrong; owner's flat refs exist):

```toml
[tools]  # ocx.toml additions
actionlint = "ocx.sh/actionlint/actionlint:1"
lychee     = "ocx.sh/lychee/lychee:0"
gitleaks   = "ocx.sh/gitleaks/gitleaks:8"
```

(Verify major-pin tags at execution; `latest` manifests exist for all three.)

Task wrappers (NOT in `verify` — network/link checks would break fast local
loop; CI runs them as a separate job):

```yaml
  lint:actions: {cmds: [ocx run -- actionlint]}
  lint:links:   {cmds: [ocx run -- lychee --cache --max-cache-age 1d .]}
  secrets:      {cmds: [ocx run -- gitleaks detect --source . --redact]}
```

- actionlint: zero config; shellcheck/pyflakes integration silently no-ops.
- lychee: `lychee.toml` with `exclude = ["^http://localhost", "^https://127\\.0\\.0\\.1"]`,
  `accept = [200, 429]`.
- gitleaks: default config; needs `fetch-depth: 0` checkout in CI;
  `.gitleaksignore` only on false positives.
- CI job `repo-checks`: checkout(fetch-depth 0) → setup-ocx → the three tasks.

## 5. workflow_dispatch

`gh workflow run <wf>.yml --ref <branch>` works only after a version of the
workflow file WITH the dispatch trigger exists on the default branch. ci.yml
already has it on main → dispatchable from the PR branch today. New workflows
(acceptance) are NOT dispatchable until merged — test on the PR via a temporary
`pull_request` trigger, or accept post-merge dispatch.

## 6. Permissions/concurrency

Reuse repo's existing pattern verbatim: workflow-level `contents: read`,
`concurrency: group: ${{ github.workflow }}-${{ github.ref }}` +
`cancel-in-progress: ${{ github.ref != 'refs/heads/main' }}`; elevate per job.

Sources: github.com/mikepenz/action-junit-report ·
docs.astral.sh/uv/guides/integration/dependabot ·
lychee.cli.rs/continuous-integration/github · rhysd/actionlint ·
cli.github.com/manual/gh_workflow_run
