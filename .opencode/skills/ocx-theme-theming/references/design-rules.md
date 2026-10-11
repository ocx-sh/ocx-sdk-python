# Design rules

The `ocx-design` rules for ocx.sh, as do and don't pairs. Read this before
choosing a colour, type size, shape or shadow. A rule that only restates a
generic CSS rule cites its `css-theming` ID (`grim add ghcr.io/ocx-sh/lore/css-theming:latest`).

## Colour

**Coral is the only interactive colour.** It marks links, the current or
selected item, the one primary action per view, and focus.

- Do: hover a row, option or menu item with `--ocx-color-hover`; hover a
  bordered control with `--ocx-color-hover-border`.
- Don't: tint a hover coral, or use coral for a status. Status is `success`,
  `warning` or `danger`.

**Small text on light uses `accent-fg`.** Coral on white fails contrast below
18px.

- Do: `color: var(--ocx-color-accent-fg)` for a link in body text; ink on a
  coral fill is `--ocx-color-on-accent`.
- Don't: `color: var(--ocx-color-accent)` on small text over a light surface.

**Override colour twice.** Colour is the only per-scheme family. Every colour
token and every colour override needs a light value and a dark value.
Generic rule: `CSS-TOK-03`.

- Do: set the override under `:root` and again under `.dark`.
- Don't: override only the light value. Dark pins to the theme default.

## Type

**Mono for nav, labels and identifiers; sans for sentences.** Commands,
versions and paths are identifiers.

- Do: `--ocx-font-mono` on nav, labels, commands, versions, paths;
  `--ocx-font-sans` on prose.
- Don't: set a sentence in mono for looks, or an identifier in sans.

**UI density 13px, prose 15.5px.** Chrome, nav, tables and cards use the UI
scale; reading text uses the prose size.

- Do: `--ocx-text-*` for chrome; the prose token for body copy. Nothing below
  15px outside the UI scale.
- Don't: shrink prose to fit a layout, or grow chrome to match prose.

## Shape and depth

**Square corners; 2px at most on large surfaces.**

- Do: radius 0 on controls and chips; `--ocx-radius-lg` or `--ocx-radius-xl`
  (2px) on large surfaces; `--ocx-radius-full` only for round marks (status
  dot, radio).
- Don't: round a button or chip, or raise a radius above 2px.

**Shadows only on floating surfaces.**

- Do: `--ocx-shadow-overlay` on popovers, menus, dialogs and toasts.
- Don't: shadow a card, a panel or a static control. Surfaces are flat.

## Values

**Tokens only, never raw values.** Generic rule: `CSS-TOK-01`.

- Do: `var(--ocx-space-4)`. A value no token carries means a token is
  missing: propose it (`adding-a-token.md`).
- Don't: write `16px`, a hex or `rgb()`, or a one-off `z-index` or duration.

## Assets

**Assets never cause flicker or a broken look.** The page must look identical
with images blocked; the e2e suite enforces it.

- Do: inline UI icons and the logo as optimized SVG, 2 KB or less each. Give
  every image a reserved box (`width` and `height`, or `aspect-ratio`). Send
  raster images through Astro `<Image>` or `<Picture>` with responsive widths
  and avif or webp. Load styles for first-paint content before that content.
- Don't: use an `<img>` or CSS `url()` for a UI icon or the logo (it loads
  late). Don't leave an image without a reserved box. Don't put styles for
  first-paint content inside that content.

## Icons

**An agent never draws, redraws or edits icon paths.** One icon, one meaning;
never swap an icon for looks. Full rules: `ocx-theme-icons`.

- Do: take every icon from Lucide or Simple Icons through `NAME_MAP` in
  `generate-icons.mjs`. A custom glyph is allowed only for a brand missing from
  Simple Icons (or over its 2 KB cap), copied verbatim from a licensed upstream
  file with its source URL and licence beside the entry.
- Don't: write or tweak a `d` attribute. No licensed source exists? Ask the
  owner.
