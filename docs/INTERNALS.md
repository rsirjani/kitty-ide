# Internals

How the pieces fit together, and the non-obvious bits.

## Layout & the tab system

The IDE is one kitty OS window using the `splits` layout (`kitty/ide.session`):

```
splits tree:  [ [explorer, [tabbar, content]], claude ]
```

- **explorer** runs yazi (`pane=explorer`).
- **tabbar** runs `ide-tabbar` — a 1-row Python strip (`pane=tabbar`).
- **content** runs `ide-home` (the idle animation) (`pane=content`).
- **claude** runs a shell (`pane=claude`).

Documents don't replace `content` — they open as kitty **overlays** over it
(`ide-open` → `kitty @ launch --type=overlay --next-to var:pane=content --var
doc=1 …`). The `--next-to var:pane=content` anchors the overlay to the content
pane explicitly: `ide-open` is invoked from whichever pane fired it (the tab-bar
on `+ web`, the claude pane on a clicked link), and with `focus_follows_mouse`
the pointer can snap focus back there before the overlay lands — so without the
anchor a doc could open over the 1-row tab-bar or the claude pane. Overlays keep
the kitty graphics protocol intact (needed for carbonyl/tdf), which a
multiplexer would strip. `ide-tabbar` lists every overlay with `doc=1` as a tab,
highlights the focused one, and on click focuses it / closes it / opens a browser.
`ide-tab` does the same by keyboard; `ide-pane` switches between the three zones.

In the explorer, `yazi/init.lua` overrides `Entity:click` to add **double-click
to activate**: yazi's stock left-click only *reveals* (hovers) an item, so the
override records the last-clicked url + `ya.time()` and, on a second left-click
of the same item within 0.4s, emits `enter` for a directory or `open` for a file
(the openers route files to `ide-open`) — the same as selecting it and pressing
Enter. Single clicks keep their normal hover behaviour. A **right-click** copies
the item's full path to the clipboard (`ya.clipboard`) with a notification.

It also overrides the empty `Header:click` to make the **path in the header a
breadcrumb**: double-clicking a segment jumps to that directory level. The level
is the number of `/` separators at or after the clicked column, so it stays
correct even when the path is left-truncated to fit the narrow explorer (the
visible right-hand segments always map to the right ancestor of the cwd); the
leading `/` or `~` jumps to root / home.

## The Claude chat-title header

A one-row strip (`pane=chat-header`, running `ide-claude-header`) sits on top of
the claude pane and shows the title of the current chat, so across many
workspaces you can tell at a glance what each one's Claude is doing. The pieces:

- **Where the title comes from.** Claude Code sets the terminal title (OSC 0/2)
  to an AI-generated summary of the conversation (disableable with
  `CLAUDE_CODE_DISABLE_TERMINAL_TITLE`), e.g. `⠐ Refactor the auth module` — a
  braille spinner that animates while it works, then the summary. So the claude
  pane is launched **without** a kitty `--title`: a `--title` makes kitty mark the
  window `title_overridden` and *suppress* the program's OSC title, pinning it to
  the literal value. With no override, the OSC title lands in the window's `title`
  field, where `kitty @ ls` can read it. (The other panes keep their `--title`.)
- **Rendering.** `ide-claude-header` polls `kitty @ ls`, finds the `pane=claude`
  window, strips Claude's animated spinner prefix (so the strip doesn't flicker),
  and draws the title as a solid one-row bar — but only when a `claude` process is
  actually in that pane's `foreground_processes`; otherwise it shows a muted "no
  chat" placeholder rather than zsh's cwd/`sh` title. It pins itself to one row
  the same way the tab bar does (see the `fixed_lines` patch below), and shares
  `--var hlgroup=claude` with the claude pane so focusing either highlights the
  pair as one box.
- **Naming gotcha.** kitty's `--match var:pane=…` is an **unanchored regex**, so
  the strip is `pane=chat-header`, *not* `claude-header`: the latter would make
  every existing `var:pane=claude` match (`ide-cwd-follow`, `ide-pane`) also hit
  the header. Pane var values are kept non-overlapping; matches that must target
  only the shell anchor it (`var:pane=claude$`).

## Waybar window title → chat title

