---
name: ocx-theme-quality
description: Quality gates for sites and components built on @ocx-sh/theme for ocx.sh - Lighthouse 100 in all four categories, page weight, JavaScript, HTML, DOM and heap budgets, no flicker and no layout shift, lazy hydration, accessibility, forced colors, dark and light parity, link and layout checks with ocx-site check, and the full theme gate before a merge. Use when setting up CI or tests for an ocx.sh section, when a Lighthouse score drops below 100, when a page is too heavy or loads JavaScript before interaction, when images flicker or shift layout, when deciding which Playwright or axe checks a consumer site should keep, when raising a budget, or before merging a change to ocx-sh/website. Not for fixing a specific component API (ocx-theme-components) or the deploy workflow (ocx-theme-deploy).
license: Apache-2.0
metadata:
  summary: The gates an ocx.sh site keeps - Lighthouse 100x4, budgets, no flicker, lazy hydration, a11y, ocx-site check
  keywords: ocx,ocx-sh,quality,lighthouse,performance,accessibility,budgets,core-web-vitals,cls,playwright,axe,e2e,hydration,flicker,forced-colors,astro,starlight
---

# ocx-theme-quality

The bar for every page on ocx.sh: **Lighthouse 100 in performance,
accessibility, best practices and SEO**, and a first paint that is final.
The theme is built to hit that; a consumer site keeps it by keeping the gates
below. The theme's own values live in `tests/budgets.mjs` of
`ocx-sh/website`.

## Gates a consumer site keeps

| Gate | Command or check | Blocks on |
|---|---|---|
| Build check | `npx ocx-site check --dist dist` | A root-relative link outside every claim, a `dist` layout that does not match the claim, a Pagefind bundle from another version |
| Lighthouse | lhci over representative built pages, mobile preset | Any category below score 1 |
| Budgets | lhci assertions plus a Playwright measurement | Any page over the budgets below |
| No flicker | Playwright: layout identical with images blocked | Any element box that moves |
| Lazy hydration | Playwright: no Zag root leaves `idle` without input | JavaScript loading before interaction |
| Accessibility | axe on each page, with menus and dialogs open | Any violation |
| Links | `lychee` over `dist` | A dead link |

Playwright recipes for the three behaviour gates, forced colors and both
schemes: [references/playwright-gates.md](references/playwright-gates.md).

## Budgets

Start from the theme's content-page budget and only ratchet down:

| Measure | Budget (content page) |
|---|---|
| HTML per page, gzip | 14,200 bytes (fits the first round trip; over it costs a simulated RTT and the score drops to 0.99) |
| Script before any input, gzip | 11 KiB |
| Script after touching every widget, gzip, Pagefind excluded | 64 KiB |
| Pagefind scripts, gzip | 58 KiB |
| Total transfer | 147 KiB |
| DOM elements | 800 |
| JS heap | 4 MiB |

A budget goes up only with a written reason in the commit (what grew, why it
is worth it, the new measured value). Page HTML is the budget most often
blown: every flat sidebar item, inline script and inline style lands in every
page.

## Rules that keep the score

- **First paint is final.** Initial state comes from props on the server; no
  script corrects content after load.
- **Nothing loads before interaction.** Heavy code loads through a dynamic
  `import()` on first hover, focus or touch. Never add a top-level import of
  a large library to a page script.
- **Reserved boxes.** Every image has `width` and `height` or an
  `aspect-ratio`; a `Terminal` has `cols` and `rows`; late content sits in a
  `Skeleton`. The page must look identical with images blocked.
- **Inline icons, never late ones.** UI icons and the logo are inline SVG
  (see `ocx-theme-icons`), never `<img>` or CSS `url()`.
- **Raster images** go through Astro `<Image>` or `<Picture>` with responsive
  widths and avif/webp.
- **Styles before content.** First-paint CSS loads in the head, never inside
  the content it styles. Do not import a styled Starlight component into a
  chrome override: its stylesheet inlines into every page.
- **Fonts**: only the theme's two families at 400 and 600. A third family or
  weight costs a preload and the performance score.
- **Contrast and focus** come from tokens (`ocx-theme-theming`); a raw colour
  is the usual cause of an accessibility drop.

## Consumer Lighthouse recipe

Three consumers (rules_ocx, ocx-sdk-python, find_ocx) run the same pipeline
from `site/`; copy it, do not reinvent it.

- Copy a trimmed `scripts/lighthouse.mjs` from `ocx-sh/website` into `site/`,
  with a `lighthouse.budgets.mjs` (budgets, lhci assert matrix, page
  discovery). Build, serve with `astro preview` under the claimed `base`,
  audit every built page mobile, judge with lhci's assertion engine; a failing
  page reruns up to 3 times and is judged on its median run. Sequential and
  uncached is fine at about 20 pages.
- Budgets start from the `content` class of the theme's `tests/budgets.mjs`
  and never go above it.
- `HTML_GZ_MAX` is 14,200 bytes per page. A long generated reference page
  (Stardoc, Griffe, Sphinx) blows it: split it into several pages in the port
  script, never raise the cap.
- `404.html` is not auditable under `astro preview`: the server answers it with
  status 404, so leave it out of page discovery.
- After pinning the theme as a git dependency, refresh the pnpm lockfile
  (`pnpm install`); a stale lockfile fails CI or audits the old theme.
- Run the full audit only at finalize; before that, check the config and unit
  test the budgets.

## When the score drops

1. Rebuild and rerun Lighthouse on the one page; read the failing audit.
2. Performance: compare HTML gzip and pre-input script size against the
   budgets. The usual causes are a new inline script, an eager import, or a
   large sidebar.
3. Accessibility: run axe; look for a missing `label`, an icon-only button
   without `aria-label`, or a colour outside the tokens.
4. CLS: run the images-blocked test; something lacks a reserved box.

## Working on ocx-sh/website itself

Before every merge run `task check` (lint, format, typecheck, tests, build,
built-CSS gate), then `task e2e`, `task lighthouse`, `task visual` and
`task pack`, all through `ocx exec -- task <name>`. Run the heavy ones (e2e,
lighthouse, visual) one at a time; together they exhaust memory. Every
component needs a showcase page under
`examples/starlight/src/content/docs/components/` covering its states, and
every change to a component, prop, plugin option, token rule or the deploy
action updates the matching skill under `skills/` in the same commit; a test
fails when an exported component or plugin option is missing from the
skills.
