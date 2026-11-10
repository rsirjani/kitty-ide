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
# sequence. It is only set while the line editor is waiting at a prompt.
[[ -n "$KITTY_WINDOW_ID" && -z "$ZELLIJ" && -o interactive ]] || return 0

_ide_caret_jump() {
  local k n=
  while read -t 0.3 -k 1 k; do
    [[ $k == '~' ]] && break
    n+=$k
  done
  local -a p=("${(@s:;:)n}")
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
zle -N _ide_caret_jump
for _ide_km in emacs viins vicmd; do
  bindkey -M $_ide_km '^[[5733;' _ide_caret_jump
done
unset _ide_km
# the buffer doesn't change: leave the suggestion alone, don't wrap the widget
ZSH_AUTOSUGGEST_IGNORE_WIDGETS+=(_ide_caret_jump)

_ide_caret_on()  { print -n '\e]1337;SetUserVar=ide_zle=MQ==\a' }
_ide_caret_off() { print -n '\e]1337;SetUserVar=ide_zle=\a' }
autoload -Uz add-zsh-hook
add-zsh-hook precmd _ide_caret_on
add-zsh-hook preexec _ide_caret_off
