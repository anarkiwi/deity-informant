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
from . import emit, lift as t2lift, lower
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
    """``(cursor address, {region id: column}, stride, entries, keys)`` -- T2's selector.

    ``keys`` is what the cell that selects a record holds for each of them: the
    values T2 saw it take, which is the record's own number where the tune keeps
    one and the offset it already is where the tune keeps that.
    """
    regs = emit.by_name(view, names)
    for s in art["t2"]["selectors"] + art["t2"]["streams"]:
        if s["kind"] != "selector":
            continue
        _name, _at, addr = s["cursor"].partition("@$")
        cols = {regs[c["table"]].id: c["table"] for c in s["columns"] if c["table"] in regs}
        stride = max(s["columns"][0]["stride"], 1) if s["columns"] else 1
        seen = sorted(s.get("visited") or ())
        keys = seen if len(seen) == s["entries"] else list(range(s["entries"]))
        return int(addr, 16), cols, stride, s["entries"], keys
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


def registers():
    """``{offset from the chip's own base: register name}``: its own columns.

    A store names its register by the address it stands at and not by the region
    it lands in: a family that writes the whole file through one indexed store
    has one region for all of it, and the seven of a voice are the offsets the
    voice map moves (section 3.1).
    """
    off = {v: k for k, v in REG.items()}
    off.update({v: k for k, v in CHIP.items() if "." not in k})
    return off


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


# the pinned reads section 8 calls external: the other three kinds are never one
EXTERNAL = ("raster", "cia", "sid_readback", "io")


def pinned_inputs(prog, img):
    """``({address: value}, refusals)``: the tick's pinned reads as data (section 8).

    ``ack``, ``entry_reg`` and ``uninit_ram`` are never external, so the byte the
    post-init image holds is the value the run reads; one of the four external
    kinds is a refusal by name, with the site S4 records it at.
    """
    out, bad = {}, []
    for site, addr, kind, _reads, _phase in prog.inputs:
        if addr >= 0x10000:
            continue
        if kind in EXTERNAL:
            bad.append(("$%04X" % addr, "$%04X" % site, kind))
        else:
            out[addr] = int(img[addr])
    return out, bad


SITES = "_sites"


def unsite(streams):
    """The rows as the object carries them: the join's own bookkeeping taken off."""
    for st in streams.values():
        for r in st["rows"]:
            r.pop(SITES, None)
    return streams


def widen(cellseed, globseed, merged, img, cells):
    """The halves T1 named as one word, seeded as the one 16-bit cell they are."""
    for lo, hi in merged:
        if lo.startswith("#"):
            base = cells.baseof(hi[1:])
            globseed[lo[1:]] = globseed.pop(lo[1:], 0) | (int(img[base]) if base else 0) << 8
            globseed.pop(hi[1:], None)
        elif lo in cellseed and hi in cellseed:
            cellseed[lo] = [a | b << 8 for a, b in zip(cellseed[lo], cellseed.pop(hi))]
    return cellseed, globseed


def prune(obj):
    """``state0`` held to the cells the object reads or writes: a dead seed is no cell."""
    live = set(obj["meta"].get("wide", ())) | {obj["meta"]["tempo"]["cell"]}
    live |= {a["cell"].lstrip("#") for a in obj["accs"].values()}
    for st in obj["streams"].values():
        for r in st["rows"]:
            live |= {s[0].lstrip("@#!") for s in r.get("sets", ())}
    live |= _reads([obj["streams"], obj["accs"], obj["score"], obj["meta"]])
    live = {n.split(".")[0] for n in live}
    obj["state0"]["cells"] = {k: v for k, v in obj["state0"]["cells"].items() if k in live}
    obj["state0"]["globals"] = {k: v for k, v in obj["state0"]["globals"].items() if k in live}
    return obj


def site_of(src):
    """One store site as the certificate and T0/T1 name it."""
    return None if src is None else "$%04X" % src


def sets_of(parts):
    """The plain assignments of one lowered block, its accumulator stores taken out."""
    return [[t, v] for t, v, _s in parts]


