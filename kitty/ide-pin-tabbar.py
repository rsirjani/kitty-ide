# kitty window watcher: pin the IDE tab bar to a single row.
#
# kitty's splits layout sizes panes proportionally and has no fixed-size window,
# so the border under the tab bar would resize it. This watcher fires on every
# resize of the tab-bar window (kitty dispatches on_resize before the next
# render) and shrinks it straight back to one row; the editor pane below absorbs
# the change. Net effect: the tab bar's height is constant — only the
# explorer/editor border actually moves things.

_busy = False


def on_resize(boss, window, data):
    global _busy
    if _busy:
        return
    try:
        if (window.user_vars or {}).get('pane') != 'tabbar':
            return
        ng = data.get('new_geometry')
        rows = getattr(ng, 'ynum', None)
        if not rows or rows <= 1:
            return
        tab = window.tabref()
        if tab is None:
            return
        _busy = True
        # Drive back to one row. resize_window_by relayouts and re-fires
        # on_resize, so this converges over a couple of (pre-render) ticks.
        tab.resize_window_by(window.id, 1 - rows, False)
    except Exception:
        pass
    finally:
        _busy = False
