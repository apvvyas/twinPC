# Mic and Camera Sharing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Apps on the twin get a "Main PC microphone" and a "Main PC camera" webcam. The main PC's camera streams only while a twin app has the webcam open, and its mic is only read while a twin app records.

**Architecture:**
- **Mic:** a PipeWire `pulse-tunnel` source on the twin, riding the existing SSH forward (`127.0.0.1:4713`).
- **Camera:** a `v4l2loopback` device `/dev/video9` on the twin. `clip/twin-camera` (Python stdlib) runs in three roles:
  - a main-PC user service that keeps a watch link to `twin-camera --watch`;
  - `--watch`, which counts the webcam's readers and keeps a placeholder picture on it while idle;
  - `--feed`, which decodes the main PC's MJPEG stream into the device.
- **Install:** a new `camera` feature in `twinpc`.

**Tech Stack:** Python 3 stdlib, ffmpeg, v4l2loopback-dkms, PipeWire (`libpipewire-module-pulse-tunnel`), systemd user units, bash.

**Spec:** `docs/superpowers/specs/2026-09-24-mic-camera-sharing-design.md`

## Global Constraints

- The camera is opened on the main PC only while a twin app has `/dev/video9` open, with a 5 s delay before stopping. The mic record stream stays corked until a twin app records.
- Everything goes over the existing SSH link: no new ports, and the twin gets no key for the main PC. Only the main PC starts the camera.
- The twin webcam is `/dev/video9`, `card_label="Main PC camera"`, `exclusive_caps=1`. The mic is PipeWire node `main-pc-mic`, "Main PC microphone".
- Settings come from `~/.config/twinpc/config` or the environment: `TWIN_CAMERA_SIZE` (default `1280x720`, `^\d{2,4}x\d{2,4}$`), `TWIN_CAMERA_FPS` (default `30`, `^\d{1,3}$`), and `TWIN_CAMERA_DEVICE` (default `/dev/video0`, `^/dev/video\d+$`).
- ffmpeg command lines are fixed argv lists; no shell is used for them.
- Supported with a `pacman` twin only (DKMS module + the running kernel's `-headers` package). Other twins are skipped with "not supported yet (planned)".
- Every new feature updates README.md: its own card, a cheat-sheet row, a troubleshooting entry, the layout and the test counts.
- The public repo contains no personal data. Every commit message ends with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Spec refinements made while planning

1. **`twin camera off` for the mic** renames the twin's `main-pc-mic.conf` to `…conf.off` and restarts the twin's PipeWire; `on` reverses that. `pactl unload-module` can't remove a module loaded from a config file, and a config file with `reconnect.interval.ms` is the robust way to survive forward restarts. The mic therefore stays off until `twin camera on`, not only until the next login, and the twin's audio blips once when switching.
2. **The main PC tells the twin the picture size** (`--watch --size S`, `--feed --size S`), so the placeholder and the live frames always use the same format. The feed scales to that size.
3. **Freshness:** `twin-camera --selftest --fresh` compares the running code's fingerprint, saved in its status file, with the file on disk, like `twin-clipd` and `twin-shelf`, and the install's link step restarts the service when they differ.
4. **Acceptance runs in the user's terminal:** the module step needs sudo on the twin, which needs a real terminal for the password.

## Review Focus

1. **A twin app opens the webcam while the main PC's camera is in use here:** the placeholder stays, the twin gets a "busy" notification, and nothing hangs or loops. Pinned by `test_camera_busy_notifies_the_twin` (Task 2).
2. **The stream dies mid-call** (ffmpeg or ssh): the twin's device returns to the placeholder instead of freezing. Pinned by `test_a_dead_feed_brings_the_placeholder_back` (Task 2).
3. **The watcher must not count its own writers** (placeholder, feed) as readers, or the camera would never turn off. Pinned by `test_readers_exclude_own_writers` (Task 1) and `test_a_reader_starts_and_stops_the_stream` (Task 2).
4. **A malformed config value** (e.g. `TWIN_CAMERA_SIZE=1280x720;id`) must be refused, not passed to ffmpeg or the ssh command line. Pinned by `test_settings_validation` (Task 1).
5. **A stopped service must not keep reporting "connected"** from a stale status file. Pinned by `test_stopping_the_service_clears_its_status` (Task 2).

---

### Task 1: twin-camera core

**Files:**
- Create: `clip/twin-camera` (executable)
- Test: `tests/test_camera.py`

**Interfaces:**
- Produces:
  - `settings(config: Path | None = None, env: dict | None = None) -> dict` (keys `TWIN_CAMERA_SIZE`, `TWIN_CAMERA_FPS`, `TWIN_CAMERA_DEVICE`; raises `ValueError`)
  - `event(name, **fields) -> bytes` and `parse_event(line) -> dict` (raises `ValueError`)
  - `readers(device: str, proc: Path = Path("/proc"), exclude=()) -> set[int]`
  - `class Demand(idle_after)` with `update(count: int, now: float) -> "wanted" | "idle" | None`
  - `capture_argv(device, size, fps)`, `feed_argv(device, size)` and `placeholder_argv(device, size)`, each returning `list[str]`
  - `runtime_dir() -> Path`, `code_hash() -> str`, `CODE`, `selftest(fresh=False) -> int` (reads `runtime_dir()/"camera.json"` = `{"linked", "streaming", "code"}`: 0 when linked, 1 when not, 2 when missing, 3 when fresh and the code differs)
  - constants `TWIN_DEVICE`, `DEFAULTS`, `PATTERNS`, `IDLE_AFTER`, `POLL`, `RETRY`, `BUSY_WITHIN`

- [ ] **Step 1: Write the failing tests**

`tests/test_camera.py`:

```python
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
```

- [ ] **Step 2: Run them to see them fail**

Run: `python3 -m unittest tests.test_camera`
Expected: ERROR — `FileNotFoundError` for `clip/twin-camera`.

- [ ] **Step 3: Write the core**

`clip/twin-camera` (then `chmod +x clip/twin-camera`):

```python
#!/usr/bin/env python3
"""twin-camera — the main PC's camera as a webcam on the twin, only while the twin uses it (man twin: CAMERA).

Main PC (default): a user service that keeps `ssh twin twin-camera --watch` open. When the twin reports
that an app opened its webcam, it streams the camera's MJPEG into `ssh twin twin-camera --feed`, and it
stops once the twin reports idle.
Twin: --watch keeps a placeholder picture on the virtual webcam and reports readers; --feed decodes the
stream into it.
"""
import argparse
import hashlib
import json
import os
import re
import shlex
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

TWIN_DEVICE = "/dev/video9"
DEFAULTS = {"TWIN_CAMERA_SIZE": "1280x720", "TWIN_CAMERA_FPS": "30", "TWIN_CAMERA_DEVICE": "/dev/video0"}
PATTERNS = {"TWIN_CAMERA_SIZE": r"\d{2,4}x\d{2,4}", "TWIN_CAMERA_FPS": r"\d{1,3}",
            "TWIN_CAMERA_DEVICE": r"/dev/video\d+"}
IDLE_AFTER = float(os.environ.get("TWIN_CAMERA_IDLE") or 5)    # seconds without readers before stopping
POLL = float(os.environ.get("TWIN_CAMERA_POLL") or 1)          # seconds between reader counts
RETRY = float(os.environ.get("TWIN_CAMERA_RETRY") or 10)       # seconds between ssh attempts
BUSY_WITHIN = 2.0                                              # a capture that dies this fast couldn't open the camera


def code_hash():
    """A fingerprint of this program's file as it is on disk now."""
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()[:16]


CODE = code_hash()                       # the code this process runs; --selftest --fresh compares the two


def log(msg):
    print(f"twin-camera: {msg}", file=sys.stderr, flush=True)


def notify(msg):
    try:
        subprocess.run(["notify-send", "-a", "twinPC", "twinPC camera", msg], timeout=5,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        pass


def runtime_dir():
    return Path(os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}") / "twinpc"


def settings(config=None, env=None):
    """Camera settings from ~/.config/twinpc/config (NAME=value lines) and the environment, validated."""
    env = os.environ if env is None else env
    if config is None:
        config = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "twinpc" / "config"
    values = dict(DEFAULTS)
    try:
        for line in config.read_text().splitlines():
            key, sep, value = line.strip().partition("=")
            if sep and key in DEFAULTS:
                values[key] = value.strip().strip("'\"")
    except OSError:
        pass
    for key in DEFAULTS:
        if env.get(key):
            values[key] = env[key]
    for key, pattern in PATTERNS.items():
        if not re.fullmatch(pattern, values[key]):
            raise ValueError(f"{key}={values[key]!r} is not valid")
    return values


def event(name, **fields):
    return (json.dumps({"event": name, **fields}) + "\n").encode()


def parse_event(line):
    try:
        h = json.loads(line)
    except ValueError:
        raise ValueError("not JSON") from None
    if not isinstance(h, dict) or not isinstance(h.get("event"), str):
        raise ValueError("not an event")
    return h


def readers(device, proc=Path("/proc"), exclude=()):
    """The pids, other than `exclude`, that have `device` open."""
    found = set()
    for entry in proc.iterdir():
        if not entry.name.isdigit() or int(entry.name) in exclude:
            continue
        try:
            fds = list((entry / "fd").iterdir())
        except OSError:
            continue
        for fd in fds:
            try:
                if os.readlink(fd) == device:
                    found.add(int(entry.name))
                    break
            except OSError:
                continue
    return found


class Demand:
    """Turns reader counts into events: 'wanted' at once, 'idle' after `idle_after` seconds with none."""

    def __init__(self, idle_after=IDLE_AFTER):
        self.idle_after, self.wanted, self.empty_since = idle_after, False, None

    def update(self, count, now):
        if count > 0:
            self.empty_since = None
            if not self.wanted:
                self.wanted = True
                return "wanted"
            return None
        if not self.wanted:
            return None
        if self.empty_since is None:
            self.empty_since = now
        if now - self.empty_since >= self.idle_after:
            self.wanted, self.empty_since = False, None
            return "idle"
        return None


def capture_argv(device, size, fps):
    """The main PC's camera as MJPEG on stdout, not re-encoded."""
    return ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "v4l2", "-input_format", "mjpeg",
            "-video_size", size, "-framerate", str(fps), "-i", device, "-c", "copy", "-f", "mjpeg", "-"]


def feed_argv(device, size):
    """MJPEG on stdin, decoded into the twin's virtual webcam at the agreed size."""
    w, h = size.split("x")
    return ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "mjpeg", "-i", "-",
            "-vf", f"scale={w}:{h}", "-pix_fmt", "yuv420p", "-f", "v4l2", device]


def placeholder_argv(device, size):
    """A black picture, twice a second, so apps always see a working camera."""
    return ["ffmpeg", "-hide_banner", "-loglevel", "error", "-re", "-f", "lavfi",
            "-i", f"color=black:size={size}:rate=2", "-pix_fmt", "yuv420p", "-f", "v4l2", device]


def selftest(fresh=False):
    try:
        status = json.loads((runtime_dir() / "camera.json").read_text())
    except (OSError, ValueError):
        print("camera sharing: not running (twin camera on)")
        return 2
    if fresh and status.get("code") != code_hash():
        print("camera sharing: running an older version — restart needed (systemctl --user restart twin-camera.service)")
        return 3
    print(f"camera sharing: {'connected to the twin' if status.get('linked') else 'not connected to the twin'}"
          f"{' · streaming now' if status.get('streaming') else ''}")
    return 0 if status.get("linked") else 1
```

- [ ] **Step 4: Run them to see them pass**

Run: `python3 -m unittest tests.test_camera`
Expected: `OK`.

- [ ] **Step 5: Commit**

```bash
git add clip/twin-camera tests/test_camera.py
git commit -m "feat(camera): twin-camera core — settings, events, reader counting, demand, ffmpeg argv

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: The camera service, the watcher and the feed

**Files:**
- Modify: `clip/twin-camera` (append the runtime and `main`)
- Test: `tests/test_camera_link.py`

**Interfaces:**
- Consumes: everything from Task 1.
- Produces:
  - CLI: `twin-camera [--host H]` (the main service); `twin-camera --watch --size S [--device D]`; `twin-camera --feed --size S [--device D]`; `twin-camera --selftest [--fresh]`; hidden `--watch-cmd` and `--feed-cmd` (for tests).
  - Status file `$XDG_RUNTIME_DIR/twinpc/camera.json` on the main PC.
  - On the twin, `$XDG_RUNTIME_DIR/twinpc/camera-feed.pid` (the feed's ffmpeg pid) and `camera-placeholder.pid`.
  - Watch link: the twin sends `ready`, `wanted` and `idle`; the main PC sends `busy {reason}`.

- [ ] **Step 1: Write the failing tests**

`tests/test_camera_link.py`:

```python
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

    def test_stopping_the_service_clears_its_status(self):
        self.start()
        self.svc.terminate()
        self.svc.wait(timeout=10)
        self.assertFalse(self.status()["linked"])
        self.svc = None


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run them to see them fail**

Run: `timeout 200 python3 -m unittest tests.test_camera_link`
Expected: FAIL/ERROR — `timed out waiting for the watch link` (there is no `main` yet).

- [ ] **Step 3: Write the runtime**

Append to `clip/twin-camera`:

```python
def pid_alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def read_pid(path):
    """The live pid stored in `path`, or None."""
    try:
        pid = int(path.read_text().strip())
    except (OSError, ValueError):
        return None
    return pid if pid_alive(pid) else None


def watch(device, size, inp, out, run=None, proc=Path("/proc")):
    """The twin side of the watch link: keep a placeholder on the webcam, report readers, show busy notices."""
    run = run or runtime_dir()
    run.mkdir(parents=True, exist_ok=True)
    feed_lock, holder_file = run / "camera-feed.pid", run / "camera-placeholder.pid"
    if not os.path.exists(device):
        log(f"no virtual camera at {device} — is the v4l2loopback module loaded? (sudo modprobe v4l2loopback)")
        return 2
    write, stop = threading.Lock(), threading.Event()

    def send(frame):
        with write:
            out.write(frame)
            out.flush()

    def listen():
        for line in inp:
            try:
                ev = parse_event(line)
            except ValueError:
                continue
            if ev["event"] == "busy":
                notify(f"Main PC camera is {ev.get('reason') or 'busy'}")
        stop.set()                                         # the main PC went away

    threading.Thread(target=listen, daemon=True).start()
    demand, holder = Demand(IDLE_AFTER), None
    send(event("ready"))
    try:
        while not stop.is_set():
            feeding = read_pid(feed_lock)
            if feeding is not None:
                if holder is not None and holder.poll() is None:
                    holder.terminate()
                    holder.wait()
                holder = None
            elif holder is None or holder.poll() is not None:
                holder = subprocess.Popen(placeholder_argv(device, size), stdin=subprocess.DEVNULL,
                                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                holder_file.write_text(str(holder.pid))
            own = {p for p in (feeding, holder.pid if holder is not None else None) if p}
            change = demand.update(len(readers(device, proc, own)), time.monotonic())
            if change:
                log(f"the twin's webcam is {'in use' if change == 'wanted' else 'idle'}")
                send(event(change))
            stop.wait(POLL)
        return 0
    finally:
        if holder is not None and holder.poll() is None:
            holder.terminate()
        holder_file.unlink(missing_ok=True)


def feed(device, size, inp, run=None):
    """The twin side of a stream: stop the placeholder and decode the main PC's MJPEG into the webcam."""
    run = run or runtime_dir()
    run.mkdir(parents=True, exist_ok=True)
    lock = run / "camera-feed.pid"
    lock.write_text(str(os.getpid()))                      # first, so the watcher doesn't restart the placeholder
    holder = read_pid(run / "camera-placeholder.pid")
    if holder is not None:
        try:
            os.kill(holder, signal.SIGTERM)
        except OSError:
            pass
        for _ in range(20):
            if not pid_alive(holder):
                break
            time.sleep(0.05)
    p = subprocess.Popen(feed_argv(device, size), stdin=inp, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    lock.write_text(str(p.pid))
    try:
        return p.wait()
    finally:
        lock.unlink(missing_ok=True)


class Camera:
    """The main PC side: the watch link to the twin, and the camera stream while the twin wants it."""

    def __init__(self, host, cfg, watch_cmd=None, feed_cmd=None):
        size, fps, device = cfg["TWIN_CAMERA_SIZE"], cfg["TWIN_CAMERA_FPS"], cfg["TWIN_CAMERA_DEVICE"]
        ssh = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "-o", "ServerAliveInterval=15", "--", host]
        self.watch_cmd = watch_cmd or [*ssh, f".local/bin/twin-camera --watch --size {size}"]
        self.feed_cmd = feed_cmd or [*ssh, f".local/bin/twin-camera --feed --size {size}"]
        self.capture = capture_argv(device, size, fps)
        self.lock = threading.Lock()
        self.stream, self.wanted, self.watcher = None, False, None
        self.status_path = runtime_dir() / "camera.json"
        self.status = {"linked": False, "streaming": False, "code": CODE}

    def save(self, **changes):
        self.status.update(changes)
        self.status_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.status_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.status))
        tmp.replace(self.status_path)

    def tell_twin(self, frame):
        w = self.watcher
        if w is not None:
            try:
                w.stdin.write(frame)
                w.stdin.flush()
            except OSError:
                pass

    def run(self):
        self.save(linked=False, streaming=False)
        while True:
            w = subprocess.Popen(self.watch_cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE)
            self.watcher = w
            try:
                for line in w.stdout:
                    try:
                        name = parse_event(line)["event"]
                    except ValueError:
                        continue
                    if name == "ready":
                        self.save(linked=True)
                        log("connected to the twin's camera watcher")
                    elif name == "wanted":
                        self.wanted = True
                        self.start()
                    elif name == "idle":
                        self.wanted = False
                        self.stop()
            except Exception as e:                               # never let one bad line end the link for good
                log(f"watch link failed: {e!r}")
            finally:
                self.wanted = False
                self.stop()
                self.watcher = None
                self.save(linked=False)
                w.kill()
                w.wait()
            time.sleep(RETRY)

    def start(self):
        with self.lock:
            if self.stream is not None:
                return
            cap = subprocess.Popen(self.capture, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            feed_proc = subprocess.Popen(self.feed_cmd, stdin=cap.stdout, stdout=subprocess.DEVNULL)
            cap.stdout.close()                                   # the feed owns the pipe now
            self.stream = (cap, feed_proc)
        self.save(streaming=True)
        log("streaming the camera to the twin")
        threading.Thread(target=self.supervise, args=(cap, feed_proc, time.monotonic()), daemon=True).start()

    def stop(self):
        with self.lock:
            stream, self.stream = self.stream, None
        if stream is None:
            return
        for p in stream:
            if p.poll() is None:
                p.terminate()
        for p in stream:
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()
        self.save(streaming=False)
        log("camera stream stopped")

    def supervise(self, cap, feed_proc, began):
        err = cap.stderr.read().decode(errors="replace").strip()
        rc = cap.wait()
        with self.lock:
            mine = self.stream is not None and self.stream[0] is cap
            if mine:
                self.stream = None
        if not mine:
            return                                               # stopped on purpose
        if feed_proc.poll() is None:
            feed_proc.terminate()
        self.save(streaming=False)
        if rc != 0 and time.monotonic() - began < BUSY_WITHIN:
            log(f"camera unavailable: {err.splitlines()[-1] if err else f'ffmpeg exit {rc}'}")
            self.tell_twin(event("busy", reason="busy or not found on the main PC"))
            return
        log(f"camera stream ended (ffmpeg exit {rc}) — retrying in 3 s")
        time.sleep(3)
        if self.wanted:
            self.start()

    def shutdown(self, *_):
        self.wanted = False
        self.stop()
        self.save(linked=False, streaming=False)
        os._exit(0)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="twin-camera", description="the main PC's camera as a webcam on the twin")
    ap.add_argument("--watch", action="store_true", help="the twin side: keep the placeholder, report readers")
    ap.add_argument("--feed", action="store_true", help="the twin side: decode the stream on stdin into the webcam")
    ap.add_argument("--size", default=DEFAULTS["TWIN_CAMERA_SIZE"], help="picture size (WxH)")
    ap.add_argument("--device", default=TWIN_DEVICE, help="the twin's virtual webcam")
    ap.add_argument("--host", default=os.environ.get("TWIN_HOST") or "twin", help="ssh alias of the twin")
    ap.add_argument("--selftest", action="store_true", help="is the service running and linked to the twin?")
    ap.add_argument("--fresh", action="store_true", help="with --selftest: also fail (3) if it runs older code")
    ap.add_argument("--watch-cmd", help=argparse.SUPPRESS)    # tests: run the twin side without ssh
    ap.add_argument("--feed-cmd", help=argparse.SUPPRESS)
    a = ap.parse_args(argv)
    if not re.fullmatch(PATTERNS["TWIN_CAMERA_SIZE"], a.size):
        ap.error(f"--size {a.size!r} is not WxH")
    if a.watch:
        return watch(a.device, a.size, sys.stdin.buffer, sys.stdout.buffer)
    if a.feed:
        return feed(a.device, a.size, sys.stdin.buffer)
    if a.selftest:
        return selftest(a.fresh)
    try:
        cfg = settings()
    except ValueError as e:
        log(str(e))
        return 2
    cam = Camera(a.host, cfg, shlex.split(a.watch_cmd) if a.watch_cmd else None,
                 shlex.split(a.feed_cmd) if a.feed_cmd else None)
    signal.signal(signal.SIGTERM, cam.shutdown)
    cam.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run them to see them pass**

Run: `timeout 250 python3 -m unittest tests.test_camera tests.test_camera_link`
Expected: `OK`.

- [ ] **Step 5: Commit**

```bash
git add clip/twin-camera tests/test_camera_link.py
git commit -m "feat(camera): camera service, twin watcher and feed — stream only while a twin app reads

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: The `camera` feature in `twinpc`

**Files:**
- Create: `twinpc/main-pc-mic.conf`, `main/twin-camera.service`
- Modify: `tool/packages.toml`, `tool/twinpc_lib/features.py`, `tests/tool/test_features.py`
- Test: `tests/tool/test_camera.py`

**Interfaces:**
- Consumes: `twin-camera --selftest --fresh` (Tasks 1–2); `twin-audio.service` from the `audio` feature.
- Produces: feature `camera`, placed after `audio`, with step ids in this order:
  1. `camera.main.packages`, `camera.twin.packages`
  2. `camera.main.audio`
  3. `camera.twin.mic`, `camera.twin.default-mic`
  4. `camera.twin.module`, `camera.twin.options`, `camera.twin.autoload`, `camera.twin.loaded`
  5. `camera.main.command`, `camera.twin.command`
  6. `camera.main.unit`, `camera.main.service`, `camera.main.link`

- [ ] **Step 1: Write the failing tests**

`tests/tool/test_camera.py`:

```python
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
```

In `tests/tool/test_features.py`, `test_key_steps_exist`: add `"camera.main.link",` after `"shelf.main.link",`.

- [ ] **Step 2: Run them to see them fail**

Run: `python3 -m unittest tests.tool.test_camera tests.tool.test_features`
Expected: FAIL/ERROR — no `camera` steps, and `'camera' is not in list`.

- [ ] **Step 3: Write the implementation**

`twinpc/main-pc-mic.conf`:

```
# Input from the main PC's microphone (its default input). 127.0.0.1:4713 is forwarded over SSH by the
# main PC's twin-audio.service; the stream there stays corked until something here records.
# `twin camera off` on the main PC renames this file to main-pc-mic.conf.off.
context.modules = [
  { name = libpipewire-module-pulse-tunnel
    args = {
      tunnel.mode           = source
      pulse.server.address  = "tcp:127.0.0.1:4713"
      reconnect.interval.ms = 5000
      node.name             = "main-pc-mic"
      node.description      = "Main PC microphone"
      audio.rate            = 48000
      audio.channels        = 1
      audio.position        = [ MONO ]
    }
  }
]
```

`main/twin-camera.service`:

```ini
[Unit]
Description=twinPC camera — the main PC's camera as a webcam on the twin, while the twin uses it
After=network-online.target

[Service]
ExecStart=%h/.local/bin/twin-camera --host twin
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
```

`tool/packages.toml` — add after the `gtk4-layer-shell` line:

```toml
ffmpeg = { apt = "ffmpeg", pacman = "ffmpeg", dnf = "ffmpeg-free" }
```

`tool/twinpc_lib/features.py` — add after `_audio`:

```python
CAMERA_OPTIONS = 'options v4l2loopback video_nr=9 card_label="Main PC camera" exclusive_caps=1\n'


def _camera(profile, repo, v):
    pkg = profile.get("twin", {}).get("pkg", "")
    if pkg != "pacman":
        return [unsupported_step("camera", "twin",
                                 f"the twin's virtual camera needs pacman — packages '{pkg}' is not supported yet (planned)")]
    r = v["repo"]
    kernel = "k=$(pacman -Qqo /usr/lib/modules/$(uname -r)/vmlinuz)"
    return [
        pkg_step("camera", "main", _pk(profile, "main"), ["ffmpeg"]),
        pkg_step("camera", "twin", _pk(profile, "twin"), ["ffmpeg"]),
        manual_step("camera.main.audio", "camera", "main", "the audio feature (the mic uses its SSH forward)",
                    "Install the audio feature first: tool/twinpc install audio",
                    check="systemctl --user is-enabled --quiet twin-audio.service"),
        file_step("camera.twin.mic", "camera", "twin", "$HOME/.config/pipewire/pipewire.conf.d/main-pc-mic.conf",
                  _read(repo, "twinpc/main-pc-mic.conf"),
                  after=f"{USER_ENV} systemctl --user restart pipewire pipewire-pulse wireplumber"),
        cmd_step("camera.twin.default-mic", "camera", "twin", "make 'Main PC microphone' the twin's default input",
                 check=f"{USER_ENV} pactl get-default-source | grep -qx main-pc-mic",
                 apply=f"{USER_ENV} for i in $(seq 20); do pactl list short sources | grep -q main-pc-mic && break;"
                       " sleep 0.5; done; pactl set-default-source main-pc-mic"),
        cmd_step("camera.twin.module", "camera", "twin", "install the virtual-camera kernel module (DKMS)",
                 check=f'{kernel} && pacman -Q v4l2loopback-dkms "$k-headers" >/dev/null 2>&1',
                 apply=f'{kernel} && pacman -S --needed --noconfirm v4l2loopback-dkms "$k-headers"',
                 root=True, check_root=False),
        file_step("camera.twin.options", "camera", "twin", "/etc/modprobe.d/twinpc-camera.conf", CAMERA_OPTIONS,
                  root=True, describe="name the twin's webcam 'Main PC camera' (/dev/video9)"),
        file_step("camera.twin.autoload", "camera", "twin", "/etc/modules-load.d/twinpc-camera.conf", "v4l2loopback\n",
                  root=True, describe="load the virtual-camera module at boot"),
        cmd_step("camera.twin.loaded", "camera", "twin", "load the virtual-camera module now",
                 check="lsmod | grep -q '^v4l2loopback ' && [ -e /dev/video9 ]",
                 apply="modprobe -r v4l2loopback 2>/dev/null; modprobe v4l2loopback", root=True, check_root=False),
        cmd_step("camera.main.command", "camera", "main", "install twin-camera on this PC",
                 check=f'[ "$(readlink ~/.local/bin/twin-camera)" = "{r}/clip/twin-camera" ]',
                 apply=f'mkdir -p ~/.local/bin && ln -sf "{r}/clip/twin-camera" ~/.local/bin/twin-camera'),
        file_step("camera.twin.command", "camera", "twin", "$HOME/.local/bin/twin-camera",
                  _read(repo, "clip/twin-camera"), mode="755", describe="install twin-camera on the twin"),
        file_step("camera.main.unit", "camera", "main", "$HOME/.config/systemd/user/twin-camera.service",
                  repo_unit(repo, "main/twin-camera.service").replace(" --host twin\n", f" --host {v['host']}\n"),
                  after="systemctl --user daemon-reload && systemctl --user try-restart twin-camera.service"),
        unit_step("camera.main.service", "camera", "main", "twin-camera.service"),
        cmd_step("camera.main.link", "camera", "main", "connect the camera service to the twin",
                 check="~/.local/bin/twin-camera --selftest --fresh >/dev/null",
                 apply="systemctl --user restart twin-camera.service && sleep 5"
                       " && ~/.local/bin/twin-camera --selftest --fresh"),
    ]
```

and register it after `audio` in `BUILDERS`:

```python
BUILDERS = {"connection": _connection, "cli": _cli, "gpu-stack": _gpu_stack, "routing": _routing,
            "mount": _mount, "power": _power, "unlock": _unlock, "kvm": _kvm, "clipboard": _clipboard,
            "shelf": _shelf, "audio": _audio, "camera": _camera, "desktop": _desktop, "gui": _gui,
            "nic-fix": _nic_fix}
```

- [ ] **Step 4: Run them to see them pass**

Run: `python3 -m unittest tests.tool.test_camera tests.tool.test_features tests.tool.test_shelf tests.tool.test_clipboard tests.tool.test_adapters tests.tool.test_review_fixes tests.tool.test_cli`
Expected: `OK`.

- [ ] **Step 5: Commit**

```bash
git add twinpc/main-pc-mic.conf main/twin-camera.service tool/packages.toml tool/twinpc_lib/features.py tests/tool/test_camera.py tests/tool/test_features.py
git commit -m "feat(tool): camera feature — twin mic input, virtual webcam module, twin-camera service

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: `twin camera`, docs, the system check, and acceptance on the real machines

**Files:**
- Modify: `twin`, `twin-completion.bash`, `man/twin.1`, `README.md`
- Create: `tests/test_camera.sh`

- [ ] **Step 1: Write the failing system check**

`tests/test_camera.sh` (then `chmod +x tests/test_camera.sh`):

```bash
#!/usr/bin/env bash
# The twin's "Main PC camera" shows live video while something reads it (and this PC's camera is on only
# then), and "Main PC microphone" records sound on the twin.
set -u
ssh twin '[ -e /dev/video9 ]' || { echo "FAIL the twin has no /dev/video9"; exit 1; }

# read the twin's webcam for 6 s; the brightest frame's average luma must be above black (16)
ssh twin 'ffmpeg -hide_banner -loglevel error -f v4l2 -i /dev/video9 -t 6
  -vf signalstats,metadata=print:key=lavfi.signalstats.YAVG:file=/tmp/twinpc-camera-test.txt -f null -' &
reader=$!
on=0
for _ in $(seq 30); do
  pgrep -f "input_format mjpeg" >/dev/null && { on=1; break; }; sleep 0.3
done
wait $reader
peak=$(ssh twin 'sed -n "s/.*YAVG=//p" /tmp/twinpc-camera-test.txt | sort -n | tail -1; rm -f /tmp/twinpc-camera-test.txt')
[[ $on == 1 ]] || { echo "FAIL this PC's camera never turned on"; exit 1; }
python3 -c "import sys; sys.exit(0 if float('${peak:-0}') > 20 else 1)" || { echo "FAIL only black frames (peak Y ${peak:-none})"; exit 1; }
off=0
for _ in $(seq 40); do
  pgrep -f "input_format mjpeg" >/dev/null || { off=1; break; }; sleep 0.5
done
[[ $off == 1 ]] || { echo "FAIL this PC's camera stayed on after the twin stopped reading"; exit 1; }

# record 1 s from the main PC's microphone on the twin; it must not be pure silence
rms=$(ssh twin 'bash -s' <<'EOF'
export XDG_RUNTIME_DIR=/run/user/$(id -u)
timeout 2 parecord -d main-pc-mic --raw --format=s16le --rate=16000 --channels=1 2>/dev/null |
  python3 -c 'import sys, array, math; a = array.array("h", sys.stdin.buffer.read()); print(int(math.sqrt(sum(x * x for x in a) / max(len(a), 1))))'
EOF
)
[[ ${rms:-0} -gt 0 ]] || { echo "FAIL the twin recorded silence from 'Main PC microphone'"; exit 1; }
echo "PASS camera and mic (peak Y $peak, mic rms $rms)"
```

- [ ] **Step 2: Run it to see it fail**

Run: `bash tests/test_camera.sh`
Expected: `FAIL the twin has no /dev/video9` (nothing is installed yet).

- [ ] **Step 3: Add `twin camera`, completion, the manual and the README**

In `twin`, add to the usage text after the `twin shelf` line:

```
  twin camera [status|on|off]  this PC's mic and camera for apps on twin (camera only while twin uses it)
```

and add a case branch before `  route)`:

```bash
  camera)
    # this PC's mic and camera on the twin: twin-camera.service here + main-pc-mic.conf there (man twin: CAMERA)
    MIC='f=$HOME/.config/pipewire/pipewire.conf.d/main-pc-mic.conf; export XDG_RUNTIME_DIR=/run/user/$(id -u)'
    case ${1:-status} in
      status)
        "$HOME/.local/bin/twin-camera" --selftest || true
        ssh -o ConnectTimeout=3 -o BatchMode=yes "$HOST" "$MIC"'
          [ -e /dev/video9 ] && echo "twin webcam: Main PC camera (/dev/video9)" || echo "twin webcam: missing (sudo modprobe v4l2loopback)"
          pactl list short sources | grep -q main-pc-mic && echo "twin mic: Main PC microphone" || echo "twin mic: off"' ;;
      on)  systemctl --user start twin-camera.service
           ssh -o ConnectTimeout=3 -o BatchMode=yes "$HOST" "$MIC"'
             [ -f "$f.off" ] && mv "$f.off" "$f" && systemctl --user restart pipewire pipewire-pulse wireplumber; true'
           echo "mic and camera sharing ON" ;;
      off) systemctl --user stop twin-camera.service
           ssh -o ConnectTimeout=3 -o BatchMode=yes "$HOST" "$MIC"'
             [ -f "$f" ] && mv "$f" "$f.off" && systemctl --user restart pipewire pipewire-pulse wireplumber; true'
           echo "mic and camera sharing OFF until 'twin camera on'" ;;
      *) echo "usage: twin camera status|on|off"; exit 1 ;;
    esac
    ;;
