# Token families

You loaded this file because you need the right `--ocx-*` token for a value.
The source of truth is `@ocx-sh/theme/tokens.css`; read it for exact values.

Contents: [Colour](#colour) · [Type](#type) · [Space and layout](#space-and-layout) ·
[Shape and focus](#shape-and-focus) · [Sizes](#sizes) · [Motion and stacking](#motion-and-stacking)

## Colour

Per scheme: every name below has a light and a dark value.

| Role | Tokens |
|---|---|
| Surfaces | `--ocx-color-bg`, `--ocx-color-surface`, `--ocx-color-surface-subtle`, `--ocx-color-inverse` |
| Borders | `--ocx-color-border`, `--ocx-color-border-strong` (frames, dashed cards), `--ocx-color-border-control` (control edges, 3:1) |
| Text | `--ocx-color-fg`, `--ocx-color-fg-muted`, `--ocx-color-fg-subtle`, `--ocx-color-on-inverse` |
| Accent | `--ocx-color-accent`, `--ocx-color-accent-hover`, `--ocx-color-accent-fg` (small text on light), `--ocx-color-accent-tint`, `--ocx-color-accent-tint-border`, `--ocx-color-on-accent` |
| Focus | `--ocx-color-focus` (the ring and thin active markers) |
| Interaction | `--ocx-color-hover` (neutral hover tint), `--ocx-color-hover-border` |
| Status | `--ocx-color-success`, `--ocx-color-warning`, `--ocx-color-danger`, each with a `-tint` |
| Keyword | `--ocx-color-keyword`, `--ocx-color-keyword-tint` (a category hue, not interactive) |
| Code | `--ocx-color-code-bg`, `-fg`, `-comment`, `-punctuation`, `-keyword`, `-string`, `-number`, `-function`, `-variable`, `-deleted`, `-prompt` |
| Overlay | `--ocx-color-overlay` (scrim), `--ocx-color-shadow` |
| Scrollbars | `--ocx-color-scrollbar-thumb`, `-thumb-hover`, `-track` |

Warning and keyword tints are label-grade: their ink stays at 4.5:1 on the
tint over every surface.

## Type

| Tokens | Use |
|---|---|
| `--ocx-font-sans`, `--ocx-font-mono` | Families (set by the Fonts API in Starlight) |
| `--ocx-font-weight-regular`, `-medium`, `-semibold`, `-bold` | Weights; only 400 and 600 files load |
| `--ocx-text-2xs` … `--ocx-text-2xl` | UI scale: chrome, nav, tables, cards. The only sizes below 15px |
| `--ocx-prose-*` | Prose body, headings (`-h1` … `-h4` with margins), code, measure (`--ocx-prose-width`) |
| `--ocx-display-*` | Landing display sizes |
| `--ocx-lh-*`, `--ocx-tracking-*` | Line heights and letter spacing; `--ocx-tracking-caps` for every mono uppercase label |

## Space and layout

`--ocx-space-1` (2px) … `--ocx-space-12` (112px) on a 4px base. Layout:
`--ocx-header-height`, `--ocx-subbar-height`, `--ocx-sidebar-width`,
`--ocx-outline-width`, `--ocx-page-max`, `--ocx-landing-max`,
`--ocx-card-pad`. `--ocx-bp-outline` and `--ocx-bp-drawer` are reference
values only: media queries cannot read custom properties, so a query writes
the number and cites the token in a comment.

## Shape and focus

| Tokens | Value and use |
|---|---|
| `--ocx-radius-sm`, `--ocx-radius-md` | 0: chips, kbd, inputs, buttons |
| `--ocx-radius-lg`, `--ocx-radius-xl` | 2px: cards, code blocks, tables, floating palette |
| `--ocx-radius-full` | Round marks only |
| `--ocx-border-width`, `-emphasis`, `-strong`, `-marker` | 1, 1.5, 2, 3px |
| `--ocx-shadow-overlay` | Floating surfaces only |
| `--ocx-focus-ring`, `--ocx-focus-offset`, `--ocx-focus-offset-inset` | The one focus style |

## Sizes

Icons `--ocx-icon-xs` (10) … `--ocx-icon-lg` (20) and `--ocx-icon-stroke`
(one line weight at every size). Controls `--ocx-control-choice`,
`--ocx-control-xs` … `--ocx-control-3xl` (38 = md field and button, 42 = lg).
Component geometry: `--ocx-dialog-width`, `--ocx-drawer-width`,
`--ocx-toast-width`, `--ocx-slider-thumb`, `--ocx-track-size`,
`--ocx-search-width`, `--ocx-panel-*` (ecosystem menu).

## Motion and stacking

Durations `--ocx-duration-fast`, `-enter`, `-base`, `-moderate`, `-slow`
(zeroed under reduced motion), easings `--ocx-ease-out` (enter),
`--ocx-ease-in-out` (move), `--ocx-scale-enter`.

Stacking, low to high: `--ocx-z-header`, `--ocx-z-drawer-backdrop`,
`--ocx-z-drawer`, `--ocx-z-popover`, `--ocx-z-modal-backdrop`,
`--ocx-z-modal`, `--ocx-z-tooltip`, `--ocx-z-toast`, `--ocx-z-skip-link`.
Never write a bare z-index number.
