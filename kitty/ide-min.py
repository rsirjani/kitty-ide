#!/usr/bin/env python3
# Minimizable IDE panes.
#
#   kitty @ action kitten ide-min.py show|hide|toggle explorer|vd|files [--auto]
#   kitty @ action kitten ide-min.py sync        (install the hook + relayout)
#   kitty @ action kitten ide-min.py uninstall   (restore stock layout, show all)
#
# A pane is minimized by setting the `minimized=1` user var on its window(s).
# kitty's splits layout has no notion of a hidden window, so this file teaches
# it one, at runtime: a split whose one side is entirely minimized hands the
# whole rectangle to the other side, and the minimized windows are marked
# invisible (not drawn, no mouse) and keep their last size, so the program in
# them sees no resize while hidden.
#
# The layout is hooked by wrapping methods of kitty.layout.splits in the running
# kitty rather than patching /usr/lib/kitty: no sudo, and it takes effect in an
# already-running IDE. The hook is installed two ways, both from this one file:
#   * as a watcher  (ide.session: `launch --watcher ide-min.py`) -> on_load(),
#     so a fresh IDE honours the session's `--var minimized=1` from frame one;
#   * as a kitten   (handle_result) on every call, so running IDEs pick it up.
# kitty re-executes this file per kitten call, so bumping VERSION re-wraps the
# saved originals and a running IDE takes an edit without a restart.
#
# --auto marks a change made by the IDE itself rather than the user: `show
# --auto` opens a pane because something opened in it (ide-open, the VD feed),
# and `hide --auto` closes it again only if it was opened that way -- a pane the
# user opened by hand stays open.
#
# The layout hook also mirrors the state to /tmp/ide-<kitty pid>-shown-<pane>
# (present = shown), so the Waybar buttons and the pane programs (the VD feed,
# the sleeping-cat home screen) can tell without asking kitty.
import os

VERSION = 3

# pane name -> the `pane` user var of the window(s) that make it up
PANES = {
    'explorer': ('explorer',),
    'vd': ('vd',),
    'files': ('tabbar', 'content'),
}
FLAG_OF = {'explorer': 'explorer', 'vd': 'vd', 'content': 'files'}
# With only the explorer open on the left, the left column shrinks to this
# fraction of the window instead of keeping the editor's width.
NARROW_BIAS = 0.24


def _vars(w):
    return getattr(w, 'user_vars', None) or {}


def _group_hidden(wg):
    if wg is None:
        return False
    for w in getattr(wg, 'windows', ()):
        if _vars(w).get('minimized') == '1':
            return True
    return False


def _child_hidden(child, groups):
    """Is everything under `child` (a group id, a Pair, or None) minimized?"""
    if child is None:
        return True
    if isinstance(child, int):
        return _group_hidden(groups.get(child))
    return _child_hidden(child.one, groups) and _child_hidden(child.two, groups)


def _group_ids(child):
    if child is None:
        return ()
    if isinstance(child, int):
        return (child,)
    return tuple(child.all_window_ids())


def _flag_path(pane):
    return f'/tmp/ide-{os.getpid()}-shown-{pane}'


def _sync_flags(layout, all_windows):
    state = {}
    for g in all_windows.groups:
        for w in g:
            name = FLAG_OF.get(_vars(w).get('pane', ''))
            if name:
                state[name] = not _group_hidden(g)
    if not state or state == getattr(layout, '_ide_shown', None):
        return
    for name, shown in state.items():
        path = _flag_path(name)
        if shown:
            open(path, 'w').close()
        else:
            try:
                os.remove(path)
            except FileNotFoundError:
                pass
    layout._ide_shown = state


