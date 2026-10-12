# Playwright gates for a consumer site

You loaded this file because a site needs the behaviour gates the theme
keeps for itself. Run them against the built site served under its real
`base` (for example `npx astro preview`), never the dev server. Each recipe
is a starting point; list your own representative pages in `PAGES`.

Contents: [No flicker](#no-flicker) · [Lazy hydration](#lazy-hydration) ·
[Accessibility](#accessibility) · [Forced colors and schemes](#forced-colors-and-schemes)

## No flicker

The layout must be identical whether images load or not.

```ts
import { expect, test, type Page } from '@playwright/test';

const PAGES = ['/integrations/bazel/', '/integrations/bazel/rules/'];

async function boxes(page: Page, path: string) {
  await page.goto(path);
  await page.evaluate(() => document.fonts.ready);
  return page.evaluate(() =>
    [...document.querySelectorAll('body *')].flatMap((e) => {
      const r = e.getBoundingClientRect();
      return r.width || r.height ? [`${e.tagName} ${[r.x, r.y, r.width, r.height].map(Math.round).join(',')}`] : [];
    }),
  );
}

test.use({ reducedMotion: 'reduce' });

for (const path of PAGES) {
  test(`same layout with images blocked: ${path}`, async ({ page, context }) => {
    const loaded = await boxes(page, path);
    await context.route('**/*', (r) => (r.request().resourceType() === 'image' ? r.abort() : r.continue()));
    expect(await boxes(page, path)).toEqual(loaded);
  });
}
```

## Lazy hydration

Theme components mark their machine root with `data-zag-root` and its state
with `data-zag-state` (`idle`, `loading`, `live`, `error`). With no input,
every interaction-triggered root stays `idle`; a hover starts it.

```ts
import { expect, test } from '@playwright/test';

const ROOT = '[data-zag-root]:not([data-zag-trigger]), [data-zag-root][data-zag-trigger="interaction"]';

test('no machine starts without input', async ({ page }) => {
  await page.goto('/integrations/bazel/');
  await page.waitForLoadState('networkidle');
  await page.waitForTimeout(2000);
  for (const root of await page.locator(ROOT).all()) {
    await expect(root).not.toHaveAttribute('data-zag-state', /loading|live|error/);
  }
});
```

Assert the root count too (count `data-zag-root` in the served HTML and
compare), so a renamed attribute cannot make the test pass on zero roots.

## Accessibility

```ts
import { AxeBuilder } from '@axe-core/playwright';
import { expect, test } from '@playwright/test';

test('axe: no violations', async ({ page }) => {
  await page.goto('/integrations/bazel/');
  const { violations } = await new AxeBuilder({ page }).analyze();
  expect(violations.map((v) => v.id)).toEqual([]);
});
```

Repeat with overlays open (the ecosystem menu, a dialog, the search dialog):
closed overlays are not in the accessibility tree, so axe misses them.

## Forced colors and schemes

- Forced colors: `test.use({ forcedColors: 'active' })`, then check that
  custom-drawn controls (checkbox, switch, slider, meter) stay visible, for
  example with a screenshot or a computed `outline`/`border` colour.
- Both schemes: set `document.documentElement.dataset.theme` to `light` and
  `dark` (Starlight's attribute), or `page.emulateMedia({ colorScheme })` for
  the automatic mode, and rerun axe in each; contrast failures differ per
  scheme.
- Mobile: add a Playwright project at 390px width; the header and the mobile
  menu must not clip.
