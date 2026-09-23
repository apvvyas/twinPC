#!/usr/bin/env bash
# Arm Wake-on-LAN explicitly on every boot and right before shutdown.
# Needed because the remote-unlock initramfs configures the wired port first, so NetworkManager only
# "assumes" the connection and never applies its wake-on-lan=magic setting.
# Run: sudo bash ~/twin-wol-fix.sh
set -euo pipefail
# the twin's wired port: the interface that reaches the main PC (override with TWIN_WIRED_IF)
IF=${TWIN_WIRED_IF:-$(ip -o route get 10.42.0.1 2>/dev/null | sed -n 's/.* dev \([^ ]*\).*/\1/p')}
[[ -n $IF ]] || { echo "cannot find the wired port to the main PC; set TWIN_WIRED_IF"; exit 1; }
cat > /etc/systemd/system/wol-arm.service <<UNIT
[Unit]
Description=Arm Wake-on-LAN (magic packet) on $IF
After=network.target NetworkManager.service
Wants=network.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/bin/ethtool -s $IF wol g
# stop runs during shutdown: re-arm as late as possible so nothing can undo it
ExecStop=/usr/bin/ethtool -s $IF wol g

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable --now wol-arm.service
ethtool "$IF" | grep -i wake
echo "wakeup: $(cat /sys/class/net/$IF/device/power/wakeup)"
echo DONE
