# Loading model: lazy Zag, final first paint

You loaded this file because a component loads JavaScript too early, does not
react, shifts layout, or you are writing a component of your own on top of the
theme.

Contents: [Triggers](#triggers) · [Root attributes](#root-attributes) ·
[Every-page script](#every-page-script) · [Writing your own](#writing-your-own)

## Triggers

Behaviour comes from Zag state machines. Each Zag-backed component has one
`*.zag.mjs` module, imported lazily; the page loads only a small trigger layer
before input.

| Trigger | Starts on | Used by |
|---|---|---|
| `interaction` (default) | First `pointerenter`, `focusin` or `touchstart` on the root; the first activating key is replayed | Every Zag component not listed below |
| `visible` | The root scrolling into view | `Toc` only |
| `manual` | An explicit start | Search dialog, toaster, mobile menu toggle |
| eager | Page load | A `Dialog` or `Drawer` rendered with `open` |

Components without a machine render plain HTML and a small delegated script,
or no script at all: `Button`, `Link`, `Label`, `InputGroup`, `Meter`,
`ProgressCircle`, `Skeleton`, `Loader`, `Breadcrumbs`, `Kbd`, `Icon`,
`PlatformIcons`, `ToggleButton`, `CycleButton`, `Tree`, `Tooltip`, `Menu`
(native popover).

Because the server renders every part, a component is usable before its
machine starts: links navigate, native inputs submit, `Tabs` shows the
selected panel, `Toc` links jump.

## Root attributes

Useful when a test or a bug report needs the state of a machine:

| Attribute | Values |
|---|---|
| `data-zag-root` | Marks a Zag-backed root |
| `data-zag-trigger` | `interaction` (or absent), `visible`, `manual` |
| `data-zag-state` | `idle` → `loading` → `live`, or `error` |

A page with no input for two seconds after load must show every
interaction root `idle`. The theme's own e2e gate asserts exactly that; copy
the idea into a consumer gate (see `ocx-theme-quality`).

## Every-page script

In Starlight, the theme's `Search.astro` override carries one script that
also mounts the header nav menu, the sidebar collapsibles, the mobile menu
drawer, the toaster, and the delegated click listener of `CycleButton`.
Overriding `Search` without calling `mountChrome(document)` (from
`@ocx-sh/theme/chrome`, which holds that script) loses all of them. Outside
Starlight, `CycleButton` needs `install()` from `@ocx-sh/theme/cycle-button`.

## Writing your own

- Build on the primitives (`Button`, `InputGroup`, `Select`) rather than
  restyling native elements.
- Render the final state on the server; let the client only react.
- A heavy dependency loads on first interaction via dynamic `import()`, never
  at the top of a page script.
- Reserve the box of anything that appears later.
- Zag imports stay in one `*.zag.mjs` module per component in the theme; a
  consumer site does not import `@zag-js/*` itself. Need a machine the theme
  lacks? Propose the component upstream in `ocx-sh/website`.
