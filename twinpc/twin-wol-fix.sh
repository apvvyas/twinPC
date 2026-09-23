#!/usr/bin/env bash
# Arm Wake-on-LAN explicitly on every boot and right before shutdown.
# Needed because the remote-unlock initramfs configures enp6s0 first, so NetworkManager only
# "assumes" the connection and never applies its wake-on-lan=magic setting.
# Run: sudo bash ~/twin-wol-fix.sh
set -euo pipefail
cat > /etc/systemd/system/wol-arm.service <<'UNIT'
[Unit]
Description=Arm Wake-on-LAN (magic packet) on enp6s0
After=network.target NetworkManager.service
Wants=network.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/bin/ethtool -s enp6s0 wol g
# stop runs during shutdown: re-arm as late as possible so nothing can undo it
ExecStop=/usr/bin/ethtool -s enp6s0 wol g

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable --now wol-arm.service
ethtool enp6s0 | grep -i wake
echo "wakeup: $(cat /sys/class/net/enp6s0/device/power/wakeup)"
echo DONE
