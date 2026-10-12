---
name: ocx-theme-components
description: Component catalog of @ocx-sh/theme for Astro and Starlight pages on ocx.sh - import paths, props, accessibility contracts, events, lazy Zag loading (nothing loads before interaction, first paint is final). Use when writing or reviewing an .astro or .mdx page that imports from @ocx-sh/theme/components, when choosing between Button, Link, CopyButton, CommandBar, ToggleButton, CycleButton, ToggleGroup, Input, InputGroup, SearchField, Select, Combobox, Choice, RadioGroup, Slider, Tag, TagGroup, TagsInput, Dialog, AlertDialog, ConfirmDialog, Drawer, Popover, Menu, ActionMenu, Tooltip, Hint, Accordion, Collapsible, Tabs, Tree, TreeView, List, DataTable, Pagination, Meter, ProgressCircle, Loader, Skeleton, toast, Avatar, Terminal, Kbd, Breadcrumbs, Toc, Icon, PlatformIcons, FeatureSection, HubGrid or DependencyExplorer or when a component fires no event, loads JavaScript early, shifts layout or fails an a11y check. Not for astro.config setup (ocx-theme-setup) or colours and tokens (ocx-theme-theming).
license: Apache-2.0
metadata:
  summary: Which @ocx-sh/theme component to use, how to import it, the props and a11y contracts that matter, and the lazy-loading rules
  keywords: ocx,ocx-sh,astro,starlight,components,mdx,zag,accessibility,a11y,button,select,combobox,dialog,tabs,accordion,tree,data-table,toast,lazy-loading,first-paint
---

# ocx-theme-components

Every component ships as Astro source in `@ocx-sh/theme`. Import it by its
file path under `components/`:

```mdx
import Button from '@ocx-sh/theme/components/ui/Button.astro';
import Tabs from '@ocx-sh/theme/components/Tabs.astro';
import TabItem from '@ocx-sh/theme/components/TabItem.astro';
import { toast } from '@ocx-sh/theme/toast';
```

Setup (the Starlight plugin) is `ocx-theme-setup` at
`../ocx-theme-setup/SKILL.md`; colours and tokens are `ocx-theme-theming` at
`../ocx-theme-theming/SKILL.md`. Missing either? Install the set:
`grim add ghcr.io/ocx-sh/lore/ocx-theme:latest`.

## Rules every component follows

1. **First paint is final.** The server renders every part in its final state:
   selected tab, open item, checked box, sorted rows. Pass initial state as
   props (`value`, `open`, `expanded`, `sort`) and never fix it up in a
   client script. A script that moves content after load is a layout-shift bug.
2. **Nothing loads before interaction.** A Zag-backed component loads its
   machine on the first hover, focus or touch of its root. Exceptions: `Toc`
   starts when it scrolls into view; search, the toaster and the mobile menu
   start on demand; a `Dialog` or `Drawer` rendered `open` starts at once.
   Never import `@zag-js/*` in page code. Details:
   [references/loading-model.md](references/loading-model.md).
3. **An accessible name is required, and the types enforce it.** `label` is a
   required prop on every field, group, list and meter. `iconOnly` on Button
   and ToggleButton requires `aria-label`. A removable Tag requires
   `removeLabel`. Use `hideLabel` to hide a label visually, never to drop it.
4. **The `zag` prop is data only.** It passes extra machine props through
   (`zag={{ loopFocus: false }}`). Functions and ids are rejected by its type;
   listen to the component's DOM event instead.
5. **Events are DOM events named `ocx:<component>:<event>`** and bubble, e.g.
   `ocx:select:change` with `detail.value`. Listen on the component or the
   document.
6. **Accent is reserved** for links, the current item, the one primary action
   per view and focus. `variant="primary"` once per view; status uses `tone`.

## Catalog

Import path = `@ocx-sh/theme/components/` + the file below.

