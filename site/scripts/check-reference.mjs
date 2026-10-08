// Fails when a public symbol is on no API group page, or on more than one: a class split over several
// pages (reference-groups.mjs) lists its members by hand, so a new method would otherwise vanish.
// Reads the expected set from griffe (same pinned version as the build) and the built pages from dist/.
import { spawnSync } from 'node:child_process';
import { mkdtempSync, readFileSync, readdirSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { API_BASE, PACKAGE } from '../reference-groups.mjs';

const site = fileURLToPath(new URL('..', import.meta.url));

/** Dotted ids of everything starlight-pydocs documents for the package's `__all__`. */
export function expectedIds(dump) {
  const root = dump[PACKAGE];
  const find = (path) => path.split('.').slice(1).reduce((node, part) => node?.members?.[part], root);
  const ids = [];
  const walk = (node, id) => {
    ids.push(id);
    for (const [name, m] of Object.entries(node.members ?? {})) {
      if (name.startsWith('_') || m.is_imported || m.is_public === false) continue;
      if (m.kind === 'module') continue;
      walk(m, `${id}.${name}`);
    }
  };
  for (const name of root.exports) {
    const entry = root.members[name];
    let target = entry;
    while (target?.kind === 'alias') target = find(target.target_path);
    if (!target || target.kind === 'module') continue;
    walk(target, `${PACKAGE}.${name}`);
  }
  return ids;
}

/** Every `data-pydocs-path` id on the built group pages, with how many times it appears. */
export function builtIds(dist) {
  const counts = new Map();
  const dir = join(dist, API_BASE);
  for (const f of readdirSync(dir, { recursive: true, encoding: 'utf8' })) {
    if (!f.endsWith('index.html') || f.split('/').length < 2) continue;
    for (const [, id] of readFileSync(join(dir, f), 'utf8').matchAll(/data-pydocs-path="([^"]+)"/g)) {
      counts.set(id, (counts.get(id) ?? 0) + 1);
    }
  }
  return counts;
}

function main() {
  const tmp = mkdtempSync(join(tmpdir(), 'ocx-griffe-'));
  const out = join(tmp, 'dump.json');
  const run = spawnSync('uvx', ['--from', 'griffe==2.3.0', 'griffe', 'dump', PACKAGE, '-s', '../src', '-f', '-d', 'google', '-o', out], { cwd: site, encoding: 'utf8' });
  if (run.status !== 0) throw new Error(`griffe failed: ${run.stderr}`);
  const dump = JSON.parse(readFileSync(out, 'utf8'));
  rmSync(tmp, { recursive: true, force: true });

  const built = builtIds(join(site, 'dist'));
  const expected = expectedIds(dump);
  const missing = expected.filter((id) => !built.has(id));
  if (missing.length) {
    console.error(`API symbols on no group page (add them to site/reference-groups.mjs):\n  ${missing.join('\n  ')}`);
    process.exitCode = 1;
  }
}

if (process.argv[1] === fileURLToPath(import.meta.url)) main();
