// ide-web-agent: the sidecar that turns carbonyl into a full browser.
//
//   ide-web-agent --port <devtools port> [--url <first page>] [--flags]
//
// --flags prints the launch flags carbonyl needs (one per line) and exits.
// Otherwise it connects to carbonyl's DevTools endpoint, disguises every
// target, runs the keychain/verifier, opens --url, and exits with the browser.

import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { parseArgs } from 'node:util';
import { WebAgent } from './agent.mjs';
import { createAutofill } from './autofill.mjs';
import { browserWsUrl, Cdp } from './cdp.mjs';
import { Keychain } from './keychain.mjs';
import { launchFlags, makeProfile } from './stealth.mjs';

const LOG_DIR = path.join(os.homedir(), '.cache', 'ide-web');

function systemLang() {
  const raw = process.env.LC_ALL || process.env.LC_MESSAGES || process.env.LANG || '';
  const m = raw.match(/^([a-z]{2,3})(?:_([A-Z]{2}))?/);
  return m ? (m[2] ? `${m[1]}-${m[2]}` : m[1]) : 'en-US';
}

function openLog(port) {
  fs.mkdirSync(LOG_DIR, { recursive: true });
  const file = path.join(LOG_DIR, `agent-${port}.log`);
  const fd = fs.openSync(file, 'w', 0o600);
  return (line) => fs.writeSync(fd, `${new Date().toISOString()} ${line}\n`);
}

async function main() {
  const { values } = parseArgs({
    options: { port: { type: 'string' }, url: { type: 'string' }, flags: { type: 'boolean' } },
  });
  const profile = makeProfile({ lang: systemLang() });

  if (values.flags) {
    process.stdout.write(`${launchFlags(profile).join('\n')}\n`);
    return;
  }
  const port = Number(values.port);
  if (!Number.isInteger(port) || port <= 0 || port > 65535) throw new Error('--port <devtools port> is required');

  const log = openLog(port);
  process.on('uncaughtException', (err) => log(`uncaught: ${err.stack}`));
  process.on('unhandledRejection', (err) => log(`unhandled: ${err?.stack ?? err}`));

  const cdp = await Cdp.connect(await browserWsUrl(port));
  cdp.on('close', () => process.exit(0));

  const keychain = new Keychain();
  let autofill;
  const agent = new WebAgent(cdp, {
    profile,
    initialUrl: values.url,
    log,
    onMessage: (ctx, msg) => autofill(ctx, msg),
  });
  autofill = createAutofill({ keychain, agent, log });
  await agent.start();
  log(`attached on port ${port} as ${profile.userAgent}`);
}

main().catch((err) => {
  process.stderr.write(`ide-web-agent: ${err.message}\n`);
  process.exit(1);
});
