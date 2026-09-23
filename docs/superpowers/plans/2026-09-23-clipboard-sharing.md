# Clipboard Sharing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Copy on either PC and paste on the other — text, images and files — automatically, over the existing SSH link, installed and checked by `twinpc` as a new `clipboard` feature.

**Architecture:** `clip/twin-clipd` (one stdlib Python script) is both the main-PC user service (`twin-clip.service`: a unix socket for the GNOME extension, plus a long-lived `ssh twin twin-clipd --agent`) and the twin-side agent (`wl-paste --watch` / `wl-copy`). Both ends speak one framing format: a JSON header line, then the body. A small GNOME Shell extension watches GNOME's clipboard (GNOME has no data-control protocol, so `wl-paste --watch` cannot work there) and serves and sets content for the service. `twinpc` installs everything through the desktop adapters.

**Tech Stack:** Python 3.11+ stdlib (`json`, `hashlib`, `tarfile` with the `data` filter, `socket`, `subprocess`, `threading`, `unittest`), GJS ES modules for GNOME Shell 50, wl-clipboard, systemd user units, bash.

**Spec:** `docs/superpowers/specs/2026-09-23-clipboard-sharing-design.md`

## Global Constraints

- Share text, images (`image/png`) and files, both directions, automatically; files up to **500 MB** total, anything bigger is skipped with a `notify-send` pointing at `twin push`.
- Frame limits: text 10 MB, image 50 MB, files frame 600 MB (500 MB of content + tar overhead), extension `data`/`set` frames 50 MB; control frames (`hello`, `offer`, `want`) have an empty body.
- Frame = `{"v":1,"kind":…,"size":<int>,"sha":"<sha256 hex of body>", …}\n` + exactly `size` bytes. Anything else is a `FrameError`, and the connection carrying it is closed.
- Never send content whose offer includes `x-kde-passwordManagerHint`. The primary selection is not synced. No queue while the twin is away.
- Received files go to `${XDG_CACHE_HOME:-~/.cache}/twinpc/clipboard/<n>/`; the newest 5 folders are kept. An unsafe archive (`..`, absolute names, links pointing outside, devices) writes nothing.
- Traffic only over the existing SSH link; no new ports or firewall rules. Socket directory `$XDG_RUNTIME_DIR/twinpc` is mode `0700`.
- Python is stdlib only. GNOME extension uuid `twinpc-clipboard@twinpc`, shell-version `["50"]`.
- On the main PC always use `/usr/bin/gsettings` (the `GSETTINGS` helper in `adapters/desktop.py`).
- No personal data in the repository (public): no usernames, MACs, hostnames or addresses other than `10.42.0.1` / `10.42.0.11`.
- Every commit message ends with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Spec refinements made while planning

These are written here so the executor and the final reviewer weigh them against the spec:

1. **One mime per set:** Mutter's memory sources and `wl-copy` each offer a single type, so received files are set as `text/uri-list` only (the spec said `text/uri-list` + `x-special/gnome-copied-files`). GTK4 Nautilus reads `text/uri-list` as a file list; Task 6 checks a file paste in Nautilus on both PCs.
2. **Echo suppression lives in `twin-clipd` only:** it keys on the clipboard *content as read back* (for files, the uri-list), so the extension doesn't need to spot its own changes.
3. **Adapter methods are per role:** `Gnome.clipboard_main_steps(feature, repo)` and `Hyprland.clipboard_twin_steps(feature, repo)`, matching the existing role-specific methods (`shortcut_steps`, `gui_steps`). They replace the spec's single `clipboard_steps`.
4. **The twin gets a copy of the agent, not a symlink** (it has no checkout): `file_step` writes `~/.local/bin/twin-clipd` with mode 755.
5. **Log-out reminder:** a manual step, remembered for the current login through a marker in `$XDG_RUNTIME_DIR` (cleared at logout). Doctor can't show ⚠️ for a failing check, so `twin clip status` is where "extension not loaded" appears.
6. **Test layout:** tests for the script go in `tests/test_clipd.py` and `tests/test_clipd_link.py` (like `tests/test_twin_route.py`); the `twinpc` steps are tested in `tests/tool/test_clipboard.py`.

## Review Focus

1. The agent (re)connects while the twin already has something on its clipboard: the old content must **not** overwrite the main PC's clipboard — pinned by `test_existing_twin_clipboard_is_not_pushed_on_connect` (Task 3).
2. A copied web link (browsers also offer `text/uri-list`) must arrive as text, not fail as "files" — pinned by `test_browser_links_are_text` (Task 1) and `test_links_fall_back_to_text` (Task 2).
3. Copies made while the twin is off must be dropped quietly: no `want` round trip, no error, no queue — pinned by `test_copies_are_dropped_while_the_twin_is_away` (Task 3).
4. A crafted archive with `/abs` names must be rejected, not quietly stripped to a relative path by the `data` filter — pinned by `test_unsafe_archives_write_nothing` "absolute" (Task 2).
5. `wl-copy` on the twin must not hold the SSH stream open (seen live while designing) — started with stdout/stderr on `/dev/null` in its own session, and exercised by every twin-side set in `tests/test_clipd_link.py` (Task 3).

---

### Task 1: Frames, type choice and echo guard

**Files:**
- Create: `clip/twin-clipd`
- Test: `tests/test_clipd.py`

**Interfaces:**
- Produces (module `twin-clipd`, loaded in tests with `SourceFileLoader`):
  - `encode(kind: str, body: bytes = b"", **fields) -> bytes`
  - `read_frame(stream) -> tuple[dict, bytes] | None` (raises `FrameError`)
  - `choose(mimes: list[str]) -> tuple[str, str] | None` → `(kind, mime)`
  - `text_choice(mimes) -> tuple[str, str] | None`
  - `class Echo` with `mark(content: bytes)` and `is_new(content: bytes) -> bool`
  - constants `MB`, `LIMITS`, `FILES_LIMIT`, `CONTROL`, `CONTENT`, `TEXT_MIME`, `URI_LIST`, `GNOME_FILES`, `SECRET_HINT`; exceptions `FrameError`, `TooBig`

- [ ] **Step 1: Write the failing tests**

`tests/test_clipd.py`:

```python
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
```

- [ ] **Step 2: Run the tests to see them fail**

Run: `python3 -m unittest tests.test_clipd`
Expected: ERROR — `FileNotFoundError` for `clip/twin-clipd`.

- [ ] **Step 3: Write the implementation**

`clip/twin-clipd` (then `chmod +x clip/twin-clipd`):

