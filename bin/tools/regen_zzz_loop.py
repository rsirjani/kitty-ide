from PIL import Image
import numpy as np, glob, os

ZDIR = '/home/tin/src/kitty-ide/bin/assets/kitty_idle/zzz'
fs = sorted(glob.glob(ZDIR + '/frame_*.png'))
SIZE = 192
L = 72            # loop length (matches cat layer)
N = 3             # number of z's
STAGGER = L // N  # 24 frames between z spawns -> 3 intervals over L => seamless

# --- base glyph: extract the mid z from frame 20 (cleanest, fullest) ---
src = np.array(Image.open(fs[20]).convert('RGBA'))
gly = src[27:39, 53:65].copy()          # 12x12 z2 bbox (y27-38, x53-64)
# trim fully-transparent border
ga = gly[:, :, 3] > 0
ys, xs = np.where(ga)
gly = gly[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
GLY = Image.fromarray(gly, 'RGBA')
GW, GH = GLY.size

# --- path (calibrated). alpha=0 at f=0 (spawn) and f=1 (despawn) ---
SX, EX = 30.0, 88.0           # x at f=0 and f=1 (extended one spacing beyond visible 3)
def path(f):
    x = SX + f * (EX - SX)
    y = 76.7 - 0.746 * x      # fitted drift line
    return x, y
def scale(x):
    return (0.054 * x + 8.4) / 11.0   # glyph grows slightly as it rises

def env(f):
    # alpha envelope: ramp in [0,.18], full [.18,.82], ramp out [.82,1]
    if f < 0.18:
        return f / 0.18
    if f > 0.82:
        return (1.0 - f) / 0.18
    return 1.0

def render(frame):
    canvas = Image.new('RGBA', (SIZE, SIZE), (0, 0, 0, 0))
    for k in range(N):
        age = (frame + k * STAGGER) % L
        f = age / L
        a = env(f)
        if a <= 0.01:
            continue
        x, y = path(f)
        s = scale(x)
        w, h = max(1, round(GW * s)), max(1, round(GH * s))
        g = GLY.resize((w, h), Image.LANCZOS)
        ga = np.array(g)
        ga[:, :, 3] = (ga[:, :, 3].astype(float) * a).astype(np.uint8)
        g = Image.fromarray(ga, 'RGBA')
        canvas.alpha_composite(g, (round(x - w / 2), round(y - h / 2)))
    return canvas

os.makedirs('/tmp/zzz_new', exist_ok=True)
for fr in range(L):
    render(fr).save('/tmp/zzz_new/frame_%03d.png' % fr)
print('rendered', L, 'frames; glyph', (GW, GH))
