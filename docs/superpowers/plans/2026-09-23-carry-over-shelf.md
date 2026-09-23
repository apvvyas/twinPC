# Carry-over Shelf Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Drop files on a strip at the screen edge that faces the other PC; they are copied there, and a shelf pops up at the same edge so they can be dragged into any folder.

**Architecture:**
- `clip/twin-shelf` is a Python/GTK4 app, the same file on both PCs. It shows the drop strip and the shelf, and talks to its local `twin-clipd` over a unix socket:
  - on the main PC, the service's `clip.sock`;
  - on the twin, a new `shelf.sock` opened by the agent.
- `twin-clipd` carries control frames (`drop`, `slot`/`slot-ready`, `shelf`, `link`) over the existing SSH link, and runs `rsync` over SSH for the data. `rsync` always runs on the main PC: it pushes for main → twin and pulls for twin → main.
- Window placement: `gtk4-layer-shell` on the twin; the existing GNOME extension on the main PC.
- A new `twinpc` feature, `shelf`, installs it.

**Tech Stack:** Python 3 stdlib + PyGObject (GTK 4, Gtk4LayerShell), rsync, GJS (GNOME Shell 50), systemd user units, bash.

**Spec:** `docs/superpowers/specs/2026-09-23-carry-over-shelf-design.md`

## Global Constraints

- Only ever copy: sources are never modified or deleted.
- File data moves by `rsync` over SSH; `rsync` always runs on the main PC. The twin gets no key for the main PC, and no new ports are opened.
- Every path is its own argv element: `rsync -a --protect-args --info=progress2 -e "ssh -o BatchMode=yes" -- …`. No shell is involved.
- Paths in `drop` frames must be absolute and non-empty, with no NUL or newline, and not `/` itself. Trailing slashes are stripped.
- Received files go to `${XDG_CACHE_HOME:-~/.cache}/twinpc/shelf/<n>/`, keeping the newest 5. A failed transfer deletes its folder.
- A free-space check runs before copying: total size + 64 MB must fit.
- `rsync` exit codes 0, 23 and 24 count as success (23/24 means some files were skipped). The shelf shows "N of M copied".
- Sockets live in `$XDG_RUNTIME_DIR/twinpc` (mode 0700): the main PC uses `clip.sock`, the twin `shelf.sock`.
- Window titles: `twinPC drop strip` and `twinPC shelf`. The edge comes from the profile's `twin_side` (default `left`): that side on the main PC, the opposite side on the twin.
- The strip is 6 px thick along the middle third of the edge. The shelf hides 120 s after the last update.
- Python is stdlib + PyGObject only. Use `/usr/bin/gsettings` on the main PC. The public repo contains no personal data.
- Every new feature updates README.md: its own card, a cheat-sheet row, a troubleshooting entry, the layout and the test counts.
- Every commit message ends with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. **Dropping on the strip while the twin is off:** a visible "twin not connected" result, no hang, and nothing queued. Pinned by `test_drops_are_refused_while_the_twin_is_away` (Task 2).
2. **A failed or interrupted `rsync`:** the receiver's partial folder is removed and the shelf shows the error. Pinned by `test_a_failed_transfer_is_reported_and_cleaned_up` (Task 2).
3. **A path from the twin that is relative or doesn't exist:** never pulled. Pinned by `test_unusable_paths_on_the_twin_are_not_forwarded` (Task 2) and `test_clean_paths` (Task 1).
4. **File names with spaces, quotes, a leading `-` or unicode:** copied intact. Pinned by `test_rsync_argv_keeps_every_path_one_argument` (Task 1), with the space name also going through the link in `test_drop_here_arrives_on_the_twin_shelf` (Task 2).
5. **The strip and shelf titles in `twin-shelf` must match what the GNOME extension looks for,** or the windows are never placed. Pinned by `test_extension_places_the_shelf_windows_by_their_titles` (Task 4).

---

### Task 1: Shelf primitives in twin-clipd

**Files:**
- Modify: `clip/twin-clipd` (imports, `CONTROL`, new functions after `incoming`)
- Test: `tests/test_clipd.py` (new class `ShelfCoreTests`, `import sys`)

**Interfaces:**
- Produces:
  - `clean_paths(paths) -> list[str] | None`
  - `new_slot(root: Path) -> Path`
  - `discard(folder, root: Path) -> None`
  - `enough_space(size: int, free: int) -> bool`
  - `rsync_push(host, paths, dest) -> list[str]`
  - `rsync_pull(host, paths, dest) -> list[str]`
  - `progress(line: str) -> int | None`
  - `run_rsync(argv, on_progress) -> tuple[int, str]`
  - `shelf_frame(**fields) -> bytes` (kind `shelf`, keeps only `dir`, `paths`, `state`, `progress` and `error`)
  - constants `RSYNC_OK = (0, 23, 24)`, `SPACE_MARGIN = 64 * MB`, `SHELF_FIELDS`
  - `CONTROL` now also holds `drop`, `slot`, `slot-ready`, `shelf` and `link`

- [ ] **Step 1: Write the failing tests**

Add `import sys` to the imports of `tests/test_clipd.py`, and append before `if __name__`:

```python
class ShelfCoreTests(unittest.TestCase):
    def test_clean_paths(self):
        self.assertEqual(cd.clean_paths(["/a b/c/", "/-x", "/ünï"]), ["/a b/c", "/-x", "/ünï"])
        for bad in [[], "/a", ["rel/x"], ["/"], ["/a\nb"], ["/a\0b"], [3], None]:
            with self.subTest(bad=bad):
                self.assertIsNone(cd.clean_paths(bad))

    def test_new_slot_numbers_and_prunes(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d, "shelf")
            slots = [cd.new_slot(root) for _ in range(7)]
            self.assertEqual([s.name for s in slots], [str(i) for i in range(1, 8)])
            self.assertEqual(sorted(int(p.name) for p in root.iterdir()), [3, 4, 5, 6, 7])

    def test_discard_only_removes_holding_folders(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d, "shelf")
            slot = cd.new_slot(root)
            other = Path(d, "keep")
            other.mkdir()
            cd.discard(str(other), root)
            cd.discard(str(root), root)
            cd.discard(None, root)
            self.assertTrue(other.exists() and root.exists())
            cd.discard(str(slot), root)
            self.assertFalse(slot.exists())

    def test_rsync_argv_keeps_every_path_one_argument(self):
        paths = ["/home/u/a b.txt", "/home/u/-rf", "/home/u/it's \"q\".png", "/home/u/ünï"]
        push = cd.rsync_push("twin", paths, "/home/t/.cache/twinpc/shelf/3")
        self.assertEqual(push[:7], ["rsync", "-a", "--protect-args", "--info=progress2",
                                    "-e", "ssh -o BatchMode=yes", "--"])
        self.assertEqual(push[7:], [*paths, "twin:/home/t/.cache/twinpc/shelf/3/"])
        pull = cd.rsync_pull("twin", paths, "/home/m/.cache/twinpc/shelf/4")
        self.assertEqual(pull[:7], push[:7])
        self.assertEqual(pull[7:], [*(f"twin:{p}" for p in paths), "/home/m/.cache/twinpc/shelf/4/"])

    def test_progress_lines(self):
        self.assertEqual(cd.progress("      1,234,567  45%    1.20MB/s    0:00:03"), 45)
        self.assertEqual(cd.progress("  32,768 100%   31.25MB/s    0:00:00 (xfr#1, to-chk=0/1)"), 100)
        self.assertIsNone(cd.progress('rsync: [sender] link_stat "/x" failed: No such file or directory (2)'))
        self.assertIsNone(cd.progress("sending incremental file list"))

    def test_enough_space(self):
        self.assertTrue(cd.enough_space(10 * cd.MB, 100 * cd.MB))
        self.assertFalse(cd.enough_space(90 * cd.MB, 100 * cd.MB))

    def test_run_rsync_reports_progress_and_errors(self):
        seen = []
        script = ("import sys; sys.stdout.write('  1  10%  1MB/s  0:00:01\\r  2  60%  1MB/s  0:00:01\\r"
                  "rsync error: some files could not be transferred\\n'); sys.exit(23)")
        rc, err = cd.run_rsync([sys.executable, "-c", script], seen.append)
        self.assertEqual((rc, seen), (23, [10, 60]))
        self.assertIn("could not be transferred", err)
        self.assertEqual(cd.run_rsync(["/nonexistent/rsync"], seen.append)[0], 127)

    def test_shelf_frames_are_control_frames(self):
        for kind in ("drop", "slot", "slot-ready", "shelf", "link"):
            with self.subTest(kind=kind):
                h, body = cd.read_frame(io.BytesIO(cd.encode(kind, paths=["/a"])))
                self.assertEqual((h["kind"], h["paths"], body), (kind, ["/a"], b""))
                with self.assertRaises(cd.FrameError):
                    cd.read_frame(io.BytesIO(b'{"v": 1, "kind": "%s", "size": 3, "sha": ""}\nabc' % kind.encode()))

    def test_shelf_frame_keeps_only_shelf_fields(self):
        h, _ = cd.read_frame(io.BytesIO(cd.shelf_frame(dir="/d", state="ready", paths=["/d/a"], progress=100,
                                                       error=None, extra="x")))
        self.assertEqual({k: h[k] for k in ("kind", "dir", "state", "paths", "progress")},
                         {"kind": "shelf", "dir": "/d", "state": "ready", "paths": ["/d/a"], "progress": 100})
        self.assertNotIn("extra", h)
        self.assertNotIn("error", h)
```

