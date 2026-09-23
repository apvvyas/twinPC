# Portable twinPC Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A `twinpc` tool that detects both machines into a profile, installs every twinPC feature as idempotent steps through platform adapters, and reports health with `doctor` — with today's Ubuntu/GNOME + Arch/Hyprland/AMD setup expressed as adapters and still fully working.

**Architecture:** `tool/probe.sh` (read-only, POSIX sh) reports facts per machine; `tool/twinpc_lib/profile.py` merges them into `~/.config/twinpc/profile.toml` (+ a shell-sourceable `profile.env`); `steps.py` defines `Step` objects with `check`/`apply` run locally or over ssh by a `Runner`; `adapters/` map platform values (apt, pacman, gnome, hyprland, amd, mkinitcpio, cable) to steps and report the rest as `Unsupported`; `features.py` builds the ordered plan; `cli.py` exposes `detect`, `install`, `doctor`.

**Tech Stack:** Python 3.11+ stdlib only (`tomllib`, `argparse`, `dataclasses`, `subprocess`, `hashlib`, `base64`, `unittest`), POSIX sh, bash, systemd, ssh; Docker (optional) for probe tests on ubuntu/fedora/archlinux images.

**Spec:** `docs/superpowers/specs/2026-09-23-portable-twinpc-foundation-design.md`

## Global Constraints

- Linux family only; detected values: family `debian|fedora|arch|unknown`, pkg `apt|dnf|pacman|unknown`, desktop `gnome|kde|hyprland|sway|x11-other|none|unknown`, session `wayland|x11|none`, gpu `nvidia|amd|intel|none`.
- Adapters implemented in this sub-project: packages `apt`, `pacman`; desktop `gnome`, `hyprland`; gpu `amd`; bootunlock `mkinitcpio` (with bootloader `limine`); network `cable`. Planned (reported "not supported yet (planned)"): packages `dnf`; desktop `kde`, `sway`, `x11-other`; gpu `nvidia`, `intel`, `none`; bootunlock `dracut`, `initramfs-tools`; network `lan`.
- A feature is skipped (not an error) only when impossible or unsupported on that setup; the reason is printed by `install` and `doctor`.
- Profile at `${XDG_CONFIG_HOME:-~/.config}/twinpc/profile.toml`, `version = 1`; `detect` only fills missing keys unless `--force`; `[user]` keys are never overwritten.
- `twinpc` exit codes: 0 success, 1 a step/check failed or twin unreachable in `detect`, 2 usage error / missing or too-new profile.
- Root commands use `sudo -S` with the password asked at most once per machine per run (not at all if `sudo -n true` succeeds).
- The probe never uses sudo and never writes anything.
- Python code is stdlib only; the tool runs as `tool/twinpc` from the repo.
- No personal data in the repository (public): no usernames, MACs, hostnames or real addresses other than the NetworkManager defaults `10.42.0.1` / `10.42.0.11`.
- Every commit message ends with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Acceptance: on the author's machines `tool/twinpc doctor` shows every feature ✅ and `tool/twinpc install --dry-run` reports every step "already done"; the existing 54 tests keep passing.

## Review Focus

1. A twin reached over SSH has no `XDG_CURRENT_DESKTOP` in its environment — desktop/session must still be found from the logged-in session's processes — pinned by `test_desktop_found_from_session_process` (Task 1).
2. Running `detect` again after the user edited the profile must not undo the edit — pinned by `test_existing_values_win_without_force` (Task 2).
3. A step that needs sudo while no terminal is attached (agent / CI) must stop with a clear message, not hang on a password prompt — pinned by `test_root_without_terminal_stops_clearly` (Task 3).
4. A package that has no mapping for the detected package manager must skip that feature with a reason, not crash planning — pinned by `test_missing_package_mapping_is_unsupported` (Task 4).
5. On the author's already-configured machines, re-installing must change nothing — pinned by the dry-run acceptance in Task 7 and by `test_config_templates_match_installed_text` (Task 5), which fixes the exact lan-mouse config text the current installer writes.

---

### Task 1: Probe

**Files:**
- Create: `tool/probe.sh`
- Test: `tests/tool/__init__.py` (empty), `tests/tool/test_probe.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `tool/probe.sh` printing `key=value` lines for keys `family pkg desktop session gpu gpu_arch luks initramfs bootloader wired_iface wired_mac wol addr user`. Test hooks: `TWINPC_ROOT` (prefix for every file read, including `/proc`), `TWINPC_TOOLPATH` (replaces `PATH` for finding tools).

- [ ] **Step 1: Write the failing tests**

`tests/tool/__init__.py`: empty file.

`tests/tool/test_probe.py`:

```python
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PROBE = ROOT / "tool" / "probe.sh"


def run_probe(root, stubs, extra_env=None):
    env = {"PATH": os.environ["PATH"], "HOME": str(root), "TWINPC_ROOT": str(root), "TWINPC_TOOLPATH": str(stubs)}
    env.update(extra_env or {})
    out = subprocess.run(["sh", str(PROBE)], capture_output=True, text=True, env=env, check=True).stdout
    return dict(line.split("=", 1) for line in out.splitlines() if "=" in line)


def stub(dirpath, name, body="exit 0"):
    p = Path(dirpath, name)
    p.write_text(f"#!/bin/sh\n{body}\n")
    p.chmod(0o755)


