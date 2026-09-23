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
    "twin-exec": '[[ $1 == -- ]] && shift; echo "STUB-TWIN-EXEC $*"',
    "ollama": 'echo "LOCAL-OLLAMA $*"',
    "twin": 'echo "STUB-TWIN $*"',
    "make": 'echo "LOCAL-MAKE $*"',
}


class Shell:
    """An interactive bash in a pty with the hook sourced and stub commands first on PATH."""

    def __init__(self, stage="up", mounted=True, cwd=None, home=None, pre_rc=""):
        self.tmp = tempfile.mkdtemp()
        stubs = Path(self.tmp, "bin"); stubs.mkdir()
        for name, body in STUBS.items():
            p = stubs / name
            p.write_text(f"#!/usr/bin/env bash\n{body}\n"); p.chmod(0o755)
        rc = Path(self.tmp, "rc")
        rc.write_text(f"PS1='PROMPT> '\nHISTFILE={self.tmp}/hist\nHISTCONTROL=ignorespace\n{pre_rc}\nsource {HOOK}\n")
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
        """Type line (+ keys for a prompt), then read until a sentinel command's output shows it finished.
        (The hook's marker makes readline redraw "PROMPT> line", so the prompt alone is no signal.)"""
        self.n = getattr(self, "n", 0) + 1
        os.write(self.fd, line.encode() + b"\r")
        if keys:
            time.sleep(0.8)
            os.write(self.fd, keys)
        time.sleep(0.3)
        # typed as __E""ND so only the command's *output* contains __END<n>__; leading space: not in history
        os.write(self.fd, f' echo __E""ND{self.n}__\r'.encode())
        return self.read_until(f"__END{self.n}__")

    def close(self):
        os.write(self.fd, b"\x15exit\r")          # Ctrl-U first: never append "exit" to stray input
        try:
            for _ in range(50):
                if os.waitpid(self.pid, os.WNOHANG)[0]:
                    break
                time.sleep(0.1)
            else:
                os.kill(self.pid, 9)
                os.waitpid(self.pid, 0)
        finally:
            shutil.rmtree(self.tmp, ignore_errors=True)


ANSI = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b\[[0-9;?]*[A-Za-z]")   # OSC and CSI sequences


def history(sh):
    out = ANSI.sub("", sh.run("history 12"))
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

    # --- Enter-key safety: the hook runs on every line and must not change shell state
    def test_exit_status_reaches_later_prompt_hooks(self):
        # starship's precmd runs after the hook in PROMPT_COMMAND and must still see the real $?
        sh = Shell(pre_rc='_probe(){ echo "PROBE-RC=$?"; }; PROMPT_COMMAND=_probe')
        try:
            self.assertIn("PROBE-RC=1", sh.run("false"))
        finally:
            sh.close()

    def test_last_argument_is_kept(self):
        sh = Shell()
        try:
            self.assertIn("U=lastarg-x", sh.run("echo lastarg-x\recho U=$_"))
        finally:
            sh.close()

    def workspace_shell(self, **kw):
        home = tempfile.mkdtemp()
        ws = Path(home, "twin", "proj"); ws.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, home, True)
        return Shell(cwd=str(ws), home=home, **kw), ws

    def test_incomplete_line_in_workspace_runs_here(self):
        sh, _ = self.workspace_shell()
        try:
            out = sh.run("for x in 1 2; do\recho LOOP-$x\rdone")
            self.assertIn("LOOP-1", out)
            self.assertIn("LOOP-2", out)
            self.assertNotIn("STUB-TWIN-EXEC", out)
        finally:
            sh.close()

    def test_shell_function_and_builtin_in_workspace_run_here(self):
        sh, ws = self.workspace_shell(pre_rc='myfn(){ echo FN-LOCAL; }')
        try:
            out = sh.run("myfn")
            self.assertIn("FN-LOCAL", out)
            self.assertNotIn("STUB-TWIN-EXEC", out)
            out = sh.run("pwd")
            self.assertIn(str(ws), out)
            self.assertNotIn("STUB-TWIN-EXEC", out)
        finally:
            sh.close()

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
