#!/usr/bin/env bash
# Remote desktop (wayvnc over SSH) + remote GUI control for twin. Run: sudo bash ~/twin-gui-setup.sh
set -euo pipefail
U=${SUDO_USER:-appspubs}      # the twin desktop user (whoever ran sudo)

echo "== packages (official extra repo) =="
pacman -S --needed --noconfirm wayvnc ydotool

echo "== let $U create virtual input devices (ydotool clicks) without root =="
cat > /etc/udev/rules.d/60-uinput-input-group.rules <<'RULE'
KERNEL=="uinput", GROUP="input", MODE="0660", OPTIONS+="static_node=uinput"
RULE
udevadm control --reload-rules
chgrp input /dev/uinput && chmod 660 /dev/uinput   # apply now; the rule covers future boots
usermod -aG input "$U"
ls -l /dev/uinput

echo "DONE — wayvnc listens only on 127.0.0.1 and is reached through SSH, so no firewall change is needed."