class ProbeFixtureTests(unittest.TestCase):
    """A fake Arch/Hyprland/AMD twin built from files, so every branch is testable anywhere."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.root, self.stubs = self.tmp / "root", self.tmp / "stubs"
        self.stubs.mkdir()
        r = self.root
        (r / "etc").mkdir(parents=True)
        (r / "etc/os-release").write_text('NAME="Omarchy"\nID=omarchy\nID_LIKE=arch\n')
        (r / "sys/class/drm/card1/device").mkdir(parents=True)
        (r / "sys/class/drm/card1/device/vendor").write_text("0x1002\n")
        node = r / "sys/class/kfd/kfd/topology/nodes/1"
        node.mkdir(parents=True)
        (node / "properties").write_text("cpu_cores_count 0\ngfx_target_version 100302\n")
        eth = r / "sys/class/net/eth0"
        (eth / "device").mkdir(parents=True)
        (eth / "carrier").write_text("1\n")
        (eth / "address").write_text("aa:bb:cc:dd:ee:ff\n")
        (r / "sys/class/net/docker0").mkdir(parents=True)          # virtual: no device → ignored
        (r / "sys/class/net/docker0/carrier").write_text("1\n")
        proc = r / "proc/4242"
        proc.mkdir(parents=True)
        (proc / "environ").write_bytes(b"HOME=/x\0XDG_CURRENT_DESKTOP=Hyprland\0XDG_SESSION_TYPE=wayland\0")
        stub(self.stubs, "pacman")
        stub(self.stubs, "mkinitcpio")
        stub(self.stubs, "limine")
        stub(self.stubs, "lsblk", "echo disk; echo part; echo crypt")
        stub(self.stubs, "ip", 'echo "2: eth0    inet 10.42.0.11/24 brd 10.42.0.255 scope global eth0"')
        stub(self.stubs, "ethtool", 'printf "\\tSupports Wake-on: pumbg\\n\\tWake-on: g\\n"')

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_arch_family_and_package_manager(self):
        p = run_probe(self.root, self.stubs)
        self.assertEqual((p["family"], p["pkg"]), ("arch", "pacman"))

    def test_desktop_found_from_session_process(self):
        # over ssh there is no XDG_CURRENT_DESKTOP: it must come from the session's processes
        p = run_probe(self.root, self.stubs)
        self.assertEqual((p["desktop"], p["session"]), ("hyprland", "wayland"))

    def test_desktop_from_own_environment_wins(self):
        p = run_probe(self.root, self.stubs, {"XDG_CURRENT_DESKTOP": "ubuntu:GNOME", "XDG_SESSION_TYPE": "wayland"})
        self.assertEqual(p["desktop"], "gnome")

    def test_amd_gpu_and_gfx_target(self):
        p = run_probe(self.root, self.stubs)
        self.assertEqual((p["gpu"], p["gpu_arch"]), ("amd", "gfx1032"))

    def test_nvidia_wins_over_integrated(self):
        (self.root / "sys/class/drm/card0/device").mkdir(parents=True)
        (self.root / "sys/class/drm/card0/device/vendor").write_text("0x10de\n")
        self.assertEqual(run_probe(self.root, self.stubs)["gpu"], "nvidia")

    def test_boot_facts(self):
        p = run_probe(self.root, self.stubs)
        self.assertEqual((p["luks"], p["initramfs"], p["bootloader"]), ("true", "mkinitcpio", "limine"))

    def test_wired_interface_facts(self):
        p = run_probe(self.root, self.stubs)
        self.assertEqual((p["wired_iface"], p["wired_mac"], p["addr"], p["wol"]),
                         ("eth0", "aa:bb:cc:dd:ee:ff", "10.42.0.11", "g"))

    def test_unknowns_are_reported_not_errors(self):
        for f in ("pacman", "mkinitcpio", "limine", "lsblk", "ethtool"):
            (self.stubs / f).unlink()
        (self.root / "etc/os-release").write_text("ID=plan9\n")
        p = run_probe(self.root, self.stubs, {"XDG_CURRENT_DESKTOP": "niri", "XDG_SESSION_TYPE": "wayland"})
        self.assertEqual((p["family"], p["pkg"], p["desktop"], p["luks"], p["initramfs"], p["bootloader"], p["wol"]),
                         ("unknown", "unknown", "unknown", "false", "unknown", "unknown", "unknown"))

    def test_probe_writes_nothing(self):
        before = sorted(str(p) for p in self.root.rglob("*"))
        run_probe(self.root, self.stubs)
        self.assertEqual(before, sorted(str(p) for p in self.root.rglob("*")))


@unittest.skipUnless(os.environ.get("TWINPC_DOCKER_TESTS") == "1", "set TWINPC_DOCKER_TESTS=1 to run the distro containers")
class ProbeDistroTests(unittest.TestCase):
    def probe_in(self, image):
        out = subprocess.run(["docker", "run", "--rm", "-i", image, "sh", "-s"], input=PROBE.read_text(),
                             capture_output=True, text=True, timeout=600, check=True).stdout
        return dict(line.split("=", 1) for line in out.splitlines() if "=" in line)

    def test_ubuntu(self):
        p = self.probe_in("ubuntu:24.04")
        self.assertEqual((p["family"], p["pkg"]), ("debian", "apt"))

    def test_fedora(self):
        p = self.probe_in("fedora:40")
        self.assertEqual((p["family"], p["pkg"]), ("fedora", "dnf"))

    def test_arch(self):
        p = self.probe_in("archlinux:latest")
        self.assertEqual((p["family"], p["pkg"]), ("arch", "pacman"))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd ~/projects/twinPC && python3 -m unittest tests.tool.test_probe -v`
Expected: errors — `sh: 0: cannot open …/tool/probe.sh` (CalledProcessError); the distro tests are skipped.

- [ ] **Step 3: Write the probe**

`tool/probe.sh` (then `chmod +x tool/probe.sh`):

```sh
#!/bin/sh
# probe.sh — read-only facts about this machine for `twinpc detect`: prints key=value lines.
# Never uses sudo, never writes. Test hooks: TWINPC_ROOT prefixes every file read (including /proc),
# TWINPC_TOOLPATH replaces PATH when looking for tools.
R=${TWINPC_ROOT:-}

have() {   # print the path of tool $1 if it is on TWINPC_TOOLPATH (or PATH)
  _old=$IFS; IFS=:
  for _d in ${TWINPC_TOOLPATH:-$PATH}; do
    if [ -x "$_d/$1" ]; then IFS=$_old; echo "$_d/$1"; return 0; fi
  done
  IFS=$_old; return 1
}
tool() { _t=$(have "$1") || return 127; shift; "$_t" "$@"; }
say() { printf '%s=%s\n' "$1" "$2"; }

# --- distribution family (ID, then ID_LIKE)
family=unknown
for i in $(sed -n 's/^ID=//p; s/^ID_LIKE=//p' "$R/etc/os-release" 2>/dev/null | tr -d '"'); do
  case $i in
    debian|ubuntu) family=debian; break ;;
    fedora|rhel|centos) family=fedora; break ;;
    arch|archlinux) family=arch; break ;;
  esac
done
say family "$family"

# --- package manager
if have apt-get >/dev/null; then pkg=apt
elif have dnf >/dev/null; then pkg=dnf
elif have pacman >/dev/null; then pkg=pacman
else pkg=unknown; fi
say pkg "$pkg"

# --- desktop and session: own environment, else the logged-in session's processes (over ssh)
cur=${XDG_CURRENT_DESKTOP:-}; stype=${XDG_SESSION_TYPE:-}
if [ -z "$cur" ]; then
  for e in "$R"/proc/[0-9]*/environ; do
    v=$( { tr '\0' '\n' < "$e"; } 2>/dev/null | sed -n 's/^XDG_CURRENT_DESKTOP=//p' | head -1)
    [ -n "$v" ] || continue
    cur=$v
    stype=$( { tr '\0' '\n' < "$e"; } 2>/dev/null | sed -n 's/^XDG_SESSION_TYPE=//p' | head -1)
    break
  done
fi
case $(printf '%s' "$cur" | tr 'A-Z' 'a-z') in
  *gnome*) desktop=gnome ;;
  *kde*|*plasma*) desktop=kde ;;
  *hyprland*) desktop=hyprland ;;
  *sway*) desktop=sway ;;
  '') desktop=none ;;
  *) if [ "$stype" = x11 ]; then desktop=x11-other; else desktop=unknown; fi ;;
esac
case $stype in wayland|x11) ;; *) stype=none ;; esac
say desktop "$desktop"
say session "$stype"

# --- GPU: a discrete vendor wins over an integrated one
gpu=none
for f in "$R"/sys/class/drm/card*/device/vendor; do
  [ -r "$f" ] || continue
  case $(cat "$f") in
    0x10de) gpu=nvidia ;;
    0x1002) [ "$gpu" = nvidia ] || gpu=amd ;;
    0x8086) [ "$gpu" = none ] && gpu=intel ;;
  esac
done
say gpu "$gpu"
gpu_arch=
if [ "$gpu" = amd ]; then
  for p in "$R"/sys/class/kfd/kfd/topology/nodes/*/properties; do
    v=$(sed -n 's/^gfx_target_version //p' "$p" 2>/dev/null)
    [ -n "$v" ] && [ "$v" != 0 ] || continue
    gpu_arch=$(printf 'gfx%d%d%x' $((v / 10000)) $((v / 100 % 100)) $((v % 100)))
    break
  done
fi
say gpu_arch "$gpu_arch"

# --- disk encryption, initramfs tool, bootloader
if tool lsblk -rno TYPE 2>/dev/null | grep -qx crypt; then say luks true; else say luks false; fi
if have mkinitcpio >/dev/null; then initramfs=mkinitcpio
elif have dracut >/dev/null; then initramfs=dracut
elif have update-initramfs >/dev/null; then initramfs=initramfs-tools
else initramfs=unknown; fi
say initramfs "$initramfs"
if have limine >/dev/null; then bootloader=limine
elif have grub-mkconfig >/dev/null || have grub2-mkconfig >/dev/null; then bootloader=grub
elif have bootctl >/dev/null; then bootloader=systemd-boot
else bootloader=unknown; fi
say bootloader "$bootloader"

# --- wired port: a real (non-virtual, non-wireless) interface with a link
wired_iface=
for d in "$R"/sys/class/net/*; do
  n=${d##*/}
  [ "$n" = lo ] && continue
  [ -d "$d/wireless" ] && continue
  [ -e "$d/device" ] || continue
  [ "$(cat "$d/carrier" 2>/dev/null)" = 1 ] || continue
  wired_iface=$n; break
done
say wired_iface "$wired_iface"
wired_mac=; addr=; wol=unknown
if [ -n "$wired_iface" ]; then
  wired_mac=$(cat "$R/sys/class/net/$wired_iface/address" 2>/dev/null)
  addr=$(tool ip -4 -o addr show dev "$wired_iface" 2>/dev/null | awk '{print $4}' | cut -d/ -f1 | head -1)
  w=$(tool ethtool "$wired_iface" 2>/dev/null | sed -n 's/^[[:space:]]*Wake-on:[[:space:]]*//p' | head -1)
  [ -n "$w" ] && wol=$w
fi
say wired_mac "$wired_mac"
say addr "$addr"
say wol "$wol"
say user "$(id -un)"
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd ~/projects/twinPC && python3 -m unittest tests.tool.test_probe -v`
Expected: 9 fixture tests PASS, 3 distro tests skipped.

Then the containers once (pulls ~300 MB): `TWINPC_DOCKER_TESTS=1 python3 -m unittest tests.tool.test_probe.ProbeDistroTests -v`
Expected: 3 PASS. (If there is no network or no Docker, record that in the ledger and continue — the fixture tests cover the logic.)

Then on the real machines: `sh tool/probe.sh; ssh twin 'sh -s' < tool/probe.sh`
Expected: main shows `family=debian pkg=apt desktop=gnome`; twin shows `family=arch pkg=pacman desktop=hyprland session=wayland gpu=amd gpu_arch=gfx1032 luks=true initramfs=mkinitcpio bootloader=limine` and `addr=10.42.0.11`.

- [ ] **Step 5: Commit**

```bash
git add tool/probe.sh tests/tool/__init__.py tests/tool/test_probe.py
git commit -m "feat(tool): read-only machine probe for twinpc detect

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Profile

**Files:**
- Create: `tool/twinpc_lib/__init__.py` (empty), `tool/twinpc_lib/profile.py`
- Test: `tests/tool/test_profile.py`

**Interfaces:**
- Consumes: probe output text (Task 1).
- Produces (`tool/twinpc_lib/profile.py`):
  - `VERSION = 1`, `MACHINE_KEYS: list[str]`, `class ProfileError(Exception)`
  - `config_dir() -> Path`, `profile_path() -> Path`
  - `parse_probe(text: str) -> dict[str, str]`
  - `build(main_probe: dict, twin_probe: dict | None, existing: dict | None = None, force: bool = False, twin_host: str = "twin") -> dict`
  - `dumps(profile: dict) -> str`, `to_env(profile: dict) -> str`
  - `save(profile: dict, path: Path | None = None) -> Path` (also writes `profile.env` next to it)
  - `load(path: Path | None = None) -> dict` (raises `ProfileError` when missing or newer than `VERSION`)
  - Profile shape: `{"version": 1, "network": {"twin_host", "mode": "cable"|"lan", "main_iface", "twin_addr"}, "main": {<MACHINE_KEYS>}, "twin": {<MACHINE_KEYS>}, "user": {"twin_user", "twin_mac"}}` with `luks` a bool.

- [ ] **Step 1: Write the failing tests**

`tests/tool/test_profile.py`:

```python
import os
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tool"))
from twinpc_lib import profile as P  # noqa: E402

MAIN = {"family": "debian", "pkg": "apt", "desktop": "gnome", "session": "wayland", "gpu": "none", "gpu_arch": "",
        "luks": "false", "initramfs": "initramfs-tools", "bootloader": "grub", "wired_iface": "enp3s0",
        "wired_mac": "11:22:33:44:55:66", "wol": "unknown", "addr": "10.42.0.1", "user": "me"}
TWIN = {"family": "arch", "pkg": "pacman", "desktop": "hyprland", "session": "wayland", "gpu": "amd",
        "gpu_arch": "gfx1032", "luks": "true", "initramfs": "mkinitcpio", "bootloader": "limine",
        "wired_iface": "eth0", "wired_mac": "aa:bb:cc:dd:ee:ff", "wol": "g", "addr": "10.42.0.11", "user": "tw"}


class ProfileTests(unittest.TestCase):
    def test_parse_probe(self):
        self.assertEqual(P.parse_probe("family=arch\npkg=pacman\n\nnoise\naddr=\n"),
                         {"family": "arch", "pkg": "pacman", "addr": ""})

    def test_build_cable(self):
        p = P.build(MAIN, TWIN)
        self.assertEqual(p["version"], 1)
        self.assertEqual(p["network"], {"twin_host": "twin", "mode": "cable", "main_iface": "enp3s0", "twin_addr": "10.42.0.11"})
        self.assertIs(p["twin"]["luks"], True)
        self.assertIs(p["main"]["luks"], False)
        self.assertEqual(p["user"], {"twin_user": "tw", "twin_mac": "aa:bb:cc:dd:ee:ff"})

    def test_build_lan(self):
        p = P.build({**MAIN, "addr": "192.168.1.20"}, {**TWIN, "addr": "192.168.1.30"})
        self.assertEqual((p["network"]["mode"], p["network"]["twin_addr"]), ("lan", "192.168.1.30"))

    def test_twin_unreachable_keeps_main(self):
        p = P.build(MAIN, None)
        self.assertEqual(p["twin"], {})
        self.assertEqual(p["main"]["pkg"], "apt")

    def test_existing_values_win_without_force(self):
        old = P.build(MAIN, TWIN)
        old["twin"]["desktop"] = "sway"             # user edited the file
        old["user"]["twin_user"] = "someone"
        new = P.build(MAIN, TWIN, existing=old)
        self.assertEqual(new["twin"]["desktop"], "sway")
        self.assertEqual(new["user"]["twin_user"], "someone")

    def test_missing_keys_are_filled(self):
        old = P.build(MAIN, TWIN)
        del old["twin"]["gpu_arch"]
        self.assertEqual(P.build(MAIN, TWIN, existing=old)["twin"]["gpu_arch"], "gfx1032")

    def test_force_refreshes_detected_but_keeps_user(self):
        old = P.build(MAIN, TWIN)
        old["twin"]["desktop"] = "sway"
        old["user"]["twin_user"] = "someone"
        new = P.build(MAIN, TWIN, existing=old, force=True)
        self.assertEqual(new["twin"]["desktop"], "hyprland")
        self.assertEqual(new["user"]["twin_user"], "someone")

    def test_round_trip_and_env(self):
        with tempfile.TemporaryDirectory() as d:
            path = P.save(P.build(MAIN, TWIN), Path(d, "profile.toml"))
            with open(path, "rb") as f:
                self.assertEqual(tomllib.load(f)["twin"]["gpu_arch"], "gfx1032")
            self.assertEqual(P.load(path)["network"]["mode"], "cable")
            env = Path(d, "profile.env").read_text()
            self.assertIn("TWIN_ADDR=${TWIN_ADDR:-10.42.0.11}", env)
            self.assertIn("TWIN_MAC=${TWIN_MAC:-aa:bb:cc:dd:ee:ff}", env)
            self.assertIn("TWIN_IFACE=${TWIN_IFACE:-enp3s0}", env)

    def test_quotes_are_escaped(self):
        p = P.build(MAIN, TWIN)
        p["user"]["twin_user"] = 'a"b\\c'
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(P.load(P.save(p, Path(d, "p.toml")))["user"]["twin_user"], 'a"b\\c')

    def test_missing_and_newer_profiles(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaisesRegex(P.ProfileError, "twinpc detect"):
                P.load(Path(d, "none.toml"))
            Path(d, "new.toml").write_text("version = 2\n")
            with self.assertRaisesRegex(P.ProfileError, "upgrade twinpc"):
                P.load(Path(d, "new.toml"))

    def test_default_path_follows_xdg(self):
        old = os.environ.get("XDG_CONFIG_HOME")
        os.environ["XDG_CONFIG_HOME"] = "/tmp/xdg-test"
        try:
            self.assertEqual(P.profile_path(), Path("/tmp/xdg-test/twinpc/profile.toml"))
        finally:
            if old is None:
                del os.environ["XDG_CONFIG_HOME"]
            else:
                os.environ["XDG_CONFIG_HOME"] = old


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd ~/projects/twinPC && python3 -m unittest tests.tool.test_profile -v`
Expected: ERROR — `ModuleNotFoundError: No module named 'twinpc_lib'`.

- [ ] **Step 3: Implement the profile module**

`tool/twinpc_lib/__init__.py`: empty file.

`tool/twinpc_lib/profile.py`:

```python
"""The profile: what twinpc knows about the two machines (~/.config/twinpc/profile.toml).

