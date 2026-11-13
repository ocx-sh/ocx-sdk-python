// Offline check of the budgets and page discovery: `node --test scripts/lighthouse.test.mjs` (task site:lighthouse:test).
import assert from 'node:assert/strict';
import { mkdirSync, mkdtempSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createRequire } from 'node:module';
import { test } from 'node:test';
import { BASE, BUDGET, CATEGORIES, METRIC_CEILING, assertions, pages } from '../lighthouse.budgets.mjs';
import { ASSERT_MATRIX, failedAssertions } from './lighthouse.mjs';

// Ceilings: ocx-sh/website tests/budgets.mjs (`content` class, HTML_GZ_MAX). Lower them, never raise.
const require = createRequire(import.meta.url);
const { getAllAssertionResults } = require(
  require.resolve('@lhci/utils/src/assertions.js', { paths: [require.resolve('@lhci/cli/package.json')] }),
);

const CEILING = { htmlBytes: 14_200, scriptBytes: 11 * 1024, totalBytes: 147 * 1024, domElements: 800 };

test('budgets never exceed the website ceilings', () => {
  for (const [k, max] of Object.entries(CEILING)) assert.ok(BUDGET[k] <= max, `${k} ${BUDGET[k]} > ${max}`);
  assert.deepEqual(Object.keys(BUDGET).sort(), Object.keys(CEILING).sort());
});

test('assertions: three categories at 1, the performance metrics, every budget', () => {
  const a = assertions();
  assert.equal(a['categories:performance'], undefined, 'the performance score is a knife edge; judge its metrics');
  assert.deepEqual(CATEGORIES, ['accessibility', 'best-practices', 'seo']);
  for (const [id, max] of Object.entries(METRIC_CEILING)) assert.deepEqual(a[id], ['error', { maxNumericValue: max }]);
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

// A synthetic run on the 100-score plateau of a content page (measured: FCP 1.21 or 1.36 s, LCP 1.66 s).
const audit = (numericValue, score = 1) => ({ score, numericValue });
const lhr = (over = {}, perfScore = 1) => ({
  lhr: 1,
  finalUrl: 'http://127.0.0.1/x/',
  categories: Object.fromEntries([...CATEGORIES, 'performance'].map((c) => [c, { score: c === 'performance' ? perfScore : 1 }])),
  audits: {
    'first-contentful-paint': audit(1360),
    'largest-contentful-paint': audit(1660),
    'total-blocking-time': audit(0),
    'cumulative-layout-shift': audit(0),
    'total-byte-weight': audit(100 * 1024),
    'dom-size': audit(400),
    'resource-summary': {
      score: 1,
      details: { items: [{ resourceType: 'document', transferSize: 12_000 }, { resourceType: 'script', transferSize: 8_000 }] },
    },
    ...over,
  },
});
const ids = (r) => failedAssertions(r).map((a) => a.auditId);

test('gate: the 100-score plateau and its 0.99 jitter pass, an extra round trip or a blocking task fails', () => {
  assert.deepEqual(ids(lhr()), []);
  // Same bytes, FCP/LCP 3 ms later: Lighthouse rounds the score to 0.99. This failed `categories:performance >= 1`.
  const jitter = lhr({ 'first-contentful-paint': audit(1363, 0.99), 'largest-contentful-paint': audit(1663, 0.99) }, 0.99);
  assert.deepEqual(ids(jitter), []);
  assert.deepEqual(ids(lhr({ 'first-contentful-paint': audit(1510, 0.97) }, 0.97)), ['first-contentful-paint']);
  assert.deepEqual(ids(lhr({ 'largest-contentful-paint': audit(1810, 0.97) }, 0.97)), ['largest-contentful-paint']);
  assert.deepEqual(ids(lhr({ 'total-blocking-time': audit(50) })), ['total-blocking-time']);
  assert.deepEqual(ids(lhr({ 'cumulative-layout-shift': audit(0.01) })), ['cumulative-layout-shift']);
});

test('gate: the retired form (performance score >= 1) fails the jitter run, so the test above is not vacuous', () => {
  const old = [{ matchingUrlPattern: '.*', assertions: { 'categories:performance': ['error', { minScore: 1 }] } }];
  const jitter = lhr({}, 0.99);
  assert.equal(getAllAssertionResults({ assertMatrix: old }, [jitter]).filter((a) => !a.passed).length, 1);
});
