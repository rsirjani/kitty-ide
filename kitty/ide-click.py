#!/usr/bin/env python3
# Click-to-place-caret for the claude pane.
#
# Claude Code's input editor is keyboard-only and doesn't grab the mouse, so a
# click in its input box normally does nothing. But Claude Code keeps the real
# terminal cursor parked on its input caret (hidden, positioned for IME), so
# the distance from the caret to the clicked cell is computable from the screen
# text — the same press arithmetic ide-seldel.py uses (ide_caret.py) — and one
# burst of arrow keys moves the caret there.
#
# Mapped as kitty's `left click ungrabbed` action, which is global (mouse_map
# has no --when-focus-on), so it reproduces kitty's default click first: an
# existing selection or a link under the mouse wins, and any other window just
# gets the stock `mouse_handle_click selection link prompt`. Only a plain click
# inside the claude pane's input box moves the caret; elsewhere in the pane
# (the transcript, a bare shell) the default handling runs.
#
# The burst goes out in one write; afterwards the caret's resting position is
# checked and, if the arithmetic was off (an unusual wrap, a char wcwidth
# disagrees on), a corrective burst or two closes the gap. A miss is harmless —
# arrows only move the caret — so there is no slow walker to fall back to.

from kitty.fast_data_types import add_timer, click_mouse_url

import ide_caret
if getattr(ide_caret, 'VERSION', 0) < 3:   # kitty caches the module: pick up fixes live
    import importlib
    importlib.reload(ide_caret)
from ide_caret import TEXT_COL, input_rows, presses_between, row_len_cells, snap_to_cluster, visual_rows

TICK = 0.008            # seconds between cursor polls
PRESS_WAIT = 0.09       # a press not dragged into a selection by then is a click
REST_TICKS = 6          # cursor still this long on target => arrived
CORRECT_REST_TICKS = 40 # still this long OFF target => burst is spent, correct
SETTLE_TICKS = 150      # burst never came to rest => give up
MAX_CORRECTIONS = 2     # corrective bursts after the first one misses
MAX_PRESSES = 4000      # absolute cap on synthesized keypresses per click

# Set on the kitty Window while a move is in flight. Not a module-level set:
# kitty re-executes a custom kitten's file on every invocation, so module state
# doesn't survive from one click to the next.
BUSY_ATTR = 'ide_click_busy'


def editable_box(rows, cy):
    """(first, last) rows of Claude Code's input box around the caret, or None.
    Stricter than input_rows alone: the `❯` row must sit right under the box's
    `───` rule. Claude's selection menus (permission prompts and the like) also
    mark the current option with `❯`, and arrows there would change the choice."""
    box = input_rows(rows, cy)
    if box is None:
        return None
    top = box[0]
    if top == 0 or rows[top - 1][:1] != '─':
        return None
    return box


def caret_target(rows, cols, cy, cx, left_half, tcol=TEXT_COL):
    """The caret position a click on cell (cy, cx) asks for, plus every cell
    that same position can render as. Like any text field: a click on the
    right half of a character puts the caret after it, a click past the end of
    a row puts it at the row's end, a click in the prompt/indent margin puts it
    at the row's start."""
    row = rows[cy]
    rl = row_len_cells(row)
    if cx < tcol or rl <= tcol:
        x = tcol
    elif cx >= rl:
        x = rl
    else:
        s, e = snap_to_cluster(row, cx) or (cx, cx)
        offset = cx - s + (0 if left_half else 0.5)
        x = e + 1 if offset >= (e - s + 1) / 2 else s
    target = (cy, x)
    if x >= cols:
        # past the last cell of a row filled to the edge: that position is the
        # next row's first, which is where claude draws the caret
        return target, (target, (cy + 1, tcol))
    return target, (target,)


