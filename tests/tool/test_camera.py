import unittest
from pathlib import Path

from tests.tool.fakes import FakeRunner
from tests.tool.test_features import PROFILE
from twinpc_lib import features as F, steps as S

REPO = Path(__file__).resolve().parents[2]


def camera_steps(prof):
    return [s for s in F.build_plan(prof, REPO) if s.feature == "camera"]


def applied(step, machine):
    r = FakeRunner([(machine, "", 0, "")])
    step.apply(S.Ctx({}, r, REPO))
    return r.calls[-1]


class CameraPlanTests(unittest.TestCase):
    def test_steps_in_order(self):
        self.assertEqual([s.id for s in camera_steps(PROFILE)], [
            "camera.main.packages", "camera.twin.packages", "camera.main.audio", "camera.twin.mic",
            "camera.twin.default-mic", "camera.twin.module", "camera.twin.options", "camera.twin.autoload",
            "camera.twin.loaded", "camera.main.command", "camera.twin.command", "camera.main.unit",
            "camera.main.service", "camera.main.link"])

    def test_comes_after_audio(self):
        self.assertEqual(F.FEATURES.index("camera"), F.FEATURES.index("audio") + 1)

    def test_mic_is_a_source_tunnel_on_the_audio_forward(self):
        [mic] = [s for s in camera_steps(PROFILE) if s.id == "camera.twin.mic"]
        machine, cmd, _root, conf = applied(mic, "twin")
        self.assertIn("pipewire.conf.d/main-pc-mic.conf", cmd)
        for needed in ("tunnel.mode           = source", '"tcp:127.0.0.1:4713"', '"main-pc-mic"',
                       '"Main PC microphone"', "reconnect.interval.ms"):
            self.assertIn(needed, conf)

    def test_module_uses_the_running_kernels_headers(self):
        steps = {s.id: s for s in camera_steps(PROFILE)}
        machine, cmd, root, _ = applied(steps["camera.twin.module"], "twin")
        self.assertTrue(root)
        self.assertIn("pacman -Qqo /usr/lib/modules/$(uname -r)/vmlinuz", cmd)
        self.assertIn('v4l2loopback-dkms "$k-headers"', cmd)
        options = applied(steps["camera.twin.options"], "twin")
        self.assertIn('video_nr=9 card_label="Main PC camera" exclusive_caps=1', options[3])
        self.assertTrue(options[2])

    def test_link_checks_fresh_code(self):
        steps = {s.id: s for s in camera_steps(PROFILE)}
        r = FakeRunner()
        steps["camera.main.link"].check(S.Ctx({}, r, REPO))
        self.assertIn("twin-camera --selftest --fresh", r.calls[-1][1])
        self.assertIn("try-restart twin-camera.service", applied(steps["camera.main.unit"], "main")[1])

    def test_default_mic_waits_for_the_tunnel_to_retry(self):
        # after the PipeWire restart the tunnel's first connect can fail; it retries 5 s later
        [st] = [s for s in camera_steps(PROFILE) if s.id == "camera.twin.default-mic"]
        self.assertIn("$(seq 60)", applied(st, "twin")[1])

    def test_needs_the_audio_forward(self):
        [st] = [s for s in camera_steps(PROFILE) if s.id == "camera.main.audio"]
        self.assertIsNone(st.apply)
        r = FakeRunner()
        st.check(S.Ctx({}, r, REPO))
        self.assertIn("twin-audio.service", r.calls[-1][1])

    def test_other_package_managers_are_skipped(self):
        prof = {**PROFILE, "twin": {**PROFILE["twin"], "pkg": "apt"}}
        [st] = camera_steps(prof)
        self.assertEqual(st.skip_reason, "the twin's virtual camera needs pacman — packages 'apt' is not supported yet (planned)")


if __name__ == "__main__":
    unittest.main()