| File | Use it for | Depth |
|---|---|---|
| `ui/Button.astro` | Any action; `href` renders a link styled as a button. `variant` primary, secondary, ghost; `size` s, m, l | [actions-forms](references/actions-forms.md) |
| `ui/Link.astro` | A prose-style link outside prose; `tone`, `underline`, `external`, `newTab` | [actions-forms](references/actions-forms.md) |
| `CopyButton.astro` | Copy one `value` to the clipboard, with a toast | [actions-forms](references/actions-forms.md) |
| `ui/CommandBar.astro` | A copyable shell command, optionally a picker of variants (platform, scope) | [actions-forms](references/actions-forms.md) |
| `ui/ToggleButton.astro` | One on/off button (`aria-pressed`) | [actions-forms](references/actions-forms.md) |
| `ui/CycleButton.astro` | A button stepping through 2+ states (theme, sort direction) | [actions-forms](references/actions-forms.md) |
| `ToggleGroup.astro` | 2 to 5 always-visible options, one or several selected | [actions-forms](references/actions-forms.md) |
| `ui/Input.astro` | A text, search, url, email, password or number field with hint and error | [actions-forms](references/actions-forms.md) |
| `ui/InputGroup.astro` | Box around a control with `start` and `end` addons | [actions-forms](references/actions-forms.md) |
| `ui/NumberStepperScript.astro` | The number stepper's script, rendered by `Input`; never import it | [actions-forms](references/actions-forms.md) |
| `ui/SearchField.astro` | Search box with icon and clear button | [actions-forms](references/actions-forms.md) |
| `ui/Label.astro` | A standalone field label | [actions-forms](references/actions-forms.md) |
| `ui/Select.astro` | Pick one of a fixed option list; `start` / `end` slots | [actions-forms](references/actions-forms.md) |
| `ui/Combobox.astro` | Type to filter a long option list; `start` / `end` slots | [actions-forms](references/actions-forms.md) |
| `ui/Choice.astro` | Checkbox, switch or a lone radio | [actions-forms](references/actions-forms.md) |
| `ui/RadioGroup.astro` | One of a few options, all visible, in a form | [actions-forms](references/actions-forms.md) |
| `ui/Slider.astro` | A number or a range in bounds | [actions-forms](references/actions-forms.md) |
| `ui/Tag.astro` | A passive chip: category (`label`), status (`stamp`), `filter` | [actions-forms](references/actions-forms.md) |
| `ui/TagGroup.astro` | A labelled set of chips, selectable or removable | [actions-forms](references/actions-forms.md) |
| `ui/TagsInput.astro` | Field that collects several values as chips | [actions-forms](references/actions-forms.md) |
| `ui/Dialog.astro` | Modal or non-modal dialog opened from a trigger | [overlays](references/overlays-disclosure.md) |
| `ui/ConfirmDialog.astro` | Confirm / cancel question, opened by event | [overlays](references/overlays-disclosure.md) |
| `ui/AlertDialog.astro` | One-button notice with a tone, opened by event | [overlays](references/overlays-disclosure.md) |
| `ui/Drawer.astro` | Side or bottom sheet | [overlays](references/overlays-disclosure.md) |
| `ui/Popover.astro` | Rich non-modal panel from a trigger | [overlays](references/overlays-disclosure.md) |
| `ui/Menu.astro` | A trigger with a panel of navigation links | [overlays](references/overlays-disclosure.md) |
| `ui/ActionMenu.astro` | A list of commands (APG menu), or a context menu | [overlays](references/overlays-disclosure.md) |
| `Tooltip.astro` | A term in prose with a definition popup | [overlays](references/overlays-disclosure.md) |
| `ui/Hint.astro` | A short label for an icon button | [overlays](references/overlays-disclosure.md) |
| `Accordion.astro` + `AccordionItem.astro` | Stacked sections, one or several open | [overlays](references/overlays-disclosure.md) |
| `Collapsible.astro` | One show/hide region | [overlays](references/overlays-disclosure.md) |
| `Tabs.astro` + `TabItem.astro` | Alternative panels (shell variants); Starlight-compatible | [overlays](references/overlays-disclosure.md) |
| `ui/List.astro` | A selectable listbox of rows, sync or `async` | [data-feedback](references/data-feedback.md) |
| `ui/DataTable.astro` | Sortable, filterable, paged table of passed-in rows | [data-feedback](references/data-feedback.md) |
| `Pagination.astro` | Page numbers as buttons or links | [data-feedback](references/data-feedback.md) |
| `Tree.astro` + `TreeNode.astro` + `TreeDescription.astro` | A static, authored file tree | [data-feedback](references/data-feedback.md) |
| `TreeView.astro` | Interactive tree from JSON (APG tree) | [data-feedback](references/data-feedback.md) |
| `ui/Meter.astro` | A value in a known range (quota, disk) | [data-feedback](references/data-feedback.md) |
| `ui/ProgressCircle.astro` | Progress of a task, determinate or not | [data-feedback](references/data-feedback.md) |
| `ui/Loader.astro` | Announced loading state of a region | [data-feedback](references/data-feedback.md) |
| `ui/Skeleton.astro` | Placeholder bars reserving the final box | [data-feedback](references/data-feedback.md) |
| `ui/Avatar.astro` + `ui/AvatarGroup.astro` | Person or org image with initials fallback | [data-feedback](references/data-feedback.md) |
| `PlatformIcons.astro` | OS glyphs for `os/arch` platform strings | [data-feedback](references/data-feedback.md) |
| `DependencyExplorer.astro` | SBOM viewer over a `dependencies.json` | [data-feedback](references/data-feedback.md) |
| `Terminal.astro` | A recorded asciinema cast in terminal chrome | [content](references/content-navigation.md) |
| `FeatureSection.astro` | Alternating text-and-visual block on landing pages | [content](references/content-navigation.md) |
| `HubGrid.astro` | One hub's entries from `nav.json` as category groups of cards | [content](references/content-navigation.md) |
| `ui/Kbd.astro` | Keyboard keys, OS-adaptive (`Mod+K`) | [content](references/content-navigation.md) |
| `ui/Breadcrumbs.astro` | A breadcrumb trail outside the page title | [content](references/content-navigation.md) |
| `Toc.astro` | Scroll-spy table of contents inside a page | [content](references/content-navigation.md) |
| `ui/Icon.astro` | One registry icon, inline | [content](references/content-navigation.md) |
| `ui/Logo.astro` | The ocx mark, optionally with its wordmark or as a link | [content](references/content-navigation.md) |
| `SiteHeader.astro`, `SiteFooter.astro` | The ocx.sh (or neutral) header and footer. Use them through `layouts/Shell.astro` (see `ocx-theme-setup`); they exist directly for the Starlight overrides | [content](references/content-navigation.md) |
| `EcosystemMenu.astro` | Header chrome only: the ecosystem mega-menu. Never place it in a page | [content](references/content-navigation.md) |