- [ ] **Step 2: Run them to see them fail**

Run: `python3 -m unittest tests.test_clipd`
Expected: ERROR/FAIL in `ShelfCoreTests` — `AttributeError: module 'twin_clipd' has no attribute 'clean_paths'` (and the others); the shelf frame kinds fail as `unknown frame kind`.

- [ ] **Step 3: Write the implementation**

In `clip/twin-clipd`:
- add `import queue` and `import re` to the imports (keep them alphabetical);
- replace the `CONTROL` line with:

```python
CONTROL = {"hello", "offer", "want", "drop", "slot", "slot-ready", "shelf", "link"}    # no body
```

- after `RETRY = …` add:

```python
RSYNC_OK = (0, 23, 24)                   # 23/24: some files couldn't be read or vanished — the rest arrived
SPACE_MARGIN = 64 * MB                   # kept free on the receiving disk
SHELF_FIELDS = ("dir", "paths", "state", "progress", "error")
```

- append after `def incoming(...)`:

```python
def clean_paths(paths):
    """A drop's paths as absolute paths without trailing slashes, or None if any is unusable."""
    if not isinstance(paths, list) or not paths:
        return None
    out = []
    for p in paths:
        if not isinstance(p, str) or not p.startswith("/") or "\0" in p or "\n" in p:
            return None
        p = p.rstrip("/")
        if not p:                                      # "/" itself
            return None
        out.append(p)
    return out


def new_slot(root):
    """A new numbered holding folder under `root`; only the newest KEEP folders are kept."""
    root.mkdir(parents=True, exist_ok=True)
    slot = root / str(max((int(d.name) for d in root.iterdir() if d.name.isdigit()), default=0) + 1)
    slot.mkdir()
    prune(root)
    return slot


def discard(folder, root):
    """Delete a holding folder after a failed transfer — only if it really is one directly under `root`."""
    if not isinstance(folder, str):
        return
    p = Path(folder)
    if p.name.isdigit() and p.resolve().parent == root.resolve():
        shutil.rmtree(p, ignore_errors=True)


def enough_space(size, free):
    return size + SPACE_MARGIN <= free


def rsync_push(host, paths, dest):
    """rsync argv copying local `paths` into the folder `dest` on `host`; every path is its own argument."""
    return ["rsync", "-a", "--protect-args", "--info=progress2", "-e", "ssh -o BatchMode=yes", "--",
            *paths, f"{host}:{dest}/"]


def rsync_pull(host, paths, dest):
    """rsync argv copying `paths` on `host` into the local folder `dest`."""
    return ["rsync", "-a", "--protect-args", "--info=progress2", "-e", "ssh -o BatchMode=yes", "--",
            *(f"{host}:{p}" for p in paths), f"{dest}/"]


def progress(line):
    """The percentage in an rsync --info=progress2 line ('  1,234,567  45%  1.20MB/s  0:00:03'), else None."""
    m = re.search(r"(?:^|\s)(\d{1,3})%(?:\s|$)", line)
    return int(m.group(1)) if m and int(m.group(1)) <= 100 else None


def run_rsync(argv, on_progress):
    """Run rsync, calling on_progress(percent) whenever it changes. Returns (exit code, last error line)."""
    try:
        p = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                             errors="replace")
    except OSError as e:
        return 127, str(e)
    last, error = -1, ""
    for line in p.stdout:                  # text mode turns rsync's \r progress updates into lines
        pct = progress(line)
        if pct is None:
            if line.strip():
                error = line.strip()
        elif pct != last:
            last = pct
            on_progress(pct)
    return p.wait(), error


def shelf_frame(**fields):
    return encode("shelf", **{k: v for k, v in fields.items() if k in SHELF_FIELDS and v is not None})
```

- [ ] **Step 4: Run them to see them pass**

Run: `python3 -m unittest tests.test_clipd`
Expected: `OK`.

- [ ] **Step 5: Commit**

```bash
git add clip/twin-clipd tests/test_clipd.py
git commit -m "feat(clip): shelf primitives — drop paths, holding folders, rsync argv and progress

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Shelf transfers through the service and the agent

**Files:**
- Modify: `clip/twin-clipd` (`Local`, `serve_unix`, `agent`, `Service`, `selftest`, `main`)
- Test: `tests/test_clipd_shelf.py`

**Interfaces:**
- Consumes: everything from Task 1.
- Produces (the protocol Task 3's app speaks):
  - **Main service** (`clip.sock`):
    - `hello role=shelf` registers the shelf app, and gets `link {up}` now and on every change;
    - `drop {paths}` from any client pushes those files to the twin;
    - a probe `hello` is answered with a `hello` that now includes `shelf: bool`.
  - **Twin agent** (`$XDG_RUNTIME_DIR/twinpc/shelf.sock`):
    - `hello role=shelf` registers the app and gets `link {up: true}`;
    - `drop {paths}` is checked and forwarded to the main PC as `drop {paths, size}`;
    - a probe `hello` is answered with `hello {shelf, link: true}`.
  - The agent answers `slot` with `slot-ready {dir, free}`, and passes `shelf` frames to its app. On `state: failed` it also discards the folder.
  - `agent(inp, out, cache, shelf_sock=None)`.

- [ ] **Step 1: Write the failing tests**

`tests/test_clipd_shelf.py`:

```python
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

    def test_unusable_paths_on_the_twin_are_not_forwarded(self):
        main, main_in, twin, twin_in = self.both_linked()
        twin.sendall(cd.encode("drop", paths=["relative/x.txt"]))
        twin.sendall(cd.encode("drop", paths=[str(self.d / "missing.txt")]))
        main.settimeout(1.5)
        with self.assertRaises(OSError):
            cd.read_frame(main_in)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run them to see them fail**

Run: `timeout 200 python3 -m unittest tests.test_clipd_shelf`
Expected: FAIL/ERROR — no `link` frame ever arrives (the reads time out), no `shelf.sock`, and the probe has no `shelf` key.

