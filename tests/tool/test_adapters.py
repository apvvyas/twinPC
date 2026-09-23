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
        self.assertEqual(ids, ["unlock.twin.packages", "unlock.twin.key", "unlock.twin.encrypt-hook",
                               "unlock.twin.hooks",
                               "unlock.twin.cmdline", "unlock.twin.rebuild"])

    def test_other_bootloader_is_unsupported(self):
        prof = {**PROFILE, "twin": {**PROFILE["twin"], "bootloader": "grub"}}
        [st] = bootunlock.Mkinitcpio().unlock_steps(prof, REPO, V, packages.Pacman())
        self.assertEqual(st.skip_reason, "remote unlock with bootloader 'grub' is not supported yet (planned)")


if __name__ == "__main__":
    unittest.main()
