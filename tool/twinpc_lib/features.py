"""Every twinPC feature expressed as ordered steps, using the platform adapters.

build_plan(profile, repo, only=None) -> list[Step]. A feature whose platform is unsupported becomes a
single skipped step carrying the reason; everything else is a step with a check, so `install` can
tell what is already done and `doctor` can check health.
"""
import base64
import hashlib
import re
from pathlib import Path

from . import adapters
from .adapters.base import Unsupported
from .adapters.network import ssh_config
from .adapters.packages import pkg_step
from .steps import StepError, cmd_step, file_step, line_step, manual_step, ufw_step, unit_step, unsupported_step

USER_ENV = "export XDG_RUNTIME_DIR=/run/user/$(id -u);"   # reach the desktop user's session services over ssh
LAN_MOUSE_URL = "https://github.com/feschber/lan-mouse/releases/download/v0.11.0/lan-mouse-linux-x86_64"
OPPOSITE = {"left": "right", "right": "left", "top": "bottom", "bottom": "top"}

LAN_MOUSE_MAIN = """# lan-mouse on the MAIN PC — twin's monitor sits to the {SIDE} of the main monitor.
# Release keys (get the mouse back if ever stuck on twin): Ctrl+Shift+Super+Alt
port = 4242

[authorized_fingerprints]
"{fp}" = "twin"

[[clients]]
position = "{side}"
hostname = "twin"
ips = ["{addr}"]
activate_on_startup = true
"""

LAN_MOUSE_TWIN = """# lan-mouse on TWIN — the main PC's monitor sits to the {SIDE} of this screen.
port = 4242

[authorized_fingerprints]
"{fp}" = "main"

[[clients]]
position = "{side}"
hostname = "main"
ips = ["{addr}"]
activate_on_startup = true
"""

WOL_UNIT = """[Unit]
Description=Arm Wake-on-LAN (magic packet) on @IF@
After=network.target NetworkManager.service
Wants=network.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/bin/ethtool -s @IF@ wol g
# stop runs during shutdown: re-arm as late as possible so nothing can undo it
ExecStop=/usr/bin/ethtool -s @IF@ wol g

[Install]
WantedBy=multi-user.target
"""

POLKIT_RULE = """polkit.addRule(function(action, subject) {
    if (subject.user == "@USER@" &&
        (action.id.indexOf("org.freedesktop.login1.power-off") == 0 ||
         action.id.indexOf("org.freedesktop.login1.reboot") == 0)) {
        return polkit.Result.YES;
    }
});
"""


def values(profile, repo):
    if not re.fullmatch(r"[A-Za-z0-9_./+-]+", str(repo)):
        raise StepError(f"the checkout path {str(repo)!r} has spaces or shell characters — move the repo"
                        " to a plain path (letters, digits, . _ - + /)")
    n, u, m, t = (profile.get(k, {}) for k in ("network", "user", "main", "twin"))
    return {"host": n.get("twin_host") or "twin", "addr": n.get("twin_addr") or "10.42.0.11",
            "main_addr": m.get("addr") or "10.42.0.1", "user": u.get("twin_user", ""),
            "twin_if": t.get("wired_iface", ""), "side": u.get("twin_side") or "left",
            "repo": str(repo), "home": str(Path.home())}


def _read(repo, rel):
    return (Path(repo) / rel).read_text()


def repo_unit(repo, rel, source_repo=None):
    """A unit file from the repo, with the checkout location it was written for replaced by `repo`."""
    text = _read(source_repo or repo, rel)
    repo = Path(repo)
    try:
        where = "%h/" + str(repo.relative_to(Path.home()))
    except ValueError:
        where = str(repo)
    return text.replace("%h/projects/twinPC", where)


