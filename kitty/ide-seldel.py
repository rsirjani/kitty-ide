#!/usr/bin/env python3
# Mouse-select-then-delete for the claude pane.
#
# Claude Code's input editor is keyboard-only: a kitty mouse selection is
# invisible to it. But two facts make select-to-delete possible from the
# outside: kitty knows exactly which screen cells are selected
# (screen.current_selections()), and Claude Code keeps the *real* terminal
# cursor parked on the input caret (hidden with ?25l, but positioned — for
# IME support). So this kitten, mapped to backspace/delete in the claude
# pane, does:
#
#   1. no selection → pass the key straight through (send_key respects the
#      window's keyboard-protocol mode, so this is transparent).
#   2. selection → walk the caret to the selection start with arrow keys,
#      verifying against the live cursor position after every batch; then
#      walk right through the selection *counting actual keypresses*; then
#      send that many backspaces in one write.
#
# The walk is self-limiting: anything the caret cannot reach — chat history
# above the input, the "❯ " prompt, blank padding, status lines below — makes
# an arrow press move nothing, which reads as a boundary. So what gets
# deleted is exactly the intersection of the selection with the editable
# text, and a selection entirely outside the input deletes nothing (the
# caret is walked back to where it started). Wrong deletions are undoable
# with Claude Code's input undo (ctrl+_).
#
# Batches are sized from the screen text itself: each row is split into
# grapheme-ish clusters (wcwidth; zero-width chars attach to the previous
# cell) so wide chars cost one press, not two. Row crossings are ambiguous
# (soft wrap = 0 presses, real newline = 1), so they are probed one press at
# a time and counted only if the cursor actually moved. Decisions are only
# taken once the cursor has been stable for a few ticks, so a half-painted
# frame is never mistaken for a caret position.
#
# Also works on the bare zsh prompt in the same pane (zle has the same
# arrow/backspace semantics).

from kitty.fast_data_types import add_timer, wcwidth

TICK = 0.015          # seconds between cursor polls
STABLE_TICKS = 3      # cursor unchanged this long => frame settled, safe to act
STALL_TICKS = 20      # a sent press moved nothing for this long => boundary
MAX_TOTAL_TICKS = 600 # ~9s hard stop for the whole operation
MAX_PRESSES = 4000    # absolute cap on synthesized keypresses

active = set()  # window ids with an operation in flight
DEBUG = False


def clusters(text):
    """[(start_cell, cell_width)] per grapheme-ish cluster of a screen row."""
    out = []
    cell = 0
    for ch in text:
        w = wcwidth(ord(ch))
        if w <= 0:
            if out:
                continue  # combining/zero-width: part of the previous cluster
            w = 1
        out.append((cell, w))
        cell += w
    return out


def presses_between(text, a, b):
    """How many caret presses cross cells [a, b) of this row (cluster starts)."""
    return sum(1 for s, _ in clusters(text) if a <= s < b)


def snap_to_cluster(text, cell):
    """(start, end_inclusive) cells of the cluster containing `cell`, or None."""
    for s, w in clusters(text):
        if s <= cell < s + w:
            return s, s + w - 1
    return None


