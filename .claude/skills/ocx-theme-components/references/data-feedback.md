# Data display and feedback

You loaded this file because a page shows collections, trees, progress or
notifications. Import path prefix: `@ocx-sh/theme/components/`.

Contents: [Lists and tables](#lists-and-tables) · [Trees](#trees) ·
[Progress and loading](#progress-and-loading) · [Toast](#toast) ·
[People and platforms](#people-and-platforms)

## Lists and tables

**`ui/List.astro`**: an APG listbox, one tab stop, arrows move the highlight.

| Prop | Notes |
|---|---|
| `items` | `{ value, label, disabled?, group?, description?, meta? }` |
| `label` | Required; shown above the list |
| `selectionMode` | `single` or `multiple`; `value` holds the selection |
| `orientation`, `columns` | Horizontal list, or a grid with 2-D arrows |
| `empty` | Text when there are no rows |
| `async`, `cursor` | The page is the data source: answer `ocx:list:fetch` by calling `detail.respond(page or promise)` synchronously; "Load more" shows while a `cursor` exists |

A row is an option: never put links or buttons inside it. Custom rows use a
function slot: `<List items={items} label="Packages">{(item) => <Row {...item} />}</List>`.
Event `ocx:list:change` `{ value }`.

**`ui/DataTable.astro`**: rows the server renders in their final order, then
sort, filter and pages on first interaction.

| Prop | Notes |
|---|---|
| `caption` | Required; the visually hidden caption and scroll region name |
| `columns` | `{ key, label, sortable? (default true), numeric?, mono? }` |
| `rows` | `Record<string, string \| number>[]` |
| `pageSize` | Enables pagination; without it every match shows |
| `filter` | Text filter above the table, default true |
| `sort`, `query`, `page` | Initial state: `{ key, direction? }`, filter text, page from 1 |
| `empty` | Shown when no row matches |

Markdown tables stay static; use DataTable only for data a page passes in.

**`Pagination.astro`**: `count` and `pageSize` (required), `page` (from 1),
`href` (links mode: a URL template with `{page}`). Without `href` the buttons
fire `ocx:pagination:change`; send `ocx:pagination:set` `{ count }` to the root
when the item count changes.

## Trees

**`Tree.astro`** is a static, authored tree. Each `TreeNode` renders itself;
a node with child nodes is a directory.

```mdx
import Tree from '@ocx-sh/theme/components/Tree.astro';
import Node from '@ocx-sh/theme/components/TreeNode.astro';
import Description from '@ocx-sh/theme/components/TreeDescription.astro';

<Tree>
  <Node name="~/.ocx/" open>
    <Node name="packages/">
      <Description slot="description">immutable, <code>content-addressed</code> packages</Description>
    </Node>
  </Node>
</Tree>
```

`Tree`: `collapsible` (false renders all open, no toggles), `selectable`.
`TreeNode`: `name` (required), `icon`, `openIcon`, `open` (default expanded),
`description` (plain-text shorthand for the slot), `selectable`.

**`TreeView.astro`** is the interactive APG tree from JSON: `items`
(`{ value, label, children?, disabled?, icon?, openIcon?, description?, selectable? }`),
`label` (required), `value`, `selectable`, `expanded` (open branch values).
Events `ocx:tree-view:select`, `ocx:tree-view:expand`. Use `Tree` for a plain
file listing.

## Progress and loading

| Component | Use | Props |
|---|---|---|
| `ui/Meter.astro` | A value in a known range, `role="meter"` | `label` (required), `value` (required), `min`, `max`, `format`, `valueText`, `tone` `neutral`/`success`/`warning`/`danger`, `size`, `hideLabel` |
| `ui/ProgressCircle.astro` | Task progress, `role="progressbar"` | `label` (required), `value` 0 to 100 (absent = indeterminate), `size` `s`/`m`/`l`, `tone`, `showValue` |
| `ui/Loader.astro` | The announced loading state of a region | `label`, `size` `s`/`m` |
| `ui/Skeleton.astro` | Placeholder bars that reserve the final box | `variant` (`text` default, `circle`, `rect`, `card`, `list`, `table`, `code`, `terminal`), `lines`, `rows`, `columns`, `size` `sm`/`md`/`lg`, `ratio`, `animated` |

None of these take the accent colour. Move a determinate ring by setting
`aria-valuenow` and `style="--_value:N"` on its root. `Loader` is rendered
from the start so its live region exists; swap its label text to report
progress, hide it when the content lands, and set `aria-busy` on the region
you fill. Pair a `Skeleton` with a `Loader` for the announcement. One
`Skeleton` covers every placeholder: `lines` (text, code, terminal), `rows` (list,
table), `columns` (table), `size` (circle, on Avatar's scale), `ratio` (rect,
`'16 / 9'`, anything but `w / h` throws). A composite reserves the real
component's block size, so nothing moves when the content lands. A faint
sweep crosses the bars and stands still under reduced motion; `animated={false}`
stops it.

## Toast

```js
import { toast } from '@ocx-sh/theme/toast';

const id = toast('Saved');
toast('Uh oh', { tone: 'error', description: 'Retry in a moment.' });
toast('Uploading…', { id: 'up', tone: 'loading', progress: 0 });
toast('Uploaded', { id: 'up', tone: 'success' }); // updates the same toast
toast.dismiss(id);
```

Options: `description`, `tone` `info`, `success` (default), `warning`,
`error`, `loading`; `duration` (default 2500 ms), `persistent`, `action`
(`{ label, event }`; implies persistent), `progress` (loading only), `id`
(pass it back to update or dismiss). The helper only dispatches `ocx:toast`,
so calling it never imports Zag; the page toaster loads on the first toast.
In Starlight the toaster mounts from the theme's `Search` script. A page
without Starlight's chrome renders the toaster root (as the theme's `Footer`
does) and calls `installToaster(document)` from `@ocx-sh/theme/toaster`.

## People and platforms

**`ui/Avatar.astro`**: `name` (required, accessible name and initials source),
`src`, `srcset`, `initials`, `size` `sm`/`md`/`lg`, `status`
`online`/`away`/`busy`/`offline`. The initials sit under the image in the same
box, so a slow or blocked image never shifts layout. **`ui/AvatarGroup.astro`**:
`items` (`{ name, src?, srcset?, initials? }`), `size`, `max` (the rest fold
into "+N").

**`PlatformIcons.astro`**: `platforms` (required, `os/arch` strings such as
`linux/amd64`), `mode` `os` (glyphs) or `os-arch` (glyph, OS name and
architecture chips). Zero client JS.

**`DependencyExplorer.astro`**: an SBOM viewer. `src` defaults to
`data/dependencies.json` under the site base. Search and licence filter,
expandable rows, loading, error and empty states; the frame paints final and
skeletons hold the values.
