"""twinpc — set up and check twinPC on a pair of Linux machines."""
import argparse
import subprocess
from pathlib import Path

from . import adapters, profile as P
from .adapters.base import Unsupported
from .doctor import doctor
from .features import FEATURES, build_plan
from .steps import Ctx, Runner, run_steps

REPO = Path(__file__).resolve().parents[2]
PROBE = REPO / "tool" / "probe.sh"


def default_probe(machine, host, script):
    argv = ["sh", "-s"] if machine == "main" else ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", host, "sh -s"]
    p = subprocess.run(argv, input=script, capture_output=True, text=True)
    return p.stdout if p.returncode == 0 else None


def _describe(label, m):
    return (f"{label}: {m.get('family')} · {m.get('pkg')} · {m.get('desktop')}/{m.get('session')}"
            f" · gpu {m.get('gpu')}{' ' + m['gpu_arch'] if m.get('gpu_arch') else ''}"
            + (f" · luks {'yes' if m.get('luks') else 'no'} · {m.get('initramfs')} · {m.get('bootloader')}"
               if label == "twin" else ""))


def _unsupported(prof):
    checks = [("packages", prof["main"].get("pkg")), ("desktop", prof["main"].get("desktop")),
              ("network", prof["network"].get("mode"))]
    if prof.get("twin"):
        t = prof["twin"]
        checks += [("packages", t.get("pkg")), ("desktop", t.get("desktop")), ("gpu", t.get("gpu"))]
        if t.get("luks"):
            checks.append(("bootunlock", t.get("initramfs")))
    seen = []
    for kind, value in checks:
        a = adapters.get(kind, value)
        if isinstance(a, Unsupported) and a.reason() not in seen:
            seen.append(a.reason())
    return seen


def cmd_detect(a, probe, out):
    existing = P.load() if P.profile_path().exists() else None
    host = a.twin or (existing or {}).get("network", {}).get("twin_host") or "twin"
    script = PROBE.read_text()
    main_raw = probe("main", host, script)
    twin_raw = probe("twin", host, script)
    prof = P.build(P.parse_probe(main_raw or ""), P.parse_probe(twin_raw) if twin_raw else None,
                   existing, a.force, host)
    path = P.save(prof)
    out(f"profile written: {path}")
    out(_describe("main", prof["main"]))
    if prof.get("twin"):
        out(_describe("twin", prof["twin"]))
        out(f"network: {prof['network'].get('mode')} · twin at {prof['network'].get('twin_addr')}")
    for reason in _unsupported(prof):
        out(f"  ⚠️ {reason} — the features that need it will be skipped")
    if twin_raw is None:
        out(f"twin '{host}' is not reachable over ssh — set up key login first (README step 1), then run: twinpc detect")
        return 1
    return 0


def main(argv=None, runner=None, probe=None, out=print, confirm=None):
    ap = argparse.ArgumentParser(prog="twinpc", description="Set up and check twinPC on a pair of Linux machines.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("detect", help="probe both machines and write ~/.config/twinpc/profile.toml")
    d.add_argument("--twin", help="ssh host of the twin (default: twin)")
    d.add_argument("--force", action="store_true", help="refresh detected values instead of only filling gaps")
    i = sub.add_parser("install", help="install every feature (or the ones named), skipping what is done")
    i.add_argument("features", nargs="*", metavar="FEATURE", help=", ".join(FEATURES))
    i.add_argument("--dry-run", action="store_true", help="show the plan, change nothing")
    i.add_argument("--yes", action="store_true", help="don't ask to confirm manual steps")
    i.add_argument("--no-root", action="store_true", help="skip everything that needs sudo")
    c = sub.add_parser("doctor", help="check every feature and report what works")
    c.add_argument("features", nargs="*", metavar="FEATURE")
    c.add_argument("--no-root", action="store_true", help="skip checks that need sudo")
    a = ap.parse_args(argv)

    if a.cmd == "detect":
        return cmd_detect(a, probe or default_probe, out)
    try:
        prof = P.load()
        steps = build_plan(prof, REPO, a.features or None)
    except (P.ProfileError, ValueError) as e:
        out(str(e))
        return 2
    host = prof.get("network", {}).get("twin_host") or "twin"
    ctx = Ctx(prof, runner or Runner(host), REPO, yes=getattr(a, "yes", False), allow_root=not a.no_root)
    if confirm:
        ctx.confirm = confirm
    if a.cmd == "install":
        return run_steps(steps, ctx, dry_run=a.dry_run, out=out)
    return doctor(steps, ctx, out=out)
