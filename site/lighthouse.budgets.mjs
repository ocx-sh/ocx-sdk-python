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

export const CATEGORIES = ['performance', 'accessibility', 'best-practices', 'seo'];

/** lhci assertions: all four categories at 1, plus the budgets above. */
export function assertions(budget = BUDGET) {
  return {
    ...Object.fromEntries(CATEGORIES.map((c) => [`categories:${c}`, ['error', { minScore: 1 }]])),
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
    .filter((f) => f.endsWith('.html'))
    .map((f) => base + f.replace(/(?:^|\/)index\.html$/, (m) => (m.startsWith('/') ? '/' : '')))
    .sort();
}