def fingerprint(ctx, machine):
    r = ctx.run(machine, "cat ~/.config/lan-mouse/lan-mouse.pem")
    if r.rc != 0:
        raise StepError(f"no lan-mouse certificate on the {machine} yet")
    lines = [line.strip() for line in r.out.splitlines()]
    try:
        a = lines.index("-----BEGIN CERTIFICATE-----")
        b = lines.index("-----END CERTIFICATE-----", a)
    except ValueError:
        raise StepError(f"no certificate in the {machine}'s lan-mouse.pem") from None
    der = base64.b64decode("".join(lines[a + 1:b]))
    return ":".join(f"{x:02x}" for x in hashlib.sha256(der).digest())


def _pk(profile, machine):
    return adapters.get("packages", profile.get(machine, {}).get("pkg", ""))


def _connection(profile, repo, v):
    net = adapters.get("network", profile.get("network", {}).get("mode", ""))
    if isinstance(net, Unsupported):
        return [unsupported_step("connection", "main", net.reason())]
    return net.steps(profile, repo, v) + [
        pkg_step("connection", "twin", _pk(profile, "twin"), ["openssh"]),
        cmd_step("connection.twin.sshd", "connection", "twin", "run the SSH server at boot",
                 check="systemctl is-enabled --quiet sshd 2>/dev/null || systemctl is-enabled --quiet ssh 2>/dev/null"
                       " || systemctl is-enabled --quiet ssh.socket 2>/dev/null",
                 apply="systemctl enable --now sshd 2>/dev/null || systemctl enable --now ssh",
                 root=True, check_root=False),
        ufw_step("connection.twin.firewall", "connection", "twin", "22/tcp", "allow SSH (22/tcp) in the twin's firewall"),
    ]


def _cli(profile, repo, v):
    r = v["repo"]
    return [
        cmd_step("cli.main.command", "cli", "main", "install the twin command",
                 check=f'[ "$(readlink ~/.local/bin/twin)" = "{r}/twin" ]',
                 apply=f'mkdir -p ~/.local/bin && ln -sf "{r}/twin" ~/.local/bin/twin'),
        cmd_step("cli.main.manual", "cli", "main", "install the twin manual page",
                 check=f'[ "$(readlink ~/.local/share/man/man1/twin.1)" = "{r}/man/twin.1" ]',
                 apply=f'mkdir -p ~/.local/share/man/man1 && ln -sf "{r}/man/twin.1" ~/.local/share/man/man1/twin.1'
                       " && (mandb -q ~/.local/share/man 2>/dev/null || true)"),
        line_step("cli.main.completion", "cli", "main", "~/.bashrc",
                  f'[ -f "{r}/twin-completion.bash" ] && . "{r}/twin-completion.bash"',
                  match="twin-completion.bash", describe="load tab completion for `twin` in ~/.bashrc"),
        pkg_step("cli", "twin", _pk(profile, "twin"), ["tmux"]),
    ]


def _gpu_stack(profile, repo, v):
    main = [
        line_step("gpu-stack.main.ollama-host", "gpu-stack", "main", "~/.bashrc",
                  f"export OLLAMA_HOST=${{OLLAMA_HOST:-{v['addr']}:11434}}"
                  f" OLLAMA_HOST_URL=${{OLLAMA_HOST_URL:-http://{v['addr']}:11434}}",
                  match="OLLAMA_HOST=", describe="point `ollama` on this PC at the twin"),
        cmd_step("gpu-stack.main.docker-context", "gpu-stack", "main", "add the docker context 'twin'",
                 check="! command -v docker >/dev/null || docker context inspect twin >/dev/null 2>&1",
                 apply=f"docker context create twin --docker host=ssh://{v['host']}"),
    ]
    gpu = adapters.get("gpu", profile.get("twin", {}).get("gpu", ""))
    if isinstance(gpu, Unsupported):
        return main + [unsupported_step("gpu-stack", "twin", gpu.reason())]
    return main + gpu.ai_stack_steps(profile, repo, v, _pk(profile, "twin"))


