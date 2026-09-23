import importlib.machinery
import importlib.util
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ROUTE = ROOT / "route" / "twin-route"
_loader = importlib.machinery.SourceFileLoader("twin_route", str(ROUTE))
_spec = importlib.util.spec_from_loader("twin_route", _loader)
tr = importlib.util.module_from_spec(_spec)
_loader.exec_module(tr)

RULES = tr.load_rules(ROOT / "route" / "rules.default.toml")
HOME = os.path.expanduser("~")
WS = os.path.join(HOME, "twin")
IDLE, CPU_BUSY, RAM_BUSY = (10.0, 30.0), (95.0, 30.0), (10.0, 90.0)


class RouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.plain = cls.tmp.name                    # empty folder outside the workspace

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def d(self, line, cwd=None, load=IDLE):
        return tr.decide(line, cwd or self.plain, load, RULES)

    # --- unparsable input stays local
    def test_unparsable(self):
        for line in ["", "   ", "echo 'unclosed", "make \\", "cat <<EOF", "echo $(date)",
                     "line1\nline2", "(cd x; make)"]:
            with self.subTest(line=line):
                self.assertEqual(self.d(line), ("local", "unparsable"))

    # --- forced / passthrough
    def test_forced_local(self):
        self.assertEqual(self.d("local ollama run x"), ("local", "forced"))

    def test_passthrough(self):
        self.assertEqual(self.d("twin gpu"), ("local", "passthrough"))
        self.assertEqual(self.d("twin-exec -- ls"), ("local", "passthrough"))

    # --- never_twin
    def test_never(self):
        self.assertEqual(self.d("git status"), ("local", "never:git"))
        self.assertEqual(self.d("ls -la | grep x")[0], "local")
        self.assertEqual(self.d("sudo apt update"), ("local", "never:sudo"))

    def test_never_needs_every_stage(self):
        self.assertEqual(self.d("git log | ollama run x summarize"), ("twin", "always:ollama"))

    def test_never_beats_workspace(self):
        self.assertEqual(self.d("git status", cwd=WS + "/proj"), ("local", "never:git"))

    # --- workspace
    def test_workspace(self):
        self.assertEqual(self.d("make", cwd=WS + "/proj"), ("twin", "workspace"))
        self.assertEqual(self.d("claude -p 'fix tests'", cwd=WS), ("twin", "workspace"))

    def test_workspace_prefix_trap(self):
        self.assertFalse(tr.in_workspace(HOME + "/twinx", RULES))
        self.assertFalse(tr.in_workspace(HOME + "/twin-other/a", RULES))

    def test_workspace_paths_do_not_touch_filesystem(self):
        # ~/twin may be a dead sshfs mount: nothing here may stat it (a stat would hang)
        orig_exists, orig_stat = os.path.exists, os.stat
        def boom(*a, **k):
            raise AssertionError("filesystem touched")
        os.path.exists = boom
        os.stat = boom
        try:
            self.assertTrue(tr.in_workspace(WS + "/deep/dir", RULES))
            self.assertEqual(tr.map_cwd(WS + "/deep/dir", RULES), "~/work/deep/dir")
            self.assertEqual(tr.decide("make", WS + "/deep", IDLE, RULES), ("twin", "workspace"))
        finally:
            os.path.exists, os.stat = orig_exists, orig_stat

    def test_map_cwd(self):
        self.assertEqual(tr.map_cwd(WS, RULES), "~/work")
        self.assertEqual(tr.map_cwd(WS + "/a/b", RULES), "~/work/a/b")
        self.assertEqual(tr.map_cwd(HOME + "/twinx", RULES), "~")
        self.assertEqual(tr.map_cwd("/tmp", RULES), "~")

    # --- always_twin
    def test_always(self):
        self.assertEqual(self.d("ollama run gemma3:4b hi"), ("twin", "always:ollama"))
        self.assertEqual(self.d("/usr/bin/ollama list"), ("twin", "always:ollama"))
        self.assertEqual(self.d("stable-diffusion-webui --port 7860"), ("twin", "always:stable-diffusion*"))

    def test_assignments_and_wrappers_are_skipped(self):
        self.assertEqual(self.d("FOO=1 ollama run x"), ("twin", "always:ollama"))
        self.assertEqual(self.d("time nice ollama run x"), ("twin", "always:ollama"))

    def test_redirect_target_is_not_a_file_reference(self):
        open(os.path.join(self.plain, "out.txt"), "w").close()
        try:
            self.assertEqual(self.d("ollama run x hi > out.txt"), ("twin", "always:ollama"))
        finally:
            os.remove(os.path.join(self.plain, "out.txt"))

    # --- files guard
    def test_files_guard_needs_cwd(self):
        self.assertEqual(self.d("python train.py"), ("local", "files-not-on-twin"))
        self.assertEqual(self.d("claude -p 'fix'"), ("local", "files-not-on-twin"))

    def test_files_guard_existing_relative_path(self):
        open(os.path.join(self.plain, "a.mp3"), "w").close()
        try:
            self.assertEqual(self.d("whisper a.mp3"), ("local", "files-not-on-twin"))
            self.assertEqual(self.d("whisper missing.mp3"), ("twin", "always:whisper"))
        finally:
            os.remove(os.path.join(self.plain, "a.mp3"))

    def test_files_guard_ignores_plain_words(self):
        # a folder named like a subcommand ("run", "build") must not pin a command locally
        os.mkdir(os.path.join(self.plain, "run"))
        try:
            self.assertEqual(self.d("ollama run gemma3:4b hi"), ("twin", "always:ollama"))
            self.assertEqual(self.d("whisper run/a.mp3")[0], "twin")      # path-like but missing file
        finally:
            os.rmdir(os.path.join(self.plain, "run"))

    # --- load_offload
    def test_load_ok_stays_local(self):
        self.assertEqual(self.d("ffmpeg -i /a.mp4 /b.mp4"), ("local", "load-ok:ffmpeg cpu=10% ram=30%"))

    def test_cpu_busy_goes_to_twin(self):
        self.assertEqual(self.d("ffmpeg -i /a.mp4 /b.mp4", load=CPU_BUSY), ("twin", "load:ffmpeg cpu=95% ram=30%"))

    def test_ram_busy_goes_to_twin(self):
        self.assertEqual(self.d("ffmpeg -i /a.mp4 /b.mp4", load=RAM_BUSY)[0], "twin")

    def test_threshold_is_inclusive(self):
        self.assertEqual(self.d("ffmpeg -i /a /b", load=(80.0, 0.0))[0], "twin")
        self.assertEqual(self.d("ffmpeg -i /a /b", load=(79.9, 0.0))[0], "local")

    def test_busy_but_needs_cwd_stays_local(self):
        self.assertEqual(self.d("make -j32", load=CPU_BUSY), ("local", "files-not-on-twin"))

    # --- default
    def test_default(self):
        self.assertEqual(self.d("echo hi"), ("local", "default"))

    # --- matching
    def test_matches(self):
        self.assertTrue(tr.matches(["python", "train.py", "--epochs", "3"], "python *train*.py"))
        self.assertTrue(tr.matches(["claude", "-p", "hi"], "claude -p*"))
        self.assertFalse(tr.matches(["claude", "hi"], "claude -p*"))
        self.assertTrue(tr.matches(["cargo", "build", "--release"], "cargo build*"))

    # --- CLI
    def test_cli_decide(self):
        out = subprocess.run([str(ROUTE), "--rules", str(ROOT / "route/rules.default.toml"),
                              "--cwd", self.plain, "--load", "95,10", "--", "ffmpeg -i /a /b"],
                             capture_output=True, text=True, check=True).stdout.strip()
        self.assertEqual(out, "twin load:ffmpeg cpu=95% ram=10%")

    def test_cli_map_cwd(self):
        out = subprocess.run([str(ROUTE), "--rules", str(ROOT / "route/rules.default.toml"),
                              "--map-cwd", WS + "/p"], capture_output=True, text=True, check=True).stdout
        self.assertEqual(out.strip(), "~/work/p")

    def test_cli_is_fast(self):
        t = time.monotonic()
        subprocess.run([str(ROUTE), "--load", "1,1", "--", "echo hi"], capture_output=True, check=True)
        self.assertLess(time.monotonic() - t, 0.2)


if __name__ == "__main__":
    unittest.main()
