# carbonyl — crisp in-terminal browser

The IDE's browser tab is [carbonyl](https://github.com/fathyb/carbonyl), a
Chromium-based terminal browser, **patched to render at full resolution via the
kitty graphics protocol** instead of downsampling to blocky half-blocks.

Stock carbonyl gets the full framebuffer from Blink and then throws the
resolution away — averaging each 2×4 pixel block into one quantized cell. The
patch adds a `--graphics` mode that:

- renders the whole page (text included) into the framebuffer (bitmap mode),
- blits that framebuffer via the kitty graphics protocol — crisp text and
  images, straight from Chromium,
- supersamples the device-pixel-ratio so it's sharp,
- matches the framebuffer aspect ratio to the real terminal cell so it isn't
  vertically stretched,
- streams frames through a temp file to keep the pty fast.

It also adds omnibox-style address-bar resolution: a bare host like
`youtube.com` becomes `https://youtube.com`, and free text becomes a search.

## Zoom & pan (graphics mode)

Small text? The graphics view doubles as a magnifier — it shows a cropped
sub-region of the framebuffer scaled to fill the same cells, so it reflows
nothing and needs no Chromium rebuild:

| Key | Action |
|-----|--------|
| `Ctrl+Up` / `Ctrl+Down` | zoom in / out (1×–6×, centered) |
| `Ctrl+Left` | reset to the whole page |
| arrows (while zoomed) | pan around |

Zoom keys are ignored while the address bar is focused, and the plain arrows
only pan once you're zoomed in — otherwise they pass through to the page. The
view is supersampled (2× DPI), so a couple of steps of zoom stay crisp.

## Highlight, copy & paste

Works like a normal browser:

| Action | How |
|--------|-----|
| highlight | click-drag over text (Blink selects, the highlight renders in the view) |
| copy | `Ctrl+C` |
| paste | `Ctrl+V` |

The catch is that in graphics mode Blink rasterizes page text straight into the
framebuffer — the selected *text* never reaches the terminal, so there's nothing
local to put on the clipboard. So carbonyl drives the very Chromium it's
rendering: the launcher starts it with an auto-picked, localhost-only
`--remote-debugging-port` (against the tab's throwaway `--user-data-dir`), and a
tiny built-in CDP client reads `window.getSelection()` on copy and pushes it to
the system clipboard via the OSC 52 terminal escape. Paste runs in reverse —
ask the terminal for the clipboard (OSC 52), then `Input.insertText` into the
focused field.

Notes:
- **`Ctrl+C` no longer quits** the browser (it copies). Close the tab to quit.
- Copy needs no terminal config (OSC 52 *write* is allowed by default). Paste
  needs the terminal to answer OSC 52 *read* requests — the IDE's `ide.conf`
  enables `clipboard_control ... read-clipboard`, so it's seamless inside the
  IDE. In a stock kitty you'd otherwise get a per-paste permission prompt.

## Where it lives

The patch is **not vendored** here — it's a fork with upstream PRs:

- Fork: [`rsirjani/carbonyl`](https://github.com/rsirjani/carbonyl)
- Graphics renderer → [fathyb/carbonyl#221](https://github.com/fathyb/carbonyl/pull/221)
- Address-bar omnibox → [fathyb/carbonyl#222](https://github.com/fathyb/carbonyl/pull/222)

[`kitty-ide-carbonyl.patch`](kitty-ide-carbonyl.patch) is the combined diff
(graphics + aspect fix + omnibox + zoom/pan + clipboard) for reference.

## Building / installing

Only the Rust core needs building — the prebuilt Chromium runtime dlopens it, so
**no Chromium build**:

```sh
git clone https://github.com/rsirjani/carbonyl ~/src/carbonyl
cd ~/src/carbonyl
git checkout local-ide        # graphics + aspect + omnibox merged
cargo build                   # ~seconds
# drop the rebuilt core into a release carbonyl:
cp build/debug/libcarbonyl.so \
   "$(npm root -g)/carbonyl/node_modules/@fathyb/carbonyl-linux-amd64/build/libcarbonyl.so"
```

Then `carbonyl --graphics <url>` renders crisply, and the IDE's `+ web` button
uses it automatically (`ide-open browser`).

> Trade-off: it's still a terminal — resolution is capped at the cell grid times
> the supersample factor. Text and most pages are very readable; this won't
> replace a GPU browser for pixel-perfect work.
