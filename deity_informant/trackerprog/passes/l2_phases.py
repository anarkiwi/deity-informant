"""L2 -- phase-normal form: the voice's pass cut into phases of predicated rows.

Region formation and if-conversion.  The voice body is cut at the fetch regions
and at the edge writes; each segment is one ``{stream}`` phase of ``meta.tick``
whose rows are that segment's blocks under the guard path each stands on, and a
``commit`` stands where a segment ended a group.  The blocks outside the voice
loop are the tick's own pre and post lists, and the register file every write
lands in is the tick's first phase, its flush.  The result is a trackerprog --
the most general the schema has, a guarded ``all: True`` row list at every
phase -- which the unchanged player renders.
"""

from __future__ import annotations

from ...tuneprog.ir import If, Load, Store, Var
from ...tuneprog.irwalk import addr_split, walk
from .. import build, schedule, shadow, tables
from ..emit import commit_order
from ..read import Reader, Unlowerable
from ..rows import ambiguous, blockrows, guards
from ..cells import ident
from ..shape import _Out, _dce, _merge_halves, _needed
from ..vocab import Vocab
from . import l2_regions
from .ir import Level

EDGE = ("ctrl", "ad", "sr")
CLOCK = "$phase"  # the counter the player steps where the tune's own rows step theirs


class PNFReader(Reader):
    """The reader phase-normal form uses: a name in a value position is its cell."""

    def value(self, e):
        if type(e) is Var and e.n in self.defs and e.n not in self.v.vidx:
            if e.n not in self.v.supplied and e.n not in self.v.subst and e.n not in self.local:
                got = self.expand(e)
                if got is not e and got != e:
                    return super().value(got)
        return super().value(e)


class Segments:
    """What the row readers ask of a level: the reader, and the names a path binds."""

    def __init__(self, low, amb):
        self.low, self.amb = low, amb


def edge_sites(t0):
    """The pcs T0 names an edge write at: where one act of the tick ends."""
    return {
        int(w["site"]["pc"].lstrip("$"), 16)
        for w in t0.get("writes") or ()
        if w["register"] in EDGE
    }


def cut(order, fetchblocks, sites, p):
    """One voice's pass as segments: the fetch is its own, and an edge ends a group.

    Section 4.1's phases: the fetch region cuts the pass in three, and inside
    each part a block that writes an edge register ends the group.
    """
    out = []
    for name, blocks in schedule.segments(order, fetchblocks, sites):
        run = []
        for lbl in blocks:
            run.append(lbl)
            if any(s.src in sites for s in p.blocks[lbl].stmts if type(s) is Store):
                out.append((name, run, True))
                run = []
        if run:
            out.append((name, run, False))
    return out


def predicates(low, blocks):
    """One predicate cell a decision: if-conversion's own register.

    A block that decides a term and then moves a cell that term reads has no
    channel for the value it decided on, so the decision is a cell, assigned
    where the block makes it and read by every row it guards.  A block the tick
    does not reach assigns nothing, and the terms that lead to it are cells of
    the same kind, so its rows stand under a guard no path made true.
    """
    out = {}
    for lbl in blocks:
        b = low.proc.blocks[lbl]
        if type(b.term) is If and b.term.t != b.term.f:
            out[lbl] = ("p" + ident(lbl), b.term.c, _late(b, b.term.c))
    return out


def _late(blk, cond):
    """Whether a condition reads a cell of the block at the terminator, past its store.

    A name the block bound is the value it had where it was bound; a load the
    condition itself makes is the value the block leaves.
    """
    put = {addr_split(s.a)[0] for s in blk.stmts if type(s) is Store and s.cls == "ram"}
    return any(type(x) is Load and addr_split(x.a)[0] in put for x in walk(cond))


def guardof(low, terms):
    """One guard list read where it stands, each term the cell its decision left."""
    when = []
    for d, c, t in terms:
        if not low.onpath(d, c, t):
            continue
        low.lbl = d
        fact = low.v.terms.get(repr(c))
        term = [fact, "!=" if t else "==", 0] if fact is not None else low.term(low.expand(c), t)
        if term not in when:
            when.append(term)
    return when


