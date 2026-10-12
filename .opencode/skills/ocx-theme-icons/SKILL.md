---
name: ocx-theme-icons
description: Iconography rules for @ocx-sh/theme on ocx.sh - the icon registry, the NAME_MAP in generate-icons.mjs, Lucide for interface icons, Simple Icons for brand, OS and shell glyphs, the narrow licensed-custom exception, and the hard rule that an agent never draws or edits SVG paths. Use when a page or component needs an icon, when rendering <Icon name>, a TabItem icon or PlatformIcons, when an icon is missing from the registry, when someone asks to add, swap, redraw, recolour or resize an icon or logo, when icons.generated.mjs is stale or a hand edit is tempting, or when an SVG, img or url() icon appears in markup or CSS. Not for raster images or screenshots (use Astro Image or Picture) or for token colours (ocx-theme-theming).
license: Apache-2.0
metadata:
  summary: The @ocx-sh/theme icon registry - Lucide and Simple Icons only, NAME_MAP as the one place to add an icon, never hand-drawn paths
  keywords: ocx,ocx-sh,icons,iconography,svg,lucide,simple-icons,registry,name-map,inline-svg,accessibility,currentcolor,astro,starlight
---

# ocx-theme-icons

Every icon on ocx.sh comes from one registry in `@ocx-sh/theme` and renders
as inline SVG in `currentColor`. Stability matters more than looks: one icon,
one meaning, and an icon is never swapped because another looks nicer.

## Hard rule: never draw an icon

An agent never draws, redraws, traces, simplifies or edits SVG path data, and
never writes a `d`, `points` or shape attribute from its own knowledge. Every
icon comes from Lucide or Simple Icons through the generator. A custom glyph
is allowed only for a brand missing from Simple Icons (or over the 2 KB cap
there), copied verbatim from a licensed upstream file whose source URL and
licence sit next to its `NAME_MAP` entry. No licensed source exists? Stop and
ask the owner; do not approximate one.

## Using an icon

```astro
---
import Icon from '@ocx-sh/theme/components/ui/Icon.astro';
---
<Icon name="copy" />                         <!-- decorative, aria-hidden -->
<Icon name="alert" label="Error" size="lg" /> <!-- meaningful: has a name -->
```

| Prop | Notes |
|---|---|
| `name` | A registry name; the type (`IconName`) rejects anything else |
| `label` | Gives the icon an accessible name. Without it the icon is hidden from assistive tech, which is right when text beside it says the same |
| `size` | `xs` 10, `sm` 11, `md` 14 (default), `lg` 20 (`--ocx-icon-*`) |

An icon-only button names the button, not the icon:
`<Button iconOnly aria-label="Copy"><Icon name="copy" /></Button>`.

Components take registry names too: `icon` on Select options, Menu and
ActionMenu items, CommandBar choices, ToggleGroup items, Breadcrumbs items,
CycleButton states, and `TabItem` (`shell`, `powershell`, `nushell`, `fish`,
`elvish`, `cmd`). `PlatformIcons` maps `os/arch` strings to the OS glyphs.

Never place an icon as `<img src="….svg">`, a CSS `url()` or a
`background-image`: those load late and flicker. Colour comes from the
parent's `color` (a token); an icon never carries a colour of its own.

## What the registry holds

`@ocx-sh/theme/icons` exports `ICONS` (name → `{ viewBox, body, stroke?, group, keywords }`),
`ICON_NAMES` and `ICON_SOURCES`. Groups: `interface`, `action`, `brand`,
`os`, `shell`. The showcase site lists every icon with a search.

| Source | Covers | Licence |
|---|---|---|
| Lucide (`@iconify-json/lucide`) | Interface and action icons, the generic `shell`, `elvish` (Lucide `lambda`); stroked at `--ocx-icon-stroke` | ISC (MIT for Feather-derived) |
| Simple Icons (`@iconify-json/simple-icons`) | `github`, `apple`, `windows`, `powershell`, `nushell`, `fish`; filled | CC0-1.0 |
| Custom, verbatim upstream | `linux` (Material Design Icons, Apache-2.0; Simple Icons' Tux is over 2 KB), `cmd` (vscode-icons, MIT) | per entry, in THIRD_PARTY_NOTICES |

## Adding an icon

The registry changes only in the theme repository (`ocx-sh/website`). A
consumer site that needs a new icon opens a pull request there; it never
inlines its own SVG.

1. Pick the meaning first, then the icon. Reuse an existing name if the
   meaning is the same. A new meaning gets a new name.
2. Find it in Lucide (interface, action) or Simple Icons (brand, OS, shell).
3. Add one line to `NAME_MAP` in `packages/theme/scripts/generate-icons.mjs`:

   ```js
   download: [['lucide', 'download'], 'action', 'save install archive get'],
   ```

   The shape is `name: [[source, upstreamName], group, keywords]`, where the
   keywords feed the catalog search.
4. Regenerate: `pnpm --filter @ocx-sh/theme icons`. Commit
   `icons.generated.mjs`, `icons.sources.generated.mjs` and `icons.d.ts`
   with the `NAME_MAP` change. Never edit the generated files by hand; a unit
   test fails when they are stale.
5. Keep each icon at 2 KB or less.
6. Custom glyph (exception only): `['custom', { viewBox, body }]` with the
   body copied verbatim from the licensed upstream file, a comment above the
   entry giving the source URL and licence, and a line in
   `packages/theme/THIRD_PARTY_NOTICES`.

## Changing an icon

Swapping the upstream icon behind an existing name changes a meaning every
page relies on. Do it only when the owner asks, and never for looks.