```python
#!/usr/bin/env python3
"""twin-clipd — share the clipboard between this PC and the twin (man twin: CLIPBOARD).

Main PC (default): a user service. The GNOME extension talks to it on a unix socket, and it keeps
`ssh <twin> twin-clipd --agent` running; clipboard changes travel both ways as frames.
Twin (--agent): watches the clipboard with `wl-paste --watch` and sets it with `wl-copy`.

A frame is one JSON header line — {"v":1,"kind":…,"size":n,"sha":"<sha256 of body>",…} — then n bytes.
"""
import hashlib
import json
import threading

MB = 1024 * 1024
LIMITS = {"text": 10 * MB, "image": 50 * MB, "files": 600 * MB, "data": 50 * MB, "set": 50 * MB}
FILES_LIMIT = 500 * MB                  # the copied files themselves; the files frame allows tar overhead
CONTROL = {"hello", "offer", "want"}    # no body
CONTENT = ("text", "image", "files")    # what travels between the PCs
TEXT_MIMES = ["text/plain;charset=utf-8", "UTF8_STRING", "text/plain", "STRING", "TEXT"]
TEXT_MIME = TEXT_MIMES[0]
URI_LIST = "text/uri-list"
GNOME_FILES = "x-special/gnome-copied-files"
BROWSER_HINTS = ("text/x-moz-url", "text/html")    # a copied link also offers text/uri-list
SECRET_HINT = "x-kde-passwordManagerHint"


class FrameError(Exception):
    pass


class TooBig(Exception):
    """args[0]: the size in bytes."""


def encode(kind, body=b"", **fields):
    header = {"v": 1, "kind": kind, **fields, "size": len(body), "sha": hashlib.sha256(body).hexdigest()}
    return json.dumps(header).encode() + b"\n" + body


def read_frame(stream):
    """The next (header, body) from a binary stream; None at a clean end of the stream."""
    line = stream.readline(65536)
    if not line:
        return None
    if not line.endswith(b"\n"):
        raise FrameError("header line too long or cut off")
    try:
        h = json.loads(line)
    except ValueError:
        raise FrameError("header is not JSON") from None
    if not isinstance(h, dict) or h.get("v") != 1:
        raise FrameError(f"unknown frame version: {line[:80]!r}")
    kind, size = h.get("kind"), h.get("size", 0)
    if not isinstance(size, int) or size < 0:
        raise FrameError("bad size")
    limit = 0 if kind in CONTROL else LIMITS.get(kind)
    if limit is None:
        raise FrameError(f"unknown frame kind {kind!r}")
    if size > limit:
        raise FrameError(f"{kind} frame of {size} bytes is over its {limit}-byte limit")
    body = bytearray()
    while len(body) < size:
        chunk = stream.read(size - len(body))
        if not chunk:
            raise FrameError("the stream ended inside a frame")
        body += chunk
    body = bytes(body)
    if hashlib.sha256(body).hexdigest() != h.get("sha"):
        raise FrameError("the body does not match its sha")
    return h, body


def text_choice(mimes):
    for m in TEXT_MIMES:
        if m in mimes:
            return "text", m
    return None


def choose(mimes):
    """(kind, mime) to read for a clipboard offering `mimes`; None: nothing useful, or a secret."""
    if SECRET_HINT in mimes:
        return None
    link = any(m in mimes for m in BROWSER_HINTS)
    if GNOME_FILES in mimes or (URI_LIST in mimes and not link):
        return "files", URI_LIST if URI_LIST in mimes else GNOME_FILES
    if "image/png" in mimes:
        return "image", "image/png"
    return text_choice(mimes)


class Echo:
    """The last clipboard content this side applied or sent, so a change it caused isn't sent back."""

    def __init__(self):
        self._key, self._lock = None, threading.Lock()

    def mark(self, content):
        with self._lock:
            self._key = hashlib.sha256(content).hexdigest()

    def is_new(self, content):
        """True (and remember it) if `content` differs from the last one; False for an echo."""
        key = hashlib.sha256(content).hexdigest()
        with self._lock:
            if key == self._key:
                return False
            self._key = key
            return True
```

- [ ] **Step 4: Run the tests to see them pass**

Run: `python3 -m unittest tests.test_clipd`
Expected: `OK` (9 tests).

- [ ] **Step 5: Commit**

```bash
git add clip/twin-clipd tests/test_clipd.py
git commit -m "feat(clip): frame protocol, type choice and echo guard

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Files and building a frame from a clipboard

**Files:**
- Modify: `clip/twin-clipd` (append after `class Echo`; extend the imports)
- Test: `tests/test_clipd.py` (append classes before `if __name__`)

**Interfaces:**
- Consumes: Task 1's `encode`, `choose`, `text_choice`, `Echo`, `LIMITS`, `FILES_LIMIT`, `TEXT_MIME`, `TooBig`.
- Produces:
  - `file_paths(data: bytes) -> list[Path] | None`
  - `total_size(paths) -> int`
  - `pack(paths) -> bytes` (raises `TooBig`)
  - `unpack(body: bytes, root: Path) -> list[Path]` (raises `tarfile.TarError`)
  - `prune(root: Path, keep: int = 5)`
  - `uri_list(paths) -> bytes`
  - `pick(mimes, get) -> tuple[str, bytes, list[Path] | None] | None`
  - `build_frame(mimes, get, echo) -> bytes | None` (raises `TooBig`)
  - `incoming(h: dict, body: bytes, cache: Path) -> tuple[str, bytes]` → `(mime, content)`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_clipd.py`:

```python
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
        self.assertEqual(cd.incoming({"kind": "text", "mime": cd.TEXT_MIME}, b"hi", self.d), (cd.TEXT_MIME, b"hi"))
        mime, content = cd.incoming({"kind": "files"}, tar_of(member("n.txt", b"n")), self.d / "c")
        self.assertEqual(mime, "text/uri-list")
        self.assertEqual(content, cd.uri_list([self.d / "c" / "1" / "n.txt"]))


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

    def test_image(self):
        h, body = self.frame(["image/png", "text/html"], {"image/png": b"\x89PNG"})
        self.assertEqual((h["kind"], h["mime"], body), ("image", "image/png", b"\x89PNG"))

    def test_empty_and_too_big(self):
        self.assertIsNone(self.frame(["text/plain"], {"text/plain": b""}))
        with self.assertRaises(cd.TooBig):
            self.frame(["text/plain"], {"text/plain": b"x" * (cd.LIMITS["text"] + 1)})
```

- [ ] **Step 2: Run the tests to see them fail**

Run: `python3 -m unittest tests.test_clipd`
Expected: ERROR/FAIL in `FilesTests` and `BuildFrameTests` — `AttributeError: module 'twin_clipd' has no attribute 'file_paths'` (and similar).

- [ ] **Step 3: Write the implementation**

Change the imports at the top of `clip/twin-clipd` to:

```python
import hashlib
import io
import json
import os
import shutil
import tarfile
import threading
from pathlib import Path
from urllib.parse import unquote, urlparse
```

Add `KEEP = 5` after `SECRET_HINT`, then append after `class Echo`:

