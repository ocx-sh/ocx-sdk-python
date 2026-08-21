# Research: scaffold state (pre-v0.1 implementation)

Date: 2026-08-21. Source: architecture-explorer worker (hex-plan Discover).
Consumed by: `.claude/artifacts/sdk-v01-plan.md` WP-scaffold, WP-ci.

## Current state summary

- `pyproject.toml`: `requires-python = ">=3.13"`, `version = 0.1.0`, `dependencies = []`,
  hatchling build, `dev` extra (coverage/pyright/pytest/ruff), `docs` extra
  (mkdocs-material + mkdocstrings + griffe + include-markdown + section-index +
  git-revision-date + pymdown). Ruff line-length 120, target py313, select
  E/W/F/I/B/UP/ANN/RUF, ignore ANN401, tests ignore ANN. Pyright standard, 3.13.
  Coverage: branch=true, `fail_under = 80`, exclude_also for TYPE_CHECKING /
  NotImplementedError / overload / Protocol / assert_never / `...`.
- `taskfile.yml` (Task v3): `verify` = format:check → lint → types → test →
  cov:report; `test` = `uv run --extra dev coverage run -m pytest`; `cov:xml/html`;
  `docs:serve/build/clean` (`mkdocs build --strict`); `changelog[:preview]`
  (git-cliff); `release:prepare` (BUMP/VERSION → git-cliff bumped-version →
  `uv version` → sed `~=` pins in README + install.md → changelog → verify).
- Workflows: `ci.yml` (push main + PR + dispatch; single ubuntu job:
  checkout@v6.0.2-SHA → setup-ocx@v1 (tag, NOT SHA) → `task verify` → `task cov:xml`
  → codecov@v5.5.4-SHA w/ CODECOV_TOKEN). `docs.yml` (build strict → deploy Pages,
  main-only; configure-pages v5.0.0 / upload-pages-artifact v3.0.1 / deploy-pages
  v4.0.5, SHA-pinned; fetch-depth 0 for git-revision-date). `release.yml`
  (tags v* + dispatch; permissions {} + per-job elevation; tag↔pyproject check;
  verify → `uv build` → gh-release v2.3.2 → artifact v4.6.2 retention 1;
  publish-pypi job: download-artifact v8.0.1 → pypa/gh-action-pypi-publish
  v1.14.2, OIDC, environment `pypi`).
- No `.github/dependabot.yml`. No actionlint/lychee/gitleaks anywhere.
- `src/ocx_sdk/`: `__init__.py` (`__version__` via importlib.metadata,
  `0.0.0+unknown` fallback) + `py.typed`. `tests/`: `test_version.py` (3 tests).
- `mkdocs.yml`: Material, nav Home/Getting started/API reference/Contributing/
  Changelog; mkdocstrings python handler `paths: [src]`, google style, filter `!^_`.
- `cliff.toml`: feat→Added, fix→Fixed, refactor|perf→Changed, doc→Documentation;
  chore/ci/style/test/build skipped. `initial_tag = v0.1.0`.
- `codecov.yml`: project target auto ±1%, patch 80%, `if_ci_failed: success`.
- `.gitattributes`: `ocx.lock`/`uv.lock` merge=union. `ocx.toml`: task/uv/git-cliff.

## Gaps vs design (§15 scaffold changes + goal directive)

1. Python floor 3.13 → **3.12** (pyproject requires-python, ruff target py312,
   pyright pythonVersion 3.12).
2. Coverage `fail_under` 80 → **100** (pyproject + codecov patch target).
3. CI: no matrix → **3 OS × 3.12/3.13/3.14 unit matrix** + contract job (Linux
   first) + canary/acceptance workflow.
4. **dependabot.yml missing** (github-actions + uv ecosystems).
5. **actionlint/lychee/gitleaks missing** — ocx packages + task wrappers + CI.
6. `setup-ocx@v1` tag-pinned — SHA-pin per subsystem-ci rule.
7. No PR test-report annotation.
8. `docs` nav lacks Guide/Reference split from design §17.
