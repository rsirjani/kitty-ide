# kitty-ide

A single-window IDE built entirely out of [kitty](https://sw.kovidgoyal.net/kitty/),
a file manager, an editor, and a handful of small scripts — no Electron, no
language server bloat, no compositor. Just terminal panes that behave like a
real IDE, with **crisp graphics** (a real browser and PDF viewer rendered
pixel-perfect inside the terminal).

> Personal workspace for [@rsirjani](https://github.com/rsirjani). Built and
> tracked here so it can keep growing. Arch Linux + Hyprland + kitty.

```
┌─ explorer (yazi) ──────────────┬─ claude ─┐
│  ~/projects                    │          │
├────────────────────────────────┤  a       │
│  README.md │ main.pdf │ 🌐 web  │  shell / │
│ ── tab bar (fixed 1 row) ───────│  claude  │
│                                 │  session │
│   editor / reader / browser     │          │
│   (nvim · tdf · carbonyl)       │          │
└────────────────────────────────┴──────────┘
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
- **A real browser, in the terminal, crisp** — [carbonyl](https://github.com/fathyb/carbonyl)
  patched to render via the kitty graphics protocol instead of blocky
  half-blocks (see [`carbonyl/`](carbonyl/)). Plus omnibox-style URL entry.
- **Fast PDF viewing** — [tdf](https://github.com/itsjunetime/tdf), full pages,
  native scroll/zoom, kitty-graphics rendering.
- **Fixed tab bar** — the tab strip is locked to exactly one row via a kitty
  layout patch; its border can't resize it, but the explorer/editor border still
  does. The tab bar + editor highlight as **one box** when focused.
- **Explorer-following terminal** — as you browse directories in the explorer,
  the terminal silently `cd`s to match — but only when it's idle (never typing
  into a running claude session or command).
- **Idle animation** — a looping ASCII sleeping cat when no document is open.
- **Catppuccin Mocha** throughout.

## Install

Arch Linux, with kitty, [yazi](https://github.com/sxyazi/yazi), neovim,
[tdf](https://github.com/itsjunetime/tdf), and (optional) a patched
[carbonyl](carbonyl/) on `PATH`.

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
| `kitty/ide-pin-tabbar.py` | watcher fallback that keeps the tab bar one row |
| `kitty/patches/` | the idempotent re-applier for the kitty splits-layout patch |
| `yazi/` | the explorer's yazi config + the `cd`-broadcast hook |
| `pacman/` | hook that re-applies the kitty patch after a kitty reinstall |
| `carbonyl/` | the crisp-browser patch (diff + notes; upstream PRs linked) |
| `docs/` | architecture & internals, known gaps |

## Keybindings

**Panes** — hover to focus, or:
- `Ctrl+Shift+←/→` previous / next pane
- `Ctrl+Shift+E` explorer · `Ctrl+Shift+.` editor · `Ctrl+Shift+T` terminal

**Tabs** (editor zone):
- `Alt+]` / `Alt+[` next / previous tab · `Alt+1…9` jump to tab
- `Alt+W` close the active tab · click a tab or its `×` · click `+ web`

**Resize** — borders aren't mouse-draggable except explorer↔editor; use
`Ctrl+Shift+R` then arrows for fine control.

See [docs/INTERNALS.md](docs/INTERNALS.md) for how the kitty patches work and the
known rough edges, and [ROADMAP.md](ROADMAP.md) for where this is going.