class SelDel:
    def __init__(self, boss, window_id, sel_first, sel_last, orig):
        self.boss = boss
        self.wid = window_id
        self.sel_first = sel_first  # (y, x) first selected cell, row-major
        self.sel_last = sel_last    # (y, x) last selected cell
        self.orig = orig            # caret position before we touched anything
        self.phase = 'to_start'
        self.count = 0              # confirmed presses across the selection
        self.pending = 0            # presses sent, counted only once movement is seen
        self.sent = 0
        self.ticks = 0
        self.await_from = None      # position a sent batch should move us off of
        self.wait = 0
        self.last_pos = None
        self.stable = 0

    # -- plumbing -------------------------------------------------------

    def __call__(self, timer_id=None):
        done = True
        try:
            done = self.tick()
        except Exception:
            import traceback
            from kitty.utils import log_error
            log_error('ide-seldel: ' + traceback.format_exc())
        if done:
            active.discard(self.wid)
        else:
            add_timer(self, TICK, False)

    def window(self):
        return self.boss.window_id_map.get(self.wid)

    def rows(self, w):
        return w.as_text().split('\n')

    def send(self, w, key, n):
        n = min(n, MAX_PRESSES - self.sent)
        if n <= 0:
            return False
        self.sent += n
        if DEBUG:
            from kitty.utils import log_error
            log_error(f'seldel: {self.phase} pos={self.last_pos} send {key}x{n} count={self.count}')
        w.send_key(*([key] * n))
        return True

    # -- main loop ------------------------------------------------------

    def tick(self):
        """One poll. Returns True when the whole operation is finished."""
        self.ticks += 1
        w = self.window()
        if w is None or self.ticks > MAX_TOTAL_TICKS or self.sent >= MAX_PRESSES:
            return True
        pos = (w.screen.cursor.y, w.screen.cursor.x)

        if self.await_from is not None:
            if pos != self.await_from:
                # the batch landed; credit probes and re-enter the stability gate
                self.count += self.pending
                self.pending = 0
                self.await_from = None
                self.last_pos = pos
                self.stable = 0
                return False
            self.wait += 1
            if self.wait > STALL_TICKS:
                self.pending = 0
                self.await_from = None
                return self.on_stall(w)
            return False

        # act only on a settled frame (claude repaints asynchronously)
        if pos != self.last_pos:
            self.last_pos = pos
            self.stable = 0
            return False
        self.stable += 1
        if self.stable < STABLE_TICKS:
            return False
        return self.step(w, pos)

    def await_movement(self, pos):
        self.await_from = pos
        self.wait = 0
        return False

    def on_stall(self, w):
        # an arrow press moved nothing: the caret hit an edge of the editable text
        if self.phase == 'to_start':
            self.phase = 'count'   # start deleting from wherever we could reach
            return False
        if self.phase == 'count':
            return self.finish(w)  # end of text: selection ran past it
        return True                # restore: best effort, stop here

    def step(self, w, pos):
        if self.phase in ('to_start', 'restore'):
            return self.walk_to(w, pos)
        if self.phase == 'count':
            return self.count_through(w, pos)
        return True

    # -- phases ---------------------------------------------------------

    def walk_to(self, w, pos):
        rows = self.rows(w)
        ty, tx = self.sel_first if self.phase == 'to_start' else self.orig
        if self.phase == 'to_start':
            # deleting half a wide char is impossible: aim at its start
            row = rows[ty] if ty < len(rows) else ''
            snapped = snap_to_cluster(row, tx)
            if snapped is not None:
                tx = snapped[0]
        if pos == (ty, tx):
            if self.phase == 'to_start':
                self.phase = 'count'
                return False
            return self.finish(w)
        going_left = pos > (ty, tx)
        key = 'left' if going_left else 'right'
        row = rows[pos[0]] if pos[0] < len(rows) else ''
        if pos[0] == ty:
            a, b = (tx, pos[1]) if going_left else (pos[1], tx)
            k = presses_between(row, a, b) or 1
        elif going_left:
            # target is on another row: clear this row first. Indent cells can
            # inflate the count (small overshoot); the same-row pass corrects it.
            k = presses_between(row, 0, pos[1]) or 1
        else:
            row_cells = sum(cw for _, cw in clusters(row))
            k = presses_between(row, pos[1], row_cells) or 1
        if not self.send(w, key, k):
            return True
        return self.await_movement(pos)

    def count_through(self, w, pos):
        rows = self.rows(w)
        ey, ex = self.sel_last
        erow = rows[ey] if ey < len(rows) else ''
        snapped = snap_to_cluster(erow, ex)
        e_end = snapped[1] if snapped is not None else ex
        if pos > (ey, e_end):
            return self.finish(w)
        row = rows[pos[0]] if pos[0] < len(rows) else ''
        row_cells = sum(cw for _, cw in clusters(row))
        limit = min(e_end + 1, row_cells) if pos[0] == ey else row_cells
        k = presses_between(row, pos[1], limit)
        if k > 0:
            # real on-screen text: these presses are guaranteed to consume chars
            self.pending = k
        else:
            self.pending = 1  # boundary probe: counted only if the caret moves
            k = 1
        if not self.send(w, 'right', k):
            return True
        return self.await_movement(pos)

    def finish(self, w):
        if self.count > 0:
            w.screen.clear_selection()
            self.send(w, 'backspace', self.count)
            return True
        if self.phase != 'restore':
            # nothing deletable in the selection: put the caret back
            w.screen.clear_selection()
            self.phase = 'restore'
            return False
        return True


def main(args):
    pass  # never runs: no_ui kittens only use handle_result (loader wants it)


def handle_result(args, answer, target_window_id, boss):
    w = boss.window_id_map.get(target_window_id)
    if w is None:
        return
    key = args[1] if len(args) > 1 else 'backspace'
    if target_window_id in active:
        return  # an operation is in flight; swallow the repeat
    screen = w.screen
    sel = screen.current_selections() if screen.has_selection() else b''
    if not any(sel) or screen.is_rectangle_select() or screen.scrolled_by:
        w.send_key(key)  # no actionable selection: behave like a normal keypress
        return
    cols = screen.columns
    first = last = None
    for i, b in enumerate(sel):
        if b:
            if first is None:
                first = i
            last = i
    # the bitmap has extra row(s) at the top (partially-visible line slot):
    # its row index is shifted vs cursor/as_text coordinates
    row_off = len(sel) // cols - screen.lines

    def cell(i):
        y, x = divmod(i, cols)
        return y - row_off, x

    orig = (screen.cursor.y, screen.cursor.x)
    machine = SelDel(boss, target_window_id, cell(first), cell(last), orig)
    active.add(target_window_id)
    add_timer(machine, 0, False)


handle_result.no_ui = True
