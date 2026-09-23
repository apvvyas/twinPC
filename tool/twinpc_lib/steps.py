"""Steps: small idempotent actions, each with a check, run on the main PC or on the twin.

A Step is "done" when its check passes. run_steps() checks each step, applies the ones that are
not done, checks again, and stops at the first failure. Manual steps (apply=None) show
instructions and wait for confirmation. Root commands run through `sudo -S`; the password is asked
at most once per machine per run, and not at all when `sudo -n true` already works.
"""
import getpass
import hashlib
import shlex
import subprocess
import sys
from dataclasses import dataclass
from typing import Callable, Optional


@dataclass
class Result:
    rc: int
    out: str


class StepError(Exception):
    pass


class RootNeedsTerminal(StepError):
    def __init__(self, machine):
        super().__init__(f"a step needs sudo on the {machine} and needs a terminal for the password "
                         f"(run twinpc in a terminal, or use --no-root to skip root checks)")


class Runner:
    """Runs shell commands on "main" (locally) or "twin" (over ssh)."""

    def __init__(self, twin_host, ask=getpass.getpass, isatty=None):
        self.twin_host = twin_host
        self.ask = ask
        self.isatty = isatty or sys.stdin.isatty
        self.passwords = {}

    def _argv(self, machine, cmd):
        if machine == "main":
            return ["bash", "-c", cmd]
        return ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "--", self.twin_host, cmd]

    def _exec(self, argv, data):
        p = subprocess.run(argv, input=data, capture_output=True, text=True)
        return Result(p.returncode, (p.stdout + p.stderr).strip())

    MAX_TRIES = 2          # two wrong passwords stay below pam_faillock's usual lock-out of 3

    def _password(self, machine):
        """The sudo password for `machine` (None when sudo needs none), verified before first use."""
        if machine in self.passwords:
            return self.passwords[machine]
        # -k: ignore cached credentials, so "no password needed" really means passwordless sudo
        if self._exec(self._argv(machine, "sudo -k -n true"), "").rc == 0:
            self.passwords[machine] = None
            return None
        if not self.isatty():
            raise RootNeedsTerminal(machine)
        for _ in range(self.MAX_TRIES):
            password = self.ask(f"[sudo] password on the {machine}: ")
            if self._exec(self._argv(machine, "sudo -k -S -p '' -v"), password + "\n").rc == 0:
                self.passwords[machine] = password
                return password
            print(f"wrong sudo password on the {machine}", file=sys.stderr)
        raise StepError(f"wrong sudo password on the {machine} — stopped after {self.MAX_TRIES} tries "
                        "so the account does not get locked")

    def run(self, machine, cmd, root=False, input=None):
        if not root:
            if machine == "twin":
                cmd = f"bash -c {shlex.quote(cmd)}"
            return self._exec(self._argv(machine, cmd), input or "")
        password = self._password(machine)
        # The password goes to sudo on stdin; the command's own input comes from a private temp
        # file (or /dev/null), so the two can never mix.
        tmp = None
        if input:
            r = self._exec(self._argv(machine, 'umask 077; t=$(mktemp); cat > "$t" && echo "$t"'), input)
            if r.rc != 0 or not r.out.strip():
                raise StepError(f"could not stage input on the {machine}: {r.out[-200:]}")
            tmp = r.out.strip().splitlines()[-1]
        body = f"{{ {cmd}\n}} < {shlex.quote(tmp) if tmp else '/dev/null'}"
        sudo = "sudo -k -n" if password is None else "sudo -k -S -p ''"
        try:
            return self._exec(self._argv(machine, f"{sudo} bash -c {shlex.quote(body)}"),
                              "" if password is None else password + "\n")
        finally:
            if tmp:
                self._exec(self._argv(machine, f"rm -f {shlex.quote(tmp)}"), "")


def _ask_yes(question):
    try:
        return input(f"{question} [y/N] ").strip().lower() == "y"
    except EOFError:
        return False


@dataclass
class Ctx:
    profile: dict
    runner: object
    repo: object
    yes: bool = False
    allow_root: bool = True
    confirm: Callable[[str], bool] = _ask_yes

    def run(self, machine, cmd, root=False, input=None):
        return self.runner.run(machine, cmd, root=root, input=input)


@dataclass
class Step:
    id: str
    feature: str
    machine: str
    root: bool
    describe: str
    check: Optional[Callable[[Ctx], bool]]
    apply: Optional[Callable[[Ctx], None]]
    manual: str = ""
    skip_reason: str = ""
    check_root: bool = False
    after_confirm: str = ""     # command run (as the user) once a manual step is confirmed


def _ok(ctx, machine, cmd, root=False):
    return ctx.run(machine, cmd, root=root).rc == 0


