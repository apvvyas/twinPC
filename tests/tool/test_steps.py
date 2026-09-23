import hashlib
import unittest

from tests.tool.fakes import FakeRunner
from twinpc_lib import steps as S


def ctx(runner, **kw):
    return S.Ctx(profile={}, runner=runner, repo="/repo", **kw)


class HelperTests(unittest.TestCase):
    def test_cmd_step_check_and_apply(self):
        r = FakeRunner([("twin", "is-it-done", 0, "")])
        st = S.cmd_step("f.twin.x", "f", "twin", "do x", check="is-it-done", apply="do-it", root=True, check_root=False)
        self.assertTrue(st.check(ctx(r)))
        self.assertEqual(r.calls[-1], ("twin", "is-it-done", False, None))
        with self.assertRaises(S.StepError):
            st.apply(ctx(r))                       # "do-it" has no response → rc 1
        self.assertEqual(r.calls[-1][2], True)     # apply ran as root

    def test_file_step_compares_hash_and_writes_content(self):
        content = "hello\n"
        digest = hashlib.sha256(content.encode()).hexdigest()
        st = S.file_step("f.main.file", "f", "main", "$HOME/x.conf", content, mode="600", after="echo after")
        self.assertTrue(st.check(ctx(FakeRunner([("main", "sha256sum", 0, digest)]))))
        self.assertFalse(st.check(ctx(FakeRunner([("main", "sha256sum", 0, "other")]))))
        r = FakeRunner([("main", "cat >", 0, "")])
        st.apply(ctx(r))
        machine, cmd, root, data = r.calls[-1]
        self.assertIn('cat > "$HOME/x.conf"', cmd)
        self.assertIn('chmod 600 "$HOME/x.conf"', cmd)
        self.assertIn("echo after", cmd)
        self.assertEqual(data, content)

    def test_file_step_content_can_depend_on_ctx(self):
        st = S.file_step("f.main.file", "f", "main", "/x", lambda c: c.profile["v"])
        c = S.Ctx(profile={"v": "abc"}, runner=FakeRunner([("main", "cat >", 0, "")]), repo="/r")
        st.apply(c)
        self.assertEqual(c.runner.calls[-1][3], "abc")

    def test_line_step(self):
        st = S.line_step("f.main.line", "f", "main", "~/.bashrc", "source x", match="x")
        r = FakeRunner()
        st.check(ctx(r))
        self.assertIn("grep -qF -- x ~/.bashrc", r.calls[-1][1])
        st.apply(ctx(FakeRunner([("main", "printf", 0, "")])))

    def test_unit_step_user_and_system(self):
        r = FakeRunner([("main", "is-enabled", 0, "")])
        u = S.unit_step("f.main.u", "f", "main", "a.service")
        u.check(ctx(r))
        self.assertIn("systemctl --user is-enabled --quiet a.service", r.calls[-1][1])
        s = S.unit_step("f.twin.s", "f", "twin", "b", user=False, now=False)
        self.assertTrue(s.root)
        r2 = FakeRunner([("twin", "enable", 0, "")])
        s.apply(ctx(r2))
        self.assertEqual(r2.calls[-1][1], "systemctl daemon-reload && systemctl enable b")