`toast(title, options?)` from `@ocx-sh/theme/toast` shows a notification; see
[data-feedback](references/data-feedback.md). Starlight's own `Aside`,
`Badge`, `Card`, `CardGrid`, `LinkCard` and `Steps` are restyled by the theme:
import them from `@astrojs/starlight/components` as usual.

## Choosing between neighbours

| Need | Use | Not |
|---|---|---|
| Navigate somewhere | `Link`, or `Button href` for a call to action | `Button` with a click handler |
| Choose one of 2 to 5 visible options | `ToggleGroup` (UI state) or `RadioGroup` (form field) | `Select` |
| Choose one of many | `Select` (fixed list) or `Combobox` (type to filter) | `RadioGroup` |
| Explain a term in running text | `Tooltip` | `Hint` |
| Name an icon-only button | `Hint` around the button, plus its `aria-label` | `Tooltip` |
| Links in a dropdown | `Menu` | `ActionMenu` |
| Commands in a dropdown | `ActionMenu` | `Menu` |
| Ask yes or no | `ConfirmDialog` | a custom `Dialog` |
| Static file listing | `Tree` | `TreeView` |
| Value in a range | `Meter` | `ProgressCircle` |
| Task progress | `ProgressCircle`, or a `loading` toast | `Meter` |
| Tabular data a page passes in | `DataTable` | a Markdown table (stays static) |

## Compound components

`Tabs` / `TabItem` and `Accordion` / `AccordionItem` pass parent state by
post-processing the rendered slot HTML, not by context: MDX renders children
before their parent. Put the items directly inside their parent, give each
`AccordionItem` a slug-like unique `value`, and never wrap items in another
component.

## Before you ship a page

- Initial state set by props, nothing corrected by a script after load.
- Each field, group and icon-only control has its accessible name.
- One `variant="primary"` per view at most.
- A reserved box for anything that loads late (`Skeleton`, `Terminal` with
  `cols` and `rows`, images with width and height).
- No `@zag-js/*` import and no inline `style` colour in page code.