- [ ] **Step 3: Write the implementation**

In `clip/twin-clipd`, add these two helpers directly **above** `def agent(`:

```python
class Local:
    """The local shelf app's connection, if any. Frames sent while it's away are dropped."""

    def __init__(self):
        self.conn, self.lock = None, threading.Lock()

    def set(self, conn):
        with self.lock:
            self.conn = conn

    def clear(self, conn):
        with self.lock:
            if self.conn is conn:
                self.conn = None

    def connected(self):
        with self.lock:
            return self.conn is not None

    def send(self, frame):
        """True if the app got it."""
        with self.lock:
            if self.conn is None:
                return False
            try:
                self.conn.sendall(frame)
                return True
            except OSError:
                return False


def serve_unix(path, handle):
    """Accept connections on a private unix socket at `path`, handling each on its own thread."""
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    path.unlink(missing_ok=True)
    server = socket.socket(socket.AF_UNIX)
    server.bind(str(path))
    server.listen()

    def loop():
        while True:
            conn, _ = server.accept()
            threading.Thread(target=handle, args=(conn,), daemon=True).start()

    threading.Thread(target=loop, daemon=True).start()
    return server
```

Change `agent`:
- signature: `def agent(inp, out, cache, shelf_sock=None):`
- after `echo, write = Echo(), threading.Lock()` add: `shelf, shelf_root = Local(), cache.parent / "shelf"`
- before `threading.Thread(target=watch, daemon=True).start()` add:

```python
    def shelf_client(conn):
        """The twin's shelf app: registers, drops files, or asks for status."""
        stream = conn.makefile("rb")
        try:
            while (frame := read_frame(stream)) is not None:
                h = frame[0]
                if h["kind"] == "hello" and h.get("role") == "shelf":
                    shelf.set(conn)
                    shelf.send(encode("link", up=True))        # the agent only runs while linked
                elif h["kind"] == "hello":
                    conn.sendall(encode("hello", shelf=shelf.connected(), link=True))
                elif h["kind"] == "drop":
                    paths = clean_paths(h.get("paths"))
                    if paths is None or not all(os.path.lexists(p) for p in paths):
                        log(f"refused a drop with unusable paths: {h.get('paths')!r}")
                        notify("those files can't be sent")
                        continue
                    send(encode("drop", paths=paths, size=total_size([Path(p) for p in paths])))
                    log(f"dropped on the twin: {len(paths)} item(s)")
        except (FrameError, OSError) as e:
            log(f"from the twin's shelf app: {e}")
        finally:
            shelf.clear(conn)
            conn.close()

    if shelf_sock is not None:
        serve_unix(shelf_sock, shelf_client)
```

- in the agent's frame loop, after the `if h["kind"] == "hello":` branch, add:

```python
            elif h["kind"] == "slot":
                slot = new_slot(shelf_root)
                send(encode("slot-ready", dir=str(slot), free=shutil.disk_usage(slot).free))
            elif h["kind"] == "shelf":
                if h.get("state") == "failed":
                    discard(h.get("dir"), shelf_root)
                delivered = shelf.send(shelf_frame(**h))
                if not delivered and h.get("state") == "ready":
                    notify(f"files from the main PC are in {h.get('dir')}")
```

Change `Service`:
- `__init__`: add after `self.sock_path, self.cache = sock_path, cache`:

```python
        self.host = host
        self.shelf, self.shelf_root = Local(), cache.parent / "shelf"
        self.slots, self.transfer = queue.Queue(), threading.Lock()   # one push at a time
```

- `twin_loop`: in the `hello` branch after `log("connected to the twin")` add `self.shelf.send(encode("link", up=True))`; in `finally` after the `with self.state: self.twin = None` block add `self.shelf.send(encode("link", up=False))`.
- rename the current `from_twin` to `from_twin_content` and add a new dispatcher above it:

```python
    def from_twin(self, h, body):
        if h["kind"] == "drop":
            threading.Thread(target=self.pull, args=(h,), daemon=True).start()
        elif h["kind"] == "slot-ready":
            self.slots.put(h)
        else:
            self.from_twin_content(h, body)
```

- in `client`, add before the probe `elif h["kind"] == "hello":` branch:

```python
                elif h["kind"] == "hello" and h.get("role") == "shelf":
                    self.shelf.set(conn)
                    with self.state:
                        up = self.twin is not None
                    self.shelf.send(encode("link", up=up))
```

- change the probe status dict to `{"twin": self.twin is not None, "extension": self.ext is not None, "shelf": self.shelf.connected(), "last": self.last}`;
- add a branch after the `offer` branch:

```python
                elif h["kind"] == "drop":
                    threading.Thread(target=self.push, args=(h.get("paths"),), daemon=True).start()
```

- in `client`'s `finally`, add `self.shelf.clear(conn)`;
- add these methods to `Service`:

```python
    def to_twin(self, frame):
        with self.state:
            twin = self.twin
        if twin is None:
            return False
        try:
            with self.twin_write:
                twin.write(frame)
                twin.flush()
            return True
        except OSError as e:
            log(f"to the twin: {e}")
            return False

    def push(self, paths):
        """This PC → twin: copy dropped files into a new holding folder on the twin and show them there."""
        paths = clean_paths(paths)
        if paths is None or not all(os.path.lexists(p) for p in paths):
            log(f"refused a drop with unusable paths: {paths!r}")
            return
        with self.state:
            up = self.twin is not None
        if not up:
            self.shelf.send(shelf_frame(state="failed", error="twin not connected"))
            notify("twin not connected — files not sent")
            return
        size = total_size([Path(p) for p in paths])
        with self.transfer:
            while not self.slots.empty():
                self.slots.get_nowait()
            if not self.to_twin(encode("slot")):
                return
            try:
                ready = self.slots.get(timeout=15)
            except queue.Empty:
                notify("the twin didn't answer — files not sent")
                return
            dest = ready.get("dir")
            if not isinstance(dest, str) or not dest.startswith("/"):
                return
            if not enough_space(size, ready.get("free") or 0):
                self.to_twin(shelf_frame(dir=dest, state="failed", error="not enough space on the twin"))
                notify(f"not enough space on the twin for {size // MB} MB")
                return
            self.to_twin(shelf_frame(dir=dest, state="receiving", progress=0))
            rc, err = run_rsync(rsync_push(self.host, paths, dest),
                                lambda pct: self.to_twin(shelf_frame(dir=dest, state="receiving", progress=pct)))
        if rc in RSYNC_OK:
            self.to_twin(shelf_frame(dir=dest, state="ready", progress=100,
                                     paths=[f"{dest}/{Path(p).name}" for p in paths]))
            self.note(f"this PC → twin shelf ({len(paths)} item(s), {size} bytes)")
        else:
            self.to_twin(shelf_frame(dir=dest, state="failed", error=err or f"rsync exit {rc}"))
            notify(f"files not sent to the twin: {err}")

    def pull(self, h):
        """Twin → this PC: copy files dropped on the twin into a new holding folder here and show them."""
        paths = clean_paths(h.get("paths"))
        if paths is None:
            log(f"refused a drop from the twin with unusable paths: {h.get('paths')!r}")
            return
        size = h.get("size") if isinstance(h.get("size"), int) else 0
        dest = new_slot(self.shelf_root)
        if not enough_space(size, shutil.disk_usage(dest).free):
            shutil.rmtree(dest, ignore_errors=True)
            notify(f"not enough space here for {size // MB} MB from the twin")
            return
        self.shelf.send(shelf_frame(dir=str(dest), state="receiving", progress=0))
        rc, err = run_rsync(rsync_pull(self.host, paths, dest),
                            lambda pct: self.shelf.send(shelf_frame(dir=str(dest), state="receiving", progress=pct)))
        if rc in RSYNC_OK:
            ready = shelf_frame(dir=str(dest), state="ready", progress=100,
                                paths=[str(dest / Path(p).name) for p in paths])
            if not self.shelf.send(ready):
                notify(f"files from the twin are in {dest}")
            self.note(f"twin → this PC shelf ({len(paths)} item(s), {size} bytes)")
        else:
            shutil.rmtree(dest, ignore_errors=True)
            self.shelf.send(shelf_frame(dir=str(dest), state="failed", error=err or f"rsync exit {rc}"))
            notify(f"files from the twin were not received: {err}")
```