def _routing(profile, repo, v):
    r = v["repo"]
    return [
        cmd_step("routing.main.commands", "routing", "main", "link twin-route and twin-exec into ~/.local/bin",
                 check=f'[ "$(readlink ~/.local/bin/twin-route)" = "{r}/route/twin-route" ]'
                       f' && [ "$(readlink ~/.local/bin/twin-exec)" = "{r}/route/twin-exec" ]',
                 apply=f'mkdir -p ~/.local/bin && ln -sf "{r}/route/twin-route" ~/.local/bin/twin-route'
                       f' && ln -sf "{r}/route/twin-exec" ~/.local/bin/twin-exec'),
        cmd_step("routing.main.rules", "routing", "main", "create ~/.config/twin-route/rules.toml",
                 check="[ -f ~/.config/twin-route/rules.toml ]",
                 apply=f'mkdir -p ~/.config/twin-route && cp "{r}/route/rules.default.toml" ~/.config/twin-route/rules.toml'),
        file_step("routing.main.load-unit", "routing", "main", "$HOME/.config/systemd/user/twin-route-load.service",
                  repo_unit(repo, "route/twin-route-load.service")),
        unit_step("routing.main.load", "routing", "main", "twin-route-load.service"),
        line_step("routing.main.hook", "routing", "main", "~/.bashrc",
                  f'[ -f "{r}/route/twin-route.bash" ] && . "{r}/route/twin-route.bash"',
                  match="route/twin-route.bash", describe="load the routing hook in ~/.bashrc"),
        file_step("routing.twin.viewer", "routing", "twin", "$HOME/.local/bin/twin-task-view",
                  _read(repo, "route/twin-task-view"), mode="755"),
    ]


def _mount(profile, repo, v):
    return [
        pkg_step("mount", "main", _pk(profile, "main"), ["sshfs"]),
        cmd_step("mount.twin.work", "mount", "twin", "create ~/work on the twin",
                 check="[ -d ~/work ]", apply="mkdir -p ~/work"),
        file_step("mount.main.unit", "mount", "main", "$HOME/.config/systemd/user/twin-mount.service",
                  repo_unit(repo, "route/twin-mount.service").replace(" twin:work ", f" {v['host']}:work ")),
        unit_step("mount.main.mount", "mount", "main", "twin-mount.service"),
    ]


def _power(profile, repo, v):
    ifc = v["twin_if"]
    if not ifc:
        return [unsupported_step("power", "twin", "the twin's wired port is unknown — plug in the cable and run"
                                 " twinpc detect --force")]
    con = f'"$(nmcli -g GENERAL.CONNECTION device show {ifc})"'
    return [
        pkg_step("power", "twin", _pk(profile, "twin"), ["ethtool"]),
        cmd_step("power.twin.nm-wol", "power", "twin", "keep Wake-on-LAN (magic packet) on in NetworkManager",
                 check=f"nmcli -g 802-3-ethernet.wake-on-lan connection show {con} | grep -q magic",
                 apply=f"nmcli connection modify {con} 802-3-ethernet.wake-on-lan magic",
                 root=True, check_root=False),
        cmd_step("power.twin.wol-arm", "power", "twin", "arm Wake-on-LAN at every boot and shutdown (wol-arm.service)",
                 check=f"grep -q 'ethtool -s {ifc} wol g' /etc/systemd/system/wol-arm.service 2>/dev/null"
                       " && systemctl is-enabled --quiet wol-arm",
                 apply="cat > /etc/systemd/system/wol-arm.service && systemctl daemon-reload"
                       " && systemctl enable --now wol-arm",
                 root=True, check_root=False, input=WOL_UNIT.replace("@IF@", ifc)),
        cmd_step("power.twin.polkit", "power", "twin", f"let {v['user']} power off / reboot the twin over SSH",
                 check="busctl call org.freedesktop.login1 /org/freedesktop/login1"
                       " org.freedesktop.login1.Manager CanPowerOff | grep -q yes",
                 apply="cat > /etc/polkit-1/rules.d/49-twin-power.rules && systemctl restart polkit",
                 root=True, check_root=False, input=POLKIT_RULE.replace("@USER@", v["user"])),
        cmd_step("power.twin.no-sleep", "power", "twin", "stop the twin from suspending (it is used remotely)",
                 check='[ "$(systemctl is-enabled sleep.target 2>/dev/null)" = masked ]',
                 apply="systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target",
                 root=True, check_root=False),
        manual_step("power.main.bios", "power", "main", "confirm Wake-on-LAN is enabled in the twin's firmware",
                    "In the twin's BIOS enable wake from PCI-E / LAN and disable ErP / deep sleep "
                    "(ASUS: Advanced → APM Configuration). Software cannot see this setting, so twinpc "
                    "asks once and remembers your answer.",
                    check='[ -f "${XDG_CONFIG_HOME:-$HOME/.config}/twinpc/bios-wol-confirmed" ]',
                    after_confirm='mkdir -p "${XDG_CONFIG_HOME:-$HOME/.config}/twinpc"'
                                  ' && touch "${XDG_CONFIG_HOME:-$HOME/.config}/twinpc/bios-wol-confirmed"'),
        file_step("power.main.link-unit", "power", "main", "$HOME/.config/systemd/user/twin-link.service",
                  repo_unit(repo, "main/twin-link.service")),
        unit_step("power.main.link", "power", "main", "twin-link.service"),
    ]