Waybar's `hyprland/window` module shows `{title}`, which for the IDE was the
static `--title "IDE"` set by the `ide` launcher. The `custom/window` module runs
`ide-waybar-window` instead: it reads the focused window via `hyprctl
activewindow`, and for a kitty IDE (`class=ide`) swaps in the chat title. The
mapping is exact — each IDE's control socket is `/tmp/ide.sock-<pid>` where
`<pid>` is the kitty process pid, which is also the Hyprland window pid — so the
focused window resolves straight to its instance's `kitty @ ls`. It falls back to
"IDE" when no chat is running and to the plain window title for non-IDE windows
(pango-escaped). It runs continuously, printing only on change, and polls (rather
than waiting on Hyprland events) because Claude updating its title in place is not
a Hyprland event: the kitty OS-window title stays "IDE", only the inner pane's
title changes. "Most recent if more than one chat" falls out for free — it tracks
whatever window Hyprland currently has focused.

## kitty patch 1 — `fixed_lines` (fixed-height tab bar)

kitty's splits layout sizes panes proportionally (`bias`), so the tab bar's
border could resize it. The patch (`apply-fixed-lines-patch.py`, applied to
`kitty/layout/splits.py`) adds: a window with the `fixed_lines=N` user var gets
a **constant** N rows from `layout_pair`, ignoring the bias. Effect:

