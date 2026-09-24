import unittest
from pathlib import Path

from tests.tool.fakes import FakeRunner
from tests.tool.test_features import PROFILE
from twinpc_lib import features as F, steps as S

REPO = Path(__file__).resolve().parents[2]


class TwinMouseCaptureTests(unittest.TestCase):
    def test_twin_lan_mouse_avoids_the_input_capture_portal(self):
        # Hyprland 0.56 asserts in libeis when lan-mouse opens an input-capture-portal session (it crashed
        # the twin's desktop twice); the layer-shell capture backend doesn't go through the portal at all
        [unit] = [s for s in F.build_plan(PROFILE, REPO) if s.id == "kvm.twin.unit"]
        r = FakeRunner([("twin", "", 0, "")])
        unit.apply(S.Ctx({}, r, REPO))
        self.assertIn("ExecStart=/usr/bin/lan-mouse --capture-backend layer-shell daemon\n", r.calls[-1][3])


if __name__ == "__main__":
    unittest.main()
