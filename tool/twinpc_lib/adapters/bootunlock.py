"""Remote unlock of the twin's encrypted disk: an SSH server (dropbear) in the boot image."""
from ..steps import cmd_step, unsupported_step
from .packages import pkg_step

HOOKS_CONF = """# Remote LUKS unlock over SSH (added for twin). Delete this file + rebuild to undo.
_ru_hooks=()
for _h in "${HOOKS[@]}"; do
  if [[ $_h == encrypt ]]; then _ru_hooks+=(netconf dropbear encryptssh); else _ru_hooks+=("$_h"); fi
done
HOOKS=("${_ru_hooks[@]}")
unset _ru_hooks _h
"""


class Mkinitcpio:
    def unlock_steps(self, profile, repo, v, pk):
        bootloader = profile.get("twin", {}).get("bootloader", "")
        if bootloader != "limine":
            return [unsupported_step("unlock", "twin",
                                     f"remote unlock with bootloader '{bootloader or 'unknown'}' is not supported yet (planned)")]
        keys = f'"$(getent passwd {v["user"]} | cut -d: -f6)/.ssh/authorized_keys"'
        ip = f"ip={v['addr']}::10.42.0.1:255.255.255.0:twin::none"
        return [
            pkg_step("unlock", "twin", pk, ["mkinitcpio-netconf", "mkinitcpio-dropbear", "mkinitcpio-utils"]),
            cmd_step("unlock.twin.key", "unlock", "twin", "allow the main PC's key to unlock the disk",
                     check=f"cmp -s {keys} /etc/dropbear/root_key",
                     apply=f"install -d -m 700 /etc/dropbear && install -m 600 {keys} /etc/dropbear/root_key",
                     root=True),
            cmd_step("unlock.twin.encrypt-hook", "unlock", "twin",
                     "check the twin boots with the 'encrypt' hook (remote unlock replaces it)",
                     check="bash -c 'source /etc/mkinitcpio.conf; for f in /etc/mkinitcpio.conf.d/*.conf; do"
                           " [ \"$f\" = /etc/mkinitcpio.conf.d/zz-remote-unlock.conf ] || source \"$f\"; done;"
                           " [[ \" ${HOOKS[*]} \" == *\" encrypt \"* ]]'",
                     apply="echo 'this twin unlocks its disk with the systemd initramfs (sd-encrypt);"
                           " remote unlock supports the busybox encrypt hook only — not supported yet (planned)' >&2;"
                           " exit 1",
                     check_root=False),
            cmd_step("unlock.twin.hooks", "unlock", "twin", "swap the 'encrypt' boot hook for 'netconf dropbear encryptssh'",
                     check="grep -q encryptssh /etc/mkinitcpio.conf.d/zz-remote-unlock.conf 2>/dev/null",
                     apply="cat > /etc/mkinitcpio.conf.d/zz-remote-unlock.conf",
                     root=True, check_root=False, input=HOOKS_CONF),
            cmd_step("unlock.twin.cmdline", "unlock", "twin", f"give the boot stage the address {v['addr']}",
                     check="grep -q '^KERNEL_CMDLINE\\[default\\]+=\" ip=' /etc/default/limine",
                     apply="cp /etc/default/limine /etc/default/limine.bak-remote-unlock"
                           f" && echo 'KERNEL_CMDLINE[default]+=\" {ip}\"' >> /etc/default/limine",
                     root=True, check_root=False),
            cmd_step("unlock.twin.rebuild", "unlock", "twin", "rebuild the boot image (limine-mkinitcpio)",
                     check="[ -n \"$(find /boot \\( -iname '*.efi' -o -name 'initramfs-*.img' \\)"
                           " -newer /etc/mkinitcpio.conf.d/zz-remote-unlock.conf -newer /etc/default/limine"
                           " -newer /etc/dropbear/root_key 2>/dev/null | head -1)\" ]",
                     apply="limine-mkinitcpio", root=True),
        ]
