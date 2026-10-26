// Web credentials in the system keyring (gnome-keyring etc. via secret-tool).
//
// Schema (secret-tool attributes):
//   logins: service=carbonyl-login domain=<host> username=<user>  -> password
//   2FA:    service=carbonyl-totp  domain=<host> username=<user>  -> base32 secret
//           (+ digits, period, algorithm)
//
// An entry stored for example.com also serves its subdomains (login.example.com)
// but never a look-alike (example.com.evil.io) or a parent (com).

import { execFile } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { normalizeSecret, totpCode } from './totp.mjs';

const LOGIN = 'carbonyl-login';
const TOTP = 'carbonyl-totp';
const TOOL_TIMEOUT_MS = 60_000; // an unlock prompt may be waiting on the user
const STATE_DIR = path.join(os.homedir(), '.local', 'share', 'ide-carbonyl');
const NEVER_FILE = path.join(STATE_DIR, 'never-save.json');

// `search` prints each item's attribute lines on stderr and the rest on
// stdout; unbuffered and merged into one pipe they arrive in program order.
function defaultRunner(args, input) {
  const [cmd, argv] =
    args[0] === 'search'
      ? ['sh', ['-c', 'exec stdbuf -o0 -e0 secret-tool "$@" 2>&1', 'sh', ...args]]
      : ['secret-tool', args];
  return new Promise((resolve, reject) => {
    const child = execFile(cmd, argv, { timeout: TOOL_TIMEOUT_MS, maxBuffer: 4 << 20 }, (err, stdout, stderr) => {
      if (err && err.code === 'ENOENT') return reject(new Error('secret-tool not found (install libsecret)'));
      // lookup/search exit 1 with no output when nothing matches.
      if (err && !stdout) return resolve({ ok: false, stdout: '', stderr: String(stderr) });
      resolve({ ok: !err, stdout: String(stdout), stderr: String(stderr) });
    });
    if (input !== undefined) child.stdin.end(input);
  });
}

export function normalizeHost(host) {
  const h = String(host || '').trim().toLowerCase().replace(/^\.+|\.+$/g, '');
  if (!h || /[\s/:@]/.test(h)) throw new Error(`not a host name: ${host}`);
  return h;
}

// Domains whose entries apply to `host`: the host itself and each parent with
// at least two labels (a.b.example.com -> a.b.example.com, b.example.com,
// example.com). IPs and single-label hosts match only themselves.
export function candidateDomains(host) {
  const h = normalizeHost(host);
  if (/^[\d.]+$/.test(h) || !h.includes('.')) return [h];
  const labels = h.split('.');
  return labels.slice(0, -1).map((_, i) => labels.slice(i).join('.'));
}

// secret-tool search output: one "[path]" block per item with "key = value"
// lines. Secrets are read with `lookup`, never parsed out of search output.
export function parseSearch(stdout) {
  const items = [];
  let cur = null;
  for (const line of String(stdout).split('\n')) {
    if (/^\[.+\]$/.test(line)) {
      cur = { attributes: {} };
      items.push(cur);
      continue;
    }
    const m = cur && line.match(/^attribute\.(\S+) = (.*)$/);
    if (m) cur.attributes[m[1]] = m[2];
  }
  return items;
}

// Newlines would let a value forge "attribute.x = y" lines in search output.
function assertSingleLine(...values) {
  if (values.some((v) => /[\r\n]/.test(v))) throw new Error('username/password may not contain line breaks');
}

export class Keychain {
  #run;
  #neverFile;

  constructor({ runner = defaultRunner, neverFile = NEVER_FILE } = {}) {
    this.#run = runner;
    this.#neverFile = neverFile;
  }

