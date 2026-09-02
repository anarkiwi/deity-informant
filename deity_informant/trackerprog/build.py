"""B6/B7 -- the trackerprog lifted from a tune's certified artefacts.

The schedule of :mod:`.schedule`, the lowering of :mod:`.lower` and the score
the fetch regions read, assembled into the one object ``universal.py`` renders.
What the lift cannot derive it takes from a named hint, one datum a line.
"""

from __future__ import annotations

import json

from ..tuneprog import accum, pipeline, provenance
from ..tuneprog.graph import succs
from ..tuneprog.history import history
from ..tuneprog.ir import Bin, Const, Load, Store, Tuneprog, Var
from ..tuneprog.irwalk import addr_split, walk
from ..tuneprog.recover import Names
from . import emit, lift as t2lift
from .universal import CHIP, REG

BYTE = {"from": "projected", "interval": [0, 0xFF], "witness": "the 8-bit store"}


def read(out):
    """The certified artefacts of one output directory, and the presentation view."""
    prog = Tuneprog.load(out / "tuneprog.S4.json")
    s6 = json.loads((out / "tuneprog.S6.json").read_text())
    view, _st, _n = pipeline.present(prog)
    return {
        "prog": prog,
        "view": view,
        "names": Names.from_dict(s6),
        "t0": json.loads((out / "tuneprog.T0.json").read_text()),
        "t1": json.loads((out / "tuneprog.T1.json").read_text()),
        "t2": json.loads((out / "tuneprog.T2.json").read_text()),
        "cert": json.loads((out / "certificate.json").read_text()),
    }


def artefacts(prog, trace, cert, calls=None):
    """The T0/T1/T2 planes of one certified program, computed rather than read."""
    view, st, names = pipeline.present(prog)
    hist, ver = history(prog, trace, names.to_dict(), calls=calls, obs=True)
    t0 = provenance.document(view, st, names)
    t1 = accum.document(view, names, t0, hist, cert, obs=ver.obs)
    return {
        "prog": prog,
        "view": view,
        "names": names,
        "t0": t0,
        "t1": t1,
        "t2": t2lift.document(view, names, hist, cert),
        "cert": cert,
    }


def pitch_of(art, view, names):
    """``(region id, index origin, entries, base note)`` of the tune's own tuning."""
    rid = next((r for r, k in names.role.items() if k == "freq_table"), None)
    entries = list((art["t2"].get("pitch") or {}).get("entries") or [])
    if rid is None or not entries:
        return None
    base = view.by_id()[rid].base
    origins = set()
    for p in view.procs.values():
        for b in p.blocks.values():
            for s in list(b.stmts) + [b.term]:
                origins |= _origins(s, rid)
    org = min(origins, default=base)
    return rid, org, len(entries), (base - org) // 2


def _origins(s, rid):
    """Every constant a statement reads one region at: the table's own index origin."""
    out = set()
    for e in (getattr(s, "e", None), getattr(s, "a", None), getattr(s, "v", None)):
        for x in walk(e) if e is not None else ():
            if type(x) is Load and x.r == rid:
                o, i = addr_split(x.a)
                if o is not None and i is not None:
                    out.add(o)
    return out


def _shifted(e):
    """``(term, shift)`` where an index is a left shift by a constant."""
    if type(e) is Bin and e.op == "<<" and type(e.b) is Const:
        return e.a, e.b.v
    return e, 0


def note_base(low, rid, org, procs):
    """The cell every read of the tuning indexes it by: the voice's own note."""
    got = {}
    loads = [
        x
        for p in procs
        for b in p.blocks.values()
        for s in b.stmts
        for x in walk(getattr(s, "e", None) or Const(0, 1))
        if type(x) is Load and x.r == rid
    ]
    for x in loads:
        base, idx = addr_split(x.a)
        if base is None or idx is None or abs(base - org) > 3:
            continue
        term, k = _shifted(low.expand(idx, 2))
        nb = addr_split(term.a)[0] if k == 1 and type(term) is Load else None
        if nb is not None and addr_split(term.a)[1] is not None:
            got[nb] = got.get(nb, 0) + 1
    return max(got, key=got.get, default=None)


