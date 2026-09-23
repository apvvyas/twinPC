# Twin Task Routing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Commands typed in a bash terminal on the main PC run on the twin PC automatically when the rules say so, with live output here and a visible session on twin's workspace 9.

**Architecture:** A pure Python router (`route/twin-route`) decides `twin|local` from the command line, cwd, main-PC load and `rules.toml`. A bash hook rewrites the line on Enter to `twin-exec -- <line>`; `twin-exec` runs it in a tmux session on twin (attached here, logged, exit code propagated) and asks the `twin` CLI to open a viewer window on twin's Hyprland workspace 9. Twin's `~/work` is sshfs-mounted at `~/twin`.

**Tech Stack:** bash 5.3 (main PC) / bash + tmux 3.7c + Hyprland 0.56 Lua dispatch + foot (twin), Python 3.14 stdlib (`tomllib`, `shlex`, `unittest`, `pty`), sshfs, systemd user units.

**Spec:** `docs/superpowers/specs/2026-09-23-twin-task-routing-design.md`

## Global Constraints

- Only interactive bash on the main PC is intercepted; GUI apps, IDE-/script-started commands and other shells are out of scope.
- Twin host: ssh alias `twin` (10.42.0.11); stage check = SSH banner (`dropbear` → unlock, `SSH-*` → up, none → off), cached 5 s.
- Twin job environment: `export HSA_OVERRIDE_GFX_VERSION=10.3.0 PATH=$HOME/ai-env/bin:$HOME/.local/share/mise/shims:$HOME/.local/bin:$PATH`.
- Workspace mapping: main `~/twin` ⇄ twin `~/work`; outside a workspace twin runs in `~`.
- Load thresholds: CPU ≥ 80 % (1-minute average) or RAM ≥ 85 % used.
- Router is side-effect free and does < 20 ms of work; the hook runs the line locally on any router error or when the router call exceeds its timeout (0.3 s wall clock including Python start-up — the spec's 200 ms budget plus process start).
- Twin unavailable → prompt `[h]ere / [w]ake twin / [c]ancel`.
- Viewer windows open on twin workspace 9 without focus change: `hl.dsp.exec_cmd("[workspace 9 silent] foot ...")` (verified on twin 2026-09-23); close 30 s after exit 0, stay open on non-zero exit.
- Env vars passed to twin: `TERM`, `LANG`, `COLORTERM` + names listed in `TWIN_ROUTE_ENV_ALLOW`.
- Notification (`notify-send`) when a routed task ran > 60 s.
- Kill switch: `TWIN_ROUTE=off` (per shell) or `twin route off|on` (flag file `${XDG_CONFIG_HOME:-~/.config}/twin-route/disabled`).
- Hyprland dispatch on twin is Lua: `hyprctl dispatch 'hl.dsp.<fn>(...)'`.
- Git author for this repo: `apoorv-vyas <apvvyas@gmail.com>`; every commit message ends with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Deviation from spec (improvement): the CPU sampler runs as a user systemd service (`twin-route-load.service`) instead of being spawned by the hook — same output file, no duplicate-instance handling needed.

## Review Focus

1. Commands containing `$VARS`, quotes, globs, a trailing `# comment` or a trailing `&` must reach twin exactly as typed and expand on twin — pinned by `test_quoting_and_comment` in Task 4.
2. With twin off, a prompt whose cwd is under `~/twin` must not hang: the router never touches the filesystem for workspace checks and the hook uses `findmnt` (reads the mount table, no `stat`) — pinned by `test_workspace_paths_do_not_touch_filesystem` (Task 1) and `test_mount_down_refuses_without_running` (Task 5).
3. Cancelling (`c`) or a refused workspace command must not delete the previous history entry — pinned by `test_cancel_keeps_history_intact` in Task 5.
4. Output of a command that finishes instantly must still appear in the local terminal — pinned by `test_fast_command_output_in_tty` in Task 4.
5. Detaching with Ctrl-b d must leave the task running on twin and print how to reattach, exiting 0 — pinned by `test_detach_keeps_task_running` in Task 4.

---

### Task 1: Router and default rules

**Files:**
- Create: `route/twin-route` (executable Python)
- Create: `route/rules.default.toml`
- Test: `tests/test_twin_route.py`

**Interfaces:**
- Consumes: nothing.
- Produces (used by Tasks 4, 5, 7):
  - CLI `route/twin-route [--cwd DIR] [--load CPU,RAM] [--rules FILE] [--explain] -- "<line>"` → stdout `twin <reason>` or `local <reason>`, exit 0.
  - CLI `route/twin-route [--rules FILE] --map-cwd DIR` → stdout `~/work/<rel>` or `~`.
  - Reasons: `unparsable`, `forced`, `passthrough`, `never:<pat>`, `workspace`, `always:<pat>`, `load:<pat> cpu=N% ram=N%`, `load-ok:<pat> cpu=N% ram=N%`, `files-not-on-twin`, `default`.
  - Python functions: `load_rules(path=None) -> dict`, `stages(line) -> list[list[str]] | None`, `matches(stage, pattern) -> bool`, `in_workspace(cwd, rules) -> bool`, `map_cwd(cwd, rules) -> str`, `decide(line, cwd, load, rules) -> tuple[str, str]`.
  - CPU sample file read by the router: `$XDG_RUNTIME_DIR/twin-route.load` containing an integer percent (written by Task 2).

- [ ] **Step 1: Commit the existing tree as a baseline and set the repo identity**

```bash
cd ~/projects/twinPC
git config user.name "apoorv-vyas" && git config user.email "apvvyas@gmail.com"
git add twin twin-completion.bash man/twin.1
git commit -m "chore: track existing twin CLI, completion and man page

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

- [ ] **Step 2: Write the default rules file**

`route/rules.default.toml`:

```toml
# twin task routing rules — copied to ~/.config/twin-route/rules.toml on install; edit that copy.
# Patterns are shell globs. A pattern without spaces matches the command name (first word);
# a pattern with spaces matches "command args…" (a trailing " *" is implied).

[load]
cpu_threshold = 80        # % busy, 1-minute average on this PC
ram_threshold = 85        # % RAM used on this PC

[workspace]
local = "~/twin"          # twin's ~/work mounted here
remote = "~/work"

always_twin = ["ollama", "python *train*.py", "python3 *train*.py", "torchrun", "accelerate",
               "whisper", "comfyui", "stable-diffusion*", "claude -p*", "llama-*"]

never_twin  = ["cd", "ls", "git", "vim", "nvim", "nano", "code", "sudo", "apt", "systemctl",
               "ssh", "exit", "source", ".", "export", "history", "man", "local", "twin", "twin-exec"]

load_offload = ["make", "cargo build*", "cargo test*", "pytest*", "npm run build*", "npm test*",
                "ffmpeg", "docker build*", "gradle*", "python *", "python3 *"]

# commands that work on the current folder: outside ~/twin they stay here (the files aren't on twin)
needs_cwd   = ["claude*", "make*", "pytest*", "npm *", "cargo *", "gradle*", "docker build*",
               "python *.py*", "python3 *.py*"]
```

- [ ] **Step 3: Write the failing tests**

`tests/test_twin_route.py`:

```python
import importlib.machinery
import importlib.util
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ROUTE = ROOT / "route" / "twin-route"
_loader = importlib.machinery.SourceFileLoader("twin_route", str(ROUTE))
_spec = importlib.util.spec_from_loader("twin_route", _loader)
tr = importlib.util.module_from_spec(_spec)
_loader.exec_module(tr)

RULES = tr.load_rules(ROOT / "route" / "rules.default.toml")
HOME = os.path.expanduser("~")
WS = os.path.join(HOME, "twin")
IDLE, CPU_BUSY, RAM_BUSY = (10.0, 30.0), (95.0, 30.0), (10.0, 90.0)


class RouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.plain = cls.tmp.name                    # empty folder outside the workspace

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def d(self, line, cwd=None, load=IDLE):
        return tr.decide(line, cwd or self.plain, load, RULES)

    # --- unparsable input stays local
    def test_unparsable(self):
        for line in ["", "   ", "echo 'unclosed", "make \\", "cat <<EOF", "echo $(date)",
                     "line1\nline2", "(cd x; make)"]:
            with self.subTest(line=line):
                self.assertEqual(self.d(line), ("local", "unparsable"))

    # --- forced / passthrough
    def test_forced_local(self):
        self.assertEqual(self.d("local ollama run x"), ("local", "forced"))

    def test_passthrough(self):
        self.assertEqual(self.d("twin gpu"), ("local", "passthrough"))
        self.assertEqual(self.d("twin-exec -- ls"), ("local", "passthrough"))

    # --- never_twin
    def test_never(self):
        self.assertEqual(self.d("git status"), ("local", "never:git"))
        self.assertEqual(self.d("ls -la | grep x")[0], "local")
        self.assertEqual(self.d("sudo apt update"), ("local", "never:sudo"))

    def test_never_needs_every_stage(self):
        self.assertEqual(self.d("git log | ollama run x summarize"), ("twin", "always:ollama"))

    def test_never_beats_workspace(self):
        self.assertEqual(self.d("git status", cwd=WS + "/proj"), ("local", "never:git"))

    # --- workspace
    def test_workspace(self):
        self.assertEqual(self.d("make", cwd=WS + "/proj"), ("twin", "workspace"))
        self.assertEqual(self.d("claude -p 'fix tests'", cwd=WS), ("twin", "workspace"))

    def test_workspace_prefix_trap(self):
        self.assertFalse(tr.in_workspace(HOME + "/twinx", RULES))
        self.assertFalse(tr.in_workspace(HOME + "/twin-other/a", RULES))

    def test_workspace_paths_do_not_touch_filesystem(self):
        # ~/twin may be a dead sshfs mount: nothing here may stat it (a stat would hang)
        orig_exists, orig_stat = os.path.exists, os.stat
        def boom(*a, **k):
            raise AssertionError("filesystem touched")
        os.path.exists = boom
        os.stat = boom
        try:
            self.assertTrue(tr.in_workspace(WS + "/deep/dir", RULES))
            self.assertEqual(tr.map_cwd(WS + "/deep/dir", RULES), "~/work/deep/dir")
            self.assertEqual(tr.decide("make", WS + "/deep", IDLE, RULES), ("twin", "workspace"))
        finally:
            os.path.exists, os.stat = orig_exists, orig_stat

    def test_map_cwd(self):
        self.assertEqual(tr.map_cwd(WS, RULES), "~/work")
        self.assertEqual(tr.map_cwd(WS + "/a/b", RULES), "~/work/a/b")
        self.assertEqual(tr.map_cwd(HOME + "/twinx", RULES), "~")
        self.assertEqual(tr.map_cwd("/tmp", RULES), "~")

    # --- always_twin
    def test_always(self):
        self.assertEqual(self.d("ollama run gemma3:4b hi"), ("twin", "always:ollama"))
        self.assertEqual(self.d("/usr/bin/ollama list"), ("twin", "always:ollama"))
        self.assertEqual(self.d("stable-diffusion-webui --port 7860"), ("twin", "always:stable-diffusion*"))

    def test_assignments_and_wrappers_are_skipped(self):
        self.assertEqual(self.d("FOO=1 ollama run x"), ("twin", "always:ollama"))
        self.assertEqual(self.d("time nice ollama run x"), ("twin", "always:ollama"))

    def test_redirect_target_is_not_a_file_reference(self):
        open(os.path.join(self.plain, "out.txt"), "w").close()
        try:
            self.assertEqual(self.d("ollama run x hi > out.txt"), ("twin", "always:ollama"))
        finally:
            os.remove(os.path.join(self.plain, "out.txt"))

    # --- files guard
    def test_files_guard_needs_cwd(self):
        self.assertEqual(self.d("python train.py"), ("local", "files-not-on-twin"))
        self.assertEqual(self.d("claude -p 'fix'"), ("local", "files-not-on-twin"))

    def test_files_guard_existing_relative_path(self):
        open(os.path.join(self.plain, "a.mp3"), "w").close()
        try:
            self.assertEqual(self.d("whisper a.mp3"), ("local", "files-not-on-twin"))
            self.assertEqual(self.d("whisper missing.mp3"), ("twin", "always:whisper"))
        finally:
            os.remove(os.path.join(self.plain, "a.mp3"))

    # --- load_offload
    def test_load_ok_stays_local(self):
        self.assertEqual(self.d("ffmpeg -i /a.mp4 /b.mp4"), ("local", "load-ok:ffmpeg cpu=10% ram=30%"))

    def test_cpu_busy_goes_to_twin(self):
        self.assertEqual(self.d("ffmpeg -i /a.mp4 /b.mp4", load=CPU_BUSY), ("twin", "load:ffmpeg cpu=95% ram=30%"))

    def test_ram_busy_goes_to_twin(self):
        self.assertEqual(self.d("ffmpeg -i /a.mp4 /b.mp4", load=RAM_BUSY)[0], "twin")

    def test_threshold_is_inclusive(self):
        self.assertEqual(self.d("ffmpeg -i /a /b", load=(80.0, 0.0))[0], "twin")
        self.assertEqual(self.d("ffmpeg -i /a /b", load=(79.9, 0.0))[0], "local")

    def test_busy_but_needs_cwd_stays_local(self):
        self.assertEqual(self.d("make -j32", load=CPU_BUSY), ("local", "files-not-on-twin"))

    # --- default
    def test_default(self):
        self.assertEqual(self.d("echo hi"), ("local", "default"))

    # --- matching
    def test_matches(self):
        self.assertTrue(tr.matches(["python", "train.py", "--epochs", "3"], "python *train*.py"))
        self.assertTrue(tr.matches(["claude", "-p", "hi"], "claude -p*"))
        self.assertFalse(tr.matches(["claude", "hi"], "claude -p*"))
        self.assertTrue(tr.matches(["cargo", "build", "--release"], "cargo build*"))

    # --- CLI
    def test_cli_decide(self):
        out = subprocess.run([str(ROUTE), "--rules", str(ROOT / "route/rules.default.toml"),
                              "--cwd", self.plain, "--load", "95,10", "--", "ffmpeg -i /a /b"],
                             capture_output=True, text=True, check=True).stdout.strip()
        self.assertEqual(out, "twin load:ffmpeg cpu=95% ram=10%")

    def test_cli_map_cwd(self):
        out = subprocess.run([str(ROUTE), "--rules", str(ROOT / "route/rules.default.toml"),
                              "--map-cwd", WS + "/p"], capture_output=True, text=True, check=True).stdout
        self.assertEqual(out.strip(), "~/work/p")

    def test_cli_is_fast(self):
        t = time.monotonic()
        subprocess.run([str(ROUTE), "--load", "1,1", "--", "echo hi"], capture_output=True, check=True)
        self.assertLess(time.monotonic() - t, 0.2)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 4: Run the tests to verify they fail**

Run: `cd ~/projects/twinPC && python3 -m unittest tests.test_twin_route -v`
Expected: ERROR — `FileNotFoundError` for `route/twin-route`.

- [ ] **Step 5: Implement the router**

`route/twin-route` (then `chmod +x route/twin-route`):

```python
#!/usr/bin/env python3
"""twin-route — decide whether a command line typed on this PC runs here or on the twin PC.

  twin-route [--cwd DIR] [--load CPU,RAM] [--rules FILE] [--explain] -- "<command line>"
      prints "twin <reason>" or "local <reason>"
  twin-route [--rules FILE] --map-cwd DIR
      prints the twin-side folder for DIR ("~/work/<rel>", or "~" outside the workspace)

Side-effect free: reads only the rules file, /proc/meminfo and the CPU sample written by
twin-route-load. Workspace checks never touch the filesystem (the sshfs mount may be dead).
Design: docs/superpowers/specs/2026-09-23-twin-task-routing-design.md
"""
import argparse
import fnmatch
import os
import shlex
import sys
import tomllib
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_RULES = HERE / "rules.default.toml"
USER_RULES = Path.home() / ".config" / "twin-route" / "rules.toml"
SEPARATORS = {"|", "||", "&&", ";", "&", "|&"}
WRAPPERS = {"time", "nice", "nohup", "env", "command", "exec"}


def load_rules(path=None):
    p = Path(path) if path else (USER_RULES if USER_RULES.exists() else DEFAULT_RULES)
    with open(p, "rb") as f:
        return tomllib.load(f)


def _is_assignment(word):
    name, eq, _ = word.partition("=")
    return bool(eq) and name.isidentifier()


def stages(line):
    """Split a command line into pipeline stages (lists of words); None if it should not be routed."""
    if not line.strip() or "\n" in line or line.rstrip().endswith("\\"):
        return None
    lex = shlex.shlex(line, posix=True, punctuation_chars=True)
    lex.whitespace_split = True
    try:
        tokens = list(lex)
    except ValueError:                       # unbalanced quotes
        return None
    out, cur, skip_next = [], [], False
    for t in tokens:
        if skip_next:                        # target of a redirection
            skip_next = False
            continue
        if t in SEPARATORS:
            if cur:
                out.append(cur)
            cur = []
        elif t in ("(", ")") or t.startswith("<<") or "$(" in t or t == "$":
            return None                      # subshells, command substitution, heredocs
        elif t and set(t) <= set("<>&"):
            skip_next = not t.endswith("&")  # "> file" skips the file; ">&" is followed by an fd
        else:
            cur.append(t)
    if cur:
        out.append(cur)
    cleaned = []
    for st in out:
        while st and _is_assignment(st[0]):
            st = st[1:]
        while len(st) > 1 and st[0] in WRAPPERS:
            st = st[1:]
        if st:
            cleaned.append(st)
    return cleaned or None


def matches(stage, pattern):
    name = os.path.basename(stage[0])
    if " " not in pattern:
        return fnmatch.fnmatchcase(name, pattern)
    text = " ".join([name] + stage[1:])
    return fnmatch.fnmatchcase(text, pattern) or fnmatch.fnmatchcase(text, pattern + " *")


def first_match(sts, patterns):
    for st in sts:
        for p in patterns:
            if matches(st, p):
                return p
    return None


def _ws(rules):
    return os.path.abspath(os.path.expanduser(rules["workspace"]["local"]))


def in_workspace(cwd, rules):
    ws, c = _ws(rules), os.path.abspath(cwd)   # abspath is pure string work: no stat
    return c == ws or c.startswith(ws + os.sep)


def map_cwd(cwd, rules):
    if not in_workspace(cwd, rules):
        return "~"
    rel = os.path.relpath(os.path.abspath(cwd), _ws(rules))
    remote = rules["workspace"]["remote"].rstrip("/")
    return remote if rel == "." else f"{remote}/{rel}"


def needs_local_files(sts, cwd, rules):
    if first_match(sts, rules.get("needs_cwd", [])):
        return True
    for st in sts:
        for w in st[1:]:
            if w.startswith(("-", "/", "~")):
                continue
            if os.path.exists(os.path.join(cwd, w)):
                return True
    return False


def current_load():
    cpu = 0.0
    sample = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / "twin-route.load"
    try:
        cpu = float(sample.read_text().split()[0])
    except (OSError, ValueError, IndexError):
        pass
    mem = {}
    with open("/proc/meminfo") as fh:
        for row in fh:
            key, _, val = row.partition(":")
            mem[key] = int(val.split()[0])
    ram = 100.0 * (1 - mem["MemAvailable"] / mem["MemTotal"])
    return cpu, ram


def decide(line, cwd, load, rules):
    sts = stages(line)
    if sts is None:
        return "local", "unparsable"
    head = sts[0][0]
    if head == "local":
        return "local", "forced"
    if head in ("twin", "twin-exec"):
        return "local", "passthrough"
    never = rules.get("never_twin", [])
    if all(any(matches(st, p) for p in never) for st in sts):
        return "local", f"never:{first_match(sts, never)}"
    if in_workspace(cwd, rules):
        return "twin", "workspace"
    pattern = first_match(sts, rules.get("always_twin", []))
    if pattern:
        decision = ("twin", f"always:{pattern}")
    else:
        pattern = first_match(sts, rules.get("load_offload", []))
        if not pattern:
            return "local", "default"
        cpu, ram = load
        limits = rules.get("load", {})
        usage = f"cpu={cpu:.0f}% ram={ram:.0f}%"
        if cpu >= limits.get("cpu_threshold", 80) or ram >= limits.get("ram_threshold", 85):
            decision = ("twin", f"load:{pattern} {usage}")
        else:
            return "local", f"load-ok:{pattern} {usage}"
    if needs_local_files(sts, cwd, rules):
        return "local", "files-not-on-twin"
    return decision


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cwd", default=None)
    ap.add_argument("--load", help="CPU,RAM percentages instead of measuring (tests)")
    ap.add_argument("--rules")
    ap.add_argument("--explain", action="store_true")
    ap.add_argument("--map-cwd", metavar="DIR")
    ap.add_argument("line", nargs="*")
    a = ap.parse_args(argv)
    rules = load_rules(a.rules)
    if a.map_cwd is not None:
        print(map_cwd(a.map_cwd, rules))
        return 0
    cwd = a.cwd or os.environ.get("PWD") or os.getcwd()
    line = " ".join(a.line)
    load = tuple(float(x) for x in a.load.split(",")) if a.load else current_load()
    decision, reason = decide(line, cwd, load, rules)
    print(f"{decision} {reason}")
    if a.explain:
        print(f"  stages:    {stages(line)}")
        print(f"  cwd:       {cwd} (workspace: {in_workspace(cwd, rules)} → twin {map_cwd(cwd, rules)})")
        print(f"  load:      cpu={load[0]:.0f}% ram={load[1]:.0f}%")
        print(f"  rules:     {a.rules or (USER_RULES if USER_RULES.exists() else DEFAULT_RULES)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `cd ~/projects/twinPC && python3 -m unittest tests.test_twin_route -v`
Expected: all tests PASS (≈ 25 tests, several with subtests).

- [ ] **Step 7: Commit**

```bash
git add route/twin-route route/rules.default.toml tests/test_twin_route.py
git commit -m "feat(route): add twin-route decision engine and default rules

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: CPU load sampler

**Files:**
- Create: `route/twin-route-load` (executable bash)
- Create: `route/twin-route-load.service`
- Test: `tests/test_load_sampler.sh`

**Interfaces:**
- Consumes: nothing.
- Produces: `$XDG_RUNTIME_DIR/twin-route.load` — one integer (0–100), the 1-minute average CPU-busy % refreshed every interval (read by Task 1's `current_load()`); env `TWIN_ROUTE_LOAD_INTERVAL` (seconds, default 5) for tests.

- [ ] **Step 1: Write the failing test**

`tests/test_load_sampler.sh`:

```bash
#!/usr/bin/env bash
# The sampler writes an integer percentage within a couple of intervals.
set -u
ROOT=$(cd "$(dirname "$0")/.." && pwd)
tmp=$(mktemp -d)
XDG_RUNTIME_DIR=$tmp TWIN_ROUTE_LOAD_INTERVAL=1 "$ROOT/route/twin-route-load" &
pid=$!
trap 'kill $pid 2>/dev/null; rm -rf "$tmp"' EXIT
for _ in $(seq 30); do [[ -s $tmp/twin-route.load ]] && break; sleep 0.2; done
v=$(cat "$tmp/twin-route.load" 2>/dev/null)
if [[ $v =~ ^[0-9]+$ ]] && (( v >= 0 && v <= 100 )); then echo "PASS load=$v"; else echo "FAIL got '$v'"; exit 1; fi
```

- [ ] **Step 2: Run it to verify it fails**

Run: `bash tests/test_load_sampler.sh`
Expected: `No such file or directory` for the sampler, then `FAIL got ''`.

- [ ] **Step 3: Implement the sampler and its service**

`route/twin-route-load` (then `chmod +x`):

```bash
#!/usr/bin/env bash
# twin-route-load — keep this PC's 1-minute average CPU-busy % in $XDG_RUNTIME_DIR/twin-route.load
# for twin-route (which decides whether CPU-heavy commands go to the twin PC). Runs as a user service.
interval=${TWIN_ROUTE_LOAD_INTERVAL:-5}
out=${XDG_RUNTIME_DIR:-/tmp}/twin-route.load
keep=$(( 60 / interval )); (( keep > 0 )) || keep=1
samples=()
read -r _ u n s i w q sq st _ < /proc/stat
prev_total=$((u + n + s + i + w + q + sq + st)); prev_idle=$((i + w))
while sleep "$interval"; do
  read -r _ u n s i w q sq st _ < /proc/stat
  total=$((u + n + s + i + w + q + sq + st)); idle=$((i + w))
  dt=$((total - prev_total)); di=$((idle - prev_idle))
  prev_total=$total; prev_idle=$idle
  (( dt > 0 )) || continue
  samples+=( $(( 100 * (dt - di) / dt )) )
  (( ${#samples[@]} > keep )) && samples=( "${samples[@]:1}" )
  sum=0; for x in "${samples[@]}"; do sum=$((sum + x)); done
  echo $(( sum / ${#samples[@]} )) > "$out.tmp" && mv "$out.tmp" "$out"
done
```

`route/twin-route-load.service`:

```ini
[Unit]
Description=CPU load sampler for twin task routing (1-minute average)

[Service]
ExecStart=%h/projects/twinPC/route/twin-route-load
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `bash tests/test_load_sampler.sh`
Expected: `PASS load=<n>`

- [ ] **Step 5: Commit**

```bash
git add route/twin-route-load route/twin-route-load.service tests/test_load_sampler.sh
git commit -m "feat(route): add CPU load sampler for load-based routing

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Task viewer on twin's workspace 9

**Files:**
- Create: `route/twin-task-view` (bash, installed to twin `~/.local/bin/`)
- Modify: `twin` — add `viewer` subcommand; `run` opens a viewer (covers `cc` and `bizdev`, which call `run`)
- Test: `tests/test_viewer.sh`

**Interfaces:**
- Consumes: existing `gsh` / `q` helpers in `twin`; twin job logs `~/jobs/<session>.log` (with a final `[exit N]` line) and, for routed tasks, `~/jobs/<session>.exit` (Task 4).
- Produces: `twin viewer <session>` — opens `foot --app-id=twin-task --title=<session>` on twin workspace 9 silently, running `twin-task-view <session>`; never fails the caller (errors ignored when there is no Hyprland session). `twin-task-view <session>` on twin: waits ≤ 5 s for the session, attaches, then prints `[exit N]`, closes after 30 s on 0 or waits for a key otherwise.

- [ ] **Step 1: Write the failing test**

`tests/test_viewer.sh`:

```bash
#!/usr/bin/env bash
# A viewer window for a twin tmux session appears on workspace 9 and the active workspace is unchanged.
set -u
ROOT=$(cd "$(dirname "$0")/.." && pwd); T="$ROOT/twin"
s=viewtest-$$
wsid() { "$T" gui hypr activeworkspace -j | python3 -c 'import sys,json;print(json.load(sys.stdin)["id"])'; }
before=$(wsid)
ssh twin "tmux new-session -d -s $s 'sleep 20'"
"$T" viewer "$s"; sleep 2
ws=$("$T" gui hypr clients -j | python3 -c "
import sys,json
print(next((str(c['workspace']['id']) for c in json.load(sys.stdin) if c['class']=='twin-task' and c['title']=='$s'), 'none'))")
after=$(wsid)
ssh twin "tmux kill-session -t $s" 2>/dev/null
if [[ $ws == 9 && $before == "$after" ]]; then echo "PASS viewer on ws 9, focus stayed on $after"; else echo "FAIL ws=$ws before=$before after=$after"; exit 1; fi
```

- [ ] **Step 2: Run it to verify it fails**

Run: `bash tests/test_viewer.sh`
Expected: `unknown command: viewer` from `twin`, then `FAIL ws=none …`.

- [ ] **Step 3: Implement the viewer script and install it on twin**

`route/twin-task-view`:

```bash
#!/usr/bin/env bash
# twin-task-view SESSION — runs in a terminal on twin's workspace 9: live view of a task sent from
# the main PC (or a `twin run` job), then its result. Closes 30 s after success; stays open on failure.
s=${1:?session}; log=$HOME/jobs/$s.log; ex=$HOME/jobs/$s.exit
printf '\e]2;%s\a' "$s"
for _ in $(seq 20); do tmux has-session -t "$s" 2>/dev/null && break; [[ -f $ex ]] && break; sleep 0.25; done
if tmux has-session -t "$s" 2>/dev/null; then
  tmux attach -t "$s"
elif [[ -f $log ]]; then
  cat "$log"                                     # finished before the window opened
fi
for _ in $(seq 10); do [[ -f $ex ]] && break; sleep 0.3; done
rc=$(cat "$ex" 2>/dev/null || sed -n 's/^\[exit \([0-9]*\)\]$/\1/p' "$log" 2>/dev/null | tail -1)
if [[ $rc == 0 ]]; then
  echo "[exit 0] — closing in 30 s (any key closes now)"; read -rsn1 -t 30
else
  echo "[exit ${rc:-?}] — press any key to close"; read -rsn1
fi
```

Install: `scp route/twin-task-view twin:.local/bin/twin-task-view && ssh twin 'chmod +x ~/.local/bin/twin-task-view'`

- [ ] **Step 4: Add `viewer` to the `twin` CLI and call it from `run`**

In `twin`, insert before the `  gpu)` case:

```bash
  viewer)
    # terminal on twin's workspace 9 attached to a task session — no focus change on twin
    s=${1:?session}
    gsh "hyprctl dispatch $(q "hl.dsp.exec_cmd(\"[workspace 9 silent] foot --app-id=twin-task --title=$s -e \$HOME/.local/bin/twin-task-view $s\")")" >/dev/null 2>&1 || true
    ;;
```

In the `run)` case, directly after the `ssh "$HOST" "$REMOTE_ENV; tmux new-session -d …"` line, add:

```bash
    "$0" viewer "$name" >/dev/null 2>&1 &
```

In `usage()`, after the `twin kill <name>` line add:

```
  twin viewer <name>           open a window for a job on twin's workspace 9 (automatic for run/cc/bizdev)
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `bash tests/test_viewer.sh`
Expected: `PASS viewer on ws 9, focus stayed on <n>`. If the window does not appear, run `twin gui hypr dispatch 'hl.dsp.exec_cmd("[workspace 9 silent] foot")'` by hand (this form was verified on twin) and check whether `$HOME` expanded inside `exec_cmd` — if not, replace `\$HOME/.local/bin/twin-task-view` with `/home/appspubs/.local/bin/twin-task-view`.

- [ ] **Step 6: Commit**

```bash
git add route/twin-task-view twin tests/test_viewer.sh
git commit -m "feat(twin): task viewer windows on twin workspace 9

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: `twin-exec` runner

**Files:**
- Create: `route/twin-exec` (executable bash)
- Test: `tests/test_twin_exec.py` (needs twin up)

**Interfaces:**
- Consumes: `route/twin-route --map-cwd DIR` (Task 1); `twin viewer <session>` (Task 3, via `twin` on PATH, falling back to `$HERE/../twin`).
- Produces: `twin-exec [--name NAME] -- <command line>` — exit code = the remote command's exit code; 0 with a message when detached (Ctrl-b d); 255 with a message when the connection drops; 2 on usage error. Twin files: `~/jobs/<session>.log` (pane output + `[exit N]`), `~/jobs/<session>.exit` (just N). Session names: `task-<MMDD-HHMMSS>-<first word>`.

- [ ] **Step 1: Write the failing tests**

`tests/test_twin_exec.py`:

```python
import os
import pty
import select
import subprocess
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXEC = str(ROOT / "route" / "twin-exec")


def run_pty(args, feed=(), timeout=40):
    """Run args in a pseudo-terminal; feed = [(delay_s, bytes)]; returns (exit_code, output)."""
    pid, fd = pty.fork()
    if pid == 0:
        os.execv(args[0], args)
    out, start, feed = b"", time.monotonic(), list(feed)
    while True:
        if feed and time.monotonic() - start >= feed[0][0]:
            os.write(fd, feed.pop(0)[1])
        r, _, _ = select.select([fd], [], [], 0.2)
        if r:
            try:
                chunk = os.read(fd, 4096)
            except OSError:
                break
            if not chunk:
                break
            out += chunk
        if time.monotonic() - start > timeout:
            os.kill(pid, 9)
            break
    _, status = os.waitpid(pid, 0)
    return os.waitstatus_to_exitcode(status), out.decode(errors="replace")


class TwinExecTests(unittest.TestCase):
    def test_output_and_exit_code(self):
        p = subprocess.run([EXEC, "--", "echo hello-from-twin; exit 3"], capture_output=True, text=True, timeout=60)
        self.assertIn("hello-from-twin", p.stdout)
        self.assertEqual(p.returncode, 3)

    def test_quoting_and_comment(self):
        line = """echo "home=$HOME" 'a  b' no*match* ; echo tail # trailing comment"""
        p = subprocess.run([EXEC, "--", line], capture_output=True, text=True, timeout=60, cwd="/tmp")
        self.assertIn("home=/home/appspubs", p.stdout)          # $HOME expanded on twin
        self.assertIn("a  b", p.stdout)
        self.assertIn("no*match*", p.stdout)
        self.assertIn("tail", p.stdout)
        self.assertEqual(p.returncode, 0)

    def test_runs_in_twin_home_outside_workspace(self):
        p = subprocess.run([EXEC, "--", "pwd"], capture_output=True, text=True, timeout=60, cwd="/tmp")
        self.assertIn("/home/appspubs", p.stdout)

    def test_fast_command_output_in_tty(self):
        code, out = run_pty([EXEC, "--", "echo fast-output-marker"])
        self.assertIn("fast-output-marker", out)
        self.assertEqual(code, 0)

    def test_ctrl_c_reaches_remote(self):
        t = time.monotonic()
        code, out = run_pty([EXEC, "--", "sleep 60"], feed=[(6, b"\x03")])
        self.assertLess(time.monotonic() - t, 30)
        self.assertEqual(code, 130)

    def test_detach_keeps_task_running(self):
        name = f"task-test-detach-{os.getpid()}"
        code, out = run_pty([EXEC, "--name", name, "--", "sleep 45"], feed=[(6, b"\x02d")])
        try:
            self.assertEqual(code, 0)
            self.assertIn(f"twin attach {name}", out)
            alive = subprocess.run(["ssh", "twin", f"tmux has-session -t {name}"]).returncode
            self.assertEqual(alive, 0)
        finally:
            subprocess.run(["ssh", "twin", f"tmux kill-session -t {name}"], stderr=subprocess.DEVNULL)

    def test_usage_error(self):
        p = subprocess.run([EXEC, "--"], capture_output=True, text=True)
        self.assertEqual(p.returncode, 2)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd ~/projects/twinPC && python3 -m unittest tests.test_twin_exec -v`
Expected: errors — `route/twin-exec` does not exist.

- [ ] **Step 3: Implement `twin-exec`**

`route/twin-exec` (then `chmod +x`):

```bash
#!/usr/bin/env bash
# twin-exec — run a command line on the twin PC inside a tmux session: live in this terminal
# (Ctrl-C works, the exit code comes back) and visible in a window on twin's workspace 9.
# Used by the twin-route bash hook.   twin-exec [--name NAME] -- <command line>
set -uo pipefail
HOST=${TWIN_HOST:-twin}
HERE=$(dirname "$(readlink -f "$0")")
TWIN=$(command -v twin || echo "$HERE/../twin")
REMOTE_ENV='export HSA_OVERRIDE_GFX_VERSION=10.3.0 PATH=$HOME/ai-env/bin:$HOME/.local/share/mise/shims:$HOME/.local/bin:$PATH'

name=""
while [[ $# -gt 0 ]]; do
  case $1 in
    --name) name=$2; shift 2 ;;
    --) shift; break ;;
    *) break ;;
  esac
done
line="$*"
[[ -n ${line//[[:space:]]/} ]] || { echo "usage: twin-exec [--name NAME] -- <command line>" >&2; exit 2; }

q() { printf '%q' "$1"; }
rdir=$("$HERE/twin-route" --map-cwd "$PWD")                  # "~/work/<rel>" or "~"
first=${line%%[[:space:]]*}; first=${first##*/}; first=${first//[^A-Za-z0-9_-]/}
session=${name:-task-$(date +%m%d-%H%M%S)-${first:-cmd}}
dir_expr='$HOME'; [[ $rdir != "~" ]] && dir_expr="\$HOME/$(q "${rdir#\~/}")"

envs=""
for v in TERM LANG COLORTERM ${TWIN_ROUTE_ENV_ALLOW:-}; do
  [[ -n ${!v:-} ]] && envs+="export $v=$(q "${!v}"); "
done

# runs inside the tmux pane on twin: log the pane, run the line as typed, record the exit code.
# The newline after $line keeps a trailing "# comment" or "&" from swallowing the bookkeeping.
inner="$REMOTE_ENV; $envs mkdir -p \$HOME/jobs; tmux pipe-pane -o $(q "cat >> \$HOME/jobs/$session.log"); cd $dir_expr || exit 1
$line
rc=\$?; echo \"[exit \$rc]\" >> \$HOME/jobs/$session.log; echo \$rc > \$HOME/jobs/$session.exit; exit \$rc"

start=$(date +%s)
"$TWIN" viewer "$session" >/dev/null 2>&1 &
if [[ -t 0 && -t 1 ]]; then
  # this terminal creates (and is attached to) the session, so even instant output is seen
  ssh -t "$HOST" "tmux new-session -s $(q "$session") bash -c $(q "$inner")"
  ssh_rc=$?
else
  ssh "$HOST" "tmux new-session -d -s $(q "$session") bash -c $(q "$inner")
    log=\$HOME/jobs/$session.log; ex=\$HOME/jobs/$session.exit
    for _ in \$(seq 50); do [ -f \$log ] && break; sleep 0.1; done
    tail -n +1 -f \$log & t=\$!
    while [ ! -f \$ex ]; do sleep 0.3; done; sleep 0.3; kill \$t"
  ssh_rc=$?
fi

if [[ $ssh_rc -eq 255 ]]; then
  echo "twin connection lost — task keeps running on twin (twin attach $session)" >&2
  exit 255
fi
rc=$(ssh "$HOST" "cat \$HOME/jobs/$session.exit 2>/dev/null")
if [[ -z $rc ]]; then
  echo "detached — '$session' keeps running on twin (twin attach $session · twin logs $session)"
  exit 0
fi
elapsed=$(( $(date +%s) - start ))
if (( elapsed > 60 )) && command -v notify-send >/dev/null; then
  notify-send "twin: $first finished (exit $rc)" "after ${elapsed}s — $line"
fi
exit "$rc"
```

Note for the non-TTY branch: the `tail` output includes the final `[exit N]` line from the log; that is intended (same format as `twin logs`).

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd ~/projects/twinPC && python3 -m unittest tests.test_twin_exec -v`
Expected: 7 tests PASS (≈ 1–2 minutes; twin must be up). If `test_ctrl_c_reaches_remote` returns 0 instead of 130, the pane shell swallowed SIGINT — confirm `$line` runs in the foreground of `bash -c` (it does above) and that the test's pty is the controlling terminal.

- [ ] **Step 5: Commit**

```bash
git add route/twin-exec tests/test_twin_exec.py
git commit -m "feat(route): add twin-exec runner (tmux on twin, live output, exit codes)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Bash hook

**Files:**
- Create: `route/twin-route.bash`
- Test: `tests/test_hook.py`

**Interfaces:**
- Consumes: `route/twin-route` CLI (Task 1); `twin-exec` on PATH (Task 4); `twin wake` (existing).
- Produces: sourcing `route/twin-route.bash` in an interactive bash binds Enter; variables `_TR_ORIG`, `_TR_ADD`; functions `_twin_route_enter`, `_twin_route_history`, `_twin_route_stage`, `_twin_route_mounted`; env overrides `TWIN_ROUTE=off`, `TWIN_ROUTE_STAGE=up|unlock|off` (tests), `TWIN_ROUTE_MOUNT_CHECK=<cmd>` (tests; default `findmnt -rn -M $HOME/twin`); disable flag `${XDG_CONFIG_HOME:-~/.config}/twin-route/disabled`.

- [ ] **Step 1: Write the failing tests**

`tests/test_hook.py`:

```python
import os
import pty
import re
import select
import shutil
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "route" / "twin-route.bash"
STUBS = {
    "twin-exec": 'echo "STUB-TWIN-EXEC $*"',
    "ollama": 'echo "LOCAL-OLLAMA $*"',
    "twin": 'echo "STUB-TWIN $*"',
    "make": 'echo "LOCAL-MAKE $*"',
}


class Shell:
    """An interactive bash in a pty with the hook sourced and stub commands first on PATH."""

    def __init__(self, stage="up", mounted=True, cwd=None, home=None):
        self.tmp = tempfile.mkdtemp()
        stubs = Path(self.tmp, "bin"); stubs.mkdir()
        for name, body in STUBS.items():
            p = stubs / name
            p.write_text(f"#!/usr/bin/env bash\n{body}\n"); p.chmod(0o755)
        rc = Path(self.tmp, "rc")
        rc.write_text(f"PS1='PROMPT> '\nHISTFILE={self.tmp}/hist\nsource {HOOK}\n")
        runtime = Path(self.tmp, "run"); runtime.mkdir()
        (runtime / "twin-route.load").write_text("5\n")
        env = dict(os.environ, HOME=home or self.tmp, XDG_RUNTIME_DIR=str(runtime),
                   XDG_CONFIG_HOME=f"{self.tmp}/cfg", PATH=f"{stubs}:{os.environ['PATH']}",
                   TWIN_ROUTE_STAGE=stage, TWIN_ROUTE_MOUNT_CHECK="true" if mounted else "false",
                   TERM="xterm")
        env.pop("TWIN_ROUTE", None)
        self.pid, self.fd = pty.fork()
        if self.pid == 0:
            os.chdir(cwd or self.tmp)
            os.execve("/bin/bash", ["bash", "--noprofile", "--rcfile", str(rc), "-i"], env)
        self.read_until("PROMPT> ")

    def read_until(self, marker, timeout=8):
        out, start = "", time.monotonic()
        while marker not in out and time.monotonic() - start < timeout:
            r, _, _ = select.select([self.fd], [], [], 0.1)
            if r:
                out += os.read(self.fd, 4096).decode(errors="replace")
        return out

    def run(self, line, keys=b""):
        os.write(self.fd, line.encode() + b"\r")
        if keys:
            time.sleep(0.8)
            os.write(self.fd, keys)
        return self.read_until("PROMPT> ")

    def close(self):
        os.write(self.fd, b"exit\r")
        try:
            os.waitpid(self.pid, 0)
        finally:
            shutil.rmtree(self.tmp, ignore_errors=True)


def history(sh):
    out = sh.run("history 4")
    return re.findall(r"^\s*\d+\s+(.*?)\r?$", out, re.M)


class HookTests(unittest.TestCase):
    def test_gpu_command_goes_to_twin(self):
        sh = Shell()
        try:
            out = sh.run("ollama run gemma3:4b hi")
            self.assertIn("→ twin (always:ollama)", out)
            self.assertIn("STUB-TWIN-EXEC ollama run gemma3:4b hi", out)
        finally:
            sh.close()

    def test_plain_command_runs_here(self):
        sh = Shell()
        try:
            out = sh.run("echo plain-local")
            self.assertIn("plain-local", out)
            self.assertNotIn("STUB-TWIN-EXEC", out)
        finally:
            sh.close()

    def test_history_shows_what_was_typed(self):
        sh = Shell()
        try:
            sh.run("ollama run gemma3:4b hi")
            h = history(sh)
            self.assertIn("ollama run gemma3:4b hi", h)
            self.assertFalse(any("twin-exec" in x for x in h))
        finally:
            sh.close()

    def test_local_prefix_forces_here(self):
        sh = Shell()
        try:
            out = sh.run("  local ollama run x")
            self.assertIn("LOCAL-OLLAMA run x", out)
            self.assertNotIn("STUB-TWIN-EXEC", out)
        finally:
            sh.close()

    def test_twin_down_here_runs_locally(self):
        sh = Shell(stage="off")
        try:
            out = sh.run("ollama run y", keys=b"h")
            self.assertIn("twin is off", out)
            self.assertIn("LOCAL-OLLAMA run y", out)
        finally:
            sh.close()

    def test_twin_down_wake_then_exec(self):
        sh = Shell(stage="off")
        try:
            out = sh.run("ollama run y", keys=b"w")
            self.assertIn("STUB-TWIN wake", out)
            self.assertIn("STUB-TWIN-EXEC ollama run y", out)
        finally:
            sh.close()

    def test_cancel_keeps_history_intact(self):
        sh = Shell(stage="off")
        try:
            sh.run("echo first-entry")
            out = sh.run("ollama run z", keys=b"c")
            self.assertNotIn("LOCAL-OLLAMA", out)
            self.assertNotIn("STUB-TWIN-EXEC", out)
            h = history(sh)
            self.assertIn("echo first-entry", h)
            self.assertIn("ollama run z", h)
        finally:
            sh.close()

    def test_mount_down_refuses_without_running(self):
        home = tempfile.mkdtemp()
        try:
            ws = Path(home, "twin", "proj"); ws.mkdir(parents=True)
            sh = Shell(mounted=False, cwd=str(ws), home=home)
            try:
                out = sh.run("make build")
                self.assertIn("~/twin is not mounted", out)
                self.assertNotIn("STUB-TWIN-EXEC", out)
                self.assertNotIn("LOCAL-MAKE", out)
            finally:
                sh.close()
        finally:
            shutil.rmtree(home, ignore_errors=True)

    def test_kill_switch(self):
        sh = Shell()
        try:
            sh.run("export TWIN_ROUTE=off")
            out = sh.run("ollama run k")
            self.assertIn("LOCAL-OLLAMA run k", out)
        finally:
            sh.close()


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd ~/projects/twinPC && python3 -m unittest tests.test_hook -v`
Expected: failures — `source: …/route/twin-route.bash: No such file or directory`, no `→ twin` markers.

- [ ] **Step 3: Implement the hook**

`route/twin-route.bash`:

```bash
# twin-route.bash — send commands typed in this terminal to the twin PC when the rules say so.
# Sourced at the end of ~/.bashrc. Rules: ~/.config/twin-route/rules.toml · off: twin route off
# Design: ~/projects/twinPC/docs/superpowers/specs/2026-09-23-twin-task-routing-design.md
[[ $- == *i* ]] || return 0

_TR_DIR=$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")
_TR_ORIG=""   # typed line whose rewritten form must be replaced in history
_TR_ADD=""    # typed line to add to history when nothing was executed (cancel / refused)

# twin's state (up|unlock|off) from the SSH banner, cached 5 s; TWIN_ROUTE_STAGE overrides (tests)
_twin_route_stage() {
  if [[ -n ${TWIN_ROUTE_STAGE:-} ]]; then echo "$TWIN_ROUTE_STAGE"; return; fi
  local cache=${XDG_RUNTIME_DIR:-/tmp}/twin-route.stage now t st b
  printf -v now '%(%s)T' -1
  if [[ -f $cache ]] && read -r t st < "$cache" && (( now - t < 5 )); then echo "$st"; return; fi
  b=$(timeout 1.5 bash -c 'exec 3<>/dev/tcp/10.42.0.11/22 && head -c 64 <&3' 2>/dev/null | tr -d '\r' | head -1)
  case $b in *dropbear*) st=unlock ;; SSH-*) st=up ;; *) st=off ;; esac
  echo "$now $st" > "$cache"; echo "$st"
}

# is ~/twin mounted? reads the mount table only — never stats the (possibly dead) mount
_twin_route_mounted() { ${TWIN_ROUTE_MOUNT_CHECK:-findmnt -rn -M "$HOME/twin"} >/dev/null 2>&1; }

_twin_route_enter() {
  _TR_ORIG=""; _TR_ADD=""
  local line=$READLINE_LINE out decision reason st key ql
  [[ -n ${line//[[:space:]]/} ]] || return 0
  [[ ${TWIN_ROUTE:-on} != off && ! -e ${XDG_CONFIG_HOME:-$HOME/.config}/twin-route/disabled ]] || return 0
  out=$(timeout 0.3 "$_TR_DIR/twin-route" --cwd "$PWD" -- "$line" 2>/dev/null) || return 0   # fail-safe
  decision=${out%% *}; reason=${out#* }

  if [[ $decision != twin ]]; then
    if [[ $reason == forced && $line =~ ^[[:space:]]*local[[:space:]]+(.*)$ ]]; then
      READLINE_LINE=${BASH_REMATCH[1]}; _TR_ORIG=$line
    elif [[ $reason == files-not-on-twin ]]; then
      printf '\e[2m→ here (needs files in %s — move the project into ~/twin to run it on twin)\e[0m\n' "$PWD"
    fi
    return 0
  fi

  if [[ $reason == workspace ]] && ! _twin_route_mounted; then
    printf '\e[33m~/twin is not mounted (twin off?) — twin wake · systemctl --user restart twin-mount\e[0m\n'
    READLINE_LINE=""; _TR_ADD=$line; return 0
  fi

  printf -v ql '%q' "$line"
  st=$(_twin_route_stage)
  if [[ $st != up ]]; then
    printf '\ntwin is %s — [h]ere / [w]ake twin / [c]ancel? ' "$st"
    read -rsn1 key </dev/tty; echo
    case $key in
      h|H) return 0 ;;
      w|W) READLINE_LINE="twin wake && twin-exec -- $ql" ;;
      *)   READLINE_LINE=""; _TR_ADD=$line; return 0 ;;
    esac
  else
    READLINE_LINE="twin-exec -- $ql"
  fi
  _TR_ORIG=$line
  printf '\e[2m→ twin (%s)\e[0m\n' "$reason"
}

# runs before the existing history -a/-c/-r in PROMPT_COMMAND: history shows what was typed
_twin_route_history() {
  if [[ -n $_TR_ORIG ]]; then history -d -1 2>/dev/null; history -s -- "$_TR_ORIG"; fi
  if [[ -n $_TR_ADD ]]; then history -s -- "$_TR_ADD"; fi
  _TR_ORIG=""; _TR_ADD=""
}

# Enter = rewrite (bind -x) then accept-line; bind -x alone cannot accept a line
bind -x '"\C-x\C-t": _twin_route_enter'
bind '"\C-x\C-a": accept-line'
bind '"\C-m": "\C-x\C-t\C-x\C-a"'
bind '"\C-j": "\C-x\C-t\C-x\C-a"'
[[ $PROMPT_COMMAND == *_twin_route_history* ]] || PROMPT_COMMAND="_twin_route_history${PROMPT_COMMAND:+; $PROMPT_COMMAND}"
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd ~/projects/twinPC && python3 -m unittest tests.test_hook -v`
Expected: 9 tests PASS. If `history -s -- "…"` records a literal `--` entry on this bash, change both calls to `history -s "$…"` and re-run.

- [ ] **Step 5: Commit**

```bash
git add route/twin-route.bash tests/test_hook.py
git commit -m "feat(route): add bash hook that routes typed commands to twin on Enter

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Workspace mount

**Files:**
- Create: `route/twin-mount.service`
- Modify: `twin` — `link stop` unmounts before powering twin off
- Test: `tests/test_mount.sh`

**Interfaces:**
- Consumes: ssh alias `twin`; `sshfs` package (the user installs it: `sudo apt install sshfs`).
- Produces: twin `~/work` mounted at `~/twin` via `twin-mount.service` (user unit); `findmnt -rn -M ~/twin` succeeds while mounted (used by Task 5's `_twin_route_mounted`).

- [ ] **Step 1: Prerequisites**

Ask the user to run `sudo apt install sshfs` on the main PC (it needs their password). Then create the remote folder: `ssh twin 'mkdir -p ~/work'`.

- [ ] **Step 2: Write the failing test**

`tests/test_mount.sh`:

```bash
#!/usr/bin/env bash
# A file written on twin in ~/work appears under ~/twin on this PC.
set -u
f=mount-test-$$
ssh twin "echo hello-mount > ~/work/$f"
sleep 1
got=$(timeout 5 cat "$HOME/twin/$f" 2>/dev/null)
ssh twin "rm -f ~/work/$f"
mounted=no; findmnt -rn -M "$HOME/twin" >/dev/null && mounted=yes
if [[ $mounted == yes && $got == hello-mount ]]; then echo PASS; else echo "FAIL (mounted: $mounted, got '$got')"; exit 1; fi
```

- [ ] **Step 3: Run it to verify it fails**

Run: `bash tests/test_mount.sh`
Expected: `FAIL (mounted: no, got '')`.

- [ ] **Step 4: Add the mount unit and enable it**

`route/twin-mount.service`:

```ini
[Unit]
Description=Mount the twin PC's ~/work at ~/twin (sshfs over the LAN cable)
After=twin-link.service

[Service]
Type=simple
ExecStartPre=/bin/mkdir -p %h/twin
ExecStart=/usr/bin/sshfs -f -o reconnect,ServerAliveInterval=15,ServerAliveCountMax=3,idmap=user,follow_symlinks,ControlMaster=no,ControlPath=none twin:work %h/twin
ExecStop=/bin/fusermount3 -u %h/twin
Restart=on-failure
RestartSec=20

[Install]
WantedBy=default.target
```

Enable:

```bash
cp route/twin-mount.service ~/.config/systemd/user/
systemctl --user daemon-reload && systemctl --user enable --now twin-mount.service
```

- [ ] **Step 5: Unmount before twin powers off**

In `twin`, in `link)` → `stop)`, insert as the first line of that branch (before `[[ $(stage) == up ]] || exit 0`):

```bash
        systemctl --user stop twin-mount.service 2>/dev/null || true   # don't leave a dead sshfs mount behind
