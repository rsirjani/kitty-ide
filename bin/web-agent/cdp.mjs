// Minimal Chrome DevTools Protocol client over Node's built-in WebSocket.
//
// One browser-level connection, flattened sessions: every attached target
// (page, OOPIF, worker) is addressed by its sessionId on the same socket.

const CALL_TIMEOUT_MS = 10_000;

export class Cdp {
  #ws;
  #nextId = 0;
  #pending = new Map();
  #listeners = new Map();

  static async connect(wsUrl) {
    const cdp = new Cdp();
    cdp.#ws = new WebSocket(wsUrl);
    await new Promise((resolve, reject) => {
      cdp.#ws.addEventListener('open', resolve, { once: true });
      cdp.#ws.addEventListener('error', () => reject(new Error(`CDP connect failed: ${wsUrl}`)), { once: true });
    });
    cdp.#ws.addEventListener('message', (e) => cdp.#dispatch(JSON.parse(e.data)));
    cdp.#ws.addEventListener('close', () => {
      for (const p of cdp.#pending.values()) {
        clearTimeout(p.timer);
        p.reject(new Error('CDP connection closed'));
      }
      cdp.#pending.clear();
      cdp.#emit('close', {});
    });
    return cdp;
  }

  // timeoutMs: 0 waits forever (long-polls into the page).
  send(method, params = {}, sessionId, { timeoutMs = CALL_TIMEOUT_MS } = {}) {
    const id = ++this.#nextId;
    const msg = { id, method, params, ...(sessionId ? { sessionId } : {}) };
    return new Promise((resolve, reject) => {
      const timer =
        timeoutMs > 0 &&
        setTimeout(() => {
          this.#pending.delete(id);
          reject(new Error(`CDP ${method} timed out`));
        }, timeoutMs);
      this.#pending.set(id, { resolve, reject, timer, method });
      this.#ws.send(JSON.stringify(msg));
    });
  }

  // handler(params, sessionId)
  on(event, handler) {
    const list = this.#listeners.get(event) ?? [];
    this.#listeners.set(event, [...list, handler]);
  }

  close() {
    this.#ws.close();
  }

  #dispatch(msg) {
    if (msg.id !== undefined) {
      const p = this.#pending.get(msg.id);
      if (!p) return;
      this.#pending.delete(msg.id);
      clearTimeout(p.timer);
      if (msg.error) p.reject(new Error(`CDP ${p.method}: ${msg.error.message}`));
      else p.resolve(msg.result);
      return;
    }
    this.#emit(msg.method, msg.params ?? {}, msg.sessionId);
  }

  #emit(event, params, sessionId) {
    for (const handler of this.#listeners.get(event) ?? []) {
      try {
        handler(params, sessionId);
      } catch (err) {
        console.error(`[web-agent] ${event} handler failed:`, err);
      }
    }
  }
}

// Resolve the browser-level WebSocket URL, retrying while Chromium starts up.
export async function browserWsUrl(port, { attempts = 100, delayMs = 100 } = {}) {
  for (let i = 0; i < attempts; i++) {
    try {
      const res = await fetch(`http://127.0.0.1:${port}/json/version`);
      const { webSocketDebuggerUrl } = await res.json();
      if (webSocketDebuggerUrl) return webSocketDebuggerUrl;
    } catch {
      // not up yet
    }
    await new Promise((r) => setTimeout(r, delayMs));
  }
  throw new Error(`no DevTools endpoint on port ${port}`);
}
