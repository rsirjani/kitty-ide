#!/usr/bin/env python3
"""Record the Virtual Desktop as a series of frames (a poor man's video).

Connects ONCE over VNC and grabs full framebuffers in a loop at a target FPS
for a fixed duration, writing each frame as a PNG. Reusing a single persistent
connection is what makes ~10 FPS feasible — the one-off `ide-vd shot` path pays
a fresh connect/handshake per frame and tops out far slower.

After capture it writes a montage contact sheet (all frames in one grid image)
so the whole clip can be eyeballed in a single look, plus the individual frames.

Usage:
  _ide_vd_rec.py --host H --port P [--password PW] \
      --secs S [--fps F] --outdir DIR [--montage-cols N] [--montage-width W]
"""
import argparse
import asyncio
import sys

import asyncvnc


async def run(args) -> int:
    from PIL import Image

    interval = 1.0 / args.fps
    n_target = max(1, round(args.secs * args.fps))

    frames = []          # (index, PIL.Image, capture_time)
    async with asyncvnc.connect(
        args.host, args.port, password=args.password or None
    ) as client:
        loop = asyncio.get_event_loop()
        t0 = loop.time()
        for i in range(n_target):
            target = t0 + i * interval
            now = loop.time()
            if target > now:
                await asyncio.sleep(target - now)
            rgba = await client.screenshot()
            img = Image.fromarray(rgba, "RGBA").convert("RGB")
            frames.append((i, img, loop.time() - t0))
        elapsed = loop.time() - t0

    # Persist individual frames.
    import os
    os.makedirs(args.outdir, exist_ok=True)
    paths = []
    for i, img, _ in frames:
        p = os.path.join(args.outdir, f"frame_{i:04d}.png")
        img.save(p)
        paths.append(p)

    # Build a montage contact sheet.
    cols = args.montage_cols
    rows = (len(frames) + cols - 1) // cols
    fw, fh = frames[0][1].size
    scale = args.montage_width / (cols * fw)
    tw, th = max(1, int(fw * scale)), max(1, int(fh * scale))
    pad, label_h = 4, 14
    cell_w, cell_h = tw + pad, th + pad + label_h
    sheet = Image.new("RGB", (cols * cell_w + pad, rows * cell_h + pad), (24, 24, 24))
    try:
        from PIL import ImageDraw
        draw = ImageDraw.Draw(sheet)
    except Exception:
        draw = None
    for idx, (i, img, t) in enumerate(frames):
        r, c = divmod(idx, cols)
        x = pad + c * cell_w
        y = pad + r * cell_h
        sheet.paste(img.resize((tw, th)), (x, y + label_h))
        if draw is not None:
            draw.text((x + 2, y + 2), f"#{i}  {t:4.2f}s", fill=(200, 200, 200))
    montage_path = os.path.join(args.outdir, "montage.png")
    sheet.save(montage_path)

    actual_fps = (len(frames) - 1) / elapsed if elapsed > 0 and len(frames) > 1 else float(len(frames))
    print(f"frames:      {len(frames)}")
    print(f"elapsed:     {elapsed:.2f}s")
    print(f"actual_fps:  {actual_fps:.2f}")
    print(f"frame_size:  {fw}x{fh}")
    print(f"outdir:      {args.outdir}")
    print(f"montage:     {montage_path}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--host", required=True)
    p.add_argument("--port", type=int, required=True)
    p.add_argument("--password", default="")
    p.add_argument("--secs", type=float, required=True)
    p.add_argument("--fps", type=float, default=10.0)
    p.add_argument("--outdir", required=True)
    p.add_argument("--montage-cols", type=int, default=5)
    p.add_argument("--montage-width", type=int, default=1100)
    args = p.parse_args()
    try:
        return asyncio.run(run(args))
    except Exception as e:
        print(f"rec error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
