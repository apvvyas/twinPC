#!/usr/bin/env bash
# twinpc/install.sh — set up the twin PC (Omarchy/Arch) from scratch. Run ON THE TWIN:
#     sudo bash install.sh          (the main PC copies this folder over: see ../README.md)
# Each step is its own script and safe to re-run; they run in this order because later steps
# rely on earlier ones (e.g. remote unlock needs the main PC's key in authorized_keys).
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"
[[ $EUID -eq 0 ]] || { echo "run with sudo: sudo bash $0"; exit 1; }
for step in \
  twin-setup.sh \
  twin-wol-setup.sh \
  twin-wol-fix.sh \
  twin-gui-setup.sh \
  twin-kvm-setup.sh \
  twin-remote-unlock-setup.sh
do
  echo; echo "######## $step"
  bash "$step"
done
echo; echo "twin setup done — reboot once so the remote-unlock boot image and Wake-on-LAN take effect."
