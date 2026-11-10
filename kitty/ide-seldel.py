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
#   2. clip the selection to the editable block — Claude's input box, or the
#      caret's logical line at a bare shell prompt. A selection that misses
#      it entirely (the transcript, the status lines) is simply dropped.
#   3. selection on the caret's own row → blind one-shot: arrows + backspaces
#      in a single write, no timer at all.
#   4. otherwise → verified one-shot: ONE arrow burst to just past the end of
#      the selection, ONE landing check against the live cursor, ONE burst of
#      backspaces. Two writes, no visible caret crawl.
#   5. only when that landing check fails does the slow walker run: the caret
#      is walked batch by batch, each step verified against the live cursor,
#      counting the presses it actually takes to cross the selection, then
#      that many backspaces.
#
# Everything is counted from the screen text: rows are split into grapheme-ish
# clusters (wcwidth; zero-width chars attach to the previous cell) so a wide
# char costs one press, not two, and each row boundary costs one press only
# when the row stopped short of the screen edge — a row filled to the edge
# wrapped mid-text and its end is adjacent to the next row's start.
#
# Safety comes from the arithmetic being checkable rather than trusted: the
# backspaces only go out once the caret has landed exactly where the count
# predicted. Anything unreachable by the caret — chat history, the "❯ "
# prompt, padding, status lines — either gets clipped away in step 2 or makes
# the landing check fail, which means a walk, and a walk stalls at the
# boundary rather than deleting past it. Wrong deletions remain undoable with
# Claude Code's input undo (ctrl+_).
#
# Also works on the bare zsh prompt in the same pane (zle has the same arrow
# and backspace semantics). Deletion is always expressed as backspaces, never
# forward-delete: zsh here takes `CSI 3~` apart and inserts a literal `~` per
# press, while backspace is understood everywhere and is a no-op at column 0.

from kitty.fast_data_types import add_timer

import ide_caret
if getattr(ide_caret, 'VERSION', 0) < 2:   # kitty caches the module: pick up fixes live
    import importlib
    importlib.reload(ide_caret)
from ide_caret import (
    TEXT_COL, boundary_cost, cell_width, clusters, input_rows, logical_run,
    presses_between, presses_over, row_len_cells, snap_to_cluster, visual_rows,
)

TICK = 0.008          # seconds between cursor polls
STABLE_TICKS = 2      # cursor unchanged this long => frame settled, safe to act
STALL_TICKS = 15      # a sent press moved nothing for this long => boundary
MAX_TOTAL_TICKS = 1100 # ~9s hard stop for the whole operation
MAX_PRESSES = 4000    # absolute cap on synthesized keypresses
JUMP_REST_TICKS = 6   # cursor still this long after a burst => claude is done
JUMP_SETTLE_TICKS = 90 # one-shot burst never came to rest => walk it
MAX_JUMP_CORRECTIONS = 2  # corrective bursts before giving up on the one-shot

active = set()  # window ids with an operation in flight
DEBUG = False


