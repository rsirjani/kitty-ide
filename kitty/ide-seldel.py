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

from kitty.fast_data_types import add_timer, wcwidth

TICK = 0.008          # seconds between cursor polls
STABLE_TICKS = 2      # cursor unchanged this long => frame settled, safe to act
STALL_TICKS = 15      # a sent press moved nothing for this long => boundary
MAX_TOTAL_TICKS = 1100 # ~9s hard stop for the whole operation
MAX_PRESSES = 4000    # absolute cap on synthesized keypresses
JUMP_SETTLE_TICKS = 90 # one-shot burst never reached its landing => walk it

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


def row_len_cells(text):
    return sum(cw for _, cw in clusters(text))


def visual_rows(w):
    """The screen as one string per *visual* row, matching cursor coordinates.
    Plain `as_text()` merges a soft-wrapped line back into one logical line, so
    its indices drift from the cursor's row the moment anything wraps (a zsh
    command line, or a token too long to break in Claude's input). The wrap
    markers say exactly where the screen broke each line."""
    text = w.as_text(add_wrap_markers=True).replace('\r\n', '\n')
    out = []
    for line in text.split('\n'):
        out.extend(line.split('\r'))
    return out


TEXT_COL = 2  # input rows start text at cell 2: "❯ " on the first, indent after
PROMPT = '❯'  # Claude Code's input marker, first cell of the input's first row


def input_rows(rows, cy):
    """(first, last) screen rows of Claude Code's input box, or None if the
    pane isn't showing one (a bare shell, say). The box is the `❯ ` row at or
    above the caret plus the two-space-indented continuation rows under it,
    stopping at the box rule (`───…`). Knowing where the editable text ends
    keeps selections that spill into the rule, the status lines or the
    transcript from being walked at all."""
    top = None
    for r in range(min(cy, len(rows) - 1), -1, -1):
        row = rows[r]
        if row[:1] == PROMPT:
            top = r
            break
        if not row.startswith('  '):
            return None  # ran out of input-shaped rows before finding a prompt
    if top is None:
        return None
    bot = top
    for r in range(top + 1, len(rows)):
        row = rows[r]
        if row[:1] == '─' or row[:1] == PROMPT:
            break
        if row.startswith('  '):
            bot = r
        elif row.strip():
            break  # a non-indented, non-empty row: out of the box
    return top, bot


def boundary_cost(rows, r, cols):
    """Caret presses to step off the end of row `r` onto the next row. A row
    filled to the screen edge wrapped mid-text: its last cell and the next
    row's first cell are adjacent positions, so the step is free. A row that
    stops short broke at something real — the space a word wrap swallowed, or
    a newline — and that character costs one press."""
    row = rows[r] if 0 <= r < len(rows) else ''
    return 0 if row_len_cells(row) >= cols else 1


def logical_run(rows, cols, cy):
    """(first, last) visual rows of the logical line the caret is on, found by
    following full-width rows outward — a row filled to the screen edge wrapped
    into the next one. Used when there's no Claude input box (a bare shell), so
    a selection in the scrollback is rejected outright instead of being walked
    into a dead end."""
    top = cy
    while top > 0 and row_len_cells(rows[top - 1]) >= cols:
        top -= 1
    bot = cy
    while bot + 1 < len(rows) and row_len_cells(rows[bot]) >= cols:
        bot += 1
    return top, bot


def presses_over(rows, cols, pfrom, pto, tcol):
    """Caret presses to travel pfrom -> pto (row-major order, pfrom <= pto):
    the characters passed on each row, plus each row boundary crossed."""
    (ay, ax), (by, bx) = pfrom, pto
    n = 0
    for r in range(ay, by + 1):
        row = rows[r] if 0 <= r < len(rows) else ''
        rl = row_len_cells(row)
        a = max(ax if r == ay else tcol, tcol)
        b = min(bx if r == by else rl, rl)
        if b > a:
            n += presses_between(row, a, b)
        if r < by:
            n += boundary_cost(rows, r, cols)
    return n


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
    def __init__(self, boss, window_id, sel_first, sel_last, orig, plan=None):
        self.boss = boss
        self.wid = window_id
        self.sel_first = sel_first  # (y, x) first selected cell, row-major
        self.sel_last = sel_last    # (y, x) last selected cell
        self.orig = orig            # caret position before we touched anything
        self.plan = plan            # precomputed one-shot, or None
        self.jump_sent = False
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
            active.discard(self.wid)
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
        key, n, lands, n_del = self.plan
        if pos in lands:
            # the caret is exactly where the press arithmetic predicted: the
            # counts are validated, fire the whole deletion in one write
            w.screen.clear_selection()
            self.send(w, 'backspace', n_del)
            return True
        if self.jump_sent:
            # in flight: the burst is a monotonic sweep toward the landing, so
            # the only cell it can pass through mid-way is the landing itself
            self.settle += 1
            if self.settle > JUMP_SETTLE_TICKS:
                self.fall_back()  # never arrived: plan was wrong, walk it
            return False
        if n <= 0:
            self.fall_back()
            return False
        self.jump_sent = True
        self.settle = 0
        return not self.send(w, key, n)

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
    plan = plan_oneshot(rows, cols, cur, sel_first, sel_last, tcol)
    machine = SelDel(boss, target_window_id, sel_first, sel_last, cur, plan)
    active.add(target_window_id)
    add_timer(machine, 0, False)


handle_result.no_ui = True