- `selftest`: add `f" · shelf app {'connected' if h.get('shelf') else 'not running'}"` before the `last:` part of the printed line;
- `main`: pass the shelf socket to the agent: `return agent(sys.stdin.buffer, sys.stdout.buffer, cache, run / "twinpc" / "shelf.sock")`.

- [ ] **Step 4: Run them to see them pass**

Run: `timeout 250 python3 -m unittest tests.test_clipd tests.test_clipd_link tests.test_clipd_shelf`
Expected: `OK`.

- [ ] **Step 5: Commit**

```bash
git add clip/twin-clipd tests/test_clipd_shelf.py
git commit -m "feat(clip): shelf drops both ways — agent shelf socket, slots, rsync push/pull with progress

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: The `twin-shelf` app

**Files:**
- Create: `clip/twin-shelf` (executable)
- Test: `tests/test_shelf.py`

**Interfaces:**
- Consumes: Task 2's socket protocol.
- Produces:
  - CLI: `twin-shelf [--edge left|right|top|bottom] [--socket clip.sock|shelf.sock] [--selftest] [--drop PATH…]`;
  - `--selftest` exit codes: 0 = the shelf app is connected to the local twin-clipd, 1 = twin-clipd is up but no shelf app, 2 = twin-clipd isn't reachable;
  - writes `$XDG_RUNTIME_DIR/twinpc/shelf.json` = `{"edge": …}` when the app starts;
  - module-level names `STRIP_TITLE`, `SHELF_TITLE`, `ShelfModel`, `uris`, `encode`, `read_header`, `socket_path`, `send_drop`, `selftest`, `write_edge`, `main`.

- [ ] **Step 1: Write the failing tests**

`tests/test_shelf.py`:

```python
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
```

- [ ] **Step 2: Run them to see them fail**

Run: `python3 -m unittest tests.test_shelf`
Expected: ERROR — `FileNotFoundError` for `clip/twin-shelf`.

- [ ] **Step 3: Write the app**

`clip/twin-shelf` (then `chmod +x clip/twin-shelf`):

```python
#!/usr/bin/env python3
"""twin-shelf — drag files to the other PC (man twin: SHELF).

A thin drop strip sits on the screen edge that faces the other PC. Files dropped on it are copied
there by twin-clipd, and a shelf pops up at that PC's edge holding them: drag them into any folder.
"""
import argparse
import hashlib
import json
import os
import socket
import subprocess
import threading
import time
from pathlib import Path

EDGES = ("left", "right", "top", "bottom")
STRIP_TITLE, SHELF_TITLE = "twinPC drop strip", "twinPC shelf"    # the GNOME extension places these
HIDE_AFTER = 120                          # seconds without news before the shelf hides itself
RETRY = 5                                 # seconds between attempts to reach twin-clipd
EMPTY_SHA = hashlib.sha256(b"").hexdigest()
CSS = b"""
.strip { background: rgba(53, 132, 228, 0.35); border-radius: 3px; }
.strip.hover { background: rgba(53, 132, 228, 0.95); }
.strip.down { background: rgba(128, 128, 128, 0.25); }
.shelf { padding: 10px; }
.handle { padding: 4px 8px; border-radius: 6px; background: rgba(53, 132, 228, 0.15); }
"""


def encode(kind, **fields):
    """A twin-clipd control frame (the shelf only ever sends and receives frames without a body)."""
    return json.dumps({"v": 1, "kind": kind, **fields, "size": 0, "sha": EMPTY_SHA}).encode() + b"\n"


def read_header(stream):
    """The next control frame's header; None at the end of the stream."""
    line = stream.readline(65536)
    if not line:
        return None
    h = json.loads(line)
    if not isinstance(h, dict) or h.get("v") != 1 or h.get("size", 0) != 0:
        raise ValueError("unexpected frame")
    return h


def socket_path(name):
    run = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    return Path(run) / "twinpc" / name


def uris(paths):
    return "".join(Path(p).absolute().as_uri() + "\r\n" for p in paths)


