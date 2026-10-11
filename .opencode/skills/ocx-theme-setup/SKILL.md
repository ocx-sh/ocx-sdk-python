---
name: ocx-theme-setup
description: Adding the @ocx-sh/theme Starlight plugin to an Astro site that joins ocx.sh, covering the ocxTheme() plugin options (breadcrumbs, root crumb), the base path that must be a nav.json claim, trailingSlash, site, fonts, favicon, customCss order, the Starlight component overrides, what overriding Search drops, Expressive Code and Pagefind merge settings, the VitePress CSS adapter, and the ocx-site check CLI. Use when installing or upgrading @ocx-sh/theme, writing or debugging astro.config.mjs for an ocx.sh section, when a build fails with "is not a claim path", "trailingSlash must be 'always'" or "site ... is not https://ocx.sh", when overriding a Starlight component such as Header, Search or Sidebar, when fonts, breadcrumbs or search merging look wrong, or when a new repo needs a path on ocx.sh. Not for picking or using individual components (ocx-theme-components) or deploying (ocx-theme-deploy).
license: Apache-2.0
metadata:
  summary: Wire @ocx-sh/theme into an Astro Starlight site on ocx.sh - base path, plugin options, overrides, fonts, search merge
  keywords: ocx,ocx-sh,astro,starlight,starlight-plugin,theme,astro-config,base-path,trailing-slash,pagefind,expressive-code,fonts,breadcrumbs,overrides,vitepress
---

# ocx-theme-setup

Every section of `ocx.sh` is its own Astro + Starlight build, mounted under its
own path and wearing `@ocx-sh/theme`. This skill gets a site to build with the
theme. The companion skills cover the rest:

| Task | Skill |
|---|---|
| Choosing and using components in pages | `ocx-theme-components` (`../ocx-theme-components/SKILL.md`) |
| Styling, tokens, dark mode, focus | `ocx-theme-theming` (`../ocx-theme-theming/SKILL.md`) |
| Icons | `ocx-theme-icons` (`../ocx-theme-icons/SKILL.md`) |
| Uploading the build to ocx.sh | `ocx-theme-deploy` (`../ocx-theme-deploy/SKILL.md`) |
| Budgets, Lighthouse, e2e gates | `ocx-theme-quality` (`../ocx-theme-quality/SKILL.md`) |

Missing a sibling? Install the whole set: `grim add ghcr.io/ocx-sh/lore/ocx-theme:latest`.

## 1. Claim a path first

`base` must be a **claim path** in the theme's `nav.json`, or the build stops.
Current claims: `/`, `/integrations/`, `/apps/`, `/install/` (ocx-sh/website),
`/docs/`, `/schemas/` (ocx-sh/ocx), `/catalog/` (ocx-sh/index),
`/integrations/bazel/` (ocx-sh/rules_ocx), `/integrations/python/`
(ocx-sh/ocx-sdk-python), `/integrations/cmake/` (ocx-sh/find_ocx),
`/apps/catalog/` (ocx-sh/catalog). `nav.json` in the
installed theme is the live list.

A new section is a pull request to `ocx-sh/website` adding a claim
(`{ path, repo, search }`) to `packages/theme/src/nav.json`, then a theme
release. Path grammar and the registry helpers: [references/nav-registry.md](references/nav-registry.md).

## 2. Install

```bash
pnpm add @ocx-sh/theme   # or npm install @ocx-sh/theme
```

### Before the first release: pin a commit

While a fix is unreleased, pin the website commit as a pnpm git-subdir dependency
(pnpm 11). The leading slash in `path:/packages/theme` is the form tested; the
slashless form also resolves:

```bash
pnpm add 'github:ocx-sh/website#<full-sha>&path:/packages/theme'
```

pnpm prepares a git package by running its `prepack` (declaration build, `tsc`);
the theme lists `typescript` and `@types/node` as devDependencies for that, so no
compiler is needed on `PATH`. pnpm refuses to run it until you allow the package,
keyed by the codeload tarball URL of the pin, and `esbuild` needs its own entry
(else `ERR_PNPM_IGNORED_BUILDS`):