class RunStepsTests(unittest.TestCase):
    def step(self, id, root=False):
        return S.cmd_step(id, "f", "main", f"do {id}", check=f"check-{id}", apply=f"apply-{id}", root=root)

    def test_done_steps_are_skipped_and_others_applied(self):
        r = FakeRunner([("main", "check-a", 0, ""), ("main", "apply-b", 0, "")])
        # after apply-b, check-b must pass: make it pass on the second call
        calls = {"n": 0}
        orig = r.run

        def run(machine, cmd, root=False, input=None):
            if cmd == "check-b":
                calls["n"] += 1
                return S.Result(0 if calls["n"] > 1 else 1, "")
            return orig(machine, cmd, root, input)
        r.run = run
        lines = []
        rc = S.run_steps([self.step("a"), self.step("b")], ctx(r), out=lines.append)
        self.assertEqual(rc, 0)
        self.assertIn("✓  a: already done", "\n".join(lines))
        self.assertIn("✓  b: done", "\n".join(lines))

    def test_stops_at_first_failure(self):
        r = FakeRunner([])                          # every check and apply fails
        lines = []
        rc = S.run_steps([self.step("a"), self.step("b")], ctx(r), out=lines.append)
        self.assertEqual(rc, 1)
        self.assertTrue(any("✗  a:" in l for l in lines))
        self.assertFalse(any(" b:" in l for l in lines))

    def test_dry_run_changes_nothing(self):
        r = FakeRunner([("main", "check-a", 0, "")])
        lines = []
        rc = S.run_steps([self.step("a"), self.step("b", root=True)], ctx(r), dry_run=True, out=lines.append)
        self.assertEqual(rc, 0)
        self.assertFalse(any(c[1].startswith("apply-") for c in r.calls))
        self.assertIn("→  b: would do b [sudo]", "\n".join(lines))

    def test_skip_reason(self):
        lines = []
        rc = S.run_steps([S.unsupported_step("kvm", "twin", "desktop 'kde' is not supported yet (planned)")],
                         ctx(FakeRunner()), out=lines.append)
        self.assertEqual(rc, 0)
        self.assertIn("⏭  kvm.twin.unsupported: skipped — desktop 'kde' is not supported yet (planned)", lines)

    def test_manual_step_needs_confirmation(self):
        m = S.manual_step("f.twin.bios", "f", "twin", "enable WoL in BIOS", "Open the BIOS …", check="bios-ok")
        lines = []
        self.assertEqual(S.run_steps([m], ctx(FakeRunner(), confirm=lambda q: False), out=lines.append), 1)
        r = FakeRunner()
        calls = {"n": 0}

        def run(machine, cmd, root=False, input=None):
            calls["n"] += 1
            return S.Result(0 if calls["n"] > 1 else 1, "")
        r.run = run
        self.assertEqual(S.run_steps([m], ctx(r, confirm=lambda q: True), out=lines.append), 0)

    def test_no_root_checks_are_reported_not_run(self):
        st = S.cmd_step("f.twin.r", "f", "twin", "root thing", check="root-check", apply="x", root=True)
        r = FakeRunner([("twin", "root-check", 0, "")])
        lines = []
        rc = S.run_steps([st], ctx(r, allow_root=False), dry_run=True, out=lines.append)
        self.assertEqual(rc, 0)
        self.assertEqual(r.calls, [])
        self.assertIn("?  f.twin.r: needs sudo to check", lines)


class RunnerTests(unittest.TestCase):
    def test_root_without_terminal_stops_clearly(self):
        class R(S.Runner):
            def _exec(self, argv, data):
                return S.Result(1, "sudo: a password is required")   # sudo -n true fails
        runner = R("twin", ask=lambda p: "pw", isatty=lambda: False)
        with self.assertRaisesRegex(S.RootNeedsTerminal, "needs a terminal"):
            runner.run("twin", "whoami", root=True)

    def test_password_asked_once_per_machine(self):
        asked = []
        seen = []

        class R(S.Runner):
            def _exec(self, argv, data):
                seen.append((argv, data))
                return S.Result(1, "") if argv[-1].endswith("sudo -n true") else S.Result(0, "ok")
        runner = R("twin", ask=lambda p: asked.append(p) or "pw", isatty=lambda: True)
        runner.run("twin", "a", root=True)
        runner.run("twin", "b", root=True)
        runner.run("main", "c", root=True)
        self.assertEqual(len(asked), 2)                         # once for twin, once for main
        self.assertTrue(seen[-1][1].startswith("pw\n"))

    def test_twin_commands_go_over_ssh(self):
        seen = []

        class R(S.Runner):
            def _exec(self, argv, data):
                seen.append(argv)
                return S.Result(0, "")
        R("mytwin").run("twin", "echo hi")
        self.assertEqual(seen[0][:4], ["ssh", "-o", "BatchMode=yes", "mytwin"])


if __name__ == "__main__":
    unittest.main()
