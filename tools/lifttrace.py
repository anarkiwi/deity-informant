"""Locate where two builds of one tune part company in rung (d2).

Records a tune's ordered lift decisions so two builds can be diffed at the first
disagreement, and repeats a build to expose a flaky gate. Subcommands: ``capture
<tune> <out.json>``, ``diff <a.json> <b.json>``, ``repeat <tune> [--runs N]``,
``stable <tune> [--runs N]``, ``verdict <tune>``.
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from deity_informant import framemath as FM
from deity_informant import frameprog, frameval
from deity_informant import structured as S
from deity_informant.c64 import load_psid

HVSC = Path(".oracle-cache/hvsc")


def sid_path(tune):
    """The cached HVSC file for a tune stem; raises when it is not unique."""
    hits = sorted(HVSC.resolve().rglob(tune + ".sid"))
    if len(hits) != 1:
        raise SystemExit("%d files match %r" % (len(hits), tune))
    return hits[0]


def build(tune, frames, sub):
    """``(model, prog)`` for one tune, as the corpus measurement builds it."""
    mem, _l, init, play = load_psid(sid_path(tune).read_bytes())
    mem[0xD418] = 0x0F
    model, _ev = S.decompile(mem, init, play, frames, sub)
    return model, frameprog.program(model)


def brief(n, depth=6):
    """Compact rendering of an IR node, bounded so traces stay diffable."""
    if not isinstance(n, tuple):
        return str(n)
    if depth == 0:
        return "%s(..)" % (n[0] if isinstance(n[0], str) else "?")
    return "(%s)" % " ".join(brief(c, depth - 1) for c in n)


def capture(tune, frames=200, sub=0):
    """The ordered rung-(d2) decision trace of building ``tune``, and its gate."""
    ev = []
    saved = (FM._fuse, FM._site, FM._premise, FM._lift, FM._may_disturb)
    o_fuse, o_site, o_prem, o_lift, o_may = saved

    def form_str(f):
        """The whole 16-bit form: two lanes alone do not tell two forms apart."""
        return "hi=%s lo=%s word=%s" % (brief(f[0], 5), brief(f[1], 5), brief(f[2], 6))

    def fuse(halves, pairs):
        got = o_fuse(halves, pairs)
        for n, forms in enumerate(got):
            ev.append(["forms", len(forms), [form_str(f) for f in forms], brief(pairs[n], 5)])
        return got

    def site(lst, i, j, env, offered, regions):
        st = o_site(lst, i, j, env, offered, regions)
        ev.append(["stmts", i, j, [brief(s, 8) for s in lst]])
        shown = None
        if st is not None:
            shown = "lo=%s hi=%s idx=%s hidx=%s at=%s why=%s src=%s" % (
                st.lo,
                st.hi,
                brief(st.idx, 3),
                brief(st.hidx, 3),
                (st.lo_at, st.hi_at),
                st.why,
                [brief(x, 5) for x in st.src],
            )
        ev.append(["site", i, j, shown])
        return st

    def rng(r):
        """One address range as text; a sentinel index prints by name, not by id."""
        if r is None:
            return "None"
        idx = brief(r[1], 2) if r[1] is None or isinstance(r[1], tuple) else "unres"
        return "$%04X idx=%s span=%d w=%d mod=%d" % (r[0], idx, r[2], r[3], r[4])

    def read_rng(ref, rw, regions):
        """One read as a range tuple, its span filled in from the declarations."""
        if ref is None:
            return None
        rb, ri, rm = ref
        return (rb, ri, FM._span(rb, ri, regions, rm), rw, rm)

    def refs(exprs, regions):
        """Every memory range the expressions read: the other half of a refusal's terms."""
        return [
            "read=%s" % rng(read_rng(ref, rw, regions))
            for x in exprs
            for ref, rw in FM._mem_refs(x)
        ]

    def ranges(lst, i, st, regions, env):
        """The store's range against each range it is held to reach: the refusal's terms.

        A refusal names a predicate, not the pair of addresses that tripped it, and
        an unresolvable one prints as None -- which is the usual answer."""
        store = FM._store(env, lst, i)
        held = [st.src[1 if st.lo_at == i else 0], st.src[2]]
        return ["store=%s" % rng(FM._reach(store, regions))] + refs(held, regions)

    def culprit(lst, k, st, regions, env):
        """The statement blocking the hoist, and which operand it changed."""
        s = FM._store(env, lst, k)
        hit = [n for n, x in enumerate(st.src) if FM._clobbers(s, (x,), regions)]
        return [k, brief(lst[k], 8), "at=%s" % rng(FM._reach(s, regions)), hit] + refs(
            [st.src[n] for n in hit], regions
        )

    def premise(lst, i, j, st, span, blocked=None, regions=None, env=None):
        why = o_prem(lst, i, j, st, span, blocked, regions, env)
        ev.append(["premise", i, j, why, bool(st.merge)])
        if why == "the lo destination may disturb the hi lane or the step":
            ev.append(["disturb", i, j, ranges(lst, i, st, regions, env)])
        if why == "an intervening statement changes an operand":
            ev.append(["clobber", i, j, culprit(lst, blocked, st, regions, env)])
        return why

    def may_disturb(stmt, base, idx, regions, mod=0):
        """``_match``'s own alias test: the lo store's range against the hi lane's."""
        got = o_may(stmt, base, idx, regions, mod)
        if got:
            lane = FM._lane(base, idx, FM._span(base, idx, regions, mod), mod)
            ev.append(["alias", "store=%s" % rng(FM._reach(stmt, regions)), "hi=%s" % rng(lane)])
        return got

    def lift(lst, st, name):
        o_lift(lst, st, name)
        at = min(st.lo_at, st.hi_at)
        ev.append(["lift", st.lo_at, st.hi_at, name, brief(lst[at], 7)])

    FM._fuse, FM._site, FM._premise = fuse, site, premise
    FM._lift, FM._may_disturb = lift, may_disturb
    try:
        model, prog = build(tune, frames, sub)
        gate = frameval.gate_fp(model, frames, prog)
    finally:
        FM._fuse, FM._site, FM._premise, FM._lift, FM._may_disturb = saved
    return {"tune": tune, "frames": frames, "sub": sub, "gate": gate and list(gate), "events": ev}