def instrument_table(art, view, names):
    """``(cursor address, {region id: column}, stride, entries)`` -- T2's own selector."""
    regs = emit.by_name(view, names)
    for s in art["t2"]["selectors"] + art["t2"]["streams"]:
        if s["kind"] != "selector":
            continue
        _name, _at, addr = s["cursor"].partition("@$")
        cols = {regs[c["table"]].id: c["table"] for c in s["columns"] if c["table"] in regs}
        stride = max(s["columns"][0]["stride"], 1) if s["columns"] else 1
        return int(addr, 16), cols, stride, s["entries"]
    return None


def pw_columns(art, view, names):
    """The instrument-scoped pair the play writes and the chip reads as ``pw``."""
    regs, out = emit.by_name(view, names), {}
    for w in art["t0"].get("writes") or ():
        if w.get("register") not in ("pw_lo", "pw_hi"):
            continue
        for c in w.get("cells") or ():
            r = view.by_id().get(c["region"])
            if r is not None and r.kind == "state" and r.stride > 1:
                out[c["region"]] = w["register"][3:]
    del regs, names
    return out


SIDBASE = 0xD400


def registers(view):
    """``{region id: register name}`` for the chip's own columns, by their base."""
    off = {v: k for k, v in REG.items()}
    off.update({v: k for k, v in CHIP.items() if "." not in k})
    out = {}
    for r in view.storage:
        if r.kind != "io" or not SIDBASE <= r.base < SIDBASE + 0x20:
            continue
        name = off.get(r.base - SIDBASE)
        if name is not None:
            out[r.id] = name
    return out


def divider_rate(store, low, img):
    """``rate``: the tick-level counter's reload, plus the step it takes."""
    v = low.expand(store.v)
    if type(v) is Load:
        base, idx = addr_split(v.a)
        if base is not None and idx is None:
            return int(img[base]) + 1
    return int(v.v) + 1 if type(v) is Const else 1


def divider_phase(img, addr, reload_, rate):
    """The residue class the tick-level counter admits the row clock on."""
    d = int(img[addr])
    for t in range(rate * 4):
        d = reload_ if (d - 1) & 0x80 else (d - 1) & 0xFF
        if d == reload_:
            return t % rate
    return 0


def beyond_words(cells, org, n, limit):
    """§3.2's words past the tuning, by how far past: the cells the fused region holds."""
    out = []
    for d in range(limit):
        halves = []
        for k in range(2):
            addr = org + 2 * (n + d) + k
            got = cells.wordat(addr)
            if got is None:
                halves = None
                break
            kind, pay = got
            if kind == "voice":
                halves.append({"cell": [cells.voicecell(addr - pay[1]), pay[1]]})
            elif kind == "global":
                halves.append({"global": pay})
            else:
                halves = None
                break
        out.append({"u16": halves} if halves else {"trap": "no cell holds %d past" % d})
    return out


def sets_of(parts):
    """The plain assignments of one lowered block, its accumulator stores taken out."""
    return [[t, v] for t, v in parts]


def row_of(low, lbl, extra, local=None):
    """One block as a row of a stream, and the accumulator stores it is split at."""
    when, parts = low.row(lbl, extra, local)
    out = []
    for sets, acc in parts:
        if sets:
            out.append(("row", {"when": [list(x) for x in when], "sets": sets_of(sets)}))
        if acc is not None:
            out.append(("acc", acc[1], acc[2], [list(x) for x in when], acc[3]))
    return out


