#!/usr/bin/env bash
# Wake-on-LAN + remote power control for the twin. Run: sudo bash ~/twin-wol-setup.sh
set -euo pipefail
U=${SUDO_USER:?run with sudo from the twin desktop user account}
# the twin's wired port: the interface that reaches the main PC (override with TWIN_WIRED_IF)
IF=${TWIN_WIRED_IF:-$(ip -o route get 10.42.0.1 2>/dev/null | sed -n 's/.* dev \([^ ]*\).*/\1/p')}
[[ -n $IF ]] || { echo "cannot find the wired port to the main PC; set TWIN_WIRED_IF"; exit 1; }

echo "== ethtool =="
pacman -S --needed --noconfirm ethtool

echo "== Wake-on-LAN (magic packet) on the LAN connection =="
nmcli con mod "Wired connection 1" 802-3-ethernet.wake-on-lan magic
nmcli con up "Wired connection 1" || true
ethtool -s "$IF" wol g
ethtool "$IF" | grep -i wake

echo "== Let $U power off / reboot over SSH without a password =="
cat > /etc/polkit-1/rules.d/49-twin-power.rules <<RULES
polkit.addRule(function(action, subject) {
    if (subject.user == "$U" &&
        (action.id.indexOf("org.freedesktop.login1.power-off") == 0 ||
         action.id.indexOf("org.freedesktop.login1.reboot") == 0)) {
        return polkit.Result.YES;
    }
});
RULES
systemctl restart polkit

echo "DONE"
