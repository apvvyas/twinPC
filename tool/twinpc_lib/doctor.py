"""twinpc doctor: run every feature's checks (read-only) and print one health line per feature."""
from .steps import StepError


def doctor(steps, ctx, out=print):
    by_feature = {}
    for s in steps:
        by_feature.setdefault(s.feature, []).append(s)
    broken = 0
    for feature, fsteps in by_feature.items():
        skips = [s.skip_reason for s in fsteps if s.skip_reason]
        failing, unknown = None, 0
        for s in fsteps:
            if s.skip_reason or s.check is None:
                continue
            if s.check_root and not ctx.allow_root:
                unknown += 1
                continue
            try:
                ok = s.check(ctx)
            except StepError:
                unknown += 1
                continue
            if not ok:
                failing = s
                break
        name = f"{feature:<11}"
        if failing:
            broken += 1
            out(f"❌ {name} broken: {failing.id} — {failing.describe}")
        elif skips and len(skips) == len(fsteps):
            out(f"⚠️ {name} skipped: {skips[0]}")
        elif skips:
            out(f"⚠️ {name} partly skipped: {skips[0]}")
        elif unknown:
            out(f"❔ {name} working as far as checked ({unknown} check(s) need sudo)")
        else:
            out(f"✅ {name} working")
    return 1 if broken else 0