def row_of(low, lbl, extra, local=None, guard=None):
    """One block as a row of a stream, and the accumulator stores it is split at.

    Each row carries the store site of every assignment under ``_sites``, which
    is what :mod:`.recognise` joins T1's accumulator records to and which is
    stripped once the join has run.
    """
    when, parts = low.row(lbl, extra, local, guard)
    out = []
    for sets, acc in parts:
        if sets:
            row = {"when": [list(x) for x in when], "sets": sets_of(sets)}
            row[SITES] = [site_of(s) for _t, _v, s in sets]
            out.append(("row", row))
        if acc is not None:
            out.append(("acc", acc[1], acc[2], [list(x) for x in when], acc[3]))
    return out


def stream_items(low, seq, trips):
    """A segment as ranked items: runs of guarded rows, and the accumulators between."""
    items, rows = [], []
    for lbl, extra, local, guard in seq:
        if lbl == lower.RESET:  # the cells the joins of this segment read, before them
            rows.append(
                {"when": [], "sets": [["@" + n, 0] for n in extra], SITES: [None] * len(extra)}
            )
            continue
        if isinstance(lbl, tuple) and lbl[0] == lower.FLAG:
            low.lbl, low.local = lbl[2], {}
            rows.append(
                {"when": low.when(lbl[2], extra, guard), "sets": [["@" + lbl[1], 1]], SITES: [None]}
            )
            continue
        if lbl is None:
            rows.append(
                {
                    "when": [list(t) for t in extra],
                    "sets": [["@trap", {"trap": "loop"}]],
                    SITES: [None],
                }
            )
            continue
        for got in row_of(low, lbl, extra, local, guard):
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


class Phases:
    """The lowered segments as ranked items: runs of guarded rows, and the accs between.

    A voice's machine is one rank order over both (§4), so a segment is a list of
    ``("stream", name)`` and ``("acc", name)`` in the order its blocks stand in.
    """

    def __init__(self, low, trips):
        self.low, self.trips = low, trips
        self.streams, self.accs, self.rank = {}, {}, 0

    def add(self, name, blocks, ranked, gate=()):
        """One segment, lowered and named; ``ranked`` where the machine phase runs it.

        ``gate`` is the guard the phase itself runs under, which its rows do not
        repeat: the row phase's is ``meta.tempo.boundary``.
        """
        self.low.gate = frozenset((id(c), t) for c, t in gate)
        self.low.scope = set(blocks)
        out = []
        for it in stream_items(self.low, self.low.sequence(blocks, self.trips), self.trips):
            if it[0] == "rows":
                nm = "%s%d" % (name, len(out))
                self.streams[nm] = {"rows": it[1], "all": True}
                if ranked:
                    self.streams[nm]["rank"] = self.rank
                out.append(("stream", nm))
            else:
                nm = "acc%d" % len(self.accs)
                self.accs[nm] = acc_of(nm, it[1], it[2], it[3], self.rank, it[4])[1]
                out.append(("acc", nm))
            self.rank += 1
        return out

    def beyond(self, words):
        """The words past the tuning every lowered stream reads through (§3.2)."""
        for st in self.streams.values():
            st["beyond"] = {"id": "the fused tuning", "words": words}


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
                at = [k for k, s in enumerate(r["sets"]) if s[0][:1] != "@" or s[0][1:] in live]
                r["sets"] = [r["sets"][k] for k in at]
                if SITES in r:  # the join's own bookkeeping, kept beside its row
                    r[SITES] = [r[SITES][k] for k in at]
                gone += n - len(at)
        if not gone:
            break
    for st in streams:
        st["rows"] = [r for r in st["rows"] if r["sets"]]
    return streams


def _reads(node):
    """Every cell and global one part of the object reads, by name."""
    out, stack = set(), [node]
    while stack:
        x = stack.pop()
        if isinstance(x, dict):
            for k, v in x.items():
                if k in ("cell", "global") and isinstance(v, str):
                    out.add(v)
                elif k == "cell":
                    out.add(v[0])
                else:
                    stack.append(v)
        elif isinstance(x, (list, tuple)):
            stack += list(x)
    return out


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


def table_streams(voc, img):
    """The const tables the lowering read at a cell, each a stream of its own bytes."""
    return {
        name: {"rows": [{"b": int(img[a])} for a in range(base, top + 1)]}
        for name, (base, top) in sorted(voc.tables.items())
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
