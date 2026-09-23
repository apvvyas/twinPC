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
                         "unlock.twin.rebuild", "unlock.main.alias", "kvm.main.config", "kvm.twin.config", "clipboard.main.link", "shelf.main.link",
                         "audio.twin.default", "desktop.main.shortcut", "gui.twin.uinput", "nic-fix.main.i225"]:
            self.assertIn(expected, ids)

    def test_unsupported_platforms_are_skipped_with_reasons(self):
        prof = {**PROFILE,
                "network": {**PROFILE["network"], "mode": "lan"},
                "main": {**PROFILE["main"], "pkg": "dnf", "desktop": "kde"},
                "twin": {**PROFILE["twin"], "desktop": "sway", "gpu": "nvidia", "initramfs": "dracut"}}
        reasons = {s.skip_reason for s in F.build_plan(prof, REPO) if s.skip_reason}
        # a feature whose packages can't be installed collapses to that one skip, hiding its desktop reason
        kde_prof = {**prof, "main": {**prof["main"], "pkg": "apt"}}
        reasons |= {s.skip_reason for s in F.build_plan(kde_prof, REPO) if s.skip_reason}
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
