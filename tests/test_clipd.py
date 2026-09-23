import importlib.machinery
import importlib.util
import io
import os
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


def member(name, data=None, type=tarfile.REGTYPE, linkname=""):
    info = tarfile.TarInfo(name)
    info.type, info.linkname, info.mode = type, linkname, 0o644
    return info, (data if type == tarfile.REGTYPE else None)


def tar_of(*members):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as t:
        for info, data in members:
            if data is not None:
                info.size = len(data)
            t.addfile(info, io.BytesIO(data) if data is not None else None)
    return buf.getvalue()


class FilesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_file_paths(self):
        f = self.d / "a b.txt"
        f.write_text("x")
        uris = f.as_uri().encode() + b"\r\n"
        self.assertEqual(cd.file_paths(uris), [f])
        self.assertEqual(cd.file_paths(b"copy\n" + uris), [f])            # x-special/gnome-copied-files
        self.assertIsNone(cd.file_paths(b"https://example.com/\n"))
        self.assertIsNone(cd.file_paths((self.d / "missing").as_uri().encode()))
        self.assertIsNone(cd.file_paths(b""))

    def test_pack_unpack_round_trip(self):
        src = self.d / "src"
        (src / "dir").mkdir(parents=True)
        (src / "one.txt").write_text("1")
        (src / "dir" / "two.txt").write_text("2")
        tops = cd.unpack(cd.pack([src / "one.txt", src / "dir"]), self.d / "cache")
        self.assertEqual([p.name for p in tops], ["one.txt", "dir"])
        self.assertEqual((tops[1] / "two.txt").read_text(), "2")
        self.assertEqual(tops[0].parent, self.d / "cache" / "1")

    def test_pack_refuses_over_the_limit(self):
        f = self.d / "big"
        f.write_bytes(b"x" * 100)
        old, cd.FILES_LIMIT = cd.FILES_LIMIT, 50
        try:
            with self.assertRaises(cd.TooBig):
                cd.pack([f])
        finally:
            cd.FILES_LIMIT = old

    def test_unsafe_archives_write_nothing(self):
        cases = {
            "dotdot": tar_of(member("ok.txt", b"ok"), member("../evil.txt", b"x")),
            "absolute": tar_of(member("ok.txt", b"ok"), member("/tmp/evil.txt", b"x")),
            "link-out": tar_of(member("ok.txt", b"ok"), member("a", type=tarfile.SYMTYPE, linkname="/"),
                               member("a/evil.txt", b"x")),
            "device": tar_of(member("ok.txt", b"ok"), member("dev", type=tarfile.CHRTYPE)),
        }
        for name, body in cases.items():
            with self.subTest(name):
                cache = self.d / name
                with self.assertRaises(tarfile.TarError):
                    cd.unpack(body, cache)
                self.assertEqual(list(cache.glob("*/*")), [])

    def test_only_the_newest_five_are_kept(self):
        cache = self.d / "cache"
        for i in range(7):
            cd.unpack(tar_of(member(f"f{i}.txt", b"x")), cache)
        self.assertEqual(sorted(int(p.name) for p in cache.iterdir()), [3, 4, 5, 6, 7])

    def test_uri_list(self):
        p = self.d / "a b"
        p.write_text("")
        self.assertEqual(cd.uri_list([p]), (p.resolve().as_uri() + "\r\n").encode())

    def test_incoming(self):
        self.assertEqual(cd.incoming({"kind": "text", "mime": cd.TEXT_MIME}, b"hi", self.d),
                         (cd.TEXT_MIME, b"hi", b"hi"))
        mime, content, key = cd.incoming({"kind": "files"}, tar_of(member("n.txt", b"n")), self.d / "c")
        self.assertEqual(mime, "text/uri-list")
        self.assertEqual(content, cd.uri_list([self.d / "c" / "1" / "n.txt"]))
        # the clipboard's re-offer of what was just set must look like a repeat
        self.assertEqual(key, cd.echo_key(content, cd.file_paths(content)))


class BuildFrameTests(unittest.TestCase):
    def frame(self, mimes, store, echo=None):
        f = cd.build_frame(mimes, lambda m: store[m], echo or cd.Echo())
        return cd.read_frame(io.BytesIO(f)) if f else None

    def test_text_is_sent_once(self):
        e, store = cd.Echo(), {"text/plain": b"hi"}
        h, body = self.frame(["text/plain"], store, e)
        self.assertEqual((h["kind"], h["mime"], body), ("text", cd.TEXT_MIME, b"hi"))
        self.assertIsNone(self.frame(["text/plain"], store, e))

    def test_links_fall_back_to_text(self):
        store = {"text/uri-list": b"https://example.com/\r\n", "text/plain": b"https://example.com/"}
        h, body = self.frame(["text/uri-list", "text/plain"], store)
        self.assertEqual((h["kind"], body), ("text", b"https://example.com/"))

    def test_files(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d, "x.txt")
            f.write_text("x")
            h, body = self.frame(["text/uri-list"], {"text/uri-list": f.as_uri().encode()})
            self.assertEqual(h["kind"], "files")
            self.assertEqual(tarfile.open(fileobj=io.BytesIO(body)).getnames(), ["x.txt"])

    def test_same_file_name_with_new_content_is_sent_again(self):
        # browsers save every copied image to the same temp name (e.g. "viewPhoto")
        with tempfile.TemporaryDirectory() as d:
            f = Path(d, "viewPhoto")
            f.write_bytes(b"first image")
            e, store = cd.Echo(), {"text/uri-list": f.as_uri().encode()}
            self.assertIsNotNone(self.frame(["text/uri-list"], store, e))
            f.write_bytes(b"second, different image")
            os.utime(f, ns=(f.stat().st_atime_ns, f.stat().st_mtime_ns + 10**9))
            self.assertIsNotNone(self.frame(["text/uri-list"], store, e), "treated as a repeat")

    def test_image(self):
        h, body = self.frame(["image/png", "text/html"], {"image/png": b"\x89PNG"})
        self.assertEqual((h["kind"], h["mime"], body), ("image", "image/png", b"\x89PNG"))

    def test_empty_and_too_big(self):
        self.assertIsNone(self.frame(["text/plain"], {"text/plain": b""}))
        with self.assertRaises(cd.TooBig):
            self.frame(["text/plain"], {"text/plain": b"x" * (cd.LIMITS["text"] + 1)})


if __name__ == "__main__":
    unittest.main()
