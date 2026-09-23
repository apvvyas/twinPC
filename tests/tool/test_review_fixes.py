"""Tests for the final-review findings (each failed before its fix)."""
import os
import tempfile
import unittest
from pathlib import Path

from tests.tool.fakes import FakeRunner
from tests.tool.test_features import PROFILE
from tests.tool.test_probe import run_probe, stub, ProbeFixtureTests
from tests.tool.test_profile import MAIN, TWIN
from twinpc_lib import cli, features as F, profile as P, steps as S

REPO = Path(__file__).resolve().parents[2]


class ScriptedRunner(S.Runner):
    """A real Runner whose process execution is scripted: rules are (substring-of-last-argv, rc, out)."""

    def __init__(self, rules, **kw):
        super().__init__("twin", **kw)
        self.rules, self.execs = rules, []

    def _exec(self, argv, data):
        self.execs.append((argv, data))
        for sub, rc, out in self.rules:
            if sub in argv[-1]:
                return S.Result(rc, out)
        return S.Result(0, "")


# --- Critical 1: sudo password handling
class SudoTests(unittest.TestCase):
    def test_payload_never_shares_stdin_with_password(self):
        r = ScriptedRunner([("sudo -k -n true", 1, ""), ("mktemp", 0, "/tmp/twinpc.abc")],
                           ask=lambda p: "pw", isatty=lambda: True)
        r.run("twin", "cat > /etc/x.conf", root=True, input="PAYLOAD\n")
        for argv, data in r.execs:
            self.assertFalse("pw" in data and "PAYLOAD" in data, "password and payload in one stream")
        sudo_calls = [(a, d) for a, d in r.execs if "sudo -k -S" in a[-1] and " -v" not in a[-1]]
        self.assertEqual(len(sudo_calls), 1)
        argv, data = sudo_calls[0]
        self.assertEqual(data, "pw\n")
        self.assertIn("/tmp/twinpc.abc", argv[-1])
        self.assertTrue(any("rm -f" in a[-1] and "/tmp/twinpc.abc" in a[-1] for a, _ in r.execs))

    def test_root_without_input_reads_dev_null(self):
        r = ScriptedRunner([("sudo -k -n true", 1, "")], ask=lambda p: "pw", isatty=lambda: True)
        r.run("main", "whoami", root=True)
        self.assertIn("< /dev/null", r.execs[-1][0][-1])

    def test_wrong_password_is_reasked_then_stops(self):
        asked = []
        r = ScriptedRunner([("sudo -k -n true", 1, ""), (" -v", 1, "Sorry")],
                           ask=lambda p: asked.append(p) or "bad", isatty=lambda: True)
        with self.assertRaisesRegex(S.StepError, "wrong sudo password on the twin"):
            r.run("twin", "whoami", root=True)
        self.assertEqual(len(asked), 2)
        self.assertEqual(sum(1 for a, _ in r.execs if " -v" in a[-1]), 2)

    def test_cached_credentials_do_not_count_as_passwordless(self):
        r = ScriptedRunner([], ask=lambda p: "pw", isatty=lambda: True)
        r.run("main", "whoami", root=True)
        self.assertIn("sudo -k -n true", r.execs[0][0][-1])
        self.assertIn("sudo -k -n bash", r.execs[-1][0][-1])     # passwordless: no password sent
        self.assertEqual(r.execs[-1][1], "")

    def test_twin_ssh_has_timeout_and_end_of_options(self):
        r = ScriptedRunner([])
        r.run("twin", "true")
        argv = r.execs[0][0]
        self.assertIn("ConnectTimeout=10", " ".join(argv))
        self.assertEqual(argv[argv.index("twin") - 1], "--")


