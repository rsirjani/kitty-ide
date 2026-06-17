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
(`ide-open` → `kitty @ launch --type=overlay --var doc=1 …`). Overlays keep the
kitty graphics protocol intact (needed for carbonyl/tdf), which a multiplexer
would strip. `ide-tabbar` lists every overlay with `doc=1` as a tab, highlights
the focused one, and on click focuses it / closes it / opens a browser.
`ide-tab` does the same by keyboard; `ide-pane` switches between the three zones.

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

## Patch persistence

kitty updates are disabled (`IgnorePkg = kitty`). If kitty is ever reinstalled,
`pacman/zz-kitty-fixed-lines.hook` re-runs the re-applier (idempotent; applies
both patches; bails safely if kitty's source changed too much). Original backed
up as `splits.py.orig`. Manual re-apply:
`sudo python3 ~/.config/kitty/patches/apply-fixed-lines-patch.py`.

## Explorer-following terminal

Two parts:

1. `yazi/init.lua` subscribes to yazi's `cd` event and writes the current dir to
   `/tmp/ide-explorer-cwd` on every move.
2. `ide-cwd-follow` (started by the explorer pane, exits when the IDE socket is
   gone) watches that file. On change it `cd`s the claude pane to match — **only
   if** that pane's foreground process is a bare shell (idle), and it isn't there
   already. If a `claude`/command is running, it does nothing, so the `cd`
   keystrokes never land inside a running program.

The `cd` is sent via `kitty @ send-text`. Known edge: the idle check looks at the
foreground process, so a *typed-but-unrun* command at the prompt could get a
`cd` appended. Rare in practice; see ROADMAP for the tighter guard.

## The browser

`ide-open browser [url]` launches `carbonyl --graphics` as an overlay tab.
`--graphics` is the patched mode (see `carbonyl/`) that renders the page via the
kitty graphics protocol — full resolution, real fonts — instead of half-blocks,
and resolves bare hosts / search queries in the address bar. Default page is
arXiv.

## Known gaps

- **Hover resize-cursor** on the fixed tab-bar border: the drag is a no-op but
  the cursor still shows the resize shape — that detection is kitty compiled-C,
  not reachable from Python. Needs a kitty source patch + local build.
- **carbonyl scrolling** re-blits the whole frame each repaint (fine for normal
  browsing; heavy for fast animations). Damage-based partial updates are a TODO.
