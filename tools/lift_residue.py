"""Find where the lift failed, by what survived rather than by what refused.

A rung's counters report only what that rung looked at, so a generalization nobody
wrote is invisible to all of them. This reads the emitted program: every node still
wearing a machine shape is a site, and the value graph tells a root from its cascade.
"""

import argparse
import json
import multiprocessing as mp
import signal
import sys
import time
from collections import Counter, defaultdict
from functools import partial
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

HVSC = ROOT / ".oracle-cache" / "hvsc"
CAP_S = 1800

USAGE = """\
  python tools/lift_residue.py                              # the whole cache, ranked
  python tools/lift_residue.py --tunes Commando --show 8    # one tune, with examples
  python tools/lift_residue.py --sig borrow --show 20       # drill into one signature"""

# What each surviving shape says the lift did not do: a machine artifact left behind.
SIGS = {
    "word_pack": "two byte columns still packed by hand into one word",
    "hi_byte": "a word's high byte extracted, so the word is being read as bytes",
    "lo_byte": "a word's low byte extracted, likewise",
    "borrow": "a comparison feeding arithmetic: a borrow/carry chain, unfolded",
    "carry_val": "``carry(..)`` as a value: the flag outlived the operation",
    "flag_bit": "a status bit recomputed as a mask-and-compare",
    "shift_pair": "a shift threaded across two byte columns: one 16-bit shift",
    "narrow_sink": "a byte store to a 16-bit SID register",
    "unnamed_addr": "an access whose address names no declared datum",
    "mod_addr": "a modular (``zp,X``) address, so no row is nameable",
    "raw_reg": "a machine register or lifter temporary in the output",
    "raw_sp": "the stack pointer survived: some procedure did not balance",
}

_BITS = frozenset((0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80))
_CMP = frozenset(
    (
        "INT_LESS",
        "INT_SLESS",
        "INT_LESSEQUAL",
        "INT_SLESSEQUAL",
        "INT_EQUAL",
        "INT_NOTEQUAL",
        "INT_CARRY",
        "INT_SCARRY",
    )
)
_ARITH = frozenset(("INT_ADD", "INT_SUB", "INT_MULT"))


def _alarm(_sig, _frame):
    raise TimeoutError("build cap")


def _arm():
    signal.signal(signal.SIGALRM, _alarm)


def _shift_pair(n):
    """``(lo >> 1) | ((hi & 1) << 7)`` and its mirror: one word shifted as two bytes."""
    from deity_informant import frameproc as P

    if not P.is_op(n, "INT_OR", arity=2):
        return False
    for a, b in P.commuted(n[2]):
        if not P.is_op(a, "INT_RIGHT") and not P.is_op(a, "INT_LEFT"):
            continue
        if not P.is_op(b, "INT_LEFT") and not P.is_op(b, "INT_RIGHT"):
            continue
        inner = b[2][0]
        if P.is_op(inner, "INT_AND") and inner[2][1][0] == "const":
            return inner[2][1][1] in (0x01, 0x80)
    return False


def _flag_bit(n):
    """``(OP(..) & BIT) != 0``: a status flag the lift left as a mask-and-compare.

    The masked value must be an operation's result, since that is what the 6502 sets
    N and Z from. The same shape over a plain load is the driver testing a bit of its
    own state byte -- program logic, not a flag the lift failed to name."""
    from deity_informant import frameproc as P

    if n[0] != "op" or n[1] not in ("INT_EQUAL", "INT_NOTEQUAL"):
        return False
    for a, b in P.commuted(n[2]):
        if b[0] != "const" or b[1] != 0 or not P.is_op(a, "INT_AND", arity=2):
            continue
        k, v = a[2][1], P.strip_zext(a[2][0])
        return k[0] == "const" and k[1] in _BITS and v[0] == "op" and v[1] not in _CMP
    return False


def _expr_sig(n, parent):
    """The residue signature ``n`` wears under ``parent``, else None."""
    from deity_informant import frameproc as P

    if n[0] in ("reg", "uni"):
        return "raw_reg"
    if n[0] == "loc" and n[1] == "sp":
        return "raw_sp"
    if n[0] == "mem":
        base, _idx = P.addr_split(n[1])
        if base is not None or n[1][0] == "const":
            return None
        got = P._index_of(n[1])
        return "mod_addr" if got is not None and got[2] else "unnamed_addr"
    if n[0] != "op":
        return None
    if P.is_op(n, "INT_CARRY") or P.is_op(n, "INT_SCARRY"):
        return "carry_val"
    if n[1] in _CMP and parent in _ARITH:
        return "borrow"
    if _flag_bit(n):
        return "flag_bit"
    if P._le_bytes(n) is not None:
        return "word_pack"
    if _shift_pair(n):
        return "shift_pair"
    if P.is_op(n, "COPY", 1):
        inner = n[2][0]
        if P.shifted_hi(inner) is not None:
            return "hi_byte"
        if P.loc_width(inner) == 2:
            return "lo_byte"
    return None