```

(The mount service retries every 20 s while twin is down and reconnects once twin is up again.)

- [ ] **Step 6: Run the test to verify it passes**

Run: `bash tests/test_mount.sh`
Expected: `PASS`

- [ ] **Step 7: Commit**

```bash
git add route/twin-mount.service twin tests/test_mount.sh
git commit -m "feat(route): mount twin ~/work at ~/twin via sshfs user service

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: CLI control, install and docs

**Files:**
- Modify: `twin` — `route` subcommand, usage text
- Modify: `twin-completion.bash` — add `route` and `viewer`
- Create: `route/install.sh`
- Modify: `man/twin.1` — TASK ROUTING section
- Modify: `~/.bashrc` (via install.sh) — source line
- Test: `tests/test_cli_route.sh`

**Interfaces:**
- Consumes: everything above.
- Produces: `twin route on|off|status|explain <command>`; an installed system (symlinks `~/.local/bin/twin-route`, `~/.local/bin/twin-exec`; `~/.config/twin-route/rules.toml`; `twin-route-load` + `twin-mount` services enabled; `twin-task-view` on twin; `.bashrc` sources the hook).

- [ ] **Step 1: Write the failing test**

`tests/test_cli_route.sh`:

```bash
#!/usr/bin/env bash
set -u
ROOT=$(cd "$(dirname "$0")/.." && pwd); T="$ROOT/twin"
XDG_CONFIG_HOME=$(mktemp -d); export XDG_CONFIG_HOME
trap 'rm -rf "$XDG_CONFIG_HOME"' EXIT
fail=0
"$T" route off >/dev/null; "$T" route status | grep -q "routing: OFF" || { echo "FAIL off"; fail=1; }
"$T" route on  >/dev/null; "$T" route status | grep -q "routing: ON"  || { echo "FAIL on"; fail=1; }
"$T" route explain ollama run x | head -1 | grep -q "^twin always:ollama" || { echo "FAIL explain"; fail=1; }
(( fail == 0 )) && echo PASS
exit $fail
```

