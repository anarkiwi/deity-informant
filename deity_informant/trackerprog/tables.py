"""B7 -- the tune's own tables: its tuning, its instrument records and its widths.

What T2 named and where the play reads it: the tuning's two halves and the words
past them, the record one instrument is, the pulse pair, and every constant
address the play also reads as a word.
"""

from __future__ import annotations

from collections import namedtuple

from ..tuneprog import irwalk
from ..tuneprog.ir import Bin, Const, Load, Store
from ..tuneprog.irwalk import addr_split, walk
from . import emit

NOTES = 256  # a note is a byte, so no index of the tuning is past this


Tuning = namedtuple("Tuning", "rids org obases step n base")


def _origin_of(view, rid):
    """The constant a load reads one region at: the table's own index origin."""
    got = set()
    for p in view.procs.values():
        for b in p.blocks.values():
            for s in list(b.stmts) + [b.term]:
                got |= _origins(s, rid)
    return min(got, default=view.by_id()[rid].base)


def pitch_of(art, view, names):
    """The tune's own tuning: T2's layout as the two halves' origins and their step.

    One word table steps 2 and holds its halves one after the other; two byte
    tables step 1 and hold one half each, in the order the layout names.
    """
    doc = art["t2"].get("pitch") or {}
    entries = list(doc.get("entries") or [])
    if not entries:
        return None
    n = len(entries)
    if (doc.get("layout") or "u16le") == "u16le":
        rid = next((r for r, k in names.role.items() if k == "freq_table"), None)
        if rid is None:
            return None
        org = _origin_of(view, rid)
        base = view.by_id()[rid].base
        return Tuning((rid,), org, (org, org + 1), 2, n, (base - org) // 2)
    byid = view.by_id()
    rids = [r for r in doc.get("regions") or () if names.role.get(r) == "freq_table"]
    rids = [r for r in rids if byid[r].size >= n][:2]
    if len(rids) != 2:
        return None
    if (doc.get("layout") or "").split("|")[0] == "hi":
        rids = rids[::-1]
    obases = tuple(_origin_of(view, r) for r in rids)
    return Tuning(tuple(rids), obases[0], obases, 1, n, byid[rids[0]].base - obases[0])


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


def note_base(low, tune, procs):
    """The cell every read of the tuning indexes it by: the voice's own note."""
    got = {}
    loads = [
        x
        for p in procs
        for b in p.blocks.values()
        for s in b.stmts
        for x in walk(getattr(s, "e", None) or Const(0, 1))
        if type(x) is Load and x.r in tune.rids
    ]
    want = tune.step // 2
    for x in loads:
        base, idx = addr_split(x.a)
        if base is None or idx is None or all(abs(base - b) > 3 for b in tune.obases):
            continue
        term, k = _shifted(low.expand(idx, 2))
        nb = addr_split(term.a)[0] if k == want and type(term) is Load else None
        if nb is not None and addr_split(term.a)[1] is not None:
            got[nb] = got.get(nb, 0) + 1
    return max(got, key=got.get, default=None)


def instrument_table(art, view, names):
    """``(cursor address, {region id: column}, stride, entries, keys)`` -- T2's selector.

    ``keys`` is what the cell that selects a record holds for each of them: the
    values T2 saw it take, which is the record's own number where the tune keeps
    one and the offset it already is where the tune keeps that. Of T2's selectors
    the instrument's is the widest: the record that states most of one sound.
    """
    regs = emit.by_name(view, names)
    got = [s for s in art["t2"]["selectors"] + art["t2"]["streams"] if s["kind"] == "selector"]
    if not got:
        return None
    s = max(got, key=lambda x: (len(x["columns"]), x["entries"]))
    _name, _at, addr = s["cursor"].partition("@$")
    cols = {regs[c["table"]].id: c["table"] for c in s["columns"] if c["table"] in regs}
    stride = max(s["columns"][0]["stride"], 1) if s["columns"] else 1
    seen = sorted(s.get("visited") or ())
    keys = seen if len(seen) == s["entries"] else list(range(s["entries"]))
    return int(addr, 16), cols, stride, s["entries"], keys


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


def word_widths(prog, proc):
    """``{address: the widest access it takes}`` over one procedure's constant addresses.

    A byte the play stores and reads again as a word is one 16-bit cell, and the
    store is that cell's own half.
    """
    out = {}
    for blk in prog.procs[proc].blocks.values():
        for st in list(blk.stmts) + [blk.term]:
            for x in (y for e in irwalk.node_exprs(st) for y in walk(e)):
                if type(x) is Load and x.cls in ("ram", "chk") and addr_split(x.a)[1] is None:
                    a = addr_split(x.a)[0]
                    out[a] = max(out.get(a, 1), x.w)
            if type(st) is Store and st.cls in ("ram", "chk") and addr_split(st.a)[1] is None:
                a = addr_split(st.a)[0]
                out[a] = max(out.get(a, 1), st.w)
    out.pop(None, None)
    return out


def beyond_limit(cells, low, tune):
    """How far past the tuning the object can answer, in words: the last index it has.

    The rest of the region the tuning is fused into, and past a region that ends
    at its own last entry the bytes the play never writes, which the image states
    once and for all. A note is a byte, so no index of the tuning is past 256.
    """
    r = cells.byid[tune.rids[0]]
    d = max(0, (r.size - tune.step * tune.n) // tune.step)
    bases = tuple(b + tune.step * tune.base for b in tune.obases)
    while d < NOTES - tune.n:
        if any(not low.frozen(b + tune.step * (tune.n + d), 1) for b in bases):
            break
        d += 1
    return d


def beyond_words(cells, low, tune, limit):
    """§3.2's words past the tuning: the cells there, or the bytes the image holds."""
    out = []
    bases = tuple(b + tune.step * tune.base for b in tune.obases)
    for d in range(limit):
        halves = [_beyond(cells, low, bases[k] + tune.step * (tune.n + d)) for k in range(2)]
        ok = all(h is not None for h in halves)
        out.append({"u16": halves} if ok else {"trap": "no cell holds %d past" % d})
    return out


def _beyond(cells, low, addr):
    """One half of a word past the tuning: its own byte, or the cell that holds it."""
    if low.frozen(addr, 1):  # a byte the play never writes: the image states it
        return int(low.v.img[addr])
    kind, pay = cells.wordat(addr) or (None, None)
    if kind == "voice":
        return {"cell": [cells.voicecell(addr - pay[1] * cells.stride), pay[1]]}
    return {"global": pay} if kind == "global" else None


# the pinned reads section 8 calls external: the other three kinds are never one
EXTERNAL = ("raster", "cia", "sid_readback", "io")
