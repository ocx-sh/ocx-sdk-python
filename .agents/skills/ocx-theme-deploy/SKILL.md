---
name: ocx-theme-deploy
description: Deploying an ocx.sh section to Bunny with the reusable deploy action that ocx-sh/website ships at .github/actions/deploy - consumer workflow template (push to main, daily schedule, manual dispatch), the SHA-pinned uses line, inputs and outputs including preview and force-prune, the BUNNY_STORAGE_KEY environment secret, upload order, the stale-HTML prune and its cap, error behaviour, and preview deploys. Use when adding or editing a GitHub Actions workflow that publishes a site to ocx.sh, when wiring ocx-sh/website/.github/actions/deploy, storage-key, dist or path inputs, when a deploy fails on a claim, a missing index.html or Pagefind entry, a 401, a failed upload or the prune cap, when the daily deploy stopped running, when onboarding a new repo to Bunny, when asked about previews or BUNNY_PREVIEW_KEY secrets, or when someone suggests putting BUNNY_API_KEY or a cache purge into CI. Not for the Astro build itself (ocx-theme-setup) or the pre-deploy quality gates (ocx-theme-quality).
license: Apache-2.0
metadata:
  summary: Ship an ocx.sh section to Bunny with the ocx-sh/website deploy action - template workflow, inputs, secrets, ordering and prune guarantees
  keywords: ocx,ocx-sh,deploy,bunny,bunnycdn,github-actions,workflow,reusable-action,cdn,static-site,pagefind,preview,prune,schedule,secrets,astro,starlight
---

# ocx-theme-deploy

Each repo that owns a claim in `nav.json` builds its site and uploads it to
its own Bunny storage zone with one reusable action from `ocx-sh/website`.
The action versions with the theme: it is released under the same `vX.Y.Z`
tag as `@ocx-sh/theme`.

Read `action.yml` at the commit you pin before relying on the tables below;
it is the contract the action implements.

