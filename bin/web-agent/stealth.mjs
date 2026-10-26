// Make carbonyl look like the desktop Chrome it is underneath.
//
// Carbonyl is headless Chromium, and out of the box it says so in a dozen ways
// bot walls check for: a "(Carbonyl)" user agent, navigator.webdriver, no
// client hints, no window.chrome, no plugins, a SwiftShader GPU. Everything
// here is applied per target over CDP before the target runs any script.
//
// We claim the engine's REAL major version. Claiming a newer Chrome would
// clear "outdated browser" banners but contradict the TLS/HTTP2 fingerprint
// and the JS feature set — exactly the mismatch serious bot scoring keys on.
// IDE_WEB_CHROME_VERSION overrides it for sites that hard-block old versions.

import fs from 'node:fs';
import path from 'node:path';

const HERE = path.dirname(new URL(import.meta.url).pathname);
const STEALTH_BUNDLE = path.join(HERE, 'vendor', 'stealth.min.js');

const DEFAULT_FULL_VERSION = '111.0.5563.64';
const GPU_VENDOR = 'Google Inc. (Intel)';
const GPU_RENDERER = 'ANGLE (Intel, Mesa Intel(R) UHD Graphics 620 (KBL GT2), OpenGL 4.6)';
const CPU_CORES = 8;

// Chrome's GREASE brand for each milestone is deterministic; this is the one
// real Chrome 111 sends. Other versions get the generic modern form.
function greaseBrand(major) {
  return major === 111 ? { brand: 'Not(A:Brand', version: '8' } : { brand: 'Not?A_Brand', version: '99' };
}

export function makeProfile({ fullVersion = process.env.IDE_WEB_CHROME_VERSION || DEFAULT_FULL_VERSION, lang = 'en-US' } = {}) {
  const major = Number.parseInt(fullVersion, 10);
  const grease = greaseBrand(major);
  const brands = [
    { brand: 'Google Chrome', version: String(major) },
    grease,
    { brand: 'Chromium', version: String(major) },
  ];
  const fullVersionList = brands.map((b) => ({
    brand: b.brand,
    version: b === grease ? `${grease.version}.0.0.0` : fullVersion,
  }));
  const userAgent = `Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/${major}.0.0.0 Safari/537.36`;
  const baseLang = lang.split('-')[0];
  const acceptLanguage = baseLang === lang ? lang : `${lang},${baseLang}`;

  return {
    userAgent,
    lang,
    acceptLanguage,
    metadata: {
      brands,
      fullVersionList,
      fullVersion,
      platform: 'Linux',
      platformVersion: '',
      architecture: 'x86',
      model: '',
      mobile: false,
      bitness: '64',
      wow64: false,
    },
    // This Chromium build never emits the low-entropy client-hint headers, so
    // we add them the way Chrome formats them.
    headers: {
      'sec-ch-ua': brands.map((b) => `"${b.brand}";v="${b.version}"`).join(', '),
      'sec-ch-ua-mobile': '?0',
      'sec-ch-ua-platform': '"Linux"',
    },
  };
}

// Command-line flags that must be set at launch (they cover workers and the
// very first request, which CDP can't reach in time).
export function launchFlags(profile) {
  return [
    '--disable-blink-features=AutomationControlled',
    `--lang=${profile.lang}`,
    `--user-agent=${profile.userAgent}`,
  ];
}

let cachedScript;

export function stealthScript() {
  if (cachedScript) return cachedScript;
  cachedScript = fs
    .readFileSync(STEALTH_BUNDLE, 'utf8')
    .replace("'Intel Inc.'", `'${GPU_VENDOR}'`)
    .replace("'Intel Iris OpenGL Engine'", `'${GPU_RENDERER}'`)
    .replace('hardwareConcurrency:4', `hardwareConcurrency:${CPU_CORES}`);
  return cachedScript;
}

const PAGE_TYPES = new Set(['page', 'iframe']);
const WORKER_TYPES = new Set(['worker', 'shared_worker', 'service_worker']);

// Apply the disguise to a freshly attached (still paused) target.
export async function disguiseTarget(cdp, sessionId, targetType, profile) {
  const send = (method, params) => cdp.send(method, params, sessionId);
  const ua = {
    userAgent: profile.userAgent,
    acceptLanguage: profile.acceptLanguage,
    platform: 'Linux x86_64',
    userAgentMetadata: profile.metadata,
  };

  if (WORKER_TYPES.has(targetType)) {
    await send('Network.setUserAgentOverride', ua).catch(() => {});
    return;
  }
  if (!PAGE_TYPES.has(targetType)) return;

  await send('Page.enable');
  await send('Network.enable', { maxTotalBufferSize: 0, maxResourceBufferSize: 0 });
  await send('Network.setUserAgentOverride', ua);
  await send('Network.setExtraHTTPHeaders', { headers: profile.headers });
  await send('Page.addScriptToEvaluateOnNewDocument', { source: stealthScript() });
}
