#!/usr/bin/env python3
# Re-apply local patches to kitty's splits layout for the kitty IDE.
#
# 1. fixed_lines: a window with the `fixed_lines=N` user var gets a constant
#    height, ignoring the split bias, so its border is a no-op (used for the
#    tab-bar strip).
# 2. hlgroup: windows sharing the `hlgroup` user var are highlighted as one box
#    (active border around the union, no divider between them) so the tab bar +
#    editor look like a single pane.
#
# kitty updates are disabled (IgnorePkg in pacman.conf), but pacman would still
# overwrite /usr/lib/kitty if kitty were ever reinstalled, so a pacman hook runs
# this (as root). It is idempotent and bails out safely if kitty's source has
# changed too much to patch.

import shutil
import sys

TARGET = "/usr/lib/kitty/kitty/layout/splits.py"
TARGET_WINDOW = "/usr/lib/kitty/kitty/window.py"

# ---- patch 1: fixed_lines ---------------------------------------------------
HELPER = '''

def fixed_lines_for_child(child: object, id_window_map: dict[int, WindowGroup]) -> Optional[int]:
    # If `child` is a single window (a group id, not a Pair) whose window sets
    # the `fixed_lines` user var, return that line count. Such a window is given
    # a constant height by the layout regardless of the split bias, so the
    # border next to it never resizes it. Used e.g. for a fixed tab-bar strip.
    if not isinstance(child, int):
        return None
    wg = id_window_map.get(child)
    if wg is None:
        return None
    for w in getattr(wg, 'windows', ()):  # noqa
        try:
            v = (w.user_vars or {}).get('fixed_lines')
        except Exception:
            v = None
        if v:
            try:
                n = int(v)
            except Exception:
                n = 0
            if n > 0:
                return n
    return None
'''

ANCHOR_IMPORT = (
    "from .base import BorderLine, Layout, LayoutOpts, blank_rects_for_window, "
    "lgd, window_geometry_from_layouts\n"
)

OLD_H1 = """            h1 = max(min_h1, int(self.bias * height) - bw)
            h2 = height - h1 - bw2
            if h2 < min_h2 and h1 >= min_h1 + bw2:
                h2 = min_h2
                h1 = height - h2
            btop = top + h1"""

NEW_H1 = """            h1 = max(min_h1, int(self.bias * height) - bw)
            # Honor a fixed-height window: give it a constant number of rows and
            # let the other child take the rest, ignoring the bias. This makes
            # the border next to it a no-op while the outer border still resizes.
            fl1 = fixed_lines_for_child(self.one, id_window_map)
            fl2 = fixed_lines_for_child(self.two, id_window_map)
            if fl1 is not None:
                wgf = id_window_map[self.one]
                deco = wgf.decoration('top', border_mult=border_mult) + wgf.decoration('bottom', border_mult=border_mult)
                h1 = min(height - bw2 - min_h2, fl1 * lgd.cell_height + deco)
            elif fl2 is not None:
                wgf = id_window_map[self.two]
                deco = wgf.decoration('top', border_mult=border_mult) + wgf.decoration('bottom', border_mult=border_mult)
                h1 = max(min_h1, height - bw2 - (fl2 * lgd.cell_height + deco))
            h2 = height - h1 - bw2
            if fl1 is None and fl2 is None and h2 < min_h2 and h1 >= min_h1 + bw2:
                h2 = min_h2
                h1 = height - h2
            btop = top + h1"""

# ---- patch 2: hlgroup merged highlight --------------------------------------
OLD_BORDERS = """        needs_borders_map = all_windows.compute_needs_borders_map(lgd.draw_active_borders)
        ag = all_windows.active_group
        active_group_id = -1 if ag is None else ag.id

        border_color_map = {}
        for grp_id, needs_borders in needs_borders_map.items():
            if needs_borders:
                wid = g.active_window_id if (g := all_windows.group_for_id(grp_id)) else 0
                if wid:
                    color = BorderColor.active if grp_id is active_group_id else BorderColor.bell
                    border_color_map[wid] = color

        for pair in self.pairs_root.self_and_descendants():
            if pair.between_borders:
                for which in pair.between_borders:
                    for bb in which:
                        yield bb._replace(color=border_color_map.get(abs(bb.window_id), BorderColor.inactive))"""