def picks(amb, lbl, path):
    """A name several blocks bind takes the definition of the block on this path."""
    out = {}
    for n, d in amb.items():
        for q in [lbl] + list(path):
            if q in d:
                out[n] = d[q]
                break
    return out


def predrow(seg, lbl, name, cond, late=False):
    """The row one decision is: the block's own guard, and the cell it leaves it in."""
    low = seg.low
    path = [d for d, _c, _t, _w in low.guards.get(lbl, ())]
    low.lbl, low.local, low.sub, low.turn = lbl, {}, {}, None
    low.pick = picks(seg.amb, lbl, path)
    when = guardof(low, [(d, c, t) for d, c, t, _w in low.guards.get(lbl, ())])
    low.lbl = lbl
    got = {"sets": [["@" + name, low.value(low.expand(cond))]]}
    del late
    return {**({"when": when} if when else {}), **got}


def flagrows(low, lbl):
    """The rows one block raises for a join no path of the tick folds (B7's ``planall``).

    The reaching condition of a block two paths carry is a disjunction, which the
    one guard shape of §3.3 cannot state, so every path that reaches it raises a
    cell where that path already stands and the block's own guard reads it.  The
    cells are cleared once, at the head of the tick.
    """
    out = []
    for name, ctx in low.flagrows.get(lbl, ()):
        low.lbl, low.local, low.pick, low.sub, low.turn = lbl, {}, {}, {}, None
        out.append({"when": guardof(low, ctx[0]), "sets": [["@" + name, 1]]})
    return out


def raised(low, lbl):
    """The terms a block's own guard carries for a join no path of the tick folds."""
    return [list(t) for t in low.eff.get(lbl, ((), ()))[1]]


def blockstmts(seg, lbl, order, preds):
    """One block as statements, in program order: its decision, then its stores."""
    low = seg.low
    got, up, rows = preds.get(lbl), raised(low, lbl), []
    for _l, kind, when, sets, _d in guards(
        seg, blockrows(seg, {lbl}, order, set(), {}, True), order
    ):
        if kind in ("set", "reg"):
            rows.append({"when": when, "sets": [list(x) for x in sets]})
    if got is not None:
        # a decision over a cell the block itself moved is read where the block
        # ends: read-after-write is the list's own order, not a second row
        rows.insert(len(rows) if got[2] else 0, predrow(seg, lbl, *got))
    rows += flagrows(low, lbl)
    low.pick = {}
    for r in rows:
        r["when"] = up + [t for t in (r.get("when") or []) if t not in up]
    return rows


def segrows(seg, blocks, order, preds, p=None, head=None):
    """One segment as a region tree: its loops kept, its blocks in program order."""

    def rows_of(bset, ordering):
        out = []
        for lbl in [l for l in ordering if l in bset]:
            out += blockstmts(seg, lbl, order, preds)
        return out

    if p is None:
        return rows_of(blocks, order)
    return l2_regions.tree(seg.low, p, blocks, order, rows_of, head)


def _every(s):
    """A per-voice cell a channel row writes is every voice's: §3.6's own ``all``.

    The tick's channel has no voice to commit through, so a name the row writes
    on one is a name it writes on all.
    """
    return ["*" + s[0][1:] if s[0][:1] == "@" else s[0], s[1]]


def channelrows(rows, key, seed):
    """``(rows, commit)``: what the tick's own channel keeps, and what it sends.

    The tick's channel has no voice to commit through, so a register a channel
    row names is one entry of ``globals.commit`` (§3.7).  That commit runs after
    all of the channel, so the row stages the value in a cell of its own where
    it computed it and the entry sends that cell under the row's own guard.
    """
    out, commit = [], []
    for r in rows:
        keep = [_every(s) for s in r.get("sets", ()) if not _needed(s[0])]
        for tgt, val in [s for s in r.get("sets", ()) if _needed(s[0])]:
            name = "%s$%s%d" % (key, tgt, len(commit))
            seed[name] = 0
            keep.append(["#" + name, val])
            commit.append([tgt, {"global": name}] + ([r["when"]] if r.get("when") else []))
        if keep:
            out.append({**({"when": r["when"]} if r.get("when") else {}), "sets": keep})
    return out, commit