def without_caret_cell(rows, cur):
    """The rows with claude's caret cell dropped when it's parked past the end
    of its row. Claude draws the caret there as an extra space cell, which would
    count as one more character to cross (and push a past-the-end click one
    press too far, onto the next row)."""
    cy, cx = cur
    if not 0 <= cy < len(rows):
        return rows
    row = rows[cy]
    if not row.endswith(' ') or row_len_cells(row) != cx + 1:
        return rows
    return rows[:cy] + [row[:-1]] + rows[cy + 1:]


def text_of(rows):
    """Screen text minus trailing blanks: claude draws a caret parked at the
    end of a row as an extra space cell, which isn't an edit."""
    return [r.rstrip() for r in rows]


def plan(rows, cols, cur, target):
    """The key presses that move the caret from cur to target. Rows are crossed
    with up/down -- one press per row, where left/right would need a whole
    row's worth each (and the classic renderer draws every one, so the caret
    visibly crawls) -- then the column is closed on the target row. Claude
    keeps the caret's column on a vertical move, clamped to the row's end."""
    (r0, x0), (r1, x1) = cur, target
    keys = []
    if r1 != r0:
        keys += ['up' if r1 < r0 else 'down'] * abs(r1 - r0)
        row = rows[r1] if 0 <= r1 < len(rows) else ''
        x0 = max(min(x0, row_len_cells(row)), TEXT_COL)
    row = rows[r1] if 0 <= r1 < len(rows) else ''
    if x1 < x0:
        keys += ['left'] * presses_between(row, x1, x0)
    elif x1 > x0:
        keys += ['right'] * presses_between(row, x0, x1)
    return keys


class CaretMove:
    def __init__(self, boss, window_id, target, lands, keys):
        self.boss = boss
        self.wid = window_id
        self.target = target
        self.lands = lands          # cells the caret may rest on once it arrives
        self.keys = keys            # the next burst to send
        self.burst_sent = False
        self.burst_from = None
        self.text = None            # screen text when the burst went out
        self.last_pos = None
        self.stable = 0
        self.settle = 0
        self.corrections = 0
        self.sent = 0

    def __call__(self, timer_id=None):
        done = True
        try:
            done = self.tick()
        except Exception:
            import traceback
            from kitty.utils import log_error
            log_error('ide-click: ' + traceback.format_exc())
        if done:
            w = self.boss.window_id_map.get(self.wid)
            if w is not None:
                setattr(w, BUSY_ATTR, False)
        else:
            add_timer(self, TICK, False)

    def tick(self):
        """One poll. Returns True when the caret move is finished."""
        w = self.boss.window_id_map.get(self.wid)
        if w is None:
            return True
        pos = (w.screen.cursor.y, w.screen.cursor.x)
        if not self.burst_sent:
            keys = self.keys[:max(MAX_PRESSES - self.sent, 0)]
            if not keys:
                return True
            self.sent += len(keys)
            self.burst_sent = True
            self.burst_from = self.last_pos = pos
            self.text = text_of(visual_rows(w))
            self.stable = self.settle = 0
            w.send_key(*keys)
            return False
        # Judge where the caret comes to REST, not what a single poll shows:
        # claude coalesces repaints, so during a long burst the caret is drawn
        # jumping several cells and may pause between chunks.
        if pos != self.last_pos:
            self.last_pos = pos
            self.stable = 0
        else:
            self.stable += 1
        self.settle += 1
        if self.settle > SETTLE_TICKS:
            return True
        if self.stable < REST_TICKS or pos == self.burst_from:
            return False
        if pos in self.lands:
            return True
        # Off target. Claude can stall mid-burst for longer than REST_TICKS, and
        # a correction sent then would stack on the presses still in flight and
        # overshoot — so only a long stillness counts as the burst being spent.
        if self.stable < CORRECT_REST_TICKS:
            return False
        rows = visual_rows(w)
        if self.corrections >= MAX_CORRECTIONS or text_of(rows) != self.text:
            # The text changed under us: the user is already typing at wherever
            # the caret went, and moving it now would scatter their keystrokes.
            return True
        # landed elsewhere: the remaining distance is measurable now, close it
        self.keys = plan(without_caret_cell(rows, pos), w.screen.columns, pos, self.target)
        if not self.keys:
            return True
        self.corrections += 1
        self.burst_sent = False
        return False


