#!/usr/bin/env bash
# Install kitty-ide: symlink configs + scripts back to this repo (so editing the
# repo edits the live IDE), apply the kitty layout patch, and pin kitty so the
# patch survives upgrades. Arch Linux + kitty + yazi + neovim assumed.
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

link() { mkdir -p "$(dirname "$2")"; ln -sfn "$1" "$2"; printf '  %s -> %s\n' "$2" "$1"; }

echo "==> scripts -> ~/.local/bin"
for f in "$REPO"/bin/*; do
  chmod +x "$f"
  link "$f" "$HOME/.local/bin/$(basename "$f")"
done

echo "==> kitty config -> ~/.config/kitty"
link "$REPO/kitty/ide.conf"          "$HOME/.config/kitty/ide.conf"
link "$REPO/kitty/ide.session"       "$HOME/.config/kitty/ide.session"
link "$REPO/kitty/ide-pin-tabbar.py" "$HOME/.config/kitty/ide-pin-tabbar.py"
link "$REPO/kitty/patches/apply-fixed-lines-patch.py" "$HOME/.config/kitty/patches/apply-fixed-lines-patch.py"

echo "==> yazi config -> ~/.config/yazi-ide"
link "$REPO/yazi/yazi.toml" "$HOME/.config/yazi-ide/yazi.toml"
link "$REPO/yazi/init.lua"  "$HOME/.config/yazi-ide/init.lua"

echo "==> kitty layout patch (needs sudo)"
sudo cp "$REPO/pacman/zz-kitty-fixed-lines.hook" /etc/pacman.d/hooks/
sudo python3 "$REPO/kitty/patches/apply-fixed-lines-patch.py" || true

echo "==> pin kitty so the patch survives upgrades"
if ! grep -qE '^IgnorePkg.*\bkitty\b' /etc/pacman.conf; then
  if grep -qE '^IgnorePkg' /etc/pacman.conf; then
    sudo sed -i 's/^\(IgnorePkg\s*=.*\)/\1 kitty/' /etc/pacman.conf
  else
    sudo sed -i 's/^#IgnorePkg\s*=.*/IgnorePkg   = kitty/' /etc/pacman.conf
  fi
fi

cat <<'EOF'

Done. Launch the IDE with:  ide

Notes:
 - ide.session has machine-specific paths (~/projects); adjust for your setup.
 - The crisp browser needs a patched carbonyl on PATH — see carbonyl/README.md.
 - PDF tabs use `tdf`; editing uses `nvim`.
EOF