- [ ] **Step 2: Run it to verify it fails**

Run: `bash tests/test_cli_route.sh`
Expected: `unknown command: route` and `FAIL off` / `FAIL on` / `FAIL explain`.

- [ ] **Step 3: Add `twin route`**

In `twin`, insert before the `  viewer)` case:

```bash
  route)
    flag=${XDG_CONFIG_HOME:-$HOME/.config}/twin-route/disabled
    case ${1:-status} in
      on)  rm -f "$flag"; echo "task routing ON (applies to the next command in every terminal)" ;;
      off) mkdir -p "$(dirname "$flag")"; touch "$flag"; echo "task routing OFF" ;;
      status)
        if [[ -e $flag ]]; then echo "task routing: OFF"; else echo "task routing: ON"; fi
        echo "rules: ${XDG_CONFIG_HOME:-$HOME/.config}/twin-route/rules.toml"
        echo "~/twin mount: $(findmnt -rn -M "$HOME/twin" >/dev/null && echo mounted || echo 'not mounted')"
        echo "this PC's CPU (1-min avg): $(cat "${XDG_RUNTIME_DIR:-/tmp}/twin-route.load" 2>/dev/null || echo n/a)%"
        ;;
      explain) shift; "$(dirname "$(readlink -f "$0")")/route/twin-route" --cwd "$PWD" --explain -- "$*" ;;
      *) echo "usage: twin route on|off|status|explain <command>"; exit 1 ;;
    esac
    ;;
```

