#!/usr/bin/env python3
"""ide-wb server — file-backed store for the shared user<->Claude whiteboard.

Serves the Excalidraw wrapper page (wb/index.html in the repo) plus a tiny
JSON API over the state dir (~/.local/state/ide-wb):

  GET  /            wrapper page
  GET  /state       {"scene": <mtime_ns>, "mmd": <mtime_ns>}  (version poll)
  GET  /scene       board.excalidraw (full scene JSON; empty scene if absent)
  POST /scene       save scene JSON            -> {"ver": <new mtime_ns>}
  POST /png         save exported board.png    -> {"ver": <new mtime_ns>}
  GET  /png         board.png
  GET  /mmd         board.mmd as text/plain (Claude's mermaid layer)
  POST /mmd-status  {"ok": bool, "error": str} -> writes/clears board.mmd.err
  GET  /health      "ok"

All merging/rendering happens in the page; this process only stores files.
Binds 0.0.0.0 so the Virtual Desktop can reach it at 172.17.0.1.
"""
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PORT = int(os.environ.get("IDE_WB_PORT", "7223"))
STATE = Path(os.environ.get("IDE_WB_STATE", Path.home() / ".local/state/ide-wb"))
SCENE = STATE / "board.excalidraw"
PNG = STATE / "board.png"
MMD = STATE / "board.mmd"
MMD_ERR = STATE / "board.mmd.err"
INDEX = Path(__file__).resolve().parent.parent / "wb" / "index.html"

EMPTY_SCENE = {"type": "excalidraw", "version": 2, "elements": [], "appState": {}, "files": {}}


def mtime_ns(p: Path) -> int:
    try:
        return p.stat().st_mtime_ns
    except FileNotFoundError:
        return 0


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # quiet
        pass

    def _send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _body(self) -> bytes:
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n)

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/":
            try:
                self._send(200, INDEX.read_bytes(), "text/html; charset=utf-8")
            except FileNotFoundError:
                self._send(500, "wb/index.html missing in repo", "text/plain")
        elif path == "/state":
            self._send(200, json.dumps({"scene": mtime_ns(SCENE), "mmd": mtime_ns(MMD)}))
        elif path == "/scene":
            if SCENE.exists():
                self._send(200, SCENE.read_bytes())
            else:
                self._send(200, json.dumps(EMPTY_SCENE))
        elif path == "/mmd":
            text = MMD.read_text() if MMD.exists() else ""
            self._send(200, text, "text/plain; charset=utf-8")
        elif path == "/png":
            if PNG.exists():
                self._send(200, PNG.read_bytes(), "image/png")
            else:
                self._send(404, "no png yet", "text/plain")
        elif path == "/health":
            self._send(200, "ok", "text/plain")
        else:
            self._send(404, "not found", "text/plain")

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        if path == "/scene":
            raw = self._body()
            try:
                json.loads(raw)
            except ValueError:
                self._send(400, json.dumps({"error": "invalid json"}))
                return
            SCENE.write_bytes(raw)
            self._send(200, json.dumps({"ver": mtime_ns(SCENE)}))
        elif path == "/png":
            PNG.write_bytes(self._body())
            self._send(200, json.dumps({"ver": mtime_ns(PNG)}))
        elif path == "/mmd-status":
            try:
                st = json.loads(self._body())
            except ValueError:
                self._send(400, json.dumps({"error": "invalid json"}))
                return
            if st.get("ok"):
                MMD_ERR.unlink(missing_ok=True)
            else:
                MMD_ERR.write_text(str(st.get("error", "unknown mermaid error")) + "\n")
            self._send(200, json.dumps({"ok": True}))
        else:
            self._send(404, "not found", "text/plain")


def main():
    STATE.mkdir(parents=True, exist_ok=True)
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"ide-wb server on :{PORT}, state {STATE}", file=sys.stderr)
    srv.serve_forever()


if __name__ == "__main__":
    main()