def _must(ctx, machine, cmd, root=False, input=None):
    r = ctx.run(machine, cmd, root=root, input=input)
    if r.rc != 0:
        first = cmd.strip().splitlines()[0][:90]
        raise StepError(f"`{first}` failed (exit {r.rc}): {r.out[-400:]}")


def cmd_step(id, feature, machine, describe, check, apply, root=False, check_root=None, input=None):
    cr = root if check_root is None else check_root
    return Step(id, feature, machine, root, describe,
                (lambda ctx: _ok(ctx, machine, check, cr)) if check else None,
                lambda ctx: _must(ctx, machine, apply, root, input),
                check_root=cr)


def manual_step(id, feature, machine, describe, manual, check=None, check_root=False, after_confirm=""):
    return Step(id, feature, machine, False, describe,
                (lambda ctx: _ok(ctx, machine, check, check_root)) if check else None,
                None, manual=manual, check_root=check_root, after_confirm=after_confirm)


def ufw_step(id, feature, machine, rule, describe):
    """Allow `rule` in ufw. Done when ufw is absent, inactive, or already has the rule."""
    return cmd_step(id, feature, machine, describe,
                    check=f"! command -v ufw >/dev/null || ufw status | grep -q '^Status: inactive'"
                          f" || ufw status | grep -q '^{rule}'",
                    apply=f"ufw allow {rule}", root=True)


def file_step(id, feature, machine, dest, content, root=False, mode=None, after=None, describe=None):
    """Write `content` (a string, or a function of the Ctx) to `dest` ($HOME allowed)."""
    def text(ctx):
        return content(ctx) if callable(content) else content

    def check(ctx):
        try:
            want = hashlib.sha256(text(ctx).encode()).hexdigest()
        except StepError:
            return False                                  # e.g. a certificate it depends on is missing
        r = ctx.run(machine, f'sha256sum "{dest}" 2>/dev/null | cut -d" " -f1', root=root)
        return r.rc == 0 and r.out.strip() == want

    def apply(ctx):
        cmd = f'mkdir -p "$(dirname "{dest}")" && cat > "{dest}"'
        if mode:
            cmd += f' && chmod {mode} "{dest}"'
        if after:
            cmd += f" && {after}"
        _must(ctx, machine, cmd, root=root, input=text(ctx))

    return Step(id, feature, machine, root, describe or f"write {dest}", check, apply, check_root=root)


def line_step(id, feature, machine, path, line, match=None, describe=None):
    """Append `line` to `path` unless a line containing `match` (default: the line) is there."""
    return cmd_step(id, feature, machine, describe or f"add a line to {path}",
                    check=f"grep -qF -- {shlex.quote(match or line)} {path} 2>/dev/null",
                    apply=f"printf '%s\\n' {shlex.quote(line)} >> {path}")


def unit_step(id, feature, machine, unit, user=True, now=True):
    sc = "systemctl --user" if user else "systemctl"
    check = f"{sc} is-enabled --quiet {unit}" + (f" && {sc} is-active --quiet {unit}" if now else "")
    apply = f"{sc} daemon-reload && {sc} enable {'--now ' if now else ''}{unit}"
    return cmd_step(id, feature, machine, f"enable {unit}", check, apply, root=not user, check_root=False)


def unsupported_step(feature, machine, reason, tag="unsupported"):
    return Step(f"{feature}.{machine}.{tag}", feature, machine, False, "", None, None, skip_reason=reason)


def run_steps(steps, ctx, dry_run=False, out=print):
    for s in steps:
        if s.skip_reason:
            out(f"⏭  {s.id}: skipped — {s.skip_reason}")
            continue
        if s.check_root and not ctx.allow_root:
            out(f"?  {s.id}: needs sudo to check")
            continue
        try:
            try:
                done = bool(s.check and s.check(ctx))
            except RootNeedsTerminal:
                if not dry_run:
                    raise
                out(f"?  {s.id}: needs sudo to check (no terminal)")
                continue
            if done:
                out(f"✓  {s.id}: already done")
                continue
            if dry_run:
                out(f"→  {s.id}: would {s.describe}" + (" (manual)" if s.apply is None else "")
                    + (" [sudo]" if s.root else ""))
                continue
            if s.root and not ctx.allow_root:
                out(f"?  {s.id}: needs sudo — skipped (--no-root)")
                continue
            if s.apply is None:
                out(f"✋ {s.id}: {s.describe}\n   {s.manual}")
                if not (ctx.yes or ctx.confirm("   done?")):
                    out(f"✗  {s.id}: not confirmed — stopping")
                    return 1
                if s.after_confirm:
                    _must(ctx, s.machine, s.after_confirm)
            else:
                out(f"…  {s.id}: {s.describe}")
                s.apply(ctx)
            if s.check and not s.check(ctx):
                out(f"✗  {s.id}: still not done after applying it — stopping")
                return 1
            out(f"✓  {s.id}: done")
        except StepError as e:
            out(f"✗  {s.id}: {e}")
            return 1
    return 0
