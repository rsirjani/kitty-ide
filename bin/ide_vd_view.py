#!/usr/bin/env python3
"""The Virtual Desktop feed pane.

Runs as the process of the IDE's `vd` pane. It asks `ide-vd _resolve` where the
active environment's VNC endpoint is, connects, and blits the framebuffer into
this pane via the kitty graphics protocol at a fixed frame rate — the same
technique carbonyl/tdf use to stay crisp inside the terminal. When no
environment is up it shows an idle card instead, and it reconnects on its own
when one appears or drops. Input/screenshots for Claude go through the stateless
one-off path in `_ide_vd_vnc.py`, not through here.
"""
import asyncio
import base64
import fcntl
import os
import signal
import struct
import subprocess
import sys
import tempfile
import termios

import asyncvnc
import numpy as np
from asyncvnc import read_int
from PIL import Image, ImageDraw, ImageFont

# Target frame rate for the *human* feed pane. The real ceiling is set by the
# pipeline below (full-framebuffer VNC capture ~13 fps + PNG encode), so this is
# an upper bound, not an artificial throttle — set it high and let the pipeline
# be the limiter. (Claude's own screenshots go through a separate one-off path.)
FPS = float(os.environ.get("IDE_VD_FPS", "30"))
BG = (30, 30, 46)            # Catppuccin Mocha base #1e1e2e
FG = (205, 214, 244)         # text
SUBTLE = (127, 132, 156)     # overlay/dim text
MAUVE = (203, 166, 247)