```

In `twin-completion.bash`: add `camera` after `shelf` in `cmds`, and before `push)` add:

```bash
            camera) COMPREPLY=($(compgen -W "status on off" -- "$cur")) ;;
```

In `man/twin.1`, insert before `.SH TASK ROUTING`:

```
.SH CAMERA AND MICROPHONE
Apps on the twin get this PC's microphone ("Main PC microphone", the twin's default input)
and camera ("Main PC camera",
.IR /dev/video9 ).
The microphone follows this PC's default input and is only read while an app on the twin
records. The camera turns on here only while an app on the twin has the webcam open, and
off again 5 seconds after the last one closes it; meanwhile the twin's webcam shows a black
picture. The user service
.I twin-camera.service
runs
.BR twin-camera ,
which keeps an SSH link to
.B twin-camera \-\-watch
on the twin and streams the camera's MJPEG to
.B twin-camera \-\-feed
when asked. Picture size and rate:
.B TWIN_CAMERA_SIZE
(default 1280x720) and
.B TWIN_CAMERA_FPS
(default 30) in
.IR ~/.config/twinpc/config .
The twin's webcam is a v4l2loopback kernel module (installed once with sudo).
.TP
.BR "camera " [ status | on | off ]
.B status
shows whether the camera service is connected and streaming, and whether the twin's
webcam and microphone exist;
.B off
stops sharing both until
.BR "twin camera on" .
```

In `README.md`:
- In the ✨ Features table, fill the empty cell next to the "🧲 Drag files across" card (the `<td valign="top">` followed by an empty line and `</td>`) with:

```html
<td valign="top">

