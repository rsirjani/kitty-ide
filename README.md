# kitty-ide

A single-window IDE built entirely out of [kitty](https://sw.kovidgoyal.net/kitty/),
a file manager, an editor, and a handful of small scripts — no Electron, no
language server bloat, no compositor. Just terminal panes that behave like a
real IDE, with **crisp graphics** (a real browser and PDF viewer rendered
pixel-perfect inside the terminal).

> Personal workspace for [@rsirjani](https://github.com/rsirjani). Built and
> tracked here so it can keep growing. Arch Linux + Hyprland + kitty.

```
┌─ explorer ─┬─ 🖥 desktop feed ──┬─ ✳ chat title ─┐
│ ~/projects │ live VNC view of a │ (fixed 1 row)  │
├────────────┴─ virtual desktop ──┼────────────────┤
│  README.md │ main.pdf │ 🌐 web   │  a             │
│ ── tab bar (fixed 1 row) ────────│  shell /       │
│                                  │  claude        │
│   editor / reader / browser      │  session       │
│ (nvim·glow·tdf·mpv·visidata·web) │                │
└──────────────────────────────────┴────────────────┘
```

## Why

Terminal multiplexers (tmux, zellij) strip the kitty graphics protocol, so you
can't have a real browser or a crisp PDF inside them. kitty itself *can* render
images at full resolution — so this builds the IDE directly on kitty windows,
keeping graphics intact, and fills the gaps kitty leaves (a fixed-size tab
strip, a merged highlight, a per-pane tab system) with small patches and
scripts.

## Features

- **One window, three zones** — explorer (top-left), editor (tab bar + content),
  and a terminal/claude pane on the right. Hover to focus, or switch by keyboard.
- **Custom per-pane tab system** — every document (nvim, a PDF, a browser) opens
  as a tab in the editor zone. Click a tab to switch, click its `×` to close,
  click `+ web` to open a browser. Keyboard too.
- **Opens any file in the right viewer** — one dispatcher (`ide-open`) detects
  the type and renders it in a tab: **markdown** via [glow](https://github.com/charmbracelet/glow),
  **HTML** in a web tab, **images** via kitty `icat`, **video/audio** via `mpv`
  (kitty graphics), **CSV / XLSX** as editable tables in [VisiData](https://www.visidata.org/),
  **docx / pptx / xlsx** as a crisp PDF render (LibreOffice → tdf), **PDF** via
  tdf, everything else in nvim. Rendered tabs can **morph to editable** in place
  (`Alt+M`, or the tab-bar `⇄`): markdown ⇄ nvim, CSV ⇄ raw, and office docs
  open in the real LibreOffice GUI inside the Virtual Desktop.
- **A real browser, in the terminal, crisp** — [carbonyl](https://github.com/fathyb/carbonyl)
  patched to render via the kitty graphics protocol instead of blocky
  half-blocks (see [`carbonyl/`](carbonyl/)). Plus omnibox-style URL entry, and
  **clicking any link** (e.g. one Claude prints) opens it as a browser tab in
  that same IDE instance instead of the host's browser.
- **Fast PDF viewing** — [tdf](https://github.com/itsjunetime/tdf), full pages,
  native scroll/zoom, kitty-graphics rendering.
- **Fixed tab bar** — the tab strip is locked to exactly one row via a kitty
  layout patch; its border can't resize it, but the explorer/editor border still
  does. The tab bar + editor highlight as **one box** when focused.
- **Claude chat-title header** — a one-row strip above the claude pane shows the
  title of the current chat (Claude Code's AI-generated session summary), so with
  many workspaces you can tell at a glance what each is working on. The same
  title also replaces the static "IDE" label in Waybar for the focused window.
- **Explorer-following terminal** — as you browse directories in the explorer,
  the terminal silently `cd`s to match — but only when it's idle (never typing
  into a running claude session or command).
- **A virtual desktop Claude can pilot** — an isolated graphical environment
  (Docker X11 desktop, a QEMU VM, or a remote box — all over VNC) rendered live
  in the top-left feed pane. Claude gets **eyes** (`ide-vd shot`) and **hands**
  (`ide-vd click/type/key`) to drive a real GUI in an act→observe loop, so
  front-end work can be *seen*, not just logged. See [`vd/`](vd/).
- **Idle animation** — a looping ASCII sleeping cat when no document is open.
- **Catppuccin Mocha** throughout.

## Install

Arch Linux, with kitty, [yazi](https://github.com/sxyazi/yazi), neovim,
[tdf](https://github.com/itsjunetime/tdf), and (optional) a patched
[carbonyl](carbonyl/) on `PATH`. The virtual desktop also needs `docker`
(`install.sh` sets up a python venv and builds the desktop image; see [`vd/`](vd/)).

```sh
git clone https://github.com/rsirjani/kitty-ide ~/src/kitty-ide
cd ~/src/kitty-ide
./install.sh        # symlinks configs/scripts, patches kitty's layout, pins kitty
ide                 # launch
```

`install.sh` symlinks everything back to this repo, so editing files here edits
the live IDE. It also applies the kitty layout patch (needs sudo) and adds kitty
to `IgnorePkg` so the patch survives upgrades.

> Paths in `kitty/ide.session` (e.g. `~/projects`) are hardcoded to this
> machine — adjust them for your own setup.

## Layout of this repo

| Path | What |
|------|------|
| `bin/` | the `ide-*` scripts (launcher, tab system, file opener, cwd-follow daemon, idle animation) |
| `kitty/ide.conf` · `ide.session` | the IDE's kitty config and pane layout |
| `kitty/ide-pin-tabbar.py` | watcher fallback that keeps a fixed strip (tab bar, claude header) one row |
| `bin/ide-claude-header` | the one-row strip above claude that shows the current chat title |
| `bin/ide-waybar-window` | Waybar module: focused window's title, or the chat title for IDE windows |
| `kitty/patches/` | the idempotent re-applier for the kitty splits-layout patch |
| `yazi/` | the explorer's yazi config + the `cd`-broadcast hook |
| `vd/` | the virtual-desktop subsystem — Dockerfile, env registry, provider docs (see [`vd/README.md`](vd/README.md)) |
| `pacman/` | hook that re-applies the kitty patch after a kitty reinstall |
| `carbonyl/` | the crisp-browser patch (diff + notes; upstream PRs linked) |
| `docs/` | architecture & internals, known gaps |

## Keybindings

**Panes** — hover to focus, or:
- `Ctrl+Shift+←/→` previous / next pane
- `Ctrl+Shift+E` explorer · `Ctrl+Shift+V` desktop feed · `Ctrl+Shift+.` editor · `Ctrl+Shift+T` terminal

**Tabs** (editor zone):
- `Alt+]` / `Alt+[` next / previous tab · `Alt+1…9` jump to tab
- `Alt+W` close the active tab · click a tab or its `×` · click `+ web`
- `Alt+M` morph the active tab between rendered view and editable · click the `⇄`

**Explorer** — single-click selects, **double-click activates**: opens a file as
a tab or enters a directory (same as pressing Enter). **Right-click** copies the
item's full path to the clipboard. **Double-click a segment of the path** in the
header to jump to that directory level (the leading `/` or `~` jumps to root /
home).

**Resize** — borders aren't mouse-draggable except explorer↔editor; use
`Ctrl+Shift+R` then arrows for fine control.

See [docs/INTERNALS.md](docs/INTERNALS.md) for how the kitty patches work and the
known rough edges, and [ROADMAP.md](ROADMAP.md) for where this is going.