```yaml
# pnpm-workspace.yaml
allowBuilds:
  esbuild: true
  '@ocx-sh/theme@https://codeload.github.com/ocx-sh/website/tar.gz/<full-sha>#path:/packages/theme': true
```

The exact key is printed in the `ERR_PNPM_GIT_DEP_PREPARE_NOT_ALLOWED` message
(a `file://` remote prints `@ocx-sh/theme@git+file://…#<sha>&path:/packages/theme`).
Setting the entry to `false` also installs, but skips the generated `.d.mts` typings.
A repin edits three places: the dependency, this key, and the deploy action `uses:`
SHA (`ocx-theme-deploy`); Dependabot and Renovate cannot track a git SHA. Move to the
npm version after the release.

Peers: `astro ^7` and `@astrojs/starlight >=0.42.0 <0.43`. The upper bound is
deliberate (the chrome CSS targets Starlight internals): a Starlight minor
arrives through a theme release, never by bumping it alone.

## 3. Configure

```js
// astro.config.mjs
import starlight from '@astrojs/starlight';
import ocxTheme from '@ocx-sh/theme/starlight';
import { defineConfig } from 'astro/config';

export default defineConfig({
  base: '/integrations/bazel/', // your claim path
  trailingSlash: 'always', // required: Starlight reads it before plugins run
  integrations: [
    starlight({
      title: 'rules_ocx',
      plugins: [ocxTheme()],
    }),
  ],
});
```

Do not set `site`: the plugin sets `https://ocx.sh`, and any other value fails
the build. Hard failures, all thrown in the plugin's `config:setup`:

| Error | Fix |
|---|---|
| `base "…" is not a claim path; claims: …` | Use a claim path; add a claim first if yours is new |
| `site "…" is not https://ocx.sh` | Drop `site` or set it to `https://ocx.sh` |
| `trailingSlash must be 'always'` | Set `trailingSlash: 'always'` in the Astro config |

## 4. Plugin options

`ocxTheme(options)` takes one option today:

| Option | Default | Effect |
|---|---|---|
| `breadcrumbs` | `{}` (on, no root crumb) | The trail above every page title: section or hub, ecosystem entry, sidebar groups, page. `false` drops it. Splash pages never show one |
| `breadcrumbs.root` | absent | `{ label, href }` prepends a root crumb (e.g. `{ label: 'ocx.sh', href: '/' }`); `false` or absent means none |

```js
plugins: [ocxTheme({ breadcrumbs: { root: { label: 'ocx.sh', href: '/' } } })];
```

## 5. What the plugin sets for you

Read [references/plugin-behaviour.md](references/plugin-behaviour.md) before
overriding any of these; it lists merge rules and the exact override set.

- `customCss`: `@ocx-sh/theme/tokens.css`, `@ocx-sh/theme/base.css`,
  `@ocx-sh/theme/starlight.css`, then yours. Your CSS is unlayered, so it wins.
- `components`: thirteen Starlight overrides (Head, Header, Footer, PageTitle,
  TableOfContents, ThemeSelect, ThemeProvider, MobileMenuFooter,
  MarkdownContent, Search, Sidebar, MobileMenuToggle, PageFrame). Your keys win.
- Fonts: IBM Plex Sans and Mono (400, 600) through the Astro Fonts API with
  metric-matched fallbacks. Add no font of your own.
- `expressiveCode`: the theme's code frame and token colours, merged under yours.
- `pagefind.mergeIndex`: every other `search: true` claim, so one search box
  finds every section.
- A `/favicon.svg` route with the logo, unless you ship `public/favicon.svg` or
  set Starlight's `favicon`.

**Overriding `Search` drops four features.** The theme's `Search.astro` script
also mounts the header nav menu, the mobile menu drawer, the sidebar
collapsibles and the toaster (including copy toasts). The plugin logs a warning.
Call `mountChrome(document)` from `@ocx-sh/theme/chrome` in your override's
`<script>`, or do not override `Search`.

