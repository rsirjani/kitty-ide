// Keychain + verifier flows, driven by messages from the in-page UI.
//
//   query     -> offer saved logins / 2FA codes / a generated password
//   fill      -> type the chosen login into the form
//   otp       -> type the current TOTP code
//   generate  -> type a generated password into the signup form's fields
//   username  -> remember step one of a two-step login
//   submitted -> offer to save/update the password (after the page moves on)
//
// Everything typed goes in as trusted Input.insertText, so sites see real
// typing. Lookups use the frame's own location (reported from the isolated
// world, which the page can't forge), so a frame only ever gets credentials
// for its own site.

import { candidateDomains } from './keychain.mjs';

const PENDING_TTL_MS = 2 * 60_000;
const USERNAME_TTL_MS = 5 * 60_000;
// If the page doesn't navigate after a login submit (SPA), ask in place.
const ASK_IN_PLACE_MS = 2500;

// Same host, or hosts sharing a parent domain (login.x.com / www.x.com).
function sameSite(a, b) {
  if (a === b) return true;
  const da = new Set(candidateDomains(a));
  return candidateDomains(b).some((d) => da.has(d) && d.includes('.'));
}

export function createAutofill({ keychain, agent, log }) {
  const tabs = new Map(); // tab session -> { pending, lastUser }

  function tab(ctx) {
    const id = agent.tabOf(ctx.sessionId);
    if (!tabs.has(id)) tabs.set(id, { id, pending: null, lastUser: null, submitting: null });
    return tabs.get(id);
  }

  async function typeInto(ctx, role, values) {
    const count = await ctx.call('focusRole', role, 0);
    if (!count) return false;
    // One value per field; a single value split across one-digit OTP boxes.
    const parts = values.length === 1 && count > 1 && role === 'otp' ? [...values[0]] : values;
    for (let i = 0; i < Math.min(count, parts.length); i++) {
      if (i > 0) await ctx.call('focusRole', role, i);
      await ctx.type(parts[i]);
    }
    return true;
  }

  async function askSave(t) {
    const p = t.pending;
    if (!p || p.asked || Date.now() - p.at > PENDING_TTL_MS) return;
    const ctx = agent.topContext(t.id);
    if (!ctx || !sameSite(ctx.host, p.host)) return;
    p.asked = true;
    await ctx.call('askSave', JSON.stringify({ username: p.username, host: p.host, update: p.update }));
  }

  async function recordSubmit(ctx, t, { username, password }) {
    const host = ctx.host;
    if (!host || !password || keychain.neverSave().has(host)) return;
    const recent = t.lastUser && Date.now() - t.lastUser.at < USERNAME_TTL_MS && sameSite(t.lastUser.host, host);
    const user = username || (recent ? t.lastUser.username : '');

    // A password we generated for this form is saved without asking.
    if (t.pending?.generated && t.pending.password === password) {
      await keychain.saveLogin(host, user || t.pending.username, password);
      t.pending = null;
      await (agent.topContext(t.id) ?? ctx).call('toast', 'Generated password saved to keychain');
      return;
    }

    const stored = await keychain.password(host, user);
    if (stored === password) return;
    t.pending = { host, username: user, password, at: Date.now(), update: stored !== null, asked: false };
    setTimeout(() => askSave(t).catch((err) => log(`ask-save failed: ${err.message}`)), ASK_IN_PLACE_MS);
  }

  const handlers = {
    async hello(ctx) {
      if (!ctx.top) return;
      const t = tab(ctx);
      await t.submitting?.catch(() => {});
      await askSave(t);
    },

    async query(ctx, { id, kind }) {
      const host = ctx.host;
      if (!host) return;
      const answer = { id, kind, host };
      if (kind === 'otp') answer.authenticators = await keychain.authenticators(host);
      else if (kind !== 'new-password') answer.accounts = await keychain.accounts(host);
      await ctx.call('showOptions', JSON.stringify(answer));
    },

    async fill(ctx, { username }) {
      const password = await keychain.password(ctx.host, username);
      if (password === null) return ctx.call('toast', 'No saved password found');
      await typeInto(ctx, 'username', [username]);
      await typeInto(ctx, 'password', [password]);
      tab(ctx).lastUser = { host: ctx.host, username, at: Date.now() };
    },

    async otp(ctx, { username }) {
      const res = await keychain.code(ctx.host, username);
      if (!res) return ctx.call('toast', 'No 2FA secret for this site');
      await typeInto(ctx, 'otp', [res.code]);
    },

    async generate(ctx, { password, username }) {
      const typed = await typeInto(ctx, 'new-password', [password, password]);
      if (!typed) return;
      const t = tab(ctx);
      t.pending = { host: ctx.host, username: username || t.lastUser?.username || '', password, at: Date.now(), generated: true, asked: false };
    },

    async username(ctx, { username }) {
      tab(ctx).lastUser = { host: ctx.host, username, at: Date.now() };
    },

    async submitted(ctx, msg) {
      const t = tab(ctx);
      // The next page's hello can arrive before the keyring check finishes.
      t.submitting = recordSubmit(ctx, t, msg);
      await t.submitting;
    },

    async 'save-answer'(ctx, { answer }) {
      const t = tab(ctx);
      const p = t.pending;
      t.pending = null;
      if (!p) return;
      if (answer === 'save') {
        await keychain.saveLogin(p.host, p.username, p.password);
        await ctx.call('toast', 'Password saved to keychain');
      } else if (answer === 'never') {
        keychain.setNeverSave(p.host, true);
      }
    },
  };

  return async function onMessage(ctx, msg) {
    const handler = handlers[msg.t];
    if (handler) await handler(ctx, msg);
  };
}