### 🎙️ Mic & camera
Apps on the twin use **this PC's microphone and webcam** — calls, OBS, Whisper, OpenCV.
The camera light comes on only while a twin app is using it.

</td>
```

- In the 📖 cheat sheet, after the `twin shelf status` row, add: `| \`twin camera status\` · \`twin camera off\` | this PC's mic and camera for apps on the twin |`
- In 🩺 Troubleshooting, after the "Nothing happens when I drop files on the edge" block, add:

```markdown
<details>
<summary><b>The twin's webcam shows only black</b></summary>

`twin camera status`. Black means this PC's camera isn't streaming: another app here may be using it
(you get a "busy" notification on the twin), or the service isn't connected. If `/dev/video9` is missing
on the twin, run `sudo modprobe v4l2loopback` there (after a kernel update, reboot the twin once).
</details>
```

- In 🗂️ Project layout, change the `clip/` line to: `clip/                                clipboard, drag-files-across, mic & camera — twin-clipd, twin-shelf, twin-camera, GNOME extension`
- In 🧪 Tests, add `tests.test_camera tests.test_camera_link tests.tool.test_camera` to the fast, local command.
- Update the tests badge and the layout's test count to the number of passing tests from the full-suite run in Step 4 (`Ran N` minus the skipped ones).