def _unlock(profile, repo, v):
    twin = profile.get("twin", {})
    if not twin.get("luks"):
        return [unsupported_step("unlock", "twin", "the twin's disk is not encrypted — nothing to unlock")]
    if profile.get("network", {}).get("mode") != "cable":
        return [unsupported_step("unlock", "twin", "remote unlock on a LAN is not supported yet (planned)")]
    bu = adapters.get("bootunlock", twin.get("initramfs", ""))
    if isinstance(bu, Unsupported):
        return [unsupported_step("unlock", "twin", bu.reason())]
    _, unlock_block = ssh_config(repo, v)
    return bu.unlock_steps(profile, repo, v, _pk(profile, "twin")) + [
        cmd_step("unlock.main.alias", "unlock", "main", f"add the ssh alias '{v['host']}-unlock'",
                 check=f"grep -q '^Host {v['host']}-unlock$' ~/.ssh/config 2>/dev/null",
                 apply="{ printf '\\n'; cat; } >> ~/.ssh/config", input=unlock_block),
        file_step("unlock.main.autostart", "unlock", "main", "$HOME/.config/autostart/twin-unlock.desktop",
                  _read(repo, "main/twin-unlock.desktop").replace("@HOME@", v["home"])),
    ]


def _kvm(profile, repo, v):
    side = v["side"] if v["side"] in OPPOSITE else "left"
    other = OPPOSITE[side]

    def main_config(ctx):
        return LAN_MOUSE_MAIN.format(SIDE=side.upper(), side=side, fp=fingerprint(ctx, "twin"), addr=v["addr"])

    def twin_config(ctx):
        return LAN_MOUSE_TWIN.format(SIDE=other.upper(), side=other, fp=fingerprint(ctx, "main"), addr=v["main_addr"])

    cert = ("timeout 4 {bin} --capture-backend dummy --emulation-backend dummy daemon >/dev/null 2>&1;"
            " [ -f ~/.config/lan-mouse/lan-mouse.pem ]")
    return [
        cmd_step("kvm.main.binary", "kvm", "main", "install lan-mouse on this PC",
                 check="~/.local/bin/lan-mouse --version >/dev/null 2>&1",
                 apply=f"mkdir -p ~/.local/bin && curl -fsSL -o ~/.local/bin/lan-mouse {LAN_MOUSE_URL}"
                       " && chmod +x ~/.local/bin/lan-mouse"),
        pkg_step("kvm", "twin", _pk(profile, "twin"), ["lan-mouse"]),
        ufw_step("kvm.twin.firewall", "kvm", "twin", "4242/udp", "allow lan-mouse (4242/udp) in the twin's firewall"),
        cmd_step("kvm.main.cert", "kvm", "main", "create this PC's lan-mouse certificate",
                 check="[ -f ~/.config/lan-mouse/lan-mouse.pem ]", apply=cert.format(bin="~/.local/bin/lan-mouse")),
        cmd_step("kvm.twin.cert", "kvm", "twin", "create the twin's lan-mouse certificate",
                 check="[ -f ~/.config/lan-mouse/lan-mouse.pem ]", apply=cert.format(bin="lan-mouse")),
        file_step("kvm.main.config", "kvm", "main", "$HOME/.config/lan-mouse/config.toml", main_config),
        file_step("kvm.twin.config", "kvm", "twin", "$HOME/.config/lan-mouse/config.toml", twin_config),
        file_step("kvm.main.unit", "kvm", "main", "$HOME/.config/systemd/user/lan-mouse.service",
                  repo_unit(repo, "main/lan-mouse.service")),
        file_step("kvm.main.upkeep-service", "kvm", "main", "$HOME/.config/systemd/user/twin-kvm.service",
                  repo_unit(repo, "main/twin-kvm.service")),
        file_step("kvm.main.upkeep-timer", "kvm", "main", "$HOME/.config/systemd/user/twin-kvm.timer",
                  repo_unit(repo, "main/twin-kvm.timer")),
        unit_step("kvm.main.upkeep", "kvm", "main", "twin-kvm.timer"),
        file_step("kvm.twin.unit", "kvm", "twin", "$HOME/.config/systemd/user/lan-mouse.service",
                  _read(repo, "twinpc/lan-mouse.service")),
        unit_step("kvm.twin.lan-mouse", "kvm", "twin", "lan-mouse.service", now=False),
    ]


