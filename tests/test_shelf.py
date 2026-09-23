import importlib.machinery
import importlib.util
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SHELF = ROOT / "clip" / "twin-shelf"
_loader = importlib.machinery.SourceFileLoader("twin_shelf", str(SHELF))
_spec = importlib.util.spec_from_loader("twin_shelf", _loader)
sh = importlib.util.module_from_spec(_spec)
_loader.exec_module(sh)


class ModelTests(unittest.TestCase):
    def test_receiving_then_ready(self):
        with tempfile.TemporaryDirectory() as d:
            a, b = Path(d, "a.txt"), Path(d, "b.txt")
            a.write_text("a")
            m = sh.ShelfModel()
            self.assertEqual(m.apply({"kind": "shelf", "dir": d, "state": "receiving", "progress": 40}), "shelf")
            self.assertEqual(m.summary(), "receiving… 40 %")
            self.assertFalse(m.draggable())
            m.apply({"kind": "shelf", "dir": d, "state": "ready", "progress": 100, "paths": [str(a), str(b)]})
            self.assertEqual(m.items, [str(a)])                     # b never arrived
            self.assertEqual(m.summary(), "1 of 2 copied — drag into any folder")
            self.assertTrue(m.draggable())

    def test_failed_and_new_drop_resets(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d, "x")
            f.write_text("x")
            m = sh.ShelfModel()
            m.apply({"kind": "shelf", "dir": d, "state": "ready", "paths": [str(f)]})
            self.assertEqual(m.summary(), "1 item — drag into any folder")
            m.apply({"kind": "shelf", "dir": "/other", "state": "failed", "error": "twin not connected"})
            self.assertEqual((m.items, m.summary()), ([], "transfer failed — twin not connected"))

    def test_link_frames(self):
        m = sh.ShelfModel()
        self.assertEqual(m.apply({"kind": "link", "up": True}), "link")
        self.assertTrue(m.link)
        self.assertIsNone(m.apply({"kind": "hello"}))

    def test_uris(self):
        self.assertEqual(sh.uris(["/a b/c.txt"]), "file:///a%20b/c.txt\r\n")


class CliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.run_dir = Path(self.tmp.name)
        (self.run_dir / "twinpc").mkdir(mode=0o700)
        self.env = {**os.environ, "XDG_RUNTIME_DIR": str(self.run_dir)}

    def tearDown(self):
        self.tmp.cleanup()

    def fake_daemon(self, reply):
        """A one-shot twin-clipd stand-in; returns the list the received headers land in."""
        got = []
        server = socket.socket(socket.AF_UNIX)
        (self.run_dir / "twinpc" / "clip.sock").unlink(missing_ok=True)
        server.bind(str(self.run_dir / "twinpc" / "clip.sock"))
        server.listen()

        def serve():
            conn, _ = server.accept()
            got.append(sh.read_header(conn.makefile("rb")))
            if reply is not None:
                conn.sendall(sh.encode("hello", **reply))
            conn.close()
            server.close()
        threading.Thread(target=serve, daemon=True).start()
        return got

    def run_cli(self, *args):
        return subprocess.run([sys.executable, str(SHELF), *args], env=self.env, capture_output=True, text=True,
                              timeout=20)

    def test_selftest(self):
        self.assertEqual(self.run_cli("--selftest").returncode, 2)
        self.fake_daemon({"shelf": True})
        r = self.run_cli("--selftest")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("running", r.stdout)

    def test_selftest_fresh(self):
        self.fake_daemon({"shelf": True, "shelf_code": "old"})
        r = self.run_cli("--selftest", "--fresh")
        self.assertEqual(r.returncode, 3, r.stdout)
        self.assertIn("restart", r.stdout)
        self.fake_daemon({"shelf": True, "shelf_code": sh.code_hash()})
        self.assertEqual(self.run_cli("--selftest", "--fresh").returncode, 0)

    def test_selftest_without_the_app(self):
        self.fake_daemon({"shelf": False})
        self.assertEqual(self.run_cli("--selftest").returncode, 1)

    def test_drop_sends_absolute_paths(self):
        got = self.fake_daemon(None)
        f = Path(self.tmp.name, "a b.txt")
        f.write_text("x")
        self.assertEqual(self.run_cli("--drop", str(f)).returncode, 0)
        self.assertEqual(got[0]["kind"], "drop")
        self.assertEqual(got[0]["paths"], [str(f)])

    def test_write_edge(self):
        with mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": str(self.run_dir)}):
            sh.write_edge("right")
        self.assertEqual(json.loads((self.run_dir / "twinpc" / "shelf.json").read_text()), {"edge": "right"})


class GtkTests(unittest.TestCase):
    def test_the_ui_uses_file_drag_and_drop(self):
        src = SHELF.read_text()
        for needed in ("Gtk.DropTarget.new(Gdk.FileList", "Gtk.DragSource", "Gdk.FileList.new_from_list",
                       '"text/uri-list"', "Gtk4LayerShell", "STRIP_TITLE", "SHELF_TITLE"):
            self.assertIn(needed, src)

    def test_the_gtk_part_imports(self):
        r = subprocess.run([sys.executable, "-c", "import gi; gi.require_version('Gtk', '4.0');"
                            " gi.require_version('Gdk', '4.0'); from gi.repository import Gtk, Gdk;"
                            " print(Gdk.FileList.new_from_list)"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == "__main__":
    unittest.main()