```python
def file_paths(data):
    """Local paths in a text/uri-list (or gnome-copied-files) body; None unless every entry is a local file."""
    paths = []
    for line in data.decode("utf-8", "replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line in ("copy", "cut"):
            continue
        u = urlparse(line)
        if u.scheme != "file" or u.netloc not in ("", "localhost"):
            return None
        p = Path(unquote(u.path))
        if not p.exists():
            return None
        paths.append(p)
    return paths or None


def total_size(paths):
    total = 0
    for p in paths:
        if p.is_dir() and not p.is_symlink():
            for root, _dirs, files in os.walk(p):
                total += sum(os.lstat(os.path.join(root, f)).st_size for f in files)
        else:
            total += p.lstat().st_size
    return total


def pack(paths):
    total = total_size(paths)
    if total > FILES_LIMIT:
        raise TooBig(total)
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for p in paths:
            tar.add(str(p), arcname=p.name)
    return buf.getvalue()


def prune(root, keep=KEEP):
    folders = sorted((d for d in root.iterdir() if d.name.isdigit()), key=lambda d: int(d.name))
    for d in folders[:-keep]:
        shutil.rmtree(d, ignore_errors=True)


def unpack(body, root):
    """Unpack a files frame into a new numbered folder under `root`; return its top-level paths.
    Every member is checked first, so an unsafe archive writes nothing."""
    root.mkdir(parents=True, exist_ok=True)
    dest = root / str(max((int(d.name) for d in root.iterdir() if d.name.isdigit()), default=0) + 1)
    with tarfile.open(fileobj=io.BytesIO(body), mode="r:") as tar:
        members = tar.getmembers()
        for m in members:
            # the data filter would quietly strip a leading "/": refuse such archives outright
            if m.name.startswith("/") or ".." in Path(m.name).parts:
                raise tarfile.TarError(f"unsafe name in the received files: {m.name!r}")
            tarfile.data_filter(m, str(dest))          # links outside, devices, … raise FilterError
        dest.mkdir()
        tar.extractall(dest, members=members, filter="data")
    prune(root)
    tops = []
    for m in members:
        top = Path(m.name).parts[0]
        if top not in tops:
            tops.append(top)
    return [dest / t for t in tops]


def uri_list(paths):
    return "".join(p.resolve().as_uri() + "\r\n" for p in paths).encode()


def pick(mimes, get):
    """(kind, content, paths) for a clipboard offering `mimes`, reading types with `get(mime)`.
    None when there is nothing to send: nothing useful, a secret, or empty content."""
    choice = choose(mimes)
    if choice is None:
        return None
    kind, mime = choice
    content, paths = get(mime), None
    if kind == "files":
        paths = file_paths(content)
        if paths is None:                              # web links, not local files: send them as text
            choice = text_choice(mimes)
            if choice is None:
                return None
            kind, mime = choice
            content = get(mime)
    if not content:
        return None
    return kind, content, paths


def build_frame(mimes, get, echo):
    """The frame to send for a clipboard offering `mimes`, or None (nothing new).
    Raises TooBig when the content is over its limit."""
    picked = pick(mimes, get)
    if picked is None:
        return None
    kind, content, paths = picked
    if not echo.is_new(content):
        return None
    if kind == "files":
        return encode("files", pack(paths), mime="application/x-tar")
    if len(content) > LIMITS[kind]:
        raise TooBig(len(content))
    return encode(kind, content, mime=TEXT_MIME if kind == "text" else "image/png")


def incoming(h, body, cache):
    """What to put on the clipboard for a received content frame: (mime, content)."""
    if h["kind"] == "files":
        return URI_LIST, uri_list(unpack(body, cache))
    return h["mime"], body
```

- [ ] **Step 4: Run the tests to see them pass**

Run: `python3 -m unittest tests.test_clipd`
Expected: `OK` (21 tests).

- [ ] **Step 5: Commit**

```bash
git add clip/twin-clipd tests/test_clipd.py
git commit -m "feat(clip): files — pack with a size limit, checked unpack, pruning; build frames

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: The service, the agent and the link between them

**Files:**
- Modify: `clip/twin-clipd` (append the runtime and `main`; extend the imports)
- Test: `tests/test_clipd_link.py`

**Interfaces:**
- Consumes: everything from Tasks 1–2.
- Produces:
  - CLI: `twin-clipd [--host H]` runs the service; `twin-clipd --agent`; `twin-clipd --selftest` (exit 0 = service running and twin connected; 1 = running, twin not connected; 2 = service not running); a hidden `--agent-cmd "<command>"` replaces the ssh command (for tests).
  - Socket at `$XDG_RUNTIME_DIR/twinpc/clip.sock`. Extension protocol:
    - extension → service: `hello` (`role: "extension"`) and `offer` (`mimes`);
    - service → extension: `want` (`mime`); extension → service: `data` (`mime`; body, or no body and `over: <bytes>` when over 50 MB);
    - service → extension: `set` (`mime`, body).
    - A `hello` from any other client is answered with `hello` carrying `twin`, `extension` (bools) and `last` (str).

- [ ] **Step 1: Write the failing tests**

`tests/test_clipd_link.py`:

```python
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

    def start(self, agent=None):
        agent = agent or (f"env PATH={self.stubs}:{os.environ['PATH']} CLIP_STATE={self.state} WAYLAND_DISPLAY=stub"
                          f" XDG_CACHE_HOME={self.twin_cache} {sys.executable} {CLIPD} --agent")
        env = {**os.environ, "XDG_RUNTIME_DIR": str(self.run_dir), "XDG_CACHE_HOME": str(self.main_cache),
               "PATH": f"{self.stubs}:{os.environ['PATH']}"}
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
            self.ext.sendall(cd.encode("data", store[h["mime"]], mime=h["mime"]))

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
```

- [ ] **Step 2: Run the tests to see them fail**

Run: `python3 -m unittest tests.test_clipd_link`
Expected: FAIL — `timed out waiting for the service socket` (the script has no `main` yet).

- [ ] **Step 3: Write the implementation**

Extend the imports of `clip/twin-clipd` to:

```python
import argparse
import hashlib
import io
import json
import os
import shlex
import shutil
import socket
import subprocess
import sys
import tarfile
import threading
import time
from pathlib import Path
from urllib.parse import unquote, urlparse
```

Add `RETRY = 10` after `KEEP = 5`, then append at the end of the file:

```python
def log(msg):
    print(f"twin-clipd: {msg}", file=sys.stderr, flush=True)


