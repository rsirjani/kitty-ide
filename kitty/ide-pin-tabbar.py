# kitty window watcher: pin a thin IDE strip to a fixed number of rows.
#
# kitty's splits layout sizes panes proportionally and has no fixed-size window,
# so the border next to a one-row strip (the editor tab bar, the claude header)
# would resize it. This watcher fires on every resize of any window carrying a
# `fixed_lines=N` user var (kitty dispatches on_resize before the next render)
# and shrinks it straight back to N rows; the neighbouring pane absorbs the
# change. Net effect: the strip's height is constant — only the other,
# non-fixed borders actually move things.

_busy = False


def on_resize(boss, window, data):
    global _busy
    if _busy:
        return
    try:
        fl = (window.user_vars or {}).get('fixed_lines')
        if not fl:
            return
        try:
            target = int(fl)
        except (TypeError, ValueError):
            return
        ng = data.get('new_geometry')
        rows = getattr(ng, 'ynum', None)
        if not rows or rows <= target:
            return
        tab = window.tabref()
        if tab is None:
            return
        _busy = True
        # Drive back to one row. resize_window_by relayouts and re-fires
        # on_resize, so this converges over a couple of (pre-render) ticks.
        tab.resize_window_by(window.id, target - rows, False)
    except Exception:
        pass
    finally:
        _busy = False
