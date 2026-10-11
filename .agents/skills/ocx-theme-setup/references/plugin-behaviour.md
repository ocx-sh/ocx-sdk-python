# Plugin behaviour in detail

You loaded this file because you are about to override something the
`ocxTheme()` plugin sets, or a themed build looks wrong.

Contents: [Overrides](#overrides) · [Merge rules](#merge-rules) ·
[Fonts](#fonts) · [Search merge](#search-merge) · [Build output](#build-output)

## Overrides

The plugin maps each of these Starlight component slots to
`@ocx-sh/theme/starlight/<Name>.astro`. A key in your own `components`
replaces the theme's for that slot only.

| Slot | What the theme's version does |
|---|---|
| `Head` | Starlight's head plus preloads of the four upright font faces, and `html[data-platform]` set before paint (Kbd reads it) |
| `Header` | Brand, sections, ecosystem mega-menu, install button, GitHub and theme icons; rendered at build time from `nav.json` |
| `Footer` | `nav.json` footer links, licence and copyright line |
| `PageTitle` | Breadcrumb trail (the `breadcrumbs` option) and the H1 |
| `TableOfContents` | Adds "edit this page" and "report an issue" links |
| `ThemeSelect` | Icon toggle (a CycleButton) shared with the mobile menu. A switch crossfades the page (a view transition, `--ocx-duration-moderate`); under reduced motion, or without the API, it flips at once |
| `ThemeProvider` | Inline pre-paint theme script, no markup |
| `MobileMenuFooter` | Icon theme toggle inside the mobile menu |
| `MarkdownContent` | Wraps every page body |
| `Search` | Search dialog on Zag, Pagefind UI loaded on first open; its script also mounts nav menu, mobile drawer, sidebar collapsibles and toaster |
| `Sidebar` | Grouped sidebar with Zag collapsibles |
| `MobileMenuToggle` | The mobile menu button |
| `PageFrame` | Header, sidebar pane and main layout |

Overriding `Search`: the warning text names the fix. Call
`mountChrome(document)` from `@ocx-sh/theme/chrome` in your override's `<script>`. Without it
the header nav and the mobile menu fall back to native popovers, nested
sidebar groups render open, no toast ever shows and the outline's current
marker never fades.

Chrome overrides you write must not import a styled Starlight component: its
stylesheet would inline into every page. Style Starlight markup from your own
`customCss` instead.

## Merge rules

| Setting | Rule |
|---|---|
| `customCss` | Theme files first, then yours in your order. Yours is outside the `ocx` cascade layer, so a plain rule of yours beats any theme rule without `!important` |
| `components` | Theme map, then your keys on top |
| `expressiveCode` | `false` keeps code blocks unthemed. An object merges over the theme's; `styleOverrides` and `styleOverrides.frames` merge one level deep; `shiki.langs` concatenates (the theme adds Elvish and PowerShell parameter grammars) |
| `pagefind` | `false` stays `false`. Otherwise the theme's `mergeIndex` list comes first; your entries are appended unless their `bundlePath` is already there |
| `vite.build.assetsInlineLimit` | Theme inlines CSS files under 1024 bytes only when you have not set this |
| client code splitting | Theme groups the every-page modules into one chunk only when you set no `codeSplitting` or `manualChunks` of your own |
| `vite.optimizeDeps.include` | Theme adds its lazily loaded Zag machines and the asciinema player; your list is kept |

## Fonts

The plugin registers two Fontsource families through the Astro Fonts API:

| Family | Weights | CSS variable |
|---|---|---|
| IBM Plex Sans | 400, 600 | `--ocx-font-sans` |
| IBM Plex Mono | 400, 600 | `--ocx-font-mono` |

Fallbacks lead with the metric-matched `OCX Sans Fallback` and
`OCX Mono Fallback` faces from `base.css`, so the swap causes no layout shift.
Use the variables, never a family name. Do not add a third family: every
font file counts against the page weight budget.

## Search merge

Each section builds its own Pagefind bundle at `<claim>pagefind/`. The plugin
adds one `mergeIndex` entry per other claim with `search: true`:
`{ bundlePath: '<claim>pagefind/', mergeFilter: { section: '<label>' }, indexWeight: 1 }`.
The label is the claim's ecosystem entry, section or hub label. A section
that is not deployed yet simply contributes no results.

## Build output

The injected integration also:

- sets `site: 'https://ocx.sh'`;
- injects `/favicon.svg` from the logo when neither `public/favicon.svg` nor
  Starlight's `favicon` exists;
- registers the route middleware that computes the breadcrumb trail into
  `starlightRoute.ocxTrail`;
- serves the plugin options to that middleware as `virtual:ocx-theme/options`.