`detect` writes it from the probe output of both machines; the user may edit it. When a profile
already exists, detected values only fill in missing keys unless force=True; [user] keys are never
overwritten. A shell-sourceable profile.env is written next to it for the `twin` command and hook.
"""
import os
import shlex
import tomllib
from pathlib import Path

VERSION = 1
MACHINE_KEYS = ["family", "pkg", "desktop", "session", "gpu", "gpu_arch", "luks", "initramfs",
                "bootloader", "wired_iface", "wired_mac", "wol", "addr"]
CABLE_MAIN_ADDR = "10.42.0.1"          # NetworkManager "Shared to other computers" address
SECTIONS = ("network", "main", "twin", "user")


class ProfileError(Exception):
    pass


def config_dir():
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "twinpc"


def profile_path():
    return config_dir() / "profile.toml"


def parse_probe(text):
    out = {}
    for line in text.splitlines():
        key, sep, value = line.partition("=")
        if sep and key.strip():
            out[key.strip()] = value.strip()
    return out


def _machine(probe):
    m = {k: probe.get(k, "") for k in MACHINE_KEYS}
    m["luks"] = probe.get("luks") == "true"
    return m


def build(main_probe, twin_probe, existing=None, force=False, twin_host="twin"):
    fresh = {
        "version": VERSION,
        "network": {
            "twin_host": twin_host,
            "mode": "cable" if main_probe.get("addr") == CABLE_MAIN_ADDR else "lan",
            "main_iface": main_probe.get("wired_iface", ""),
        },
        "main": _machine(main_probe),
        "twin": _machine(twin_probe) if twin_probe else {},
        "user": {},
    }
    if twin_probe:
        fresh["network"]["twin_addr"] = twin_probe.get("addr", "")
        fresh["user"] = {"twin_user": twin_probe.get("user", ""), "twin_mac": twin_probe.get("wired_mac", "")}
    if not existing:
        return fresh
    merged = {"version": VERSION}
    for section in SECTIONS:
        old, new = existing.get(section, {}), fresh.get(section, {})
        if section == "user" or not force:
            merged[section] = {**new, **old}     # existing values win; only missing keys are filled
        else:
            merged[section] = {**old, **new}     # --force: detected values win
    return merged


def _toml_value(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    s = str(v).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{s}"'


def dumps(profile):
    lines = ["# written by `twinpc detect` — edit freely; detect only fills in missing values",
             f"version = {profile.get('version', VERSION)}", ""]
    for section in SECTIONS:
        lines.append(f"[{section}]")
        for key, value in profile.get(section, {}).items():
            lines.append(f"{key} = {_toml_value(value)}")
        lines.append("")
    return "\n".join(lines)


def to_env(profile):
    n, u = profile.get("network", {}), profile.get("user", {})
    values = {"TWIN_HOST": n.get("twin_host", ""), "TWIN_ADDR": n.get("twin_addr", ""),
              "TWIN_IFACE": n.get("main_iface", ""), "TWIN_USER": u.get("twin_user", ""),
              "TWIN_MAC": u.get("twin_mac", "")}
    lines = ["# generated by `twinpc detect` from profile.toml — do not edit; environment variables win"]
    lines += [f"{k}=${{{k}:-{shlex.quote(v)}}}" for k, v in values.items() if v]
    return "\n".join(lines) + "\n"


def save(profile, path=None):
    path = Path(path or profile_path())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dumps(profile))
    path.with_name("profile.env").write_text(to_env(profile))
    return path


def load(path=None):
    path = Path(path or profile_path())
    if not path.exists():
        raise ProfileError(f"no profile at {path} — run: twinpc detect")
    with open(path, "rb") as f:
        profile = tomllib.load(f)
    if profile.get("version", 0) > VERSION:
        raise ProfileError(f"profile version {profile['version']} is newer than this twinpc ({VERSION}) — upgrade twinpc")
    return profile
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd ~/projects/twinPC && python3 -m unittest tests.tool.test_profile -v`
Expected: 11 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add tool/twinpc_lib/__init__.py tool/twinpc_lib/profile.py tests/tool/test_profile.py
git commit -m "feat(tool): twinpc profile — build, merge, save, load, profile.env

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Step engine

**Files:**
- Create: `tool/twinpc_lib/steps.py`
- Test: `tests/tool/test_steps.py`, `tests/tool/fakes.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces (`tool/twinpc_lib/steps.py`):
  - `Result(rc: int, out: str)`, `class StepError(Exception)`, `class RootNeedsTerminal(StepError)`
  - `Runner(twin_host: str, ask=getpass.getpass, isatty=sys.stdin.isatty)` with `run(machine, cmd, root=False, input=None) -> Result`
  - `Ctx(profile, runner, repo, yes=False, allow_root=True, confirm=<ask y/N>)` with `run(machine, cmd, root=False, input=None) -> Result`
  - `Step(id, feature, machine, root, describe, check, apply, manual="", skip_reason="", check_root=False)`
  - helpers: `cmd_step(id, feature, machine, describe, check, apply, root=False, check_root=None, input=None)`, `manual_step(id, feature, machine, describe, manual, check=None, check_root=False)`, `file_step(id, feature, machine, dest, content, root=False, mode=None, after=None, describe=None)` (`content`: str or `Callable[[Ctx], str]`), `line_step(id, feature, machine, path, line, match=None, describe=None)`, `unit_step(id, feature, machine, unit, user=True, now=True)`, `unsupported_step(feature, machine, reason)`
  - `run_steps(steps, ctx, dry_run=False, out=print) -> int` (0 all done/skipped, 1 first failure)
- Test helper `tests/tool/fakes.py`: `FakeRunner(responses)` — `responses` is a list of `(machine, substring, rc, out)`; the first match wins; unmatched commands return `Result(1, "")`; every call is recorded in `.calls` as `(machine, cmd, root, input)`.

- [ ] **Step 1: Write the fake runner and the failing tests**

`tests/tool/fakes.py`:

```python
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tool"))
from twinpc_lib.steps import Result  # noqa: E402


class FakeRunner:
    def __init__(self, responses=()):
        self.responses = list(responses)
        self.calls = []

    def run(self, machine, cmd, root=False, input=None):
        self.calls.append((machine, cmd, root, input))
        for m, sub, rc, out in self.responses:
            if m == machine and sub in cmd:
                return Result(rc, out)
        return Result(1, "")
```

`tests/tool/test_steps.py`:

```python
import hashlib
import unittest

from tests.tool.fakes import FakeRunner
from twinpc_lib import steps as S


def ctx(runner, **kw):
    return S.Ctx(profile={}, runner=runner, repo="/repo", **kw)


class HelperTests(unittest.TestCase):
    def test_cmd_step_check_and_apply(self):
        r = FakeRunner([("twin", "is-it-done", 0, "")])
        st = S.cmd_step("f.twin.x", "f", "twin", "do x", check="is-it-done", apply="do-it", root=True, check_root=False)
        self.assertTrue(st.check(ctx(r)))
        self.assertEqual(r.calls[-1], ("twin", "is-it-done", False, None))
        with self.assertRaises(S.StepError):
            st.apply(ctx(r))                       # "do-it" has no response → rc 1
        self.assertEqual(r.calls[-1][2], True)     # apply ran as root

    def test_file_step_compares_hash_and_writes_content(self):
        content = "hello\n"
        digest = hashlib.sha256(content.encode()).hexdigest()
        st = S.file_step("f.main.file", "f", "main", "$HOME/x.conf", content, mode="600", after="echo after")
        self.assertTrue(st.check(ctx(FakeRunner([("main", "sha256sum", 0, digest)]))))
        self.assertFalse(st.check(ctx(FakeRunner([("main", "sha256sum", 0, "other")]))))
        r = FakeRunner([("main", "cat >", 0, "")])
        st.apply(ctx(r))
        machine, cmd, root, data = r.calls[-1]
        self.assertIn('cat > "$HOME/x.conf"', cmd)
        self.assertIn('chmod 600 "$HOME/x.conf"', cmd)
        self.assertIn("echo after", cmd)
        self.assertEqual(data, content)

    def test_file_step_content_can_depend_on_ctx(self):
        st = S.file_step("f.main.file", "f", "main", "/x", lambda c: c.profile["v"])
        c = S.Ctx(profile={"v": "abc"}, runner=FakeRunner([("main", "cat >", 0, "")]), repo="/r")
        st.apply(c)
        self.assertEqual(c.runner.calls[-1][3], "abc")

    def test_line_step(self):
        st = S.line_step("f.main.line", "f", "main", "~/.bashrc", "source x", match="x")
        r = FakeRunner()
        st.check(ctx(r))
        self.assertIn("grep -qF -- x ~/.bashrc", r.calls[-1][1])
        st.apply(ctx(FakeRunner([("main", "printf", 0, "")])))

    def test_unit_step_user_and_system(self):
        r = FakeRunner([("main", "is-enabled", 0, "")])
        u = S.unit_step("f.main.u", "f", "main", "a.service")
        u.check(ctx(r))
        self.assertIn("systemctl --user is-enabled --quiet a.service", r.calls[-1][1])
        s = S.unit_step("f.twin.s", "f", "twin", "b", user=False, now=False)
        self.assertTrue(s.root)
        r2 = FakeRunner([("twin", "enable", 0, "")])
        s.apply(ctx(r2))
        self.assertEqual(r2.calls[-1][1], "systemctl daemon-reload && systemctl enable b")


class RunStepsTests(unittest.TestCase):
    def step(self, id, root=False):
        return S.cmd_step(id, "f", "main", f"do {id}", check=f"check-{id}", apply=f"apply-{id}", root=root)

    def test_done_steps_are_skipped_and_others_applied(self):
        r = FakeRunner([("main", "check-a", 0, ""), ("main", "apply-b", 0, "")])
        # after apply-b, check-b must pass: make it pass on the second call
        calls = {"n": 0}
        orig = r.run

        def run(machine, cmd, root=False, input=None):
            if cmd == "check-b":
                calls["n"] += 1
                return S.Result(0 if calls["n"] > 1 else 1, "")
            return orig(machine, cmd, root, input)
        r.run = run
        lines = []
        rc = S.run_steps([self.step("a"), self.step("b")], ctx(r), out=lines.append)
        self.assertEqual(rc, 0)
        self.assertIn("✓  a: already done", "\n".join(lines))
        self.assertIn("✓  b: done", "\n".join(lines))

    def test_stops_at_first_failure(self):
        r = FakeRunner([])                          # every check and apply fails
        lines = []
        rc = S.run_steps([self.step("a"), self.step("b")], ctx(r), out=lines.append)
        self.assertEqual(rc, 1)
        self.assertTrue(any("✗  a:" in l for l in lines))
        self.assertFalse(any(" b:" in l for l in lines))

    def test_dry_run_changes_nothing(self):
        r = FakeRunner([("main", "check-a", 0, "")])
        lines = []
        rc = S.run_steps([self.step("a"), self.step("b", root=True)], ctx(r), dry_run=True, out=lines.append)
        self.assertEqual(rc, 0)
        self.assertFalse(any(c[1].startswith("apply-") for c in r.calls))
        self.assertIn("→  b: would do b [sudo]", "\n".join(lines))

    def test_skip_reason(self):
        lines = []
        rc = S.run_steps([S.unsupported_step("kvm", "twin", "desktop 'kde' is not supported yet (planned)")],
                         ctx(FakeRunner()), out=lines.append)
        self.assertEqual(rc, 0)
        self.assertIn("⏭  kvm.twin.unsupported: skipped — desktop 'kde' is not supported yet (planned)", lines)

    def test_manual_step_needs_confirmation(self):
        m = S.manual_step("f.twin.bios", "f", "twin", "enable WoL in BIOS", "Open the BIOS …", check="bios-ok")
        lines = []
        self.assertEqual(S.run_steps([m], ctx(FakeRunner(), confirm=lambda q: False), out=lines.append), 1)
        r = FakeRunner()
        calls = {"n": 0}

        def run(machine, cmd, root=False, input=None):
            calls["n"] += 1
            return S.Result(0 if calls["n"] > 1 else 1, "")
        r.run = run
        self.assertEqual(S.run_steps([m], ctx(r, confirm=lambda q: True), out=lines.append), 0)

    def test_no_root_checks_are_reported_not_run(self):
        st = S.cmd_step("f.twin.r", "f", "twin", "root thing", check="root-check", apply="x", root=True)
        r = FakeRunner([("twin", "root-check", 0, "")])
        lines = []
        rc = S.run_steps([st], ctx(r, allow_root=False), dry_run=True, out=lines.append)
        self.assertEqual(rc, 0)
        self.assertEqual(r.calls, [])
        self.assertIn("?  f.twin.r: needs sudo to check", lines)


class RunnerTests(unittest.TestCase):
    def test_root_without_terminal_stops_clearly(self):
        class R(S.Runner):
            def _exec(self, argv, data):
                return S.Result(1, "sudo: a password is required")   # sudo -n true fails
        runner = R("twin", ask=lambda p: "pw", isatty=lambda: False)
        with self.assertRaisesRegex(S.RootNeedsTerminal, "needs a terminal"):
            runner.run("twin", "whoami", root=True)

    def test_password_asked_once_per_machine(self):
        asked = []
        seen = []

        class R(S.Runner):
            def _exec(self, argv, data):
                seen.append((argv, data))
                return S.Result(1, "") if argv[-1].endswith("sudo -n true") else S.Result(0, "ok")
        runner = R("twin", ask=lambda p: asked.append(p) or "pw", isatty=lambda: True)
        runner.run("twin", "a", root=True)
        runner.run("twin", "b", root=True)
        runner.run("main", "c", root=True)
        self.assertEqual(len(asked), 2)                         # once for twin, once for main
        self.assertTrue(seen[-1][1].startswith("pw\n"))

    def test_twin_commands_go_over_ssh(self):
        seen = []

        class R(S.Runner):
            def _exec(self, argv, data):
                seen.append(argv)
                return S.Result(0, "")
        R("mytwin").run("twin", "echo hi")
        self.assertEqual(seen[0][:4], ["ssh", "-o", "BatchMode=yes", "mytwin"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd ~/projects/twinPC && python3 -m unittest tests.tool.test_steps -v`
Expected: ERROR — `ImportError: cannot import name 'Result' from 'twinpc_lib.steps'` (module missing).

- [ ] **Step 3: Implement the step engine**

`tool/twinpc_lib/steps.py`:

```python
"""Steps: small idempotent actions, each with a check, run on the main PC or on the twin.

