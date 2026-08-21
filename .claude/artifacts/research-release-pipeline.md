# Research: release pipeline reference (ocx-mirror-sdk)

Date: 2026-08-21. Source: explorer worker over `../ocx-mirror-sdk` (hex-plan
Discover). Consumed by: WP-release. Verdict: **pipeline proven** — ocx-mirror-sdk
published v0.3.0→v0.6.0 to PyPI with exactly this shape; mirror it, swap names.

## Deltas our repo needs (ours is already 95% identical)

Our `release.yml` already matches the mirror pattern (tag↔pyproject check,
verify, `uv build`, gh-release, OIDC publish to `pypi` environment, dispatch =
dry-run build since publish gates on `github.ref_type == 'tag'`). Remaining work:

1. **pyproject `[project]` enrichment** (mirror the table, adapt):
   - `license = "Apache-2.0"` + `license-files = ["LICENSE"]`
   - `authors = [{ name = "The OCX Authors" }]`
   - `keywords = ["ocx", "oci", "registry", "packages", "sdk", "toolchain"]`
   - classifiers: Development Status 3 - Alpha, Intended Audience Developers,
     License OSI Apache, Python 3 Only + 3.12/3.13/3.14, Topic Build Tools,
     Typing :: Typed
   - `[project.urls]`: Homepage/Repository/Issues + Documentation
     (https://ocx-sh.github.io/ocx-sdk-python/) + "OCX Project"
2. **PyPI trusted publisher registration** (owner action, morning): pypi.org →
   Publishing → add **pending publisher** for project `ocx-sdk`, repo
   `ocx-sh/ocx-sdk-python`, workflow `release.yml`, environment `pypi` — must
   exist BEFORE first tag push.
3. Release human steps (already in CLAUDE.md): `task release:prepare` → review →
   `chore(release): vX.Y.Z` commit → tag → `git push --atomic origin main vX.Y.Z`.
4. Mirror has no RELEASING.md — ours documents steps in CLAUDE.md; add a short
   "Releasing" section to contributing docs for coworkers.

## Facts pinned

- `workflow_dispatch` on release.yml = safe dry-run (build + verify only).
- Codecov PR comment exists but no junit annotation in mirror — our goal
  requires PR test annotation (see research-ci-tooling.md).
- Mirror also lacks dependabot + actionlint/lychee/gitleaks — we go beyond, not
  behind, the reference.
