#!/usr/bin/env python3
"""Turn captured Virtual Desktop audio into Claude-readable artifacts.

Claude can't ingest audio directly, so this renders a wav two ways:
  • speech → TEXT   via faster-whisper (transcript.txt)
  • any sound → IMAGE via a time×frequency spectrogram (spectrogram.png) — time
    runs along X, so a single image already encodes the whole sequence (the audio
    analogue of `_ide_vd_rec.py`'s frame montage). A labelled frequency axis +
    the printed peak frequency let Claude reason about tones/effects.

Usage:
  _ide_vd_hear.py --wav FILE --outdir DIR [--model base.en]
"""
import argparse
import os
import sys
import wave

import numpy as np
from PIL import Image, ImageDraw, ImageFont

_FONT_PATHS = [
    "/usr/share/fonts/TTF/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]


def font(size):
    for p in _FONT_PATHS:
        if os.path.exists(p):
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()


def read_wav(path):
    with wave.open(path, "rb") as w:
        sr = w.getframerate()
        n = w.getnframes()
        ch = w.getnchannels()
        raw = w.readframes(n)
    x = np.frombuffer(raw, np.int16).astype(np.float32) / 32768.0
    if ch > 1:
        x = x.reshape(-1, ch).mean(axis=1)
    return x, sr


def colorize(norm):
    """Map [0,1] -> Catppuccin gradient (base → mauve → rosewater)."""
    stops = np.array([[30, 30, 46], [203, 166, 247], [245, 224, 220]], float) / 255
    pos = np.array([0.0, 0.5, 1.0])
    out = np.empty(norm.shape + (3,), float)
    for c in range(3):
        out[..., c] = np.interp(norm, pos, stops[:, c])
    return (out * 255).astype(np.uint8)


def spectrogram(x, sr, win=1024, hop=256, dyn_db=80.0):
    if len(x) < win:
        x = np.pad(x, (0, win - len(x)))
    w = np.hanning(win)
    nfr = 1 + (len(x) - win) // hop
    frames = np.stack([x[i * hop:i * hop + win] * w for i in range(nfr)])
    mag = np.abs(np.fft.rfft(frames, axis=1))            # (nfr, win/2+1)
    db = 20 * np.log10(mag + 1e-6)
    db -= db.max()
    norm = np.clip((db + dyn_db) / dyn_db, 0, 1)          # 0..1
    img = norm.T[::-1]                                    # freq up the Y axis, time on X
    # dominant frequency (ignore DC)
    avg = mag.mean(axis=0)
    avg[0] = 0
    peak_hz = int(np.argmax(avg) * sr / win)
    return img, peak_hz


def render(spec, sr, size=(1000, 400)):
    """spec: HxW float [0,1] -> labelled PIL image."""
    h, w = spec.shape
    rgb = Image.fromarray(colorize(spec), "RGB").resize(size, Image.BILINEAR)
    iw, ih = size
    pad_l, pad_b = 56, 22
    canvas = Image.new("RGB", (iw + pad_l, ih + pad_b), (24, 24, 24))
    canvas.paste(rgb, (pad_l, 0))
    d = ImageDraw.Draw(canvas)
    f = font(12)
    nyq = sr / 2
    for hz in [0, 1000, 2000, 4000, 8000]:
        if hz > nyq:
            continue
        y = int(ih * (1 - hz / nyq))
        d.line([(pad_l, y), (iw + pad_l, y)], fill=(80, 80, 100), width=1)
        d.text((4, min(ih - 12, max(0, y - 6))), f"{hz//1000}k" if hz else "0", font=f, fill=(180, 180, 200))
    d.text((pad_l + 4, ih + 4), "time →   (Y = frequency)", font=f, fill=(180, 180, 200))
    return canvas


def transcribe(x, sr, model_name):
    """Transcribe a mono float32 signal. Robust to short/quiet clips: the VAD
    filter silently drops those, so we peak-normalize first and fall back to a
    no-VAD pass when the VAD pass comes back empty."""
    try:
        from faster_whisper import WhisperModel
    except Exception as e:
        return None, f"(faster-whisper unavailable: {e})"
    try:
        audio = np.ascontiguousarray(x, dtype=np.float32)
        peak = float(np.max(np.abs(audio))) if len(audio) else 0.0
        if peak > 0:
            audio = audio / peak * 0.95          # lift quiet clips into whisper's range
        # hear records 16k mono; faster-whisper expects 16k float32 arrays
        if sr != 16000:
            n = max(1, round(len(audio) * 16000 / sr))
            audio = np.interp(np.linspace(0, len(audio), n, endpoint=False),
                              np.arange(len(audio)), audio).astype(np.float32)
        model = WhisperModel(model_name, device="cpu", compute_type="int8")

        def run(vad):
            segs, _ = model.transcribe(audio, language="en", vad_filter=vad)
            return " ".join(s.text.strip() for s in segs).strip()

        text = run(True) or run(False)           # VAD-clean first, then VAD-off fallback
        return text, None
    except Exception as e:
        return None, f"(transcription failed: {e})"


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--wav", required=True)
    p.add_argument("--outdir", required=True)
    p.add_argument("--model", default="base.en")
    args = p.parse_args()
    os.makedirs(args.outdir, exist_ok=True)

    x, sr = read_wav(args.wav)
    dur = len(x) / sr if sr else 0.0
    rms = float(np.sqrt(np.mean(x ** 2))) if len(x) else 0.0

    spec, peak_hz = spectrogram(x, sr)
    spec_path = os.path.join(args.outdir, "spectrogram.png")
    render(spec, sr).save(spec_path)

    text, err = transcribe(x, sr, args.model)
    tpath = os.path.join(args.outdir, "transcript.txt")
    with open(tpath, "w") as f:
        f.write((text or "") + "\n")

    print(f"duration:    {dur:.2f}s   sample_rate: {sr} Hz")
    print(f"loudness:    rms {rms:.4f}   peak_freq ~{peak_hz} Hz")
    print(f"spectrogram: {spec_path}   (Read this image to 'see' the sound)")
    print(f"transcript:  {tpath}")
    # Distinguish the three real outcomes so the result never misleads: silence,
    # audible-but-unintelligible (whisper choked — check the spectrogram), or speech.
    SOUND_FLOOR = 0.005
    if err:
        print(f"speech:      {err}")
    elif text:
        print(f"speech:      “{text}”")
    elif rms > SOUND_FLOOR:
        print(f"speech:      (sound present — rms {rms:.4f}, peak ~{peak_hz} Hz — "
              f"but no intelligible speech; see the spectrogram)")
    else:
        print(f"speech:      (silence — rms {rms:.4f} below floor {SOUND_FLOOR})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