- [ ] **Step 4: Install on the real machines and see it work**

The module step needs sudo on the twin, and sudo needs a real terminal for the password, so **the user runs this in their own terminal**: `tool/twinpc install camera`
Expected: every step `done` or `already done`; `camera.twin.module` builds the DKMS module (this takes about a minute); `camera.main.link` ends by printing `camera sharing: connected to the twin`.

Then run: `twin camera status`
Expected: `camera sharing: connected to the twin`, `twin webcam: Main PC camera (/dev/video9)`, `twin mic: Main PC microphone`.

Run: `bash tests/test_camera.sh`
Expected: `PASS camera and mic (peak Y …, mic rms …)`.

Check by hand with the user:
1. Open a Meet test call (or https://webcamtests.com) in the twin's browser: the "Main PC camera" shows live video and this PC's camera light comes on; after closing the tab it goes off within about 10 s.
2. Speak: the call's mic meter moves with "Main PC microphone" selected.
3. With a call app open on this PC holding the camera, open the twin's webcam: the twin shows a "busy" notification and the black placeholder.

Run the whole suite (the list from README 🧪 plus `tests.test_twin_exec`) and `for t in tests/*.sh; do bash "$t"; done`
Expected: `OK` (3 docker probe tests skipped), and every shell check passes.

- [ ] **Step 5: Commit**

```bash
git add twin twin-completion.bash man/twin.1 README.md tests/test_camera.sh
git commit -m "feat: twin camera, manual and README for mic and camera sharing, and a live system check

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```
