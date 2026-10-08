# Handover: move the docs to the shared ocx.sh site

For an agent working in `ocx-sdk-python`. Read this before touching docs, CI or `ocx.toml`.
Plan: [plan_phase3-pilots.md](https://github.com/ocx-sh/website/blob/main/.agents/plans/plan_phase3-pilots.md) (WP S1 to S3).
Start only after `rules_ocx` is verified on prod: this repo reuses its result.

## What the shared site is

`ocx.sh` is one namespace served by Bunny from many repos. Each repo owns a path claimed in
`nav.json` of `ocx-sh/website`. This repo owns `/integrations/python/`. It builds a Starlight
site with the shared theme `@ocx-sh/theme` and deploys it to its own Bunny storage zone
(`sh-ocx-ocx-sdk-python`). Today the path redirects (302) to `https://ocx-sh.github.io/ocx-sdk-python/`.

## Steps this repo owns

Work on a branch. Never commit on main, push, tag, or use `--no-verify` / `--no-gpg-sign`
(`.claude/rules/workflow-git.md`). `task verify` must pass. The owner lands it.

1. `ocx.toml`: add `node = "ocx.sh/nodejs/node:24"` and `pnpm = "ocx.sh/pnpm/pnpm:11"`, run `ocx lock`.
2. `site/` Starlight project: `base: '/integrations/python/'`, `trailingSlash: 'always'`,
   `plugins: [ocxTheme()]`, no `site`. Install `@ocx-sh/theme` exactly `0.2.0` (no `^`), commit `pnpm-lock.yaml`, CI uses `--frozen-lockfile`; block until the plan's `ACTION_SHA` line is filled (P0.2). See the
   [setup skill](https://github.com/ocx-sh/website/blob/main/skills/ocx-theme-setup/SKILL.md).
   `site/` is also the MkDocs output dir today (gitignored); rename one of them, and say which in the PR.
3. API reference: `starlight-pydocs`, pinned to an exact version (0.3.0 at planning time), and
   `<Autodoc name="ocx_sdk" />` in place of `::: ocx_sdk`. Feed it Griffe JSON (`source: {file}`) dumped at build time by `uvx` with a pinned `griffe` version (`uv` is in `ocx.toml`), so no dump is committed. Fallback: `griffe2md`.
   The mkdocstrings options (`separate_signature`, `merge_init_into_class`, `members_order: source`,
   filters) may have no equivalent. Diff the rendered reference against the live page.
4. Prose port (19 pages, `nav` in `mkdocs.yml:139-162`): translate admonitions, the four grid
   cards, tabs, `def_list`, magiclink shorthand, and `include-markdown` for the changelog. The Pre-1.0 notice (`docs/index.md:9`) is a plain `warning` admonition; the `.experimental` CSS in `extra.css` is applied by nothing, so drop it, and drop the last-updated footer (Q5 default).
5. Keep doctests alive. `pytest` collects `docs/` and `README.md` through Sybil in `conftest.py`;
   `task test:contract` runs them. Keep sources in `docs/` and GENERATE the Starlight content from them (decided; do not change `testpaths`/`conftest.py`). 100% coverage must hold.
6. Update references to MkDocs: `CLAUDE.md:79-80,103-107`, `docs/contributing/docs.md`,
   `lychee.toml:1-4`, `.github/workflows/docs.yml`, the `docs` extra in `pyproject.toml`,
   README and PyPI project URLs.
7. CI: build job (build, `npx ocx-site check --dist dist`), and `deploy.yml` from the
   [deploy skill](https://github.com/ocx-sh/website/blob/main/skills/ocx-theme-deploy/SKILL.md).
   Triggers: push to `main`, daily schedule, dispatch. Pin every `uses:` by full SHA with a
   `# vX.Y.Z` comment, including the
   [deploy action](https://github.com/ocx-sh/website/tree/main/.github/actions/deploy). The build
   needs full git history if the last-updated date is kept.
8. `.github/dependabot.yml` (exists; github-actions, uv): add an npm group for `@ocx-sh/theme`. Bump theme and deploy-action SHA together (the action aborts on a Pagefind mismatch).
9. Last, after verification: one final Pages build publishing meta-refresh stubs for the old index page, then remove the `docs.yml` Pages deploy; the owner disables Pages. The mkdocstrings `objects.inv` (intersphinx) break is accepted: no replacement.
10. Before handover: `grep -rE '(href|src)="/(_astro|pagefind)/' site/dist` returns nothing; lhci (mobile, per the `ocx-theme-quality` skill) against `astro preview` scores 100x4; add `site/node_modules`/`site/dist` to `.gitignore`.

## What the website repo does

- Owner creates the GitHub environment `ocx.sh` here (policy: `main` only).
- `task bunny:onboard -- ocx-sh/ocx-sdk-python` creates the zone and sets `BUNNY_STORAGE_KEY`.
- One website PR removes `legacy-ocx-sdk-python` from `infra/bunny/legacy.json`, landing LAST, after your first deploy filled the zone and was verified via storage listing/preview zone. Rules apply to the dev zone, then prod.

You never hold the Bunny account key. It must not enter a workflow, a secret or a file here.

## Verify (the website agent runs these; check the results)

- `deploy` job green, files uploaded.
- `curl -i /integrations/python/does-not-exist/` returns 404 with the themed page and working links.
- `https://sh-ocx-dev.b-cdn.net/integrations/python/` and `https://next.ocx.sh/integrations/python/` return 200.
- `/`, `/integrations/` and `/integrations/bazel/` link both ways with this section.
- Header nav and merged search work. Lighthouse 100 in all four categories (mobile).
- Every public symbol in the old reference exists in the new one.

## Do not

- Do not drop the Sybil doctests or lower `fail_under = 100`.
- Do not edit `CHANGELOG.md` by hand (git-cliff owns it).
- Do not set `site` or change `base`. Do not float action pins or add `pull_request`/tag triggers to `deploy.yml`.
- Do not remove the Pages deploy before verification.

## Rollback

Website side: restore the legacy entry, re-apply rules, `task bunny:purge /integrations/python/`, check `curl -sI` shows 302. Repo side: the docs change is one branch; revert it (Sybil/`docs/` untouched since content is generated). A bad deploy is fixed by redeploying a good commit. Keep Pages enabled until prod has run clean for 48 h.

## Open questions

- Resolved: Q3 generate from `docs/`; Q5 drop footer and `.experimental` style; Griffe dump via `uvx` at build.
- Is `starlight-pydocs` stable enough? 7 weeks old, one maintainer.

## Links

- [Plan](https://github.com/ocx-sh/website/blob/main/.agents/plans/plan_phase3-pilots.md)
- [ocx-theme-setup](https://github.com/ocx-sh/website/blob/main/skills/ocx-theme-setup/SKILL.md)
- [ocx-theme-deploy](https://github.com/ocx-sh/website/blob/main/skills/ocx-theme-deploy/SKILL.md)
- [Deploy action](https://github.com/ocx-sh/website/tree/main/.github/actions/deploy)