def install():
    from kitty.layout import splits
    from kitty.layout.base import lgd, window_geometry_from_layouts
    Pair, Splits = splits.Pair, splits.Splits
    if getattr(Pair, '_ide_min_version', None) == VERSION:
        return
    orig = getattr(Pair, '_ide_min_orig', None)
    if orig is None:
        orig = Pair._ide_min_orig = {
            'layout_pair': Pair.layout_pair,
            'edge_border': Pair.edge_border,
            'corner_group_id': Pair.corner_group_id,
            'drag': Splits.drag_resize_target_windows,
        }
    o_layout, o_edge, o_corner, o_drag = (
        orig['layout_pair'], orig['edge_border'], orig['corner_group_id'], orig['drag'])

    def hidden_side(pair):
        """1 / 2 = that child was minimized at the last layout, 0 = neither."""
        h = getattr(pair, '_ide_hide', 0)
        return h if (h and pair.one is not None and pair.two is not None) else 0

    class as_single:
        """Temporarily present a pair with one minimized child as a pair with
        only its visible child, so kitty's own single-child code handles it."""

        def __init__(self, pair, hide):
            self.pair, self.hide = pair, hide

        def __enter__(self):
            p = self.pair
            self.saved = p.one, p.two
            p.one, p.two = (p.two if self.hide == 1 else p.one), None

        def __exit__(self, *a):
            self.pair.one, self.pair.two = self.saved

    def park(child, left, top, width, height, groups, layout_object):
        # A minimized subtree is not laid out. Drop the dividers it drew last
        # time, and give a window that has never been laid out (minimized from
        # launch) a real size so its program starts with a sane pty.
        if not isinstance(child, int) and child is not None:
            for p in child.self_and_descendants():
                p.between_borders = None
                p._ide_hide = 0
        border_mult = 0 if lgd.draw_minimal_borders else 1
        for gid in _group_ids(child):
            wg = groups.get(gid)
            if wg is None:
                continue
            g = wg.geometry
            if g is not None and g.xnum > 0 and g.ynum > 0:
                continue
            h = height
            fixed = splits.fixed_lines_for_child(gid, groups) if hasattr(splits, 'fixed_lines_for_child') else None
            if fixed:
                h = min(height, fixed * lgd.cell_height
                        + wg.decoration('top', border_mult=border_mult)
                        + wg.decoration('bottom', border_mult=border_mult))
            xl = next(layout_object.xlayout(iter((wg,)), start=left, size=width, border_mult=border_mult))
            yl = next(layout_object.ylayout(iter((wg,)), start=top, size=h, border_mult=border_mult))
            wg.set_geometry(window_geometry_from_layouts(xl, yl))

    def narrow_bias(pair, groups, layout_object):
        # Root split = left column | claude. When the explorer is the only
        # thing showing on the left, the column narrows; each mode remembers
        # its own divider position, so a drag in one mode survives the other.
        if layout_object.pairs_root is not pair or not pair.horizontal:
            return
        panes = set()
        for gid in _group_ids(pair.one):
            wg = groups.get(gid)
            if wg is not None and not _group_hidden(wg):
                panes.update(_vars(w).get('pane', '') for w in wg.windows)
        mode = 'narrow' if panes == {'explorer'} else 'wide'
        prev = getattr(pair, '_ide_mode', None)
        if prev is None:
            prev = 'wide'
            pair._ide_bias = {'narrow': NARROW_BIAS}
        if mode != prev:
            pair._ide_bias[prev] = pair.bias
            pair.bias = pair._ide_bias.get(mode, pair.bias)
        pair._ide_mode = mode

    def layout_pair(self, left, top, width, height, id_window_map, layout_object):
        hide = 0
        try:
            if self.one is not None and self.two is not None:
                h1 = _child_hidden(self.one, id_window_map)
                h2 = _child_hidden(self.two, id_window_map)
                if h1 != h2:
                    hide = 1 if h1 else 2
                else:
                    narrow_bias(self, id_window_map, layout_object)
        except Exception:
            hide = 0
        self._ide_hide = hide
        if not hide:
            return o_layout(self, left, top, width, height, id_window_map, layout_object)
        hidden = self.one if hide == 1 else self.two
        with as_single(self, hide):
            o_layout(self, left, top, width, height, id_window_map, layout_object)
        park(hidden, left, top, width, height, id_window_map, layout_object)

    def edge_border(self, which, id_group_map):
        hide = hidden_side(self)
        if not hide:
            return o_edge(self, which, id_group_map)
        with as_single(self, hide):
            return list(o_edge(self, which, id_group_map))

    def corner_group_id(self, which):
        hide = hidden_side(self)
        if not hide:
            return o_corner(self, which)
        with as_single(self, hide):
            return o_corner(self, which)

    def update_visibility(self, all_windows):
        active = all_windows.active_window
        for g in all_windows.groups:
            hidden = _group_hidden(g)
            top = g.active_window_id
            for w in g:
                w.set_visible_in_layout((not hidden) and (w is active or w.id == top))
        try:
            _sync_flags(self, all_windows)
        except Exception:
            pass

    def drag_resize_target_windows(self, click_window, x, y, edges, all_windows):
        # A split with a minimized side has no divider of its own; a drag that
        # kitty assigns to it belongs to the nearest enclosing split of the
        # same orientation that does.
        ans = o_drag(self, click_window, x, y, edges, all_windows)
        try:
            parent, by_id = {}, {}
            for p in self.pairs_root.self_and_descendants():
                by_id[id(p)] = p
                for c in (p.one, p.two):
                    if isinstance(c, Pair):
                        parent[c] = p

            def retarget(pid):
                p = by_id.get(pid)
                if p is None or not hidden_side(p):
                    return pid
                q = parent.get(p)
                while q is not None and (q.horizontal != p.horizontal or hidden_side(q)):
                    q = parent.get(q)
                return id(q) if q is not None else None
            ans = ans._replace(horizontal_id=retarget(ans.horizontal_id), vertical_id=retarget(ans.vertical_id))
        except Exception:
            pass
        return ans

    Pair.layout_pair = layout_pair
    Pair.edge_border = edge_border
    Pair.corner_group_id = corner_group_id
    Splits.update_visibility = update_visibility
    Splits.drag_resize_target_windows = drag_resize_target_windows
    Pair._ide_min_version = VERSION