def notify(msg):
    try:
        subprocess.run(["notify-send", "-a", "twinPC", "twinPC shelf", msg], timeout=5,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        pass


class ShelfModel:
    """What the strip and the shelf show, driven by `link` and `shelf` frames."""

    def __init__(self):
        self.link = False
        self.dir = self.state = None
        self.progress, self.error, self.items, self.expected = 0, "", [], 0

    def apply(self, h):
        """Update from a frame; returns 'link', 'shelf', or None for frames the shelf ignores."""
        if h.get("kind") == "link":
            self.link = bool(h.get("up"))
            return "link"
        if h.get("kind") != "shelf":
            return None
        if h.get("dir") != self.dir:
            self.items, self.expected = [], 0
        self.dir, self.state = h.get("dir"), h.get("state")
        self.progress = int(h.get("progress") or 0)
        self.error = str(h.get("error") or "")
        if self.state == "ready":
            paths = [p for p in h.get("paths") or [] if isinstance(p, str)]
            self.expected = len(paths)
            self.items = [p for p in paths if os.path.lexists(p)]
        return "shelf"

    def summary(self):
        if self.state == "receiving":
            return f"receiving… {self.progress} %"
        if self.state == "failed":
            return f"transfer failed — {self.error}"
        if self.state == "ready":
            n = len(self.items)
            if n < self.expected:
                return f"{n} of {self.expected} copied — drag into any folder"
            return f"{n} item{'s' if n != 1 else ''} — drag into any folder"
        return ""

    def draggable(self):
        return self.state == "ready" and bool(self.items)


class Link:
    """The connection to the local twin-clipd: frames arrive on a thread; the UI sends."""

    def __init__(self, sock):
        self.sock, self.conn, self.lock = sock, None, threading.Lock()

    def send(self, frame):
        with self.lock:
            if self.conn is None:
                return False
            try:
                self.conn.sendall(frame)
                return True
            except OSError:
                return False

    def run(self, on_frame):
        """Connect (retrying), register as the shelf app and hand every frame to on_frame. Never returns."""
        while True:
            try:
                s = socket.socket(socket.AF_UNIX)
                s.connect(str(self.sock))
                s.sendall(encode("hello", role="shelf"))
                with self.lock:
                    self.conn = s
                stream = s.makefile("rb")
                while (h := read_header(stream)) is not None:
                    on_frame(h)
            except (OSError, ValueError):
                pass
            with self.lock:
                self.conn = None
            on_frame({"kind": "link", "up": False})
            time.sleep(RETRY)


def send_drop(sock, paths):
    """Send the local twin-clipd a drop of `paths` (made absolute) — for scripts and tests."""
    s = socket.socket(socket.AF_UNIX)
    s.settimeout(5)
    s.connect(str(sock))
    s.sendall(encode("drop", paths=[str(Path(p).absolute()) for p in paths]))
    s.close()


def selftest(sock):
    try:
        s = socket.socket(socket.AF_UNIX)
        s.settimeout(5)
        s.connect(str(sock))
        s.sendall(encode("hello", role="probe"))
        h = read_header(s.makefile("rb"))
        s.close()
    except (OSError, ValueError, TypeError):
        print("shelf: twin-clipd isn't running here")
        return 2
    if h and h.get("shelf"):
        print("shelf: running")
        return 0
    print("shelf: app not running (twin shelf on)")
    return 1


def write_edge(edge):
    """Tell the GNOME extension which edge to place the strip and shelf on."""
    p = socket_path("shelf.json")
    p.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(p.parent, 0o700)
    p.write_text(json.dumps({"edge": edge}))


def run_app(sock, edge):
    """The drop strip and the shelf. GTK is imported only here, so the rest works without a display."""
    import gi
    gi.require_version("Gtk", "4.0")
    gi.require_version("Gdk", "4.0")
    from gi.repository import Gdk, Gio, GLib, Gtk, Pango
    try:                                        # the twin (Hyprland): pin windows to the edge
        gi.require_version("Gtk4LayerShell", "1.0")
        from gi.repository import Gtk4LayerShell as LayerShell
    except (ValueError, ImportError):
        LayerShell = None                       # the main PC (GNOME): the twinPC extension places them

    model, link = ShelfModel(), Link(sock)
    across = edge in ("left", "right")
    app = Gtk.Application(application_id="org.twinpc.Shelf", flags=Gio.ApplicationFlags.NON_UNIQUE)

    def pin(win, width, height, margin=0):
        win.set_decorated(False)
        win.set_resizable(False)
        win.set_default_size(width, height)
        if LayerShell is not None and LayerShell.is_supported():
            LayerShell.init_for_window(win)
            LayerShell.set_layer(win, LayerShell.Layer.OVERLAY)
            side = getattr(LayerShell.Edge, edge.upper())
            LayerShell.set_anchor(win, side, True)
            LayerShell.set_margin(win, side, margin)
            LayerShell.set_keyboard_mode(win, LayerShell.KeyboardMode.NONE)

    def icon_for(path):
        if os.path.isdir(path):
            return Gio.ThemedIcon.new("folder")
        ctype, _ = Gio.content_type_guess(path, None)
        return Gio.content_type_get_icon(ctype)

    def drag_source(widget, paths_fn):
        src = Gtk.DragSource(actions=Gdk.DragAction.COPY)

        def prepare(_src, _x, _y):
            paths = paths_fn()
            if not paths:
                return None
            files = Gdk.FileList.new_from_list([Gio.File.new_for_path(p) for p in paths])
            return Gdk.ContentProvider.new_union([
                Gdk.ContentProvider.new_for_value(files),
                Gdk.ContentProvider.new_for_bytes("text/uri-list", GLib.Bytes.new(uris(paths).encode())),
            ])
        src.connect("prepare", prepare)
        widget.add_controller(src)

    def activate(app):
        css = Gtk.CssProvider()
        css.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), css,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        monitors = Gdk.Display.get_default().get_monitors()
        geo = monitors.get_item(0).get_geometry() if monitors.get_n_items() else None
        long_side = ((geo.height if across else geo.width) // 3) if geo else 360

        strip = Gtk.ApplicationWindow(application=app, title=STRIP_TITLE)
        pin(strip, 6 if across else long_side, long_side if across else 6)
        bar = Gtk.Box(hexpand=True, vexpand=True)
        bar.add_css_class("strip")
        strip.set_child(bar)

        drop = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY)

        def enter(*_):
            bar.add_css_class("hover")
            return Gdk.DragAction.COPY

        def on_drop(_target, value, _x, _y):
            bar.remove_css_class("hover")
            paths = [f.get_path() for f in value.get_files() if f.get_path()]
            if not paths:
                return False
            if not model.link:
                notify("the other PC isn't connected — files not sent")
                return False
            return link.send(encode("drop", paths=paths))
        drop.connect("enter", enter)
        drop.connect("leave", lambda *_: bar.remove_css_class("hover"))
        drop.connect("drop", on_drop)
        bar.add_controller(drop)

        shelf = Gtk.ApplicationWindow(application=app, title=SHELF_TITLE)
        pin(shelf, 300, 10, margin=14)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.add_css_class("shelf")
        head = Gtk.Box(spacing=6)
        label = Gtk.Label(xalign=0, hexpand=True, wrap=True)
        close = Gtk.Button(icon_name="window-close-symbolic")
        close.add_css_class("flat")
        close.connect("clicked", lambda *_: shelf.set_visible(False))
        head.append(label)
        head.append(close)
        bar_progress = Gtk.ProgressBar()
        items = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        drag_all = Gtk.Label(label="⠿ drag all into a folder", xalign=0)
        drag_all.add_css_class("handle")
        drag_source(drag_all, lambda: list(model.items))
        for w in (head, bar_progress, items, drag_all):
            box.append(w)
        shelf.set_child(box)
        hide = {"id": 0}

        def hide_shelf():
            hide["id"] = 0
            shelf.set_visible(False)
            return False

        def refresh(what):
            if what is None:
                return
            if model.link:
                bar.remove_css_class("down")
                bar.set_tooltip_text("drop files here to send them to the other PC")
            else:
                bar.add_css_class("down")
                bar.set_tooltip_text("the other PC isn't connected")
            if what != "shelf":
                return
            label.set_text(model.summary())
            bar_progress.set_visible(model.state == "receiving")
            bar_progress.set_fraction(model.progress / 100)
            items.remove_all()
            for p in model.items:
                row = Gtk.Box(spacing=8)
                row.append(Gtk.Image.new_from_gicon(icon_for(p)))
                row.append(Gtk.Label(label=Path(p).name, xalign=0, ellipsize=Pango.EllipsizeMode.MIDDLE))
                drag_source(row, lambda p=p: [p])
                items.append(row)
            drag_all.set_visible(len(model.items) > 1)
            shelf.present()
            if hide["id"]:
                GLib.source_remove(hide["id"])
            hide["id"] = GLib.timeout_add_seconds(HIDE_AFTER, hide_shelf)

        def on_frame(h):
            def apply():
                refresh(model.apply(h))
                return False
            GLib.idle_add(apply)

        strip.present()
        refresh("link")
        threading.Thread(target=link.run, args=(on_frame,), daemon=True).start()

    app.connect("activate", activate)
    return app.run(None)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="twin-shelf", description="drag files to the other PC")
    ap.add_argument("--edge", choices=EDGES, default="left", help="the screen edge that faces the other PC")
    ap.add_argument("--socket", default="clip.sock", help="twin-clipd's socket name (on the twin: shelf.sock)")
    ap.add_argument("--selftest", action="store_true", help="is the shelf app running and connected?")
    ap.add_argument("--drop", nargs="+", metavar="PATH", help="send these files to the other PC and exit")
    a = ap.parse_args(argv)
    sock = socket_path(a.socket)
    if a.selftest:
        return selftest(sock)
    if a.drop:
        send_drop(sock, a.drop)
        return 0
    write_edge(a.edge)
    return run_app(sock, a.edge)


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run them to see them pass**

Run: `python3 -m unittest tests.test_shelf`
Expected: `OK`.

- [ ] **Step 5: Smoke-run the GTK part on this PC for 3 seconds (a small window flashes)**

Run: `XDG_RUNTIME_DIR=$(mktemp -d) WAYLAND_DISPLAY=$WAYLAND_DISPLAY timeout 3 clip/twin-shelf --edge left; echo rc=$?`
Expected: `rc=124` (still running when the timeout killed it), and no Python traceback on stderr.

- [ ] **Step 6: Commit**

