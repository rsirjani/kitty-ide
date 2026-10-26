// ide-web-cred: manage the in-terminal browser's keychain and 2FA verifier.
//
//   ide-web-cred add  <host> [username]          store a login (prompts for password)
//   ide-web-cred rm   <host> [username]          remove one login (or all for host)
//   ide-web-cred ls                               list stored logins and 2FA entries
//   ide-web-cred totp add <host> [username]      store a 2FA secret or otpauth:// URI (prompted)
//   ide-web-cred totp rm  <host> [username]      remove a 2FA secret
//   ide-web-cred code <host> [username]          print the current 2FA code
//   ide-web-cred never rm <host>                 allow save prompts again for a host
//
// Secrets live in the system keyring (secret-tool). The browser offers them
// on login / 2FA fields of the matching site (and its subdomains) only.

import readline from 'node:readline';
import { Keychain, normalizeHost } from './keychain.mjs';
import { parseTotp } from './totp.mjs';

const USAGE = `usage:
  ide-web-cred add  <host> [username]
  ide-web-cred rm   <host> [username]
  ide-web-cred ls
  ide-web-cred totp add|rm <host> [username]
  ide-web-cred code <host> [username]
  ide-web-cred never rm <host>`;

function ask(question, { hidden = false } = {}) {
  return new Promise((resolve) => {
    const rl = readline.createInterface({ input: process.stdin, output: process.stderr, terminal: process.stdin.isTTY });
    if (hidden && process.stdin.isTTY) {
      rl._writeToOutput = (s) => {
        if (s.includes(question)) rl.output.write(s);
      };
    }
    rl.question(question, (answer) => {
      rl.close();
      if (hidden && process.stdin.isTTY) process.stderr.write('\n');
      resolve(answer);
    });
  });
}

function need(value) {
  if (!value) throw new Error(USAGE);
  return value;
}

async function main(argv) {
  const kc = new Keychain();
  const [cmd, a, b, c] = argv;

  switch (cmd) {
    case 'add': {
      const host = normalizeHost(need(a));
      const user = b ?? (await ask(`username for ${host}: `));
      const pw = await ask(`password for ${user || host}: `, { hidden: true });
      if (!pw) throw new Error('empty password, nothing stored');
      await kc.saveLogin(host, user, pw);
      return `stored login ${user || '(no username)'} @ ${host}`;
    }
    case 'rm':
      await kc.removeLogin(need(a), b);
      return `removed ${b ?? 'all logins'} @ ${a}`;
    case 'ls': {
      const logins = await kc.listLogins();
      const totp = await kc.listTotp();
      const rows = [
        ...logins.map((l) => `login  ${l.domain}  ${l.username || '(no username)'}`),
        ...totp.map((t) => `2fa    ${t.domain}  ${t.username || '(default)'}`),
      ];
      return rows.sort().join('\n') || '(keychain empty)';
    }
    case 'totp': {
      const host = normalizeHost(need(b));
      if (a === 'rm') {
        await kc.removeTotp(host, c);
        return `removed 2FA ${c ?? '(all)'} @ ${host}`;
      }
      if (a !== 'add') throw new Error(USAGE);
      const spec = parseTotp(await ask('2FA secret or otpauth:// URI: ', { hidden: true }));
      const user = c ?? spec.account;
      await kc.saveTotp(host, user, spec);
      return `stored 2FA ${user || '(default)'} @ ${host}`;
    }
    case 'code': {
      const res = await kc.code(need(a), b);
      if (!res) throw new Error(`no 2FA secret for ${a}`);
      return `${res.code}  (${res.secondsLeft}s left)`;
    }
    case 'never': {
      if (a !== 'rm') throw new Error(USAGE);
      kc.setNeverSave(need(b), false);
      return `save prompts re-enabled for ${b}`;
    }
    default:
      throw new Error(USAGE);
  }
}

main(process.argv.slice(2)).then(
  (out) => out && console.log(out),
  (err) => {
    console.error(err.message);
    process.exit(2);
  },
);