def stream_items(low, seq, trips):
    """A segment as ranked items: runs of guarded rows, and the accumulators between."""
    items, rows = [], []
    for lbl, extra, local in seq:
        if lbl is None:
            rows.append({"when": [list(t) for t in extra], "sets": [["@trap", {"trap": "loop"}]]})
            continue
        for got in row_of(low, lbl, extra, local):
            if got[0] == "row":
                rows.append(got[1])
            else:
                if rows:
                    items.append(("rows", rows))
                    rows = []
                items.append(("acc", got[1], got[2], got[3], got[4]))
    del trips
    if rows:
        items.append(("rows", rows))
    return items


def dce(streams, keep):
    """Drop the ``sets`` whose cell nothing reads: a cell no consumer reads is no cell."""
    for _ in range(8):
        live = set(keep)
        for st in streams:
            for r in st["rows"]:
                live |= _cellnames(r.get("when", [])) | _cellnames([s[1] for s in r["sets"]])
        gone = 0
        for st in streams:
            for r in st["rows"]:
                n = len(r["sets"])
                r["sets"] = [s for s in r["sets"] if s[0][:1] != "@" or s[0][1:] in live]
                gone += n - len(r["sets"])
        if not gone:
            break
    for st in streams:
        st["rows"] = [r for r in st["rows"] if r["sets"]]
    return streams


def _cellnames(node):
    """Every cell one lowered expression reads."""
    out, stack = set(), [node]
    while stack:
        x = stack.pop()
        if isinstance(x, dict):
            for k, v in x.items():
                if k == "cell" and isinstance(v, str):
                    out.add(v)
                elif k == "cell":
                    out.add(v[0])
                else:
                    stack.append(v)
        elif isinstance(x, (list, tuple)):
            stack += list(x)
    return out


def voice_order(p, head, latches, vidx, n):
    """The order one pass over the voices takes: the index its pre-header binds."""
    src = {x.n: x.e for b in p.blocks.values() for x in b.stmts if type(x) is not Store}
    start = None
    for lbl, b in p.blocks.items():
        if lbl in latches or head not in succs(b.term):
            continue
        for x in b.stmts:
            if getattr(x, "n", None) not in vidx:
                continue
            e, seen = x.e, 0
            while type(e) is Var and e.n in src and seen < 8:
                e, seen = src[e.n], seen + 1
            if type(e) is Const:
                start = e.v
    return [(start or 0) - k for k in range(n)]


def _supplied(low, blocks):
    """The names a first pass could not lower: the score's own bytes, and refusals."""
    out = set()
    for lbl in blocks:
        low.lbl = lbl
        for s in low.proc.blocks[lbl].stmts:
            low.one(s)
    out |= {n for n in low.bad if n in low.defs}
    return out


def order_letters(low, rid):
    """The supplied names whose load is the order table: each row's own pattern number."""
    out = set()
    for n in low.v.supplied:
        e = low.defs.get(n)
        for x in walk(e) if e is not None else ():
            if type(x) is Load and x.r == rid:
                out.add(n)
    return out


def score_of(records, low, vvar, ordernames, tempo, voices, ordpos=None, keep=None):
    """The score the fetches read: per-voice orders of patterns of events.

    A visit ends where the fetch stepped the *order* cursor T2 named -- which is
    the pattern's own end, and the only place the score's shape comes from.
    """
    rows = {v: [] for v in range(voices)}
    for got in records:
        v = got["env"].get(vvar)
        if v is None or v not in rows:
            continue
        sets = [
            ["@" + low.temps[n], int(got["temps"][n])]
            for n in got["seen"]
            if n in low.temps and n in got["temps"] and (keep is None or low.temps[n] in keep)
        ]
        pat = next((int(got["temps"][n]) for n in got["seen"] if n in ordernames), 0)
        dur = next((c[2] for c in got["cmds"] if c[0] == "ram" and c[1] == tempo + v), 0)
        ends = ordpos is not None and any(c[0] == "ram" and c[1] == ordpos + v for c in got["cmds"])
        rows[v].append((pat, dur, sets, ends))
    orders, pats = [], {}
    for v in range(voices):
        play, cur, last = [], [], None
        for pat, dur, sets, ends in rows[v]:
            if last is not None and (pat != last or cur and cur[-1][2]):
                _visit(play, pats, last, cur)
                cur = []
            cur.append((dur, sets, ends))
            last = pat
        if cur:
            _visit(play, pats, last, cur)
        orders.append({"play": play, "end": {"jump": 0}})
    return orders, pats


