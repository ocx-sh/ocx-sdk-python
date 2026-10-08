import assert from 'node:assert/strict';
import { test } from 'node:test';
import { GROUPS, locate } from '../reference-groups.mjs';

test('group slugs are unique and every include is on one page', () => {
  assert.equal(new Set(GROUPS.map((g) => g.slug)).size, GROUPS.length);
  const seen = new Map();
  for (const g of GROUPS) {
    for (const p of g.include) {
      assert.ok(!seen.has(p) || g.cont?.includes(p), `${p} is on ${seen.get(p)} and ${g.slug}`);
      seen.set(p, g.slug);
    }
  }
});

test('a continued class has its header on an earlier page', () => {
  GROUPS.forEach((g, i) => {
    for (const name of g.cont ?? []) {
      assert.ok(GROUPS.slice(0, i).some((e) => e.include.includes(name) && !e.cont?.includes(name)), `${g.slug}: ${name}`);
    }
  });
});

test('locate follows a member to its page and an unknown symbol to nothing', () => {
  assert.equal(locate('ocx_sdk.Ocx.invoke').id, 'ocx_sdk.Ocx.invoke');
  assert.ok(locate('ocx_sdk.Ocx.invoke').group.include.includes('Ocx.invoke'));
  assert.equal(locate('ocx_sdk.NoSuchSymbol'), undefined);
});