A Step is "done" when its check passes. run_steps() checks each step, applies the ones that are
not done, checks again, and stops at the first failure. Manual steps (apply=None) show
instructions and wait for confirmation. Root commands run through `sudo -S`; the password is asked
at most once per machine per run, and not at all when `sudo -n true` already works.
"""
import getpass
import hashlib
import shlex
import subprocess
import sys
from dataclasses import dataclass
from typing import Callable, Optional


@dataclass
class Result:
    rc: int
    out: str


class StepError(Exception):
    pass


class RootNeedsTerminal(StepError):
    def __init__(self, machine):
        super().__init__(f"a step needs sudo on the {machine} and needs a terminal for the password "
                         f"(run twinpc in a terminal, or use --no-root to skip root checks)")


class Runner:
    """Runs shell commands on "main" (locally) or "twin" (over ssh)."""

    def __init__(self, twin_host, ask=getpass.getpass, isatty=None):
        self.twin_host = twin_host
        self.ask = ask
        self.isatty = isatty or sys.stdin.isatty
        self.passwords = {}

    def _argv(self, machine, cmd):
        if machine == "main":
            return ["bash", "-c", cmd]
        return ["ssh", "-o", "BatchMode=yes", self.twin_host, cmd]

    def _exec(self, argv, data):
        p = subprocess.run(argv, input=data, capture_output=True, text=True)
        return Result(p.returncode, (p.stdout + p.stderr).strip())

    def _password(self, machine):
        if machine not in self.passwords:
            if self._exec(self._argv(machine, "sudo -n true"), "").rc == 0:
                self.passwords[machine] = ""          # passwordless sudo
            elif not self.isatty():
                raise RootNeedsTerminal(machine)
            else:
                self.passwords[machine] = self.ask(f"[sudo] password on the {machine}: ")
        return self.passwords[machine]

    def run(self, machine, cmd, root=False, input=None):
        data = input or ""
        if root:
            data = self._password(machine) + "\n" + data
            cmd = f"sudo -S -p '' bash -c {shlex.quote(cmd)}"
        elif machine == "twin":
            cmd = f"bash -c {shlex.quote(cmd)}"
        return self._exec(self._argv(machine, cmd), data)


def _ask_yes(question):
    try:
        return input(f"{question} [y/N] ").strip().lower() == "y"
    except EOFError:
        return False


@dataclass
class Ctx:
    profile: dict
    runner: object
    repo: object
    yes: bool = False
    allow_root: bool = True
    confirm: Callable[[str], bool] = _ask_yes

    def run(self, machine, cmd, root=False, input=None):
        return self.runner.run(machine, cmd, root=root, input=input)


@dataclass
class Step:
    id: str
    feature: str
    machine: str
    root: bool
    describe: str
    check: Optional[Callable[[Ctx], bool]]
    apply: Optional[Callable[[Ctx], None]]
    manual: str = ""
    skip_reason: str = ""
    check_root: bool = False


def _ok(ctx, machine, cmd, root=False):
    return ctx.run(machine, cmd, root=root).rc == 0


def _must(ctx, machine, cmd, root=False, input=None):
    r = ctx.run(machine, cmd, root=root, input=input)
    if r.rc != 0:
        first = cmd.strip().splitlines()[0][:90]
        raise StepError(f"`{first}` failed (exit {r.rc}): {r.out[-400:]}")


def cmd_step(id, feature, machine, describe, check, apply, root=False, check_root=None, input=None):
    cr = root if check_root is None else check_root
    return Step(id, feature, machine, root, describe,
                (lambda ctx: _ok(ctx, machine, check, cr)) if check else None,
                lambda ctx: _must(ctx, machine, apply, root, input),
                check_root=cr)


def manual_step(id, feature, machine, describe, manual, check=None, check_root=False):
    return Step(id, feature, machine, False, describe,
                (lambda ctx: _ok(ctx, machine, check, check_root)) if check else None,
                None, manual=manual, check_root=check_root)


def file_step(id, feature, machine, dest, content, root=False, mode=None, after=None, describe=None):
    """Write `content` (a string, or a function of the Ctx) to `dest` ($HOME allowed)."""
    def text(ctx):
        return content(ctx) if callable(content) else content

    def check(ctx):
        try:
            want = hashlib.sha256(text(ctx).encode()).hexdigest()
        except StepError:
            return False                                  # e.g. a certificate it depends on is missing
        r = ctx.run(machine, f'sha256sum "{dest}" 2>/dev/null | cut -d" " -f1', root=root)
        return r.rc == 0 and r.out.strip() == want

    def apply(ctx):
        cmd = f'mkdir -p "$(dirname "{dest}")" && cat > "{dest}"'
        if mode:
            cmd += f' && chmod {mode} "{dest}"'
        if after:
            cmd += f" && {after}"
        _must(ctx, machine, cmd, root=root, input=text(ctx))

    return Step(id, feature, machine, root, describe or f"write {dest}", check, apply, check_root=root)


def line_step(id, feature, machine, path, line, match=None, describe=None):
    """Append `line` to `path` unless a line containing `match` (default: the line) is there."""
    return cmd_step(id, feature, machine, describe or f"add a line to {path}",
                    check=f"grep -qF -- {shlex.quote(match or line)} {path} 2>/dev/null",
                    apply=f"printf '%s\\n' {shlex.quote(line)} >> {path}")


def unit_step(id, feature, machine, unit, user=True, now=True):
    sc = "systemctl --user" if user else "systemctl"
    check = f"{sc} is-enabled --quiet {unit}" + (f" && {sc} is-active --quiet {unit}" if now else "")
    apply = f"{sc} daemon-reload && {sc} enable {'--now ' if now else ''}{unit}"
    return cmd_step(id, feature, machine, f"enable {unit}", check, apply, root=not user, check_root=False)


def unsupported_step(feature, machine, reason):
    return Step(f"{feature}.{machine}.unsupported", feature, machine, False, "", None, None, skip_reason=reason)


def run_steps(steps, ctx, dry_run=False, out=print):
    for s in steps:
        if s.skip_reason:
            out(f"⏭  {s.id}: skipped — {s.skip_reason}")
            continue
        if s.check_root and not ctx.allow_root:
            out(f"?  {s.id}: needs sudo to check")
            continue
        try:
            if s.check and s.check(ctx):
                out(f"✓  {s.id}: already done")
                continue
            if dry_run:
                out(f"→  {s.id}: would {s.describe}" + (" (manual)" if s.apply is None else "")
                    + (" [sudo]" if s.root else ""))
                continue
            if s.apply is None:
                out(f"✋ {s.id}: {s.describe}\n   {s.manual}")
                if not (ctx.yes or ctx.confirm("   done?")):
                    out(f"✗  {s.id}: not confirmed — stopping")
                    return 1
            else:
                out(f"…  {s.id}: {s.describe}")
                s.apply(ctx)
            if s.check and not s.check(ctx):
                out(f"✗  {s.id}: still not done after applying it — stopping")
                return 1
            out(f"✓  {s.id}: done")
        except StepError as e:
            out(f"✗  {s.id}: {e}")
            return 1
    return 0
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd ~/projects/twinPC && python3 -m unittest tests.tool.test_steps -v`
Expected: 14 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add tool/twinpc_lib/steps.py tests/tool/test_steps.py tests/tool/fakes.py
git commit -m "feat(tool): step engine — checks, idempotent apply, manual steps, sudo once per machine

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Adapters

**Files:**
- Create: `tool/packages.toml`, `tool/twinpc_lib/adapters/__init__.py`, `tool/twinpc_lib/adapters/base.py`, `tool/twinpc_lib/adapters/packages.py`, `tool/twinpc_lib/adapters/network.py`, `tool/twinpc_lib/adapters/desktop.py`, `tool/twinpc_lib/adapters/gpu.py`, `tool/twinpc_lib/adapters/bootunlock.py`
- Modify: `tool/twinpc_lib/steps.py` — `unsupported_step` gains a `tag` parameter
- Test: `tests/tool/test_adapters.py`

**Interfaces:**
- Consumes: `cmd_step`, `manual_step`, `unsupported_step`, `Step` (Task 3).
- Produces:
  - `adapters.get(kind: str, value: str) -> adapter | Unsupported` for kinds `packages`, `desktop`, `gpu`, `bootunlock`, `network`
  - `adapters.base.Unsupported(kind, value, planned)` with `.reason() -> str` ("… is not supported yet (planned)" / "… is not supported")
  - `adapters.packages.pkg_step(feature, machine, adapter, logical: list[str], tag="packages") -> Step`
  - `adapters.network.ssh_config(repo, v) -> (twin_block: str, unlock_block: str)`; `Cable().steps(profile, repo, v) -> list[Step]`
  - `Gnome().shortcut_steps(feature, key, name, binding, command) -> list[Step]`; `Hyprland().remote_desktop_steps(feature, pk) -> list[Step]`; `Hyprland().gui_steps(feature, pk, v) -> list[Step]`
  - `adapters.gpu.hsa_override(gfx: str) -> str`; `Amd().ai_stack_steps(profile, repo, v, pk) -> list[Step]`
  - `Mkinitcpio().unlock_steps(profile, repo, v, pk) -> list[Step]`
  - `v` is the values dict built by Task 5's `values()`: keys `host addr main_addr user twin_if side repo home`.
- `steps.unsupported_step(feature, machine, reason, tag="unsupported")` — id is `f"{feature}.{machine}.{tag}"`.

- [ ] **Step 1: Write the failing tests**

`tests/tool/test_adapters.py`:

```python
import unittest
from pathlib import Path

from tests.tool.fakes import FakeRunner
from twinpc_lib import adapters, steps as S
from twinpc_lib.adapters import base, bootunlock, desktop, gpu, network, packages

REPO = Path(__file__).resolve().parents[2]
V = {"host": "twin", "addr": "10.42.0.11", "main_addr": "10.42.0.1", "user": "tw", "twin_if": "eth0",
     "side": "left", "repo": str(REPO), "home": "/home/me"}
PROFILE = {"network": {"mode": "cable", "twin_host": "twin", "twin_addr": "10.42.0.11"},
           "twin": {"luks": True, "initramfs": "mkinitcpio", "bootloader": "limine", "gpu": "amd", "gpu_arch": "gfx1032", "pkg": "pacman"},
           "main": {"pkg": "apt"}, "user": {"twin_user": "tw"}}


class RegistryTests(unittest.TestCase):
    def test_supported_values(self):
        self.assertIsInstance(adapters.get("packages", "apt"), packages.Apt)
        self.assertIsInstance(adapters.get("packages", "pacman"), packages.Pacman)
        self.assertIsInstance(adapters.get("desktop", "gnome"), desktop.Gnome)
        self.assertIsInstance(adapters.get("desktop", "hyprland"), desktop.Hyprland)
        self.assertIsInstance(adapters.get("gpu", "amd"), gpu.Amd)
        self.assertIsInstance(adapters.get("bootunlock", "mkinitcpio"), bootunlock.Mkinitcpio)
        self.assertIsInstance(adapters.get("network", "cable"), network.Cable)

    def test_planned_and_unknown_values(self):
        kde = adapters.get("desktop", "kde")
        self.assertIsInstance(kde, base.Unsupported)
        self.assertEqual(kde.reason(), "desktop 'kde' is not supported yet (planned)")
        self.assertEqual(adapters.get("gpu", "nvidia").reason(), "gpu 'nvidia' is not supported yet (planned)")
        self.assertEqual(adapters.get("desktop", "niri").reason(), "desktop 'niri' is not supported")
        self.assertEqual(adapters.get("packages", "").reason(), "packages 'unknown' is not supported")


class PackageTests(unittest.TestCase):
    def test_names_and_commands(self):
        apt, pac = packages.Apt(), packages.Pacman()
        self.assertEqual(apt.names(["sshfs", "docker"]), ["sshfs", "docker.io"])
        self.assertEqual(apt.check_cmd(["a", "b"]), "dpkg -s a b >/dev/null 2>&1")
        self.assertEqual(pac.install_cmd(["a"]), "pacman -S --needed --noconfirm a")

    def test_pkg_step(self):
        st = packages.pkg_step("gui", "twin", packages.Pacman(), ["wtype", "grim"])
        self.assertEqual((st.id, st.root, st.check_root), ("gui.twin.packages", True, False))
        r = FakeRunner([("twin", "pacman -Q wtype grim", 0, "")])
        self.assertTrue(st.check(S.Ctx({}, r, REPO)))

    def test_missing_package_mapping_is_unsupported(self):
        st = packages.pkg_step("gpu-stack", "twin", packages.Apt(), ["ollama-rocm"])
        self.assertEqual(st.id, "gpu-stack.twin.packages")
        self.assertEqual(st.skip_reason, "package 'ollama-rocm' is not available for apt yet (planned)")

    def test_unsupported_manager(self):
        st = packages.pkg_step("cli", "twin", adapters.get("packages", "dnf"), ["tmux"])
        self.assertEqual(st.skip_reason, "packages 'dnf' is not supported yet (planned)")


class NetworkTests(unittest.TestCase):
    def test_ssh_config_blocks(self):
        twin_block, unlock_block = network.ssh_config(REPO, {**V, "user": "alice"})
        self.assertTrue(twin_block.startswith("Host twin\n"))
        self.assertIn("User alice", twin_block)
        self.assertNotIn("twin-unlock", twin_block)
        self.assertIn("Host twin-unlock", unlock_block)
        self.assertIn("HostName 10.42.0.11", unlock_block)

    def test_ssh_config_other_host_and_address(self):
        twin_block, unlock_block = network.ssh_config(REPO, {**V, "host": "gpu", "addr": "10.42.0.50"})
        self.assertIn("Host gpu\n", twin_block)
        self.assertIn("HostName 10.42.0.50", twin_block)
        self.assertIn("Host gpu-unlock", unlock_block)

    def test_cable_steps(self):
        ids = [s.id for s in network.Cable().steps(PROFILE, REPO, V)]
        self.assertEqual(ids, ["connection.main.shared", "connection.main.ssh-alias",
                               "connection.main.key-login", "connection.twin.static-ip"])


class DesktopTests(unittest.TestCase):
    def test_gnome_shortcut(self):
        [st] = desktop.Gnome().shortcut_steps("desktop", "twin-desktop", "Twin", "<Super>F12", "/x/twin desktop")
        r = FakeRunner()
        st.check(S.Ctx({}, r, REPO))
        self.assertIn("custom-keybindings/twin-desktop/", r.calls[-1][1])
        self.assertIn("/usr/bin/gsettings", r.calls[-1][1])

    def test_hyprland_gui_steps(self):
        ids = [s.id for s in desktop.Hyprland().gui_steps("gui", packages.Pacman(), V)]
        self.assertEqual(ids, ["gui.twin.packages", "gui.twin.uinput", "gui.twin.input-group", "gui.twin.stay-awake"])


class GpuTests(unittest.TestCase):
    def test_hsa_override(self):
        self.assertEqual(gpu.hsa_override("gfx1032"), "10.3.0")
        self.assertEqual(gpu.hsa_override("gfx1031"), "10.3.0")
        self.assertEqual(gpu.hsa_override("gfx1102"), "11.0.0")
        self.assertEqual(gpu.hsa_override("gfx1100"), "")      # officially supported: no override
        self.assertEqual(gpu.hsa_override(""), "")

    def test_amd_steps(self):
        st = gpu.Amd().ai_stack_steps(PROFILE, REPO, V, packages.Pacman())
        self.assertEqual([s.id for s in st], ["gpu-stack.twin.packages", "gpu-stack.twin.ollama-config",
                                              "gpu-stack.twin.ollama", "gpu-stack.twin.docker", "gpu-stack.twin.groups",
                                              "gpu-stack.twin.firewall", "gpu-stack.twin.pytorch"])


class BootUnlockTests(unittest.TestCase):
    def test_mkinitcpio_limine(self):
        ids = [s.id for s in bootunlock.Mkinitcpio().unlock_steps(PROFILE, REPO, V, packages.Pacman())]
        self.assertEqual(ids, ["unlock.twin.packages", "unlock.twin.key", "unlock.twin.hooks",
                               "unlock.twin.cmdline", "unlock.twin.rebuild"])

    def test_other_bootloader_is_unsupported(self):
        prof = {**PROFILE, "twin": {**PROFILE["twin"], "bootloader": "grub"}}
        [st] = bootunlock.Mkinitcpio().unlock_steps(prof, REPO, V, packages.Pacman())
        self.assertEqual(st.skip_reason, "remote unlock with bootloader 'grub' is not supported yet (planned)")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd ~/projects/twinPC && python3 -m unittest tests.tool.test_adapters -v`
Expected: ERROR — `ModuleNotFoundError: No module named 'twinpc_lib.adapters'`.

- [ ] **Step 3: Give `unsupported_step` a tag**

In `tool/twinpc_lib/steps.py` replace the `unsupported_step` function with:

```python
def unsupported_step(feature, machine, reason, tag="unsupported"):
    return Step(f"{feature}.{machine}.{tag}", feature, machine, False, "", None, None, skip_reason=reason)
```

- [ ] **Step 4: Write the package table and the adapters**

`tool/packages.toml`:

```toml
# Logical package name → the package name for each package manager.
# A missing entry means "not available there yet": the feature that needs it is skipped with a reason.
tmux = { apt = "tmux", pacman = "tmux", dnf = "tmux" }
openssh = { apt = "openssh-server", pacman = "openssh", dnf = "openssh-server" }
ethtool = { apt = "ethtool", pacman = "ethtool", dnf = "ethtool" }
sshfs = { apt = "sshfs", pacman = "sshfs", dnf = "fuse-sshfs" }
remmina = { apt = "remmina", pacman = "remmina", dnf = "remmina" }
wayvnc = { apt = "wayvnc", pacman = "wayvnc", dnf = "wayvnc" }
wtype = { apt = "wtype", pacman = "wtype", dnf = "wtype" }
ydotool = { apt = "ydotool", pacman = "ydotool", dnf = "ydotool" }
grim = { apt = "grim", pacman = "grim", dnf = "grim" }
docker = { apt = "docker.io", pacman = "docker", dnf = "moby-engine" }
lan-mouse = { pacman = "lan-mouse" }
ollama-rocm = { pacman = "ollama-rocm" }
mkinitcpio-netconf = { pacman = "mkinitcpio-netconf" }
mkinitcpio-dropbear = { pacman = "mkinitcpio-dropbear" }
mkinitcpio-utils = { pacman = "mkinitcpio-utils" }
```

`tool/twinpc_lib/adapters/base.py`:

```python
from dataclasses import dataclass


@dataclass
class Unsupported:
    """What adapters.get() returns for a platform value twinpc cannot handle (yet)."""
    kind: str
    value: str
    planned: bool

    def reason(self):
        what = f"{self.kind} '{self.value or 'unknown'}'"
        return f"{what} is not supported yet (planned)" if self.planned else f"{what} is not supported"
```

`tool/twinpc_lib/adapters/packages.py`:

```python
"""Package managers: logical package names (tool/packages.toml) → install/check commands."""
import tomllib
from pathlib import Path

from ..steps import cmd_step, unsupported_step
from .base import Unsupported

TABLE = Path(__file__).resolve().parents[2] / "packages.toml"


def table():
    with open(TABLE, "rb") as f:
        return tomllib.load(f)


class PackageAdapter:
    manager = ""

    def names(self, logical):
        t = table()
        out = []
        for name in logical:
            real = t.get(name, {}).get(self.manager)
            if not real:
                raise KeyError(name)
            out.append(real)
        return out


class Apt(PackageAdapter):
    manager = "apt"

    def check_cmd(self, names):
        return f"dpkg -s {' '.join(names)} >/dev/null 2>&1"

    def install_cmd(self, names):
        return f"DEBIAN_FRONTEND=noninteractive apt-get install -y {' '.join(names)}"


class Pacman(PackageAdapter):
    manager = "pacman"

    def check_cmd(self, names):
        return f"pacman -Q {' '.join(names)} >/dev/null 2>&1"

    def install_cmd(self, names):
        return f"pacman -S --needed --noconfirm {' '.join(names)}"


def pkg_step(feature, machine, adapter, logical, tag="packages"):
    if isinstance(adapter, Unsupported):
        return unsupported_step(feature, machine, adapter.reason(), tag)
    try:
        names = adapter.names(logical)
    except KeyError as e:
        return unsupported_step(feature, machine,
                                f"package '{e.args[0]}' is not available for {adapter.manager} yet (planned)", tag)
    return cmd_step(f"{feature}.{machine}.{tag}", feature, machine, "install " + ", ".join(names),
                    adapter.check_cmd(names), adapter.install_cmd(names), root=True, check_root=False)
```

`tool/twinpc_lib/adapters/network.py`:

```python
"""How the two machines reach each other. `cable`: direct cable, main PC shares its connection."""
from pathlib import Path

from ..steps import cmd_step, manual_step


def ssh_config(repo, v):
    """main/ssh-config rendered for this setup → (twin alias block, twin-unlock alias block)."""
    text = (Path(repo) / "main" / "ssh-config").read_text()
    text = text.replace("@TWIN_USER@", v["user"]).replace("10.42.0.11", v["addr"])
    text = text.replace("Host twin-unlock", f"Host {v['host']}-unlock").replace("Host twin\n", f"Host {v['host']}\n")
    lines = text.splitlines()
    i = next(n for n, line in enumerate(lines) if line.startswith("Host ") and line.rstrip().endswith("-unlock"))
    j = i - 1 if i > 0 and lines[i - 1].startswith("#") else i
    return "\n".join(lines[:j]).strip() + "\n", "\n".join(lines[j:]).strip() + "\n"


class Cable:
    def steps(self, profile, repo, v):
        twin_block, _ = ssh_config(repo, v)
        addr_re = v["addr"].replace(".", "\\.")
        con = f'"$(nmcli -g GENERAL.CONNECTION device show {v["twin_if"]})"'
        return [
            manual_step("connection.main.shared", "connection", "main",
                        "share this PC's wired connection with the twin",
                        "Settings → Network → Wired → IPv4 → 'Shared to other computers', then plug in the cable.",
                        check="ip -4 -o addr show | grep -q ' 10\\.42\\.0\\.1/24 '"),
            cmd_step("connection.main.ssh-alias", "connection", "main",
                     f"add the ssh alias '{v['host']}' to ~/.ssh/config",
                     check=f"grep -q '^Host {v['host']}$' ~/.ssh/config 2>/dev/null",
                     apply="mkdir -p ~/.ssh && touch ~/.ssh/config && chmod 600 ~/.ssh/config"
                           " && { printf '\\n'; cat; } >> ~/.ssh/config",
                     input=twin_block),
            manual_step("connection.main.key-login", "connection", "main",
                        "log in to the twin with your SSH key",
                        f"In a terminal run: ssh-copy-id {v['user']}@{v['addr']}",
                        check=f"ssh -o BatchMode=yes -o ConnectTimeout=5 {v['host']} true"),
            cmd_step("connection.twin.static-ip", "connection", "twin",
                     f"give the twin the fixed address {v['addr']}",
                     check=f"ip -4 -o addr show | grep -q ' {addr_re}/24 '"
                           f" && nmcli -g ipv4.method connection show {con} | grep -qx manual",
                     apply=f"nmcli connection modify {con} ipv4.method manual ipv4.addresses {v['addr']}/24"
                           f" ipv4.gateway 10.42.0.1 ipv4.dns '10.42.0.1 1.1.1.1' && nmcli connection up {con}",
                     root=True, check_root=False),
        ]
```

`tool/twinpc_lib/adapters/desktop.py`:

```python
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
```

`tool/twinpc_lib/adapters/gpu.py`:

```python
"""GPU vendors: the AI stack on the twin (Ollama, Docker, PyTorch)."""
import re
from pathlib import Path

from ..steps import cmd_step, unit_step
from .packages import pkg_step


def hsa_override(gfx):
    """ROCm only ships kernels for some consumer GPUs; close relatives run with an override."""
    m = re.fullmatch(r"gfx(\d+)(\d)([0-9a-f])", gfx or "")
    if not m:
        return ""
    major, minor, step = m.group(1), m.group(2), m.group(3)
    if major == "10" and minor == "3" and step != "0":
        return "10.3.0"                     # RDNA2 consumer cards (RX 6000 series)
    if major == "11" and minor == "0" and step != "0":
        return "11.0.0"                     # RDNA3 cards other than gfx1100
    return ""


class Amd:
    def ai_stack_steps(self, profile, repo, v, pk):
        hsa = hsa_override(profile.get("twin", {}).get("gpu_arch", ""))
        override = ("[Service]\nEnvironment=\"OLLAMA_HOST=0.0.0.0:11434\"\n"
                    + (f'Environment="HSA_OVERRIDE_GFX_VERSION={hsa}"\n' if hsa else "")
                    + 'Environment="OLLAMA_KEEP_ALIVE=30m"\n')
        return [
            pkg_step("gpu-stack", "twin", pk, ["ollama-rocm", "docker"]),
            cmd_step("gpu-stack.twin.ollama-config", "gpu-stack", "twin",
                     "let Ollama serve the main PC and use the GPU",
                     check="grep -q 'OLLAMA_HOST=0.0.0.0' /etc/systemd/system/ollama.service.d/override.conf 2>/dev/null",
                     apply="mkdir -p /etc/systemd/system/ollama.service.d"
                           " && cat > /etc/systemd/system/ollama.service.d/override.conf && systemctl daemon-reload",
                     root=True, check_root=False, input=override),
            unit_step("gpu-stack.twin.ollama", "gpu-stack", "twin", "ollama", user=False),
            unit_step("gpu-stack.twin.docker", "gpu-stack", "twin", "docker", user=False),
            cmd_step("gpu-stack.twin.groups", "gpu-stack", "twin", f"add {v['user']} to docker, render and video",
                     check=f"[ \"$(id -nG {v['user']} | tr ' ' '\\n' | grep -cxE 'docker|render|video')\" = 3 ]",
                     apply=f"usermod -aG docker,render,video {v['user']}", root=True, check_root=False),
            cmd_step("gpu-stack.twin.firewall", "gpu-stack", "twin", "allow Ollama (11434/tcp) in the twin's firewall",
                     check="! command -v ufw >/dev/null || ufw status | grep -q '^11434/tcp'",
                     apply="ufw allow 11434/tcp", root=True),
            cmd_step("gpu-stack.twin.pytorch", "gpu-stack", "twin", "install PyTorch for ROCm in ~/ai-env (~4 GB)",
                     check='~/ai-env/bin/python -c "import torch" 2>/dev/null',
                     apply="bash -s", input=(Path(repo) / "twinpc" / "setup-ai-env.sh").read_text()),
        ]
```

`tool/twinpc_lib/adapters/bootunlock.py`:

```python
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
                     check="[ -n \"$(find /boot -iname '*.efi' -newer /etc/mkinitcpio.conf.d/zz-remote-unlock.conf"
                           " -newer /etc/default/limine 2>/dev/null | head -1)\" ]",
                     apply="limine-mkinitcpio", root=True),
        ]
```

`tool/twinpc_lib/adapters/__init__.py`:

```python
"""Platform adapters. get(kind, value) returns the adapter for a detected value, or Unsupported."""
from . import bootunlock, desktop, gpu, network, packages
from .base import Unsupported

PLANNED = {
    "packages": {"dnf"},
    "desktop": {"kde", "sway", "x11-other"},
    "gpu": {"nvidia", "intel", "none"},
    "bootunlock": {"dracut", "initramfs-tools"},
    "network": {"lan"},
}

REGISTRY = {
    ("packages", "apt"): packages.Apt(),
    ("packages", "pacman"): packages.Pacman(),
    ("desktop", "gnome"): desktop.Gnome(),
    ("desktop", "hyprland"): desktop.Hyprland(),
    ("gpu", "amd"): gpu.Amd(),
    ("bootunlock", "mkinitcpio"): bootunlock.Mkinitcpio(),
    ("network", "cable"): network.Cable(),
}


def get(kind, value):
    adapter = REGISTRY.get((kind, value))
    if adapter is not None:
        return adapter
    return Unsupported(kind, value or "unknown", value in PLANNED.get(kind, set()))
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd ~/projects/twinPC && python3 -m unittest tests.tool.test_adapters tests.tool.test_steps -v`
Expected: all PASS (15 adapter tests + 14 step tests).

- [ ] **Step 6: Commit**

```bash
git add tool/packages.toml tool/twinpc_lib/adapters tool/twinpc_lib/steps.py tests/tool/test_adapters.py
git commit -m "feat(tool): platform adapters — apt/pacman, cable, gnome/hyprland, amd, mkinitcpio

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Features as steps

**Files:**
- Create: `tool/twinpc_lib/features.py`
- Test: `tests/tool/test_features.py`

**Interfaces:**
- Consumes: steps helpers (Task 3); `adapters.get`, `Unsupported`, `pkg_step`, `ssh_config`, adapter step methods (Task 4); repo files `main/*`, `route/*`, `twinpc/setup-ai-env.sh`, `twinpc/main-pc-speakers.conf`, `twinpc/lan-mouse.service`.
- Produces (`tool/twinpc_lib/features.py`):
  - `FEATURES: list[str]` = `["connection", "cli", "gpu-stack", "routing", "mount", "power", "unlock", "kvm", "audio", "desktop", "gui", "nic-fix"]`
  - `values(profile, repo) -> dict` (keys `host addr main_addr user twin_if side repo home`)
  - `build_plan(profile, repo, only=None) -> list[Step]` — raises `ValueError` for unknown feature names or a profile without `[twin]`
  - `fingerprint(ctx, machine) -> str` (lan-mouse certificate SHA-256, lower-case, colon-separated)
  - `LAN_MOUSE_MAIN`, `LAN_MOUSE_TWIN` templates; `repo_unit(repo, rel) -> str`

- [ ] **Step 1: Write the failing tests**

`tests/tool/test_features.py`:

```python
import base64
import hashlib
import unittest
from pathlib import Path

from tests.tool.fakes import FakeRunner
from twinpc_lib import features as F, steps as S

REPO = Path(__file__).resolve().parents[2]
PROFILE = {
    "version": 1,
    "network": {"twin_host": "twin", "mode": "cable", "main_iface": "enp3s0", "twin_addr": "10.42.0.11"},
    "main": {"family": "debian", "pkg": "apt", "desktop": "gnome", "session": "wayland", "gpu": "none", "addr": "10.42.0.1"},
    "twin": {"family": "arch", "pkg": "pacman", "desktop": "hyprland", "session": "wayland", "gpu": "amd",
             "gpu_arch": "gfx1032", "luks": True, "initramfs": "mkinitcpio", "bootloader": "limine",
             "wired_iface": "eth0", "addr": "10.42.0.11"},
    "user": {"twin_user": "tw", "twin_mac": "aa:bb:cc:dd:ee:ff"},
}


def features_in_order(steps):
    seen = []
    for s in steps:
        if s.feature not in seen:
            seen.append(s.feature)
    return seen


class PlanTests(unittest.TestCase):
    def test_full_profile_is_fully_supported(self):
        steps = F.build_plan(PROFILE, REPO)
        self.assertEqual([s.id for s in steps if s.skip_reason], [])
        self.assertEqual(features_in_order(steps), F.FEATURES)
        ids = [s.id for s in steps]
        self.assertEqual(len(ids), len(set(ids)), "step ids must be unique")
        self.assertTrue(all(s.machine in ("main", "twin") for s in steps))

    def test_every_step_can_be_checked(self):
        # idempotency: without a check, install could not tell "already done"
        self.assertEqual([s.id for s in F.build_plan(PROFILE, REPO) if s.check is None], [])

    def test_key_steps_exist(self):
        ids = {s.id for s in F.build_plan(PROFILE, REPO)}
        for expected in ["connection.twin.static-ip", "cli.main.command", "gpu-stack.twin.pytorch",
                         "routing.main.hook", "mount.main.mount", "power.twin.wol-arm", "power.twin.no-sleep",
                         "unlock.twin.rebuild", "unlock.main.alias", "kvm.main.config", "kvm.twin.config",
                         "audio.twin.default", "desktop.main.shortcut", "gui.twin.uinput", "nic-fix.main.i225"]:
            self.assertIn(expected, ids)

    def test_unsupported_platforms_are_skipped_with_reasons(self):
        prof = {**PROFILE,
                "network": {**PROFILE["network"], "mode": "lan"},
                "main": {**PROFILE["main"], "pkg": "dnf", "desktop": "kde"},
                "twin": {**PROFILE["twin"], "desktop": "sway", "gpu": "nvidia", "initramfs": "dracut"}}
        reasons = {s.skip_reason for s in F.build_plan(prof, REPO) if s.skip_reason}
        for expected in ["network 'lan' is not supported yet (planned)",
                         "packages 'dnf' is not supported yet (planned)",
                         "desktop 'kde' is not supported yet (planned)",
                         "desktop 'sway' is not supported yet (planned)",
                         "gpu 'nvidia' is not supported yet (planned)",
                         "remote unlock on a LAN is not supported yet (planned)"]:
            self.assertIn(expected, reasons)

    def test_unencrypted_twin_skips_unlock(self):
        prof = {**PROFILE, "twin": {**PROFILE["twin"], "luks": False}}
        unlock = [s for s in F.build_plan(prof, REPO) if s.feature == "unlock"]
        self.assertEqual([s.skip_reason for s in unlock], ["the twin's disk is not encrypted — nothing to unlock"])

    def test_only_selected_features_in_canonical_order(self):
        self.assertEqual(features_in_order(F.build_plan(PROFILE, REPO, ["audio", "cli"])), ["cli", "audio"])

    def test_bad_input(self):
        with self.assertRaisesRegex(ValueError, "unknown feature"):
            F.build_plan(PROFILE, REPO, ["teleport"])
        with self.assertRaisesRegex(ValueError, "twinpc detect"):
            F.build_plan({**PROFILE, "twin": {}}, REPO)


class TemplateTests(unittest.TestCase):
    def test_config_templates_match_installed_text(self):
        # exactly what main/install.sh wrote on the author's machines — a difference would make
        # `install` rewrite working configs
        self.assertEqual(
            F.LAN_MOUSE_MAIN.format(SIDE="LEFT", side="left", fp="ab:cd", addr="10.42.0.11"),
            "# lan-mouse on the MAIN PC — twin's monitor sits to the LEFT of the main monitor.\n"
            "# Release keys (get the mouse back if ever stuck on twin): Ctrl+Shift+Super+Alt\n"
            "port = 4242\n\n[authorized_fingerprints]\n\"ab:cd\" = \"twin\"\n\n[[clients]]\n"
            "position = \"left\"\nhostname = \"twin\"\nips = [\"10.42.0.11\"]\nactivate_on_startup = true\n")
        self.assertEqual(
            F.LAN_MOUSE_TWIN.format(SIDE="RIGHT", side="right", fp="ef:01", addr="10.42.0.1"),
            "# lan-mouse on TWIN — the main PC's monitor sits to the RIGHT of this screen.\n"
            "port = 4242\n\n[authorized_fingerprints]\n\"ef:01\" = \"main\"\n\n[[clients]]\n"
            "position = \"right\"\nhostname = \"main\"\nips = [\"10.42.0.1\"]\nactivate_on_startup = true\n")

    def test_unit_paths_follow_repo_location(self):
        home_repo = Path.home() / "projects" / "twinPC"
        self.assertIn("ExecStart=%h/projects/twinPC/route/twin-route-load",
                      F.repo_unit(home_repo, "route/twin-route-load.service", REPO))
        self.assertIn("ExecStart=/opt/twinPC/route/twin-route-load",
                      F.repo_unit(Path("/opt/twinPC"), "route/twin-route-load.service", REPO))

    def test_fingerprint(self):
        der = b"not really a certificate"
        pem = "-----BEGIN CERTIFICATE-----\n" + base64.b64encode(der).decode() + "\n-----END CERTIFICATE-----"
        ctx = S.Ctx({}, FakeRunner([("twin", "lan-mouse.pem", 0, pem)]), REPO)
        want = ":".join(f"{b:02x}" for b in hashlib.sha256(der).digest())
        self.assertEqual(F.fingerprint(ctx, "twin"), want)
        with self.assertRaises(S.StepError):
            F.fingerprint(S.Ctx({}, FakeRunner(), REPO), "main")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd ~/projects/twinPC && python3 -m unittest tests.tool.test_features -v`
Expected: ERROR — `ImportError: cannot import name 'features' from 'twinpc_lib'`.

- [ ] **Step 3: Implement the features**

`tool/twinpc_lib/features.py`:

```python
"""Every twinPC feature expressed as ordered steps, using the platform adapters.