```bash
git add clip/twin-shelf tests/test_shelf.py
git commit -m "feat(clip): twin-shelf — drop strip and shelf (GTK4, layer-shell on the twin)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: The GNOME extension places the strip and shelf

**Files:**
- Modify: `clip/gnome-extension/twinpc-clipboard@twinpc/extension.js`
- Test: `tests/test_clip_extension.py`

**Interfaces:**
- Consumes: `STRIP_TITLE` and `SHELF_TITLE` from `clip/twin-shelf`, and `shelf.json` (Task 3).

- [ ] **Step 1: Write the failing tests**

Append to `ExtensionTests` in `tests/test_clip_extension.py`:

```python
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
```

(`EXT` is `clip/gnome-extension/twinpc-clipboard@twinpc`, so `EXT.parents[1]` is `clip/`.)

- [ ] **Step 2: Run them to see them fail**

Run: `python3 -m unittest tests.test_clip_extension`
Expected: FAIL — `"'twinPC drop strip'" not found`.

- [ ] **Step 3: Write the placement code**

In `extension.js`:
- after `const decoder = new TextDecoder();` add:

```js
// twin-shelf's windows: Wayland apps can't place themselves on GNOME, so the extension does it
const PLACED = ['twinPC drop strip', 'twinPC shelf'];
const EDGES = ['left', 'right', 'top', 'bottom'];
```

- at the end of `enable()` add:

```js
        this._watched = new Map();
        this._createdId = global.display.connect('window-created', (_display, win) => this._watch(win));
        for (const actor of global.get_window_actors())
            this._watch(actor.meta_window);
```

- at the start of `disable()` add:

```js
        global.display.disconnect(this._createdId);
        for (const win of [...this._watched.keys()])
            this._unwatch(win);
        this._watched = null;
```

- add these methods to the class:

```js
    _watch(win) {
        if (this._watched.has(win))
            return;
        this._watched.set(win, [
            win.connect('notify::title', () => this._place(win)),
            win.connect('size-changed', () => this._place(win)),
            win.connect('unmanaged', () => this._unwatch(win)),
        ]);
        this._place(win);
    }

    _unwatch(win) {
        for (const id of this._watched?.get(win) ?? [])
            win.disconnect(id);
        this._watched?.delete(win);
    }

    _place(win) {
        const title = win.get_title();
        if (!PLACED.includes(title))
            return;
        const edge = this._edge();
        const mon = this._edgeMonitor(edge);
        const rect = win.get_frame_rect();
        const gap = title === PLACED[1] ? 14 : 0;           // the shelf sits just inside the strip
        let x, y;
        if (edge === 'left' || edge === 'right') {
            x = edge === 'left' ? mon.x + gap : mon.x + mon.width - rect.width - gap;
            y = mon.y + Math.round((mon.height - rect.height) / 2);
        } else {
            x = mon.x + Math.round((mon.width - rect.width) / 2);
            y = edge === 'top' ? mon.y + gap : mon.y + mon.height - rect.height - gap;
        }
        if (rect.x !== x || rect.y !== y)
            win.move_frame(true, x, y);
        if (!win.is_above())
            win.make_above();
        if (!win.is_on_all_workspaces())
            win.stick();
    }

    _edge() {
        try {
            const path = GLib.build_filenamev([GLib.get_user_runtime_dir(), 'twinpc', 'shelf.json']);
            const [, bytes] = GLib.file_get_contents(path);
            const edge = JSON.parse(decoder.decode(bytes)).edge;
            if (EDGES.includes(edge))
                return edge;
        } catch (e) {
            // not written yet: fall back to the default below
        }
        return 'left';
    }

    _edgeMonitor(edge) {
        let best = global.display.get_monitor_geometry(0);
        for (let i = 1; i < global.display.get_n_monitors(); i++) {
            const g = global.display.get_monitor_geometry(i);
            if ((edge === 'left' && g.x < best.x) ||
                (edge === 'right' && g.x + g.width > best.x + best.width) ||
                (edge === 'top' && g.y < best.y) ||
                (edge === 'bottom' && g.y + g.height > best.y + best.height))
                best = g;
        }
        return best;
    }
```

- [ ] **Step 4: Run them to see them pass**

Run: `python3 -m unittest tests.test_clip_extension`
Expected: `OK` (includes the `node --check` syntax test).

- [ ] **Step 5: Commit**

```bash
git add clip/gnome-extension tests/test_clip_extension.py
git commit -m "feat(clip): GNOME extension places the shelf's strip and shelf at the facing edge

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: The `shelf` feature in `twinpc`

**Files:**
- Create: `main/twin-shelf.service`, `twinpc/twin-shelf.service`
- Modify: `tool/packages.toml`, `tool/twinpc_lib/adapters/desktop.py`, `tool/twinpc_lib/features.py`, `tests/tool/test_features.py`
- Test: `tests/tool/test_shelf.py`

**Interfaces:**
- Consumes: `twin-shelf --selftest [--socket shelf.sock]` (Task 3); the clipboard feature's `twin-clip.service`.
- Produces:
  - `features.facing_edge(twin_side, machine) -> str`;
  - `Gnome.shelf_main_steps(feature, repo)` and `Hyprland.shelf_twin_steps(feature, pk)`;
  - feature `shelf`, placed after `clipboard`, with step ids in this order:
    1. `shelf.main.packages`, `shelf.twin.packages`, `shelf.twin.layer-shell`
    2. `shelf.main.clipboard`
    3. `shelf.main.command`, `shelf.twin.command`
    4. `shelf.main.unit`, `shelf.main.service`, `shelf.twin.unit`, `shelf.twin.service`
    5. `shelf.main.link`, `shelf.twin.link`

- [ ] **Step 1: Write the failing tests**

`tests/tool/test_shelf.py`:

```python
import unittest
from pathlib import Path

from tests.tool.fakes import FakeRunner
from tests.tool.test_features import PROFILE
from twinpc_lib import features as F, steps as S

REPO = Path(__file__).resolve().parents[2]


def shelf_steps(prof):
    return [s for s in F.build_plan(prof, REPO) if s.feature == "shelf"]


def applied(step, machine):
    r = FakeRunner([(machine, "", 0, "")])
    step.apply(S.Ctx({}, r, REPO))
    return r.calls[-1]


class ShelfPlanTests(unittest.TestCase):
    def test_gnome_main_and_hyprland_twin(self):
        self.assertEqual([s.id for s in shelf_steps(PROFILE)], [
            "shelf.main.packages", "shelf.twin.packages", "shelf.twin.layer-shell", "shelf.main.clipboard",
            "shelf.main.command", "shelf.twin.command", "shelf.main.unit", "shelf.main.service",
            "shelf.twin.unit", "shelf.twin.service", "shelf.main.link", "shelf.twin.link"])

    def test_comes_after_clipboard(self):
        self.assertEqual(F.FEATURES.index("shelf"), F.FEATURES.index("clipboard") + 1)

    def test_facing_edges(self):
        self.assertEqual((F.facing_edge("left", "main"), F.facing_edge("left", "twin")), ("left", "right"))
        self.assertEqual((F.facing_edge("top", "main"), F.facing_edge("top", "twin")), ("top", "bottom"))
        self.assertEqual(F.facing_edge("nonsense", "main"), "left")

    def test_units_use_the_facing_edges(self):
        prof = {**PROFILE, "user": {**PROFILE["user"], "twin_side": "right"}}
        steps = {s.id: s for s in shelf_steps(prof)}
        self.assertIn("ExecStart=%h/.local/bin/twin-shelf --edge right\n", applied(steps["shelf.main.unit"], "main")[3])
        twin_unit = applied(steps["shelf.twin.unit"], "twin")[3]
        self.assertIn("ExecStart=%h/.local/bin/twin-shelf --edge left --socket shelf.sock\n", twin_unit)
        self.assertIn("LD_PRELOAD=/usr/lib/libgtk4-layer-shell.so", twin_unit)

    def test_twin_gets_a_copy_of_the_app(self):
        [cmd] = [s for s in shelf_steps(PROFILE) if s.id == "shelf.twin.command"]
        machine, shell, _root, data = applied(cmd, "twin")
        self.assertEqual((machine, data), ("twin", (REPO / "clip" / "twin-shelf").read_text()))
        self.assertIn("chmod 755", shell)

    def test_needs_the_clipboard_feature(self):
        [st] = [s for s in shelf_steps(PROFILE) if s.id == "shelf.main.clipboard"]
        self.assertIsNone(st.apply)
        r = FakeRunner()
        st.check(S.Ctx({}, r, REPO))
        self.assertIn("twin-clip.service", r.calls[-1][1])

    def test_link_checks_use_each_side_socket(self):
        steps = {s.id: s for s in shelf_steps(PROFILE)}
        r = FakeRunner()
        steps["shelf.twin.link"].check(S.Ctx({}, r, REPO))
        self.assertIn("--selftest --socket shelf.sock", r.calls[-1][1])

    def test_unsupported_desktops(self):
        for machine, desktop, reason in [
                ("main", "kde", "desktop 'kde' is not supported yet (planned)"),
                ("main", "hyprland", "desktop 'hyprland' on the main PC is not supported yet (planned)"),
                ("twin", "gnome", "desktop 'gnome' on the twin is not supported yet (planned)")]:
            prof = {**PROFILE, machine: {**PROFILE[machine], "desktop": desktop}}
            with self.subTest(machine=machine, desktop=desktop):
                self.assertEqual([s.skip_reason for s in shelf_steps(prof)], [reason])


if __name__ == "__main__":
    unittest.main()
```