def plan_oneshot(rows, cols, cur, sel_first, sel_last, tcol=TEXT_COL):
    """Precompute the whole edit as (arrow_key, n_arrows, landings, n_del):
    one arrow burst to just past the end of the selection, a landing check,
    one burst of backspaces. The landing check is the safety valve — the
    backspaces only go out if the caret arrived exactly where the press
    arithmetic predicted, so a wrong assumption (an unreachable selection, an
    editor that moves the caret its own way) degrades to the slow-but-verified
    walker rather than to a wrong deletion.

    Everything is expressed as backspaces on purpose: forward-delete is not
    universally understood — zsh here takes `CSI 3~` apart and inserts a
    literal `~` per press — while backspace works in every line editor, and
    backspacing at position 0 is a harmless no-op."""
    (sr, sx), (er, ex) = sel_first, sel_last
    segs = []
    for r in range(sr, er + 1):
        row = rows[r] if 0 <= r < len(rows) else ''
        rl = row_len_cells(row)
        a = max(sx if r == sr else tcol, tcol)
        b = min(ex + 1 if r == er else rl, rl)
        n = 0
        if b > a:
            s1 = snap_to_cluster(row, a)
            if s1 is not None:
                a = s1[0]
            s2 = snap_to_cluster(row, b - 1)
            if s2 is not None:
                b = s2[1] + 1
            n = presses_between(row, a, b)
        segs.append((r, a, b, n))
    live = [s for s in segs if s[3] > 0]
    if not live:
        return None
    fr, fa = live[0][0], live[0][1]
    lr, lb = live[-1][0], live[-1][2]
    n_del = sum(s[3] for s in live) + sum(
        boundary_cost(rows, r, cols) for r in range(fr, lr))
    end = (lr, lb)  # the position just past the last selected character
    lr_len = row_len_cells(rows[lr]) if 0 <= lr < len(rows) else 0
    # a position at a row's end can render as the next row's start
    end_alt = (lr + 1, tcol) if lb >= lr_len else None
    lands = (end,) + ((end_alt,) if end_alt is not None else ())
    # The caret is used exactly as rendered — no guessing about which side of a
    # wrap it "really" sits on. A wrong guess would inflate the arrow count
    # (e.g. onto a UI row above the input) and the burst would overshoot; an
    # ambiguous-but-honest position just fails the landing check instead.
    if cur >= end:
        return ('left', presses_over(rows, cols, end, cur, tcol), lands, n_del)
    return ('right', presses_over(rows, cols, cur, end, tcol), lands, n_del)


class SelDel:
    def __init__(self, boss, window_id, sel_first, sel_last, orig, plan=None, tcol=TEXT_COL):
        self.boss = boss
        self.wid = window_id
        self.sel_first = sel_first  # (y, x) first selected cell, row-major
        self.sel_last = sel_last    # (y, x) last selected cell
        self.orig = orig            # caret position before we touched anything
        self.plan = plan            # precomputed one-shot, or None
        self.tcol = tcol            # first editable column of a wrapped row
        self.jump_sent = False
        self.jump_from = None
        self.expect_move = False
        self.corrections = 0
        self.settle = 0
        self.phase = 'jump' if plan else 'to_start'
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
            w = self.boss.window_id_map.get(self.wid)
            if w is not None:
                w._ide_seldel_busy = 0
        else:
            add_timer(self, TICK, False)

    def window(self):
        return self.boss.window_id_map.get(self.wid)

    def rows(self, w):
        return visual_rows(w)

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

        if self.phase == 'jump':
            # checked every tick, not just on settled frames: the caret sweeps
            # through many positions while claude digests the burst, and a
            # momentary pause mid-sweep must not be mistaken for the landing
            return self.jump(w, pos)

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

    def jump(self, w, pos):
        """Send the arrow burst, wait for the caret to come to rest, then check
        where it rests. Resting position — not any position seen in passing —
        is what matters: Claude coalesces repaints, so a long burst often shows
        the caret jumping several cells at a time and the predicted cell may
        never be rendered at all. (Watching for it tick by tick is what made
        the first, longest jump of a session fall through to the walker.)"""
        key, n, lands, n_del = self.plan
        if not self.jump_sent:
            self.jump_sent = True
            self.jump_from = pos
            self.last_pos = pos
            self.settle = self.stable = 0
            self.expect_move = n > 0 and self.send(w, key, n)
            if self.expect_move:
                return False
        if pos != self.last_pos:
            self.last_pos = pos
            self.stable = 0
        else:
            self.stable += 1
        self.settle += 1
        if self.settle > JUMP_SETTLE_TICKS:
            self.fall_back()  # never came to rest: hand it to the walker
            return False
        if self.stable < JUMP_REST_TICKS or (self.expect_move and pos == self.jump_from):
            # Claude renders a long burst in chunks and can pause between them,
            # so "unchanged for a couple of polls" is not yet "arrived"
            return False
        if pos in lands:
            # the caret came to rest exactly where the press arithmetic said it
            # would: the counts are validated, delete in one write
            w.screen.clear_selection()
            self.send(w, 'backspace', n_del)
            return True
        # It stopped somewhere else, so some assumption was off. The distance
        # left is measurable now, so try closing it — a miscount of one or two
        # presses is far likelier than the whole model being wrong, and one
        # more burst is still invisible next to the walker's crawl.
        if self.corrections >= MAX_JUMP_CORRECTIONS:
            self.fall_back()
            return False
        target = lands[0]
        rows = self.rows(w)
        if pos > target:
            key, n = 'left', presses_over(rows, w.screen.columns, target, pos, self.tcol)
        else:
            key, n = 'right', presses_over(rows, w.screen.columns, pos, target, self.tcol)
        if n <= 0:
            self.fall_back()
            return False
        self.corrections += 1
        self.jump_sent = False
        self.plan = (key, n, lands, n_del)
        return False

    def fall_back(self):
        self.phase = 'to_start'
        self.last_pos = None
        self.stable = 0
        self.await_from = None

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


