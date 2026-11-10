# Click-to-place-caret for zsh, the direct way.  Source from ~/.zshrc (after
# the plugins); see kitty/ide-click.py.
#
# kitty's own "click in the prompt" sends one arrow key per character crossed.
# With zsh-syntax-highlighting every one is a repaint (the caret visibly
# crawls), and with zsh-autosuggestions a right-arrow at the end of the line
# ACCEPTS the grey suggestion, so a click past the end pulled it in and the
# caret jumped. Instead the kitten counts the characters between the caret and
# the click on screen and sends a single private key sequence
#     ESC [ 5733 ; <chars> ; <rows> ~
# which this widget turns into one assignment to CURSOR: one repaint, clamped
# to the buffer, nothing accepted or executed.
#
# The `ide_zle` kitty user var tells the kitten this shell understands the
# sequences (1 = jump only, 2 = jump and cut). It is only set while the line
# editor is waiting at a prompt.
[[ -n "$KITTY_WINDOW_ID" && -z "$ZELLIJ" && -o interactive ]] || return 0

_ide_caret_args() {        # the rest of the sequence, up to "~" -> $reply
  local k n=
  while read -t 0.3 -k 1 k; do
    [[ $k == '~' ]] && break
    n+=$k
  done
  reply=("${(@s:;:)n}")
}

ide-caret-jump() {
  local -a reply; _ide_caret_args
  local -a p=("${reply[@]}")
  [[ ${p[1]} == (-|)<-> ]] || return 0
  local -i new=$(( CURSOR + p[1] ))
  if (( new < 0 )); then
    # before the start of the buffer: the prompt itself on the caret's row,
    # otherwise something above the prompt (old output) -- not ours
    [[ ${p[2]} == 0 ]] || return 0
    new=0
  fi
  (( new > $#BUFFER )) && new=$#BUFFER
  CURSOR=$new
}

# ESC [ 5734 ; <from> ; <to> ~   cut the characters from..to (both counted from
# the caret): a mouse selection + backspace (kitty/ide-seldel.py). One edit, so
# one undo brings it back.
ide-caret-cut() {
  local -a reply; _ide_caret_args
  [[ ${reply[1]} == (-|)<-> && ${reply[2]} == (-|)<-> ]] || return 0
  local -i a=$(( CURSOR + reply[1] )) b=$(( CURSOR + reply[2] ))
  (( a < 0 )) && a=0
  (( b > $#BUFFER )) && b=$#BUFFER
  (( b > a )) || return 0
  BUFFER="${BUFFER[1,a]}${BUFFER[b+1,-1]}"
  CURSOR=$a
}
zle -N ide-caret-jump
zle -N ide-caret-cut
for _ide_km in emacs viins vicmd; do
  bindkey -M $_ide_km '^[[5733;' ide-caret-jump
  bindkey -M $_ide_km '^[[5734;' ide-caret-cut
done
unset _ide_km
# A jump doesn't change the buffer: leave the suggestion alone. (The cut is
# left for zsh-autosuggestions to wrap, so the suggestion follows the edit --
# which is also why these names don't start with "_": it skips those.)
ZSH_AUTOSUGGEST_IGNORE_WIDGETS+=(ide-caret-jump)

_ide_caret_on()  { print -n '\e]1337;SetUserVar=ide_zle=Mg==\a' }
_ide_caret_off() { print -n '\e]1337;SetUserVar=ide_zle=\a' }
autoload -Uz add-zsh-hook
add-zsh-hook precmd _ide_caret_on
add-zsh-hook preexec _ide_caret_off
