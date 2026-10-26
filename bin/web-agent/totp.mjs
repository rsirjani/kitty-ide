// RFC 6238 TOTP (and the otpauth:// URIs authenticator QR codes carry).

import crypto from 'node:crypto';

const BASE32 = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567';
const ALGORITHMS = new Set(['SHA1', 'SHA256', 'SHA512']);

export function normalizeSecret(secret) {
  const clean = String(secret).toUpperCase().replace(/[\s-]/g, '').replace(/=+$/, '');
  if (!clean || [...clean].some((c) => !BASE32.includes(c))) {
    throw new Error('TOTP secret must be base32 (A–Z, 2–7)');
  }
  return clean;
}

export function base32Decode(secret) {
  const clean = normalizeSecret(secret);
  let bits = 0;
  let value = 0;
  const out = [];
  for (const c of clean) {
    value = (value << 5) | BASE32.indexOf(c);
    bits += 5;
    if (bits >= 8) {
      out.push((value >>> (bits - 8)) & 0xff);
      bits -= 8;
    }
  }
  return Buffer.from(out);
}

// Parse a plain base32 secret or an otpauth://totp/... URI into a spec.
export function parseTotp(input) {
  const text = String(input).trim();
  if (!text.toLowerCase().startsWith('otpauth://')) {
    return { secret: normalizeSecret(text), digits: 6, period: 30, algorithm: 'SHA1', issuer: '', account: '' };
  }
  const url = new URL(text);
  if (url.host.toLowerCase() !== 'totp') throw new Error('only otpauth://totp URIs are supported');
  const label = decodeURIComponent(url.pathname.replace(/^\//, ''));
  const [labelIssuer, account] = label.includes(':') ? label.split(/:(.*)/s) : ['', label];
  const p = url.searchParams;
  const algorithm = (p.get('algorithm') || 'SHA1').toUpperCase();
  if (!ALGORITHMS.has(algorithm)) throw new Error(`unsupported TOTP algorithm ${algorithm}`);
  const digits = Number(p.get('digits') || 6);
  const period = Number(p.get('period') || 30);
  if (![6, 7, 8].includes(digits)) throw new Error(`unsupported TOTP digits ${digits}`);
  if (!(period > 0 && period <= 300)) throw new Error(`unsupported TOTP period ${period}`);
  return {
    secret: normalizeSecret(p.get('secret') || ''),
    digits,
    period,
    algorithm,
    issuer: p.get('issuer') || labelIssuer.trim(),
    account: account.trim(),
  };
}

// The code valid at `now` (ms) plus how many seconds it has left.
export function totpCode({ secret, digits = 6, period = 30, algorithm = 'SHA1' }, now = Date.now()) {
  const counter = Math.floor(now / 1000 / period);
  const msg = Buffer.alloc(8);
  msg.writeBigUInt64BE(BigInt(counter));
  const mac = crypto.createHmac(algorithm.toLowerCase(), base32Decode(secret)).update(msg).digest();
  const offset = mac[mac.length - 1] & 0x0f;
  const bin = (mac.readUInt32BE(offset) & 0x7fffffff) % 10 ** digits;
  return {
    code: String(bin).padStart(digits, '0'),
    secondsLeft: period - (Math.floor(now / 1000) % period),
  };
}
