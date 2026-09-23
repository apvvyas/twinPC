"""Desktops: keyboard shortcuts (main PC) and remote-desktop / GUI-control tools (twin)."""
import shlex

from ..steps import cmd_step
from .packages import pkg_step

GSETTINGS = "G=$(command -v /usr/bin/gsettings || command -v gsettings)"   # prefer the distro's: a Nix one writes a keyfile GNOME never reads
KEYS = "org.gnome.settings-daemon.plugins.media-keys"


class Gnome:
    def shortcut_steps(self, feature, key, name, binding, command):
        path = f"/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/{key}/"
        add = ("import ast,sys; l=ast.literal_eval(sys.argv[1].replace('@as ','')); "
               "l.append(sys.argv[2]) if sys.argv[2] not in l else None; print(l)")
        apply = (f"{GSETTINGS}; list=$($G get {KEYS} custom-keybindings)\n"
                 f"new=$(python3 -c {shlex.quote(add)} \"$list\" {shlex.quote(path)})\n"
                 f"$G set {KEYS} custom-keybindings \"$new\"\n"
                 f"S={KEYS}.custom-keybinding:{path}\n"
                 f"$G set \"$S\" name {shlex.quote(name)} && $G set \"$S\" command {shlex.quote(command)}"
                 f" && $G set \"$S\" binding {shlex.quote(binding)}")
        check = f"{GSETTINGS}; $G get {KEYS} custom-keybindings | grep -qF {shlex.quote(path)}"
        return [cmd_step(f"{feature}.main.shortcut", feature, "main", f"bind {binding} to `{command}`", check, apply)]


class Hyprland:
    def remote_desktop_steps(self, feature, pk):
        return [pkg_step(feature, "twin", pk, ["wayvnc"])]

    def gui_steps(self, feature, pk, v):
        rule = 'KERNEL=="uinput", GROUP="input", MODE="0660", OPTIONS+="static_node=uinput"\n'
        return [
            pkg_step(feature, "twin", pk, ["wtype", "ydotool", "grim"]),
            cmd_step(f"{feature}.twin.uinput", feature, "twin",
                     "let the desktop user create virtual input devices (/dev/uinput)",
                     check="grep -q 'GROUP=\"input\"' /etc/udev/rules.d/60-uinput-input-group.rules 2>/dev/null",
                     apply="cat > /etc/udev/rules.d/60-uinput-input-group.rules && udevadm control --reload-rules"
                           " && chgrp input /dev/uinput && chmod 660 /dev/uinput",
                     root=True, check_root=False, input=rule),
            cmd_step(f"{feature}.twin.input-group", feature, "twin", f"add {v['user']} to the input group",
                     check=f"id -nG {v['user']} | tr ' ' '\\n' | grep -qx input",
                     apply=f"usermod -aG input {v['user']}", root=True, check_root=False),
            cmd_step(f"{feature}.twin.stay-awake", feature, "twin", "keep the twin's screen from locking itself",
                     check="! command -v omarchy-toggle-idle >/dev/null || [ -f ~/.local/state/omarchy/indicators/stay-awake ]",
                     apply="omarchy-toggle-idle stay-awake"),
        ]
