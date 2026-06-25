#!/usr/bin/env bash
# Bring up the virtual desktop: Xvfb -> openbox -> x11vnc, then stay in the
# foreground as PID 1 (x11vnc) so `docker stop` ends the container cleanly.
set -e

: "${GEOMETRY:=1280x800x24}"   # WxHxDEPTH for Xvfb
: "${DISPLAY_NUM:=99}"
: "${VNC_PORT:=5901}"
: "${VD_BG:=#1e1e2e}"          # Catppuccin Mocha base
export DISPLAY=":${DISPLAY_NUM}"

# A stale lock from a previous boot would make Xvfb refuse the display.
rm -f "/tmp/.X${DISPLAY_NUM}-lock" "/tmp/.X11-unix/X${DISPLAY_NUM}" 2>/dev/null || true

# dbus keeps some GUI apps (e.g. chromium) from spewing warnings / hanging.
mkdir -p /run/dbus && rm -f /run/dbus/pid 2>/dev/null || true
dbus-daemon --system --fork 2>/dev/null || true

Xvfb "$DISPLAY" -screen 0 "$GEOMETRY" -ac +extension GLX +extension RANDR +render -noreset &

# Wait for the X server to accept connections before starting clients.
for _ in $(seq 1 100); do
  xdpyinfo -display "$DISPLAY" >/dev/null 2>&1 && break
  sleep 0.1
done

xsetroot -solid "$VD_BG" 2>/dev/null || true
openbox &

# Default browser wrapper: the container runs as root, so Chromium refuses to
# start without --no-sandbox. Apps that call shell.openExternal()/xdg-open (e.g.
# "Sign in with Google" OAuth, which opens the auth page in a new window) would
# otherwise silently fail to open any window. Route the default browser through
# a wrapper that always passes --no-sandbox so those flows work.
# --user-data-dir on the shared volume so logins / "remember this device" 2FA
# cookies survive container restarts (and even `ide-vd reset`, since /vdshare is
# a host bind-mount). It also makes a second launch open a new window in the
# existing Chromium instead of failing on the singleton lock.
cat > /usr/local/bin/vd-browser <<'EOF'
#!/bin/sh
exec chromium --no-sandbox --disable-gpu --no-first-run --no-default-browser-check \
  --user-data-dir=/vdshare/chromium-profile "$@"
EOF
chmod +x /usr/local/bin/vd-browser
cat > /usr/share/applications/vd-browser.desktop <<'EOF'
[Desktop Entry]
Version=1.0
Name=VD Browser
Exec=/usr/local/bin/vd-browser %U
Terminal=false
Type=Application
MimeType=text/html;x-scheme-handler/http;x-scheme-handler/https;
EOF
update-desktop-database /usr/share/applications 2>/dev/null || true
xdg-settings set default-web-browser vd-browser.desktop 2>/dev/null || true
export BROWSER=/usr/local/bin/vd-browser

# No idle backdrop runs in the container: when nothing is launched the desktop is
# just a blank root window (~0% CPU). The feed pane (ide_vd_view.py) detects the
# blank framebuffer and draws a cheap ASCII scene as text instead of streaming —
# far cheaper than decoding a video here (which cost ~1.7 CPU cores). A real app
# opens on top as usual and the live feed kicks in automatically.

# Headless audio: a system-wide PulseAudio with a null sink as the default.
# Apps play into "vdsink"; `ide-vd hear` records its monitor (the desktop's ears).
# A virtual *microphone* completes the loop: "virtmic" is a source apps see as a
# mic (getUserMedia), fed by "virtmic_sink" — `ide-vd speak` plays TTS into that
# sink so the app under test "hears" us (the desktop's mouth). Self-contained like
# the X display (no host audio device). System mode + anonymous auth on a fixed
# socket is the container-friendly way to run Pulse as root; PULSE_SERVER is set
# in the image ENV so docker-exec'd apps all find this server.
cat >/tmp/vd-pulse.pa <<'PA'
load-module module-native-protocol-unix auth-anonymous=1 socket=/tmp/pulse-native
load-module module-null-sink sink_name=vdsink sink_properties=device.description=vdsink
set-default-sink vdsink
load-module module-null-sink sink_name=virtmic_sink sink_properties=device.description=VirtMicSink
load-module module-remap-source master=virtmic_sink.monitor source_name=virtmic source_properties=device.description=VirtualMicrophone
set-default-source virtmic
PA
pulseaudio --system -n --file=/tmp/vd-pulse.pa --exit-idle-time=-1 --disallow-exit -D 2>/dev/null || true
for _ in $(seq 1 50); do [ -S /tmp/pulse-native ] && break; sleep 0.1; done

# VNC auth: the run command already binds the host port to 127.0.0.1, but we add
# a password as defense-in-depth so a misconfigured port mapping (e.g. -p
# 5901:5901, host networking) can't expose a passwordless desktop. Generate one
# per container; store the plaintext on the shared volume so the host-side
# provider can hand it to clients, and an x11vnc hash for -rfbauth. (Classic VNC
# auth uses only the first 8 chars, so the secret is exactly 8.)
mkdir -p /vdshare
if [ ! -s /vdshare/.vncpass ]; then
  head -c 32 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c 8 > /vdshare/.vncpass
fi
auth_args="-nopw"
if x11vnc -storepasswd "$(cat /vdshare/.vncpass)" /tmp/.vncpasswd >/dev/null 2>&1 \
   && [ -s /tmp/.vncpasswd ]; then
  auth_args="-rfbauth /tmp/.vncpasswd"
fi

# x11vnc serves the live display. It must -listen on 0.0.0.0 *inside* the
# container for docker's port proxy to reach it; off-host exposure is prevented
# by the 127.0.0.1 host binding plus the password above.
# Use the XDAMAGE extension (event-driven) instead of constant polling: when the
# screen is idle x11vnc does ~no work (the feed mostly shows a local ASCII scene
# anyway), and real changes still push promptly. -wait is just a fallback poll
# interval; -defer 5 keeps latency low; -threads decouples client I/O.
exec x11vnc \
  -display "$DISPLAY" \
  -rfbport "$VNC_PORT" \
  -listen 0.0.0.0 \
  -forever -shared $auth_args \
  -nocursorshape \
  -wait 20 -defer 5 -threads \
  -quiet