In `usage()`, after the `twin viewer` line add:

```
  twin route [on|off|status]   automatic task routing from this terminal to twin (twin route explain <cmd>)
```

In `twin-completion.bash`, add `route viewer` to the `cmds` list.

- [ ] **Step 4: Run the test to verify it passes**

Run: `bash tests/test_cli_route.sh`
Expected: `PASS`

- [ ] **Step 5: Write the installer**

`route/install.sh` (then `chmod +x`):

```bash
#!/usr/bin/env bash
# install.sh — wire twin task routing into this PC and the twin (safe to run again).
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"
mkdir -p ~/.local/bin ~/.config/twin-route ~/.config/systemd/user
ln -sf "$PWD/twin-route" ~/.local/bin/twin-route
ln -sf "$PWD/twin-exec" ~/.local/bin/twin-exec
[[ -f ~/.config/twin-route/rules.toml ]] || cp rules.default.toml ~/.config/twin-route/rules.toml
cp twin-route-load.service twin-mount.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now twin-route-load.service
if command -v sshfs >/dev/null; then
  systemctl --user enable --now twin-mount.service
else
  echo "sshfs is not installed — run: sudo apt install sshfs   then run this installer again"
fi
scp -q twin-task-view twin:.local/bin/twin-task-view && ssh twin 'chmod +x ~/.local/bin/twin-task-view; mkdir -p ~/work'
src='[ -f ~/projects/twinPC/route/twin-route.bash ] && . ~/projects/twinPC/route/twin-route.bash'
grep -qF "$src" ~/.bashrc || printf '\n# route GPU/AI and heavy commands to the twin PC (twin route off to disable)\n%s\n' "$src" >> ~/.bashrc
echo "installed — open a new terminal; 'twin route status' shows the state"
```