def try_fast_path(w, sel_first, sel_last):
    """Selection confined to the caret's own row: the arrow/backspace count is
    exactly computable from the cluster map, so the whole edit goes out in one
    write — no walking, no timer, visually instant. This is the common case
    (sweeping part of the line being typed). Anything else — other rows,
    selections touching the prompt/indent margin — returns False and takes the
    verified walker. Extra backspaces can't escape the input box (backspace at
    position 0 is a no-op), so the one-shot is as safe as the walker here."""
    screen = w.screen
    cy, cx = screen.cursor.y, screen.cursor.x
    (sy, sx), (ey, ex) = sel_first, sel_last
    if not (sy == ey == cy):
        return False
    if sx < 2:  # prompt "❯ " / wrap indent: needs the boundary-probing walker
        return False
    rows = visual_rows(w)
    row = rows[cy] if cy < len(rows) else ''
    row_cells = sum(cw for _, cw in clusters(row))
    snapped = snap_to_cluster(row, sx)
    if snapped is None:
        return False  # selection starts past the text: nothing deletable
    sx = snapped[0]
    snapped = snap_to_cluster(row, min(ex, row_cells - 1))
    if snapped is None:
        return False
    end = snapped[1]
    if end < sx:
        return False
    n_del = presses_between(row, sx, end + 1)
    if n_del <= 0:
        return False
    if cx <= end:
        keys = ['right'] * presses_between(row, cx, end + 1)
    else:
        keys = ['left'] * presses_between(row, end + 1, cx)
    keys += ['backspace'] * n_del
    screen.clear_selection()
    w.send_key(*keys)
    return True


# ---- marker-verified delete (Claude Code's input box) ------------------------
#
# The arithmetic above cannot be trusted across rows of Claude's input box: a
# row break may or may not hide a character (a word wrap swallows a space, a
# hard break of a long token hides nothing, and the two can look alike), and --
# worse -- "the caret stopped moving" is not "Claude finished": a busy Claude
# Code pauses mid-burst for longer than any sane timeout, and acting on that
# pause deleted text in the wrong place.
#
# So for the box nothing is assumed. Keys are processed in order, so a typed
# character that shows up on screen is proof every key before it was handled:
#
#   1. move to the end of the selection, type a marker, wait until it is ON
#      SCREEN, and check it sits right after the last selected character
#      (corrected and retried if not; removed and abandoned if it can't be);
#   2. same at the start of the selection, with a second marker;
#   3. forward-delete what is visibly between the two markers -- never more
#      than is on screen, so the end marker cannot be overrun -- and repeat
#      until they touch; then remove the markers.
#
# Worst case is a no-op (markers removed), not a wrong deletion.

MARKS = '§¤¶‡†ǂ¦¬±µ'
ACK_TICKS = 300        # ~2.4s for a marker to appear before giving up
SETTLE_TICKS = 2       # frame unchanged this long before it is read
DOUBT_TICKS = 15       # ...and this long before a marker is declared misplaced
MAX_FIX = 2            # corrective moves per marker
MAX_ROUNDS = 8         # delete rounds


def _row_cells(row, r, tcol):
    out, cell = [], 0
    for ch in row:
        wd = cell_width(ch)
        if wd <= 0:
            if out:
                continue
            wd = 1
        if cell >= tcol:
            out.append((ch, r, cell))
        cell += wd
    return out


