#!/usr/bin/env bash
# Remote LUKS unlock over SSH at boot (Arch: netconf + dropbear + encryptssh).
# Run: sudo bash ~/twin-remote-unlock-setup.sh
# Undo: sudo rm /etc/mkinitcpio.conf.d/zz-remote-unlock.conf; restore /etc/default/limine from the .bak; sudo limine-mkinitcpio
set -euo pipefail
U=${SUDO_USER:?run with sudo from the twin desktop user account}

echo "== packages (official extra repo) =="
pacman -S --needed --noconfirm mkinitcpio-netconf mkinitcpio-dropbear mkinitcpio-utils

echo "== key allowed to unlock (this is the main PC's key) =="
install -d -m 700 /etc/dropbear
cp "$(getent passwd "$U" | cut -d: -f6)/.ssh/authorized_keys" /etc/dropbear/root_key
chmod 600 /etc/dropbear/root_key

echo "== initramfs hooks: swap 'encrypt' for 'netconf dropbear encryptssh' =="
# Separate file, sourced after omarchy_*.conf, so Omarchy's own hook file stays untouched.
cat > /etc/mkinitcpio.conf.d/zz-remote-unlock.conf <<'CONF'
# Remote LUKS unlock over SSH (added for twin). Delete this file + rebuild to undo.
_ru_hooks=()
for _h in "${HOOKS[@]}"; do
  if [[ $_h == encrypt ]]; then _ru_hooks+=(netconf dropbear encryptssh); else _ru_hooks+=("$_h"); fi
done
HOOKS=("${_ru_hooks[@]}")
unset _ru_hooks _h
CONF

echo "== kernel cmdline: static IP for the initramfs =="
if ! grep -q '^KERNEL_CMDLINE\[default\]+=" ip=' /etc/default/limine; then
  cp /etc/default/limine /etc/default/limine.bak-remote-unlock
  echo 'KERNEL_CMDLINE[default]+=" ip=10.42.0.11::10.42.0.1:255.255.255.0:twin::none"' >> /etc/default/limine
fi

echo "== rebuild initramfs / UKI =="
limine-mkinitcpio

echo "== check =="
grep -E "ip=" /etc/default/limine
echo "DONE — reboot to test: at boot the disk can be unlocked with 'twin unlock' from the main PC."
