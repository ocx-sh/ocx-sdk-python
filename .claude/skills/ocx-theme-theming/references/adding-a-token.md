# Adding or changing a token

You loaded this file because a style needs a value no `--ocx-*` token
carries, or a token's value or name has to change. Tokens change only in the
theme repository, `ocx-sh/website`.

## Procedure

1. **Check it is really missing.** Search `packages/theme/src/tokens.css` for
   the role, not the number. Reuse beats a near-duplicate: a second grey that
   differs by 2% is a bug, not a token.
2. **Name it by role.** `--ocx-<family>-<role>`: `--ocx-color-…`,
   `--ocx-space-…`, `--ocx-control-…`, `--ocx-z-…`. No per-component hook
   tokens (`--ocx-button-bg`): components read role tokens.
3. **Place it.**
   - Colour: add it to the light block (`:root, :root[data-theme='light']`)
     **and** the dark block (`.dark, :root[data-theme='dark']`). A missing
     twin fails the dark-parity gate.
   - Anything else: once, in the light block only.
   - Inside `@layer ocx`, next to its family, with a one-line comment giving
     the role and, for colour, the contrast it meets.
4. **Prove contrast.** A text or UI colour gets a pair in
   `packages/theme/test/contrast-pairs.ts` (text 4.5:1, UI boundaries 3:1),
   checked in both schemes by `tokens.test.ts`.
5. **Use it** in the component that needed it, replacing the raw value.
6. **Update the skill.** Token rules live in `skills/ocx-theme-theming/`;
   a new family or a changed rule updates it in the same commit.
7. **Run the gates**: `task test` for the token tests, then `task build` and
   `task css`, which runs over the built CSS:
   - `scripts/css/literal-colours.mjs`: no colour literal outside a token
     declaration;
   - `scripts/css/outside-layers.mjs`: no rule outside a cascade layer;
   - `scripts/css/dark-parity.mjs`: every colour token in both schemes.

## Renaming or removing

A token is public API: consumers may read it in their own CSS. Treat a rename
or removal as a breaking change: list it in the release notes, and search the
consumer repos (see the claims in `nav.json`) for the old name first.

## Consumer sites

A consumer never declares `--ocx-*`. Need a one-off value on one page? Ask
first whether the design really needs it; if it does, propose the token
upstream. A site-local custom property for a site-local component is fine,
with the site's own prefix, built from `--ocx-*` values.