def _is_space(ch):
    return ch in ' \xa0'


class Box:
    """The editable text of the input box as cells in reading order."""

    def __init__(self, w, marks=''):
        screen = w.screen
        self.cols = screen.columns
        self.cur = (screen.cursor.y, screen.cursor.x)
        rows = visual_rows(w)
        box = input_rows(rows, self.cur[0])
        self.ok = box is not None
        self.cells = []
        if not self.ok:
            return
        self.top, self.bot = box
        for r in range(self.top, self.bot + 1):
            rc = _row_cells(rows[r], r, TEXT_COL)
            # Claude draws the caret at the end of a row as a space cell
            if rc and (r, rc[-1][2]) == self.cur and _is_space(rc[-1][0]):
                rc.pop()
            self.cells.extend(rc)
        self.marks = marks

    def find(self, mark):
        hits = [i for i, c in enumerate(self.cells) if c[0] == mark]
        return hits[0] if len(hits) == 1 else None

    def is_mark(self, i):
        return self.cells[i][0] in self.marks

    def k_before(self, i):
        """Real (non-space, non-marker) characters before point i."""
        return sum(1 for j in range(i) if not _is_space(self.cells[j][0]) and not self.is_mark(j))

    def caret_index(self):
        for i, (_, r, x) in enumerate(self.cells):
            if (r, x) >= self.cur:
                return i
        return len(self.cells)

    def bcost(self, r):
        """Guess: presses hidden in the break after row r. A word wrap swallowed
        a space (1); a token too long for a line was hard-broken (0)."""
        last = [c for c in self.cells if c[1] == r and c[0] not in self.marks]
        nxt = [c for c in self.cells if c[1] == r + 1 and c[0] not in self.marks]
        if not last or not nxt:
            return 1
        a = 0
        for c in reversed(last):
            if _is_space(c[0]):
                break
            a += 1
        b = 0
        for c in nxt:
            if _is_space(c[0]):
                break
            b += 1
        return 0 if a + b >= self.cols - TEXT_COL - 2 else 1

    def presses(self, a, b):
        """Estimated caret presses between points a <= b."""
        n = b - a
        for j in range(a, min(b, len(self.cells) - 1)):
            if self.cells[j][1] != self.cells[j + 1][1]:
                n += self.bcost(self.cells[j][1])
        return n

    def row_end(self, r):
        """Column just past the last character of row r."""
        xs = [c[2] + max(cell_width(c[0]), 1) for c in self.cells if c[1] == r]
        return max(xs) if xs else TEXT_COL

    def travel(self, frm, to):
        """Keys that move the caret from cell `frm` to cell `to` ((row, col)).
        Rows are crossed with up/down -- one press per row instead of a whole
        row of left/right presses, which is what made a delete far from the
        caret take hundreds of keys -- then the column is closed on the target
        row. Claude keeps the caret's column when moving vertically, clamped
        to the row's end. Any misjudgement is caught by the marker check."""
        (r0, x0), (r1, x1) = frm, to
        keys = []
        if r1 != r0:
            keys += ['up' if r1 < r0 else 'down'] * abs(r1 - r0)
            x0 = max(min(x0, self.row_end(r1)), TEXT_COL)
        lo, hi = min(x0, x1), max(x0, x1)
        n = sum(1 for c in self.cells if c[1] == r1 and lo <= c[2] < hi)
        keys += ['left' if x1 < x0 else 'right'] * n
        return keys

    def after(self, i):
        """Cell position of the caret sitting just after cell i."""
        ch, r, x = self.cells[i]
        return (r, x + max(cell_width(ch), 1))

    def neighbour(self, i, step, row):
        """Nearest non-marker cell from point i going left (-1) / right (+1),
        if it is on `row`; else None."""
        j = i - 1 if step < 0 else i
        while 0 <= j < len(self.cells) and self.is_mark(j):
            j += step
        if 0 <= j < len(self.cells) and self.cells[j][1] == row:
            return j
        return None

    def run(self, j, step):
        """(length, hit_row_edge) of the run of spaces at j going `step`."""
        row, n = self.cells[j][1], 0
        while 0 <= j < len(self.cells) and self.cells[j][1] == row:
            if self.is_mark(j):
                j += step
                continue
            if not _is_space(self.cells[j][0]):
                return n, False
            n += 1
            j += step
        return n, True


