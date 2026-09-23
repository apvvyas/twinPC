#!/usr/bin/env bash
# main/install.sh — set up the MAIN PC (Ubuntu/GNOME) to drive the twin, and push the twin's
# user-level pieces over SSH. Safe to re-run: a file is only written when its content differs.
# Prerequisites (see ../README.md): `ssh twin` works with a key, and twinpc/install.sh has run on
# the twin. Needs no sudo; it prints the one sudo step (the NIC udev rule) if it is missing.
set -euo pipefail
ROOT=$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)
M=$ROOT/main
changed=()

# put SRC DEST — copy only when different; remembers what changed
put() { mkdir -p "$(dirname "$2")"; if ! cmp -s "$1" "$2"; then cp "$1" "$2"; changed+=("$2"); fi; }
# put_text DEST <<<content
put_text() { local tmp; tmp=$(mktemp); cat > "$tmp"; put "$tmp" "$1"; rm -f "$tmp"; }

echo "== twin CLI, completion, manual"
mkdir -p ~/.local/bin ~/.local/share/man/man1
ln -sf "$ROOT/twin" ~/.local/bin/twin
ln -sf "$ROOT/man/twin.1" ~/.local/share/man/man1/twin.1
mandb -q ~/.local/share/man 2>/dev/null || true

echo "== ssh aliases (twin, twin-unlock)"
touch ~/.ssh/config; chmod 600 ~/.ssh/config
CONF=${XDG_CONFIG_HOME:-$HOME/.config}/twinpc/config
[[ -f $CONF ]] && . "$CONF"
if ! grep -q '^Host twin$' ~/.ssh/config; then
  [[ -n ${TWIN_USER:-} ]] || { echo "set TWIN_USER (your user on the twin) in $CONF"; exit 1; }
  { printf '\n'; sed "s|@TWIN_USER@|$TWIN_USER|" "$M/ssh-config"; } >> ~/.ssh/config; changed+=(~/.ssh/config)
fi
ssh -o BatchMode=yes -o ConnectTimeout=5 twin true || { echo "cannot reach twin with a key — see README 'First-time setup'"; exit 1; }

echo "== shell: Ollama on twin + tab completion"
if ! grep -q 'OLLAMA_HOST=' ~/.bashrc; then
  cat >> ~/.bashrc <<'EOF'

