# kitty-ide Virtual Desktop — a headless X11 Linux desktop served over VNC.
#
# It is deliberately a *general* desktop, not a single-app image: Claude drives
# it to test and refine front-ends, and real apps sometimes need real
# dependencies — so apt is left in place and common runtimes are preinstalled.
# Claude can `ide-vd run apt-get install -y <whatever>` for the complicated ones.
#
#   build:  docker build -t ide-vd-linux vd/
#   serves: x11vnc on :5901 (bound to 127.0.0.1 by the run command)
FROM debian:bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
      # the desktop itself
      xvfb x11vnc openbox xdotool \
      x11-utils x11-xserver-utils dbus-x11 \
      # a real browser for testing web front-ends
      chromium \
      # office suite: GUI editing of docx/pptx/xlsx opened from the editor pane
      libreoffice-writer libreoffice-calc libreoffice-impress libreoffice-gtk3 \
      # runtimes apps commonly need
      nodejs npm python3 python3-pip python3-venv \
      # plumbing
      git ca-certificates curl wget \
      # fonts so rendered pages/apps don't fall back to boxes
      fonts-noto fonts-noto-color-emoji fonts-liberation \
 && rm -rf /var/lib/apt/lists/*

# --- GPU (VirtualGL/EGL) + audio (PulseAudio) + capture ---------------------
# GL userspace + tools; the NVIDIA driver libs are injected at run time by the
# container toolkit (`--gpus all`), NOT baked into the image — that keeps the
# image driver-version-agnostic.
RUN apt-get update && apt-get install -y --no-install-recommends \
      libegl1 libgl1 libglvnd0 libgles2 libglu1-mesa mesa-utils libxtst6 \
      pulseaudio pulseaudio-utils alsa-utils ffmpeg sox espeak-ng \
 && rm -rf /var/lib/apt/lists/*

# VirtualGL renders OpenGL on the GPU via EGL and composites the result into the
# X framebuffer that x11vnc serves — so GPU apps work on a headless Xvfb. Not in
# Debian repos, so install the upstream .deb (apt resolves its deps).
ARG VGL_VERSION=3.1.4
RUN wget -q -O /tmp/vgl.deb \
      "https://github.com/VirtualGL/virtualgl/releases/download/${VGL_VERSION}/virtualgl_${VGL_VERSION}_amd64.deb" \
 && apt-get update && apt-get install -y --no-install-recommends /tmp/vgl.deb \
 && rm -f /tmp/vgl.deb && rm -rf /var/lib/apt/lists/*

# Catppuccin Mocha base colour for the root window, to match the IDE.
ENV VD_BG=#1e1e2e \
    GEOMETRY=1280x800x24 \
    DISPLAY_NUM=99 \
    VNC_PORT=5901 \
    DISPLAY=:99 \
    PULSE_SERVER=unix:/tmp/pulse-native

COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

# When nothing is launched the desktop stays a blank root window (~0% CPU); the
# feed pane (ide_vd_view.py) draws a cheap ASCII scene instead of running any
# backdrop here. openbox-rc.xml is kept for its sensible window rules.
COPY openbox-rc.xml /root/.config/openbox/rc.xml

# A writable workspace; the run command bind-mounts the host project here.
WORKDIR /work

EXPOSE 5901
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
