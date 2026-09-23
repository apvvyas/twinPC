"""The service and the agent linked through a local pipe: stub wl-copy/wl-paste stand in for the twin's
clipboard, and this test plays the GNOME extension on the service's socket."""
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from urllib.parse import unquote, urlparse

from tests.test_clipd import CLIPD, cd

WL_COPY = """#!/bin/sh
# stub: the twin's clipboard lives in $CLIP_STATE
t="text/plain;charset=utf-8"
[ "$1" = --type ] && t=$2
cat > "$CLIP_STATE/content.tmp" && printf '%s\\n' "$t" > "$CLIP_STATE/types" \\
  && mv "$CLIP_STATE/content.tmp" "$CLIP_STATE/content" && date +%s%N > "$CLIP_STATE/stamp"
"""
WL_PASTE = """#!/bin/sh
case "$1" in
  --list-types) cat "$CLIP_STATE/types" 2>/dev/null ;;
  --watch) last=; while :; do s=$(cat "$CLIP_STATE/stamp" 2>/dev/null)
           if [ "$s" != "$last" ]; then last=$s; echo changed; fi; sleep 0.1; done ;;
  *) cat "$CLIP_STATE/content" 2>/dev/null || { echo "Nothing is copied" >&2; exit 1; } ;;
esac
"""


class LinkTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        self.state, self.stubs, self.run_dir = d / "twin-clipboard", d / "bin", d / "run"
        self.main_cache, self.twin_cache = d / "main-cache", d / "twin-cache"
        self.state.mkdir()
        self.stubs.mkdir()
        self.run_dir.mkdir(mode=0o700)
        for name, text in (("wl-copy", WL_COPY), ("wl-paste", WL_PASTE), ("notify-send", "#!/bin/sh\n")):
            (self.stubs / name).write_text(text)
            (self.stubs / name).chmod(0o755)
        self.sock = self.run_dir / "twinpc" / "clip.sock"
        self.svc = None

    def agent_cmd(self, stubs=None):
        return (f"env PATH={stubs or self.stubs}:{os.environ['PATH']} CLIP_STATE={self.state} WAYLAND_DISPLAY=stub"
                f" XDG_CACHE_HOME={self.twin_cache} {sys.executable} {CLIPD} --agent")

    def start(self, agent=None):
        agent = agent or self.agent_cmd()
        env = {**os.environ, "XDG_RUNTIME_DIR": str(self.run_dir), "XDG_CACHE_HOME": str(self.main_cache),
               "PATH": f"{self.stubs}:{os.environ['PATH']}", "TWIN_CLIPD_RETRY": "0.5"}
        self.svc = subprocess.Popen([sys.executable, str(CLIPD), "--agent-cmd", agent], env=env,
                                    stderr=subprocess.DEVNULL)
        self.wait(self.sock.exists, "the service socket")
        self.ext = socket.socket(socket.AF_UNIX)
        self.ext.settimeout(5)
        self.ext.connect(str(self.sock))
        self.ext.sendall(cd.encode("hello", role="extension"))
        self.ext_in = self.ext.makefile("rb")
        self.wait(lambda: self.status().get("extension"), "the extension to register")

    def tearDown(self):
        if self.svc:
            self.ext.close()
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

    def status(self):
        s = socket.socket(socket.AF_UNIX)
        s.settimeout(5)
        s.connect(str(self.sock))
        s.sendall(cd.encode("hello", role="probe"))
        h, _ = cd.read_frame(s.makefile("rb"))
        s.close()
        return h

    def twin_copy(self, data, mime=None):
        argv = [str(self.stubs / "wl-copy")] + (["--type", mime] if mime else [])
        subprocess.run(argv, input=data, env={**os.environ, "CLIP_STATE": str(self.state)}, check=True)

    def twin_clipboard(self):
        f = self.state / "content"
        return f.read_bytes() if f.exists() else None

    def main_copy(self, mimes, store, wants=1):
        """Play GNOME's side of a copy: offer, then answer the service's `want`s."""
        self.ext.sendall(cd.encode("offer", mimes=mimes))
        for _ in range(wants):
            h, _ = cd.read_frame(self.ext_in)
            self.assertEqual(h["kind"], "want")
            self.ext.sendall(cd.encode("data", store[h["mime"]], mime=h["mime"], id=h.get("id")))

    def assert_quiet(self):
        """Nothing more arrives at the extension (use last: a timed-out socket file can't be read again)."""
        self.ext.settimeout(1.5)
        with self.assertRaises(OSError):
            cd.read_frame(self.ext_in)

    def test_text_main_to_twin_without_echo(self):
        self.start()
        self.wait(lambda: self.status()["twin"], "the agent to connect")
        self.main_copy(["text/plain;charset=utf-8"], {"text/plain;charset=utf-8": b"from main"})
        self.wait(lambda: self.twin_clipboard() == b"from main", "the twin's clipboard")
        self.assert_quiet()                    # the twin's watcher saw its own change: nothing comes back

    def test_text_twin_to_main_without_echo(self):
        self.start()
        self.wait(lambda: self.status()["twin"], "the agent to connect")
        self.twin_copy(b"from twin")
        h, body = cd.read_frame(self.ext_in)
        self.assertEqual((h["kind"], h["mime"], body), ("set", cd.TEXT_MIME, b"from twin"))
        stamp = (self.state / "stamp").read_text()
        self.main_copy([h["mime"]], {h["mime"]: body})   # GNOME reports the change the extension made
        time.sleep(1)
        self.assertEqual((self.state / "stamp").read_text(), stamp, "echoed back to the twin")

    def test_files_twin_to_main(self):
        self.start()
        self.wait(lambda: self.status()["twin"], "the agent to connect")
        src = Path(self.tmp.name) / "src"
        src.mkdir()
        (src / "note.txt").write_text("hello file")
        self.twin_copy((src / "note.txt").as_uri().encode() + b"\r\n", "text/uri-list")
        h, body = cd.read_frame(self.ext_in)
        self.assertEqual((h["kind"], h["mime"]), ("set", "text/uri-list"))
        [path] = [Path(unquote(urlparse(u).path)) for u in body.decode().split()]
        self.assertEqual(path.read_text(), "hello file")
        self.assertTrue(str(path).startswith(str(self.main_cache)))

    def test_image_main_to_twin(self):
        self.start()
        self.wait(lambda: self.status()["twin"], "the agent to connect")
        png = b"\x89PNG\r\n\x1a\n" + os.urandom(64)
        self.main_copy(["image/png", "text/html"], {"image/png": png})
        self.wait(lambda: self.twin_clipboard() == png, "the twin's clipboard")
        self.assertEqual((self.state / "types").read_text().strip(), "image/png")

    def test_existing_twin_clipboard_is_not_pushed_on_connect(self):
        self.twin_copy(b"old twin clipboard")
        self.start()
        self.wait(lambda: self.status()["twin"], "the agent to connect")
        self.assert_quiet()

    def test_copies_are_dropped_while_the_twin_is_away(self):
        self.start(agent="false")
        self.assertFalse(self.status()["twin"])
        self.ext.sendall(cd.encode("offer", mimes=["text/plain"]))
        self.assert_quiet()                    # no `want`: nothing is read, sent or queued

    def test_twin_counts_as_connected_only_when_the_agent_answers(self):
        self.start(agent=f"sh -c 'sleep 3; exec {self.agent_cmd()}'")
        self.assertFalse(self.status()["twin"], "connected before the agent answered")
        self.wait(lambda: self.status()["twin"], "the agent to connect")

    def test_agent_exits_when_its_clipboard_watcher_dies(self):
        dead = Path(self.tmp.name) / "dead-bin"
        dead.mkdir()
        (dead / "wl-paste").write_text(WL_PASTE.replace("--watch) last=;", "--watch) exit 1;"))
        (dead / "wl-paste").chmod(0o755)
        p = subprocess.Popen(["sh", "-c", self.agent_cmd(dead)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL)
        try:
            p.wait(timeout=5)
        except subprocess.TimeoutExpired:
            p.kill()
            self.fail("the agent kept running without a clipboard watcher")

    def test_link_survives_a_bad_frame_from_the_twin(self):
        starts = Path(self.tmp.name) / "starts"
        fake = Path(self.tmp.name) / "fake_agent.py"
        fake.write_text(
            "import json, sys, time\n"
            f"open({str(starts)!r}, 'a').write('x')\n"
            "def frame(**h):\n"
            "    sys.stdout.buffer.write(json.dumps({'v': 1, 'size': 0, 'sha': "
            "'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855', **h}).encode() + b'\\n')\n"
            "    sys.stdout.flush()\n"
            "frame(kind='hello')\nframe(kind='text')\ntime.sleep(30)\n")
        self.start(agent=f"{sys.executable} {fake}")
        self.wait(lambda: starts.exists() and len(starts.read_text()) >= 2, "the link to reconnect after a bad frame")

    def test_a_stale_answer_after_a_newer_copy_is_ignored(self):
        self.start()
        self.wait(lambda: self.status()["twin"], "the agent to connect")
        self.ext.sendall(cd.encode("offer", mimes=["image/png"]))
        want1, _ = cd.read_frame(self.ext_in)
        self.ext.sendall(cd.encode("offer", mimes=[cd.TEXT_MIME]))              # copied again meanwhile
        self.ext.sendall(cd.encode("data", b"\x89PNG old", mime="image/png", id=want1.get("id")))
        want2, _ = cd.read_frame(self.ext_in)
        self.assertEqual(want2["mime"], cd.TEXT_MIME)
        self.assertNotEqual(want2.get("id"), want1.get("id"))
        self.ext.sendall(cd.encode("data", b"newer text", mime=cd.TEXT_MIME, id=want2.get("id")))
        self.wait(lambda: self.twin_clipboard() == b"newer text", "the newer copy on the twin")

    def test_received_files_are_not_echoed(self):
        self.start()
        self.wait(lambda: self.status()["twin"], "the agent to connect")
        src = Path(self.tmp.name) / "src"
        src.mkdir()
        (src / "a.png").write_bytes(b"img")
        self.twin_copy((src / "a.png").as_uri().encode() + b"\r\n", "text/uri-list")
        h, body = cd.read_frame(self.ext_in)
        stamp = (self.state / "stamp").read_text()
        self.main_copy(["text/uri-list"], {"text/uri-list": body})     # GNOME reports the change it made
        time.sleep(1)
        self.assertEqual((self.state / "stamp").read_text(), stamp, "echoed back to the twin")

    def test_selftest(self):
        env = {**os.environ, "XDG_RUNTIME_DIR": str(self.run_dir)}
        r = subprocess.run([sys.executable, str(CLIPD), "--selftest"], env=env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)
        self.assertIn("not running", r.stdout)
        self.start()
        self.wait(lambda: self.status()["twin"], "the agent to connect")
        r = subprocess.run([sys.executable, str(CLIPD), "--selftest"], env=env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("twin connected", r.stdout)


if __name__ == "__main__":
    unittest.main()