class Want:
    """Where a marker must sit: after (side=-1) or before (side=+1) a given
    character, identified by its ordinal among the box's real characters --
    which survives any re-wrapping the marker itself causes."""

    def __init__(self, box, i, side):
        self.side = side
        self.k = box.k_before(i)
        row = box.cells[i - 1][1] if side < 0 else box.cells[i][1]
        j = box.neighbour(i, side, row)
        self.ch = box.cells[j][0]
        self.run = box.run(j, side) if _is_space(self.ch) else None

    def at(self, box, i, row):
        if box.k_before(i) != self.k:
            return False
        j = box.neighbour(i, self.side, row)
        if j is None:
            return True          # at a row edge: the break may hide the rest
        if box.cells[j][0] != self.ch and not (_is_space(box.cells[j][0]) and _is_space(self.ch)):
            return False
        if self.run is not None:
            got = box.run(j, self.side)
            if not got[1] and not self.run[1] and got[0] != self.run[0]:
                return False
        return True

    def locate(self, box):
        """A point on the current screen that satisfies this, or None."""
        n = len(box.cells)
        for i in range(n + 1):
            if self.side < 0 and i == 0 or self.side > 0 and i == n:
                continue
            row = box.cells[i - 1][1] if self.side < 0 else box.cells[i][1]
            if self.at(box, i, row):
                return i
        return None