# twin PC (twinPC project): its GPU runs Ollama
export OLLAMA_HOST_URL=${OLLAMA_HOST_URL:-http://10.42.0.11:11434}
export OLLAMA_HOST=${OLLAMA_HOST:-10.42.0.11:11434}
EOF
  changed+=(~/.bashrc)
fi
grep -q 'twinPC/twin-completion.bash' ~/.bashrc || {
  echo '[ -f ~/projects/twinPC/twin-completion.bash ] && . ~/projects/twinPC/twin-completion.bash' >> ~/.bashrc; changed+=(~/.bashrc); }

echo "== user services: wake/unlock/power link, upkeep timer, audio forward, lan-mouse"
for u in twin-link.service twin-kvm.service twin-kvm.timer twin-audio.service lan-mouse.service; do
  put "$M/$u" ~/.config/systemd/user/$u
done
systemctl --user daemon-reload
systemctl --user enable --now twin-link.service twin-kvm.timer twin-audio.service >/dev/null
# lan-mouse.service is started/stopped by twin-kvm.timer (only while the twin has a monitor)

echo "== login unlock window, Remmina fullscreen profile, Super+F12"
sed "s|@HOME@|$HOME|" "$M/twin-unlock.desktop" | put_text ~/.config/autostart/twin-unlock.desktop
put "$M/twin.remmina" ~/.local/share/remmina/twin.remmina
G=/usr/bin/gsettings    # not a Nix gsettings: that one silently writes to a keyfile GNOME never reads
KP=/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/twin-desktop/
list=$($G get org.gnome.settings-daemon.plugins.media-keys custom-keybindings)
if [[ $list != *"$KP"* ]]; then
  new=$(python3 -c 'import ast,sys; l=ast.literal_eval(sys.argv[1].replace("@as ","")); l.append(sys.argv[2]); print(l)' "$list" "$KP")
  $G set org.gnome.settings-daemon.plugins.media-keys custom-keybindings "$new"
  S="org.gnome.settings-daemon.plugins.media-keys.custom-keybinding:$KP"
  $G set "$S" name 'Twin PC desktop (fullscreen toggle)'
  $G set "$S" command "$HOME/.local/bin/twin desktop"
  $G set "$S" binding '<Super>F12'
  changed+=("GNOME shortcut Super+F12")
fi

echo "== lan-mouse (shared mouse/keyboard) — binary, certificates, configs on both sides"
LM_VER=v0.11.0
if ! ~/.local/bin/lan-mouse --version >/dev/null 2>&1; then
  curl -sL -o ~/.local/bin/lan-mouse "https://github.com/feschber/lan-mouse/releases/download/$LM_VER/lan-mouse-linux-x86_64"
  chmod +x ~/.local/bin/lan-mouse; changed+=(~/.local/bin/lan-mouse)
fi
[[ -f ~/.config/lan-mouse/lan-mouse.pem ]] || timeout 4 ~/.local/bin/lan-mouse --capture-backend dummy --emulation-backend dummy daemon >/dev/null 2>&1 || true
ssh twin '[ -f ~/.config/lan-mouse/lan-mouse.pem ] || timeout 4 lan-mouse --capture-backend dummy --emulation-backend dummy daemon >/dev/null 2>&1 || true'
fp() { openssl x509 -noout -fingerprint -sha256 | cut -d= -f2 | tr 'A-F' 'a-f'; }
FP_MAIN=$(fp < ~/.config/lan-mouse/lan-mouse.pem)
FP_TWIN=$(ssh twin 'cat ~/.config/lan-mouse/lan-mouse.pem' | fp)
put_text ~/.config/lan-mouse/config.toml <<EOF
# lan-mouse on the MAIN PC — twin's monitor sits to the LEFT of the main monitor.
# Release keys (get the mouse back if ever stuck on twin): Ctrl+Shift+Super+Alt
port = 4242

[authorized_fingerprints]
"$FP_TWIN" = "twin"

[[clients]]
position = "left"
hostname = "twin"
ips = ["10.42.0.11"]
activate_on_startup = true
EOF

echo "== twin: user-level pieces (audio output, lan-mouse, stay awake)"
tmp=$(mktemp -d)
cat > "$tmp/lm.toml" <<EOF
# lan-mouse on TWIN — the main PC's monitor sits to the RIGHT of this screen.
port = 4242

[authorized_fingerprints]
"$FP_MAIN" = "main"

[[clients]]
position = "right"
hostname = "main"
ips = ["10.42.0.1"]
activate_on_startup = true
EOF
# twin_put SRC REMOTE_PATH — copy to twin only when different; prints "changed" when it wrote
twin_put() {
  if [[ $(ssh twin "sha256sum $2 2>/dev/null | cut -d' ' -f1") != $(sha256sum "$1" | cut -d' ' -f1) ]]; then
    ssh twin "mkdir -p \$(dirname $2)" && scp -q "$1" "twin:$2" && echo changed
  fi
}
if [[ -n $(twin_put "$ROOT/twinpc/main-pc-speakers.conf" .config/pipewire/pipewire.conf.d/main-pc-speakers.conf) ]]; then
  ssh twin 'export XDG_RUNTIME_DIR=/run/user/$(id -u); systemctl --user restart pipewire pipewire-pulse wireplumber'
  changed+=("twin: Main PC speakers output")
fi
[[ -z $(twin_put "$ROOT/twinpc/lan-mouse.service" .config/systemd/user/lan-mouse.service) ]] || changed+=("twin: lan-mouse.service")
[[ -z $(twin_put "$tmp/lm.toml" .config/lan-mouse/config.toml) ]] || changed+=("twin: lan-mouse config")
rm -rf "$tmp"
ssh twin 'export XDG_RUNTIME_DIR=/run/user/$(id -u); systemctl --user daemon-reload; systemctl --user enable --now lan-mouse.service >/dev/null 2>&1; omarchy-toggle-idle stay-awake >/dev/null 2>&1 || true'
"$ROOT/twin" audio on >/dev/null

echo "== task routing"
bash "$ROOT/route/install.sh"

if ! cmp -s "$M/80-i225-no-aspm.rules" /etc/udev/rules.d/80-i225-no-aspm.rules; then
  echo
  echo "One sudo step left (stops the Intel I225-V wired port from hanging):"
  echo "  sudo cp $M/80-i225-no-aspm.rules /etc/udev/rules.d/ && sudo udevadm trigger --action=add --subsystem-match=pci --attr-match=vendor=0x8086 --attr-match=device=0x15f3"
fi

echo
if ((${#changed[@]})); then printf 'changed:\n'; printf '  %s\n' "${changed[@]}"; else echo "everything was already up to date"; fi
