"""The camera service with --watch and --feed linked through local pipes. A stub ffmpeg stands in for
the camera, the placeholder and the decoder, and a plain file stands in for the twin's webcam."""
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from tests.test_camera import CAMERA

FFMPEG = """#!/usr/bin/env python3
# stub ffmpeg: placeholder writes BLACK lines, capture writes FRAME lines, feed copies stdin to the device
import os, signal, sys, time
signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
args, state = sys.argv[1:], os.environ["CAM_STATE"]
if "lavfi" in args:
    with open(args[-1], "ab", buffering=0) as dev:
        while True:
            dev.write(b"BLACK\\n")
            time.sleep(0.1)
if "-input_format" in args:
    if os.environ.get("CAM_BUSY"):
        print("[video4linux2] /dev/video0: Device or resource busy", file=sys.stderr)
        sys.exit(1)
    on = os.path.join(state, "camera-on")
    open(on, "w").close()
    time.sleep(float(os.environ.get("CAM_DELAY") or 0))       # a camera that is slow to deliver its first frame
    try:
        while True:
            sys.stdout.buffer.write(b"FRAME\\n")
            sys.stdout.flush()
            time.sleep(0.05)
    except BrokenPipeError:
        pass
    finally:
        os.unlink(on)
    sys.exit(0)
with open(args[-1], "ab", buffering=0) as dev:
    for line in sys.stdin.buffer:
        dev.write(line)
"""


class CameraLinkTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        self.state, self.stubs = d / "state", d / "bin"
        self.main_run, self.twin_run = d / "main-run", d / "twin-run"
        for p in (self.state, self.stubs, self.main_run, self.twin_run):
            p.mkdir()
        (self.stubs / "ffmpeg").write_text(FFMPEG)
        (self.stubs / "notify-send").write_text('#!/bin/sh\necho "$*" >> "$CAM_STATE/notify"\n')
        for f in self.stubs.iterdir():
            f.chmod(0o755)
        self.dev = d / "video9"
        self.dev.write_bytes(b"")
        self.svc = None

    def start(self, **env):
        twin_env = (f"env PATH={self.stubs}:{os.environ['PATH']} CAM_STATE={self.state} XDG_RUNTIME_DIR={self.twin_run}"
                    f" TWIN_CAMERA_POLL=0.2 TWIN_CAMERA_IDLE=1 {sys.executable} {CAMERA}")
        e = {**os.environ, "PATH": f"{self.stubs}:{os.environ['PATH']}", "CAM_STATE": str(self.state),
             "XDG_RUNTIME_DIR": str(self.main_run), "XDG_CONFIG_HOME": str(self.state), "TWIN_CAMERA_RETRY": "0.5",
             "TWIN_CAMERA_SIZE": "64x48", **env}
        self.svc = subprocess.Popen(
            [sys.executable, str(CAMERA),
             "--watch-cmd", f"{twin_env} --watch --size 64x48 --device {self.dev}",
             "--feed-cmd", f"{twin_env} --feed --size 64x48 --device {self.dev}"],
            env=e, stderr=subprocess.DEVNULL)
        self.wait(lambda: self.status().get("linked"), "the watch link")

    def tearDown(self):
        if self.svc:
            self.svc.kill()
            self.svc.wait()
        subprocess.run(["pkill", "-f", str(self.stubs)])
        subprocess.run(["pkill", "-f", f"{CAMERA} --"])
        self.tmp.cleanup()

    def wait(self, cond, what, timeout=12):
        end = time.time() + timeout
        while time.time() < end:
            try:
                if cond():
                    return
            except (OSError, ValueError):
                pass
            time.sleep(0.1)
        self.fail(f"timed out waiting for {what}")

    def status(self):
        return json.loads((self.main_run / "twinpc" / "camera.json").read_text())

    def tail(self):
        return self.dev.read_bytes()[-60:]

    def test_a_reader_starts_and_stops_the_stream(self):
        self.start()
        self.wait(lambda: b"BLACK" in self.tail(), "the placeholder")
        self.assertFalse((self.state / "camera-on").exists())
        with open(self.dev, "rb"):
            self.wait(lambda: (self.state / "camera-on").exists(), "the camera to turn on")
            self.wait(lambda: self.tail().endswith(b"FRAME\n"), "live frames on the twin's webcam")
            self.assertTrue(self.status()["streaming"])
        self.wait(lambda: not (self.state / "camera-on").exists(), "the camera to turn off after idle")
        size = len(self.dev.read_bytes())
        self.wait(lambda: b"BLACK" in self.dev.read_bytes()[size:], "the placeholder to come back")
        self.assertFalse(self.status()["streaming"])

    def test_camera_busy_notifies_the_twin(self):
        self.start(CAM_BUSY="1")
        with open(self.dev, "rb"):
            self.wait(lambda: "busy" in (self.state / "notify").read_text(), "the busy notification")
        self.assertIn(b"BLACK", self.tail())

    def test_a_dead_feed_brings_the_placeholder_back(self):
        self.start()
        with open(self.dev, "rb"):
            self.wait(lambda: self.tail().endswith(b"FRAME\n"), "live frames")
            feed_pid = int((self.twin_run / "twinpc" / "camera-feed.pid").read_text())
            os.kill(feed_pid, signal.SIGKILL)
            size = len(self.dev.read_bytes())
            self.wait(lambda: b"BLACK" in self.dev.read_bytes()[size:], "the placeholder after the feed died", 5)

    def test_a_dead_watcher_leaves_nothing_that_turns_the_camera_on(self):
        self.start()
        self.wait(lambda: b"BLACK" in self.tail(), "the placeholder")
        watcher = subprocess.run(["pgrep", "-f", "--", f"--watch --size 64x48 --device {self.dev}"],
                                 capture_output=True, text=True).stdout.split()
        for pid in watcher:
            if int(pid) != self.svc.pid:                     # the service's own command line names --watch too
                os.kill(int(pid), signal.SIGKILL)
        self.wait(lambda: not self.status()["linked"], "the link to notice")
        self.wait(lambda: self.status()["linked"], "a new watcher")
        time.sleep(3)
        self.assertFalse((self.state / "camera-on").exists(), "the camera turned on with nobody reading")

    def test_the_placeholder_stays_until_the_first_frame(self):
        self.start(CAM_DELAY="2")
        holder = int((self.twin_run / "twinpc" / "camera-placeholder.pid").read_text())
        with open(self.dev, "rb"):
            self.wait(lambda: (self.state / "camera-on").exists(), "the camera to turn on")
            time.sleep(1)
            os.kill(holder, 0)                               # still alive: the webcam never went without a writer
            self.wait(lambda: self.tail().endswith(b"FRAME\n"), "live frames")

    def test_stopping_the_service_clears_its_status(self):
        self.start()
        self.svc.terminate()
        self.svc.wait(timeout=10)
        self.assertFalse(self.status()["linked"])
        self.svc = None


if __name__ == "__main__":
    unittest.main()