def _clipboard(profile, repo, v):
    md = _desktop_for(profile, "main", "clipboard_main_steps")
    if isinstance(md, str):
        return [unsupported_step("clipboard", "main", md)]
    td = _desktop_for(profile, "twin", "clipboard_twin_steps")
    if isinstance(td, str):
        return [unsupported_step("clipboard", "twin", td)]
    r = v["repo"]
    return [
        pkg_step("clipboard", "main", _pk(profile, "main"), ["wl-clipboard"]),
        pkg_step("clipboard", "twin", _pk(profile, "twin"), ["wl-clipboard"]),
        cmd_step("clipboard.main.command", "clipboard", "main", "install twin-clipd on this PC",
                 check=f'[ "$(readlink ~/.local/bin/twin-clipd)" = "{r}/clip/twin-clipd" ]',
                 apply=f'mkdir -p ~/.local/bin && ln -sf "{r}/clip/twin-clipd" ~/.local/bin/twin-clipd'),
        file_step("clipboard.twin.agent", "clipboard", "twin", "$HOME/.local/bin/twin-clipd",
                  _read(repo, "clip/twin-clipd"), mode="755", describe="install twin-clipd on the twin"),
        file_step("clipboard.main.unit", "clipboard", "main", "$HOME/.config/systemd/user/twin-clip.service",
                  repo_unit(repo, "main/twin-clip.service").replace(" --host twin\n", f" --host {v['host']}\n")),
        unit_step("clipboard.main.service", "clipboard", "main", "twin-clip.service"),
        *md.clipboard_main_steps("clipboard", repo),
        *td.clipboard_twin_steps("clipboard", repo),
        cmd_step("clipboard.main.link", "clipboard", "main", "connect the clipboard service to the twin",
                 check="~/.local/bin/twin-clipd --selftest >/dev/null",
                 apply="systemctl --user restart twin-clip.service && sleep 3 && ~/.local/bin/twin-clipd --selftest"),
    ]