def diff(a, b):
    """Print the first decision two traces disagree on, with its context."""
    ea, eb = a["events"], b["events"]
    print("A gate %s\nB gate %s\nA %d events, B %d" % (a["gate"], b["gate"], len(ea), len(eb)))
    for k in range(max(len(ea), len(eb))):
        x, y = (ea[k] if k < len(ea) else None), (eb[k] if k < len(eb) else None)
        if x == y:
            continue
        print("\nfirst divergence, event %d:" % k)
        for j in range(max(0, k - 3), k):
            print("  same  %s" % (ea[j],))
        print("  A     %s\n  B     %s" % (x, y))
        return 1
    print("\ntraces are identical -- any behaviour difference is outside rung (d2)")
    return 0


def verdict(tune, frames=200, sub=0):
    """Gate FP verdict for one build, as a string."""
    model, prog = build(tune, frames, sub)
    return str(frameval.gate_fp(model, frames, prog))


def repeat(tune, runs=5, frames=200, sub=0):
    """Build ``tune`` in ``runs`` FRESH processes; a split verdict means a flaky gate.

    In-process repetition cannot see this: it shares one hash seed and a warm
    ``framemath._FUSED``, which is precisely what varies between corpus workers."""
    seen = {}
    for _ in range(runs):
        got = subprocess.run(
            [sys.executable, __file__, "verdict", tune, "--frames", str(frames), "--sub", str(sub)],
            capture_output=True,
            text=True,
            check=False,
        )
        key = got.stdout.strip() or ("ERROR: " + got.stderr.strip()[-90:])
        seen[key] = seen.get(key, 0) + 1
    for key, n in sorted(seen.items(), key=lambda kv: -kv[1]):
        print("  %2d/%d  %s" % (n, runs, key))
    print("FLAKY" if len(seen) > 1 else "stable")
    return 1 if len(seen) > 1 else 0


def stable(tune, runs=8, frames=200, sub=0):
    """Diff whole decision traces across ``runs`` hash seeds, not just the verdict.

    A gate verdict is a lossy detector: an unstable decision only shows up in it
    when it happens to change an emitted frame. Two seeds are a lossy detector
    too -- ``Amulet_of_Yendor`` first parted company at seed 3."""
    out = Path(tempfile.mkdtemp(prefix="lifttrace-"))
    caps = []
    for r in range(runs):
        dst = out / ("%d.json" % r)
        env = dict(os.environ, PYTHONHASHSEED=str(r))
        got = subprocess.run(
            [sys.executable, __file__, "capture", tune, str(dst)]
            + ["--frames", str(frames), "--sub", str(sub)],
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )
        if not dst.exists():
            raise SystemExit("seed %d: %s" % (r, got.stderr.strip()[-400:]))
        caps.append(json.loads(dst.read_text(encoding="utf-8")))
    base = caps[0]
    for r, got in enumerate(caps[1:], 1):
        if got != base:
            print("seed 0 vs seed %d:" % r)
            diff(base, got)
            return 1
    print(
        "%d seeds agree on all %d decisions -- rung (d2) is deterministic"
        % (runs, len(base["events"]))
    )
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="rung (d2) decision tracing")
    sub = ap.add_subparsers(dest="cmd", required=True)
    cap = sub.add_parser("capture")
    cap.add_argument("tune")
    cap.add_argument("out")
    dif = sub.add_parser("diff")
    dif.add_argument("a")
    dif.add_argument("b")
    rep = sub.add_parser("repeat")
    rep.add_argument("tune")
    rep.add_argument("--runs", type=int, default=5)
    stb = sub.add_parser("stable")
    stb.add_argument("tune")
    stb.add_argument("--runs", type=int, default=4)
    ver = sub.add_parser("verdict")
    ver.add_argument("tune")
    for p in (cap, rep, stb, ver):
        p.add_argument("--frames", type=int, default=200)
        p.add_argument("--sub", type=int, default=0)
    args = ap.parse_args(argv)
    if args.cmd == "diff":
        return diff(*(json.loads(Path(f).read_text(encoding="utf-8")) for f in (args.a, args.b)))
    if args.cmd == "repeat":
        return repeat(args.tune, args.runs, args.frames, args.sub)
    if args.cmd == "stable":
        return stable(args.tune, args.runs, args.frames, args.sub)
    if args.cmd == "verdict":
        print(verdict(args.tune, args.frames, args.sub))
        return 0
    Path(args.out).write_text(
        json.dumps(capture(args.tune, args.frames, args.sub)), encoding="utf-8"
    )
    print("captured %s -> %s" % (args.tune, args.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
