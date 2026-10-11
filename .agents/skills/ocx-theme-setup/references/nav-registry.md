# The path registry (`nav.json`)

You loaded this file because a site needs a new path on `ocx.sh`, or you are
reading the registry from code.

## Shape

`@ocx-sh/theme/nav.json` (schema `nav.schema.json` in the theme repo) holds
`brand`, `sections`, `hubs`, `claims`, `entries`, `actions` and `footer`.
The header, the footer, the breadcrumb trail, the search merge, `ocx-site
check` and the deploy action all read this one file, so a nav change is a
theme release that reaches each site through its Dependabot update.

A claim is `{ "path": "/integrations/bazel/", "repo": "ocx-sh/rules_ocx", "search": true }`:

- `path` is the directory the repo deploys to and the Astro `base`.
- `repo` is the only repository allowed to deploy there.
- `search: true` means the site builds a Pagefind bundle that every other
  section merges.

## Path grammar

`validate(nav)` enforces it:

- top-level sections are one of `docs`, `catalog`, `install`, `schemas`,
  `integrations`, `apps`, plus the root `/`;
- hub children are one slug deep: `/integrations/<slug>/`, `/apps/<slug>/`,
  slugs matching `[a-z0-9]+(-[a-z0-9]+)*`, at most 32 characters;
- `repo` matches `ocx-sh/<name>`;
- reserved names such as `_astro` are not claimable; the root claim also owns
  `/_astro/` and `/pagefind/`.

## Helpers (`@ocx-sh/theme/nav`)

| Export | Returns |
|---|---|
| `validate(nav)` | Problems (`{ at, message }[]`), empty when valid |
| `claimFor(nav, path)` | The longest claim owning `path` |
| `claimsOf(nav, repo)` | Every claim `repo` deploys |
| `zoneName(repo)` | The Bunny storage zone: `ocx-sh/rules_ocx` → `sh-ocx-rules-ocx` |
| `mergeTargets(nav, ownPath)` | The search bundles a section merges |
| `activeSection(nav, pathname)` | The header section id for a URL |
| `PAGEFIND_VERSION` | The Pagefind version the theme was built against |

## Adding a section

1. Open a pull request on `ocx-sh/website` adding the claim, and an
   `entries` item if it belongs in the ecosystem menu.
2. After the theme release, install it in the new repo and set `base` to the
   claim path.
3. Ask the owner to onboard the Bunny storage zone (see `ocx-theme-deploy`).
