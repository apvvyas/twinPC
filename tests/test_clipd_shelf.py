"""Shelf drops through the service and the agent linked by a local pipe; a stub rsync copies locally."""
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from tests.test_clipd import CLIPD, cd
from tests.test_clipd_link import WL_COPY, WL_PASTE

RSYNC = """#!/usr/bin/env python3
# stub rsync: copies local paths, reading "twin:/x" as "/x"; RSYNC_STUB_FAIL=1 makes it fail
import os, shutil, sys, time
if os.environ.get("RSYNC_STUB_FAIL"):
    print("rsync: connection unexpectedly closed (stub)")
    sys.exit(12)
args = [a.split(":", 1)[1] if a.startswith("twin:") else a for a in sys.argv[sys.argv.index("--") + 1:]]
*sources, dest = args
for i, src in enumerate(sources):
    target = os.path.join(dest, os.path.basename(src.rstrip("/")))
    if os.path.isdir(src):
        shutil.copytree(src, target, symlinks=True)
    else:
        shutil.copy2(src, target)
    sys.stdout.write(f"  {i + 1}  {int(100 * (i + 1) / len(sources))}%  1.00MB/s  0:00:01\\r")
    sys.stdout.flush()
    time.sleep(0.05)
print()
"""


class ShelfLinkTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = self.d = Path(self.tmp.name)
        self.state, self.stubs = d / "twin-clipboard", d / "bin"
        self.main_run, self.twin_run = d / "main-run", d / "twin-run"
        self.main_cache, self.twin_cache = d / "main-cache", d / "twin-cache"
        self.state.mkdir()
        self.stubs.mkdir()
        self.main_run.mkdir(mode=0o700)
        self.twin_run.mkdir(mode=0o700)
        for name, text in (("wl-copy", WL_COPY), ("wl-paste", WL_PASTE), ("notify-send", "#!/bin/sh\n"),
                           ("rsync", RSYNC)):
            (self.stubs / name).write_text(text)
            (self.stubs / name).chmod(0o755)
        self.main_sock = self.main_run / "twinpc" / "clip.sock"
        self.twin_sock = self.twin_run / "twinpc" / "shelf.sock"
        self.svc, self.clients = None, []

    def start(self, agent=None, **env):
        agent = agent or (f"env PATH={self.stubs}:{os.environ['PATH']} CLIP_STATE={self.state} WAYLAND_DISPLAY=stub"
                          f" XDG_RUNTIME_DIR={self.twin_run} XDG_CACHE_HOME={self.twin_cache}"
                          f" {sys.executable} {CLIPD} --agent")
        e = {**os.environ, "XDG_RUNTIME_DIR": str(self.main_run), "XDG_CACHE_HOME": str(self.main_cache),
             "PATH": f"{self.stubs}:{os.environ['PATH']}", "TWIN_CLIPD_RETRY": "0.5", **env}
        self.svc = subprocess.Popen([sys.executable, str(CLIPD), "--agent-cmd", agent], env=e,
                                    stderr=subprocess.DEVNULL)
        self.wait(self.main_sock.exists, "the service socket")

    def tearDown(self):
        for s in self.clients:
            s.close()
        if self.svc:
            self.svc.kill()
            self.svc.wait()
        subprocess.run(["pkill", "-f", str(self.stubs)])
        subprocess.run(["pkill", "-f", f"{CLIPD} --agent"])
        self.tmp.cleanup()

    def wait(self, cond, what, timeout=10):
        end = time.time() + timeout
        while time.time() < end:
            try:
                if cond():
                    return
            except OSError:
                pass
            time.sleep(0.1)
        self.fail(f"timed out waiting for {what}")

    def shelf_client(self, sock):
        s = socket.socket(socket.AF_UNIX)
        s.settimeout(10)
        s.connect(str(sock))
        s.sendall(cd.encode("hello", role="shelf"))
        self.clients.append(s)
        return s, s.makefile("rb")

    def wait_link_up(self, stream):
        while True:
            h, _ = cd.read_frame(stream)
            if h["kind"] == "link" and h.get("up"):
                return

    def frames_until(self, stream, state):
        """Read shelf frames until one has `state`; return every shelf frame read."""
        seen = []
        while True:
            h, _ = cd.read_frame(stream)
            if h["kind"] == "shelf":
                seen.append(h)
                if h.get("state") == state:
                    return seen

    def both_linked(self, **env):
        self.start(**env)
        main, main_in = self.shelf_client(self.main_sock)
        self.wait_link_up(main_in)
        self.wait(self.twin_sock.exists, "the agent's shelf socket")
        twin, twin_in = self.shelf_client(self.twin_sock)
        self.wait_link_up(twin_in)
        return main, main_in, twin, twin_in

    def probe(self, sock):
        s = socket.socket(socket.AF_UNIX)
        s.settimeout(5)
        s.connect(str(sock))
        s.sendall(cd.encode("hello", role="probe"))
        h, _ = cd.read_frame(s.makefile("rb"))
        s.close()
        return h

    def test_drop_here_arrives_on_the_twin_shelf(self):
        main, main_in, twin, twin_in = self.both_linked()
        self.assertTrue(self.probe(self.main_sock)["shelf"])
        self.assertTrue(self.probe(self.twin_sock)["shelf"])
        src = self.d / "src"
        (src / "album").mkdir(parents=True)
        (src / "a b.txt").write_text("one")
        (src / "album" / "p.png").write_bytes(b"png")
        main.sendall(cd.encode("drop", paths=[str(src / "a b.txt"), str(src / "album")]))
        seen = self.frames_until(twin_in, "ready")
        self.assertEqual(seen[0]["state"], "receiving")
        self.assertIn(50, [h.get("progress") for h in seen])
        ready = seen[-1]
        self.assertTrue(ready["dir"].startswith(str(self.twin_cache / "twinpc" / "shelf")))
        self.assertEqual([Path(p).name for p in ready["paths"]], ["a b.txt", "album"])
        self.assertEqual(Path(ready["paths"][0]).read_text(), "one")
        self.assertEqual((Path(ready["paths"][1]) / "p.png").read_bytes(), b"png")

    def test_drop_on_the_twin_arrives_on_this_pcs_shelf(self):
        main, main_in, twin, twin_in = self.both_linked()
        f = self.d / "from twin.txt"
        f.write_text("hi")
        twin.sendall(cd.encode("drop", paths=[str(f)]))
        ready = self.frames_until(main_in, "ready")[-1]
        self.assertTrue(ready["dir"].startswith(str(self.main_cache / "twinpc" / "shelf")))
        self.assertEqual(Path(ready["paths"][0]).read_text(), "hi")

    def test_a_failed_transfer_is_reported_and_cleaned_up(self):
        main, main_in, twin, twin_in = self.both_linked(RSYNC_STUB_FAIL="1")
        f = self.d / "x.txt"
        f.write_text("x")
        main.sendall(cd.encode("drop", paths=[str(f)]))
        failed = self.frames_until(twin_in, "failed")[-1]
        self.assertIn("connection unexpectedly closed", failed["error"])
        self.wait(lambda: not Path(failed["dir"]).exists(), "the partial folder to be removed")

    def test_drops_are_refused_while_the_twin_is_away(self):
        self.start(agent="false")
        main, main_in = self.shelf_client(self.main_sock)
        f = self.d / "x.txt"
        f.write_text("x")
        main.sendall(cd.encode("drop", paths=[str(f)]))
        failed = self.frames_until(main_in, "failed")[-1]
        self.assertEqual(failed["error"], "twin not connected")

    def test_a_drop_too_big_for_this_pc_is_refused(self):
        # a stand-in agent that says hello, then drops a file claiming to be enormous
        f = self.d / "huge.bin"
        f.write_bytes(b"x")
        fake = self.d / "fake_agent.py"
        fake.write_text(
            "import hashlib, json, sys, time\n"
            "def frame(**h):\n"
            "    h = {'v': 1, **h, 'size': 0, 'sha': hashlib.sha256(b'').hexdigest()}\n"
            "    sys.stdout.buffer.write(json.dumps(h).encode() + b'\\n'); sys.stdout.flush()\n"
            "frame(kind='hello'); time.sleep(0.5)\n"
            f"frame(kind='drop', paths=[{str(f)!r}], bytes=10**18)\n"
            "time.sleep(30)\n")
        self.start(agent=f"{sys.executable} {fake}")
        main, main_in = self.shelf_client(self.main_sock)
        failed = self.frames_until(main_in, "failed")[-1]
        self.assertIn("not enough space", failed["error"])

    def test_unusable_paths_on_the_twin_are_not_forwarded(self):
        main, main_in, twin, twin_in = self.both_linked()
        twin.sendall(cd.encode("drop", paths=["relative/x.txt"]))
        twin.sendall(cd.encode("drop", paths=[str(self.d / "missing.txt")]))
        main.settimeout(1.5)
        with self.assertRaises(OSError):
            cd.read_frame(main_in)


if __name__ == "__main__":
    unittest.main()
