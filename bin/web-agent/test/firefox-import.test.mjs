import assert from 'node:assert/strict';
import { test } from 'node:test';
import { planImport } from '../firefox-import.mjs';

const stored = (db) => async (h, u) => db[`${h}/${u}`] ?? null;

test('classifies new, same, conflicting and skipped logins', async () => {
  const records = [
    { origin: 'https://github.com', username: 'amy', password: 'p1' },
    { origin: 'https://example.com', username: 'bob', password: 'same' },
    { origin: 'https://example.com', username: 'cat', password: 'new' },
    { origin: 'chrome://FirefoxAccounts', username: 'x', password: 'y' },
    { origin: 'https://github.com', username: 'amy', password: 'p2' },
    { origin: 'https://a.io', username: 'd', password: 'two\nlines' },
  ];
  const plan = await planImport(records, stored({ 'example.com/bob': 'same', 'example.com/cat': 'old' }));
  assert.deepEqual(plan.map((p) => [p.action, p.host]), [
    ['add', 'github.com'],
    ['same', 'example.com'],
    ['conflict', 'example.com'],
    ['skip', 'chrome://FirefoxAccounts'],
    ['skip', 'github.com'],
    ['skip', 'a.io'],
  ]);
});

test('--overwrite turns conflicts into updates', async () => {
  const plan = await planImport([{ origin: 'https://x.com:8443', username: 'u', password: 'new' }], stored({ 'x.com/u': 'old' }), { overwrite: true });
  assert.equal(plan[0].action, 'update');
  assert.equal(plan[0].host, 'x.com');
});
