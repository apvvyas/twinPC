#!/usr/bin/env bash
# One-time setup for the "twin" AI box. Run: sudo bash ~/twin-setup.sh
set -euo pipefail
U=${SUDO_USER:?run with sudo from the twin desktop user account}

echo "== Static IP 10.42.0.11 on the LAN link =="
nmcli con mod "Wired connection 1" ipv4.method manual \
  ipv4.addresses 10.42.0.11/24 ipv4.gateway 10.42.0.1 ipv4.dns "10.42.0.1 1.1.1.1"
nmcli con up "Wired connection 1" || true

echo "== Ollama with ROCm (RDNA2 GPUs, e.g. RX 6000 series: gfx103x -> override to 10.3.0) =="
pacman -S --needed --noconfirm ollama-rocm
mkdir -p /etc/systemd/system/ollama.service.d
cat > /etc/systemd/system/ollama.service.d/override.conf <<'CONF'
[Service]
Environment="OLLAMA_HOST=0.0.0.0:11434"
Environment="HSA_OVERRIDE_GFX_VERSION=10.3.0"
Environment="OLLAMA_KEEP_ALIVE=30m"
CONF
systemctl daemon-reload
systemctl enable --now ollama

echo "== Docker =="
systemctl enable --now docker
usermod -aG docker,render,video "$U"

echo "== Firewall: allow Ollama from the LAN link only =="
ufw allow 11434/tcp
ufw reload

echo "== Keep machine awake when used headless =="
systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target

echo "DONE"
