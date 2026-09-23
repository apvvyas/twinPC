import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

EXT = Path(__file__).resolve().parents[1] / "clip" / "gnome-extension" / "twinpc-clipboard@twinpc"


class ExtensionTests(unittest.TestCase):
    def test_metadata(self):
        m = json.loads((EXT / "metadata.json").read_text())
        self.assertEqual(m["uuid"], "twinpc-clipboard@twinpc")
        self.assertIn("50", m["shell-version"])

    @unittest.skipUnless(shutil.which("node"), "needs node for a syntax check")
    def test_syntax(self):
        with tempfile.TemporaryDirectory() as d:
            copy = Path(d, "extension.mjs")
            copy.write_text((EXT / "extension.js").read_text())
            r = subprocess.run(["node", "--check", str(copy)], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)

    def test_speaks_the_services_frames(self):
        js = (EXT / "extension.js").read_text()
        for kind in ("hello", "offer", "data"):
            self.assertIn(f"kind: '{kind}'", js)
        for kind in ("want", "set"):
            self.assertIn(f"=== '{kind}'", js)
        self.assertIn("'twinpc', 'clip.sock'", js)
        self.assertIn("role: 'extension'", js)

    def test_writes_never_block_gnome_shell(self):
        js = (EXT / "extension.js").read_text()
        self.assertNotIn(".write_all(", js)          # a synchronous write stalls the whole desktop
        self.assertIn("write_bytes_async", js)

    def test_answers_carry_the_want_id(self):
        js = (EXT / "extension.js").read_text()
        self.assertIn("header.id", js)
        self.assertRegex(js, r"kind: 'data', mime, id")

    def test_extension_places_the_shelf_windows_by_their_titles(self):
        import importlib.machinery
        import importlib.util
        path = EXT.parents[1] / "twin-shelf"
        loader = importlib.machinery.SourceFileLoader("twin_shelf_titles", str(path))
        mod = importlib.util.module_from_spec(importlib.util.spec_from_loader("twin_shelf_titles", loader))
        loader.exec_module(mod)
        js = (EXT / "extension.js").read_text()
        self.assertIn(f"'{mod.STRIP_TITLE}'", js)
        self.assertIn(f"'{mod.SHELF_TITLE}'", js)
        for needed in ("window-created", "'shelf.json'", "move_frame", "make_above", "stick",
                       "get_monitor_geometry"):
            self.assertIn(needed, js)


if __name__ == "__main__":
    unittest.main()
