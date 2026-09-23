import os
import pty
import select
import subprocess
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXEC = str(ROOT / "route" / "twin-exec")
os.environ["TWIN_EXEC_VIEWER"] = "0"          # viewer windows are covered by tests/test_viewer.sh


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
