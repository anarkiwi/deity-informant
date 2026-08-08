"""Name the shape of every store the lift ladder left behind (docs/frameprog.md 7.9.1).

``fuse_measure`` counts the residue into buckets; this names it. A bucket label is a
claim -- ``notaligned`` says *irreducible* -- so each residual store is emitted with the
evidence it was bucketed on: the index's constants, the registers reached, the roles.
"""

import argparse
import json
import multiprocessing as mp
import signal
import sys
import time
from collections import Counter
from pathlib import Path

import _sweep

ROOT = _sweep.ROOT
sys.path.insert(0, str(ROOT))

SID_LO, SID_HI = 0xD400, 0xD41C

USAGE = """\
  python tools/lift_triage.py                                   # the whole cache
  python tools/lift_triage.py --tunes Comic_Bakery,Krakout      # named tunes only
  python tools/lift_triage.py --classes window,hi_lane -o out/triage.json"""

# How an indexed lane store rung (d) refused reaches the registers it reaches.
LANE = (
    "unproven",  # the index resolves to no constant set: what the model cannot see
    "hi_lane",  # every reaching index lands on a pair *hi*: widening is right one cell down
    "swept",  # a covering run a loop counter proves swept: rung (d) leaves it alone (7.10.2)
    "window",  # covering, with no counter proving every value it may take occurs
    "straddle",  # covers some pair by one half only: widening really would write a neighbour
    "offlane",  # reaches only 8-bit registers: no 16-bit form exists to widen to
)

# Why ``addr_split`` could not name a store's address.
UNNAMED = (
    "modular",  # ``zp,X`` wraps inside the byte, so no base+index names the row
    "deref",  # the address is loaded, not computed: a pointer or a table row
    "computed",  # an add whose base is not a constant, or no add at all
    "opaque",  # none of the above: a shape this tool does not name either
)


def build(entry):
    """The frame program of one tune, as ``fuse_measure`` builds it."""
    from deity_informant import frameprog
    from deity_informant import structured as S
    from deity_informant.c64 import load_psid

    sid, sub, secs = entry
    mem, _load, init, play = load_psid(Path(sid).read_bytes())
    mem[0xD418] = 0x0F  # the filter volume the corpus is swept at
    model, _ev = S.decompile(mem, init, play, int(secs * 50), sub)
    return model, frameprog.program(model)


def _roles(cell, ks):
    """Each register ``cell + k`` reaches, as ``lo`` / ``hi`` / ``byte``."""
    from deity_informant import framefuse

    out = []
    for k in sorted(ks):
        at = cell + k
        base = framefuse._sid_base(at)
        out.append("byte" if base is None else ("lo" if base == at else "hi"))
    return out


def _lane_class(cell, ks, swept):
    """Which ``LANE`` class an indexed lane store rung (d) refused belongs to."""
    from deity_informant import framefuse

    if swept:
        return "swept"
    if ks is None:
        return "unproven"
    roles = _roles(cell, ks)
    if all(r == "hi" for r in roles):
        return "hi_lane"
    if all(r == "byte" for r in roles):
        return "offlane"
    return "window" if framefuse._covering(cell, ks) else "straddle"


def _has(node, kind):
    """True where ``kind`` appears anywhere in the expression."""
    stack = [node]
    while stack:
        n = stack.pop()
        if n[0] == kind:
            return True
        if n[0] == "op":
            stack.extend(n[2])
        elif n[0] == "mem":
            stack.append(n[1])
    return False


def _unnamed_class(addr):
    """Why ``addr_split`` refused this address, by the shape it refused."""
    from deity_informant import frameproc

    got = frameproc._index_of(addr)
    if got is not None:
        return "modular" if got[2] else "opaque"  # a named one never reaches here
    if addr[0] == "loc":
        return "indirect"
    if _has(addr, "mem"):
        return "deref"
    if frameproc.is_op(addr, "INT_ADD", 2) or frameproc.is_op(addr, "INT_ADD", 1):
        return "computed"
    return "opaque"


def _through(addr, at):
    """The address as its definition spells it, else None where none reaches it.

    ``addr_split`` reads the address as written, so a store through a local is
    unnameable however plain its definition is; ``addr_bits`` no longer does
    (G1), and this is the same edge, kept for the record it prints."""
    return None if addr[0] != "loc" else at.defn(addr)


def _lane_records(model, prog):
    """One record per indexed lane store rung (d) left byte-wide.

    ``framefuse._visit``'s measure pass re-walked with its verdict spelled out: same
    environment, same ``_consts``, same ``_lane_aligned``, keeping the evidence rather
    than a counter. Nothing is mutated, so the program is untouched."""
    from deity_informant import framefuse
    from deity_informant import frameproc

    ctx = framefuse.contexts(model, prog.data_decls, prog.procs)
    cands = framefuse.candidates(model, prog.data_decls, prog.procs)
    out = []

    def walk(stmts, p, c, outer, cyclic, entry):
        env = frameproc.Defs(stmts, outer, cyclic, c[4] if outer is None else None)
        for i, s in enumerate(stmts):
            for body in frameproc._stmt_bodies(s):
                walk(body, p, c, (env, i), s[0] in frameproc._CYCLIC, entry)
            half = framefuse._store_half(s, p)
            if half is None or half[1] is None:
                continue
            ks = framefuse._consts(half[1], env, i, c)
            if framefuse._lane_aligned(p, ks):
                continue  # rung (d) widens it: not residue
            cell = half[0]
            rec = {
                "class": _lane_class(cell, ks, framefuse._lane_sweep(cell, half[1], env, i)),
                "entry": "$%04X" % entry,
                "pair": "$%04X/$%04X" % (p.lo, p.hi),
                "store": "%s = %s" % (frameproc._memref(s[1]), frameproc._fmt(s[2])),
                "index": frameproc._fmt(half[1]),
                "in_loop": bool(cyclic),
            }
            if ks is None:
                rec["index_kind"] = half[1][0]
            else:
                rec["reaches"] = ["$%04X" % (cell + k) for k in sorted(ks)]
                rec["roles"] = _roles(cell, ks)
            out.append(rec)

    for (lo, hi), (kind, ev) in sorted(cands.items()):
        if kind != "sid":
            continue
        p = framefuse._Pair(lo, hi, kind, ev)
        for e, _pa, _r, stmts in prog.procs:
            walk(stmts, p, ctx[e], None, False, e)
    return out


