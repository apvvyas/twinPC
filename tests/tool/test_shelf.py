import unittest
from pathlib import Path

from tests.tool.fakes import FakeRunner
from tests.tool.test_features import PROFILE
from twinpc_lib import features as F, steps as S

REPO = Path(__file__).resolve().parents[2]


def shelf_steps(prof):
    return [s for s in F.build_plan(prof, REPO) if s.feature == "shelf"]


def applied(step, machine):
    r = FakeRunner([(machine, "", 0, "")])
    step.apply(S.Ctx({}, r, REPO))
    return r.calls[-1]


class ShelfPlanTests(unittest.TestCase):
    def test_gnome_main_and_hyprland_twin(self):
        self.assertEqual([s.id for s in shelf_steps(PROFILE)], [
            "shelf.main.packages", "shelf.twin.packages", "shelf.twin.layer-shell", "shelf.main.clipboard",
            "shelf.main.command", "shelf.twin.command", "shelf.main.unit", "shelf.main.service",
            "shelf.twin.unit", "shelf.twin.service", "shelf.main.link", "shelf.twin.link"])

    def test_comes_after_clipboard(self):
        self.assertEqual(F.FEATURES.index("shelf"), F.FEATURES.index("clipboard") + 1)

    def test_facing_edges(self):
        self.assertEqual((F.facing_edge("left", "main"), F.facing_edge("left", "twin")), ("left", "right"))
        self.assertEqual((F.facing_edge("top", "main"), F.facing_edge("top", "twin")), ("top", "bottom"))
        self.assertEqual(F.facing_edge("nonsense", "main"), "left")

    def test_units_use_the_facing_edges(self):
        prof = {**PROFILE, "user": {**PROFILE["user"], "twin_side": "right"}}
        steps = {s.id: s for s in shelf_steps(prof)}
        self.assertIn("ExecStart=%h/.local/bin/twin-shelf --edge right\n", applied(steps["shelf.main.unit"], "main")[3])
        twin_unit = applied(steps["shelf.twin.unit"], "twin")[3]
        self.assertIn("ExecStart=%h/.local/bin/twin-shelf --edge left --socket shelf.sock\n", twin_unit)
        self.assertIn("LD_PRELOAD=/usr/lib/libgtk4-layer-shell.so", twin_unit)

    def test_twin_gets_a_copy_of_the_app(self):
        [cmd] = [s for s in shelf_steps(PROFILE) if s.id == "shelf.twin.command"]
        machine, shell, _root, data = applied(cmd, "twin")
        self.assertEqual((machine, data), ("twin", (REPO / "clip" / "twin-shelf").read_text()))
        self.assertIn("chmod 755", shell)

    def test_needs_the_clipboard_feature(self):
        [st] = [s for s in shelf_steps(PROFILE) if s.id == "shelf.main.clipboard"]
        self.assertIsNone(st.apply)
        r = FakeRunner()
        st.check(S.Ctx({}, r, REPO))
        self.assertIn("twin-clip.service", r.calls[-1][1])

    def test_link_checks_use_each_side_socket(self):
        steps = {s.id: s for s in shelf_steps(PROFILE)}
        r = FakeRunner()
        steps["shelf.twin.link"].check(S.Ctx({}, r, REPO))
        self.assertIn("--selftest --socket shelf.sock", r.calls[-1][1])

    def test_unsupported_desktops(self):
        for machine, desktop, reason in [
                ("main", "kde", "desktop 'kde' is not supported yet (planned)"),
                ("main", "hyprland", "desktop 'hyprland' on the main PC is not supported yet (planned)"),
                ("twin", "gnome", "desktop 'gnome' on the twin is not supported yet (planned)")]:
            prof = {**PROFILE, machine: {**PROFILE[machine], "desktop": desktop}}
            with self.subTest(machine=machine, desktop=desktop):
                self.assertEqual([s.skip_reason for s in shelf_steps(prof)], [reason])


if __name__ == "__main__":
    unittest.main()
