#!/usr/bin/env python3
# Clean copies out of the claude pane.
#
# Claude Code lays its output out itself: it wraps every paragraph to the pane
# width with REAL newlines, indents continuation lines, and prefixes messages
# with a "● " bullet. kitty can only un-wrap lines the terminal wrapped, so a
# copy came out shaped like the pane -- a newline every ~60 characters, two
# spaces of indent on each line -- and pasted as a ragged block.
#
# Every copy path in kitty (copy-on-select, ctrl+c, middle click, the primary
# selection) asks Window.text_for_selection() for the text, so that one method
# is wrapped: for a window that is the IDE's claude pane AND is running Claude
# Code, the selection is re-flowed before it reaches the clipboard:
#
#   * a line is joined to the next when it was wrapped, i.e. when the next
#     line's first word would not have fit on it (so short lines -- code, list
#     items, one-liners -- are left alone), and the next line continues at the
#     paragraph's text column and doesn't start a list item / table / tool row;
#   * the "● " message bullet and the common indent are dropped.
#
# Other panes, a plain shell in the claude pane, rectangle selections and ANSI
# copies are untouched.
#
# Installed like ide-min.py: as a watcher (ide.session) for new IDEs, and by
# running it once as a kitten (`kitty @ action kitten ide-copy.py`) for running
# ones. kitty re-executes the file per kitten call, so bumping VERSION swaps in
# a new version live.
import os
import re

VERSION = 2

BULLETS = ('● ', '⏺ ')
LIST_RE = re.compile(r'(?:[-*•‣◦]|\d{1,3}[.)])\s+')
# rows that are never part of a flowed paragraph: tool rows, tables, rules, fences, headings
NOFLOW_RE = re.compile(r'[⎿│├└┌┬┴┼╭╰╮╯─━═┃|]|```|#{1,6}\s')


def _width(s):
    from kitty.fast_data_types import wcwidth
    n = 0
    for ch in s:
        w = wcwidth(ord(ch))
        n += 1 if w < -1 else max(w, 0)   # -2 = "ambiguous width": one cell
    return n


def reflow(text, cols, x0=0):
    """Undo Claude Code's own wrapping in `text` (a selection out of a pane
    `cols` wide whose first line starts at column `x0`)."""
    lines = [ln.replace('\xa0', ' ').rstrip() for ln in text.split('\n')]
    out = []          # [indent, text] per logical line
    cur = None        # the logical line being extended
    prev = None       # facts about the previous physical line
    for i, line in enumerate(lines):
        off = x0 if i == 0 else 0
        body = line.lstrip(' ')
        indent = len(line) - len(body) + off
        if not body:
            out.append([0, ''])
            cur = prev = None
            continue
        marker = False
        textcol = indent
        if body.startswith(BULLETS) and not off:
            body = body[2:].lstrip(' ')
            textcol = indent = indent + 2
        else:
            m = LIST_RE.match(body)
            if m:
                marker = True
                textcol = indent + len(m.group(0))
        noflow = bool(NOFLOW_RE.match(body)) or '│' in body
        first_word = body.split(' ', 1)[0]
        join = (
            cur is not None and prev is not None and not marker and not noflow and not prev['noflow']
            and (prev['textcol'] is None or indent == prev['textcol'])
            and prev['len'] <= cols                                   # a terminal-wrapped line is one real line
            and prev['len'] + 1 + _width(first_word) > cols           # ...the word did not fit: it was wrapped
        )
        if join:
            # a token longer than a whole line was cut mid-token: no space was lost
            glue = '' if _width(prev['last_word']) + _width(first_word) > cols - prev['indent'] else ' '
            cur[1] += glue + body
        else:
            cur = [indent, body]
            out.append(cur)
        prev = {
            'len': _width(line) + off,
            'indent': indent if not join else prev['indent'],
            # a line picked up mid-row has no known column: let anything continue it
            'textcol': None if (off and not join) else (textcol if not join else prev['textcol']),
            'noflow': noflow,
            'last_word': body.rsplit(' ', 1)[-1],
        }
    indents = [ind for ind, t in out[1 if x0 else 0:] if t]
    base = min(indents) if indents else 0
    res = []
    for j, (ind, t) in enumerate(out):
        pad = 0 if (j == 0 and x0) else max(ind - base, 0)
        res.append(' ' * pad + t if t else '')
    return '\n'.join(res)


def _runs_claude(window):
    try:
        for p in window.child.foreground_processes:
            cmd = p.get('cmdline') or ['']
            if os.path.basename(cmd[0]).startswith('claude'):
                return True
    except Exception:
        pass
    return False


def install():
    from kitty.window import Window
    if getattr(Window, '_ide_copy_version', None) == VERSION:
        return
    orig = getattr(Window, '_ide_copy_orig', None)
    if orig is None:
        orig = Window._ide_copy_orig = Window.text_for_selection

    def text_for_selection(self, as_ansi=False):
        text = orig(self, as_ansi)
        try:
            if (as_ansi or not text or (self.user_vars or {}).get('pane') != 'claude'
                    or self.screen.is_rectangle_select() or not _runs_claude(self)):
                return text
            cols = self.screen.columns
            x0 = 0
            sel = self.screen.current_selections()
            first = next((i for i, b in enumerate(sel) if b), None)
            if first is not None:
                x0 = first % cols
            return reflow(text, cols, x0)
        except Exception:
            return text

    Window.text_for_selection = text_for_selection
    Window._ide_copy_version = VERSION


def on_load(boss, data):       # watcher entry point
    install()


def main(args):
    pass  # never runs: no_ui kittens only use handle_result (the loader wants it)


def handle_result(args, answer, target_window_id, boss):
    install()


handle_result.no_ui = True