NEW_BORDERS = """        needs_borders_map = all_windows.compute_needs_borders_map(lgd.draw_active_borders)
        ag = all_windows.active_group
        active_group_id = -1 if ag is None else ag.id

        # Windows that share a `hlgroup` user var are highlighted as one box:
        # when any member is active they all get the active border, and the
        # border *between* two members is suppressed so they look like one pane.
        hlgroup_by_gid: dict[int, str] = {}
        for g in groups:
            for w in g:
                try:
                    v = (w.user_vars or {}).get('hlgroup')
                except Exception:
                    v = None
                if v:
                    hlgroup_by_gid[g.id] = v
                    break
        active_hl = hlgroup_by_gid.get(active_group_id)

        border_color_map = {}
        for grp_id, needs_borders in needs_borders_map.items():
            if needs_borders:
                wid = g.active_window_id if (g := all_windows.group_for_id(grp_id)) else 0
                if wid:
                    color = BorderColor.active if grp_id is active_group_id else BorderColor.bell
                    border_color_map[wid] = color
        if active_hl is not None:
            for g in groups:
                if hlgroup_by_gid.get(g.id) == active_hl and g.active_window_id:
                    border_color_map[g.active_window_id] = BorderColor.active

        for pair in self.pairs_root.self_and_descendants():
            if pair.between_borders:
                if (isinstance(pair.one, int) and isinstance(pair.two, int)
                        and (hl := hlgroup_by_gid.get(pair.one)) is not None
                        and hl == hlgroup_by_gid.get(pair.two)):
                    continue  # same highlight group -> no divider between members
                for which in pair.between_borders:
                    for bb in which:
                        yield bb._replace(color=border_color_map.get(abs(bb.window_id), BorderColor.inactive))"""

# ---- patch 3: fix inverted divider drag direction ---------------------------
# In the splits layout a mouse-dragged divider always belongs to the *targeted
# pair*, and `pair.bias` is the fraction given to that pair's leading (left/top)
# child. So increasing the bias always moves the divider toward bottom/right --
# i.e. toward the cursor. The drag is therefore always "forwards".
#
# Upstream computes a per-drag `fwd` sign via `size_increases_forwards()`. That
# heuristic returns False (inverting the drag) when the divider under the cursor
# belongs to an *ancestor* pair of the opposite orientation and the grabbed
# window sits in that ancestor's leading subtree. In the IDE that is exactly the
# top-band / editor divider: grab it from the `tabbar` (below) and it tracks the
# mouse, but grab the same line from `vd` or `explorer` (above) and it inverts --
# the "sometimes the panes move the opposite way" bug. (The same flaw inverts the
# left-column / claude divider when grabbed from the tabbar's right edge.)
#
# The WindowResizeDragData defaults are already True and the upstream special
# case hardcodes True; only the generic path is wrong. Pin both signs to True so
# a dragged divider always follows the cursor, regardless of which pane edge it
# was grabbed from. Pair *selection* is unchanged -- only the direction sign.
OLD_DRAG = """            if ans.horizontal_id is None and p.horizontal:
                p, fwd = pair_or_parent(p)
                ans = ans._replace(horizontal_id=id(p), width_increases_rightwards=fwd)
            if ans.vertical_id is None and not p.horizontal:
                p, fwd = pair_or_parent(p)
                ans = ans._replace(vertical_id=id(p), height_increases_downwards=fwd)"""