build_plan(profile, repo, only=None) -> list[Step]. A feature whose platform is unsupported becomes a
single skipped step carrying the reason; everything else is a step with a check, so `install` can
tell what is already done and `doctor` can check health.
"""
import base64
import hashlib
from pathlib import Path

from . import adapters
from .adapters.base import Unsupported
from .adapters.network import ssh_config
from .adapters.packages import pkg_step
from .steps import StepError, cmd_step, file_step, line_step, manual_step, unit_step, unsupported_step

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
        unit_step("connection.twin.sshd", "connection", "twin", "sshd", user=False),
        cmd_step("connection.twin.firewall", "connection", "twin", "allow SSH (22/tcp) in the twin's firewall",
                 check="! command -v ufw >/dev/null || ufw status | grep -q '^22/tcp'",
                 apply="ufw allow 22/tcp", root=True),
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
        manual_step("power.twin.bios", "power", "twin", "enable Wake-on-LAN in the twin's firmware",
                    "In the twin's BIOS enable wake from PCI-E / LAN and disable ErP / deep sleep "
                    "(ASUS: Advanced → APM Configuration).",
                    check=f"ethtool {ifc} | grep -q 'Wake-on: g'", check_root=True),
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
        cmd_step("kvm.twin.firewall", "kvm", "twin", "allow lan-mouse (4242/udp) in the twin's firewall",
                 check="! command -v ufw >/dev/null || ufw status | grep -q '^4242/udp'",
                 apply="ufw allow 4242/udp", root=True),
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
    md = adapters.get("desktop", profile.get("main", {}).get("desktop", ""))
    steps += ([unsupported_step("desktop", "main", md.reason(), "shortcut")] if isinstance(md, Unsupported) else
              md.shortcut_steps("desktop", "twin-desktop", "Twin PC desktop (fullscreen toggle)", "<Super>F12",
                                f"{v['home']}/.local/bin/twin desktop"))
    td = adapters.get("desktop", profile.get("twin", {}).get("desktop", ""))
    steps += ([unsupported_step("desktop", "twin", td.reason())] if isinstance(td, Unsupported) else
              td.remote_desktop_steps("desktop", _pk(profile, "twin")))
    return steps


def _gui(profile, repo, v):
    td = adapters.get("desktop", profile.get("twin", {}).get("desktop", ""))
    if isinstance(td, Unsupported):
        return [unsupported_step("gui", "twin", td.reason())]
    return td.gui_steps("gui", _pk(profile, "twin"), v)


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
            "mount": _mount, "power": _power, "unlock": _unlock, "kvm": _kvm, "audio": _audio,
            "desktop": _desktop, "gui": _gui, "nic-fix": _nic_fix}
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
        if not only or name in only:
            steps += BUILDERS[name](profile, Path(repo), v)
    return steps
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd ~/projects/twinPC && python3 -m unittest tests.tool.test_features -v`
Expected: 10 tests PASS.

Then check the generated content against what is installed on the real machines (read-only):
`python3 -c "import sys; sys.path.insert(0,'tool'); from pathlib import Path; from twinpc_lib import features as F; print(F.repo_unit(Path.cwd(), 'route/twin-route-load.service') == open(Path.home()/'.config/systemd/user/twin-route-load.service').read())"`
Expected: `True`.

- [ ] **Step 5: Commit**

```bash
git add tool/twinpc_lib/features.py tests/tool/test_features.py
git commit -m "feat(tool): all twinPC features as idempotent steps

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: `doctor` and the `twinpc` command

**Files:**
- Create: `tool/twinpc_lib/doctor.py`, `tool/twinpc_lib/cli.py`, `tool/twinpc` (executable)
- Test: `tests/tool/test_cli.py`

**Interfaces:**
- Consumes: `profile.*` (Task 2), `steps.Ctx/Runner/run_steps/StepError` (Task 3), `adapters.get` (Task 4), `features.build_plan/FEATURES` (Task 5), `tool/probe.sh` (Task 1).
- Produces:
  - `doctor.doctor(steps, ctx, out=print) -> int` — one line per feature: `✅ <feature> working`, `❔ <feature> working as far as checked (N check(s) need sudo)`, `⚠️ <feature> skipped: <reason>` / `⚠️ <feature> partly skipped: <reason>`, `❌ <feature> broken: <step id> — <describe>`; returns 1 if any ❌.
  - `cli.main(argv=None, runner=None, probe=None, out=print, confirm=None) -> int`; `probe(machine, host, script) -> str | None` (default runs `sh -s` locally / `ssh <host> sh -s`).
  - Commands: `twinpc detect [--twin HOST] [--force]`, `twinpc install [FEATURE…] [--dry-run] [--yes] [--no-root]`, `twinpc doctor [FEATURE…] [--no-root]`.

- [ ] **Step 1: Write the failing tests**

`tests/tool/test_cli.py`:

```python
import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from tests.tool.fakes import FakeRunner
from twinpc_lib import cli, profile as P
from tests.tool.test_profile import MAIN, TWIN


def probe_text(d):
    return "".join(f"{k}={v}\n" for k, v in d.items())


class CliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old = os.environ.get("XDG_CONFIG_HOME")
        os.environ["XDG_CONFIG_HOME"] = self.tmp.name
        self.lines = []

    def tearDown(self):
        if self.old is None:
            os.environ.pop("XDG_CONFIG_HOME", None)
        else:
            os.environ["XDG_CONFIG_HOME"] = self.old
        self.tmp.cleanup()

    def run_cli(self, *argv, runner=None, probe=None):
        return cli.main(list(argv), runner=runner, probe=probe, out=self.lines.append, confirm=lambda q: True)

    def detect(self):
        probes = {"main": probe_text(MAIN), "twin": probe_text(TWIN)}
        return self.run_cli("detect", probe=lambda machine, host, script: probes[machine])

    def test_detect_writes_profile_and_env(self):
        self.assertEqual(self.detect(), 0)
        prof = P.load()
        self.assertEqual((prof["twin"]["desktop"], prof["network"]["mode"]), ("hyprland", "cable"))
        self.assertTrue((P.config_dir() / "profile.env").exists())
        self.assertTrue(any("twin:" in line and "hyprland" in line for line in self.lines))

    def test_detect_with_unreachable_twin(self):
        rc = self.run_cli("detect", probe=lambda m, h, s: probe_text(MAIN) if m == "main" else None)
        self.assertEqual(rc, 1)
        self.assertEqual(P.load()["main"]["pkg"], "apt")
        self.assertTrue(any("not reachable" in line for line in self.lines))

    def test_detect_reports_unsupported_values(self):
        probes = {"main": probe_text({**MAIN, "desktop": "kde"}), "twin": probe_text({**TWIN, "gpu": "nvidia"})}
        self.run_cli("detect", probe=lambda m, h, s: probes[m])
        text = "\n".join(self.lines)
        self.assertIn("desktop 'kde' is not supported yet (planned)", text)
        self.assertIn("gpu 'nvidia' is not supported yet (planned)", text)

    def test_install_without_profile(self):
        self.assertEqual(self.run_cli("install"), 2)
        self.assertTrue(any("twinpc detect" in line for line in self.lines))

    def test_install_dry_run_when_everything_is_done(self):
        self.detect()
        self.lines.clear()
        everything_ok = FakeRunner([("main", "", 0, ""), ("twin", "", 0, "")])   # every check passes
        rc = self.run_cli("install", "cli", "--dry-run", runner=everything_ok)
        self.assertEqual(rc, 0)
        self.assertTrue(all(line.startswith("✓") for line in self.lines if line[:1] in "✓→✗"))
        self.assertFalse(any(c[1].startswith("mkdir -p ~/.local/bin") for c in everything_ok.calls))

    def test_unknown_feature(self):
        self.detect()
        self.assertEqual(self.run_cli("install", "teleport", runner=FakeRunner()), 2)

    def test_doctor_reports_broken_feature(self):
        self.detect()
        self.lines.clear()
        rc = self.run_cli("doctor", "cli", runner=FakeRunner())      # every check fails
        self.assertEqual(rc, 1)
        self.assertTrue(any(line.startswith("❌ cli") for line in self.lines))

    def test_doctor_marks_unchecked_root_steps(self):
        from twinpc_lib import doctor, steps as S
        steps = [S.cmd_step("f.twin.a", "f", "twin", "plain", check="ok-a", apply="x"),
                 S.cmd_step("f.twin.b", "f", "twin", "needs root", check="root-b", apply="y", root=True)]
        ctx = S.Ctx({}, FakeRunner([("twin", "ok-a", 0, "")]), "/r", allow_root=False)
        self.assertEqual(doctor.doctor(steps, ctx, out=self.lines.append), 0)
        self.assertEqual(self.lines, ["❔ f           working as far as checked (1 check(s) need sudo)"])

    def test_entry_point_help(self):
        import subprocess
        tool = Path(__file__).resolve().parents[2] / "tool" / "twinpc"
        out = subprocess.run([str(tool), "--help"], capture_output=True, text=True)
        self.assertEqual(out.returncode, 0)
        self.assertIn("detect", out.stdout)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd ~/projects/twinPC && python3 -m unittest tests.tool.test_cli -v`
Expected: ERROR — `ImportError: cannot import name 'cli' from 'twinpc_lib'`.

- [ ] **Step 3: Implement doctor, the CLI and the entry point**

`tool/twinpc_lib/doctor.py`:

```python
"""twinpc doctor: run every feature's checks (read-only) and print one health line per feature."""
from .steps import StepError


