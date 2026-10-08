// Generates site/src/content/docs from ../docs and ../CHANGELOG.md.
// docs/ stays the source of truth (Sybil collects it); nothing here edits it.
import { mkdirSync, readFileSync, readdirSync, rmSync, writeFileSync } from 'node:fs';
import { dirname, join, posix, relative } from 'node:path';
import { fileURLToPath } from 'node:url';
import { API_BASE, GROUPS, locate } from '../reference-groups.mjs';

const root = fileURLToPath(new URL('../../', import.meta.url));
const docs = join(root, 'docs');
const out = fileURLToPath(new URL('../src/content/docs/', import.meta.url));

const walk = (dir) =>
  readdirSync(dir, { withFileTypes: true }).flatMap((e) =>
    e.isDirectory() ? walk(join(dir, e.name)) : e.name.endsWith('.md') ? [join(dir, e.name)] : [],
  );

// docs-relative page path -> directory-style URL path ("guide/bootstrap/", "" for the index).
const urlOf = (rel) => rel.replace(/(^|\/)index\.md$/, '$1').replace(/\.md$/, '/').replace(/^\.\/$/, '');

const yamlStr = (s) => JSON.stringify(s);

function groupIndex() {
  const rows = GROUPS.map((g) => `- [${g.label}](${g.slug}/): ${g.intro ? 'the package overview' : g.names.join(', ')}`);
  return `## Pages\n\nEvery symbol has its own page; \`Ocx\`, \`Project\` and \`PackageCommands\` span several.\n\n${rows.join('\n')}`;
}

// Pages over the HTML or DOM budget split at these `##` headings ([heading prefix, part slug]); the
// first part keeps the page's URL, the rest nest under it. Links to a moved heading follow it.
const SPLITS = {
  'guide/authoring.md': [['`announce`', 'announce']],
  'guide/bootstrap.md': [['Corporate mirror', 'mirror-and-auth']],
  'guide/concepts/errors-and-security.md': [['`PackageRef`', 'package-ref-and-credentials']],
  'guide/pytest-fixture.md': [['Keep the test hermetic', 'hermetic']],
  'guide/hermetic-ci.md': [['The forge identity ladder', 'forge-identity']],
  'reference/command-map.md': [
    ['Package tier', 'package-tier'],
    ['Config tier', 'config-index-patch-self'],
  ],
  'reference/environment.md': [
    ['Auth', 'auth'],
    ['Discovery', 'discovery-and-exit-codes'],
  ],
};

// github-slugger, which Starlight uses for heading ids.
const slugify = (text) =>
  text
    .replace(/[`*_]/g, (c) => (c === '_' ? c : ''))
    .toLowerCase()
    .replace(/[^\p{L}\p{M}\p{N}\p{Pc} -]/gu, '')
    .replace(/ /g, '-');

/** Heading ids of a markdown body (outside fences), in order, with github-slugger's -1 suffixes. */
function headingIds(body) {
  const seen = new Map();
  const ids = [];
  let fenced = false;
  for (const line of body.split('\n')) {
    if (/^```/.test(line)) fenced = !fenced;
    const m = !fenced && /^#{2,6} (.+)$/.exec(line);
    if (!m) continue;
    const id = slugify(m[1]);
    const n = seen.get(id) ?? 0;
    seen.set(id, n + 1);
    ids.push(n ? `${id}-${n}` : id);
  }
  return ids;
}

/** One docs page as one or more output pages: {rel, title, body, last, next?}. */
function prepare(rel, src) {
  let body = src;
  body = body.replace('{% include-markdown "../CHANGELOG.md" %}', () => readFileSync(join(root, 'CHANGELOG.md'), 'utf8'));
  body = body.replace(/^<!-- doc_(?:type|tier): [\w-]+ -->\n/gm, '').replace(/^\n+/, '');
  const h1 = /^# (.+)\n/m.exec(body);
  const title = h1 ? h1[1].replace(/`/g, '') : rel;
  if (h1) body = body.replace(h1[0], '').replace(/^\n+/, '');

  const cuts = SPLITS[rel] ?? [];
  if (!cuts.length) return [{ rel, title, body }];
  const bounds = cuts.map(([prefix]) => {
    const at = body.search(new RegExp(`^## ${prefix.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}`, 'm'));
    if (at < 0) throw new Error(`${rel}: no "## ${prefix}" heading to split at`);
    return at;
  });
  return [0, ...bounds].map((from, i) => {
    const chunk = body.slice(from, bounds[i]).trimEnd();
    if (i === 0) return { rel, title, body: chunk };
    const heading = /^## (.+)$/m.exec(chunk)[1].replace(/`/g, '');
    return { rel: `${rel.replace(/\.md$/, '')}/${cuts[i - 1][1]}.md`, title: `${title}: ${heading.split(' — ')[0]}`, body: chunk };
  });
}

