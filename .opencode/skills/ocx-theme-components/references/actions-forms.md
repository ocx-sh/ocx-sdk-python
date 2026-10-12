# Actions and forms

You loaded this file because a page needs a button, a field, a picker or chips.
Import path prefix: `@ocx-sh/theme/components/`.

Contents: [Actions](#actions) · [Toggles](#toggles) · [Text fields](#text-fields) ·
[Pickers](#pickers) · [Choices and ranges](#choices-and-ranges) · [Chips](#chips)

## Actions

**`ui/Button.astro`** renders `<a>` when `href` is set, else
`<button type="button">`.

| Prop | Values | Notes |
|---|---|---|
| `variant` | `primary`, `secondary` (default), `ghost` | Primary is the coral fill: one per view |
| `size` | `s` (30, mono), `m` (38, default), `l` (42) | |
| `iconOnly` | `true` | Square button; the type requires `aria-label` |
| `type` | `button`, `submit`, `reset` | Button form only |
| `aria-disabled="true"` | | On a link it drops `href`, so it takes no focus |

```astro
<Button variant="primary" href="/install/">Install</Button>
<Button iconOnly aria-label="Settings"><Icon name="edit" /></Button>
```

**`ui/Link.astro`**: `href` (required), `tone` `accent` (default) or `neutral`
(footers, meta), `underline` `always`, `hover` or `none` (then give a
non-colour cue yourself), `external` (default: an absolute URL off
`Astro.site`, appends the external icon), `newTab` (never implied by
`external`).

**`CopyButton.astro`**: `value` (required), `label` (what the toast names
instead of the value). Reads "copied" for 1.5 s, then toasts. Events
`ocx:clipboard:copy`, `ocx:clipboard:error`.

**`ui/CommandBar.astro`**: one copyable command. `choices` (required,
`{ value, label, command, icon? }[]`): none means an action-only bar, one
means no picker, more renders a compact `Select` picker. `pickerLabel` names
the picker ("Platform"); `pickerText` shows the choice label beside its icon;
`noun` finishes the copy toast ("install command"); `detect` preselects the
visitor's OS before first paint (choice values `linux`, `macos`, `windows`).
Slot `action` adds trailing controls: an icon-only link Button with an
`aria-label`, wrapped in a `Hint`.

## Toggles

| Component | Props that matter | Event |
|---|---|---|
| `ui/ToggleButton.astro` | `pressed`, `value`, `size` `s`/`m`, `iconOnly` (requires `aria-label`) | `ocx:toggle-button:change` |
| `ui/CycleButton.astro` | `states` (2+, `{ value, label, icon }`, click order), `value`, `name` (hidden form input), `variant` `ghost`/`secondary`, `size`, `iconOnly` | `ocx:cycle-button:change` |
| `ToggleGroup.astro` | `items` (`{ value, label, icon?, iconOnly?, disabled? }`), `label` (required), `multiple`, `value` | `ocx:toggle-group:change` |

`ToggleGroup` with one selection is a radio group and cannot be emptied; with
`multiple` the items are pressed buttons.

## Text fields

**`ui/Input.astro`**: `label` (required), `hideLabel`, `type` `text`, `search`,
`url`, `email`, `password`, `number`, `hint`, `error` (replaces the hint, sets
`aria-invalid`; say how to fix it), `stepper` (number only). Slots `start` and
`end` wrap the input in an `InputGroup`. A stepper input renders
`ui/NumberStepperScript.astro` (its one delegated script); a text or search
input carries no script. Never import it directly.

**`ui/InputGroup.astro`**: box around slots `start`, default (the control) and
`end`. `disabled`, `invalid`, `wrap` (chips before the control). No JS: focus,
invalid and disabled come from the control's own state.

**`ui/SearchField.astro`**: `label` (required, the clear button is named
"Clear <label>"), `hideLabel`, `placeholder` (defaults to `label`, must not be
empty). Escape and the clear button clear it; event `ocx:search-field:clear`.

**`ui/Label.astro`**: `for`, `as` `label` or `span` (for targets a `<label>`
cannot name; give it an `id` and point `aria-labelledby` at it), `hidden`
(visually), `required` (decorative `*`; put `required` on the control),
`disabled`.

## Pickers

**`ui/Select.astro`**: an APG select-only combobox over a hidden native
`<select>`, so it submits in forms. `label` (required), `hideLabel`, `options`
(`{ value, label, meta?, icon?, disabled? }`), `value` (none selects the first
enabled option), `aria-invalid="true"` for the error border (add your own
message). Options appended to the native `<select>` at runtime join the list.
Slots `start` and `end` put a control inside the box, such as a sort-direction
`CycleButton`; it is its own tab stop and never opens the list. Event
`ocx:select:change` `{ value }`.

```astro
<Select label="Sort by" options={sortKeys} value="updated">
  <CycleButton slot="start" variant="ghost" size="s" states={directions} value="desc" />
</Select>
```

**`ui/Combobox.astro`**: an editable combobox that filters `options`
(`{ value, label, meta? }`) as the reader types. `label` (required), `value`,
`placeholder`, `matchMode` `fuzzy` (default, one-typo tolerant), `contains`,
`startsWith`, `empty` (no-match text), `disabled`. Initial state: `open`,
`highlighted`, `query`. Slots `start` and `end` as in `Select`.

## Choices and ranges

**`ui/Choice.astro`**: `type` `checkbox` (default), `switch` or `radio`, `label`
(required), plus any native input attribute (`name`, `checked`, `required`).
The native input is the source of truth, so it works with JS off. A group of
radios is `RadioGroup`, never several `Choice`s.

**`ui/RadioGroup.astro`**: `label` (required), `name` (required), `items`
(`{ value, label, disabled? }`), `value`, `orientation`.

**`ui/Slider.astro`**: `label` (required), `value` (number = one thumb, pair =
range), `min`, `max`, `step`, `name` (one hidden input per thumb), `marks`,
`format` (`Intl.NumberFormatOptions`), `thumbLabels`, `showValue` (default
true), `disabled`.

## Chips

**`ui/Tag.astro`**: behaviour-free chip. `variant` `label` (tint, a category),
`stamp` (outline caps, a status fact), `filter` (toggle chip, driven by a
group); `tone` `neutral`, `keyword`, `success`, `warning`, `danger`;
`removable` requires `removeLabel`.

**`ui/TagGroup.astro`**: `label` (required), `items`
(`{ value, label, disabled?, tone? }`, value without whitespace),
`selectionMode` `none` (default), `single` or `multiple`, `value`. `removable`
(with `removeLabel`, `{label}` substituted) works only with
`selectionMode="none"`; removal fires a cancelable `ocx:tag-group:remove`.

**`ui/TagsInput.astro`**: `label` (required), `value` (the tags),
`suggestions` (`{ value, label, meta? }`), `allowCreate` (default: true without
suggestions, false with), `matchMode`, `max`, `delimiter`, `name` (tags joined
by ", "), `placeholder`, `hint`, `error`, `disabled`, `readOnly`, `empty`.