def _unnamed_records(prog):
    """One record per byte store whose address the emitter cannot name.

    ``addr_bits`` is all that stands between these and the SID: an address that cannot
    set every bit $D400 and $D41C share reaches no register. The population is what it
    cannot rule out *as written*; ``ruled`` is G1's verdict on the same store."""
    from deity_informant import frameproc
    from deity_informant import grammar as G

    out = []

    def walk(stmts, outer, cyclic):
        env = frameproc.Defs(stmts, outer, cyclic)
        for k, s in enumerate(stmts):
            for body in frameproc._stmt_bodies(s):
                walk(body, (env, k), s[0] in frameproc._CYCLIC)
            if s[0] != "st" or G.store_width(s[2]) != 1:
                continue
            base, _idx = frameproc.addr_split(s[1])
            if base is not None or s[1] in prog.resolved:
                continue
            if frameproc.addr_bits(s[1]) & SID_LO != SID_LO:
                continue  # ruled out as written: it cannot reach a register
            at = frameproc.DefsAt(env, k)
            rec = {
                "class": _unnamed_class(s[1]),
                "addr": frameproc._fmt(s[1]),
                "ruled": frameproc.addr_bits(s[1], at) & SID_LO != SID_LO,
            }
            got = _through(s[1], at)
            if got is not None:
                rec["def"] = frameproc._fmt(got)
                rec["def_class"] = _unnamed_class(got)
                rec["def_named"] = frameproc.addr_split(got)[0] is not None
            out.append(rec)

    for _e, _p, _r, stmts in prog.procs:
        walk(stmts, None, False)
    return out


def one(entry):
    """One tune's records, or the exception that stopped it."""
    try:
        signal.alarm(_sweep.CAP_S)
        return _one(entry)
    except Exception as exc:  # pylint: disable=broad-except
        return {**_sweep.row_head(entry), "error": "%s: %s" % (type(exc).__name__, exc)}
    finally:
        signal.alarm(0)


def _one(entry):
    t0 = time.monotonic()
    model, prog = build(entry)
    lanes = _lane_records(model, prog)
    unnamed = _unnamed_records(prog)
    return {
        **_sweep.row_head(entry),
        "build_s": round(time.monotonic() - t0, 1),
        "lane": dict(Counter(r["class"] for r in lanes)),
        "unnamed": dict(Counter(r["class"] for r in unnamed)),
        "unnamed_ruled": dict(Counter(r["class"] for r in unnamed if r["ruled"])),
        "lane_records": lanes,
        "unnamed_records": unnamed,
    }


def _totals(done, key):
    out = Counter()
    for r in done:
        out.update(r[key])
    return dict(out)


def _report(done, want):
    """Per-tune records for the classes asked for, worst tune first."""
    hits = [(r["tune"], [x for x in r["lane_records"] if x["class"] in want]) for r in done]
    for tune, recs in sorted(hits, key=lambda t: -len(t[1])):
        if not recs:
            continue
        print("\n=== %s (%d)" % (tune, len(recs)))
        for x in recs:
            print("  [%s] %s" % (x["class"], x["store"]))
            if "reaches" in x:
                pairs = zip(x["reaches"], x["roles"])
                print("      reaches %s" % " ".join("%s:%s" % (a, b) for a, b in pairs))
            else:
                print("      index %s (%s), unresolved" % (x["index"], x["index_kind"]))


def main():
    ap = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        epilog=USAGE,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--tunes", help="comma-separated tune ids or stems; default the whole cache")
    ap.add_argument("--classes", help="also print each record in these comma-separated classes")
    ap.add_argument("-j", "--procs", type=int, default=32)
    ap.add_argument("-o", "--out", default=str(ROOT / "out" / "lift_triage.json"))
    args = ap.parse_args()

    tunes = _sweep.entries(args.tunes.split(",") if args.tunes else None)
    if not tunes:
        sys.exit("no cached tune matched")
    t0 = time.monotonic()
    with mp.Pool(min(len(tunes), args.procs), _sweep.arm) as pool:
        rows = _sweep.check_rows(pool.map(one, tunes))
    done = [r for r in rows if "error" not in r]
    out = {
        "tunes": len(done),
        "refused": [r for r in rows if "error" in r],
        "wall_s": round(time.monotonic() - t0, 1),
        "lane_total": _totals(done, "lane"),
        "unnamed_total": _totals(done, "unnamed"),
        "unnamed_ruled_total": _totals(done, "unnamed_ruled"),
        "rows": rows,
    }
    Path(args.out).parent.mkdir(exist_ok=True)
    Path(args.out).write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in out.items() if k != "rows"}, indent=1))
    if args.classes:
        _report(done, set(args.classes.split(",")))


if __name__ == "__main__":
    main()