## Content porting notes

- **Sidebar**: a Starlight sidebar generated from the file tree is wrong for generated
  pages; list `sidebar` explicitly in `starlight({ ... })`.
- **Edit this page**: the link shows only when the route has an `editUrl`. For generated pages
  set `editUrl` in each page's frontmatter to the true source file (not the gitignored output),
  or set Starlight's `editLink.baseUrl` for hand-written ones. There is no theme option.
- **Heading ids**: Starlight slugs with github-slugger (an em dash becomes a double hyphen:
  `bootstrap-only--ocx_install_`), unlike MkDocs. Starlight has no `{#id}` syntax. Rewrite
  cross-page links to the slugger id and check every fragment against the built HTML.
  Stardoc's `<a id=X></a>` above `## X` duplicates the heading id: drop the anchor when it
  equals the next heading's id. `ocx-site check` does not flag repeated ids; add your own check.
- **Sphinx to Starlight** (find_ocx, the third consumer): the port script reads the committed
  `.rst` and never edits it, so Sphinx stays the source of truth. A directive or role it does
  not know (`.. cmake:...`, `:cmake:...`) fails the port loudly with file and line; map it or
  add a handler, never drop it silently. Then diff the built headings against the Sphinx
  index, as for Stardoc and Griffe.
- **Large generated pages** (Stardoc rule references, Griffe API modules, Sphinx indexes):
  split them in the port script, one page per rule, module or section group. One page over
  14,200 bytes gzipped fails the Lighthouse HTML budget (see `ocx-theme-quality`), and the
  sidebar must list the parts explicitly.
- **Sitemap**: do not add `@astrojs/sitemap`. Starlight already writes `sitemap-index.xml`
  and `sitemap-0.xml` under your `base` (forced `site`). The root `robots.txt` gains your
  sitemap line when your section goes live.
- **Build warnings**: `collection i18n does not exist or is empty` is harmless. `Could not render
  /404 … conflicts with /404` goes away with a `404.md` in the docs content. An unknown fence
  language (`pycon`) warns; map it with `expressiveCode: { shiki: { langAlias: { pycon: 'python' } } }`.

## 6. Check the build

```bash
npx astro build
npx ocx-site check --dist dist
```

