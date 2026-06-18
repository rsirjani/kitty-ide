# Vision & roadmap

## The vision

The ideal workspace is **one window** where everything — code, docs, PDFs, the
web, a shell, an AI session — lives as a tab or a pane, rendered crisply, with
no mouse round-trips to other apps and no graphical bloat. The terminal is
already fast and scriptable; the only things it's missing are real images and
some IDE ergonomics. This project adds exactly those, and nothing more:

- **Graphics where it matters** — a real browser and PDF viewer at full pixel
  resolution, via the kitty graphics protocol (not ASCII approximations).
- **IDE ergonomics** — a per-pane tab system, a fixed tab strip, panes that
  behave like one unit, an explorer the terminal follows.
- **Everything scriptable and owned** — small shell/Python scripts and a couple
  of surgical kitty patches, all in this repo, all editable.

## Done

- [x] Single-window layout: explorer · editor (tabs) · terminal
- [x] Custom clickable per-pane tab system (open / switch / close / `+ web`)
- [x] Crisp in-terminal browser — carbonyl + kitty graphics protocol ([PR #221](https://github.com/fathyb/carbonyl/pull/221))
- [x] Omnibox URL resolution for the browser ([PR #222](https://github.com/fathyb/carbonyl/pull/222))
- [x] Fast PDF viewing (tdf)
- [x] Fixed-height tab bar via kitty `fixed_lines` layout patch
- [x] Merged highlight — tab bar + editor read as one pane (`hlgroup` patch)
- [x] Explorer-following terminal with an idle guard — silent `cd`, never into a running session *and* never onto a half-typed command (OSC 133 prompt-mark check)
- [x] Hover-to-focus + keyboard pane/tab switching
- [x] Double-click in the explorer to activate an item (file → tab, dir → enter)
- [x] Right-click an explorer item to copy its path to the clipboard
- [x] Double-click a path segment in the explorer header to jump to that level
- [x] Idle ASCII animation (sleeping cat)
- [x] Patch persistence: kitty pinned + pacman re-apply hook
- [x] **Virtual desktop Claude can pilot** — a VNC-backed graphical environment
  (Docker X11 desktop now; QEMU VM / remote as drop-in providers) rendered live
  in the feed pane, with eyes (`ide-vd shot`) and hands (`ide-vd click/type/key`)
- [x] **VD senses + power** — video (`rec` → frames + montage), **ears**
  (`hear` → faster-whisper transcript + spectrogram, since Claude takes no
  audio/video), **GPU** (NVIDIA RTX via container toolkit + VirtualGL, `ide-vd gl`),
  and **persistence** (`down`=stop, `reset`, `commit`) for a self-configuring box

## Next / ideas

- **More openers** — image viewer tab (kitty icat / chafa), a git status/diff
  pane, a scratch/REPL tab, markdown preview.
- **carbonyl polish** — damage-based partial frame updates (scrolling currently
  re-blits the whole frame), and a configurable supersample factor.
- **Pure-art idle screen** option (drop the hint line); maybe a few rotating
  animations.
- **Portability** — parametrize the hardcoded paths in `ide.session` so the repo
  installs cleanly on another machine / for someone else.
- **Virtual-desktop polish** — damage-based partial frame updates for a higher
  feed/`rec` FPS (today it reblits whole frames); a provisioned QEMU VM env for
  testing other OSes (Windows/macOS); GPU-accelerated Chromium under headless X;
  combined audio+video capture in one `rec`; surfacing the desktop's clipboard.

## Notes for future sessions

- The kitty layout patch lives in `/usr/lib/kitty/kitty/layout/splits.py`
  (root-owned); the source of truth is `kitty/patches/apply-fixed-lines-patch.py`
  here. Edit the re-applier, not the installed file.
- carbonyl is a separate fork (`rsirjani/carbonyl`); this repo only carries the
  diff + notes. Rebuild = `cargo build` in that checkout, copy the `.so` over the
  installed one.
- See `docs/INTERNALS.md` for exactly how each patch works.