def _visit(play, pats, pat, rows):
    """One visit of one pattern: its events, kept once and named by what they decode to."""
    key = (pat, tuple((d, tuple(tuple(s) for s in ss)) for d, ss, _e in rows))
    name = pats.get(key)
    if name is None:
        name = pats[key] = len(pats)
    play.append(name)


def patterns_of(pats):
    return {
        str(name): {
            "events": [
                {
                    "dur": d,
                    "sounds": False,
                    "note": None,
                    "gate": None,
                    "tie": False,
                    "ins": None,
                    "arm": {"rows": [{"sets": [list(s) for s in ss]}]},
                }
                for d, ss in rows
            ]
        }
        for (_p, rows), name in pats.items()
    }


def acc_of(name, cell, expr, when, rank, src=0):
    """One store the object has no ``sets`` target for: a reload accumulator (§5)."""
    return name, {
        "site": "$%04X" % src,
        "rank": rank,
        "cell": cell,
        "target": "pw",
        "width": 8,
        "policy": {"reload": expr},
        "bound": dict(BYTE),
        "rate": 1,
        "scope": "instrument",
        "produce": [],
        "when": when,
    }


def coverage(low, prog, proc, segs, glob, streams, accs, t1):
    """B7's numbers: the store sites lowered, recognised and refused, and their leaves."""
    p = prog.procs[proc]
    blocks = sum(segs.values(), []) + list(glob)
    sites = [s for l in blocks for s in p.blocks[l].stmts if type(s) is Store and s.cls != "chk"]
    leaves = {}
    for st in streams:
        for r in st["rows"]:
            _leafkinds([x[1] for x in r["sets"]] + list(r.get("when", [])), leaves)
    for a in accs.values():
        _leafkinds([a["policy"]["reload"]] + list(a.get("when", [])), leaves)
    sited = {a.get("site") for a in accs.values()}
    lowered = {"$%04X" % s.src for s in sites}
    t1got = [
        {
            "id": a["id"],
            "cell": a["cell"]["name"],
            "sites": a.get("sites") or [],
            "form": _form(a.get("sites") or [], sited, lowered),
        }
        for a in (t1 or {}).get("accs") or ()
    ]
    return {
        "store_sites": len(sites),
        "rows": sum(len(st["rows"]) for st in streams),
        "sets": sum(len(r["sets"]) for st in streams for r in st["rows"]),
        "accs": len(accs),
        "refused": sorted(low.bad - set(low.v.supplied)),
        "leaves": dict(sorted(leaves.items())),
        "t1_accumulators": t1got,
        "t1_recognised": sum(1 for a in t1got if a["form"] == "acc"),
    }


def _form(sites, sited, lowered):
    """Where a T1 accumulator's own store landed: an ``Acc``, a ``sets`` row, or neither."""
    if any(s in sited for s in sites):
        return "acc"
    return "sets" if any(s in lowered for s in sites) else "refused"


def _leafkinds(nodes, out):
    """How many of each section 5 leaf form the lowered rows carry."""
    stack = list(nodes)
    while stack:
        x = stack.pop()
        if isinstance(x, int):
            out["const"] = out.get("const", 0) + 1
        elif isinstance(x, (list, tuple)):
            stack += list(x)
        elif isinstance(x, dict):
            for k, v in x.items():
                if k in ("cell", "global", "ins", "flag"):
                    out[k] = out.get(k, 0) + 1
                elif k in ("transpose", "tuned"):
                    out["pitch"] = out.get("pitch", 0) + 1
                    stack.append(v)
                else:
                    out[k] = out.get(k, 0) + 1
                    stack.append(v)
    return out