def _skeleton(n, depth=2):
    """The shape of ``n`` with names and values abstracted away, for clustering."""
    from deity_informant import frameproc as P

    if depth == 0:
        return "_"
    k = n[0]
    if k == "const":
        return "$%X" % n[1] if n[1] in _BITS or n[1] in (0, 1, 8, 0xFF) else "K"
    if k in ("loc", "reg", "uni"):
        return "v%d" % P.loc_width(n)
    if k == "mem":
        base, idx = P.addr_split(n[1])
        row = "?" if base is None else ("" if idx is None else "[i]")
        return "m%s:%d" % (row, n[2])
    kids = ",".join(_skeleton(c, depth - 1) for c in n[2])
    return "%s%d(%s)" % (n[1], n[3], kids)


def _walk_expr(n, parent, hit):
    """Every residue site in one expression, outermost first."""
    sig = _expr_sig(n, parent)
    if sig is not None:
        hit.append((sig, _skeleton(n)))
    kids = n[2] if n[0] == "op" else ((n[1],) if n[0] == "mem" else ())
    for c in kids:
        _walk_expr(c, n[1] if n[0] == "op" else parent, hit)


def _reads(n, out):
    """Every local name ``n`` reads."""
    if n[0] == "loc":
        out.add(n[1])
    elif n[0] == "op":
        for c in n[2]:
            _reads(c, out)
    elif n[0] == "mem":
        _reads(n[1], out)


def _collect(hit, rd, n):
    """``_map_exprs`` visitor: gather one expression's sites and the locals it reads."""
    _walk_expr(n, None, hit)
    _reads(n, rd)
    return n


def _stmt_sig(s):
    """The residue signature the statement itself carries, else None."""
    from deity_informant import framefuse
    from deity_informant import frameproc as P
    from deity_informant import grammar as G

    if s[0] != "st" or G.store_width(s[2]) != 1:
        return None
    base, _idx = P.addr_split(s[1])
    if base is None or framefuse._sid_base(base) is None:
        return None
    return "narrow_sink"


def _list_sites(stmts, entry, out, edges):
    """Residue sites of one statement list, and the def-use edges between them.

    A site is attributed to the statement it sits in; an edge runs from the
    statement defining a local to each statement reading it, so a site's upstream is
    whatever residue its inputs already carried."""
    from deity_informant import frameproc as P

    env = P.Defs(stmts)
    per = {}
    for k, s in enumerate(stmts):
        hit, rd = [], set()
        P._map_exprs(s, partial(_collect, hit, rd))
        sig = _stmt_sig(s)
        if sig is not None:
            hit.append((sig, _skeleton(s[1])))
        per[k] = [len(out) + i for i in range(len(hit))]
        for name, skel in hit:
            out.append({"sig": name, "skel": skel, "entry": "$%04X" % entry, "at": k})
        for name in rd:
            got = env.at(name, k)
            if got is not None:
                for up in per.get(got[0], ()):
                    for down in per[k]:
                        edges.append((up, down))
        for body in P._stmt_bodies(s):
            _list_sites(body, entry, out, edges)


def census(prog):
    """Every residue site in the program, and the def-use edges among them."""
    sites, edges = [], []
    for entry, _p, _r, stmts in prog.procs:
        _list_sites(stmts, entry, sites, edges)
    return sites, edges


def _blocking(sites, edges):
    """``upstream sig -> downstream sig -> count``: what fixing one would unblock."""
    out = defaultdict(Counter)
    for up, down in edges:
        a, b = sites[up]["sig"], sites[down]["sig"]
        if a != b:
            out[a][b] += 1
    return {k: dict(v) for k, v in out.items()}


def build(entry):
    """The frame program of one tune, as ``fuse_measure`` builds it."""
    from deity_informant import frameprog
    from deity_informant import structured as S
    from deity_informant.c64 import load_psid

    sid, sub, secs = entry
    mem, _load, init, play = load_psid(Path(sid).read_bytes())
    mem[0xD418] = 0x0F  # the filter volume the corpus is swept at
    model, _ev = S.decompile(mem, init, play, int(secs * 50), sub)
    return frameprog.program(model)