- [ ] **Step 6: Run the installer and check its effects**

Run: `bash route/install.sh && systemctl --user is-active twin-route-load twin-mount && tail -2 ~/.bashrc && ssh twin 'ls -l ~/.local/bin/twin-task-view'`
Expected: `installed — …`, `active` twice, the source line at the end of `.bashrc`, the viewer present on twin.

- [ ] **Step 7: Document in the man page**

In `man/twin.1`, insert before `.SH POWER`:

```
.SH TASK ROUTING
Commands typed in a bash terminal on this PC are checked when you press Enter.
GPU/AI work (ollama, *train*.py, whisper, torchrun, claude -p, …), anything run inside
.I ~/twin
(the twin's
.I ~/work
mounted here) and \(em only while this PC is busy (CPU \(>= 80 % or RAM \(>= 85 %) \(em
CPU-heavy commands run on the twin instead. A dim
.B \(-> twin (reason)
line shows it happened; output, Ctrl-C and the exit code behave as if it ran here, and
history keeps what you typed. Each task also opens a terminal window on the twin's
workspace 9 (closes 30 s after success, stays open on failure). Ctrl-b d detaches: the
task keeps running on the twin
.RB ( "twin ls" ", " "twin attach" ).
If the twin is not up you are asked: [h]ere / [w]ake twin / [c]ancel.
.PP
Rules:
.I ~/.config/twin-route/rules.toml
(always_twin, never_twin, load_offload, needs_cwd, thresholds). Commands that need files
from a folder that only exists on this PC stay here.
.B local
.I cmd
forces this PC;
.B twin-exec --
.I cmd
forces the twin;
.B twin route explain
.I cmd
shows the decision;
.B twin route off
/
.B on
(or
.B TWIN_ROUTE=off
for one shell) switches it.
```

- [ ] **Step 8: Commit**

```bash
git add twin twin-completion.bash route/install.sh man/twin.1 tests/test_cli_route.sh
git commit -m "feat(twin): route subcommand, installer and TASK ROUTING docs

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

- [ ] **Step 9: Manual acceptance with the user (new terminal)**

1. `ollama run gemma3:4b "say hi"` → dim `→ twin (always:ollama)`, the answer streams, a window appears on twin workspace 9 and closes 30 s later.
2. `twin route explain make` in `~/projects/twinPC` → `local files-not-on-twin`; in `~/twin/<proj>` → `twin workspace`.
3. `git status` inside `~/twin/<proj>` → runs here on the mounted files.
4. `twin off`, then `ollama run gemma3:4b hi` → `twin is off — [h]ere / [w]ake twin / [c]ancel?`; `c` → nothing runs and history contains the line.
5. `history 5` shows the commands as typed (no `twin-exec`).