def doctor(steps, ctx, out=print):
    by_feature = {}
    for s in steps:
        by_feature.setdefault(s.feature, []).append(s)
    broken = 0
    for feature, fsteps in by_feature.items():
        skips = [s.skip_reason for s in fsteps if s.skip_reason]
        failing, unknown = None, 0
        for s in fsteps:
            if s.skip_reason or s.check is None:
                continue
            if s.check_root and not ctx.allow_root:
                unknown += 1
                continue
            try:
                ok = s.check(ctx)
            except StepError:
                unknown += 1
                continue
            if not ok:
                failing = s
                break
        name = f"{feature:<11}"
        if failing:
            broken += 1
            out(f"❌ {name} broken: {failing.id} — {failing.describe}")
        elif skips and len(skips) == len(fsteps):
            out(f"⚠️ {name} skipped: {skips[0]}")
        elif skips:
            out(f"⚠️ {name} partly skipped: {skips[0]}")
        elif unknown:
            out(f"❔ {name} working as far as checked ({unknown} check(s) need sudo)")
        else:
            out(f"✅ {name} working")
    return 1 if broken else 0
```

`tool/twinpc_lib/cli.py`:

```python
"""twinpc — set up and check twinPC on a pair of Linux machines."""
import argparse
import subprocess
from pathlib import Path

