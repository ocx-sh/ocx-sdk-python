# Content and navigation

You loaded this file because a page shows a terminal recording, a landing
block, keys, a breadcrumb trail, an in-page table of contents, an icon or the
logo.
Import path prefix: `@ocx-sh/theme/components/`.

Contents: [Terminal](#terminal) · [FeatureSection](#featuresection) ·
[HubGrid](#hubgrid) ·
[Kbd](#kbd) · [Breadcrumbs](#breadcrumbs) · [Toc](#toc) · [Icon](#icon) ·
[Logo](#logo) · [Chrome](#chrome) · [Starlight built-ins](#starlight-built-ins)

## Terminal

**`Terminal.astro`** plays a recorded asciinema `.cast` in terminal chrome.
The cast file loads with the page; the player JS and CSS load only when a
player is created.

| Prop | Notes |
|---|---|
| `src` | Required; root-absolute (`/casts/install.cast`), always prefixed with the site base, so write it base-relative (never `/docs/casts/…` under `/docs/`) |
| `cols`, `rows` | Set both: with `fit="width"` they reserve the player height before it loads (no layout shift) |
| `title` | Centred in the chrome |
| `autoPlay` | Default false. Keep it: no autoplay is the docs rule |
| `speed`, `idleTimeLimit` (default 2 s), `loop` | Playback |
| `fit` | `width` (default), `height`, `both`, `none` |
| `collapsed` | Chrome only; its button expands the player (a Zag collapsible) |

Record casts from a real command run; do not hand-write them.

## FeatureSection

**`FeatureSection.astro`**: the alternating landing block. `title` (required,
rendered as `<h2>`), `flip` (visual on the left on desktop; mobile always
stacks text first). Slot `text` is the copy, the default slot the visual. A
one-shot reveal plays once when the block scrolls in.

## HubGrid

**`HubGrid.astro`**: one hub page's body, read from `nav.json`. `hub`
(required, a hub id: `integrations` or `apps`; an unknown id throws). One
group per category in `nav.json` order, one card per entry. A `planned`
entry is a dashed card with a "planned" stamp and no link; an external
`href` adds the `external` icon (↗) named "(external)". Add entries in
`nav.json`, never in the page. Zero JS.

## Kbd

**`ui/Kbd.astro`**: `keys` as `+`-joined names (`"Mod+Shift+K"`), or one key
in the default slot. `Mod` renders Cmd on mac and Ctrl elsewhere; `Alt` and
`Super` get per-OS glyphs. All variants render and CSS shows one, keyed off
`html[data-platform]` that the theme's `Head` sets before paint, so nothing
flickers. `platform` (`auto`, `mac`, `windows`, `linux`) forces one variant
at build time.

## Breadcrumbs

The page title already carries a trail (the plugin's `breadcrumbs` option).
Use **`ui/Breadcrumbs.astro`** only for a trail elsewhere: `items`
(`{ label, href?, icon? }`, outermost first, the last is the current page),
`label` (landmark name), `id`. Zero JS: past three items a narrow container
folds the middle into a native popover.

## Toc

**`Toc.astro`** is a scroll-spy table of contents for a region inside a
page, not Starlight's page TOC. `items` (required,
`{ id, label, depth }[]`), `label`. Plain anchors work before the machine
starts; it starts when scrolled into view, the only `visible` trigger.
Event `ocx:toc:change`.

## Icon

**`ui/Icon.astro`**: `name` (required, a registry name, type-checked),
`label` (makes it meaningful; without it the icon is decorative and hidden
from assistive tech), `size` `xs` 10, `sm` 11, `md` 14, `lg` 20. It inlines
the SVG in `currentColor`. The registry and how to add an icon: the
`ocx-theme-icons` skill at `../ocx-theme-icons/SKILL.md`.

## Logo

**`ui/Logo.astro`** inlines the theme's `logo.svg` unchanged. `wordmark`
(text beside the mark, mono and lowercase; default `false`, the mark only),
`size` `s` 14, `m` 20 (default), `l` 38, `xl` 84, `tone` `brand` (accent,
default) or `current` (text colour), `href` (a link; otherwise a span),
`label` (default `'ocx'`; an empty string makes it decorative, a link needs
one), `class`. The header's brand renders through it. Never redraw the mark,
edit its paths or its `fill`, or draw a wordmark SVG: colour comes from CSS.

## Chrome

**`EcosystemMenu.astro`** is the header's ecosystem mega-menu, rendered by the
theme's `Header` from `nav.json`. It is exported for the header only; never
place it in page content.

## Starlight built-ins

The theme restyles these; import them from `@astrojs/starlight/components`:
`Aside`, `Badge`, `Card`, `CardGrid`, `LinkCard`, `Steps`. Code blocks use
Expressive Code with the theme's frame and token colours. For tabbed content
use the theme's `Tabs` and `TabItem` (see `overlays-disclosure.md`).