class MarkDel:
    def __init__(self, boss, wid, sel_first, sel_last):
        self.boss, self.wid = boss, wid
        self.sel_first, self.sel_last = sel_first, sel_last
        self.phase = 'start'
        self.ticks = self.wait = 0
        self.fixes = 0
        self.rounds = 0
        self.await_mark = None
        self.used = ''
        self.guards = 0        # markers sitting just left of the caret
        self.last_sig, self.still = None, 0
        self.mE = self.mS = None

    def __call__(self, timer_id=None):
        done = True
        try:
            done = self.tick()
        except Exception:
            import traceback
            from kitty.utils import log_error
            log_error('ide-seldel: ' + traceback.format_exc())
        w = self.boss.window_id_map.get(self.wid)
        if done:
            if w is not None:
                w._ide_seldel_busy = 0
        else:
            add_timer(self, TICK, False)

    def log(self, *a):
        _trace('  markdel', *a)
        if DEBUG:
            from kitty.utils import log_error
            log_error('seldel: ' + ' '.join(str(x) for x in a))

    def new_mark(self, box):
        present = {c[0] for c in box.cells}
        for m in MARKS:
            if m not in present and m not in self.used:
                self.used += m
                return m
        return None

    def send(self, w, keys=(), mark=None):
        """Send keys then (optionally) a marker, and wait for the marker."""
        self.log(self.phase, 'send', _summ(keys), 'mark', mark)
        if keys:
            w.send_key(*keys)
        if mark:
            w.write_to_child(mark.encode())
            self.await_mark = mark
            self.wait = 0
        return False

    def tick(self):
        w = self.boss.window_id_map.get(self.wid)
        self.ticks += 1
        if w is None or self.ticks > MAX_TOTAL_TICKS:
            return True
        box = Box(w, self.used)
        if not box.ok:
            # a frame without the input box (mid-repaint): wait, don't bail
            self.wait += 1
            if self.wait > ACK_TICKS:
                self.log('no input box on screen; giving up in phase', self.phase)
            return self.wait > ACK_TICKS
        if self.await_mark is not None:
            if box.find(self.await_mark) is None:
                self.wait += 1
                if self.wait > ACK_TICKS:        # never showed up: stop, touch nothing
                    self.log('marker', self.await_mark, 'never appeared in phase', self.phase)
                return self.wait > ACK_TICKS
            self.await_mark = None
        # Only read a frame that has held still: Claude repaints in pieces, and
        # a half-drawn box (marker present, text not yet reflowed, cursor parked
        # elsewhere) must not be judged.
        sig = (box.cur, tuple(box.cells))
        if sig != self.last_sig:
            self.last_sig, self.still = sig, 0
        else:
            self.still += 1
        if self.still < SETTLE_TICKS:
            return False
        self.log('tick', self.phase, 'cur', box.cur, 'cells', len(box.cells))
        return getattr(self, 'on_' + self.phase)(w, box)

    # -- 1. end marker ----------------------------------------------------

    def on_start(self, w, box):
        cells = box.cells
        fi = next((i for i, c in enumerate(cells) if (c[1], c[2]) >= self.sel_first), None)
        li = next((i for i in range(len(cells) - 1, -1, -1) if (cells[i][1], cells[i][2]) <= self.sel_last), None)
        if fi is None or li is None or fi > li:
            return True
        self.want_e = Want(box, li + 1, -1)
        self.want_s = Want(box, fi, +1)
        self.mE = self.new_mark(box)
        if self.mE is None:
            return True
        self.phase = 'end'
        return self.send(w, box.travel(box.cur, box.after(li)), self.mE)

    def on_end(self, w, box):
        m = box.find(self.mE)
        if self.want_e.at(box, m, box.cells[m][1]):
            self.fixes = 0
            self.mS = self.new_mark(box)
            tgt = self.want_s.locate(box)
            if self.mS is None or tgt is None or tgt > m:
                self.send(w, ['backspace'])
                return True
            self.phase = 'begin'
            return self.send(w, box.travel(box.after(m), box.cells[tgt][1:]), self.mS)
        return self.fix(w, box, m, self.want_e, 'mE')

    def fix(self, w, box, m, want, attr):
        if self.still < DOUBT_TICKS:
            return False     # be sure the frame is final before undoing anything
        return self.refit(w, box, m, want, attr)

    def refit(self, w, box, m, want, attr):
        """The marker is in the wrong place: take it out, close the measured
        gap, put a fresh one in. Out of tries -> take it out and stop."""
        self.fixes += 1
        tgt = want.locate(box)
        new = self.new_mark(box) if (self.fixes <= MAX_FIX and tgt is not None) else None
        if new is None:
            return self.abandon(w, box, attr)
        # after the backspace the caret is at point m of the list without it
        if tgt > m:
            keys = ['right'] * box.presses(m + 1, tgt)
        else:
            keys = ['left'] * box.presses(tgt, m)
        setattr(self, attr, new)
        return self.send(w, ['backspace'] + keys, new)

    def abandon(self, w, box, attr):
        self.log('abandon', attr)
        if attr == 'mE':
            self.send(w, ['backspace'])
            return True
        # the start marker failed: remove it, then go back and remove the end one
        s, e = box.find(self.mS), box.find(self.mE)
        probe = self.new_mark(box)
        if e is None or s is None or probe is None:
            self.send(w, ['backspace'])
            return True
        self.phase = 'cleanup'
        self.mP = probe
        return self.send(w, ['backspace'] + ['right'] * box.presses(s + 1, e + 1), probe)

    def on_cleanup(self, w, box):
        p, e = box.find(self.mP), box.find(self.mE)
        if e is None:
            self.send(w, ['backspace'])
        elif p == e + 1:
            self.send(w, ['backspace', 'backspace'])
        elif p == e - 1:
            self.send(w, ['backspace', 'delete'])
        elif box.cells[p][1] == box.cells[e][1]:
            # same row: the distance is exact
            if p < e:
                self.send(w, ['backspace'] + ['right'] * (e - p) + ['backspace'])
            else:
                self.send(w, ['backspace'] + ['left'] * (p - e - 1) + ['backspace'])
        else:
            self.send(w, ['backspace'])      # leave the stray end marker; undo removes it
        return True

    # -- 2. start marker --------------------------------------------------

    def on_begin(self, w, box):
        s = box.find(self.mS)
        if self.want_s.at(box, s, box.cells[s][1]):
            self.guards = 1
            self.left = self.mS
            self.phase = 'delete'
            return self.on_delete(w, box)
        return self.fix(w, box, s, self.want_s, 'mS')

    # -- 3. delete between the markers -------------------------------------

    def on_delete(self, w, box):
        x, e = box.find(self.left), box.find(self.mE)
        if x is None or e is None or e <= x:
            return True
        n = e - x - 1                      # cells visibly between caret and end marker
        if box.cells[x][1] == box.cells[e][1]:
            # same row: exact. Guards, the rest, and the end marker in one write.
            self.send(w, ['backspace'] * self.guards + ['delete'] * (n + 1))
            return True
        self.rounds += 1
        mark = self.new_mark(box)
        if self.rounds > MAX_ROUNDS or mark is None:
            return self.give_up_delete(w)
        if n == 0:
            # only a row break between them: does it hide a character? Step
            # over it and see which side of the end marker we come out on.
            self.phase = 'probe'
            self.mP = mark
            return self.send(w, ['right'], mark)
        self.left = mark
        self.guards += 1
        return self.send(w, ['delete'] * n, mark)

    def on_probe(self, w, box):
        p, e = box.find(self.mP), box.find(self.mE)
        if e is not None and p == e + 1:       # no hidden character
            self.send(w, ['backspace'] * (2 + self.guards))
        elif e is not None and p == e - 1:     # stepped over a hidden one
            self.send(w, ['backspace'] * (2 + self.guards) + ['delete'])
        else:
            self.send(w, ['backspace'])
        return True

    def give_up_delete(self, w):
        self.send(w, ['backspace'] * self.guards)
        return True