def facing_edge(twin_side, machine):
    """The screen edge that faces the other PC: the twin's side on the main PC, the opposite one on the twin."""
    side = twin_side if twin_side in OPPOSITE else "left"
    return side if machine == "main" else OPPOSITE[side]


def _shelf(profile, repo, v):
    md = _desktop_for(profile, "main", "shelf_main_steps")
    if isinstance(md, str):
        return [unsupported_step("shelf", "main", md)]
    td = _desktop_for(profile, "twin", "shelf_twin_steps")
    if isinstance(td, str):
        return [unsupported_step("shelf", "twin", td)]
    r = v["repo"]
    main_edge, twin_edge = facing_edge(v["side"], "main"), facing_edge(v["side"], "twin")
    return [
        pkg_step("shelf", "main", _pk(profile, "main"), ["python-gi", "gtk4-gir"]),
        pkg_step("shelf", "twin", _pk(profile, "twin"), ["python-gi", "gtk4-gir"]),
        *td.shelf_twin_steps("shelf", _pk(profile, "twin")),
        manual_step("shelf.main.clipboard", "shelf", "main", "the clipboard feature (the shelf uses its link)",
                    "Install the clipboard feature first: tool/twinpc install clipboard",
                    check="systemctl --user is-enabled --quiet twin-clip.service"),
        cmd_step("shelf.main.command", "shelf", "main", "install twin-shelf on this PC",
                 check=f'[ "$(readlink ~/.local/bin/twin-shelf)" = "{r}/clip/twin-shelf" ]',
                 apply=f'mkdir -p ~/.local/bin && ln -sf "{r}/clip/twin-shelf" ~/.local/bin/twin-shelf'),
        file_step("shelf.twin.command", "shelf", "twin", "$HOME/.local/bin/twin-shelf",
                  _read(repo, "clip/twin-shelf"), mode="755", describe="install twin-shelf on the twin"),
        file_step("shelf.main.unit", "shelf", "main", "$HOME/.config/systemd/user/twin-shelf.service",
                  repo_unit(repo, "main/twin-shelf.service").replace("--edge left\n", f"--edge {main_edge}\n")),
        unit_step("shelf.main.service", "shelf", "main", "twin-shelf.service"),
        file_step("shelf.twin.unit", "shelf", "twin", "$HOME/.config/systemd/user/twin-shelf.service",
                  _read(repo, "twinpc/twin-shelf.service").replace("--edge right ", f"--edge {twin_edge} ")),
        unit_step("shelf.twin.service", "shelf", "twin", "twin-shelf.service"),
        *md.shelf_main_steps("shelf", repo),
        cmd_step("shelf.main.link", "shelf", "main", "check the shelf app is connected on this PC",
                 check="~/.local/bin/twin-shelf --selftest >/dev/null",
                 apply="systemctl --user restart twin-shelf.service && sleep 3 && ~/.local/bin/twin-shelf --selftest"),
        cmd_step("shelf.twin.link", "shelf", "twin", "check the shelf app is connected on the twin",
                 check="~/.local/bin/twin-shelf --selftest --socket shelf.sock >/dev/null",
                 apply="systemctl --user restart twin-shelf.service && sleep 3"
                       " && ~/.local/bin/twin-shelf --selftest --socket shelf.sock"),
    ]


def _audio(profile, repo, v):
    return [
        file_step("audio.main.unit", "audio", "main", "$HOME/.config/systemd/user/twin-audio.service",
                  repo_unit(repo, "main/twin-audio.service").replace("/pulse/native twin\n", f"/pulse/native {v['host']}\n")),
        unit_step("audio.main.forward", "audio", "main", "twin-audio.service"),
        file_step("audio.twin.output", "audio", "twin", "$HOME/.config/pipewire/pipewire.conf.d/main-pc-speakers.conf",
                  _read(repo, "twinpc/main-pc-speakers.conf"),
                  after=f"{USER_ENV} systemctl --user restart pipewire pipewire-pulse wireplumber"),
        cmd_step("audio.twin.default", "audio", "twin", "make 'Main PC speakers' the twin's default output",
                 check=f"{USER_ENV} pactl get-default-sink | grep -qx main-pc-speakers",
                 apply=f"{USER_ENV} pactl set-default-sink main-pc-speakers"),
    ]