def notify(msg):
    try:
        subprocess.run(["notify-send", "-a", "twinPC", "twinPC clipboard", msg], timeout=5,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        pass


def too_big(e):
    notify(f"not shared: {e.args[0] // MB} MB is over the limit — use twin push")


def wayland_env():
    """Reach the logged-in desktop from an ssh session: runtime dir, Wayland display, session bus."""
    env = os.environ
    run = env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    if "WAYLAND_DISPLAY" not in env:
        socks = sorted(p.name for p in Path(run).glob("wayland-*") if not p.name.endswith(".lock"))
        if socks:
            env["WAYLAND_DISPLAY"] = socks[0]
    env.setdefault("DBUS_SESSION_BUS_ADDRESS", f"unix:path={run}/bus")


def wl_types():
    r = subprocess.run(["wl-paste", "--list-types"], capture_output=True)
    return r.stdout.decode(errors="replace").splitlines() if r.returncode == 0 else []


def wl_get(mime):
    return subprocess.run(["wl-paste", "--no-newline", "--type", mime], capture_output=True, check=True).stdout


def wl_set(mime, content):
    """Set the clipboard. wl-copy stays behind to serve it, so it must not hold our stdout (the ssh stream)."""
    argv = ["wl-copy"] if mime.startswith("text/plain") else ["wl-copy", "--type", mime]
    p = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
    p.communicate(content)


def agent(inp, out, cache):
    """The twin side: frames from the main PC on `inp` go onto the clipboard; clipboard changes go to `out`."""
    wayland_env()
    echo, write = Echo(), threading.Lock()

    def send(frame):
        with write:
            out.write(frame)
            out.flush()

    try:                                          # what is on the clipboard already isn't a new copy
        picked = pick(wl_types(), wl_get)
        if picked:
            echo.mark(picked[1])
    except (subprocess.CalledProcessError, OSError):
        pass
    watcher = subprocess.Popen(["wl-paste", "--watch", "echo", "changed"], stdout=subprocess.PIPE,
                               stderr=subprocess.DEVNULL)

    def watch():
        for _ in watcher.stdout:
            try:
                frame = build_frame(wl_types(), wl_get, echo)
            except TooBig as e:
                too_big(e)
                continue
            except (subprocess.CalledProcessError, OSError):
                continue
            if frame:
                try:
                    send(frame)
                except OSError:
                    return

    threading.Thread(target=watch, daemon=True).start()
    try:
        while (frame := read_frame(inp)) is not None:
            h, body = frame
            if h["kind"] == "hello":
                send(encode("hello"))
            elif h["kind"] in CONTENT:
                try:
                    mime, content = incoming(h, body, cache)
                except (tarfile.TarError, OSError) as e:
                    notify(f"files from the main PC were not accepted: {e}")
                    continue
                echo.mark(content)
                wl_set(mime, content)
        return 0
    except FrameError as e:
        log(f"from the main PC: {e}")
        return 1
    finally:
        watcher.terminate()


class Newer(Exception):
    """A newer offer arrived while the previous one was being read. args[0]: its mimes."""


class Service:
    """The main PC side: the GNOME extension on a unix socket, the twin's agent over ssh."""

    def __init__(self, host, sock_path, cache, agent_cmd=None):
        self.sock_path, self.cache = sock_path, cache
        self.agent_cmd = agent_cmd or ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
                                       "-o", "ServerAliveInterval=15", "--", host, ".local/bin/twin-clipd --agent"]
        self.echo = Echo()
        self.state = threading.Lock()                  # guards twin, ext, last
        self.twin_write, self.ext_write = threading.Lock(), threading.Lock()
        self.twin = self.ext = None
        self.last = "nothing yet"

    def serve(self):
        self.sock_path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(self.sock_path.parent, 0o700)
        self.sock_path.unlink(missing_ok=True)
        server = socket.socket(socket.AF_UNIX)
        server.bind(str(self.sock_path))
        server.listen()
        threading.Thread(target=self.twin_loop, daemon=True).start()
        while True:
            conn, _ = server.accept()
            threading.Thread(target=self.client, args=(conn,), daemon=True).start()

    def note(self, what):
        with self.state:
            self.last = f"{time.strftime('%H:%M:%S')} {what}"

    def twin_loop(self):
        while True:
            p = subprocess.Popen(self.agent_cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE)
            with self.state:
                self.twin = p.stdin
            try:
                while (frame := read_frame(p.stdout)) is not None:
                    self.from_twin(*frame)
            except FrameError as e:
                log(f"from the twin: {e}")
            finally:
                with self.state:
                    self.twin = None
                p.kill()
                p.wait()
            time.sleep(RETRY)

    def from_twin(self, h, body):
        if h["kind"] not in CONTENT:
            return
        try:
            mime, content = incoming(h, body, self.cache)
        except (tarfile.TarError, OSError) as e:
            notify(f"files from the twin were not accepted: {e}")
            return
        self.echo.mark(content)
        self.note(f"twin → this PC ({h['kind']})")
        with self.state:
            ext = self.ext
        if ext is None:
            wl_set(mime, content)          # extension not loaded yet: a one-shot wl-copy works on GNOME too
            return
        try:
            with self.ext_write:
                ext.sendall(encode("set", content, mime=mime))
        except OSError as e:
            log(f"to the extension: {e}")

    def client(self, conn):
        stream = conn.makefile("rb")
        try:
            while (frame := read_frame(stream)) is not None:
                h = frame[0]
                if h["kind"] == "hello" and h.get("role") == "extension":
                    with self.state:
                        self.ext = conn
                elif h["kind"] == "hello":
                    with self.state:
                        status = {"twin": self.twin is not None, "extension": self.ext is not None,
                                  "last": self.last}
                    conn.sendall(encode("hello", **status))
                elif h["kind"] == "offer":
                    mimes = h.get("mimes", [])
                    while mimes is not None:
                        mimes = self.from_main(conn, stream, mimes)
        except (FrameError, OSError) as e:
            log(f"from the extension: {e}")
        finally:
            with self.state:
                if self.ext is conn:
                    self.ext = None
            conn.close()

    def from_main(self, conn, stream, mimes):
        """Send the main PC's new clipboard to the twin. Returns the mimes of a newer offer that arrived
        meanwhile (handle it next), else None."""
        with self.state:
            twin = self.twin
        if twin is None:
            return None                                # twin away: dropped, not queued

        def get(mime):
            with self.ext_write:
                conn.sendall(encode("want", mime=mime))
            frame = read_frame(stream)
            if frame is None:
                raise OSError("the extension went away")
            h, body = frame
            if h["kind"] == "offer":
                raise Newer(h.get("mimes", []))
            if h["kind"] != "data":
                raise FrameError(f"expected data, got {h['kind']!r}")
            if h.get("over"):
                raise TooBig(h["over"])
            return body

        try:
            frame = build_frame(mimes, get, self.echo)
        except Newer as newer:
            return newer.args[0]
        except TooBig as e:
            too_big(e)
            return None
        if frame is None:
            return None
        try:
            with self.twin_write:
                twin.write(frame)
                twin.flush()
        except OSError as e:
            log(f"to the twin: {e}")
            return None
        self.note("this PC → twin")
        return None


