// Target plumbing: attach to every tab/frame/worker, disguise it before it
// runs, inject the keychain UI into each frame, and pump its messages.

import fs from 'node:fs';
import path from 'node:path';
import { disguiseTarget } from './stealth.mjs';

const HERE = path.dirname(new URL(import.meta.url).pathname);
const PAGE_UI = fs.readFileSync(path.join(HERE, 'page-ui.js'), 'utf8');
const UI_WORLD = 'kitty-ide-keychain';
const FRAME_TYPES = new Set(['page', 'iframe']);
const DEBUG = Boolean(process.env.IDE_WEB_DEBUG);
const AUTO_ATTACH = { autoAttach: true, waitForDebuggerOnStart: true, flatten: true };

export class WebAgent {
  #cdp;
  #profile;
  #onMessage;
  #initialUrl;
  #tabOf = new Map(); // sessionId -> root (tab) sessionId
  #contexts = new Map(); // `${sessionId}/${frameId}` -> FrameContext

  constructor(cdp, { profile, initialUrl, onMessage, log }) {
    this.#cdp = cdp;
    this.#profile = profile;
    this.#initialUrl = initialUrl;
    this.#onMessage = onMessage;
    this.log = log;
  }

  async start() {
    this.#cdp.on('Target.attachedToTarget', (p, parent) => this.#attached(p, parent));
    this.#cdp.on('Target.detachedFromTarget', ({ sessionId }) => this.#detached(sessionId));
    this.#cdp.on('Page.lifecycleEvent', (p, sessionId) => {
      if (p.name === 'DOMContentLoaded') this.#injectUi(sessionId, p.frameId);
    });
    await this.#cdp.send('Target.setAutoAttach', AUTO_ATTACH);
  }

  tabOf(sessionId) {
    return this.#tabOf.get(sessionId) ?? sessionId;
  }

  // The live top-frame context of a tab, if its UI is up.
  topContext(tabSession) {
    for (const ctx of this.#contexts.values()) {
      if (ctx.top && this.tabOf(ctx.sessionId) === tabSession) return ctx;
    }
    return null;
  }

  async #attached({ sessionId, targetInfo, waitingForDebugger }, parentSession) {
    const { type } = targetInfo;
    this.#tabOf.set(sessionId, parentSession ? this.tabOf(parentSession) : sessionId);
    try {
      await disguiseTarget(this.#cdp, sessionId, type, this.#profile);
      if (FRAME_TYPES.has(type)) {
        await this.#cdp.send('Page.setLifecycleEventsEnabled', { enabled: true }, sessionId);
      }
      // OOPIFs and workers of this target attach (paused) the same way.
      await this.#cdp.send('Target.setAutoAttach', AUTO_ATTACH, sessionId).catch(() => {});
    } catch (err) {
      this.log(`disguise ${type} ${targetInfo.url} failed: ${err.message}`);
    }
    if (waitingForDebugger) {
      await this.#cdp.send('Runtime.runIfWaitingForDebugger', {}, sessionId).catch(() => {});
    }
    if (type === 'page' && this.#initialUrl) {
      const url = this.#initialUrl;
      this.#initialUrl = null;
      await this.#cdp.send('Page.navigate', { url }, sessionId).catch((err) => this.log(`navigate failed: ${err.message}`));
    }
  }

  #detached(sessionId) {
    this.#tabOf.delete(sessionId);
    for (const [key, ctx] of this.#contexts) {
      if (ctx.sessionId === sessionId) this.#contexts.delete(key);
    }
  }

  async #injectUi(sessionId, frameId) {
    const key = `${sessionId}/${frameId}`;
    try {
      const { executionContextId } = await this.#cdp.send('Page.createIsolatedWorld', { frameId, worldName: UI_WORLD }, sessionId);
      const ctx = new FrameContext(this.#cdp, sessionId, frameId, executionContextId);
      this.#contexts.set(key, ctx);
      await ctx.eval(PAGE_UI);
      this.#pump(key, ctx);
    } catch (err) {
      this.log(`keychain UI injection failed: ${err.message}`);
    }
  }

  // Long-poll the frame's outbox until its document goes away.
  async #pump(key, ctx) {
    for (;;) {
      let text;
      try {
        text = await ctx.eval('__kc.next()', { awaitPromise: true, timeoutMs: 0 });
      } catch {
        break; // context destroyed (navigation, frame removed, tab closed)
      }
      if (typeof text !== 'string') break;
      const msg = JSON.parse(text);
      if (DEBUG) this.log(`ui ${msg.t} from ${ctx.host || '?'}${ctx.top ? ' (top)' : ''}`);
      if (msg.t === 'hello') {
        ctx.host = msg.host;
        ctx.top = msg.top;
      }
      this.#onMessage(ctx, msg).catch((err) => this.log(`${msg.t} failed: ${err.message}`));
    }
    if (this.#contexts.get(key) === ctx) this.#contexts.delete(key);
  }
}

// One frame's isolated world: where the keychain UI lives.
export class FrameContext {
  host = '';
  top = false;

  constructor(cdp, sessionId, frameId, contextId) {
    this.cdp = cdp;
    this.sessionId = sessionId;
    this.frameId = frameId;
    this.contextId = contextId;
  }

  async eval(expression, { awaitPromise = false, timeoutMs } = {}) {
    const res = await this.cdp.send(
      'Runtime.evaluate',
      { expression, contextId: this.contextId, awaitPromise, returnByValue: true },
      this.sessionId,
      { timeoutMs },
    );
    if (res.exceptionDetails) throw new Error(res.exceptionDetails.exception?.description ?? 'evaluation failed');
    return res.result.value;
  }

  // Call a UI method with JSON-safe arguments.
  call(method, ...args) {
    return this.eval(`__kc.${method}(${args.map((a) => JSON.stringify(a)).join(',')})`);
  }

  // Type into whatever the UI just focused, as trusted user input.
  type(text) {
    return this.cdp.send('Input.insertText', { text }, this.sessionId);
  }
}
