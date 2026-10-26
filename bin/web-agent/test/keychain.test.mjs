import assert from 'node:assert/strict';
import { test } from 'node:test';
import { candidateDomains, Keychain, parseSearch } from '../keychain.mjs';

test('candidate domains cover parents but never the bare TLD', () => {
  assert.deepEqual(candidateDomains('a.login.Example.com'), ['a.login.example.com', 'login.example.com', 'example.com']);
  assert.deepEqual(candidateDomains('localhost'), ['localhost']);
  assert.deepEqual(candidateDomains('10.0.0.1'), ['10.0.0.1']);
  assert.throws(() => candidateDomains('evil.com/x'));
});

test('search parser reads attributes only, never secrets', () => {
  const out = '[/1]\nlabel = x\nsecret = hunter2\nattribute.domain = example.com\nattribute.username = bob\n[/2]\nsecret = y\nattribute.domain = example.com\nattribute.username = amy\n';
  assert.deepEqual(parseSearch(out), [
    { attributes: { domain: 'example.com', username: 'bob' } },
    { attributes: { domain: 'example.com', username: 'amy' } },
  ]);
});

test('look-alike domains get nothing; subdomains inherit', async () => {
  const store = [{ domain: 'example.com', username: 'bob', secret: 'pw' }];
  const runner = async (args) => {
    const attr = (k) => args[args.indexOf(k) + 1];
    const hits = store.filter((e) => e.domain === attr('domain') && (!args.includes('username') || e.username === attr('username')));
    if (args[0] === 'lookup') return { ok: hits.length > 0, stdout: hits[0]?.secret ?? '' };
    const stdout = hits.map((e, i) => `[/${i}]\nattribute.domain = ${e.domain}\nattribute.username = ${e.username}\n`).join('');
    return { ok: hits.length > 0, stdout };
  };
  const kc = new Keychain({ runner, neverFile: '/nonexistent/never.json' });
  assert.deepEqual(await kc.accounts('login.example.com'), [{ username: 'bob', domain: 'example.com' }]);
  assert.equal(await kc.password('login.example.com', 'bob'), 'pw');
  assert.deepEqual(await kc.accounts('example.com.evil.io'), []);
  assert.equal(await kc.password('example.com.evil.io', 'bob'), null);
});

test('values with line breaks are rejected', async () => {
  const kc = new Keychain({ runner: async () => ({ ok: true, stdout: '' }) });
  await assert.rejects(kc.saveLogin('example.com', 'bob\nattribute.domain = x', 'pw'));
});
