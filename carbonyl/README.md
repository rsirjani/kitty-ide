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

Frames are transmitted incrementally: each new framebuffer is diffed against
the previous one — identical frames send nothing, small changes (a caret
blink, a hover) go out as a kitty *frame edit* of just the changed rows, and
zoom/pan re-send only the placement, no pixels. Full retransmits happen only
on first paint, resize, or when most of the frame changed (e.g. scrolling).

## Address bar

A real text field, like a browser omnibox:

| Action | How |
|--------|-----|
| focus + select whole URL | click the bar (type to replace it) |
| place the caret | click again |
| select a range | click-drag |
| select all | `Ctrl+A` |
| copy / paste selection | `Ctrl+C` / `Ctrl+V` (OSC 52) |
| start / end | `Up` / `Down` |
| navigate | `Enter` |

## Keychain, 2FA verifier & real-browser handoff

`ide-open browser` runs carbonyl through `ide-web`, which starts a sidecar
(`ide-web-agent`, Node, zero deps) on carbonyl's DevTools port:

- **Keychain** — focus a login field and a dropdown lists the saved accounts
  for that site (click or ↑/↓ + Enter). After you sign in with new or changed
  credentials it asks to Save / Update / Never. Two-step logins (email page,
  then password page) are tracked. Signup fields offer a generated strong
  password, saved automatically on submit.
- **Verifier** — 2FA/OTP fields (incl. one-digit-per-box widgets) offer the
  current TOTP code for the site.
- `Ctrl+L` opens the dropdown on the focused (or first) login field.
- **Identity** — carbonyl no longer announces itself as a bot: a standard
  Chrome user agent with client hints, no `navigator.webdriver`, and the usual
  `window.chrome`/plugins surface. It reports its real engine version (111).

The UI runs in an isolated world in a closed shadow root, so page scripts
can't read it or trigger it; fills are typed as trusted input. A frame only
ever gets credentials for its own site (stored `example.com` also serves
`login.example.com`, never `example.com.evil.io`).

Secrets live in the system keyring (secret-tool). Manage them with
`ide-web-cred add|rm|ls`, `ide-web-cred totp add <host>` (paste a base32
secret or `otpauth://` URI) and `ide-web-cred code <host>`.

`Ctrl+O` hands the URL to `$CARBONYL_OPEN_EXTERNAL_CMD` (default
`ide-open desktop`): the real Chromium inside the Virtual Desktop, with a
persistent profile — use it for passkeys, OAuth popups, and sites that still
refuse this browser.

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
(graphics + incremental frames + aspect fix + omnibox + URL-bar text field +
zoom/pan + clipboard + keyring autofill + VD handoff) for reference.

## Building / installing

Only the Rust core needs building — the prebuilt Chromium runtime dlopens it, so
**no Chromium build**:

```sh
git clone https://github.com/rsirjani/carbonyl ~/src/carbonyl
cd ~/src/carbonyl
git checkout local-ide        # graphics + aspect + omnibox merged
cargo build --release         # ~seconds; release matters — the blit path is hot
# drop the rebuilt core into a release carbonyl:
cp build/release/libcarbonyl.so \
   "$(npm root -g)/carbonyl/node_modules/@fathyb/carbonyl-linux-amd64/build/libcarbonyl.so"
```

Then `carbonyl --graphics <url>` renders crisply, and the IDE's `+ web` button
uses it automatically (`ide-open browser`).

> Trade-off: it's still a terminal — resolution is capped at the cell grid times
> the supersample factor. Text and most pages are very readable; this won't
> replace a GPU browser for pixel-perfect work.
