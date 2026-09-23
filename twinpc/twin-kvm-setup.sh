#!/usr/bin/env bash
# Mouse/keyboard sharing with the main PC (lan-mouse). Run: sudo bash ~/twin-kvm-setup.sh
set -euo pipefail
pacman -S --needed --noconfirm lan-mouse
ufw allow 4242/udp                 # lan-mouse (encrypted, only authorized fingerprints are accepted)
ufw reload
lan-mouse --version
echo DONE