def selftest(sock_path):
    try:
        s = socket.socket(socket.AF_UNIX)
        s.settimeout(5)
        s.connect(str(sock_path))
        s.sendall(encode("hello", role="probe"))
        h, _ = read_frame(s.makefile("rb"))
    except (OSError, FrameError, TypeError):
        print("clipboard sharing: not running (twin clip on)")
        return 2
    print(f"clipboard sharing: running · twin {'connected' if h.get('twin') else 'not connected'}"
          f" · GNOME extension {'connected' if h.get('extension') else 'not loaded (log out and back in)'}"
          f" · last: {h.get('last')}")
    return 0 if h.get("twin") else 1


def main(argv=None):
    ap = argparse.ArgumentParser(prog="twin-clipd", description="share the clipboard with the twin PC")
    ap.add_argument("--agent", action="store_true", help="the twin side: frames on stdin/stdout")
    ap.add_argument("--selftest", action="store_true", help="is the service running and the twin connected?")
    ap.add_argument("--host", default=os.environ.get("TWIN_HOST") or "twin", help="ssh alias of the twin")
    ap.add_argument("--agent-cmd", help=argparse.SUPPRESS)      # tests: run the agent without ssh
    a = ap.parse_args(argv)
    run = Path(os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}")
    sock = run / "twinpc" / "clip.sock"
    cache = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "twinpc" / "clipboard"
    if a.agent:
        return agent(sys.stdin.buffer, sys.stdout.buffer, cache)
    if a.selftest:
        return selftest(sock)
    Service(a.host, sock, cache, shlex.split(a.agent_cmd) if a.agent_cmd else None).serve()
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run the tests to see them pass**

Run: `python3 -m unittest tests.test_clipd tests.test_clipd_link`
Expected: `OK` (28 tests). If a link test times out, read the service's stderr (temporarily drop `stderr=subprocess.DEVNULL`) before changing code.

- [ ] **Step 5: Commit**

```bash
git add clip/twin-clipd tests/test_clipd_link.py
git commit -m "feat(clip): clipboard service and twin agent over one ssh link

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: The GNOME Shell extension

**Files:**
- Create: `clip/gnome-extension/twinpc-clipboard@twinpc/metadata.json`
- Create: `clip/gnome-extension/twinpc-clipboard@twinpc/extension.js`
- Test: `tests/test_clip_extension.py`

**Interfaces:**
- Consumes: Task 3's socket path and extension protocol (`hello` role extension, `offer`, `want`, `data` with optional `over`, `set`).
- Produces: the extension directory that Task 5 installs.

- [ ] **Step 1: Write the failing tests**

`tests/test_clip_extension.py`:

```python
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
```

- [ ] **Step 2: Run the tests to see them fail**

Run: `python3 -m unittest tests.test_clip_extension`
Expected: ERROR — `FileNotFoundError` for `metadata.json` / `extension.js`.

- [ ] **Step 3: Write the extension**

`clip/gnome-extension/twinpc-clipboard@twinpc/metadata.json`:

```json
{
  "uuid": "twinpc-clipboard@twinpc",
  "name": "twinPC clipboard",
  "description": "Shares the clipboard with the twin PC through twin-clipd (part of twinPC).",
  "shell-version": ["50"]
}
```

`clip/gnome-extension/twinpc-clipboard@twinpc/extension.js`:

```js
// twinPC clipboard: tells twin-clipd (a user service) when GNOME's clipboard changes, hands it the
// content it asks for, and puts content from the twin on the clipboard. GNOME has no data-control
// protocol, so this is the only way to watch the clipboard. Frame format: see clip/twin-clipd.
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Meta from 'gi://Meta';
import St from 'gi://St';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';

const CLIPBOARD = Meta.SelectionType.SELECTION_CLIPBOARD;
const DATA_LIMIT = 50 * 1024 * 1024;
const RETRY_SECONDS = 5;
const encoder = new TextEncoder();
const decoder = new TextDecoder();

function frame(header, bytes = new Uint8Array(0)) {
    const sha = GLib.compute_checksum_for_data(GLib.ChecksumType.SHA256, bytes);
    const head = encoder.encode(`${JSON.stringify({v: 1, ...header, size: bytes.length, sha})}\n`);
    const out = new Uint8Array(head.length + bytes.length);
    out.set(head);
    out.set(bytes, head.length);
    return out;
}

export default class TwinClipboard extends Extension {
    enable() {
        this._cancel = new Gio.Cancellable();
        this._selection = global.display.get_selection();
        this._ownerId = this._selection.connect('owner-changed', (_selection, type) => {
            if (type === CLIPBOARD)
                this._offer();
        });
        this._connect();
    }

    disable() {
        this._selection.disconnect(this._ownerId);
        this._cancel.cancel();
        if (this._retryId)
            GLib.source_remove(this._retryId);
        this._conn?.close(null);
        this._conn = this._in = this._out = this._selection = this._retryId = null;
    }

    _connect() {
        const path = GLib.build_filenamev([GLib.get_user_runtime_dir(), 'twinpc', 'clip.sock']);
        new Gio.SocketClient().connect_async(new Gio.UnixSocketAddress({path}), this._cancel, (client, res) => {
            try {
                this._conn = client.connect_finish(res);
            } catch (e) {
                this._retry();
                return;
            }
            this._in = new Gio.DataInputStream({base_stream: this._conn.get_input_stream()});
            this._out = this._conn.get_output_stream();
            this._send({kind: 'hello', role: 'extension'});
            this._readFrame();
        });
    }

