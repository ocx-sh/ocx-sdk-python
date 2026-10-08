#!/usr/bin/env node
/**
 * `task site:lighthouse`: builds the site, serves it with `astro preview` under its base path and
 * audits every built HTML page with Lighthouse (mobile) through lhci's assertion engine: all four
 * categories at 1 plus the budgets in ../lighthouse.budgets.mjs. A failing page gets two more runs
 * and is judged on the median run. Trimmed from ocx-sh/website `scripts/lighthouse.mjs` (no lanes,
 * no result cache: one Chrome, one page at a time).
 *
 * Flags: `--no-build` reuse site/dist; `--list` print the pages and exit (no browser, no server);
 * `--only=<regex>` audit just the matching URL paths (a partial run for iterating; CI runs them all).
 * Env: `LH_HTML=1` adds HTML reports; `CHROME_PATH` overrides Playwright's Chromium.
 */
import { spawn, spawnSync } from 'node:child_process';
import { mkdirSync, rmSync, writeFileSync } from 'node:fs';
import Module, { createRequire } from 'node:module';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { BASE, assertions, pages } from '../lighthouse.budgets.mjs';

const require = createRequire(import.meta.url);
const SITE = join(dirname(fileURLToPath(import.meta.url)), '..');
const PORT = 4331;
/** Runs a failing page gets in total before its median run is judged. */
export const RETRY_RUNS = 3;
export const ASSERT_MATRIX = [{ matchingUrlPattern: '.*', assertions: assertions() }];

const slug = (path) => path.replace(/^\/|\/$/g, '').replace(/[^\w.-]+/g, '_') || 'index';

async function waitFor(url) {
  for (let i = 0; i < 100; i++) {
    try {
      if ((await fetch(url)).ok) return;
    } catch {
      // not up yet
    }
    await new Promise((r) => setTimeout(r, 200));
  }
  throw new Error(`preview did not come up at ${url}`);
}

async function main() {
  const args = process.argv.slice(2);
  if (!args.includes('--no-build') && !args.includes('--list')) {
    const build = spawnSync('pnpm', ['build'], { cwd: SITE, stdio: 'inherit' });
    if (build.status !== 0) throw new Error('site build failed');
  }
  const only = args.find((a) => a.startsWith('--only='))?.slice(7);
  const list = pages().filter((path) => !only || new RegExp(only).test(path));
  if (args.includes('--list')) return console.log(list.join('\n'));

  const html = process.env.LH_HTML === '1';
  const reports = join(SITE, '.lighthouseci');
  rmSync(reports, { recursive: true, force: true });
  mkdirSync(reports, { recursive: true });

  // chrome-launcher's WSL branch hands a Linux Chrome a Windows profile path; pin `is-wsl` to false.
  const isWsl = createRequire(require.resolve('chrome-launcher')).resolve('is-wsl');
  require.cache[isWsl] = Object.assign(new Module(isWsl), { filename: isWsl, loaded: true, exports: false });
  const { default: lighthouse } = await import('lighthouse');
  const { launch } = await import('chrome-launcher');
  const { chromium } = await import('playwright-core');
  const utils = require.resolve('@lhci/utils/src/assertions.js', { paths: [require.resolve('@lhci/cli/package.json')] });
  const { getAllAssertionResults } = require(utils);
  const { computeRepresentativeRuns } = require(utils.replace('assertions.js', 'representative-runs.js'));

  const preview = spawn('pnpm', ['exec', 'astro', 'preview', '--host', '127.0.0.1', '--port', String(PORT)], {
    cwd: SITE,
    stdio: 'ignore',
  });
  const chrome = await launch({
    chromePath: process.env.CHROME_PATH || chromium.executablePath(),
    chromeFlags: ['--headless=new', '--no-sandbox'],
    handleSIGINT: false,
  });
  const failures = [];
  try {
    await waitFor(`http://127.0.0.1:${PORT}${BASE}`);
    const once = async (url) => {
      const r = await lighthouse(url, {
        port: chrome.port,
        output: html ? 'html' : 'json',
        logLevel: 'error',
        disableFullPageScreenshot: true,
      });
      if (!r) throw new Error('Lighthouse returned no result');
      return r;
    };
    const judge = (runs) => {
      const [median] = computeRepresentativeRuns([runs.map((r) => [r.lhr, r.lhr])]);
      return getAllAssertionResults({ assertMatrix: ASSERT_MATRIX }, [median]).filter((a) => !a.passed);
    };
    for (const path of list) {
      const url = `http://127.0.0.1:${PORT}${path}`;
      const runs = [await once(url)];
      let failed = judge(runs);
      if (failed.length) {
        while (runs.length < RETRY_RUNS) runs.push(await once(url));
        failed = judge(runs);
      }
      runs.forEach((r, i) => {
        const base = join(reports, `${slug(path)}-${i + 1}.report`);
        writeFileSync(`${base}.json`, JSON.stringify(r.lhr));
        if (html) writeFileSync(`${base}.html`, String(r.report));
      });
      console.log(`${failed.length ? 'FAIL' : 'pass'} ${path} (${runs.length} run(s))`);
      for (const a of failed) {
        failures.push(`${path}: ${a.auditId}${a.auditProperty ? `:${a.auditProperty}` : ''} ${a.actual} (want ${a.operator} ${a.expected})`);
      }
    }
  } finally {
    chrome.kill();
    preview.kill();
  }
  if (failures.length) {
    console.error(`\n${failures.length} assertion(s) failed (median of ${RETRY_RUNS} runs):\n  ${failures.join('\n  ')}`);
    process.exitCode = 1;
  }
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  main().catch((err) => {
    console.error(`lighthouse: FAILED - ${err instanceof Error ? (err.stack ?? err.message) : String(err)}`);
    process.exitCode = 1;
  });
}
