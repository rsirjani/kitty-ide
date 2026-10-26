// Copy Firefox's saved logins into the keychain (`ide-web-cred import firefox`).
//
// firefox_logins.py decrypts them locally via NSS and streams JSON over a
// pipe; nothing is written to disk in the clear.

import { execFile } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const HERE = path.dirname(new URL(import.meta.url).pathname);
const DECRYPTER = path.join(HERE, 'firefox_logins.py');
const PROFILE_ROOTS = [
  path.join(os.homedir(), '.config', 'mozilla', 'firefox'),
  path.join(os.homedir(), '.mozilla', 'firefox'),
];
const MAX_OUTPUT = 64 << 20;

// The profile Firefox opens by default: installs.ini's Default=, else the
// first profile that has saved logins.
export function defaultProfile(roots = PROFILE_ROOTS) {
  for (const root of roots) {
    const ini = path.join(root, 'installs.ini');
    const m = fs.existsSync(ini) && fs.readFileSync(ini, 'utf8').match(/^Default=(.+)$/m);
    const candidates = m ? [path.resolve(root, m[1].trim())] : [];
    if (fs.existsSync(root)) {
      for (const d of fs.readdirSync(root)) candidates.push(path.join(root, d));
    }
    const hit = candidates.find((p) => fs.existsSync(path.join(p, 'logins.json')));
    if (hit) return hit;
  }
  throw new Error('no Firefox profile with saved logins found (pass the profile directory)');
}

function decryptProfile(profile) {
  return new Promise((resolve, reject) => {
    execFile('python3', ['-I', DECRYPTER, profile], { maxBuffer: MAX_OUTPUT }, (err, stdout, stderr) => {
      if (stderr) process.stderr.write(stderr);
      if (err) return reject(new Error(`Firefox decryption failed: ${String(stderr).trim() || err.message}`));
      resolve(JSON.parse(stdout));
    });
  });
}

function hostOf(origin) {
  try {
    const url = new URL(origin);
    return ['http:', 'https:'].includes(url.protocol) ? url.hostname : null;
  } catch {
    return null;
  }
}

// Decide what to do with each record, given `stored(host, user)` -> password|null.
// Returns [{ action: 'add'|'update'|'same'|'conflict'|'skip', host, username, password?, reason? }].
export async function planImport(records, stored, { overwrite = false } = {}) {
  const plan = [];
  const seen = new Set();
  for (const r of records) {
    const host = hostOf(r.origin);
    const username = r.username ?? '';
    if (!host) {
      plan.push({ action: 'skip', host: r.origin, username, reason: 'not a web origin' });
      continue;
    }
    if (!r.password || /[\r\n]/.test(r.password) || /[\r\n]/.test(username)) {
      plan.push({ action: 'skip', host, username, reason: 'empty or multi-line value' });
      continue;
    }
    const key = `${host}\u0000${username}`;
    if (seen.has(key)) {
      plan.push({ action: 'skip', host, username, reason: 'duplicate in Firefox' });
      continue;
    }
    seen.add(key);
    const current = await stored(host, username);
    let action = 'add';
    if (current === r.password) action = 'same';
    else if (current !== null) action = overwrite ? 'update' : 'conflict';
    plan.push({ action, host, username, password: r.password });
  }
  return plan;
}

export async function importFirefox(kc, { profile, dryRun = false, overwrite = false } = {}) {
  const dir = profile ? path.resolve(profile) : defaultProfile();
  const records = await decryptProfile(dir);
  const plan = await planImport(records, (h, u) => kc.password(h, u), { overwrite });
  if (!dryRun) {
    for (const p of plan) {
      if (p.action === 'add' || p.action === 'update') await kc.saveLogin(p.host, p.username, p.password);
    }
  }
  return { profile: dir, plan };
}

export function summarize({ profile, plan }, { dryRun }) {
  const count = (a) => plan.filter((p) => p.action === a).length;
  const lines = plan
    .filter((p) => p.action !== 'same')
    .map((p) => `  ${p.action.padEnd(8)} ${p.host}  ${p.username || '(no username)'}${p.reason ? `  — ${p.reason}` : ''}`);
  const verb = dryRun ? 'would import' : 'imported';
  const tail = count('conflict') ? `\n${count('conflict')} differ from the keychain — rerun with --overwrite to take Firefox's` : '';
  return [
    `Firefox profile: ${profile}`,
    ...lines,
    `${verb} ${count('add') + count('update')} (${count('add')} new, ${count('update')} updated), ` +
      `${count('same')} already present, ${count('conflict')} conflicts, ${count('skip')} skipped${tail}`,
  ].join('\n');
}