- the border *next to* the fixed window is a no-op (can't resize it),
- the *outer* border still resizes the editor normally,
- only that one window is affected — other borders stay draggable.

**Gotcha:** the fixed height must include the window's padding/border
`decoration(...)`. With `window_padding_width 6`, a bare `1 * cell_height` clips
the single row to zero and the text vanishes — so it's `fl1 * cell_height +
decoration`.

The tab bar is launched with `--var fixed_lines=1`. As a belt-and-suspenders
fallback if the patch is ever absent, `ide-pin-tabbar.py` (a kitty *watcher*) and
a polling pin inside `ide-tabbar` also snap it back to one row.

## kitty patch 2 — `hlgroup` (merged highlight)

In `minimal_borders` mode kitty colors the thin line next to the active window.
The patch makes windows that share an `hlgroup` user var highlight as **one
box**: when any member is active they all get the active border, and the line
*between* two members is suppressed. The tab bar and content both set
`--var hlgroup=editor`, so focusing either lights up the whole editor unit. (The
faint grey seam that remains between them is a structural window edge, not a
border we color.)

## kitty patch 3 — `live_resize` (graphics track a border drag)

During an interactive split-border **drag**, kitty deliberately *pauses* resize
notifications to every child (`boss.py` `drag_resize_*` → `Window.
pause_resize_notifications_to_child`): the cell grid reflows live, but the child
gets no SIGWINCH and its PTY size isn't updated until the drag **ends**. For text
panes that's smoother (no reflow thrash mid-drag). But a pane rendering via the
**kitty graphics protocol** — the VD feed (`ide_vd_view.py`), which blits a
pane-sized image — can't learn it was resized, so kitty keeps drawing the last
transmitted frame at its old pixel size over the new window: it freezes, then
clips/gaps ("bugged out") for the whole drag, snapping right only on release.
(`resize_draw_strategy scale` doesn't help — that only covers *OS-window*
resizes, not internal drags.)

The patch (`window.py`) lets a window with the `live_resize` user var **opt out**
of the pause: it keeps getting SIGWINCH on every drag step, so the feed re-fits
its frame to the live pane size and the image scales smoothly *with* the pane.
Only opted-in panes are affected — text panes keep the calm paused behaviour. The
`vd` pane is launched with `--var live_resize=1`.

This pairs with the feed itself (`ide_vd_view.py`): a SIGWINCH handler wakes the
paint loop immediately (coalesced), and the loop repaints on a **pane-size
change**, not only on VNC damage — so a resize re-fits even when the desktop is
static. Capture (the blocking VNC read) runs as its own task so it can never
freeze the repaint.

(There is also a small **patch 4** in the same applier — pinning a dragged
divider's direction so it always follows the cursor; see the comment in
`apply-fixed-lines-patch.py`.)

## Patch persistence

kitty updates are disabled (`IgnorePkg = kitty`). If kitty is ever reinstalled,
`pacman/zz-kitty-fixed-lines.hook` re-runs the re-applier (idempotent; applies
both patches; bails safely if kitty's source changed too much). Original backed
up as `splits.py.orig`. Manual re-apply:
`sudo python3 ~/.config/kitty/patches/apply-fixed-lines-patch.py`.

## Explorer-following terminal

Two parts:

1. `yazi/init.lua` subscribes to yazi's `cd` event and writes the current dir to
   `/tmp/ide-<id>-explorer-cwd` on every move.
2. `ide-cwd-follow` (started by the explorer pane, exits when this IDE's socket
   is gone) watches that file. On change it `cd`s the claude pane to match — **only
   if** that pane is sitting at an empty shell prompt. Two gates:
   - **Stage 1 (no command running):** the pane's foreground process must be a
     bare shell. If a `claude`/command is running, it does nothing, so the `cd`
     keystrokes never land inside a running program.
   - **Stage 2 (no half-typed command):** it reads the pane via
     `kitty @ get-text --ansi`, scopes to the current prompt using its OSC 133
     `A` (prompt-start) mark, and takes the text after the last prompt symbol as
     the input buffer. If that buffer is non-empty, it skips the `cd` so it can't
     be appended onto a *typed-but-unrun* command. If shell-integration marks are
     absent it falls back to the stage-1 decision rather than regress.

The `cd` is sent via `kitty @ send-text`.

## The browser

`ide-open browser [url]` launches `carbonyl --graphics` as an overlay tab.
`--graphics` is the patched mode (see `carbonyl/`) that renders the page via the
kitty graphics protocol — full resolution, real fonts — instead of half-blocks,
and resolves bare hosts / search queries in the address bar. Default page is
arXiv.

carbonyl is Chromium, so two tabs sharing one profile would hit Chromium's
singleton lock — the second refuses to open while the first is up. `ide-open`
therefore hands each tab its own `--user-data-dir` from a **persistent pool**
under `~/.local/share/ide-carbonyl/p<N>`: it reuses the lowest-numbered profile
not currently held by a running carbonyl, creating it on first use. So with one
tab open at a time you always land on `p0` and the browser stays logged in
across sessions (cookies/history persist); extra *concurrent* tabs spill into
`p1`, `p2`, … (each persistent in its own right). The pool lives under
`~/.local/share` (not `/tmp`) so it survives reboots.

**Clicking a link** (e.g. a PR/issue URL Claude prints in the claude pane) opens
it as a browser tab in that same IDE instance, rather than the host's default
browser. `kitty/ide.conf` sets `open_url_with ide-open-url`; kitty runs that
handler with `KITTY_LISTEN_ON` pointing at the *clicking* instance's socket, so
`ide-open-url` just forwards http(s) URLs to `ide-open browser` (which resolves
the instance via `ide-sock` and anchors the tab to `content`). Non-web URLs fall
back to `xdg-open`.

## PDFs

`ide-open pdf <file>` opens the file in `tdf` as an overlay tab. tdf starts in
fit-to-screen mode, which renders pages too small to read; its `z` key toggles
fill-screen ("zoom") mode. `ide-open` auto-sends a single `z` (after a short
delay so tdf's kitty-graphics startup handshake doesn't swallow it) so PDFs open
readable. `z` is a toggle, so it's sent exactly once.

## Multiple instances (per-instance isolation)

Each `ide` launch is a self-contained kitty OS window with its own control
socket, `/tmp/ide.sock-<pid>` (kitty appends its pid). kitty exports that path
as `KITTY_LISTEN_ON` in **every** pane and child process of that window, so it
is the natural per-instance key:

- The helper scripts (`ide-open`, `ide-pane`, `ide-tab`, `ide-cwd-follow`)
  source `bin/ide-sock`, which resolves the socket from `KITTY_LISTEN_ON`. Every
  `kitty @` command — and the `+ web` browser launch the tab bar fires via
  `ide-open` — therefore acts on the IDE that issued it, never a sibling on
  another workspace. (Previously they used `find /tmp -name 'ide.sock-*' | head
  -1`, the *first* socket on the machine, so actions leaked across instances.)
- State files are namespaced by the same id: `/tmp/ide-<id>-active-doc`,
  `/tmp/ide-<id>-explorer-cwd`, `/tmp/ide-<id>-tabs-dirty` (`ide-tabbar` and
  `yazi/init.lua` derive the id the same way from `KITTY_LISTEN_ON`).
- `ide` reaps only *stale* sockets at startup — those whose owning kitty pid is
  dead — so launching a second IDE never deletes a running sibling's live socket
  (which used to strand that instance's `kitty @` calls until they timed out).

## The virtual desktop (`ide-vd`)

The `vd` pane is a window into a graphical environment Claude pilots — so GUI
work can be *seen*, not just logged. Full subsystem docs are in
[`vd/README.md`](../vd/README.md); the non-obvious bits:

- **One protocol, two halves.** Everything is **VNC**. A *system-agnostic core*
  (`bin/ide_vd_view.py` for the feed, `bin/_ide_vd_vnc.py` for eyes/hands) speaks
  plain RFB and never changes per-OS. *Pluggable providers*
  (`bin/ide-vd-provider-{docker,qemu,remote}`) each only know how to start an
  environment and hand back a `host:port[,password]` endpoint. Adding an OS is
  one provider + one `vd/envs/<name>.env`. Input goes over VNC (not
  container-only `xdotool`), so the same code drives a Docker desktop, a VM, or a
  remote box identically.
- **The feed.** `ide-vd-view` runs as the pane process. It asks `ide-vd _resolve`
  for the active endpoint, holds a VNC connection, and blits each framebuffer
  into the pane with the **kitty graphics protocol** (same trick as carbonyl):
  it reads the pane's pixel size via `TIOCGWINSZ`, aspect-fits the frame onto a
  Catppuccin-base canvas, and transmits it as PNG with a stable image id `i=1`
  (`a=T,f=100`) at cursor-home so each frame replaces the last with no scroll. It
  reconnects on drop and shows an idle/connecting card when no env is up, so the
  IDE boots fine with Docker stopped.
- **Eyes & hands are stateless.** Unlike the feed, `shot`/`click`/`type`/`key`
  each make a short one-off VNC connection via `_ide_vd_vnc.py` (x11vnc is run
  `-shared`, so they coexist with the live feed). No daemon, no FIFO, no shared
  mutable state — every command is independent. `key` parses combos like
  `ctrl+shift+s` into X keysym names; `click` moves then presses after a short
  settle.
- **The docker desktop.** `vd/Dockerfile` builds Xvfb + openbox + x11vnc +
  xdotool + chromium + node/python on Debian. `entrypoint.sh` starts Xvfb `:99`,
  paints the root Catppuccin base, runs openbox, then `exec`s x11vnc as PID 1
  (so `docker stop` is clean). x11vnc uses `-nocursorshape` so the pointer is
  drawn **into** the framebuffer and therefore shows up in screenshots/feed. The
  port is published to `127.0.0.1` only — the desktop is never reachable
  off-host, and Claude's input lands only inside the container, never on the host
  Hyprland session.
- **Senses beyond a still.** Claude can't ingest audio or video, so motion and
  sound are converted to text/images. `rec` (`_ide_vd_rec.py`) holds one VNC
  connection and grabs frames in a timed loop → PNGs + a `montage.png` contact
  sheet. `hear` captures the desktop's audio and `_ide_vd_hear.py` renders it as
  **text** (faster-whisper transcript) **and an image** (a numpy-STFT spectrogram,
  time×frequency, with the peak frequency printed) — the audio analogue of rec's
  montage.
- **GPU.** When the env sets `gpu=nvidia`, the provider adds `--gpus all` plus
  `NVIDIA_DRIVER_CAPABILITIES=all` (the toolkit defaults to compute-only — GL
  needs the `graphics` capability). The image ships **VirtualGL** (no driver libs
  baked in — the toolkit injects matching ones at run time); `ide-vd gl <app>`
  runs `vglrun -d egl <app>`, which renders on the GPU via EGL and composites into
  the Xvfb framebuffer x11vnc serves. So a *headless* X server still gets hardware
  GL.
- **Audio plumbing.** `entrypoint.sh` starts a system-mode PulseAudio (the
  root-in-container-friendly mode) with a `module-null-sink` named `vdsink` as the
  default; apps play into it and `hear` records `vdsink.monitor`. `PULSE_SERVER`
  is an image ENV so docker-exec'd apps and the ffmpeg capture all find the
  server. Self-contained — no host audio device involved.
- **Persistence.** `down` *stops* the container (not `rm`), so Claude's apt
  installs / config survive `up`. `reset` removes it; `commit` snapshots it to an
  image. A `~/.cache/ide-vd/share` ↔ `/vdshare` bind-mount is how captured wavs
  leave the container without `docker cp`.

## Known gaps

- **Hover resize-cursor** on the fixed tab-bar border: the drag is a no-op but
  the cursor still shows the resize shape — that detection is kitty compiled-C,
  not reachable from Python. Needs a kitty source patch + local build.
- **carbonyl scrolling** re-blits the whole frame each repaint (fine for normal
  browsing; heavy for fast animations). Damage-based partial updates are a TODO.
- **virtual-desktop FPS** — the feed likewise reblits whole frames over VNC
  (default 5 fps, `IDE_VD_FPS`): fine for app testing and normal UI, limited for
  fast games. Damage-based partial updates would lift it.