  async #search(service, domain) {
    const res = await this.#run(['search', '--all', '--unlock', 'service', service, 'domain', domain]);
    return res.ok ? parseSearch(res.stdout) : [];
  }

  async #itemsFor(service, host) {
    const domains = candidateDomains(host);
    const found = await Promise.all(domains.map((d) => this.#search(service, d)));
    // Most specific domain first, so a subdomain-specific login wins.
    return found.flat();
  }

  // [{ username, domain }] for the page host — no secrets.
  async accounts(host) {
    const items = await this.#itemsFor(LOGIN, host);
    return items.map((i) => ({ username: i.attributes.username ?? '', domain: i.attributes.domain }));
  }

  async #lookup(service, domain, username) {
    const res = await this.#run(['lookup', 'service', service, 'domain', domain, 'username', username]);
    return res.ok && res.stdout ? res.stdout : null;
  }

  async password(host, username) {
    const items = await this.#itemsFor(LOGIN, host);
    const hit = items.find((i) => (i.attributes.username ?? '') === username);
    return hit ? this.#lookup(LOGIN, hit.attributes.domain, username) : null;
  }

  async saveLogin(domain, username, password) {
    if (!password) throw new Error('empty password');
    assertSingleLine(username, password);
    const d = normalizeHost(domain);
    const res = await this.#run(
      ['store', `--label=carbonyl: ${username || '(no username)'} @ ${d}`, 'service', LOGIN, 'domain', d, 'username', username],
      password,
    );
    if (!res.ok) throw new Error(`secret-tool store failed: ${res.stderr.trim()}`);
  }

  async removeLogin(domain, username) {
    const args = ['clear', 'service', LOGIN, 'domain', normalizeHost(domain)];
    await this.#run(username === undefined ? args : [...args, 'username', username]);
  }

  async listLogins() {
    const res = await this.#run(['search', '--all', '--unlock', 'service', LOGIN]);
    return (res.ok ? parseSearch(res.stdout) : []).map((i) => ({
      domain: i.attributes.domain,
      username: i.attributes.username ?? '',
    }));
  }

  // [{ username, domain }] of 2FA authenticators for the page host.
  async authenticators(host) {
    const items = await this.#itemsFor(TOTP, host);
    return items.map((i) => ({ username: i.attributes.username ?? '', domain: i.attributes.domain }));
  }

  async code(host, username) {
    const items = await this.#itemsFor(TOTP, host);
    const hit = items.find((i) => (i.attributes.username ?? '') === username) ?? (username === undefined ? items[0] : null);
    if (!hit) return null;
    const a = hit.attributes;
    const secret = await this.#lookup(TOTP, a.domain, a.username ?? '');
    if (!secret) return null;
    return totpCode({
      secret,
      digits: Number(a.digits || 6),
      period: Number(a.period || 30),
      algorithm: a.algorithm || 'SHA1',
    });
  }

  async saveTotp(domain, username, spec) {
    assertSingleLine(username);
    const d = normalizeHost(domain);
    const res = await this.#run(
      [
        'store', `--label=carbonyl 2FA: ${username || '(default)'} @ ${d}`,
        'service', TOTP, 'domain', d, 'username', username,
        'digits', String(spec.digits), 'period', String(spec.period), 'algorithm', spec.algorithm,
      ],
      normalizeSecret(spec.secret),
    );
    if (!res.ok) throw new Error(`secret-tool store failed: ${res.stderr.trim()}`);
  }

  async removeTotp(domain, username) {
    const args = ['clear', 'service', TOTP, 'domain', normalizeHost(domain)];
    await this.#run(username === undefined ? args : [...args, 'username', username]);
  }

  async listTotp() {
    const res = await this.#run(['search', '--all', '--unlock', 'service', TOTP]);
    return (res.ok ? parseSearch(res.stdout) : []).map((i) => ({
      domain: i.attributes.domain,
      username: i.attributes.username ?? '',
    }));
  }

  // Hosts the user said "never save" for.
  neverSave() {
    try {
      return new Set(JSON.parse(fs.readFileSync(this.#neverFile, 'utf8')));
    } catch {
      return new Set();
    }
  }

  setNeverSave(host, never) {
    const hosts = this.neverSave();
    const h = normalizeHost(host);
    const next = never ? [...hosts, h] : [...hosts].filter((x) => x !== h);
    fs.mkdirSync(path.dirname(this.#neverFile), { recursive: true });
    fs.writeFileSync(this.#neverFile, JSON.stringify([...new Set(next)].sort(), null, 1));
  }
}