# --- Important 3 and 9: --no-root and dry run without a terminal
class RunStepsFixTests(unittest.TestCase):
    def test_no_root_skips_root_applies(self):
        st = S.cmd_step("f.twin.x", "f", "twin", "root change", check="not-done", apply="do-root", root=True, check_root=False)
        r = FakeRunner()
        lines = []
        rc = S.run_steps([st], S.Ctx({}, r, REPO, allow_root=False), out=lines.append)
        self.assertEqual(rc, 0)
        self.assertFalse(any("do-root" in c[1] for c in r.calls))
        self.assertIn("?  f.twin.x: needs sudo — skipped (--no-root)", lines)

    def test_dry_run_without_terminal_marks_root_checks(self):
        class NoTty:
            def run(self, machine, cmd, root=False, input=None):
                if root:
                    raise S.RootNeedsTerminal(machine)
                return S.Result(1, "")
        st = S.cmd_step("f.twin.r", "f", "twin", "x", check="c", apply="a", root=True)
        after = S.cmd_step("f.twin.s", "f", "twin", "y", check="c2", apply="a2")
        lines = []
        rc = S.run_steps([st, after], S.Ctx({}, NoTty(), REPO), dry_run=True, out=lines.append)
        self.assertEqual(rc, 0)
        self.assertIn("?  f.twin.r: needs sudo to check (no terminal)", lines)
        self.assertTrue(any(line.startswith("→  f.twin.s") for line in lines))

    def test_bios_confirmation_is_recorded(self):
        [bios] = [s for s in F.build_plan(PROFILE, REPO) if s.id == "power.main.bios"]
        r = FakeRunner([("main", "touch", 0, "")])
        calls = {"n": 0}
        orig = r.run

        def run(machine, cmd, root=False, input=None):
            if "bios-wol-confirmed" in cmd and "touch" not in cmd:
                calls["n"] += 1
                return S.Result(0 if calls["n"] > 1 else 1, "")
            return orig(machine, cmd, root, input)
        r.run = run
        self.assertEqual(S.run_steps([bios], S.Ctx({}, r, REPO, confirm=lambda q: True), out=lambda line: None), 0)
        self.assertTrue(any("touch" in c[1] for c in r.calls))


# --- Important 1, 2, 5, 6: planning
class PlanFixTests(unittest.TestCase):
    def reasons(self, prof):
        return {s.skip_reason for s in F.build_plan(prof, REPO) if s.skip_reason}

    def test_desktop_roles(self):
        prof = {**PROFILE, "main": {**PROFILE["main"], "desktop": "hyprland"}, "twin": {**PROFILE["twin"], "desktop": "gnome"}}
        reasons = self.reasons(prof)
        self.assertIn("desktop 'hyprland' on the main PC is not supported yet (planned)", reasons)
        self.assertIn("desktop 'gnome' on the twin is not supported yet (planned)", reasons)

    def test_skipped_package_skips_whole_feature(self):
        prof = {**PROFILE, "twin": {**PROFILE["twin"], "pkg": "apt"}}
        plan = F.build_plan(prof, REPO)
        for feature in ("kvm", "gpu-stack", "unlock"):
            steps = [s for s in plan if s.feature == feature]
            self.assertEqual(len(steps), 1, feature)
            self.assertTrue(steps[0].skip_reason)

    def test_firewall_inactive_counts_as_done(self):
        ids = {"connection.twin.firewall", "kvm.twin.firewall", "gpu-stack.twin.firewall"}
        found = [s for s in F.build_plan(PROFILE, REPO) if s.id in ids]
        self.assertEqual(len(found), 3)
        for st in found:
            r = FakeRunner()
            st.check(S.Ctx({}, r, REPO))
            self.assertIn("Status: inactive", r.calls[-1][1], st.id)

    def test_sshd_accepts_ubuntu_ssh_unit(self):
        [st] = [s for s in F.build_plan(PROFILE, REPO) if s.id == "connection.twin.sshd"]
        r = FakeRunner()
        st.check(S.Ctx({}, r, REPO))
        self.assertIn("ssh.socket", r.calls[-1][1])

    def test_static_ip_refuses_to_move_the_twin(self):
        [st] = [s for s in F.build_plan(PROFILE, REPO) if s.id == "connection.twin.static-ip"]
        r = FakeRunner([("twin", "", 0, "")])
        st.apply(S.Ctx({}, r, REPO))
        cmd = r.calls[-1][1]
        self.assertIn("refusing", cmd)
        self.assertIn("nmcli device reapply eth0", cmd)
        self.assertNotIn("connection up", cmd)

    def test_unlock_requires_the_encrypt_hook_and_rebuild_tracks_key(self):
        steps = {s.id: s for s in F.build_plan(PROFILE, REPO)}
        self.assertIn("unlock.twin.encrypt-hook", steps)
        r = FakeRunner()
        steps["unlock.twin.rebuild"].check(S.Ctx({}, r, REPO))
        self.assertIn("-newer /etc/dropbear/root_key", r.calls[-1][1])
        self.assertIn("initramfs-*.img", r.calls[-1][1])

    def test_power_needs_the_wired_port(self):
        prof = {**PROFILE, "twin": {**PROFILE["twin"], "wired_iface": ""}}
        power = [s for s in F.build_plan(prof, REPO) if s.feature == "power"]
        self.assertEqual(len(power), 1)
        self.assertIn("wired port is unknown", power[0].skip_reason)


