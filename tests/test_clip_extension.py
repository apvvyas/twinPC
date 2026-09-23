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


if __name__ == "__main__":
    unittest.main()
