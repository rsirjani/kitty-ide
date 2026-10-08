# Caret arithmetic over a kitty screen, shared by the claude-pane kittens
# (ide-seldel.py: select-to-delete, ide-click.py: click-to-place-caret).
#
# Claude Code's input editor is keyboard-only, so both kittens drive it with
# arrow presses, and everything hinges on predicting how many presses separate
# two screen cells. The model: rows are split into grapheme-ish clusters
# (wcwidth; zero-width chars attach to the previous cell) so a wide char costs
# one press, not two, and each row boundary costs one press only when the row
# stopped short of the screen edge — a row filled to the edge wrapped mid-text
# and its end is adjacent to the next row's start.
#
# Imported as a plain module (kitty puts the kitten's directory on sys.path
# while loading it), so kitty caches it: edits here need a kitty restart.

from kitty.fast_data_types import wcwidth

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
