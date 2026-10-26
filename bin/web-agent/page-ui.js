// Keychain UI, evaluated by ide-web-agent in an ISOLATED world of each frame.
//
// The page cannot see or call anything defined here: isolated worlds share the
// DOM but not JS objects, so our listeners, prototypes and the closed shadow
// root holding the dropdown are out of the page's reach. Only trusted events
// (real user input, isTrusted) drive it, so page script can't click "fill".
//
// Protocol with the agent:
//   UI -> agent : the agent long-polls `__kc.next()` (a Promise per message).
//   agent -> UI : the agent calls `__kc.<method>(...)` via Runtime.evaluate.
// Stored secrets never pass through here — fills are typed by the agent with
// Input.insertText into the field this script focuses.
(() => {
  if (globalThis.__kc) return;

  const OTP_RE = /one[-_ ]?time|\botp\b|totp|2fa|mfa|two[-_ ]?factor|verification[-_ ]?code|verify[-_ ]?code|auth(entication|enticator)?[-_ ]?code|security[-_ ]?code|passcode|\bpin\b/i;
  const USER_RE = /user|login|e-?mail|account|identifier|phone|handle/i;
  const NEW_PW_RE = /new|confirm|repeat|retype|signup|register|create/i;
  const TEXTLIKE = new Set(['text', 'email', 'tel', 'number', '']);
  const GEN_LENGTH = 20;
  const GEN_CHARS = 'ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789-_.!@#%';
  const SUBMIT_DEDUPE_MS = 3000;
  const BLUR_CLOSE_MS = 150;
  const TOAST_MS = 2500;
  const MIN_OTP_BOXES = 4;

  // ---- messages to the agent ------------------------------------------------
  const outbox = [];
  let waiter = null;
  function post(msg) {
    const text = JSON.stringify(msg);
    if (waiter) {
      const w = waiter;
      waiter = null;
      w(text);
    } else {
      outbox.push(text);
    }
  }
  function next() {
    return new Promise((resolve) => {
      if (outbox.length) resolve(outbox.shift());
      else waiter = resolve;
    });
  }

  // ---- field classification -------------------------------------------------
  function labelText(field) {
    const parts = [];
    if (field.labels) for (const l of field.labels) parts.push(l.textContent);
    const by = field.getAttribute('aria-labelledby');
    if (by) for (const id of by.split(/\s+/)) parts.push(document.getElementById(id)?.textContent ?? '');
    return parts.join(' ');
  }
  function describe(field) {
    return [field.name, field.id, field.placeholder, field.getAttribute('aria-label'), field.autocomplete, labelText(field)].join(' ');
  }
  function visible(field) {
    if (!field.isConnected || field.disabled || field.readOnly || field.type === 'hidden') return false;
    const r = field.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) return false;
    const cs = getComputedStyle(field);
    return cs.visibility !== 'hidden' && cs.display !== 'none' && Number(cs.opacity) > 0.05;
  }
  function scopeOf(field) {
    return field.form ?? document;
  }
  function inputsIn(sc) {
    return [...sc.querySelectorAll('input')].filter(visible);
  }
  function passwordsIn(sc) {
    return inputsIn(sc).filter((i) => i.type === 'password');
  }
  function isNewPassword(field) {
    const ac = (field.autocomplete || '').toLowerCase();
    if (ac.includes('new-password')) return true;
    if (ac.includes('current-password')) return false;
    if (NEW_PW_RE.test(describe(field))) return true;
    return passwordsIn(scopeOf(field)).length >= 2;
  }
  // The boxes of a "one digit per box" OTP widget that `field` belongs to.
  function otpBoxes(field) {
    const group = field.parentElement?.parentElement ?? field.parentElement;
    if (!group) return [field];
    return [...group.querySelectorAll('input')].filter((i) => i.maxLength === 1 && visible(i));
  }
  function isOtpBox(field) {
    return field.maxLength === 1 && otpBoxes(field).length >= MIN_OTP_BOXES;
  }
  function usernameIn(sc) {
    const inputs = inputsIn(sc);
    const pw = inputs.findIndex((i) => i.type === 'password');
    const before = (pw === -1 ? inputs : inputs.slice(0, pw)).filter((i) => TEXTLIKE.has(i.type) && !isOtpBox(i));
    const tagged = before.find((i) => {
      const ac = (i.autocomplete || '').toLowerCase();
      return ac.includes('username') || ac.includes('email') || i.type === 'email';
    });
    if (tagged) return tagged;
    if (pw === -1) return before.find((i) => USER_RE.test(describe(i)) && !OTP_RE.test(describe(i))) ?? null;
    return before.at(-1) ?? null;
  }
  function kindOf(field) {
    if (!(field instanceof HTMLInputElement) || !visible(field)) return null;
    if (field.type === 'password') return isNewPassword(field) ? 'new-password' : 'password';
    if (!TEXTLIKE.has(field.type)) return null;
    const ac = (field.autocomplete || '').toLowerCase();
    if (ac.includes('one-time-code') || OTP_RE.test(describe(field)) || isOtpBox(field)) return 'otp';
    if (ac.includes('username') || ac.includes('email') || field.type === 'email' || USER_RE.test(describe(field))) {
      return 'username';
    }
    // A plain text box right before a password field is the username.
    return usernameIn(scopeOf(field)) === field ? 'username' : null;
  }
  function fieldsFor(role, sc) {
    switch (role) {
      case 'username': {
        const u = usernameIn(sc);
        return u ? [u] : [];
      }
      case 'password': {
        const pws = passwordsIn(sc);
        const current = pws.find((p) => !isNewPassword(p)) ?? pws[0];
        return current ? [current] : [];
      }
      case 'new-password':
        return passwordsIn(sc).filter(isNewPassword);
      case 'otp': {
        const otp = inputsIn(sc).find((i) => kindOf(i) === 'otp');
        if (!otp) return [];
        return isOtpBox(otp) ? otpBoxes(otp) : [otp];
      }
      default:
        return [];
    }
  }
  function firstLoginField() {
    return inputsIn(document).find((i) => kindOf(i)) ?? null;
  }

  // ---- overlay UI (closed shadow root) --------------------------------------
  const STYLE = `
    :host { all: initial; }
    .box { position: fixed; z-index: 2147483647; min-width: 240px; max-width: 420px;
      background: #1e1f29; color: #e6e6f0; border: 1px solid #555a78; border-radius: 6px;
      box-shadow: 0 6px 22px rgba(0,0,0,.45); font: 13px/1.35 system-ui, sans-serif; overflow: hidden; }
    .head { padding: 5px 10px; font-size: 11px; color: #9aa0c0; border-bottom: 1px solid #33364a; }
    .item { padding: 7px 10px; cursor: pointer; display: flex; justify-content: space-between; gap: 12px; }
    .item .sub { color: #9aa0c0; font-size: 11px; }
    .item.on { background: #3b3f5c; }
    .card { position: fixed; z-index: 2147483647; top: 10px; right: 10px; width: 330px;
      background: #1e1f29; color: #e6e6f0; border: 1px solid #555a78; border-radius: 8px;
      box-shadow: 0 6px 22px rgba(0,0,0,.45); font: 13px/1.4 system-ui, sans-serif; padding: 12px 14px; }
    .card b { word-break: break-all; }
    .row { display: flex; gap: 8px; justify-content: flex-end; margin-top: 10px; }
    button { all: unset; cursor: pointer; padding: 5px 12px; border-radius: 4px; background: #33364a; color: #e6e6f0; }
    button.primary { background: #5865f2; color: #fff; }
    .toast { position: fixed; z-index: 2147483647; bottom: 14px; right: 14px; padding: 8px 12px;
      background: #1e1f29; color: #e6e6f0; border: 1px solid #555a78; border-radius: 6px;
      font: 13px system-ui, sans-serif; }
  `;
  let root = null;
  function shadow() {
    if (root?.host.isConnected) return root;
    const host = document.createElement('kc-overlay');
    host.style.cssText =
      'all: initial !important; position: fixed !important; top: 0 !important; left: 0 !important; ' +
      'width: 0 !important; height: 0 !important; z-index: 2147483647 !important;';
    root = host.attachShadow({ mode: 'closed' });
    const style = document.createElement('style');
    style.textContent = STYLE;
    root.append(style);
    document.documentElement.append(host);
    return root;
  }
  function h(tag, cls, ...children) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    node.append(...children);
    return node;
  }
  // Activate on a trusted press only. The mousedown default is cancelled so
  // the page's field keeps focus while the dropdown is used.
  function onPress(node, handler) {
    node.addEventListener('mousedown', (e) => {
      e.preventDefault();
      e.stopPropagation();
      if (e.isTrusted && e.button === 0) handler();
    });
  }

  let dropdown = null; // { node, field, items, sel }
  let scope = document;
  let queryId = 0;
  let fillingFocus = false; // focus moves made by a fill must not reopen the dropdown

  function closeDropdown() {
    dropdown?.node.remove();
    dropdown = null;
  }
  function placeDropdown() {
    if (!dropdown) return;
    const r = dropdown.field.getBoundingClientRect();
    const below = r.bottom + 4;
    const fitsBelow = below + dropdown.node.offsetHeight < innerHeight;
    dropdown.node.style.left = `${Math.max(4, r.left)}px`;
    dropdown.node.style.top = `${fitsBelow ? below : Math.max(4, r.top - dropdown.node.offsetHeight - 4)}px`;
  }
  function highlight(i) {
    if (!dropdown) return;
    dropdown.sel = (i + dropdown.items.length) % dropdown.items.length;
    dropdown.items.forEach((it, n) => it.node.classList.toggle('on', n === dropdown.sel));
  }
  function choose(it) {
    closeDropdown();
    it.run();
  }

  function generatePassword() {
    const bytes = new Uint32Array(GEN_LENGTH);
    for (;;) {
      crypto.getRandomValues(bytes);
      const pw = [...bytes].map((b) => GEN_CHARS[b % GEN_CHARS.length]).join('');
      if (/[a-z]/.test(pw) && /[A-Z]/.test(pw) && /\d/.test(pw) && /[^A-Za-z\d]/.test(pw)) return pw;
    }
  }

  function query(field, kind) {
    scope = scopeOf(field);
    queryId += 1;
    post({ t: 'query', id: queryId, kind });
  }

  function optionsFor(kind, { accounts = [], authenticators = [], host }) {
    if (kind === 'otp') {
      return authenticators.map((a) => ({
        label: 'Fill 2FA code',
        sub: a.username || a.domain,
        run: () => post({ t: 'otp', username: a.username }),
      }));
    }
    if (kind === 'new-password') {
      return [
        {
          label: 'Use a strong generated password',
          sub: 'saved to keychain',
          run: () => post({ t: 'generate', password: generatePassword(), username: usernameIn(scope)?.value?.trim() ?? '' }),
        },
      ];
    }
    return accounts.map((a) => ({
      label: a.username || '(no username)',
      sub: a.domain !== host ? a.domain : '',
      run: () => post({ t: 'fill', username: a.username }),
    }));
  }

  // ---- agent-callable API ---------------------------------------------------
  const api = {
    next,

    // The agent's answer to a 'query': render the dropdown under the field.
    showOptions(json) {
      const answer = JSON.parse(json);
      const field = document.activeElement;
      if (answer.id !== queryId || !(field instanceof HTMLInputElement) || kindOf(field) !== answer.kind) return false;
      closeDropdown();
      const items = optionsFor(answer.kind, answer);
      if (!items.length) return false;

      const node = h('div', 'box', h('div', 'head', answer.kind === 'otp' ? 'Verifier' : 'Keychain'));
      items.forEach((it, n) => {
        it.node = h('div', 'item', h('span', '', it.label), h('span', 'sub', it.sub));
        onPress(it.node, () => choose(it));
        it.node.addEventListener('mousemove', () => highlight(n));
        node.append(it.node);
      });
      shadow().append(node);
      dropdown = { node, field, items, sel: 0 };
      highlight(0);
      placeDropdown();
      return true;
    },

    // Focus the n-th field for `role` in the active scope and select its text,
    // so the agent's Input.insertText replaces it. Returns the field count.
    focusRole(role, n = 0) {
      closeDropdown();
      const fields = fieldsFor(role, scope);
      const f = fields[n];
      if (!f) return fields.length;
      fillingFocus = true;
      f.focus();
      fillingFocus = false;
      f.select?.();
      return fields.length;
    },

    askSave(json) {
      const { username, host, update } = JSON.parse(json);
      if (window !== top) return false;
      const save = h('button', 'primary', update ? 'Update' : 'Save');
      const never = h('button', '', 'Never for this site');
      const dismiss = h('button', '', 'Not now');
      const card = h(
        'div',
        'card',
        h('div', '', update ? 'Update the saved password for ' : 'Save the password for ', h('b', '', username || 'this site'), ' on ', h('b', '', host), '?'),
        h('div', 'row', dismiss, never, save),
      );
      const answer = (a) => {
        card.remove();
        post({ t: 'save-answer', answer: a });
      };
      onPress(save, () => answer('save'));
      onPress(never, () => answer('never'));
      onPress(dismiss, () => answer('dismiss'));
      shadow().append(card);
      return true;
    },

    toast(text) {
      const t = h('div', 'toast', text);
      shadow().append(t);
      setTimeout(() => t.remove(), TOAST_MS);
      return true;
    },
  };

  // ---- save capture ---------------------------------------------------------
  let lastSubmit = { key: '', at: 0 };
  function captureSubmit(from) {
    const sc = from instanceof HTMLElement ? scopeOf(from) : document;
    const pws = passwordsIn(sc).filter((p) => p.value);
    const user = usernameIn(sc)?.value?.trim() ?? '';
    if (!pws.length) {
      // Step one of a two-step login (email page, then password page).
      if (user) post({ t: 'username', username: user });
      return;
    }
    const password = (pws.filter(isNewPassword).at(-1) ?? pws[0]).value;
    const key = `${user}\u0000${password}`;
    if (key === lastSubmit.key && Date.now() - lastSubmit.at < SUBMIT_DEDUPE_MS) return;
    lastSubmit = { key, at: Date.now() };
    post({ t: 'submitted', username: user, password });
  }

  // ---- listeners ------------------------------------------------------------
  addEventListener(
    'focusin',
    (e) => {
      const f = e.target;
      const kind = e.isTrusted && !fillingFocus && kindOf(f);
      if (!kind) return closeDropdown();
      if (dropdown?.field !== f) query(f, kind);
    },
    true,
  );
  addEventListener(
    'focusout',
    (e) => {
      if (dropdown && e.target === dropdown.field) {
        setTimeout(() => document.activeElement !== dropdown?.field && closeDropdown(), BLUR_CLOSE_MS);
      }
    },
    true,
  );
  addEventListener('scroll', placeDropdown, true);
  addEventListener('resize', placeDropdown);

  // Normalize a key event to 'Enter' | 'Escape' | 'ArrowUp' | 'ArrowDown' |
  // 'Ctrl+L' | ''. Desktop Chrome fills in e.key; carbonyl's input bridge
  // doesn't — it sends Enter/Escape/Ctrl+letter as bare char events (keypress
  // charCode 13/27/12, no ctrlKey) and arrows as keydown keyCodes only.
  const CHAR_KEYS = { 13: 'Enter', 27: 'Escape', 12: 'Ctrl+L' };
  const CODE_KEYS = { 38: 'ArrowUp', 40: 'ArrowDown' };
  function keyName(e) {
    if (e.key) {
      if (e.ctrlKey && !e.altKey && !e.shiftKey && e.key.toLowerCase() === 'l') return 'Ctrl+L';
      return e.key;
    }
    return (e.type === 'keypress' ? CHAR_KEYS[e.charCode] : CODE_KEYS[e.keyCode]) ?? '';
  }

  function dropdownKey(key) {
    const keys = {
      ArrowDown: () => highlight(dropdown.sel + 1),
      ArrowUp: () => highlight(dropdown.sel - 1),
      Escape: closeDropdown,
      Enter: () => choose(dropdown.items[dropdown.sel]),
    };
    if (!keys[key]) return false;
    keys[key]();
    return true;
  }

  // Ctrl+L: open the keychain on the focused field, or the first login field.
  function openKeychain() {
    const active = document.activeElement;
    const target = kindOf(active) ? active : firstLoginField();
    if (!target) return;
    if (target === active) query(target, kindOf(target));
    else target.focus();
  }

  // Handled keys are cancelled on keydown, so desktop Chrome never fires the
  // matching keypress; under carbonyl only the keypress carries them.
  function onKey(e) {
    if (!e.isTrusted) return;
    const key = keyName(e);
    if (!key) return;
    const handled =
      (dropdown && document.activeElement === dropdown.field && dropdownKey(key)) ||
      (key === 'Ctrl+L' && (openKeychain(), true));
    if (handled) {
      e.preventDefault();
      e.stopImmediatePropagation();
      return;
    }
    if (key === 'Enter' && kindOf(e.target)) captureSubmit(e.target);
  }
  addEventListener('keydown', onKey, true);
  addEventListener('keypress', onKey, true);
  addEventListener('submit', (e) => captureSubmit(e.target), true);
  addEventListener(
    'click',
    (e) => {
      if (!e.isTrusted || !(e.target instanceof Element)) return;
      const b = e.target.closest('button, input[type=submit], input[type=button], [role=button]');
      if (!b) return;
      const form = b.form ?? b.closest('form');
      const sc = form ?? document;
      if (passwordsIn(sc).some((p) => p.value) || usernameIn(sc)?.value) captureSubmit(form ?? b);
    },
    true,
  );

  globalThis.__kc = api;
  post({ t: 'hello', host: location.hostname, top: window === top });
  // A login field may already hold focus (autofocus) before we were injected.
  const active = document.activeElement;
  const kind = kindOf(active);
  if (kind) query(active, kind);
})();