def _summ(keys):
    out, prev, n = [], None, 0
    for k in list(keys) + [None]:
        if k == prev:
            n += 1
            continue
        if prev is not None:
            out.append(f'{prev}x{n}')
        prev, n = k, 1
    return ' '.join(out)



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
    _trace('seldel', args[1:], 'sel', w.screen.has_selection(), 'busy', getattr(w, '_ide_seldel_busy', 0) > 0,
           'cur', (w.screen.cursor.y, w.screen.cursor.x))
    key = args[1] if len(args) > 1 else 'backspace'
    # An operation is in flight: swallow the key (a repeat, or a second press).
    # The flag lives on the Window -- kitty re-executes this file per call, so a
    # module global would be fresh every time and the "repeat" would go through
    # as a real backspace in the middle of the edit. It expires on its own in
    # case an operation dies without clearing it.
    import time
    if getattr(w, '_ide_seldel_busy', 0) > time.monotonic():
        return
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

    sel_first, sel_last = cell(first), cell(last)
    cur = (screen.cursor.y, screen.cursor.x)
    rows = visual_rows(w)
    # Clip the selection to the editable block — Claude's input box, or the
    # caret's own logical line in a bare shell. Whatever lies outside it (the
    # transcript, the box rule, the status lines, earlier shell output) is
    # unreachable by the caret, so dropping it up front saves discovering that
    # the slow way, one stalled keypress at a time.
    box = input_rows(rows, cur[0])
    tcol = TEXT_COL if box is not None else 0
    top, bot = box if box is not None else logical_run(rows, cols, cur[0])
    if sel_last[0] < top or sel_first[0] > bot:
        screen.clear_selection()
        return
    sel_first = max(sel_first, (top, tcol))
    sel_last = min(sel_last, (bot, max(row_len_cells(rows[bot]) - 1, 0)))
    if sel_first > sel_last:
        screen.clear_selection()
        return
    if try_fast_path(w, sel_first, sel_last):
        return
    w._ide_seldel_busy = time.monotonic() + 12
    if box is not None:
        # Claude's input box: marker-verified (see MarkDel)
        screen.clear_selection()
        machine = MarkDel(boss, target_window_id, sel_first, sel_last)
    else:
        plan = plan_oneshot(rows, cols, cur, sel_first, sel_last, tcol)
        machine = SelDel(boss, target_window_id, sel_first, sel_last, cur, plan, tcol)
    add_timer(machine, 0, False)


handle_result.no_ui = True