def _desktop(profile, repo, v):
    steps = [
        pkg_step("desktop", "main", _pk(profile, "main"), ["remmina"]),
        file_step("desktop.main.remmina-profile", "desktop", "main", "$HOME/.local/share/remmina/twin.remmina",
                  _read(repo, "main/twin.remmina")),
    ]
    md = _desktop_for(profile, "main", "shortcut_steps")
    steps += ([unsupported_step("desktop", "main", md, "shortcut")] if isinstance(md, str) else
              md.shortcut_steps("desktop", "twin-desktop", "Twin PC desktop (fullscreen toggle)", "<Super>F12",
                                f"{v['home']}/.local/bin/twin desktop"))
    td = _desktop_for(profile, "twin", "remote_desktop_steps")
    steps += ([unsupported_step("desktop", "twin", td)] if isinstance(td, str) else
              td.remote_desktop_steps("desktop", _pk(profile, "twin")))
    return steps


def _gui(profile, repo, v):
    td = _desktop_for(profile, "twin", "gui_steps")
    if isinstance(td, str):
        return [unsupported_step("gui", "twin", td)]
    return td.gui_steps("gui", _pk(profile, "twin"), v)


def _desktop_for(profile, machine, method):
    """The desktop adapter for `machine` if it can do `method` there, else the reason it can't."""
    value = profile.get(machine, {}).get("desktop", "")
    adapter = adapters.get("desktop", value)
    if isinstance(adapter, Unsupported):
        return adapter.reason()
    if not hasattr(adapter, method):
        where = "the main PC" if machine == "main" else "the twin"
        return f"desktop '{value}' on {where} is not supported yet (planned)"
    return adapter


def _nic_fix(profile, repo, v):
    rule = f'"{v["repo"]}/main/80-i225-no-aspm.rules"'
    return [cmd_step("nic-fix.main.i225", "nic-fix", "main",
                     "stop an Intel I225-V wired port from hanging (udev rule)",
                     check=f"! lspci -n 2>/dev/null | grep -q '8086:15f3'"
                           f" || cmp -s {rule} /etc/udev/rules.d/80-i225-no-aspm.rules",
                     apply=f"cp {rule} /etc/udev/rules.d/ && udevadm trigger --action=add --subsystem-match=pci"
                           " --attr-match=vendor=0x8086 --attr-match=device=0x15f3",
                     root=True, check_root=False)]


BUILDERS = {"connection": _connection, "cli": _cli, "gpu-stack": _gpu_stack, "routing": _routing,
            "mount": _mount, "power": _power, "unlock": _unlock, "kvm": _kvm, "clipboard": _clipboard,
            "shelf": _shelf, "audio": _audio, "desktop": _desktop, "gui": _gui, "nic-fix": _nic_fix}
FEATURES = list(BUILDERS)


def build_plan(profile, repo, only=None):
    unknown = sorted(set(only or []) - set(BUILDERS))
    if unknown:
        raise ValueError(f"unknown feature(s): {', '.join(unknown)} — choose from {', '.join(FEATURES)}")
    if not profile.get("twin"):
        raise ValueError("the profile has no [twin] section — make `ssh twin` work, then run: twinpc detect")
    v = values(profile, repo)
    steps = []
    for name in FEATURES:
        if only and name not in only:
            continue
        fsteps = BUILDERS[name](profile, Path(repo), v)
        # a package that can't be installed makes the rest of its feature pointless: skip it all
        missing = [s for s in fsteps if s.skip_reason and s.id.endswith(".packages")]
        steps += missing[:1] if missing else fsteps
    return steps