    _retry() {
        if (this._cancel.is_cancelled())
            return;
        this._conn = this._in = this._out = null;
        this._retryId = GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, RETRY_SECONDS, () => {
            this._retryId = null;
            this._connect();
            return GLib.SOURCE_REMOVE;
        });
    }

    _drop() {
        this._conn?.close(null);
        this._retry();
    }

    _send(header, bytes) {
        if (!this._out)
            return;
        try {
            this._out.write_all(frame(header, bytes), null);
        } catch (e) {
            this._drop();
        }
    }

    _offer() {
        this._send({kind: 'offer', mimes: this._selection.get_mimetypes(CLIPBOARD)});
    }

    _readFrame() {
        this._in.read_line_async(GLib.PRIORITY_DEFAULT, this._cancel, (stream, res) => {
            let header;
            try {
                const [line] = stream.read_line_finish_utf8(res);
                if (line === null)
                    throw new Error('the service went away');
                header = JSON.parse(line);
            } catch (e) {
                if (!this._cancel.is_cancelled())
                    this._drop();
                return;
            }
            this._readBody(header, header.size || 0, [], 0);
        });
    }

    _readBody(header, size, chunks, got) {
        if (got >= size) {
            const body = new Uint8Array(size);
            let at = 0;
            for (const chunk of chunks) {
                body.set(chunk, at);
                at += chunk.length;
            }
            this._handle(header, body);
            this._readFrame();
            return;
        }
        this._in.read_bytes_async(Math.min(size - got, 1 << 20), GLib.PRIORITY_DEFAULT, this._cancel, (stream, res) => {
            let data;
            try {
                data = stream.read_bytes_finish(res).toArray();
                if (data.length === 0)
                    throw new Error('the service went away');
            } catch (e) {
                if (!this._cancel.is_cancelled())
                    this._drop();
                return;
            }
            chunks.push(data);
            this._readBody(header, size, chunks, got + data.length);
        });
    }

    _handle(header, body) {
        if (header.kind === 'want')
            this._transfer(header.mime);
        else if (header.kind === 'set')
            this._set(header.mime, body);
    }

    _transfer(mime) {
        const out = Gio.MemoryOutputStream.new_resizable();
        this._selection.transfer_async(CLIPBOARD, mime, -1, out, this._cancel, (selection, res) => {
            try {
                selection.transfer_finish(res);
                out.close(null);
                const bytes = out.steal_as_bytes().toArray();
                if (bytes.length > DATA_LIMIT)
                    this._send({kind: 'data', mime, over: bytes.length});
                else
                    this._send({kind: 'data', mime}, bytes);
            } catch (e) {
                this._send({kind: 'data', mime});        // the clipboard changed meanwhile: an empty answer
            }
        });
    }

    _set(mime, body) {
        const clipboard = St.Clipboard.get_default();
        if (mime.startsWith('text/plain'))
            clipboard.set_text(St.ClipboardType.CLIPBOARD, decoder.decode(body));
        else
            clipboard.set_content(St.ClipboardType.CLIPBOARD, mime, new GLib.Bytes(body));
    }
}
```

- [ ] **Step 4: Run the tests to see them pass**

Run: `python3 -m unittest tests.test_clip_extension`
Expected: `OK` (3 tests).

- [ ] **Step 5: Commit**

```bash
git add clip/gnome-extension tests/test_clip_extension.py
git commit -m "feat(clip): GNOME Shell extension that watches and sets the clipboard for twin-clipd

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: The `clipboard` feature in `twinpc`

**Files:**
- Create: `main/twin-clip.service`
- Modify: `tool/packages.toml` (add `wl-clipboard`)
- Modify: `tool/twinpc_lib/adapters/desktop.py` (shared `_APPEND` snippet; `Gnome.clipboard_main_steps`; `Hyprland.clipboard_twin_steps`)
- Modify: `tool/twinpc_lib/features.py` (`_clipboard`, registered after `kvm`)
- Modify: `tests/tool/test_features.py` (`test_key_steps_exist` gains `clipboard.main.link`)
- Test: `tests/tool/test_clipboard.py`

**Interfaces:**
- Consumes: `twin-clipd --selftest` (Task 3); the extension directory (Task 4); from `steps.py`: `cmd_step`, `file_step`, `manual_step`, `unit_step`, `unsupported_step`; from `features.py`: `_desktop_for`, `_pk`, `_read`, `repo_unit`, `values()["host"|"repo"]`.
- Produces: feature `clipboard`, with step ids in this order:
  1. `clipboard.main.packages`, `clipboard.twin.packages`
  2. `clipboard.main.command`, `clipboard.twin.agent`
  3. `clipboard.main.unit`, `clipboard.main.service`
  4. `clipboard.main.extension-code`, `clipboard.main.extension-metadata`, `clipboard.main.extension-enabled`, `clipboard.main.relogin`
  5. `clipboard.main.link`

- [ ] **Step 1: Write the failing tests**

`tests/tool/test_clipboard.py`:

```python
import unittest
from pathlib import Path

from tests.tool.fakes import FakeRunner
from tests.tool.test_features import PROFILE
from twinpc_lib import features as F, steps as S

REPO = Path(__file__).resolve().parents[2]


def clip_steps(prof):
    return [s for s in F.build_plan(prof, REPO) if s.feature == "clipboard"]


def applied(step, machine):
    r = FakeRunner([(machine, "", 0, "")])
    step.apply(S.Ctx({}, r, REPO))
    return r.calls[-1]


class ClipboardPlanTests(unittest.TestCase):
    def test_gnome_main_and_hyprland_twin(self):
        self.assertEqual([s.id for s in clip_steps(PROFILE)], [
            "clipboard.main.packages", "clipboard.twin.packages", "clipboard.main.command", "clipboard.twin.agent",
            "clipboard.main.unit", "clipboard.main.service", "clipboard.main.extension-code",
            "clipboard.main.extension-metadata", "clipboard.main.extension-enabled", "clipboard.main.relogin",
            "clipboard.main.link"])

    def test_comes_after_kvm(self):
        self.assertEqual(F.FEATURES.index("clipboard"), F.FEATURES.index("kvm") + 1)

    def test_unit_uses_the_profile_host(self):
        prof = {**PROFILE, "network": {**PROFILE["network"], "twin_host": "gpu"}}
        [unit] = [s for s in clip_steps(prof) if s.id == "clipboard.main.unit"]
        self.assertIn("ExecStart=%h/.local/bin/twin-clipd --host gpu\n", applied(unit, "main")[3])

    def test_twin_gets_a_copy_of_the_agent(self):
        [agent] = [s for s in clip_steps(PROFILE) if s.id == "clipboard.twin.agent"]
        machine, cmd, _root, data = applied(agent, "twin")
        self.assertEqual((machine, data), ("twin", (REPO / "clip" / "twin-clipd").read_text()))
        self.assertIn("chmod 755", cmd)

    def test_extension_files_and_enabling(self):
        steps = {s.id: s for s in clip_steps(PROFILE)}
        code = applied(steps["clipboard.main.extension-code"], "main")
        self.assertIn("gnome-shell/extensions/twinpc-clipboard@twinpc/extension.js", code[1])
        self.assertIn("owner-changed", code[3])
        enable = applied(steps["clipboard.main.extension-enabled"], "main")[1]
        self.assertIn("/usr/bin/gsettings", enable)
        self.assertIn("enabled-extensions", enable)

    def test_relogin_is_remembered_for_this_login(self):
        [st] = [s for s in clip_steps(PROFILE) if s.id == "clipboard.main.relogin"]
        self.assertIsNone(st.apply)
        self.assertIn("relogin-noted", st.after_confirm)
        r = FakeRunner()
        st.check(S.Ctx({}, r, REPO))
        self.assertIn("State: ACTIVE", r.calls[-1][1])
        self.assertIn("$XDG_RUNTIME_DIR/twinpc/relogin-noted", r.calls[-1][1])

    def test_unsupported_desktops(self):
        for machine, desktop, reason in [
                ("main", "kde", "desktop 'kde' is not supported yet (planned)"),
                ("main", "hyprland", "desktop 'hyprland' on the main PC is not supported yet (planned)"),
                ("twin", "gnome", "desktop 'gnome' on the twin is not supported yet (planned)")]:
            prof = {**PROFILE, machine: {**PROFILE[machine], "desktop": desktop}}
            with self.subTest(machine=machine, desktop=desktop):
                self.assertEqual([s.skip_reason for s in clip_steps(prof)], [reason])


if __name__ == "__main__":
    unittest.main()
```

