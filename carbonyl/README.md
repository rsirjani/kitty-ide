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

## Where it lives

The patch is **not vendored** here — it's a fork with upstream PRs:

- Fork: [`rsirjani/carbonyl`](https://github.com/rsirjani/carbonyl)
- Graphics renderer → [fathyb/carbonyl#221](https://github.com/fathyb/carbonyl/pull/221)
- Address-bar omnibox → [fathyb/carbonyl#222](https://github.com/fathyb/carbonyl/pull/222)

[`kitty-ide-carbonyl.patch`](kitty-ide-carbonyl.patch) is the combined diff
(graphics + aspect fix + omnibox) for reference.

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