from . import adapters, profile as P
from .adapters.base import Unsupported
from .doctor import doctor
from .features import FEATURES, build_plan
from .steps import Ctx, Runner, run_steps

REPO = Path(__file__).resolve().parents[2]
PROBE = REPO / "tool" / "probe.sh"


def default_probe(machine, host, script):
    argv = ["sh", "-s"] if machine == "main" else ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", host, "sh -s"]
    p = subprocess.run(argv, input=script, capture_output=True, text=True)
    return p.stdout if p.returncode == 0 else None


def _describe(label, m):
    return (f"{label}: {m.get('family')} · {m.get('pkg')} · {m.get('desktop')}/{m.get('session')}"
            f" · gpu {m.get('gpu')}{' ' + m['gpu_arch'] if m.get('gpu_arch') else ''}"
            + (f" · luks {'yes' if m.get('luks') else 'no'} · {m.get('initramfs')} · {m.get('bootloader')}"
               if label == "twin" else ""))


def _unsupported(prof):
    checks = [("packages", prof["main"].get("pkg")), ("desktop", prof["main"].get("desktop")),
              ("network", prof["network"].get("mode"))]
    if prof.get("twin"):
        t = prof["twin"]
        checks += [("packages", t.get("pkg")), ("desktop", t.get("desktop")), ("gpu", t.get("gpu"))]
        if t.get("luks"):
            checks.append(("bootunlock", t.get("initramfs")))
    seen = []
    for kind, value in checks:
        a = adapters.get(kind, value)
        if isinstance(a, Unsupported) and a.reason() not in seen:
            seen.append(a.reason())
    return seen


