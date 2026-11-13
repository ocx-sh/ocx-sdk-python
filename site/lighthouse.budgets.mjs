/**
 * Lighthouse budgets and page discovery for `task site:lighthouse` (scripts/lighthouse.mjs).
 * Trimmed from ocx-sh/website `tests/budgets.mjs` (`content` class): a value here never goes above
 * the website's, and only goes down. Sizes are bytes as Lighthouse measures them (transfer size).
 */
import { existsSync, readdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const KB = 1024;

/** Where the site is mounted (astro.config.mjs `base`). */
export const BASE = '/integrations/python/';

/** Built output served by `astro preview`. */
export const DIST = fileURLToPath(new URL('./dist/', import.meta.url));

export const BUDGET = {
  /** Every page's HTML (website HTML_GZ_MAX: one TCP initial window). */
  htmlBytes: 14_200,
  /** Scripts before any interaction (website `content.preJsGz`). */
  scriptBytes: 11 * KB,
  /** Whole page weight (website `content.totalBytes`). */
  totalBytes: 147 * KB,
  /** DOM elements (website `content.domElements`). */
  domElements: 800,
};

/** Categories judged by score: Lighthouse's 100. Performance is judged by its metrics (METRIC_CEILING). */
export const CATEGORIES = ['accessibility', 'best-practices', 'seo'];

/**
 * The metrics behind the performance score that a content page can move, as ceilings on the simulated mobile run (ms; CLS is
 * unitless). `categories:performance >= 1` was a knife edge: a content-only page lands on FCP 1.21 s or
 * 1.36 s depending on whether the observed trace saves one simulated round trip, and the score falls from
 * 1 to 0.99 at FCP ~1.36 s, so identical bytes flipped the gate between runs. The ceilings sit just above
 * the 100-score plateau (FCP 1.36 s, LCP 1.66 s; Speed Index follows FCP and scores 1 up to ~1.5 s, so it adds nothing) and below the next step (+150 ms, one extra round
 * trip, which the score also punishes), so a real regression still fails. TBT and CLS are 0 on a static page.
 */
export const METRIC_CEILING = {
  'first-contentful-paint': 1400,
  'largest-contentful-paint': 1700,
  'total-blocking-time': 0,
  'cumulative-layout-shift': 0,
};

/** lhci assertions: the other three categories at 1, the performance metrics under their ceilings, plus the budgets above. */
export function assertions(budget = BUDGET) {
  return {
    ...Object.fromEntries(CATEGORIES.map((c) => [`categories:${c}`, ['error', { minScore: 1 }]])),
    ...Object.fromEntries(
      Object.entries(METRIC_CEILING).map(([id, max]) => [id, ['error', { maxNumericValue: max }]]),
    ),
    'resource-summary:document:size': ['error', { maxNumericValue: budget.htmlBytes }],
    'resource-summary:script:size': ['error', { maxNumericValue: budget.scriptBytes }],
    'total-byte-weight': ['error', { maxNumericValue: budget.totalBytes }],
    'dom-size': ['error', { maxNumericValue: budget.domElements }],
  };
}

/**
 * Every HTML page of the build as a sorted URL path under `base` (`index.html` -> base,
 * `x/index.html` -> `base x/`, `404.html` as itself).
 * @param {string} [dist]
 * @param {string} [base]
 * @returns {string[]}
 */
export function pages(dist = DIST, base = BASE) {
  if (!existsSync(dist)) throw new Error(`missing dist: ${dist}`);
  return readdirSync(dist, { recursive: true, encoding: 'utf8' })
    .map((f) => f.split('\\').join('/'))
    // frag/ pages are noindex Deferred fragments, not pages to audit.
    .filter((f) => f.endsWith('.html') && !/(?:^|\/)frag\//.test(f))
    .map((f) => base + f.replace(/(?:^|\/)index\.html$/, (m) => (m.startsWith('/') ? '/' : '')))
    .sort();
}