Contents: [Owner steps](#before-the-first-deploy-owner-steps) ·
[Workflow](#the-workflow) · [Inputs and outputs](#inputs-and-outputs) ·
[What a deploy does](#what-a-deploy-does) · [Prune cap](#the-prune-cap) ·
[Previews](#previews) · [Do not](#do-not) · failures in
[references/failure-modes.md](references/failure-modes.md)

## Before the first deploy (owner steps)

These need account access; ask the owner, never try them from CI:

1. The repo's claim exists in `nav.json` in a released theme
   (`ocx-theme-setup`, `references/nav-registry.md`).
2. The owner runs `task bunny:onboard` in `ocx-sh/website`. It creates the
   storage zone `sh-ocx-<repo>` (`ocx-sh/rules_ocx` → `sh-ocx-rules-ocx`, `ocx-sh/find_ocx` → `sh-ocx-find-ocx`) and
   stores its password as the secret `BUNNY_STORAGE_KEY` in the repo's GitHub
   environment `ocx.sh`, limited to the `main` branch.

**Secrets rule.** CI only ever holds `BUNNY_STORAGE_KEY`, one zone's password.
`BUNNY_API_KEY` (the account key) lives in the owner's gitignored `.env` and
never in a workflow, a secret, a file or a log. No cache purge runs in CI.

## The workflow

A deploy runs on a push to `main`, once a day, and by hand. Use exactly these
three triggers: no `tags:`, no `pull_request`. The `ocx.sh` environment only
admits `main`, so a dispatch from another branch fails at the deploy job.

```yaml
# .github/workflows/deploy.yml
name: deploy
on:
  push:
    branches: [main]
  schedule:
    - cron: '17 4 * * *' # daily; an off-peak minute avoids the top-of-hour queue
  workflow_dispatch:
permissions: {}
jobs:
  build:
    runs-on: ubuntu-latest
    permissions: { contents: read }
    steps:
      - uses: actions/checkout@de0fac2e4500dabe0009e67214ff5f5447ce83dd # v6.0.2
        with: { persist-credentials: false }
      # install your toolchain and dependencies here
      - run: npx astro build # Starlight writes dist/, including dist/pagefind/
      - run: npx ocx-site check --dist dist
      - uses: actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a # v7.0.1
        with: { name: site, path: dist }
  deploy:
    needs: build
    runs-on: ubuntu-latest
    environment: { name: ocx.sh, url: '${{ steps.deploy.outputs.url }}' }
    concurrency: { group: ocx-sh-deploy, cancel-in-progress: false }
    permissions: {}
    steps:
      - uses: actions/download-artifact@3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c # v8.0.1
        with: { name: site, path: dist }
      - id: deploy
        uses: ocx-sh/website/.github/actions/deploy@0000000000000000000000000000000000000000 # v0.0.0
        with:
          dist: dist
          storage-key: ${{ secrets.BUNNY_STORAGE_KEY }}
```

Before the first release, pin the full SHA of the `ocx-sh/website` commit that the
theme pin also uses and write the branch as the comment (`# feat/phase3-pilots`). Bump
theme and action SHA together: the action aborts on a Pagefind version mismatch. A
branch comment and a pnpm git dependency are invisible to Dependabot and Renovate, so
repin by hand until the npm release; after it, both pins fall under the bot.

Replace the all-zero SHA and `# v0.0.0` with the full commit SHA of the
`ocx-sh/website` release you adopt and its tag. Pin every `uses:` to a full
commit SHA with a `# vX.Y.Z` comment, never a floating `@v1`; Dependabot's
`github-actions` ecosystem keeps them current. Keep `cancel-in-progress:
false`: a cancelled deploy stops between phases.

GitHub disables a scheduled workflow after 60 days without repository
activity. The push and dispatch triggers still deploy; see
[references/failure-modes.md](references/failure-modes.md).

## Inputs and outputs

| Input | Required | Default | Meaning |
|---|---|---|---|
| `dist` | yes | | Build output; its root maps to `path` |
| `storage-key` | yes | | The zone password. Empty fails at once, before any request |
| `path` | no | The repo's only claim, or its shortest when it owns several | Public path `dist` maps to; must be a claim the repo owns |
| `storage-host` | no | `storage.bunnycdn.com` | Regional storage endpoint |
| `dry-run` | no | `false` | List and plan; send no PUT or DELETE |
| `preview` | no | empty | A preview site name from `scripts/previews/sites.mjs`; see [Previews](#previews). Not combinable with `path` |
| `force-prune` | no | `false` | Delete stale HTML even past the [prune cap](#the-prune-cap) |

| Output | Meaning |
|---|---|
| `url` | `https://ocx.sh<path>`, or the preview URL in preview mode |
| `uploaded`, `deleted` | File counts; `0` in a dry run |

## What a deploy does

1. Resolves repo, zone and path; checks the claim and the key. No requests.
2. Runs the `ocx-site check` logic on `dist`. `dist/index.html` must exist;
   a `search: true` claim needs `dist/pagefind/pagefind-entry.json`.
3. Lists the zone's current files under the path.
4. **Phase 1**: uploads every file except HTML and the top-level Pagefind
   entry files: hashed `_astro/` assets and Pagefind chunks first. Eight in
   parallel, SHA-256 checksum on each, three tries.
5. **Phase 2**: uploads the top-level Pagefind files and the HTML, with
   `404.html` as the zone's error page.
6. **Prune**: deletes HTML files the new build no longer has, never in
   another repo's claim subtree, unless the cap below stops it. Stale non-HTML is never deleted by a deploy; the owner
   garbage-collects old assets long after any cache lifetime.
7. Writes the step summary and outputs.

The guarantee: new assets exist before any page that references them, and no
deploy deletes an asset, so no cached page points at a missing file.

## The prune cap

When the prune would delete more than half of the listed HTML files and at
least 10 are listed, the run exits 1 after the uploads, names the count, and
sends no DELETE. That ratio almost always means a wrong `dist` or `path`, not
a real cleanup. Uploads already done stay, and the site is consistent.

- Wrong `dist` or `path`: fix it and re-run. Do not force.
- A real mass removal (a section dropped or renamed): dispatch once with
  `force-prune: 'true'` in the workflow, merge that, and remove the input
  again after the run. Never leave it on.

## Previews

A preview deploys a whole consumer site to its own zone, for review before
the claim goes live. Only the `previews` workflow in `ocx-sh/website` uses
it; a consumer's `deploy.yml` never sets `preview`.

- `preview: <site>` takes a name or slug from `scripts/previews/sites.mjs`
  (`rules_ocx` or `rules-ocx`) and deploys to zone `sh-ocx-preview-<slug>` at
  path `/`.
- The key is the `previews` environment secret `BUNNY_PREVIEW_KEY_<SLUG>`:
  slug uppercased, `-` as `_` (`rules-ocx` → `BUNNY_PREVIEW_KEY_RULES_OCX`),
  one zone password per site, never account-wide.
- The URL is `https://sh-ocx-preview-<slug>.b-cdn.net/` (the `url` output).
  Read `previewUrl` in `sites.mjs` for the current form; link a preview only
  once the owner confirms it is live.
- A preview skips the claim and layout checks and keeps everything else:
  `index.html`, checksums, phases, prune and prune cap. An unknown site, or
  `preview` with `path`, exits 1 with zero requests. Without `preview`, a
  `sh-ocx-preview-` zone exits 1.

## Do not

- Put `BUNNY_API_KEY`, a purge call or a zone-management step in CI.
- Add a `tags:` or `pull_request` trigger to the deploy workflow.
- Deploy from a fork, a pull request, or any branch the `ocx.sh` environment
  does not allow.
- Upload with another tool beside the action: it breaks the ordering
  guarantee.
- Deploy to a path your repo does not own in `nav.json`.
- Leave `force-prune: 'true'` in a committed workflow.
