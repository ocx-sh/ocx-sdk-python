---
name: ocx-theme-theming
description: Styling rules for sites and components that wear @ocx-sh/theme on ocx.sh - the --ocx-* design tokens, colour as the only per-scheme token family, light and dark parity, the coral accent rule, the focus ring, the ocx cascade layer, forced-colors support, and how to add a token. Use when writing or reviewing CSS, a <style> block, customCss, or a component stylesheet for an ocx.sh site, when choosing a colour, size, spacing, radius, shadow, z-index or motion value, when dark mode or Windows high contrast looks wrong, when a theme rule does not apply or a consumer rule does not win, when adding or renaming a --ocx- token, or when a raw hex, rgb() or px value appears in styles. Not for icons (ocx-theme-icons) or component choice (ocx-theme-components).
license: Apache-2.0
metadata:
  summary: Tokens-only styling for @ocx-sh/theme - colour per scheme, the accent rule, focus ring, cascade layer, dark parity, adding a token
  keywords: ocx,ocx-sh,css,design-tokens,custom-properties,theming,dark-mode,light-mode,color-scheme,accent,focus-ring,cascade-layers,forced-colors,high-contrast,starlight
---

# ocx-theme-theming

`@ocx-sh/theme/tokens.css` holds every design value as a `--ocx-*` custom
property. Styles reference tokens only. This skill covers using them in a
consumer site and adding one to the theme. Families, names and values:
[references/tokens.md](references/tokens.md).
The design rules (colour, type, shape, depth, assets, icons) as do and
don't pairs: [references/design-rules.md](references/design-rules.md).

For the generic cascade and token discipline this builds on, see the
`css-theming` rule (`grim add ghcr.io/ocx-sh/lore/css-theming:latest`).

## Non-negotiables

1. **Tokens only, never raw values.** No hex, `rgb()`, named colour, or
   free-standing `px` for colour, type size, spacing, radius, shadow,
   z-index or duration. `var(--ocx-space-4)`, not `16px`. A value no token
   carries means a token is missing: propose it upstream (below), never
   inline it.
2. **Colour is the only per-scheme family.** Light sits on `:root` (and
   `:root[data-theme='light']`), dark on `.dark` and
   `:root[data-theme='dark']`. Every colour token has both; geometry, type and
   motion tokens are declared once.
3. **Coral is the only interactive colour.** Accent marks links, the current
   or selected item, the one primary action per view, and focus. Every other
   hover is neutral: rows, options and menu items take
   `--ocx-color-hover`; bordered controls take `--ocx-color-hover-border`.
   Status uses `success`, `warning`, `danger`, never accent.
4. **Small text on light uses `--ocx-color-accent-fg`**, not
   `--ocx-color-accent` (coral on white fails contrast below 18px). Ink on a
   coral fill is `--ocx-color-on-accent`, the same in both schemes.
5. **One focus ring.** `outline: var(--ocx-focus-ring); outline-offset: var(--ocx-focus-offset);`
   on `:focus-visible`. A box with `--ocx-radius-lg` uses
   `--ocx-focus-offset-inset` so the ring stays square. Never remove an
   outline without drawing this ring instead.
6. **Sharp shapes, flat surfaces.** Radius 0 on controls and chips, 2px
   (`--ocx-radius-lg`, `--ocx-radius-xl`) at most on large surfaces;
   `--ocx-radius-full` only for round marks (status dot, radio). Shadows
   (`--ocx-shadow-overlay`) only on floating surfaces: popovers, menus,
   dialogs, toasts.
7. **Mono for identifiers, sans for sentences.** `--ocx-font-mono` for nav,
   labels, commands, versions, paths; `--ocx-font-sans` for prose. The UI
   scale (`--ocx-text-*`) is the only place sizes below 15px live.
8. **No `!important`.** The cascade layer decides (next section).

## The cascade layer

`tokens.css` opens with `@layer starlight, ocx;`. Every theme rule lives in
`@layer ocx`. Your `customCss` and page styles are unlayered, so a plain rule
of yours beats any theme rule regardless of specificity.

- Want to change a theme look for your site? Write a plain, unlayered rule.
- Writing CSS inside the theme itself? Put it in `@layer ocx`; only
  `@font-face` sits outside.
- Style Starlight markup from CSS, not by importing a styled Starlight
  component into a chrome override (its stylesheet would inline into every
  page).

## Dark and light parity

Test every change in both schemes (`[data-theme]` flips on `<html>`). Tints
are translucent so they read on `bg`, `surface` and `surface-subtle` alike.
Do not invert a colour by hand in a component: reference the token and let
the scheme swap its value.

## Forced colors

Windows high contrast replaces author colours. Custom-drawn controls (a
checkbox box, a switch knob) must repaint with system colours under
`@media (forced-colors: active)`. Follow the theme's `choice.css` pattern:

```css
@media (forced-colors: active) {
  .my-box::before {
    forced-color-adjust: none;
    background-color: CanvasText;
    border-color: CanvasText;
  }
}
```

Use `forced-color-adjust: none` only on the element you repaint; never on a
container.

## Motion

Use `--ocx-duration-*` and `--ocx-ease-*`. `tokens.css` zeroes them under
`prefers-reduced-motion: reduce`, so a transition built on them needs no
media query of its own. The motion contract (C-302):

- Tokens only: no raw `ms`/`s` (except `0s`), no `ease`/`cubic-bezier()`
  keyword, and never `transition: all`. `linear` is for infinite loops.
- Animate `transform`, `opacity` and colour, not size.
- First paint is final. A rule that can match at load (`@starting-style`, a
  state set on mount) sits under `[data-zag-state='live']`, or `[data-live]`
  for a plain script. A hover-only colour fade needs no gate.
- Exit with `display`/`overlay` plus `allow-discrete` when the node stays in
  the DOM; a node the script removes leaves through the internal `leave()`.
- Focus rings, and a field's focus border, never animate.
- `motion-tokens.test.ts` fails on any break under `packages/theme/src`.

## Adding or changing a token

Tokens live in the theme repo (`ocx-sh/website`), never in a consumer. A
consumer redefining `--ocx-*` is a fork that the next theme release silently
fights. The procedure: [references/adding-a-token.md](references/adding-a-token.md).