def uninstall():
    """Put kitty's own layout methods back (escape hatch for a bad hook)."""
    from kitty.layout import splits
    Pair, Splits = splits.Pair, splits.Splits
    orig = getattr(Pair, '_ide_min_orig', None)
    if orig is None:
        return
    Pair.layout_pair = orig['layout_pair']
    Pair.edge_border = orig['edge_border']
    Pair.corner_group_id = orig['corner_group_id']
    Splits.drag_resize_target_windows = orig['drag']
    if 'update_visibility' in Splits.__dict__:
        del Splits.update_visibility
    Pair._ide_min_version = None


def on_load(boss, data):      # watcher entry point: runs once per kitty process
    install()


def main(args):
    pass  # never runs: no_ui kittens only use handle_result (the loader wants it)


def handle_result(args, answer, target_window_id, boss):
    cmd = args[1] if len(args) > 1 else 'sync'
    if cmd == 'uninstall':
        uninstall()
        for tab in boss.all_tabs:
            for win in tab:
                win.set_user_var('minimized', None)
            tab.relayout()
        return
    install()
    pane = args[2] if len(args) > 2 else ''
    auto = '--auto' in args
    w = boss.window_id_map.get(target_window_id)
    tab = (w.tabref() if w is not None else None) or boss.active_tab
    if tab is None:
        return
    wins = {}
    for win in tab:
        name = _vars(win).get('pane')
        if name:
            wins.setdefault(name, win)
    targets = [wins[n] for n in PANES.get(pane, ()) if n in wins]
    if cmd in ('show', 'hide', 'toggle') and targets:
        lead = targets[0]
        hidden = any(_vars(t).get('minimized') == '1' for t in targets)
        want = {'show': False, 'hide': True, 'toggle': not hidden}[cmd]
        if auto and want and _vars(lead).get('min_auto') != '1':
            want = hidden          # opened by the user -> not ours to close
        if want != hidden:
            for t in targets:
                t.set_user_var('minimized', '1' if want else None)
            lead.set_user_var('min_auto', '1' if (auto and not want) else None)
        elif not auto and not want:
            lead.set_user_var('min_auto', None)   # the user asked for it: theirs now
    # never leave keyboard focus in a pane that isn't on screen
    aw = tab.active_window
    if aw is not None and 'claude' in wins and _group_hidden(tab.windows.group_for_window(aw)):
        tab.set_active_window(wins['claude'])
    tab.relayout()


handle_result.no_ui = True