def lead(low, sch, fetchblocks):
    """The terms the fetch stands under that the row's own boundary does not.

    A fetch one tick ahead of the boundary it stages for reads under a guard of
    its own; where the two guards are one, the fetch is the row.
    """
    if not sch.clock or not fetchblocks:
        return []
    keep = {id(c) for c, _t in sch.boundary}
    got = []
    for lbl in sorted(fetchblocks):
        for d, c, t, _w in low.guards.get(lbl, ()):
            if id(c) in keep:
                continue
            low.lbl, low.local = d, {}
            term = low.term(low.expand(c), t)
            if term not in got:
                got.append(term)
    return got


def _channel(order, body, head):
    """``(before, after)``: the blocks of the tick that are no voice's own."""
    at = order.index(head) if head in order else len(order)
    outside = [l for l in order if l not in body]
    return [l for l in outside if order.index(l) < at], [l for l in outside if order.index(l) > at]


def reader(l1):
    """The expression reader over the structured tick, with its own vocabulary."""
    art, prog, proc = l1.art, l1.prog, l1.proc
    cells, img = l1.facts["cells"], prog.reads()
    voc = Vocab(cells, img, build.registers(), l1.facts["vidx"])
    pit = l1.facts["pitch"]
    if pit is not None:
        voc.pitch = (pit.rids, pit.obases, pit.step, pit.n)
    ins = tables.instrument_table(art, art["view"], art["names"])
    if ins:
        voc.insbase, voc.inscol, voc.insstride = ins[0], ins[1], ins[2]
    voc.inspw = tables.pw_columns(art, art["view"], art["names"])
    voc.img = img
    if pit is not None:
        voc.notebase = tables.note_base(PNFReader(prog, proc, cells, voc), pit, [prog.procs[proc]])
    sh = shadow.of(art["t0"], prog, art["view"])
    if sh is not None:
        voc.shadow = (sh.base, sh.size)
    low = PNFReader(prog, proc, cells, voc)
    # every cell read is the cell: a store forwarded into a block a second path
    # also reaches is a *may* fact read as a must.  The later levels specialise
    low.reach = {lbl: {} for lbl in low.proc.blocks}
    return low, voc, sh


def unstatable(l1, fetchblocks=()):
    """The decisions of a tick whose condition no value of this level's vocabulary states.

    One entry a block: the label, why the read has no name, and whether the block
    stands in the fetch region the level cuts the pass at.
    """
    low = reader(l1)[0]
    out = []
    for lbl in low.rpo:
        t = low.proc.blocks[lbl].term
        if type(t) is not If or t.t == t.f:
            continue
        low.lbl, low.local, low.pick, low.sub, low.turn = lbl, {}, {}, {}, None
        try:
            low.value(low.expand(t.c))
        except Unlowerable as x:
            out.append(
                {"block": lbl, "why": str(x), "region": "fetch" if lbl in fetchblocks else "voice"}
            )
    return out