def place_caret(boss, w):
    """Move claude's input caret to the clicked cell. False when the click
    wasn't in an input box (so kitty's default click handling should run)."""
    screen = w.screen
    mouse = w.current_mouse_position()
    if not mouse or screen.scrolled_by:
        return False
    cy, cx = mouse['cell_y'], mouse['cell_x']
    cur = (screen.cursor.y, screen.cursor.x)
    rows = without_caret_cell(visual_rows(w), cur)
    box = editable_box(rows, cur[0])
    if box is None or not box[0] <= cy <= box[1] or cy >= len(rows):
        return False
    if getattr(w, BUSY_ATTR, False):
        return True  # a move is still settling; a fresh burst would race it
    target, lands = caret_target(rows, screen.columns, cy, cx, mouse['in_left_half_of_cell'])
    if cur in lands:
        return True
    keys = plan(rows, screen.columns, cur, target)
    if not keys:
        return True
    _trace('  caret', cur, '->', target, len(keys), 'keys')
    setattr(w, BUSY_ATTR, True)
    add_timer(CaretMove(boss, w.id, target, lands, keys), 0, False)
    return True


def main(args):
    pass  # never runs: no_ui kittens only use handle_result (loader wants it)


def _trace(*a):
    """One line per event in /tmp/ide-kittens.log -- cheap, and the only way to
    see afterwards what a click / delete in a live session actually did."""
    try:
        import time
        with open('/tmp/ide-kittens.log', 'a') as f:
            f.write(f"{time.strftime('%H:%M:%S')} {time.monotonic():.3f} " + ' '.join(str(x) for x in a) + '\n')
    except OSError:
        pass


def handle_result(args, answer, target_window_id, boss):
    w = boss.window_id_map.get(target_window_id)
    if w is None:
        return
    _trace('click', args[1:], 'pane', w.user_vars.get('pane'), 'sel', w.screen.has_selection(),
           'cur', (w.screen.cursor.y, w.screen.cursor.x))
    if len(args) > 1 and args[1] == 'press':
        # Fast path. kitty reports a "click" only once its double-click window
        # (click_interval, ~half a second) has passed, which made every caret
        # move feel laggy -- and it never reports the release of a plain left
        # button at all while it is tracking a possible drag. The press is the
        # only prompt event there is. So this mapping replaces kitty's default
        # for it (start a selection -- done first, unconditionally), and a
        # moment later, if no drag has turned it into a selection, treats it as
        # a click and moves the caret. The later click event then finds the
        # caret already in place.
        try:
            from kitty.fast_data_types import MOUSE_SELECTION_NORMAL
            w.mouse_selection(MOUSE_SELECTION_NORMAL)
        except Exception:
            import traceback
            from kitty.utils import log_error
            log_error('ide-click: ' + traceback.format_exc())
        if w.user_vars.get('pane') == 'claude':
            def later(timer_id=None):
                try:
                    win = boss.window_id_map.get(target_window_id)
                    if win is not None and not win.screen.has_selection():
                        place_caret(boss, win)
                except Exception:
                    import traceback
                    from kitty.utils import log_error
                    log_error('ide-click: ' + traceback.format_exc())
            add_timer(later, PRESS_WAIT, False)
        return
    if w.user_vars.get('pane') != 'claude':
        w.mouse_handle_click('selection', 'link', 'prompt')
        return
    if w.screen.has_selection():
        return  # a drag-select just ended: keep it (ide-seldel acts on it)
    if click_mouse_url(w.os_window_id, w.tab_id, w.id):
        return
    try:
        if place_caret(boss, w):
            return
    except Exception:
        # this kitten IS the IDE's left click: a bug here must cost the caret
        # move, never the click itself
        import traceback
        from kitty.utils import log_error
        log_error('ide-click: ' + traceback.format_exc())
    w.mouse_handle_click('prompt')


handle_result.no_ui = True
