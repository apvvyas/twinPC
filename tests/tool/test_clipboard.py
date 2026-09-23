import unittest
from pathlib import Path

from tests.tool.fakes import FakeRunner
from tests.tool.test_features import PROFILE
from twinpc_lib import features as F, steps as S

REPO = Path(__file__).resolve().parents[2]


def clip_steps(prof):
    return [s for s in F.build_plan(prof, REPO) if s.feature == "clipboard"]


def applied(step, machine):
    r = FakeRunner([(machine, "", 0, "")])
    step.apply(S.Ctx({}, r, REPO))
    return r.calls[-1]


class ClipboardPlanTests(unittest.TestCase):
    def test_gnome_main_and_hyprland_twin(self):
        self.assertEqual([s.id for s in clip_steps(PROFILE)], [
            "clipboard.main.packages", "clipboard.twin.packages", "clipboard.main.command", "clipboard.twin.agent",
            "clipboard.main.unit", "clipboard.main.service", "clipboard.main.extension-code",
            "clipboard.main.extension-metadata", "clipboard.main.extension-enabled", "clipboard.main.relogin",
            "clipboard.main.link"])

    def test_comes_after_kvm(self):
        self.assertEqual(F.FEATURES.index("clipboard"), F.FEATURES.index("kvm") + 1)

    def test_unit_uses_the_profile_host(self):
        prof = {**PROFILE, "network": {**PROFILE["network"], "twin_host": "gpu"}}
        [unit] = [s for s in clip_steps(prof) if s.id == "clipboard.main.unit"]
        self.assertIn("ExecStart=%h/.local/bin/twin-clipd --host gpu\n", applied(unit, "main")[3])

    def test_twin_gets_a_copy_of_the_agent(self):
        [agent] = [s for s in clip_steps(PROFILE) if s.id == "clipboard.twin.agent"]
        machine, cmd, _root, data = applied(agent, "twin")
        self.assertEqual((machine, data), ("twin", (REPO / "clip" / "twin-clipd").read_text()))
        self.assertIn("chmod 755", cmd)

    def test_extension_files_and_enabling(self):
        steps = {s.id: s for s in clip_steps(PROFILE)}
        code = applied(steps["clipboard.main.extension-code"], "main")
        self.assertIn("gnome-shell/extensions/twinpc-clipboard@twinpc/extension.js", code[1])
        self.assertIn("owner-changed", code[3])
        enable = applied(steps["clipboard.main.extension-enabled"], "main")[1]
        self.assertIn("/usr/bin/gsettings", enable)
        self.assertIn("enabled-extensions", enable)

    def test_relogin_is_remembered_for_this_login(self):
        [st] = [s for s in clip_steps(PROFILE) if s.id == "clipboard.main.relogin"]
        self.assertIsNone(st.apply)
        self.assertIn("relogin-noted", st.after_confirm)
        r = FakeRunner()
        st.check(S.Ctx({}, r, REPO))
        self.assertIn("State: ACTIVE", r.calls[-1][1])
        self.assertIn("$XDG_RUNTIME_DIR/twinpc/relogin-noted", r.calls[-1][1])

    def test_unsupported_desktops(self):
        for machine, desktop, reason in [
                ("main", "kde", "desktop 'kde' is not supported yet (planned)"),
                ("main", "hyprland", "desktop 'hyprland' on the main PC is not supported yet (planned)"),
                ("twin", "gnome", "desktop 'gnome' on the twin is not supported yet (planned)")]:
            prof = {**PROFILE, machine: {**PROFILE[machine], "desktop": desktop}}
            with self.subTest(machine=machine, desktop=desktop):
                self.assertEqual([s.skip_reason for s in clip_steps(prof)], [reason])


if __name__ == "__main__":
    unittest.main()