def phases(l1, fetchblocks=(), ticks=None):  # noqa: C901 - one clause a section
    """L1 to L2: the phases, their predicated rows, and the tick's own channel."""
    art, prog, proc = l1.art, l1.prog, l1.proc
    p = prog.procs[proc]
    low, voc, sh = reader(l1)
    order, body, head = low.rpo, l1.facts["body"], l1.facts["head"]
    fetchblocks = frozenset(fetchblocks)
    sites = edge_sites(art["t0"])
    flush = sh.blocks if sh is not None else frozenset()
    inner = [l for l in order if l in body and l not in flush]
    segs = cut(inner, fetchblocks, sites, p)
    before, after = _channel(order, body | flush, head)
    seg = Segments(low, ambiguous(p))
    flags = low.planall([list(g) for _n, g, _c in segs] + [before, after])
    preds = predicates(low, [l for _n, g, _c in segs for l in g] + before + after)
    for name, cond, _late_ in preds.values():
        voc.terms[repr(cond)] = {"cell": name}
    out, tick, pre, post, commit, staged = _Out(), [], [], [], [], {}
    if flags:
        tick.append({"stream": out.stream("flags", [{"sets": [["@" + n, 0] for n in flags]}])})
    for i, (name, blocks, group) in enumerate(segs):
        got = segrows(seg, set(blocks), order, preds, p, head)
        if got:
            tick.append({"stream": out.stream("%s%d" % (name, i), got)})
        if group:
            tick.append("commit")
    for key, blocks, into in (("pre", before, pre), ("post", after, post)):
        for i, lbl in enumerate(blocks):
            got, sent = channelrows(segrows(seg, {lbl}, order, preds, p, head), key, staged)
            commit += sent
            if got:
                into.append(out.stream("%s%d" % (key, i), got))
    cells = l1.facts["cells"]
    cellseed, globseed = cells.seed(prog.reads())
    cellseed[CLOCK] = [0] * cells.voices
    for name, _c, _l in preds.values():
        cellseed[name] = [0] * cells.voices
    for name in flags:
        cellseed[name] = [0] * cells.voices
    globseed.update(staged)
    pit = l1.facts["pitch"]
    obj = {
        "$trackerprog": 1,
        "meta": {
            "tune": prog.meta.get("name"),
            "song": prog.meta.get("song"),
            "family": "pnf",
            "cycles_per_tick": prog.meta["entry"]["cycles_per_tick"],
            "voices": cells.voices,
            "horizon": ticks or art["t2"]["horizon"]["ticks"],
            "voice_order": build.voice_order(
                p, head, l1.facts["latches"], l1.facts["vidx"], cells.voices, cells.stride
            ),
            "commit_order": list(commit_order(art["t0"])),
            "instrument": {},
            "tempo": {"cell": CLOCK, "step": 0, "rate": 1, "phase": 0, "boundary": [[0, "!=", 0]]},
            "tick": tick,
            "row_consumes_tick": False,
            "row": [],
            "wide": sorted(low.wide),
            **({"shadow": {"registers": list(sh.registers)}} if sh is not None else {}),
        },
        "pitch": (
            {"base": pit.base, "freq": list(art["t2"]["pitch"]["entries"])}
            if pit is not None
            else {"base": 0, "freq": []}
        ),
        "streams": {**out.streams, **build.table_streams(voc, prog.reads())},
        "accs": {},
        "instruments": {},
        "score": {"patterns": {}, "orders": [[] for _ in range(cells.voices)]},
        # both lists go through the one liveness the shape module runs, and are
        # split back after it: a stream the channel names is live either way
        "globals": {"streams": pre + post, **({"commit": commit} if commit else {})},
        "state0": {
            "cells": cellseed,
            "globals": globseed,
            **({"shadow": shadow.seed(prog.reads(), sh)} if sh is not None else {}),
        },
    }
    _merge_halves(obj)
    # a write into the image is observable: the flush sends it, so liveness has
    # the image itself for a root, as it has the registers
    if sh is not None:
        obj["globals"]["$shadow"] = [{"cell": "shadow"}]
    _dce(obj)
    build.prune(obj)
    obj["globals"].pop("$shadow", None)
    got = obj["globals"].get("streams", [])
    obj["globals"] = {
        **{k: v for k, v in obj["globals"].items() if k != "streams"},
        **({"streams": [k for k in got if k in pre]} if any(k in pre for k in got) else {}),
        **({"after": [k for k in got if k in post]} if any(k in post for k in got) else {}),
    }
    return Level(
        2,
        art=art,
        prog=prog,
        proc=proc,
        obj=obj,
        facts={
            **l1.facts,
            "segments": [(n, tuple(g)) for n, g, _c in segs],
            "fetchblocks": frozenset(fetchblocks),
            "channel": (tuple(before), tuple(after)),
            "flush": tuple(sh.registers) if sh is not None else (),
            "stage_guard": lead(low, _sch(prog, proc, fetchblocks, art["t0"], order), fetchblocks),
            "reader": low,
            "vocab": voc,
            "predicates": {l: n for l, (n, _c, _l) in preds.items()},
            "joins": list(flags),
            "refused": sorted(low.bad),
            "loops": [n for _n, g, _c in segs for n in l2_regions.loops(p, set(g), head)],
            "unstated_loops": [
                n for _n, g, _c in segs for n in l2_regions.unstated(low, p, set(g), head)
            ],
        },
    )


def _sch(prog, proc, fetchblocks, t0, order):
    """B6's own schedule over the structured tick, for the fetch's own guard."""
    got = [l for l in order if l in fetchblocks]
    if not got:
        return schedule.Schedule(proc)
    return schedule.derive(prog, proc, fetchblocks, t0, got[0])
