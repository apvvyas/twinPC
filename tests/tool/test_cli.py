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
