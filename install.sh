#!/usr/bin/env bash
# Install kitty-ide: symlink configs + scripts back to this repo (so editing the
# repo edits the live IDE), apply the kitty layout patch, and pin kitty so the
# patch survives upgrades. Arch Linux + kitty + yazi + neovim assumed.
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

link() { mkdir -p "$(dirname "$2")"; ln -sfn "$1" "$2"; printf '  %s -> %s\n' "$2" "$1"; }

echo "==> scripts -> ~/.local/bin"
for f in "$REPO"/bin/*; do
  [ -f "$f" ] || continue                 # skip data dirs (assets/, tools/, __pycache__/)
  case "$(basename "$f")" in *.pyc) continue ;; esac
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

echo "==> editor-pane viewer dependencies"
# ide-open renders many file types in the editor pane:
#   glow (markdown)  mpv (video/audio)  visidata (csv/xlsx tables, editable)
#   libreoffice (docx/pptx/xlsx -> crisp PDF render via tdf, + GUI editing in
#                the Virtual Desktop)
# kitten icat (images) ships with kitty; tdf/carbonyl install separately (see
# their READMEs). Install whichever Arch packages are missing.
pkgs=()
command -v glow     >/dev/null || pkgs+=(glow)
command -v mpv      >/dev/null || pkgs+=(mpv)
[ -x /usr/bin/less ]           || pkgs+=(less)   # ide-view-md pages glow through less
command -v ffmpeg   >/dev/null || pkgs+=(ffmpeg)
command -v visidata >/dev/null || command -v vd >/dev/null || pkgs+=(visidata)
command -v soffice  >/dev/null || command -v libreoffice >/dev/null || pkgs+=(libreoffice-fresh)
if [ ${#pkgs[@]} -gt 0 ]; then
  echo "   installing: ${pkgs[*]}  (libreoffice is ~500MB)"
  sudo pacman -S --needed --noconfirm "${pkgs[@]}" \
    || echo "   (pacman failed — install manually: ${pkgs[*]})"
else
  echo "   all present (glow, mpv, visidata, libreoffice)"
fi

echo "==> virtual desktop (ide-vd)"
# python venv for the VNC feed + Claude's eyes/hands (kept out of system python)
VENV="$HOME/.local/share/ide-vd/venv"
[ -x "$VENV/bin/python" ] || python3 -m venv "$VENV"
"$VENV/bin/pip" -q install --upgrade pip
"$VENV/bin/pip" -q install asyncvnc pillow numpy faster-whisper
mkdir -p "$HOME/.cache/ide-vd/share"
# GPU acceleration for the desktop (NVIDIA): the container toolkit lets the
# desktop's apps render on the host GPU via VirtualGL. Optional — without it the
# desktop falls back to software GL (set gpu=none in the env).
if command -v nvidia-smi >/dev/null && ! command -v nvidia-ctk >/dev/null; then
  echo "   enabling NVIDIA GPU for containers…"
  sudo pacman -S --needed --noconfirm nvidia-container-toolkit \
    && sudo nvidia-ctk runtime configure --runtime=docker \
    && sudo systemctl restart docker.service \
    || echo "   (GPU setup skipped — set gpu=none in vd/envs/linux-desktop.env)"
fi
# the default 'linux-desktop' env runs in Docker; the IDE still works without it
# (the vd pane just shows an idle card until an env is up).
if ! command -v docker >/dev/null; then
  echo "   docker not found — to enable the linux-desktop env:"
  echo "     sudo pacman -S --needed docker && sudo systemctl enable --now docker.service"
  echo "     sudo usermod -aG docker \$USER   # re-login for the group to take effect"
else
  DOCKER=docker; docker info >/dev/null 2>&1 || DOCKER="sudo docker"
  echo "   building the linux desktop image (ide-vd-linux)…"
  $DOCKER build -t ide-vd-linux "$REPO/vd" || echo "   (image build failed — build it later: $DOCKER build -t ide-vd-linux $REPO/vd)"
fi

echo "==> kitty layout patch (needs sudo)"
# Template the hook's hardcoded /home/tin path to THIS user's home, so the
# post-upgrade re-apply works for anyone (not just the author's machine).
sed "s|/home/tin/|$HOME/|g" "$REPO/pacman/zz-kitty-fixed-lines.hook" \
  | sudo tee /etc/pacman.d/hooks/zz-kitty-fixed-lines.hook >/dev/null
sudo python3 "$REPO/kitty/patches/apply-fixed-lines-patch.py" \
  || echo "   (kitty patch did not fully apply — layout tweaks may be inactive; see kitty/patches/)"

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
 - The editor pane renders by type: markdown→glow, html→web tab, images→icat,
   video/audio→mpv, csv/xlsx→visidata, docx/pptx→LibreOffice PDF, pdf→tdf, else
   nvim. Press Alt+M (or click the tab-bar ⇄) to morph rendered⇄editable.
 - PDF tabs use `tdf`; editing uses `nvim`.
 - Virtual Desktop: `ide-vd up` starts the Docker desktop; the vd pane shows it
   live, and Claude drives it with `ide-vd shot/click/type/key`, sees motion with
   `rec`, hears sound with `hear`, and runs GPU apps with `gl`. See vd/README.md.
EOF
