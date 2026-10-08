// Offline check of the budgets and page discovery: `node --test scripts/lighthouse.test.mjs` (task site:lighthouse:test).
import assert from 'node:assert/strict';
import { mkdirSync, mkdtempSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { test } from 'node:test';
import { BASE, BUDGET, CATEGORIES, assertions, pages } from '../lighthouse.budgets.mjs';
import { ASSERT_MATRIX } from './lighthouse.mjs';

// Ceilings: ocx-sh/website tests/budgets.mjs (`content` class, HTML_GZ_MAX). Lower them, never raise.
const CEILING = { htmlBytes: 14_200, scriptBytes: 11 * 1024, totalBytes: 147 * 1024, domElements: 800 };

test('budgets never exceed the website ceilings', () => {
  for (const [k, max] of Object.entries(CEILING)) assert.ok(BUDGET[k] <= max, `${k} ${BUDGET[k]} > ${max}`);
  assert.deepEqual(Object.keys(BUDGET).sort(), Object.keys(CEILING).sort());
});

test('assertions: four categories at 1 plus every budget', () => {
  const a = assertions();
  for (const c of CATEGORIES) assert.deepEqual(a[`categories:${c}`], ['error', { minScore: 1 }]);
  assert.equal(a['dom-size'][1].maxNumericValue, BUDGET.domElements);
  assert.equal(a['total-byte-weight'][1].maxNumericValue, BUDGET.totalBytes);
  assert.equal(a['resource-summary:script:size'][1].maxNumericValue, BUDGET.scriptBytes);
  assert.equal(a['resource-summary:document:size'][1].maxNumericValue, BUDGET.htmlBytes);
  assert.deepEqual(ASSERT_MATRIX[0].assertions, a);
});

test('pages: every built HTML file, under the base path, sorted', () => {
  const dist = mkdtempSync(join(tmpdir(), 'lh-'));
  for (const f of ['index.html', '404.html', 'guide/quickstart/index.html', 'pagefind/pagefind.js']) {
    mkdirSync(join(dist, f, '..'), { recursive: true });
    writeFileSync(join(dist, f), '');
  }
  assert.deepEqual(pages(dist), [`${BASE}`, `${BASE}404.html`, `${BASE}guide/quickstart/`].sort());
  assert.throws(() => pages(join(dist, 'nope')), /missing dist/);
});
