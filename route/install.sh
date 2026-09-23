#!/usr/bin/env bash
# install.sh — wire twin task routing into this PC and the twin (safe to run again).
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"
mkdir -p ~/.local/bin ~/.config/twin-route ~/.config/systemd/user
ln -sf "$PWD/twin-route" ~/.local/bin/twin-route
ln -sf "$PWD/twin-exec" ~/.local/bin/twin-exec
[[ -f ~/.config/twin-route/rules.toml ]] || cp rules.default.toml ~/.config/twin-route/rules.toml
cp twin-route-load.service twin-mount.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now twin-route-load.service
if command -v sshfs >/dev/null; then
  systemctl --user enable --now twin-mount.service
else
  echo "sshfs is not installed — run: sudo apt install sshfs   then run this installer again"
fi
scp -q twin-task-view twin:.local/bin/twin-task-view && ssh twin 'chmod +x ~/.local/bin/twin-task-view; mkdir -p ~/work'
src='[ -f ~/projects/twinPC/route/twin-route.bash ] && . ~/projects/twinPC/route/twin-route.bash'
grep -qF "$src" ~/.bashrc || printf '\n# route GPU/AI and heavy commands to the twin PC (twin route off to disable)\n%s\n' "$src" >> ~/.bashrc
echo "installed — open a new terminal; 'twin route status' shows the state"