# --- Important 7: profile validation
class ValidationTests(unittest.TestCase):
    def test_unsafe_values_are_rejected(self):
        for section, key, bad in [("user", "twin_user", "a;reboot"), ("twin", "wired_iface", "eth0 x"),
                                  ("network", "twin_addr", "10.42.0.11; id"), ("network", "twin_host", "-oProxyCommand=x"),
                                  ("user", "twin_mac", "$(id)")]:
            prof = P.build(MAIN, TWIN)
            prof[section][key] = bad
            with self.subTest(key=key), tempfile.TemporaryDirectory() as d:
                with self.assertRaisesRegex(P.ProfileError, key):
                    P.save(prof, Path(d, "p.toml"))

    def test_unsafe_side_is_rejected(self):
        prof = P.build(MAIN, TWIN)
        prof["user"]["twin_side"] = "left; id"
        with tempfile.TemporaryDirectory() as d, self.assertRaisesRegex(P.ProfileError, "twin_side"):
            P.save(prof, Path(d, "p.toml"))

    def test_unsafe_repo_path_is_rejected(self):
        with self.assertRaisesRegex(S.StepError, "checkout path"):
            F.build_plan(PROFILE, Path("/tmp/my repo;id"))

    def test_good_profile_passes(self):
        with tempfile.TemporaryDirectory() as d:
            P.load(P.save(P.build(MAIN, TWIN), Path(d, "p.toml")))


# --- Important 8 and minor 1: detect --twin
class DetectTwinTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old = os.environ.get("XDG_CONFIG_HOME")
        os.environ["XDG_CONFIG_HOME"] = self.tmp.name

    def tearDown(self):
        if self.old is None:
            os.environ.pop("XDG_CONFIG_HOME", None)
        else:
            os.environ["XDG_CONFIG_HOME"] = self.old
        self.tmp.cleanup()

    def detect(self, *args):
        seen = []

        def probe(machine, host, script):
            seen.append((machine, host))
            d = MAIN if machine == "main" else TWIN
            return "".join(f"{k}={v}\n" for k, v in d.items())
        cli.main(["detect", *args], probe=probe, out=lambda line: None)
        return seen

    def test_bootstrap_by_address(self):
        seen = self.detect("--twin", "tw@10.42.0.11")
        self.assertIn(("twin", "tw@10.42.0.11"), seen)
        self.assertEqual(P.load()["network"]["twin_host"], "twin")

    def test_explicit_alias_wins(self):
        self.detect()
        self.detect("--twin", "gpu")
        self.assertEqual(P.load()["network"]["twin_host"], "gpu")


# --- Important 4: the probe picks the port that reaches the other PC
class ProbePortTests(ProbeFixtureTests):
    def test_twin_uses_the_route_to_the_ssh_peer(self):
        (self.root / "sys/class/net/eth1/device").mkdir(parents=True)
        (self.root / "sys/class/net/eth1/carrier").write_text("1\n")
        stub(self.stubs, "ip", 'case "$*" in *"route get"*) echo "10.42.0.1 dev eth1 src 10.42.0.11 uid 1000" ;;'
                               ' *) echo "2: eth0    inet 192.168.1.5/24 brd 192.168.1.255 scope global eth0" ;; esac')
        p = run_probe(self.root, self.stubs, {"SSH_CONNECTION": "10.42.0.1 51234 10.42.0.11 22"})
        self.assertEqual(p["wired_iface"], "eth1")

    def test_main_prefers_the_shared_connection_port(self):
        stub(self.stubs, "ip", 'case "$*" in "-o -4 addr show") echo "3: eth1    inet 10.42.0.1/24 brd 10.42.0.255 scope global eth1" ;;'
                               ' *) echo "2: eth0    inet 192.168.1.5/24 brd 192.168.1.255 scope global eth0" ;; esac')
        self.assertEqual(run_probe(self.root, self.stubs)["wired_iface"], "eth1")


if __name__ == "__main__":
    unittest.main()
