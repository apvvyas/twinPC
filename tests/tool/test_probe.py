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
