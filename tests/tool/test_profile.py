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