In `tests/tool/test_features.py`, `test_key_steps_exist`, add `"clipboard.main.link",` after `"kvm.twin.config",`.

- [ ] **Step 2: Run the tests to see them fail**

Run: `python3 -m unittest tests.tool.test_clipboard tests.tool.test_features`
Expected: FAIL — no steps with feature `clipboard` (`[] != [...]`), `ValueError: 'clipboard' is not in list`, and `clipboard.main.link` missing.

- [ ] **Step 3: Write the implementation**

`main/twin-clip.service`:

```ini
[Unit]
Description=twinPC clipboard sharing with the twin PC (twin-clipd)
PartOf=graphical-session.target
After=graphical-session.target

[Service]
ExecStart=%h/.local/bin/twin-clipd --host twin
Restart=on-failure
RestartSec=5

[Install]
WantedBy=graphical-session.target
```

`tool/packages.toml` — add after the `grim` line:

```toml
wl-clipboard = { apt = "wl-clipboard", pacman = "wl-clipboard", dnf = "wl-clipboard" }
```

`tool/twinpc_lib/adapters/desktop.py`:
- Change the imports to:

```python
import shlex
from pathlib import Path

from ..steps import cmd_step, file_step, manual_step
from .packages import pkg_step
```

- Add after `KEYS = …`:

```python
# python snippet: append argv[2] to the GVariant string list in argv[1] (a gsettings value), print it
_APPEND = ("import ast,sys; l=ast.literal_eval(sys.argv[1].replace('@as ','')); "
           "l.append(sys.argv[2]) if sys.argv[2] not in l else None; print(l)")
CLIP_EXTENSION = "twinpc-clipboard@twinpc"
```

- In `Gnome.shortcut_steps`, delete the local `add = (...)` assignment and use `_APPEND` where it used `add`: `new=$(python3 -c {shlex.quote(_APPEND)} \"$list\" {shlex.quote(path)})`.
- Add to `class Gnome`:

```python
    def clipboard_main_steps(self, feature, repo):
        src = Path(repo) / "clip" / "gnome-extension" / CLIP_EXTENSION
        where = f"$HOME/.local/share/gnome-shell/extensions/{CLIP_EXTENSION}"
        return [
            file_step(f"{feature}.main.extension-code", feature, "main", f"{where}/extension.js",
                      (src / "extension.js").read_text()),
            file_step(f"{feature}.main.extension-metadata", feature, "main", f"{where}/metadata.json",
                      (src / "metadata.json").read_text()),
            cmd_step(f"{feature}.main.extension-enabled", feature, "main", "turn on the twinPC clipboard extension",
                     check=f"{GSETTINGS}; $G get org.gnome.shell enabled-extensions | grep -qF {CLIP_EXTENSION}",
                     apply=f"{GSETTINGS}; list=$($G get org.gnome.shell enabled-extensions)\n"
                           f"$G set org.gnome.shell enabled-extensions"
                           f" \"$(python3 -c {shlex.quote(_APPEND)} \"$list\" {CLIP_EXTENSION})\""),
            # GNOME on Wayland loads new extensions only at login; the marker lives until logout
            manual_step(f"{feature}.main.relogin", feature, "main", "load the clipboard extension",
                        "GNOME loads new extensions at login: log out and back in once "
                        "(finishing this install first is fine).",
                        check=f"gnome-extensions info {CLIP_EXTENSION} 2>/dev/null | grep -q 'State: ACTIVE'"
                              ' || [ -f "$XDG_RUNTIME_DIR/twinpc/relogin-noted" ]',
                        after_confirm='mkdir -p "$XDG_RUNTIME_DIR/twinpc"'
                                      ' && touch "$XDG_RUNTIME_DIR/twinpc/relogin-noted"'),
        ]
```

- Add to `class Hyprland`:

```python
    def clipboard_twin_steps(self, feature, repo):
        return []          # Hyprland has data-control: `wl-paste --watch` works, the agent needs nothing more
```

`tool/twinpc_lib/features.py` — add after `_kvm`:

```python
def _clipboard(profile, repo, v):
    md = _desktop_for(profile, "main", "clipboard_main_steps")
    if isinstance(md, str):
        return [unsupported_step("clipboard", "main", md)]
    td = _desktop_for(profile, "twin", "clipboard_twin_steps")
    if isinstance(td, str):
        return [unsupported_step("clipboard", "twin", td)]
    r = v["repo"]
    return [
        pkg_step("clipboard", "main", _pk(profile, "main"), ["wl-clipboard"]),
        pkg_step("clipboard", "twin", _pk(profile, "twin"), ["wl-clipboard"]),
        cmd_step("clipboard.main.command", "clipboard", "main", "install twin-clipd on this PC",
                 check=f'[ "$(readlink ~/.local/bin/twin-clipd)" = "{r}/clip/twin-clipd" ]',
                 apply=f'mkdir -p ~/.local/bin && ln -sf "{r}/clip/twin-clipd" ~/.local/bin/twin-clipd'),
        file_step("clipboard.twin.agent", "clipboard", "twin", "$HOME/.local/bin/twin-clipd",
                  _read(repo, "clip/twin-clipd"), mode="755", describe="install twin-clipd on the twin"),
        file_step("clipboard.main.unit", "clipboard", "main", "$HOME/.config/systemd/user/twin-clip.service",
                  repo_unit(repo, "main/twin-clip.service").replace(" --host twin\n", f" --host {v['host']}\n")),
        unit_step("clipboard.main.service", "clipboard", "main", "twin-clip.service"),
        *md.clipboard_main_steps("clipboard", repo),
        *td.clipboard_twin_steps("clipboard", repo),
        cmd_step("clipboard.main.link", "clipboard", "main", "connect the clipboard service to the twin",
                 check="~/.local/bin/twin-clipd --selftest >/dev/null",
                 apply="systemctl --user restart twin-clip.service && sleep 3 && ~/.local/bin/twin-clipd --selftest"),
    ]
```

and register it after `kvm` in `BUILDERS`:

```python
BUILDERS = {"connection": _connection, "cli": _cli, "gpu-stack": _gpu_stack, "routing": _routing,
            "mount": _mount, "power": _power, "unlock": _unlock, "kvm": _kvm, "clipboard": _clipboard,
            "audio": _audio, "desktop": _desktop, "gui": _gui, "nic-fix": _nic_fix}
```

- [ ] **Step 4: Run the tests to see them pass**

Run: `python3 -m unittest tests.tool.test_clipboard tests.tool.test_features tests.tool.test_adapters tests.tool.test_review_fixes tests.tool.test_cli`
Expected: `OK`.

- [ ] **Step 5: Commit**