In `tests/tool/test_features.py`, `test_key_steps_exist`: add `"shelf.main.link",` after `"clipboard.main.link",`.

- [ ] **Step 2: Run them to see them fail**

Run: `python3 -m unittest tests.tool.test_shelf tests.tool.test_features`
Expected: FAIL/ERROR — no `shelf` steps, `'shelf' is not in list`, no `facing_edge`.

- [ ] **Step 3: Write the implementation**

`main/twin-shelf.service`:

```ini
[Unit]
Description=twinPC shelf — drop strip and shelf for dragging files to the twin PC
PartOf=graphical-session.target
After=graphical-session.target twin-clip.service

[Service]
ExecStart=%h/.local/bin/twin-shelf --edge left
Restart=on-failure
RestartSec=5

[Install]
WantedBy=graphical-session.target
```

`twinpc/twin-shelf.service`:

```ini
[Unit]
Description=twinPC shelf — drop strip and shelf for dragging files to the main PC
PartOf=graphical-session.target
After=graphical-session.target

[Service]
# gtk4-layer-shell has to be loaded before libwayland-client, so it is preloaded
Environment=LD_PRELOAD=/usr/lib/libgtk4-layer-shell.so
ExecStart=%h/.local/bin/twin-shelf --edge right --socket shelf.sock
Restart=on-failure
RestartSec=5

[Install]
WantedBy=graphical-session.target
```

`tool/packages.toml` — add after the `wl-clipboard` line:

```toml
python-gi = { apt = "python3-gi", pacman = "python-gobject", dnf = "python3-gobject" }
gtk4-gir = { apt = "gir1.2-gtk-4.0", pacman = "gtk4", dnf = "gtk4" }
gtk4-layer-shell = { pacman = "gtk4-layer-shell" }
```

`tool/twinpc_lib/adapters/desktop.py`:
- add to `class Gnome`:

```python
    def shelf_main_steps(self, feature, repo):
        return []          # the clipboard feature's GNOME extension places the strip and shelf here
```

- add to `class Hyprland`:

```python
    def shelf_twin_steps(self, feature, pk):
        return [pkg_step(feature, "twin", pk, ["gtk4-layer-shell"], tag="layer-shell")]   # pins them to the edge
```

`tool/twinpc_lib/features.py` — add after `_clipboard`:

```python
def facing_edge(twin_side, machine):
    """The screen edge that faces the other PC: the twin's side on the main PC, the opposite one on the twin."""
    side = twin_side if twin_side in OPPOSITE else "left"
    return side if machine == "main" else OPPOSITE[side]


def _shelf(profile, repo, v):
    md = _desktop_for(profile, "main", "shelf_main_steps")
    if isinstance(md, str):
        return [unsupported_step("shelf", "main", md)]
    td = _desktop_for(profile, "twin", "shelf_twin_steps")
    if isinstance(td, str):
        return [unsupported_step("shelf", "twin", td)]
    r = v["repo"]
    main_edge, twin_edge = facing_edge(v["side"], "main"), facing_edge(v["side"], "twin")
    return [
        pkg_step("shelf", "main", _pk(profile, "main"), ["python-gi", "gtk4-gir"]),
        pkg_step("shelf", "twin", _pk(profile, "twin"), ["python-gi", "gtk4-gir"]),
        *td.shelf_twin_steps("shelf", _pk(profile, "twin")),
        manual_step("shelf.main.clipboard", "shelf", "main", "the clipboard feature (the shelf uses its link)",
                    "Install the clipboard feature first: tool/twinpc install clipboard",
                    check="systemctl --user is-enabled --quiet twin-clip.service"),
        cmd_step("shelf.main.command", "shelf", "main", "install twin-shelf on this PC",
                 check=f'[ "$(readlink ~/.local/bin/twin-shelf)" = "{r}/clip/twin-shelf" ]',
                 apply=f'mkdir -p ~/.local/bin && ln -sf "{r}/clip/twin-shelf" ~/.local/bin/twin-shelf'),
        file_step("shelf.twin.command", "shelf", "twin", "$HOME/.local/bin/twin-shelf",
                  _read(repo, "clip/twin-shelf"), mode="755", describe="install twin-shelf on the twin"),
        file_step("shelf.main.unit", "shelf", "main", "$HOME/.config/systemd/user/twin-shelf.service",
                  repo_unit(repo, "main/twin-shelf.service").replace("--edge left\n", f"--edge {main_edge}\n")),
        unit_step("shelf.main.service", "shelf", "main", "twin-shelf.service"),
        file_step("shelf.twin.unit", "shelf", "twin", "$HOME/.config/systemd/user/twin-shelf.service",
                  _read(repo, "twinpc/twin-shelf.service").replace("--edge right ", f"--edge {twin_edge} ")),
        unit_step("shelf.twin.service", "shelf", "twin", "twin-shelf.service"),
        *md.shelf_main_steps("shelf", repo),
        cmd_step("shelf.main.link", "shelf", "main", "check the shelf app is connected on this PC",
                 check="~/.local/bin/twin-shelf --selftest >/dev/null",
                 apply="systemctl --user restart twin-shelf.service && sleep 3 && ~/.local/bin/twin-shelf --selftest"),
        cmd_step("shelf.twin.link", "shelf", "twin", "check the shelf app is connected on the twin",
                 check="~/.local/bin/twin-shelf --selftest --socket shelf.sock >/dev/null",
                 apply="systemctl --user restart twin-shelf.service && sleep 3"
                       " && ~/.local/bin/twin-shelf --selftest --socket shelf.sock"),
    ]
```

and register it after `clipboard` in `BUILDERS`:

```python
BUILDERS = {"connection": _connection, "cli": _cli, "gpu-stack": _gpu_stack, "routing": _routing,
            "mount": _mount, "power": _power, "unlock": _unlock, "kvm": _kvm, "clipboard": _clipboard,
            "shelf": _shelf, "audio": _audio, "desktop": _desktop, "gui": _gui, "nic-fix": _nic_fix}
```

- [ ] **Step 4: Run them to see them pass**