`ocx-site check` (the package's `bin`) fails when a root-relative link leaves
every claim, when the `dist` layout does not match the claim, or when a
`search: true` claim's Pagefind bundle is a different version from the theme's.
Exit codes: `0` clean, `1` problems, `2` usage. Flags `--repo <owner/name>` and
`--path <p>` cover a checkout without a git remote. The deploy action runs the
same check.

## Other generators

The VitePress catalog uses `@ocx-sh/theme/vitepress` (CSS-only adapter) plus
`@ocx-sh/theme/fonts.css` (plain `@font-face`, since it has no Astro Fonts API).
Header data for a non-Starlight site: `@ocx-sh/theme/nav.json` and the helpers
in `@ocx-sh/theme/nav`; the logo is `@ocx-sh/theme/logo.svg` (inline it, never
an `<img>`). On an Astro page, render it with `@ocx-sh/theme/components/ui/Logo.astro`
instead. No mdBook or MkDocs adapter exists.

### Shell: a plain Astro site

`@ocx-sh/theme/layouts/Shell.astro` is the whole HTML document for an Astro
site without Starlight: pre-paint theme script, `tokens.css`, `base.css`,
`fonts.css` (plain `@font-face`, no Fonts API config needed; the four upright faces are
preloaded, and the metric-matched fallbacks in `base.css` keep layout from shifting), the header, `<main id="main">`
and the footer. Set `site` and `base` in Astro config; Shell validates no claim,
`site` or `trailingSlash` (the Starlight plugin and `ocx-site check` do).
`@astrojs/starlight` is an optional peer, so a Shell-only site needs only `astro`.
With the theme linked from a local checkout (`file:`), add `vite: { resolve: { dedupe: ['astro'] } }`
so the theme and the site share one Astro.

```astro
---
import Shell from '@ocx-sh/theme/layouts/Shell.astro';
---
<Shell title="Packages" description="..."><h1>Packages</h1></Shell>
```

Props: `title` (required), `description`, `canonical` (default: the path on
`Astro.site`), `activeSection` (ocx mode), `brand`, `nav`, `footer`. Slots:
`head`, the default slot, `header-search` (empty means no search).

- **ocx mode** (no `brand`): sections, ecosystem menu, install, GitHub and the
  theme toggle from `nav.json`, and the `nav.json` footer with the licence line.
  Shell links `<base>favicon.svg`, so ship that file in `public/` (copy
  `@ocx-sh/theme/logo.svg`). Neutral mode without a head icon gets the empty `data:,` icon.
- **Neutral mode** (`brand={{ title, wordmark?, logoSrc?, logoAlt? }}`): a mirror's own
  brand (`logoSrc` renders an `<img>` with `logoAlt`, default empty, in a fixed
  square token-sized box with `object-fit: contain`: the layout is identical with
  images blocked; the wordmark text sits beside it; consumer SVG is never inlined), plain `nav` links with
  `aria-current` when the path starts with the href (`/` and the base match
  exactly, external hrefs never), the theme toggle and `footer={{ links, note? }}`.
  `logoSrc`, nav and footer hrefs are used as given and must include the base.
  Neutral mode emits an empty `<link rel="icon" href="data:,">` unless you fill the `head` slot
  (add your own icon there), so the browser never requests a 404 `/favicon.ico`.
  Without `footer` the footer is empty (no ocx links); without `note` there is no
  licence line. `nav` or `footer` without
  `brand` throws.
- **CSP**: `INLINE_SCRIPT_HASHES` from `@ocx-sh/theme/csp` is the frozen list of
  `sha256-<base64>` sources of every inline script Shell, `SiteHeader` and
  `SiteFooter` emit (theme and platform scripts); put each in `script-src` as
  `'sha256-…'`. Shell's bundled module script is external: allow it by `'self'`
  (a build that inlines it needs `assetsInlineLimit: 0`, or that script's own hash).
  The module is Node-only: import it in config or build code, never client code.
- **Highlight.js in prose**: `@import '@ocx-sh/theme/prose-code.css'` (opt-in,
  after base.css) colours `hljs-*` spans in a `<pre><code>` outside Expressive
  Code from the `--ocx-color-code-*` tokens, in both schemes. The rules sit in
  `@layer ocx`: never load an unlayered highlight.js theme beside it, it would win.
- **Lazy widgets of your own**: `mount(root, { load, trigger?, replay? })` from
  `@ocx-sh/theme/lazy` is the trigger layer the theme's Zag components use, with
  no Zag import. It starts on the first `pointerenter`/`focusin`/`touchstart`
  (`trigger: 'visible'` or `'manual'` + `handle.start()` otherwise), replays
  one early click or activation key (`replay: false` disables), and sets `data-zag-state` to `idle`/`loading`/`live`/
  `error` on `root`. `load` resolves to `{ start(root, firstEvent?) }`; a returned
  `{ api, stop }` is kept as `handle.api` and stopped on `handle.destroy()`.
- On narrow viewports a menu button opens a native popover list of the
  sections (or `nav`): no JavaScript, no sidebar drawer.
- The theme key is shared (`starlight-theme`), so a reader's theme carries
  between sections on ocx.sh.

## Keep the theme current

Add a Dependabot entry grouping `@ocx-sh/theme` in each consumer; a nav change
is a theme release, so it only reaches a site through that update:

```yaml
# .github/dependabot.yml
version: 2
updates:
  - package-ecosystem: npm
    directory: / # wherever package.json lives
    schedule: { interval: daily }
    groups:
      ocx-theme: { patterns: ['@ocx-sh/theme'] }
```