```bash
git add main/twin-clip.service tool/packages.toml tool/twinpc_lib/adapters/desktop.py tool/twinpc_lib/features.py tests/tool/test_clipboard.py tests/tool/test_features.py
git commit -m "feat(tool): clipboard feature — service, agent, GNOME extension, doctor link check

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: `twin clip`, docs, the system check, and acceptance on the real machines

**Files:**
- Modify: `twin` (usage line + `clip)` subcommand)
- Modify: `twin-completion.bash`
- Modify: `man/twin.1` (new `.SH CLIPBOARD` after SOUND)
- Modify: `README.md` (feature text, cheat sheet row, troubleshooting, layout)
- Create: `tests/test_clipboard.sh`

**Interfaces:**
- Consumes: `twin-clip.service` and `twin-clipd --selftest` (Tasks 3 and 5).

- [ ] **Step 1: Write the failing system check**

`tests/test_clipboard.sh` (then `chmod +x tests/test_clipboard.sh`):

```bash
#!/usr/bin/env bash
# A token copied on each PC can be pasted on the other (needs both PCs up and the GNOME extension loaded).
set -u
TWENV='export XDG_RUNTIME_DIR=/run/user/$(id -u); export WAYLAND_DISPLAY=${WAYLAND_DISPLAY:-$(ls $XDG_RUNTIME_DIR | grep -m1 "^wayland-[0-9]*$")}'

tok=main-$RANDOM$RANDOM
printf %s "$tok" | wl-copy
got=
for _ in $(seq 20); do
  got=$(ssh twin "$TWENV; wl-paste -n 2>/dev/null")
  [[ $got == "$tok" ]] && break; sleep 0.5
done
[[ $got == "$tok" ]] || { echo "FAIL this PC → twin: the twin has '${got:0:40}'"; exit 1; }

tok=twin-$RANDOM$RANDOM
ssh twin "$TWENV; printf %s $tok | wl-copy >/dev/null 2>&1"
for _ in $(seq 20); do
  got=$(wl-paste -n 2>/dev/null)
  [[ $got == "$tok" ]] && break; sleep 0.5
done
[[ $got == "$tok" ]] || { echo "FAIL twin → this PC: this PC has '${got:0:40}'"; exit 1; }
echo "PASS clipboard both ways"
```

- [ ] **Step 2: Run it to see it fail**

Run: `bash tests/test_clipboard.sh`
Expected: `FAIL this PC → twin: …` (nothing is installed yet).

- [ ] **Step 3: Add `twin clip`, completion, manual and README**

In `twin`, add to the usage text after the `twin audio` line:

```
  twin clip [status|on|off]    clipboard sharing with twin (text, images, files)
```

and add a case branch before `  route)`:

```bash
  clip)
    # clipboard sharing: twin-clip.service here + `twin-clipd --agent` on twin over ssh (man twin: CLIPBOARD)
    case ${1:-status} in
      status) "$HOME/.local/bin/twin-clipd" --selftest || true ;;
      on)  systemctl --user start twin-clip.service; echo "clipboard sharing ON" ;;
      off) systemctl --user stop twin-clip.service
           echo "clipboard sharing OFF until 'twin clip on' or your next login" ;;
      *) echo "usage: twin clip status|on|off"; exit 1 ;;
    esac
    ;;
```

In `twin-completion.bash`, add `clip` after `audio` in `cmds`, and add this case before `push)`:

```bash
            clip) COMPREPLY=($(compgen -W "status on off" -- "$cur")) ;;
```

In `man/twin.1`, insert before `.SH TASK ROUTING`:

```
.SH CLIPBOARD
Copy on either PC and paste on the other: text, images (PNG) and files. Files up to
500 MB in total go across when you copy them and land in
.I ~/.cache/twinpc/clipboard/
on the receiving PC (the newest 5 copies are kept); bigger selections show a
notification \(em use
.B twin push
for those. Anything a password manager marks as secret is never sent.
The user service
.I twin-clip.service
runs
.BR twin-clipd ,
which keeps one SSH connection to
.B twin-clipd \-\-agent
on the twin; on GNOME a small extension
.RI ( twinpc-clipboard@twinpc )
watches the clipboard, because GNOME has no clipboard protocol for other programs.
A new extension loads at login: log out and back in once after installing.
.TP
.BR "clip " [ status | on | off ]
.B status
shows whether the service runs, the twin and the extension are connected, and the
last copy that went across;
.B off
stops sharing until
.B twin clip on
or your next login.
```

In `README.md`:
- In the "One desk, two computers" cell, after the Super+F12 sentence, add: `**Copy on one PC, paste on the other** — text, images and files.`
- In the cheat sheet, after the `twin audio test` row, add: `| \`twin clip status\` · \`twin clip off\` | clipboard sharing between the PCs |`
- In Troubleshooting, after the "No sound from the twin" block, add:

```markdown
<details>
<summary><b>Copy/paste doesn't cross over</b></summary>

`twin clip status`. On GNOME, log out and back in once after installing (that's when the
clipboard extension loads). Files over 500 MB don't go across — use `twin push`.
</details>
```

- In "Project layout", add the line `clip/                                clipboard sharing — twin-clipd service/agent and the GNOME extension`.

- [ ] **Step 4: Install on the real machines, log in again, and see the system check pass**

Run: `tool/twinpc install clipboard`
Expected:
- every step is `✓ … done` or `already done`; `clipboard.main.relogin` asks you to confirm — answer yes;
- `clipboard.main.link` ends by printing `clipboard sharing: running · twin connected · GNOME extension not loaded (log out and back in) …`.

Then the **user logs out and back in** on the main PC (the executor asks for it and waits).

Run: `twin clip status`
Expected: `clipboard sharing: running · twin connected · GNOME extension connected · last: nothing yet`

Run: `bash tests/test_clipboard.sh`
Expected: `PASS clipboard both ways`

Ask the user to check by hand:
1. Copy a screenshot on each PC and paste it into an image-aware app on the other.
2. Copy a file in Nautilus on each PC and paste it in Nautilus on the other.

Expected: both arrive. If the file paste fails on one side, that disproves spec refinement 1 — record what that side's Nautilus offers (`wl-paste --list-types` after copying a file there) and stop for a ruling.

Run the whole suite:
`python3 -m unittest tests.test_twin_route tests.test_hook tests.test_twin_exec tests.test_clipd tests.test_clipd_link tests.test_clip_extension tests.tool.test_probe tests.tool.test_profile tests.tool.test_steps tests.tool.test_adapters tests.tool.test_features tests.tool.test_cli tests.tool.test_review_fixes tests.tool.test_clipboard`
Expected: `OK` (3 docker probe tests skipped as before).

Run: `tool/twinpc doctor --no-root`
Expected: a `✅ clipboard   working` line; no feature that was ✅ before has become ❌.

- [ ] **Step 5: Commit**

```bash
git add twin twin-completion.bash man/twin.1 README.md tests/test_clipboard.sh
git commit -m "feat: twin clip, clipboard manual/README, and a both-ways system check

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```
