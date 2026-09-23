import importlib.machinery
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
CAMERA = ROOT / "clip" / "twin-camera"
_loader = importlib.machinery.SourceFileLoader("twin_camera", str(CAMERA))
_spec = importlib.util.spec_from_loader("twin_camera", _loader)
cam = importlib.util.module_from_spec(_spec)
_loader.exec_module(cam)


class SettingsTests(unittest.TestCase):
    def test_defaults_config_and_environment(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d, "config")
            cfg.write_text("TWIN_MAC=aa\nTWIN_CAMERA_SIZE='1920x1080'\nTWIN_CAMERA_FPS=15\n")
            s = cam.settings(cfg, env={"TWIN_CAMERA_FPS": "25"})
            self.assertEqual(s, {"TWIN_CAMERA_SIZE": "1920x1080", "TWIN_CAMERA_FPS": "25",
                                 "TWIN_CAMERA_DEVICE": "/dev/video0"})
            self.assertEqual(cam.settings(Path(d, "missing"), env={})["TWIN_CAMERA_SIZE"], "1280x720")

    def test_settings_validation(self):
        for key, bad in [("TWIN_CAMERA_SIZE", "1280x720;id"), ("TWIN_CAMERA_FPS", "30 -f"),
                         ("TWIN_CAMERA_DEVICE", "/etc/passwd")]:
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, key):
                cam.settings(Path("/nonexistent"), env={key: bad})


class EventTests(unittest.TestCase):
    def test_round_trip(self):
        self.assertEqual(cam.parse_event(cam.event("busy", reason="x")), {"event": "busy", "reason": "x"})

    def test_bad_lines(self):
        for bad in (b"nonsense\n", b"[1]\n", b'{"event": 3}\n'):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                cam.parse_event(bad)


class ReaderTests(unittest.TestCase):
    def make_proc(self, d, holders):
        proc = Path(d, "proc")
        for pid, targets in holders.items():
            fd = proc / str(pid) / "fd"
            fd.mkdir(parents=True)
            for i, target in enumerate(targets):
                os.symlink(target, fd / str(i))
        (proc / "self").mkdir()
        return proc

    def test_readers_exclude_own_writers(self):
        with tempfile.TemporaryDirectory() as d:
            proc = self.make_proc(d, {10: ["/dev/video9", "/dev/null"], 11: ["/dev/video9"],
                                      12: ["/dev/video0"], 13: ["/dev/video9"]})
            self.assertEqual(cam.readers("/dev/video9", proc), {10, 11, 13})
            self.assertEqual(cam.readers("/dev/video9", proc, exclude={11, 13}), {10})

    def test_unreadable_processes_are_skipped(self):
        with tempfile.TemporaryDirectory() as d:
            proc = self.make_proc(d, {10: ["/dev/video9"]})
            (proc / "20").mkdir()                        # no fd directory (a process we may not inspect)
            self.assertEqual(cam.readers("/dev/video9", proc), {10})


class DemandTests(unittest.TestCase):
    def test_wanted_at_once_idle_after_the_delay(self):
        d = cam.Demand(idle_after=5)
        self.assertIsNone(d.update(0, 0))
        self.assertEqual(d.update(1, 1), "wanted")
        self.assertIsNone(d.update(2, 2))
        self.assertIsNone(d.update(0, 3))
        self.assertIsNone(d.update(0, 7.9))
        self.assertIsNone(d.update(1, 8))               # a reader came back: the countdown restarts
        self.assertIsNone(d.update(0, 9))
        self.assertEqual(d.update(0, 14), "idle")
        self.assertIsNone(d.update(0, 30))


class ArgvTests(unittest.TestCase):
    def test_capture_feed_placeholder(self):
        self.assertEqual(cam.capture_argv("/dev/video0", "1280x720", "30"),
                         ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "v4l2", "-input_format", "mjpeg",
                          "-video_size", "1280x720", "-framerate", "30", "-i", "/dev/video0",
                          "-c", "copy", "-f", "mjpeg", "-"])
        self.assertEqual(cam.feed_argv("/dev/video9", "1280x720"),
                         ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "mjpeg", "-i", "-",
                          "-vf", "scale=1280:720", "-pix_fmt", "yuv420p", "-f", "v4l2", "/dev/video9"])
        self.assertEqual(cam.placeholder_argv("/dev/video9", "1280x720"),
                         ["ffmpeg", "-hide_banner", "-loglevel", "error", "-re", "-f", "lavfi",
                          "-i", "color=black:size=1280x720:rate=2", "-pix_fmt", "yuv420p", "-f", "v4l2", "/dev/video9"])


class SelftestTests(unittest.TestCase):
    def test_status_file(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": d}):
            self.assertEqual(cam.selftest(), 2)
            status = Path(d, "twinpc", "camera.json")
            status.parent.mkdir()
            status.write_text(json.dumps({"linked": True, "streaming": False, "code": cam.code_hash()}))
            self.assertEqual(cam.selftest(fresh=True), 0)
            status.write_text(json.dumps({"linked": False, "streaming": False, "code": cam.code_hash()}))
            self.assertEqual(cam.selftest(), 1)
            status.write_text(json.dumps({"linked": True, "streaming": False, "code": "old"}))
            self.assertEqual(cam.selftest(), 0)
            self.assertEqual(cam.selftest(fresh=True), 3)


if __name__ == "__main__":
    unittest.main()