_FONT_PATHS = [
    "/usr/share/fonts/TTF/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]


def _font(size):
    for p in _FONT_PATHS:
        if os.path.exists(p):
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()


def pane_pixels():
    """Pane size in pixels via TIOCGWINSZ (rows, cols, xpix, ypix)."""
    try:
        buf = fcntl.ioctl(sys.stdout.fileno(), termios.TIOCGWINSZ, b"\0" * 8)
        rows, cols, xpix, ypix = struct.unpack("HHHH", buf)
        if xpix and ypix:
            return xpix, ypix
    except Exception:
        pass
    return 1280, 800


def pane_cells():
    """Pane size in character cells via TIOCGWINSZ (rows, cols)."""
    try:
        buf = fcntl.ioctl(sys.stdout.fileno(), termios.TIOCGWINSZ, b"\0" * 8)
        rows, cols, _xp, _yp = struct.unpack("HHHH", buf)
        if rows and cols:
            return cols, rows
    except Exception:
        pass
    return 80, 24


# ---- idle ASCII scene -------------------------------------------------------
# When nothing is launched the desktop is a blank root window, so instead of
# streaming a (near-empty) framebuffer we draw a cheap animated ASCII scene as
# plain text — no chromium in the container, no image encoding. A peaceful night
# sea: drifting stars, a moon, and rolling waves.

def _is_blank(rgba) -> bool:
    """True if the framebuffer is essentially the bare root colour (no app open).

    Compares against the *known* root colour (BG), not the corner pixel — a
    full-screen app filling the pane with one colour (e.g. a white page) must NOT
    read as blank.
    """
    if rgba is None:
        return True
    small = rgba[::5, ::5, :3].astype(int)
    root = np.array(BG, dtype=int)              # Catppuccin base painted by xsetroot
    nonbg = (np.abs(small - root).sum(axis=2) > 36).mean()
    return nonbg < 0.01         # ~all root colour (allow cursor / a few stray px)


_MONO_PATHS = [
    "/usr/share/fonts/TTF/DejaVuSansMono.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    "/usr/share/fonts/dejavu/DejaVuSansMono.ttf",
]
_mono_cache: dict = {}


def _mono_font(size):
    f = _mono_cache.get(size)
    if f is None:
        for p in _MONO_PATHS:
            if os.path.exists(p):
                f = ImageFont.truetype(p, size); break
        else:
            f = ImageFont.load_default()
        _mono_cache[size] = f
    return f


def ansi256_rgb(i):
    """xterm-256 palette index -> (r,g,b)."""
    if i < 16:
        base = [(0, 0, 0), (128, 0, 0), (0, 128, 0), (128, 128, 0), (0, 0, 128),
                (128, 0, 128), (0, 128, 128), (192, 192, 192), (128, 128, 128),
                (255, 0, 0), (0, 255, 0), (255, 255, 0), (0, 0, 255),
                (255, 0, 255), (0, 255, 255), (255, 255, 255)]
        return base[i]
    if i < 232:
        i -= 16
        r, g, b = i // 36, (i // 6) % 6, i % 6
        return tuple(0 if v == 0 else 55 + 40 * v for v in (r, g, b))
    v = 8 + 10 * (i - 232)
    return (v, v, v)


# brightness ramp (dark -> bright) using block glyphs for smooth gradients
_RAMP = " .:-=+░▒▓█"
# aurora uses block glyphs only (no punctuation) so faint areas read as a smooth
# glow instead of speckle
_ARAMP = " ·░░▒▒▓▓██"
# aurora colour by brightness: deep teal -> green -> pale mint (with a violet base)
_AUR = [23, 29, 30, 36, 42, 48, 84, 121, 159, 195]
_VIOLET = [54, 55, 91, 97]


def _star_field(grid, cols, top, bottom, t, density, bright=False):
    rnd = 99173
    n = int(cols * (bottom - top) * density)
    for _ in range(n):
        rnd = (rnd * 1103515245 + 12345) & 0x7fffffff
        x = rnd % cols
        rnd = (rnd * 1103515245 + 12345) & 0x7fffffff
        y = top + rnd % max(1, bottom - top)
        tw = ((t + x * 7 + y * 13) // 4) % 5
        if tw == 0 and not bright:
            continue
        if bright and tw >= 3:
            grid[y][x] = ("✦", 230)
        else:
            grid[y][x] = (".•*"[min(2, tw)], 252 if tw >= 3 else 245)


def ascii_grid(cols, rows, t):
    """Idle scene: a clearly-readable aurora borealis over snow-capped mountains,
    with a thin reflecting lake. Returns a grid of (char, ansi256_color); all
    motion is a function of t (deterministic, cheap).
    """
    import math
    cols = max(30, cols)
    rows = max(16, rows)
    water_y = int(rows * 0.80)          # thin lake strip below the mountains
    grid = [[(" ", 16) for _ in range(cols)] for _ in range(rows)]
    tf = t * 0.10

    GREEN = [22, 28, 34, 40, 46, 83, 120, 159, 194]     # dark -> pale green
    TEAL = [23, 23, 30, 37, 44, 80, 123, 159, 195]

    # --- starry night sky (upper half) ---
    rnd = 99173
    for _ in range(int(cols * rows * 0.045)):
        rnd = (rnd * 1103515245 + 12345) & 0x7fffffff; x = rnd % cols
        rnd = (rnd * 1103515245 + 12345) & 0x7fffffff; y = rnd % max(1, int(rows * 0.5))
        tw = ((t + x * 5 + y * 11) // 4) % 6
        if tw == 0:
            grid[y][x] = ("✦", 230)
        elif tw <= 2:
            grid[y][x] = (".", 246)

    # --- aurora: soft draping curtains glowing along a wavy arc. A smooth 2D
    #     field (no hard edges, no regular bars) so it reads as diffuse light. ---
    def aurora_field(x, y):
        best_v, best_ramp = 0.0, GREEN
        for cy, amp, f1, f2, ph, up, down, ramp in (
                (0.17, 0.055, 0.050, 0.11, 0.0, 3.0, rows * 0.30, GREEN),
                (0.30, 0.040, 0.075, 0.14, 2.3, 2.5, rows * 0.20, TEAL)):
            yc = (cy + amp * math.sin(x * f1 + tf + ph)
                  + 0.4 * amp * math.sin(x * f2 + tf * 1.4)) * rows
            dy = y - yc
            fall = (1.0 + dy / up) if dy < 0 else (1.0 - dy / down)
            if fall <= 0.0:
                continue
            fall = fall * fall                          # soft shoulders
            # gentle, irregular vertical striations that never fully break up
            stri = (0.64 + 0.22 * math.sin(x * 0.21 + tf * 0.7)
                    + 0.16 * math.sin(x * 0.37 - y * 0.09 + tf))
            v = fall * max(0.30, min(1.0, stri))
            if v > best_v:
                best_v, best_ramp = v, ramp
        return best_v, best_ramp

    for y in range(water_y):
        for x in range(cols):
            v, ramp = aurora_field(x, y)
            if v > 0.14:
                ch = _ARAMP[min(len(_ARAMP) - 1, int(v * (len(_ARAMP) - 1) + 0.5))]
                if ch != " ":
                    grid[y][x] = (ch, ramp[min(len(ramp) - 1, int(v * (len(ramp) - 1) + 0.5))])

    # --- snow-capped triangular mountains ---
    peaks = [(0.10, 0.34), (0.27, 0.22), (0.45, 0.44), (0.64, 0.26), (0.83, 0.37)]
    ridge_y = []
    for x in range(cols):
        h = 2.0
        for pf, hf in peaks:
            h = max(h, hf * rows - abs(x - pf * cols) * 0.55)   # triangle slopes
        ridge_y.append(int(water_y - h))
    for x in range(cols):
        ry = ridge_y[x]
        for y in range(ry, water_y):
            d = y - ry
            if d == 0:
                grid[y][x] = ("▀", 255)                 # snow crest
            elif d == 1:
                grid[y][x] = ("▓", 250)                 # snow
            else:
                grid[y][x] = ("█", 234 if d > 3 else 238)

    # --- thin lake: faint reflected aurora + ripple, then a dark base ---
    for y in range(water_y, rows):
        depth = y - water_y
        for x in range(cols):
            xr = x + int(2 * math.sin(depth * 0.7 + x * 0.3 + tf))
            ch, col = " ", 17
            src = water_y - 1 - depth * 3               # mirror a sky row
            if 0 <= src < water_y and 0 <= xr < cols:
                sch, scol = grid[src][xr]
                if sch in "·░▒▓█":
                    ch, col = ("░" if depth == 0 else "·"), scol
            if ch == " " and (x + t // 3 + depth) % 9 == 0:
                ch, col = "·", 24
            grid[y][x] = (ch, col)

    # --- caption, low and unobtrusive ---
    sub = "nothing running"
    yy = rows - 1
    sx = max(0, (cols - len(sub)) // 2)
    for i, chq in enumerate(sub):
        if sx + i < cols:
            grid[yy][sx + i] = (chq, 244)
    return grid


def render_ascii_image(size, t):
    """Render the idle scene as a dense ASCII image sized to the pane. Character
    density is set by a small monospace font (IDE_VD_ASCII_FONT), independent of
    the terminal's own font — so it's higher-resolution than printing text."""
    pw, ph = size
    fs = max(6, int(os.environ.get("IDE_VD_ASCII_FONT", "11")))
    font = _mono_font(fs)
    cw = max(1, int(round(font.getlength("M"))))
    chh = max(1, int(fs * 1.18))
    cols = max(20, pw // cw)
    rows = max(8, ph // chh)
    grid = ascii_grid(cols, rows, t)

    img = Image.new("RGB", (pw, ph), BG)
    d = ImageDraw.Draw(img)
    for y in range(rows):
        row = grid[y]
        i = 0
        while i < cols:
            col = row[i][1]
            j = i
            while j < cols and row[j][1] == col:
                j += 1
            seg = "".join(c for c, _ in row[i:j])
            if seg.strip():
                d.text((i * cw, y * chh), seg, font=font, fill=ansi256_rgb(col))
            i = j
    return img


# --- generated pixel-art idle loop -------------------------------------------
# A short aurora-over-mountains video (made in OpenArt, bundled under assets/)
# replaces the cheap ASCII scene whenever it can be decoded (PyAV). It plays as
# a seamless ping-pong loop, nearest-neighbour scaled so the pixels stay crisp.
# If the asset or PyAV is missing we silently fall back to render_ascii_image.
_IDLE_ASSET = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "assets", "idle_scene", "aurora_loop.mp4")
_idle_frames = None          # list[PIL.Image] base frames (None=unloaded, []=unavailable)
_idle_seq = None             # ping-pong index order over _idle_frames


def _load_idle_frames():
    """Decode the bundled aurora loop once into RGB frames + a ping-pong order.
    Returns [] (cached) if the asset can't be read so we fall back to ASCII."""
    global _idle_frames, _idle_seq
    if _idle_frames is not None:
        return _idle_frames
    frames = []
    try:
        import av
        with av.open(_IDLE_ASSET) as container:
            for frame in container.decode(video=0):
                frames.append(frame.to_image().convert("RGB"))
    except Exception:
        frames = []
    _idle_frames = frames
    if frames:
        n = len(frames)
        # forward 0..n-1 then back n-2..1 — both endpoints visited once, so the
        # wrap (…->1->0->1->…) is seamless even though frame 0 != frame n-1.
        _idle_seq = list(range(n)) + list(range(n - 2, 0, -1))
    else:
        _idle_seq = []
    return _idle_frames


def _fit_idle(frame, size):
    """Cover-fit a base frame so it FILLS the pane (no bars) — scale to the
    larger ratio, centre-crop the overflow. Nearest-neighbour on upscale keeps
    the pixel-art grid sharp."""
    pw, ph = size
    fw, fh = frame.size
    scale = max(pw / fw, ph / fh)
    tw, th = max(pw, round(fw * scale)), max(ph, round(fh * scale))
    resample = Image.NEAREST if scale >= 1 else Image.LANCZOS
    scaled = frame.resize((tw, th), resample)
    left, top = (tw - pw) // 2, (th - ph) // 2
    return scaled.crop((left, top, left + pw, top + ph))


# Twinkling stars drawn into the 640x360 base frame (so they scale crisply with
# the art). Deterministic positions in the upper sky; brightness pulses with t.
def _gen_stars(n, w, h):
    rnd, stars = 1234567, []
    for _ in range(n):
        rnd = (rnd * 1103515245 + 12345) & 0x7fffffff; x = rnd % w
        rnd = (rnd * 1103515245 + 12345) & 0x7fffffff; y = rnd % int(h * 0.55)
        rnd = (rnd * 1103515245 + 12345) & 0x7fffffff; ph = (rnd % 628) / 100.0
        rnd = (rnd * 1103515245 + 12345) & 0x7fffffff; sp = 0.6 + (rnd % 100) / 100.0
        rnd = (rnd * 1103515245 + 12345) & 0x7fffffff; big = (rnd % 4 == 0)
        stars.append((x, y, ph, sp, big))
    return stars


_STARS = _gen_stars(46, 640, 360)


def _draw_twinkle(frame, tick):
    import math
    px = frame.load()
    w, h = frame.size
    for x, y, ph, sp, big in _STARS:
        b = 0.5 + 0.5 * math.sin(tick * 0.22 * sp + ph)
        if b < 0.12:
            continue
        v = int(170 + 85 * b)
        col = (v, v, min(255, v + 12))
        px[x, y] = col
        if b > 0.55:                                  # glow into neighbours
            d = int(60 + 70 * b)
            dim = (d, d, min(255, d + 10))
            for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
                if 0 <= nx < w and 0 <= ny < h:
                    px[nx, ny] = dim
        if big and b > 0.82:                          # 4-point sparkle when bright
            for nx, ny in ((x - 2, y), (x + 2, y), (x, y - 2), (x, y + 2)):
                if 0 <= nx < w and 0 <= ny < h:
                    px[nx, ny] = (v, v, v)


_IDLE_CAPTION = os.environ.get("IDE_VD_IDLE_CAPTION", "nothing running")


def render_idle_image(size, tick):
    """Idle scene for the pane: the generated pixel-art aurora loop when its
    asset decodes, else the procedural ASCII scene."""
    frames = _load_idle_frames()
    if not frames:
        return render_ascii_image(size, tick)
    base = frames[_idle_seq[tick % len(_idle_seq)]].copy()
    _draw_twinkle(base, tick)
    img = _fit_idle(base, size)
    if _IDLE_CAPTION:
        d = ImageDraw.Draw(img)
        f = _font(max(11, size[1] // 45))
        tw = d.textlength(_IDLE_CAPTION, font=f)
        d.text(((size[0] - tw) / 2, size[1] - f.size - 6), _IDLE_CAPTION,
               font=f, fill=SUBTLE)
    return img


# Where to drop the frame file for kitty to read. A tmpfs (/dev/shm) keeps it
# off disk. We reuse ONE fixed path per process (overwritten each frame) with
# t=f (kitty does NOT delete it) — so the feed can never accumulate files. (An
# earlier version used a fresh mkstemp + t=t each frame, relying on kitty to
# delete them; it didn't keep up and filled the tmpfs, crashing the feed.)
_SHM_DIR = "/dev/shm" if os.path.isdir("/dev/shm") and os.access("/dev/shm", os.W_OK) \
    else tempfile.gettempdir()
_FRAME_FILE = os.path.join(_SHM_DIR, f"ide-vd-frame-{os.getpid()}.rgb")
# IDE_VD_BLIT=inline forces the legacy base64-through-the-pty path (fallback).
_BLIT_INLINE = os.environ.get("IDE_VD_BLIT", "file") == "inline"


def _cleanup_frame_file():
    try:
        os.unlink(_FRAME_FILE)
    except OSError:
        pass


def _sweep_stale_frames():
    """Remove frame files left by panes that were killed (SIGTERM skips atexit).
    Only deletes files whose owning pid is no longer alive, so it never touches a
    live pane's file."""
    import glob
    import re
    for p in glob.glob(os.path.join(_SHM_DIR, "ide-vd-frame-*.rgb")):
        m = re.search(r"-(\d+)\.rgb$", p)
        if m and not os.path.exists(f"/proc/{m.group(1)}"):
            try:
                os.unlink(p)
            except OSError:
                pass


import atexit
atexit.register(_cleanup_frame_file)
_sweep_stale_frames()


def blit(img: Image.Image):
    """Draw a full-pane image via the kitty graphics protocol, pinned top-left.

    Default path writes raw RGB to a tmpfs file and points kitty at it (t=f) —
    the carbonyl trick of keeping frames off the pty.

    CRITICAL: each frame is written to a fresh temp file and **atomically
    renamed** over the path. Writing in place (open+truncate) raced kitty's
    mmap of the previous frame and crashed it with SIGBUS — and since the feed
    is a pane in the kitty window, that took the whole IDE down. rename() never
    truncates the inode kitty already opened, so kitty's read stays valid.
    """
    out = sys.stdout.buffer
    out.write(b"\x1b[H")                        # cursor home — pin frame, no scroll

    if not _BLIT_INLINE:
        w, h = img.size
        raw = img.tobytes()                     # packed RGB, no encode step
        try:                                    # a full tmpfs must not kill the feed
            fd, tmp = tempfile.mkstemp(dir=_SHM_DIR, suffix=".rgb")
            try:
                os.write(fd, raw)
            finally:
                os.close(fd)
            os.rename(tmp, _FRAME_FILE)         # atomic: never truncates a file kitty is reading
        except OSError:
            try:
                os.unlink(tmp)
            except (OSError, NameError):
                pass
            out.flush()
            return
        b64 = base64.standard_b64encode(_FRAME_FILE.encode())
        # a=T transmit+display, f=24 raw RGB, s/v dims, t=f regular file (kitty
        # reads but does NOT delete it — we replace it atomically), i=1 stable id.
        out.write(b"\x1b_Ga=T,f=24,t=f,s=%d,v=%d,i=1,q=2;%s\x1b\\" % (w, h, b64))
        out.flush()
        return

    import io
    raw = io.BytesIO()
    img.save(raw, format="PNG", compress_level=1)
    payload = base64.standard_b64encode(raw.getvalue())
    chunk = 4096
    first = True
    i = 0
    while i < len(payload):
        part = payload[i:i + chunk]
        i += chunk
        last = i >= len(payload)
        m = b"0" if last else b"1"
        if first:
            out.write(b"\x1b_Ga=T,f=100,i=1,q=2,m=" + m + b";" + part + b"\x1b\\")
            first = False
        else:
            out.write(b"\x1b_Gm=" + m + b";" + part + b"\x1b\\")
    out.flush()


def canvas(size):
    return Image.new("RGB", size, BG)


def fit_onto(frame: Image.Image, size):
    """Aspect-fit `frame` onto a pane-sized BG canvas (letterboxed)."""
    pw, ph = size
    fw, fh = frame.size
    scale = min(pw / fw, ph / fh)
    nw, nh = max(1, int(fw * scale)), max(1, int(fh * scale))
    # Skip the resize entirely when the frame already fills the pane (the common
    # 1280x800-pane case) — resizing to the same size is pure wasted CPU.
    if (nw, nh) == (fw, fh) and (pw, ph) == (fw, fh):
        return frame
    resized = frame if (nw, nh) == (fw, fh) else frame.resize((nw, nh), Image.BILINEAR)
    c = canvas(size)
    c.paste(resized, ((pw - nw) // 2, (ph - nh) // 2))
    return c


def card(size, title, lines, accent=MAUVE):
    """A centered text card for idle / connecting states."""
    c = canvas(size)
    d = ImageDraw.Draw(c)
    pw, ph = size
    tf = _font(max(18, ph // 22))
    sf = _font(max(13, ph // 36))

    def center(text, font, y, fill):
        w = d.textlength(text, font=font)
        d.text(((pw - w) / 2, y), text, font=font, fill=fill)

    block_h = tf.size + 14 + len(lines) * (sf.size + 8)
    y = (ph - block_h) / 2
    center(title, tf, y, accent)
    y += tf.size + 14
    for ln in lines:
        center(ln, sf, y, SUBTLE if ln else FG)
        y += sf.size + 8
    return c


def resolve():
    """Ask the CLI for the active endpoint.

    Returns ("up", host, port, password, label) | ("down", label)
            | ("none", "") | None on error.
    """
    try:
        out = subprocess.run(
            ["ide-vd", "_resolve"], capture_output=True, text=True, timeout=10
        ).stdout.strip()
    except Exception:
        return None
    if not out:
        return ("none", "")
    parts = out.split("\t")
    return tuple(parts)


def _render_and_blit(rgb, size):
    """CPU-bound stage (fit-to-pane + blit). Runs in a thread so it overlaps with
    the next frame's capture. `rgb` is a packed HxWx3 uint8 array (own copy)."""
    frame = Image.frombuffer("RGB", (rgb.shape[1], rgb.shape[0]), rgb.tobytes())
    blit(fit_onto(frame, size))


async def _pull_damage(client):
    """Request an *incremental* framebuffer update and patch only the changed
    rectangles into the persistent buffer. Returns the number of changed rects.

    This is the damage-based path: after the first full frame, the server sends
    only the pixels that changed, so a near-static screen costs almost nothing.
    (The library's screenshot() throws the buffer away each call, forcing a full
    transfer every frame — the slow default this replaces.)
    """
    v = client.video
    v.refresh()                                 # incremental, because v.data is set
    await client.drain()
    while True:                                 # skip clipboard/bell, wait for video
        mtype = await read_int(client.reader, 1)
        if mtype == 0:
            break
        if mtype == 2:                          # ServerCutText (clipboard)
            await client.reader.readexactly(3)
            n = await read_int(client.reader, 4)
            await client.reader.readexactly(n)
        elif mtype == 1:                        # SetColourMapEntries — consume its
            await client.reader.readexactly(3)  # body (1 pad + 2 first-colour) so
            ncol = await read_int(client.reader, 2)   # a palette-mode server can't
            await client.reader.readexactly(ncol * 6)  # desync the RFB stream
        # mtype == 3 (bell) has no payload; anything else we just loop on
    await client.reader.readexactly(1)          # padding
    n_rects = await read_int(client.reader, 2)
    for _ in range(n_rects):
        x = await read_int(client.reader, 2); y = await read_int(client.reader, 2)
        w = await read_int(client.reader, 2); h = await read_int(client.reader, 2)
        enc = await read_int(client.reader, 4)
        if enc == 0:                            # Raw
            data = await client.reader.readexactly(h * w * 4)
        elif enc == 6:                          # ZRLE/zlib
            length = await read_int(client.reader, 4)
            data = v.decompress(await client.reader.readexactly(length))
        else:
            raise ValueError(f"unsupported VNC encoding {enc}")
        v.data[y:y + h, x:x + w] = np.ndarray((h, w, 4), 'B', data)
        v.data[y:y + h, x:x + w, v.mode.index('a')] = 255
    return n_rects


IDLE_FPS = 4.0          # the ASCII idle scene is just text; a few fps is plenty
IDLE_VIDEO_FPS = float(os.environ.get("IDE_VD_IDLE_FPS", "12"))  # generated loop

# Set by a SIGWINCH handler (registered in main) whenever the pane is resized —
# e.g. while the user drags a split border. The paint loops wait on this instead
# of a plain sleep, so a resize wakes them *immediately* to re-fit the current
# frame to the new pane size, and the feed tracks the drag smoothly instead of
# showing a stale, wrongly-sized frame until the next damage/tick.
RESIZE_EVT: "asyncio.Event | None" = None


async def _sleep_or_resize(period):
    """Sleep up to `period`, but return as soon as a SIGWINCH (pane resize) fires.

    Many resize events during a fast drag coalesce into one wake (Event is a
    flag), so we re-fit once per wake using the *latest* pane size — no backlog.
    Returns True if woken by a resize, False on timeout."""
    if RESIZE_EVT is None:
        await asyncio.sleep(period)
        return False
    try:
        await asyncio.wait_for(RESIZE_EVT.wait(), period)
    except asyncio.TimeoutError:
        return False
    RESIZE_EVT.clear()                  # clear *after* the wake so we never busy-spin
    return True


async def stream(host, port, password, size_ref):
    """Hold a connection and either show the cheap ASCII idle scene (when nothing
    is launched — a blank root window) or stream the live feed (when an app is
    up).

    Wins: (1) when idle we draw plain ANSI text, never touching chromium or image
    encoding, and the desktop runs no backdrop, so it costs ~nothing; (2) when
    live, damage-based capture pulls only changed pixels (see _pull_damage) and
    the render+blit runs pipelined in a worker thread.
    """
    loop = asyncio.get_event_loop()
    async with asyncvnc.connect(host, int(port), password=password or None) as client:
        await client.screenshot()               # seed the persistent buffer (one full frame)
        period = 1.0 / max(0.5, FPS)
        idle_period = 1.0 / IDLE_FPS

        # Capture and paint are decoupled. `_pull_damage` blocks until the VNC
        # server has changes, so on a static screen it parks indefinitely — if
        # the paint loop awaited it directly (as it used to), nothing would
        # re-fit while the pane was being resized. Instead a background task owns
        # the VNC reads and just keeps `state['rgb']` current; the paint loop
        # repaints on its own cadence *and* whenever the pane size changes.
        state = {
            "blank": _is_blank(client.video.data),
            "rgb": None,
            "seq": 0,                           # bumps on every new captured frame
            "alive": True,
        }
        if not state["blank"]:                  # seed rgb so a static screen paints at once
            state["rgb"] = np.ascontiguousarray(client.video.as_rgba()[:, :, :3])

        async def capture():
            while state["alive"]:
                try:
                    await _pull_damage(client)
                except Exception:
                    state["alive"] = False      # let the paint loop reconnect
                    return
                state["blank"] = _is_blank(client.video.data)
                if not state["blank"]:
                    state["rgb"] = np.ascontiguousarray(client.video.as_rgba()[:, :, :3])
                state["seq"] += 1

        cap = asyncio.create_task(capture())
        pending = None                          # one render/blit in flight at a time
        tick = 0
        last_size = None
        last_seq = -1
        last_resolve = loop.time()
        try:
            while True:
                if not state["alive"]:          # VNC dropped inside capture
                    raise ConnectionError("vnc capture ended")
                size = pane_pixels()

                if state["blank"] or state["rgb"] is None:
                    if pending is not None:     # don't let a live blit race the idle one
                        await pending
                        pending = None
                    blit(render_ascii_image(size, tick))    # cheap ASCII, a few fps
                    tick += 1
                    last_size = size
                    await _sleep_or_resize(idle_period)
                else:
                    # Repaint on a new captured frame OR a pane-size change (a
                    # resize-driven re-fit, even with no VNC damage at all).
                    if size != last_size or state["seq"] != last_seq:
                        if pending is not None:
                            await pending
                        pending = asyncio.create_task(
                            asyncio.to_thread(_render_and_blit, state["rgb"], size))
                        last_size = size
                        last_seq = state["seq"]
                    await _sleep_or_resize(period)

                if loop.time() - last_resolve >= 1.0:   # catch env switches ~1/s
                    last_resolve = loop.time()
                    r = resolve()
                    if not r or r[0] != "up" or (r[1], r[2]) != (host, str(port)):
                        return
        finally:
            state["alive"] = False
            cap.cancel()
            try:
                await cap
            except (asyncio.CancelledError, Exception):
                pass
            if pending is not None:
                try:
                    await pending
                except Exception:
                    pass


async def animate_idle(still_matches):
    """Animate the aurora placeholder (cheap local ASCII, no desktop running)
    until still_matches(resolve()) goes False — i.e. the env changes state."""
    fps = IDLE_VIDEO_FPS if _load_idle_frames() else IDLE_FPS
    tick = 0
    period = 1.0 / fps
    while True:
        blit(render_idle_image(pane_pixels(), tick))
        tick += 1
        await _sleep_or_resize(period)              # wake at once on a pane resize
        if tick % max(1, int(fps)) == 0:            # re-check ~once a second
            if not still_matches(resolve()):
                return


async def main():
    sys.stdout.write("\x1b[?25l")             # hide cursor
    sys.stdout.flush()
    global RESIZE_EVT
    RESIZE_EVT = asyncio.Event()
    try:                                      # wake the paint loop the instant the pane resizes
        asyncio.get_running_loop().add_signal_handler(signal.SIGWINCH, RESIZE_EVT.set)
    except (NotImplementedError, RuntimeError, ValueError):
        RESIZE_EVT = None                     # no signal support: fall back to timed repaint
    last_state = None
    try:
        while True:
            r = resolve()
            size = pane_pixels()
            if r and r[0] == "up":
                _, host, port, password, label = (list(r) + ["", "", "", ""])[:5]
                if last_state != ("up", host, port):
                    blit(card(size, "Virtual Desktop",
                              ["", f"connecting to {label or host}…"]))
                    last_state = ("up", host, port)
                try:
                    await stream(host, port, password, size)
                except Exception:
                    blit(card(size, "Virtual Desktop",
                              ["", f"reconnecting to {label or host}…"]))
                    await asyncio.sleep(1.0)
            elif r and r[0] == "down":
                # Desktop isn't running (and need not be): show the aurora idle
                # scene in place of a "stopped" card. `ide-vd up` starts it.
                last_state = "down"
                await animate_idle(lambda rr: bool(rr) and rr[0] == "down")
            else:
                last_state = "none"
                await animate_idle(lambda rr: (not rr) or rr[0] == "none")
    except KeyboardInterrupt:
        pass
    finally:
        sys.stdout.write("\x1b[?25h")
        sys.stdout.flush()


if __name__ == "__main__":
    asyncio.run(main())