def entries(names=None):
    """``[(path, subtune, secs)]`` per cached tune, at full Songlengths length."""
    from deity_informant.c64 import load_psid, psid_songs, song_lengths, song_seconds

    lengths = song_lengths((HVSC / "Songlengths.md5").read_text(encoding="latin-1"))
    want = None if names is None else set(names)
    out = []
    for path in sorted(HVSC.rglob("*.sid")):
        if want is not None and path.stem not in want:
            continue
        data = path.read_bytes()
        _mem, _load, _init, play = load_psid(data)
        sub = psid_songs(data)[1] - 1
        secs = song_seconds(data, lengths, sub)
        if play and secs:
            out.append((str(path), sub, secs))
    return out


def one(entry):
    """One tune's census, or the exception that stopped it."""
    try:
        signal.alarm(CAP_S)
        return _one(entry)
    except Exception as exc:  # pylint: disable=broad-except
        return {"tune": Path(entry[0]).stem, "error": "%s: %s" % (type(exc).__name__, exc)}
    finally:
        signal.alarm(0)


def _one(entry):
    t0 = time.monotonic()
    sites, edges = census(build(entry))
    skels = defaultdict(Counter)
    for s in sites:
        skels[s["sig"]][s["skel"]] += 1
    return {
        "tune": Path(entry[0]).stem,
        "build_s": round(time.monotonic() - t0, 1),
        "sites": dict(Counter(s["sig"] for s in sites)),
        "skels": {k: dict(v) for k, v in skels.items()},
        "blocking": _blocking(sites, edges),
    }


def _merge(done):
    """Corpus totals: sites per signature, tunes per signature, the blocking matrix."""
    sites, tunes, skels = Counter(), Counter(), defaultdict(Counter)
    block = defaultdict(Counter)
    for r in done:
        sites.update(r["sites"])
        tunes.update(r["sites"].keys())
        for sig, per in r["skels"].items():
            skels[sig].update(per)
        for up, per in r["blocking"].items():
            block[up].update(per)
    return sites, tunes, skels, {k: dict(v) for k, v in block.items()}


def _print(sites, tunes, skels, block, show):
    """Signatures ranked by site count, then the cascade each one gates."""
    print("\n%-14s %7s %6s  %s" % ("signature", "sites", "tunes", "meaning"))
    for sig, n in sites.most_common():
        print("%-14s %7d %6d  %s" % (sig, n, tunes[sig], SIGS.get(sig, "")))
    print("\nblocking (fixing LEFT may unblock RIGHT):")
    for up in sorted(block, key=lambda k: -sum(block[k].values())):
        per = ", ".join("%s %d" % kv for kv in sorted(block[up].items(), key=lambda kv: -kv[1])[:5])
        print("  %-14s -> %s" % (up, per))
    if not show:
        return
    print("\ntop shapes per signature:")
    for sig, _n in sites.most_common():
        print("  %s:" % sig)
        for skel, n in skels[sig].most_common(show):
            print("    %6d  %s" % (n, skel))


def main():
    ap = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        epilog=USAGE,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--tunes", help="comma-separated tune stems; default the whole cache")
    ap.add_argument("--sig", help="restrict the shape listing to one signature")
    ap.add_argument("--show", type=int, default=0, help="list this many shapes per signature")
    ap.add_argument("-j", "--procs", type=int, default=32)
    ap.add_argument("-o", "--out", default=str(ROOT / "out" / "lift_residue.json"))
    args = ap.parse_args()

    tunes = entries(args.tunes.split(",") if args.tunes else None)
    if not tunes:
        sys.exit("no cached tune matched")
    t0 = time.monotonic()
    with mp.Pool(min(len(tunes), args.procs), _arm) as pool:
        rows = pool.map(one, tunes)
    done = [r for r in rows if "error" not in r]
    sites, tune_ct, skels, block = _merge(done)
    out = {
        "tunes": len(done),
        "refused": [r for r in rows if "error" in r],
        "wall_s": round(time.monotonic() - t0, 1),
        "sites": dict(sites),
        "tunes_per_sig": dict(tune_ct),
        "blocking": block,
        "skels": {k: dict(v) for k, v in skels.items()},
        "rows": rows,
    }
    Path(args.out).parent.mkdir(exist_ok=True)
    Path(args.out).write_text(json.dumps(out, indent=1), encoding="utf-8")
    print("%d tunes, %d refused, %.1fs" % (len(done), len(out["refused"]), out["wall_s"]))
    keep = sites if not args.sig else Counter({args.sig: sites.get(args.sig, 0)})
    _print(keep, tune_ct, skels, block, args.show)


if __name__ == "__main__":
    main()
