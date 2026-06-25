# Virtual Desktop (`ide-vd`)

A graphical environment Claude can **pilot** and the human can **watch** — built
so back-end logs aren't the only proof a front-end works. When Claude builds a
web app, a game, or any GUI, it runs inside an isolated, GPU-accelerated virtual
desktop; the **vd pane** in the IDE shows it live, and Claude gets eyes
(screenshots + frame capture), **ears** (audio → text + spectrogram), and hands
(mouse/keyboard) to drive it in an act → observe loop.

## The one idea: everything speaks VNC

The environment can be a Docker Linux desktop, a full-OS VM, or a remote box —
so we standardize on **one wire protocol, VNC (RFB)**, and split the system in
two:

```
        system-agnostic CORE                pluggable PROVIDERS (per env type)
  ┌───────────────────────────────┐   ┌──────────────────────────────────────┐
  │ ide-vd-view  live feed pane    │   │ docker  Linux X11 desktop (default)   │
  │   VNC framebuffer → kitty gfx  │◄──┤ qemu    full-OS VM via QEMU's VNC     │
  │ _ide_vd_vnc.py  eyes + hands   │   │ remote  any existing VNC endpoint     │
  │   shot / click / type / key    │   └──────────────────────────────────────┘
  └───────────────────────────────┘     each just: bring up an env, hand back
        never changes per-OS                 a host:port[,password] endpoint
```

Input goes over VNC (not container-specific `xdotool`), so the *same* eyes/hands
code drives a Docker desktop, a Windows VM, or a remote machine unchanged.
Adding an OS = one provider script + one env file. Nothing else moves.

## Environments

An **env** is `vd/envs/<name>.env` — a tiny KEY=value file naming a `provider=`
and its config. `ide-vd ls` lists them; `ide-vd use <name>` selects one.

| Env | Provider | What |
|-----|----------|------|
| `linux-desktop` | docker | the default — Xvfb + openbox + x11vnc desktop, built from `Dockerfile`, served on `127.0.0.1:5901` |
| `example-vm.env.disabled` | qemu | template for a Windows/macOS/Linux VM (rename to enable) |
| `example-remote.env.disabled` | remote | template pointing at an existing VNC host |

The Docker desktop is a *general* image (browser + node + python + git + fonts,
with `apt` left in) so Claude can test real front-ends and install whatever a
complicated app needs: `ide-vd run apt-get install -y <pkg>`.

## Commands

```
ide-vd ls | use <env> | up [env] | down | status

# senses (all produce things Claude can Read — never raw audio/video)
ide-vd shot [path]                   # one screenshot -> PNG (eyes)
ide-vd rec  <secs> [fps] [outdir]    # motion -> consecutive PNGs + montage.png
ide-vd hear <secs> [outdir]          # sound  -> transcript.txt + spectrogram.png (ears)

# hands & voice
ide-vd click X Y [left|right|middle] | move X Y | scroll up|down [n]
ide-vd type "text" | key ctrl+s      # combos: ctrl/alt/shift/super + key
ide-vd speak "text" [voice] [wpm]    # speak into the virtual mic (mouth; TTS in)

# run things
ide-vd open <url>                    # launch the desktop browser at a URL
ide-vd run <cmd...>                  # blocking command (apt, build, tests…)
ide-vd launch [--gpu] <cmd...>       # detached GUI app (stays running)
ide-vd gl <cmd...>                   # run a GPU app via VirtualGL

# manage the box
ide-vd reset                         # destroy the container (clean slate)
ide-vd commit [tag]                  # snapshot the configured box to an image
```

## Senses (Claude can't take audio/video — only text + images)

- **Eyes** — `shot` is one frame; **`rec`** grabs frames at N fps and lays them
  out as `montage.png` so motion/animation reads in a single look.
- **Ears** — `hear` records the desktop's audio and renders it two ways:
  **speech → text** (`transcript.txt`, via faster-whisper) and **any sound →
  image** (`spectrogram.png`, time on X / frequency on Y, with the dominant
  frequency printed). Coordinate it with whatever should be playing, e.g.
  `ide-vd hear 5 &` then start your app.
- **Mouth** — `speak` is the input counterpart to `hear`: it synthesizes text
  with espeak-ng and plays it into a **virtual microphone** (`virtmic`) that apps
  see as a real mic via `getUserMedia`. This drives voice-input UIs — push-to-talk,
  speech-to-text fields, voice assistants — that you otherwise couldn't exercise
  headlessly. The audio path is two independent channels: apps *play out* to
  `vdsink` (what `hear` records) and *listen in* on `virtmic` (what `speak` feeds),
  so speaking never bleeds into `hear`. Optional `voice` (default `en-us`) and
  `wpm` (default `160`) tune the espeak-ng voice and rate. Example loop — answer a
  voice prompt and capture the reply: `ide-vd speak "yes, go ahead"` then
  `ide-vd hear 6` to transcribe what the app says back.

## GPU acceleration

The desktop renders on the host **NVIDIA GPU** via the container toolkit +
VirtualGL/EGL, so WebGL / `<canvas>` / 3D front-ends run on hardware. Launch GPU
apps through VirtualGL: `ide-vd gl glxgears`, or `ide-vd launch --gpu <app>`.
Toggle per env with `gpu=nvidia|none` in the env file. (`chromium` via `open`
stays CPU by default — GPU-in-Chromium under a headless X is flaky; use `gl` for
explicit GPU apps.)

## Persistence & self-configuration

The box is Claude's playground and its changes **stick**: `ide-vd run apt-get
install -y …` to add deps, set up toolchains, etc. `ide-vd down` *stops* the
container (state preserved); `ide-vd up` resumes it. `ide-vd reset` wipes it for
a clean slate; `ide-vd commit <tag>` snapshots a configured box into a reusable
image.

## How Claude uses it

1. `ide-vd up` (or it's already up) → the vd pane shows the desktop.
2. `ide-vd open http://localhost:3000` (or `run` your app).
3. `ide-vd shot` → `Read` the PNG → see the actual rendered UI.
4. `ide-vd click / type / key` to interact, `shot` again to confirm — repeat.

## Isolation & safety

The Docker desktop is a separate X server; Claude's clicks/keystrokes land only
inside the container, never on the host Hyprland/Wayland session. The VNC port
is bound to `127.0.0.1` only, so the desktop is never reachable off-host.

## Limitations

- **Frame rate** — the live feed (and `rec`) reblit whole frames: fine for UI and
  motion, limited for fast games. Feed FPS via `IDE_VD_FPS` (default 5).
- **No raw audio/video to Claude** — by design: `rec`→images, `hear`→text+image.
- **GPU is per-app** — only apps launched via `gl`/`launch --gpu` use the GPU;
  needs `nvidia-container-toolkit` (set `gpu=none` to fall back to software GL).
- **`run`/`launch`/`gl` are docker-only** — VMs/remote have no guest agent; launch
  apps from inside the guest there.
- **Docker group** — after `usermod -aG docker`, the group needs a re-login;
  until then `ide-vd` falls back to `sudo docker` automatically.

## Dependencies

- `docker`; `nvidia-container-toolkit` (for GPU); `qemu-system-x86_64` (only for a
  qemu env).
- A python venv at `~/.local/share/ide-vd/venv` with
  `asyncvnc pillow numpy faster-whisper` (created by `install.sh`).
