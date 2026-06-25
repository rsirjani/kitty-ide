#!/usr/bin/env python3
"""Stateless VNC operations for the kitty-ide Virtual Desktop.

This is the *system-agnostic* half of the VD: it speaks plain VNC (RFB), so it
drives a Docker X11 desktop, a QEMU VM, or a remote box identically. The shell
`ide-vd` CLI resolves an endpoint via the active env's provider and shells out
here for one-off operations; `ide-vd-view` imports the helpers below for the
live feed.

Usage:
  _ide_vd_vnc.py --host H --port P [--password PW] <cmd> [args]

  shot <outpath>            screenshot -> PNG
  geometry                  print "WxH" of the framebuffer
  move  X Y
  click X Y [left|right|middle]
  scroll up|down [n]
  type  "text"
  key   <combo>             e.g.  ctrl+s   alt+Tab   Return
"""
import argparse
import asyncio
import sys

import asyncvnc

# asyncvnc button indices
BUTTONS = {"left": 0, "middle": 1, "right": 2}

# friendly names -> X keysym names (asyncvnc/keysymdef vocabulary)
KEY_ALIASES = {
    "ctrl": "Control_L", "control": "Control_L",
    "alt": "Alt_L", "meta": "Alt_L", "option": "Alt_L",
    "shift": "Shift_L",
    "super": "Super_L", "win": "Super_L", "cmd": "Super_L",
    "enter": "Return", "return": "Return", "ret": "Return",
    "esc": "Escape", "escape": "Escape",
    "tab": "Tab", "space": "space", "spc": "space",
    "backspace": "BackSpace", "bksp": "BackSpace", "bs": "BackSpace",
    "del": "Delete", "delete": "Delete",
    "up": "Up", "down": "Down", "left": "Left", "right": "Right",
    "home": "Home", "end": "End",
    "pageup": "Page_Up", "pgup": "Page_Up",
    "pagedown": "Page_Down", "pgdn": "Page_Down",
}


def resolve_key(token: str) -> str:
    """Map one token of a key combo to an X keysym name."""
    low = token.lower()
    if low in KEY_ALIASES:
        return KEY_ALIASES[low]
    if len(token) == 2 and low[0] == "f" and token[1].isdigit():
        return "F" + token[1]              # f1..f9
    if len(token) == 3 and low[:1] == "f" and token[1:].isdigit():
        return "F" + token[1:]             # f10..f12
    return token                            # single chars / explicit keysyms


async def _connect(host, port, password):
    return asyncvnc.connect(host, port, password=password or None)


async def run(args) -> int:
    async with await _connect(args.host, args.port, args.password) as client:
        cmd = args.cmd

        if cmd == "shot":
            from PIL import Image
            rgba = await client.screenshot()
            Image.fromarray(rgba, "RGBA").convert("RGB").save(args.rest[0])

        elif cmd == "geometry":
            rgba = await client.screenshot()
            h, w = rgba.shape[0], rgba.shape[1]
            print(f"{w}x{h}")

        elif cmd == "move":
            x, y = int(args.rest[0]), int(args.rest[1])
            client.mouse.move(x, y)
            await client.drain()

        elif cmd == "click":
            x, y = int(args.rest[0]), int(args.rest[1])
            button = BUTTONS[(args.rest[2] if len(args.rest) > 2 else "left").lower()]
            client.mouse.move(x, y)
            await client.drain()
            await asyncio.sleep(0.05)       # let the pointer settle before the press
            client.mouse.click(button)
            await client.drain()

        elif cmd == "scroll":
            direction = args.rest[0].lower()
            n = int(args.rest[1]) if len(args.rest) > 1 else 3
            (client.mouse.scroll_up if direction == "up" else client.mouse.scroll_down)(n)
            await client.drain()

        elif cmd == "type":
            client.keyboard.write(args.rest[0])
            await client.drain()

        elif cmd == "key":
            keys = [resolve_key(t) for t in args.rest[0].replace("-", "+").split("+") if t]
            client.keyboard.press(*keys)
            await client.drain()

        elif cmd == "hold":
            # Press a combo, hold for <secs> (default 1.0), then release — within
            # ONE connection, so the keys stay down (games etc). `key` only taps.
            keys = [resolve_key(t) for t in args.rest[0].replace("-", "+").split("+") if t]
            secs = float(args.rest[1]) if len(args.rest) > 1 else 1.0
            with client.keyboard.hold(*keys):
                await client.drain()
                await asyncio.sleep(secs)
            await client.drain()

        elif cmd in ("keydown", "keyup"):
            # Raw key-down / key-up events. Best-effort: x11vnc releases held keys
            # when a client disconnects, so a keydown in one process won't survive
            # into a separate keyup process — use `hold` for a reliable hold.
            keys = [resolve_key(t) for t in args.rest[0].replace("-", "+").split("+") if t]
            prefix = b"\x04\x01\x00\x00" if cmd == "keydown" else b"\x04\x00\x00\x00"
            for k in keys:
                client.keyboard.writer.write(prefix + asyncvnc.key_codes[k].to_bytes(4, "big"))
            await client.drain()

        else:
            print(f"unknown command: {cmd}", file=sys.stderr)
            return 2

        await asyncio.sleep(0.05)           # flush before the connection closes
    return 0


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--host", required=True)
    p.add_argument("--port", type=int, required=True)
    p.add_argument("--password", default="")
    p.add_argument("cmd")
    p.add_argument("rest", nargs="*")
    args = p.parse_args()
    try:
        return asyncio.run(run(args))
    except Exception as e:                   # endpoint down, auth, etc.
        print(f"vnc error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
