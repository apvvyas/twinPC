import importlib.machinery
import importlib.util
import io
import tarfile
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLIPD = ROOT / "clip" / "twin-clipd"
_loader = importlib.machinery.SourceFileLoader("twin_clipd", str(CLIPD))
_spec = importlib.util.spec_from_loader("twin_clipd", _loader)
cd = importlib.util.module_from_spec(_spec)
_loader.exec_module(cd)


class FrameTests(unittest.TestCase):
    def test_round_trip(self):
        h, body = cd.read_frame(io.BytesIO(cd.encode("text", b"hello", mime=cd.TEXT_MIME)))
        self.assertEqual((h["kind"], h["mime"], h["size"], body), ("text", cd.TEXT_MIME, 5, b"hello"))

    def test_control_frames_carry_fields(self):
        h, body = cd.read_frame(io.BytesIO(cd.encode("offer", mimes=["image/png"])))
        self.assertEqual((h["kind"], h["mimes"], body), ("offer", ["image/png"], b""))

    def test_clean_end_is_none(self):
        self.assertIsNone(cd.read_frame(io.BytesIO(b"")))

    def test_several_frames_in_a_row(self):
        s = io.BytesIO(cd.encode("text", b"a", mime=cd.TEXT_MIME) + cd.encode("hello"))
        self.assertEqual(cd.read_frame(s)[1], b"a")
        self.assertEqual(cd.read_frame(s)[0]["kind"], "hello")
        self.assertIsNone(cd.read_frame(s))

    def test_bad_frames_are_rejected(self):
        good = cd.encode("text", b"hello", mime=cd.TEXT_MIME)
        head = good.split(b"\n")[0]
        cases = {
            "truncated": good[:-2],
            "sha": good[:-5] + b"HELLO",
            "json": b"not json\n",
            "version": head.replace(b'"v": 1', b'"v": 2') + b"\nhello",
            "kind": cd.encode("bogus"),
            "over": b'{"v": 1, "kind": "text", "size": %d, "sha": ""}\n' % (cd.LIMITS["text"] + 1),
            "control-body": b'{"v": 1, "kind": "hello", "size": 3, "sha": ""}\nabc',
            "no-newline": b'{"v": 1',
        }
        for name, data in cases.items():
            with self.subTest(name), self.assertRaises(cd.FrameError):
                cd.read_frame(io.BytesIO(data))


class ChooseTests(unittest.TestCase):
    def test_preference(self):
        self.assertEqual(cd.choose(["text/plain", "image/png"]), ("image", "image/png"))
        self.assertEqual(cd.choose(["x-special/gnome-copied-files", "text/uri-list", "text/plain"]),
                         ("files", "text/uri-list"))
        self.assertEqual(cd.choose(["x-special/gnome-copied-files", "text/plain"]),
                         ("files", "x-special/gnome-copied-files"))
        self.assertEqual(cd.choose(["UTF8_STRING", "text/plain;charset=utf-8"]), ("text", "text/plain;charset=utf-8"))
        self.assertEqual(cd.choose(["STRING"]), ("text", "STRING"))

    def test_browser_links_are_text(self):
        self.assertEqual(cd.choose(["text/x-moz-url", "text/uri-list", "text/plain"]), ("text", "text/plain"))
        self.assertEqual(cd.choose(["text/html", "text/uri-list", "UTF8_STRING"]), ("text", "UTF8_STRING"))

    def test_nothing_useful_or_secret(self):
        self.assertIsNone(cd.choose(["application/x-foo"]))
        self.assertIsNone(cd.choose([]))
        self.assertIsNone(cd.choose(["text/plain", "x-kde-passwordManagerHint"]))


class EchoTests(unittest.TestCase):
    def test_same_content_is_an_echo(self):
        e = cd.Echo()
        self.assertTrue(e.is_new(b"a"))
        self.assertFalse(e.is_new(b"a"))
        e.mark(b"b")
        self.assertFalse(e.is_new(b"b"))
        self.assertTrue(e.is_new(b"a"))


if __name__ == "__main__":
    unittest.main()
