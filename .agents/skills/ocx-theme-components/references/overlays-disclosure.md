# Overlays and disclosure

You loaded this file because a page needs a dialog, a menu, a popup, or
content that opens and closes. Import path prefix: `@ocx-sh/theme/components/`.

Contents: [Dialogs](#dialogs) · [Panels and menus](#panels-and-menus) ·
[Tooltip and Hint](#tooltip-and-hint) · [Disclosure](#disclosure) · [Tabs](#tabs)

## Dialogs

All dialogs run on Zag: focus trap (when modal), scroll lock, Escape,
outside click and focus return come with them.

**`ui/Dialog.astro`**: `title` (required, the accessible name), `description`
(linked as the accessible description), `open` (renders open and starts
eagerly), `modal` (default true), `id`. Slot `trigger` holds the opener
text; slot `icon` sits in the header; the default slot is the body. Without
a `trigger` slot the dialog waits for `ocx:dialog:open`. Event
`ocx:dialog:change` `{ open }`.

```astro
<Dialog title="Remove package?">
  <span slot="trigger">Remove…</span>
  The lock file keeps its entry until you run `ocx lock`.
</Dialog>
```

**`ui/ConfirmDialog.astro`** and **`ui/AlertDialog.astro`** are presets with
no trigger slot. Place one once, open it by event, read the answer back:

```js
document.dispatchEvent(new CustomEvent('ocx:dialog:open', { detail: { id: 'remove-confirm' } }));
document.addEventListener('ocx:dialog-result', (e) => {
  if (e.detail.id === 'remove-confirm' && e.detail.value === 'confirm') remove();
});
```

| Preset | Props | `value` in `ocx:dialog-result` |
|---|---|---|
| `ConfirmDialog` | `id`, `title` (required), `message`, `confirmLabel`, `cancelLabel` | `confirm`, `cancel`, `dismiss` |
| `AlertDialog` | `id`, `title` (required), `message`, `tone` `info`/`success`/`warning`/`error`, `acknowledgeLabel` | `ok`, `dismiss` |

No danger variant exists for the confirm button; state the consequence in
`message`.

**`ui/Drawer.astro`**: Dialog's contract plus swipe to close. `title`
(required), `description`, `open`, `modal`, `side` `start`, `end` or
`bottom`. Slot `trigger`. Event `ocx:drawer:change`.

## Panels and menus

**`ui/Popover.astro`**: a non-modal panel from slot `trigger`; default slot
is the panel. `placement` (default `bottom-start`), `modal` (traps focus).
Event `ocx:popover:change`.

**`ui/Menu.astro`**: a trigger plus a native-popover panel of **links**
(disclosure navigation, Tab order, no ARIA menu). `label` (required, trigger
text and panel name), `items` (`{ label, href, current?, icon? }`), `align`
`start`/`end`.

**`ui/ActionMenu.astro`**: a list of **commands** (APG menu: arrows,
Home/End, typeahead). `label` (required), `items`
(`{ value, label, disabled?, icon? }`), `placement`, `context` (the default
slot becomes the target: right-click or long-press opens the menu at the
pointer). Events `ocx:menu:select` `{ value }`, `ocx:menu:change`.

## Tooltip and Hint

| | `Tooltip.astro` | `ui/Hint.astro` |
|---|---|---|
| For | A term inside running text | An icon-only button |
| Markup | Phrasing content, nests inside `<p>` | Wraps exactly one button |
| Props | `term` (required, the underlined text), `side`, `delayDuration` | `label` (required), `openDelay`, `placement` |
| Content | Default slot: the definition (may hold links and code) | `label` only, short |
| Engine | Popover API and CSS anchor positioning, no Zag | Zag tooltip |

```mdx
Pinned by <Tooltip term="OCI digest">A SHA-256 content fingerprint.</Tooltip>, not by ranges.

<Hint label="copy command"><Button iconOnly aria-label="Copy command"><Icon name="copy" /></Button></Hint>
```

The Hint is a description; the button keeps its own `aria-label`.

## Disclosure

**`Accordion.astro`** with **`AccordionItem.astro`** children:

| Prop | Notes |
|---|---|
| `Accordion` `multiple` | Several items open at once |
| `Accordion` `collapsible` | Default true: the open item closes when selected again. `false` keeps one open |
| `Accordion` `value` | Values of the items open on first paint |
| `AccordionItem` `value` | Required, unique, slug-like (it becomes part of the element ids) |
| `AccordionItem` `label` | Required, the trigger text |

Event `ocx:accordion:change`.

**`Collapsible.astro`**: `label` or a `trigger` slot (one is required),
`open`, `disabled`. Event `ocx:collapsible:change`.

## Tabs

**`Tabs.astro`** with **`TabItem.astro`** children, API-compatible with
Starlight's tabs. `Tabs` `syncKey`: groups sharing the key switch together
and the choice persists across pages (restored before paint, no flash).
`TabItem` `label` (required; synced groups match on it) and `icon`, one of
`shell`, `powershell`, `nushell`, `fish`, `elvish`, `cmd`. Event
`ocx:tabs:change`. All panels share one grid cell: the area is as tall as
the tallest panel from first paint, and a switch crossfades the two panels
(the inactive one is `visibility: hidden`, so the height never changes). A
panel that is a lone code frame switches instantly instead: no transition,
the frame stays opaque.

```mdx
<Tabs syncKey="shell">
  <TabItem label="sh" icon="shell">`echo "$HOME"`</TabItem>
  <TabItem label="PowerShell" icon="powershell">`echo $env:USERPROFILE`</TabItem>
</Tabs>
```