NEW_DRAG = """            if ans.horizontal_id is None and p.horizontal:
                p, fwd = pair_or_parent(p)
                # kitty-ide: a dragged divider always follows the cursor (bias is
                # the leading child's fraction), so the sign is always forward.
                ans = ans._replace(horizontal_id=id(p), width_increases_rightwards=True)
            if ans.vertical_id is None and not p.horizontal:
                p, fwd = pair_or_parent(p)
                ans = ans._replace(vertical_id=id(p), height_increases_downwards=True)"""

# ---- patch 4: live_resize opt-out of resize-notification pausing ------------
# During an interactive split-border drag, kitty pauses resize notifications to
# every child (boss.py drag_resize_*), so a pane gets no SIGWINCH / PTY resize
# until the drag *ends*. For the VD feed that means its kitty-graphics frame is
# drawn at the last transmitted pixel size over a now-differently-sized window --
# it freezes then clips ("bugged out") for the whole drag. A pane that sets the
# `live_resize` user var opts out of the pause, so it keeps receiving SIGWINCH on
# every drag step and the feed re-fits its frame to track the drag smoothly. Only
# opted-in panes are affected; text panes keep the smooth paused behaviour.
OLD_PAUSE = """    def pause_resize_notifications_to_child(self, pause: bool = True) -> None:
        if pause:
            if self._pause_resize_notifications_to_child is None:
                self._pause_resize_notifications_to_child = -1, -1, -1, -1"""

NEW_PAUSE = """    def pause_resize_notifications_to_child(self, pause: bool = True) -> None:
        if pause:
            # kitty-ide: a pane with the `live_resize` user var opts OUT of the
            # resize pause, so it keeps getting SIGWINCH live during a border drag
            # (the VD feed uses this to re-fit its graphics to the pane each step).
            if (self.user_vars or {}).get('live_resize'):
                return
            if self._pause_resize_notifications_to_child is None:
                self._pause_resize_notifications_to_child = -1, -1, -1, -1"""

# (target, marker, anchor, replacement). marker present + replacement present
# => already applied. Patches are grouped by target file below.
PATCHES = [
    (TARGET, "fixed_lines_for_child", ANCHOR_IMPORT, ANCHOR_IMPORT + HELPER),
    (TARGET, "fixed_lines_for_child", OLD_H1, NEW_H1),  # marker shared; both go in together
    (TARGET, "hlgroup_by_gid", OLD_BORDERS, NEW_BORDERS),
    (TARGET, "width_increases_rightwards=True", OLD_DRAG, NEW_DRAG),
    (TARGET_WINDOW, "get('live_resize')", OLD_PAUSE, NEW_PAUSE),
]

# files whose __pycache__ must be cleared after a write
_PYCACHE = {
    TARGET: "/usr/lib/kitty/kitty/layout/__pycache__",
    TARGET_WINDOW: "/usr/lib/kitty/kitty/__pycache__",
}


def main() -> int:
    # group patches by target file so each file is read once / written once
    targets: dict[str, list] = {}
    for target, marker, anchor, replacement in PATCHES:
        targets.setdefault(target, []).append((marker, anchor, replacement))

    rc = 0
    for target, patches in targets.items():
        try:
            with open(target, encoding="utf-8") as f:
                src = f.read()
        except OSError as e:
            print(f"kitty-ide patch: cannot read {target}: {e}", file=sys.stderr)
            continue

        changed = False
        for marker, anchor, replacement in patches:
            if marker in src and replacement in src:
                continue  # this piece already applied
            if anchor not in src:
                print(f"kitty-ide patch: anchor for {marker} not found in "
                      f"{target}, kitty source changed; skipping.",
                      file=sys.stderr)
                continue
            src = src.replace(anchor, replacement, 1)
            changed = True

        if not changed:
            continue

        try:
            with open(target, "w", encoding="utf-8") as f:
                f.write(src)
        except OSError as e:
            print(f"kitty-ide patch: cannot write {target}: {e}", file=sys.stderr)
            continue

        shutil.rmtree(_PYCACHE.get(target, ""), ignore_errors=True)
        print(f"kitty-ide patch: applied to {target}")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