Run: `python3 -m unittest tests.tool.test_shelf tests.tool.test_features tests.tool.test_clipboard tests.tool.test_adapters tests.tool.test_review_fixes tests.tool.test_cli`
Expected: `OK`.

- [ ] **Step 5: Commit**

```bash
git add main/twin-shelf.service twinpc/twin-shelf.service tool/packages.toml tool/twinpc_lib/adapters/desktop.py tool/twinpc_lib/features.py tests/tool/test_shelf.py tests/tool/test_features.py
git commit -m "feat(tool): shelf feature — packages, twin-shelf on both PCs, units, link checks

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: `twin shelf`, docs, the system check, and acceptance on the real machines

**Files:**
- Modify: `twin`, `twin-completion.bash`, `man/twin.1`, `README.md`
- Create: `tests/test_shelf.sh`

- [ ] **Step 1: Write the failing system check**

`tests/test_shelf.sh` (then `chmod +x tests/test_shelf.sh`):

```bash
#!/usr/bin/env bash
# A file dropped through each PC's shelf socket arrives in the other PC's shelf folder.
# (It pops the shelf up on both PCs.)
set -u
tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
tok=shelf-$RANDOM$RANDOM
echo "$tok" > "$tmp/$tok.txt"
"$HOME/.local/bin/twin-shelf" --drop "$tmp/$tok.txt" || { echo "FAIL could not reach twin-clipd here"; exit 1; }
got=
for _ in $(seq 30); do
  got=$(ssh twin "cat ~/.cache/twinpc/shelf/*/$tok.txt 2>/dev/null")
  [[ $got == "$tok" ]] && break; sleep 0.5
done
[[ $got == "$tok" ]] || { echo "FAIL this PC → twin shelf"; exit 1; }

tok=shelf-$RANDOM$RANDOM
src=/tmp/twinpc-shelf-test-$tok
ssh twin "mkdir -p $src && echo $tok > $src/$tok.txt && .local/bin/twin-shelf --socket shelf.sock --drop $src/$tok.txt" \
  || { echo "FAIL could not reach the twin's shelf socket"; exit 1; }
for _ in $(seq 30); do
  got=$(cat ~/.cache/twinpc/shelf/*/"$tok.txt" 2>/dev/null)
  [[ $got == "$tok" ]] && break; sleep 0.5
done
ssh twin "rm -rf $src"
[[ $got == "$tok" ]] || { echo "FAIL twin → this PC shelf"; exit 1; }
echo "PASS shelf both ways"
```

- [ ] **Step 2: Run it to see it fail**

Run: `bash tests/test_shelf.sh`
Expected: `FAIL could not reach twin-clipd here` or `FAIL this PC → twin shelf` (nothing is installed yet).

- [ ] **Step 3: Add `twin shelf`, completion, the manual and the README**

In `twin`, add to the usage text after the `twin clip` line:

```
  twin shelf [status|on|off]   drop files on the screen edge → a shelf on the other PC to drag them from
```

and add a case branch before `  route)`:

```bash
  shelf)
    # drag files between the PCs: twin-shelf.service on both PCs + twin-clipd (man twin: SHELF)
    case ${1:-status} in
      status)
        echo "this PC: $("$HOME/.local/bin/twin-shelf" --selftest 2>&1)"
        echo "twin:    $(ssh -o ConnectTimeout=3 -o BatchMode=yes "$HOST" '.local/bin/twin-shelf --selftest --socket shelf.sock' 2>&1)" ;;
      on)  systemctl --user start twin-shelf.service
           ssh -o ConnectTimeout=3 -o BatchMode=yes "$HOST" 'systemctl --user start twin-shelf.service' || true
           echo "shelf ON" ;;
      off) systemctl --user stop twin-shelf.service
           ssh -o ConnectTimeout=3 -o BatchMode=yes "$HOST" 'systemctl --user stop twin-shelf.service' || true
           echo "shelf OFF until 'twin shelf on' or your next login" ;;
      *) echo "usage: twin shelf status|on|off"; exit 1 ;;
    esac
    ;;
```

In `twin-completion.bash`: add `shelf` after `clip` in `cmds`, and before `push)` add:

```bash
            shelf) COMPREPLY=($(compgen -W "status on off" -- "$cur")) ;;
```

In `man/twin.1`, insert before `.SH TASK ROUTING`:

```
.SH SHELF (DRAG FILES ACROSS)
A thin strip sits on the screen edge that faces the other PC. Drop files or folders on
it and they are copied to the other PC; a small shelf pops up at that PC's edge holding
them \(em move the mouse across and drag them into any folder. Files are only ever
copied. They wait in
.I ~/.cache/twinpc/shelf/
(the newest 5 drops are kept). The data goes over SSH with rsync, always started by
this PC; the strip turns grey while the twin isn't connected.
.I twin-shelf.service
runs the strip on both PCs and needs the clipboard link
.RI ( twin-clip.service ).
.TP
.BR "shelf " [ status | on | off ]
.B status
shows whether the shelf app runs and is connected on each PC;
.B off
hides the strips until
.B twin shelf on
or your next login.
```

In `README.md`:
- In the ✨ Features table, add a row after the "📋 Shared clipboard / 🧰 One-command setup" row:

```html
<tr>
<td valign="top">

### 🧲 Drag files across
Drop files on the **edge of the screen that faces the other PC** — a shelf pops up over
there; drag them into **any folder**. Big folders welcome (rsync over SSH).

</td>
<td valign="top">

</td>
</tr>
```

- In the 📖 cheat sheet, after the `twin clip status` row, add: `| \`twin shelf status\` · \`twin shelf off\` | the drag-files-across strip and shelf |`
- In 🩺 Troubleshooting, after the "Copy/paste doesn't cross over" block, add:

```markdown
<details>
<summary><b>Nothing happens when I drop files on the edge</b></summary>

`twin shelf status` on this PC. A grey strip means the twin isn't connected (`twin clip status`).
On GNOME the strip is placed by the twinPC extension — log out and back in once after installing.
Every transfer is logged: `journalctl --user -t twin-clipd`.
</details>
```

- In 🗂️ Project layout, change the `clip/` line to: `clip/                                clipboard + drag-files-across — twin-clipd, twin-shelf, GNOME extension`
- In 🧪 Tests, add `tests.test_shelf tests.test_clipd_shelf tests.tool.test_shelf` to the fast, local command.
- Update the tests badge and the layout's test count to the new number of passing tests: `Ran N` from the full-suite run in Step 4, minus the skipped ones.

- [ ] **Step 4: Install on the real machines, log in again, and see it work**

Run: `tool/twinpc install --yes clipboard shelf`
Expected: every step `done` or `already done`. `shelf.main.link` and `shelf.twin.link` pass: they print `shelf: running`.

Run: `twin shelf status`
Expected: `this PC: shelf: running` and `twin:    shelf: running`.

Run: `bash tests/test_shelf.sh`
Expected: `PASS shelf both ways`.

Then the **user logs out and back in** on the main PC, so GNOME loads the extension's placement code; the executor asks for this and waits. After that, check by hand with the user:
1. The strip is on the left edge here and on the right edge on the twin.
2. Drag a file and a folder from Files here onto the strip. The shelf pops up on the twin; drag the items into a folder there.
3. Do the reverse from the twin's Files.
4. Drop a large folder (over 1 GB) and watch the progress on the shelf.

Run the whole suite (the list from README 🧪 plus `tests.test_twin_exec`) and `for t in tests/*.sh; do bash "$t"; done`
Expected: `OK` (3 docker probe tests skipped), and every shell check passes.

- [ ] **Step 5: Commit**

```bash
git add twin twin-completion.bash man/twin.1 README.md tests/test_shelf.sh
git commit -m "feat: twin shelf, manual and README for dragging files across, and a both-ways system check

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```