def cmd_detect(a, probe, out):
    existing = P.load() if P.profile_path().exists() else None
    host = a.twin or (existing or {}).get("network", {}).get("twin_host") or "twin"
    script = PROBE.read_text()
    main_raw = probe("main", host, script)
    twin_raw = probe("twin", host, script)
    prof = P.build(P.parse_probe(main_raw or ""), P.parse_probe(twin_raw) if twin_raw else None,
                   existing, a.force, host)
    path = P.save(prof)
    out(f"profile written: {path}")
    out(_describe("main", prof["main"]))
    if prof.get("twin"):
        out(_describe("twin", prof["twin"]))
        out(f"network: {prof['network'].get('mode')} · twin at {prof['network'].get('twin_addr')}")
    for reason in _unsupported(prof):
        out(f"  ⚠️ {reason} — the features that need it will be skipped")
    if twin_raw is None:
        out(f"twin '{host}' is not reachable over ssh — set up key login first (README step 1), then run: twinpc detect")
        return 1
    return 0


def main(argv=None, runner=None, probe=None, out=print, confirm=None):
    ap = argparse.ArgumentParser(prog="twinpc", description="Set up and check twinPC on a pair of Linux machines.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("detect", help="probe both machines and write ~/.config/twinpc/profile.toml")
    d.add_argument("--twin", help="ssh host of the twin (default: twin)")
    d.add_argument("--force", action="store_true", help="refresh detected values instead of only filling gaps")
    i = sub.add_parser("install", help="install every feature (or the ones named), skipping what is done")
    i.add_argument("features", nargs="*", metavar="FEATURE", help=", ".join(FEATURES))
    i.add_argument("--dry-run", action="store_true", help="show the plan, change nothing")
    i.add_argument("--yes", action="store_true", help="don't ask to confirm manual steps")
    i.add_argument("--no-root", action="store_true", help="skip everything that needs sudo")
    c = sub.add_parser("doctor", help="check every feature and report what works")
    c.add_argument("features", nargs="*", metavar="FEATURE")
    c.add_argument("--no-root", action="store_true", help="skip checks that need sudo")
    a = ap.parse_args(argv)

    if a.cmd == "detect":
        return cmd_detect(a, probe or default_probe, out)
    try:
        prof = P.load()
        steps = build_plan(prof, REPO, a.features or None)
    except (P.ProfileError, ValueError) as e:
        out(str(e))
        return 2
    host = prof.get("network", {}).get("twin_host") or "twin"
    ctx = Ctx(prof, runner or Runner(host), REPO, yes=getattr(a, "yes", False), allow_root=not a.no_root)
    if confirm:
        ctx.confirm = confirm
    if a.cmd == "install":
        return run_steps(steps, ctx, dry_run=a.dry_run, out=out)
    return doctor(steps, ctx, out=out)
```

`tool/twinpc` (then `chmod +x tool/twinpc`):

```python
#!/usr/bin/env python3
"""twinpc — set up and check twinPC. See README.md or: twinpc --help"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
from twinpc_lib.cli import main  # noqa: E402

sys.exit(main())
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd ~/projects/twinPC && python3 -m unittest tests.tool.test_cli -v`
Expected: 9 tests PASS.

Then the whole tool suite: `python3 -m unittest discover -s tests/tool -t . -v 2>&1 | tail -3`
Expected: `OK` (≈ 68 tests, 3 distro tests skipped).

- [ ] **Step 5: Commit**

```bash
git add tool/twinpc tool/twinpc_lib/doctor.py tool/twinpc_lib/cli.py tests/tool/test_cli.py
git commit -m "feat(tool): twinpc detect / install / doctor

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Integrate with the existing tools, docs, and acceptance on the real machines

**Files:**
- Modify: `twin` (settings loading; lines 7, 75, 202, 338), `route/twin-route.bash` (line 16 and the top)
- Modify: `main/install.sh`, `route/install.sh` (become wrappers), `twinpc/install.sh` (points to `tool/twinpc`)
- Delete: `twinpc/twin-setup.sh`, `twinpc/twin-wol-setup.sh`, `twinpc/twin-wol-fix.sh`, `twinpc/twin-gui-setup.sh`, `twinpc/twin-kvm-setup.sh`, `twinpc/twin-remote-unlock-setup.sh` (their content now lives in `features.py` / adapters)
- Modify: `README.md` (Quick start / installation), `man/twin.1` (FILES)
- Test: `tests/test_cli_route.sh` gains a profile check; acceptance commands below

**Interfaces:**
- Consumes: `profile.env` (Task 2), `tool/twinpc` (Task 6).
- Produces: the `twin` command and routing hook reading `TWIN_ADDR` / `TWIN_HOST` / `TWIN_USER` / `TWIN_MAC` / `TWIN_IFACE` from `profile.env`, with `~/.config/twinpc/config` still overriding.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_cli_route.sh`, before the final `(( fail == 0 )) && echo PASS` line:

```bash
# the twin command takes the twin's address from the twinpc profile
mkdir -p "$XDG_CONFIG_HOME/twinpc"
printf 'TWIN_ADDR=${TWIN_ADDR:-10.99.0.7}\n' > "$XDG_CONFIG_HOME/twinpc/profile.env"
bash -c "source <(sed -n '1,/^cmd=/p' '$T' | sed '\$d'); echo \"\$TWIN_ADDR\"" | grep -qx 10.99.0.7 || { echo "FAIL profile address"; fail=1; }
```

Run: `bash tests/test_cli_route.sh`
Expected: `FAIL profile address`.

- [ ] **Step 2: Make `twin` read the profile**

In `twin`, replace the line

```bash
[[ -f ${XDG_CONFIG_HOME:-$HOME/.config}/twinpc/config ]] && . "${XDG_CONFIG_HOME:-$HOME/.config}/twinpc/config"
```

with

```bash
# twinpc's detected profile first, then the user's own overrides
for _f in "${XDG_CONFIG_HOME:-$HOME/.config}/twinpc/profile.env" "${XDG_CONFIG_HOME:-$HOME/.config}/twinpc/config"; do
  [[ -f $_f ]] && . "$_f"
done
TWIN_ADDR=${TWIN_ADDR:-10.42.0.11}
```

Then replace the three hard-coded twin addresses:
- in `stage()`: `b=$(timeout 3 bash -c 'exec 3<>/dev/tcp/10.42.0.11/22 && head -c 64 <&3' 2>/dev/null | tr -d '\r' | head -1)` → `b=$(timeout 3 bash -c 'exec 3<>/dev/tcp/$0/22 && head -c 64 <&3' "$TWIN_ADDR" 2>/dev/null | tr -d '\r' | head -1)`
- in `models)`: `curl -s http://10.42.0.11:11434/api/tags` → `curl -s "http://$TWIN_ADDR:11434/api/tags"`
- in `status)`: `ping -c1 -W1 10.42.0.11` → `ping -c1 -W1 "$TWIN_ADDR"`

- [ ] **Step 3: Make the routing hook read the profile address**

In `route/twin-route.bash`, after the line `_TR_DIR=$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")` add:

```bash
# the twin's address from the twinpc profile (read in a subshell: no variables leak into this shell)
_TR_ADDR=${TWIN_ADDR:-$(f=${XDG_CONFIG_HOME:-$HOME/.config}/twinpc/profile.env; [[ -f $f ]] && . "$f"; echo "${TWIN_ADDR:-10.42.0.11}")}
```

and change the stage probe line to

```bash
  b=$(timeout 1.5 bash -c 'exec 3<>/dev/tcp/$0/22 && head -c 64 <&3' "$_TR_ADDR" 2>/dev/null | tr -d '\r' | head -1)
```

- [ ] **Step 4: Wrappers and removed scripts**

`main/install.sh` (whole file):

```bash
#!/usr/bin/env bash
# main/install.sh — kept for compatibility: twinPC is installed by the twinpc tool.
set -euo pipefail
T="$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)/tool/twinpc"
[[ -f ${XDG_CONFIG_HOME:-$HOME/.config}/twinpc/profile.toml ]] || "$T" detect
exec "$T" install "$@"
```

`route/install.sh` (whole file):

```bash
#!/usr/bin/env bash
# route/install.sh — kept for compatibility: installs only the task-routing feature via twinpc.
exec "$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)/tool/twinpc" install routing "$@"
```

`twinpc/install.sh` (whole file):

```bash
#!/usr/bin/env bash
# twinpc/install.sh — the twin is now set up from the main PC, over ssh:
echo "Run on the MAIN PC, in the twinPC folder:  tool/twinpc detect && tool/twinpc install"
exit 1
```

Remove the per-step scripts (their content is in `tool/twinpc_lib/features.py` and the adapters):

```bash
git rm twinpc/twin-setup.sh twinpc/twin-wol-setup.sh twinpc/twin-wol-fix.sh \
       twinpc/twin-gui-setup.sh twinpc/twin-kvm-setup.sh twinpc/twin-remote-unlock-setup.sh
```

- [ ] **Step 5: Run the tests**

Run: `bash tests/test_cli_route.sh && python3 -m unittest tests.test_twin_route tests.test_hook -v 2>&1 | tail -3`
Expected: `PASS`, then `OK`.

- [ ] **Step 6: Update the docs**

In `README.md`, replace steps 3 and 4 of the **Quick start** code block with:

```bash
# 3 · on the main PC: detect both machines, see the plan, install, check
tool/twinpc detect
tool/twinpc install --dry-run
tool/twinpc install          # asks for sudo on each machine once; BIOS step asks you to confirm
tool/twinpc doctor
```

and in the collapsed step-by-step section replace "### 3. Set up the twin" and "### 4. Set up the main PC" with one section:

````markdown
### 3. Install from the main PC
```bash
cd ~/projects/twinPC
tool/twinpc detect            # probes both machines → ~/.config/twinpc/profile.toml (edit it if a value is wrong)
tool/twinpc install --dry-run # what would change
tool/twinpc install           # everything, or name features: tool/twinpc install gpu-stack kvm
tool/twinpc doctor            # ✅ / ⚠️ / ❌ per feature
```
`install` skips steps that are already done, stops at the first failure with the reason, and asks for
`sudo` at most once per machine. Features your platform doesn't support yet are skipped with the reason.
````

In `man/twin.1`, in the FILES section replace
`.RB ( "main/install.sh" " on this PC, " "twinpc/install.sh" " on the twin)."`
with
`.RB ( "tool/twinpc detect" ", " "tool/twinpc install" ", " "tool/twinpc doctor" ").`

- [ ] **Step 7: Acceptance on the real machines**

```bash
cd ~/projects/twinPC
tool/twinpc detect                     # expect twin: arch · pacman · hyprland/wayland · gpu amd gfx1032 · luks yes · mkinitcpio · limine; network: cable
tool/twinpc doctor --no-root           # expect every feature ✅ or ❔ (root checks skipped), none ❌
tool/twinpc install --dry-run --no-root   # expect every line "✓ … already done" or "? … needs sudo to check"
```

Any `→ … would …` or `❌` line is a difference between the generated steps and the working setup: fix the generator (not the machine) until it disappears, and record each fix as a ledger ruling.

Then ask the user to run, in a normal terminal (it asks for sudo on each machine once):

```bash
tool/twinpc doctor && tool/twinpc install --dry-run
```

Expected: every feature ✅ and every step "already done".

Finally the whole suite: `python3 -m unittest discover -s tests -t . 2>&1 | tail -3` and `for t in tests/*.sh; do bash "$t"; done`
Expected: `OK` and every script `PASS`.

- [ ] **Step 8: Commit**

```bash
git add twin route/twin-route.bash main/install.sh route/install.sh twinpc/install.sh README.md man/twin.1 tests/test_cli_route.sh
git commit -m "feat: twin and the routing hook read the twinpc profile; installers call twinpc

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