function convert(page, parts, anchors) {
  const { rel, title } = page;
  let body = page.body;
  const here = parts.findIndex((p) => p.rel === rel);
  const mine = parts[0].rel;

  // The API reference is split into group pages: `api.md#ocx_sdk.X` points at the page that documents X.
  body = body.replace(/\]\((?:\.\.\/|(?:\.\.\/)*reference\/)?api\.md#(ocx_sdk\.[\w.]+)\)/g, (m, path) => {
    const hit = locate(path);
    if (!hit) throw new Error(`${rel}: ${path} is on no API group page (reference-groups.mjs)`);
    return `](${posix.relative(posix.join('/', urlOf(rel)), `/${API_BASE}/${hit.group.slug}/`)}/#${hit.id})`;
  });

  // Relative links to sibling .md pages become relative directory URLs; a heading that moved to another part of
  // a split page is reached through that part. Bare `#frag` links get the same treatment inside split pages.
  const from = posix.join('/', urlOf(rel));
  const link = (targetRel, frag) => {
    const home = anchors.get(`${targetRel}${frag}`) ?? targetRel;
    const to = posix.join('/', urlOf(home));
    return `${posix.relative(from, to) || '.'}/${frag}`;
  };
  body = body.replace(/\]\(((?!https?:|mailto:|#|\/)[^)\s]+?\.md)(#[^)\s]*)?\)/g, (_, target, frag = '') =>
    `](${link(posix.normalize(posix.join(posix.dirname(mine), target)), frag)})`,
  );
  if (parts.length > 1) body = body.replace(/\]\((#[^)\s]+)\)/g, (m, frag) => (anchors.has(`${mine}${frag}`) ? `](${link(mine, frag)})` : m));

  // `<Autodoc name="ocx_sdk" />` would render all 800 symbols on one page; list the group pages instead.
  body = body.replace(/^<Autodoc name="ocx_sdk" \/>$/m, () => groupIndex());
  if (here > 0) body = `Part ${here + 1} of ${parts.length} of [${parts[0].title}](${posix.relative(from, posix.join('/', urlOf(mine)))}/).\n\n${body}`;
  if (here >= 0 && here < parts.length - 1) {
    const next = parts[here + 1];
    body = `${body}\n\nContinues on [${next.title}](${posix.relative(from, posix.join('/', urlOf(next.rel)))}/).`;
  }
  return { file: rel, text: `---\ntitle: ${yamlStr(title)}\n---\n\n${body.trimEnd()}\n` };
}

const pages = walk(docs).map((abs) => {
  const rel = relative(docs, abs).split('\\').join('/');
  return prepare(rel, readFileSync(abs, 'utf8'));
});
// "<page rel>#<heading id>" -> rel of the part that holds it.
const anchors = new Map();
for (const parts of pages) for (const part of parts) for (const id of headingIds(part.body)) anchors.set(`${parts[0].rel}#${id}`, part.rel);

rmSync(out, { recursive: true, force: true });
for (const parts of pages) {
  for (const part of parts) {
    const { file, text } = convert(part, parts, anchors);
    mkdirSync(dirname(join(out, file)), { recursive: true });
    writeFileSync(join(out, file), text);
  }
}

writeFileSync(
  join(out, '404.md'),
  `---
title: Not Found
description: The page you are looking for does not exist.
---

The page does not exist. It may have moved, or the link that brought you here may have a typo.

- Start again from the [home page](/integrations/python/).
- Use the search in the header to find the topic by name.
- Check the address for a missing or extra trailing slash.
`,
);
