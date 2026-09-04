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

from ...tuneprog.ir import If, Store, Var
from .. import build, schedule, shadow
from ..emit import commit_order
from ..read import Reader
from ..rows import ambiguous, blockrows, guards
from ..cells import ident
from ..shape import _Out, _dce, _merge_halves, _needed
from ..vocab import Vocab
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
        t = low.proc.blocks[lbl].term
        if type(t) is If and t.t != t.f:
            out[lbl] = ("p" + ident(lbl), t.c)
    return out


def predrow(low, lbl, name, cond):
    """The row one decision is: the block's own guard, and the cell it leaves it in."""
    low.lbl, low.local, low.pick, low.sub, low.turn = lbl, {}, {}, {}, None
    when = []
    for d, c, t in tuple((d, c, t) for d, c, t, _w in low.guards.get(lbl, ())):
        if not low.onpath(d, c, t):
            continue
        low.lbl = d
        fact = low.v.terms.get(repr(c))
        term = [fact, "!=" if t else "==", 0] if fact is not None else low.term(low.expand(c), t)
        if term not in when:
            when.append(term)
    low.lbl = lbl
    got = {"sets": [["@" + name, low.value(low.expand(cond))]]}
    return {**({"when": when} if when else {}), **got}


def segrows(seg, blocks, order, preds):
    """One segment as rows, in program order: each block's decision, then its stores."""
    low, out = seg.low, []
    for lbl in [l for l in order if l in blocks]:
        got = preds.get(lbl)
        if got is not None:
            out.append(predrow(low, lbl, *got))
        for _l, kind, when, sets, _d in guards(
            seg, blockrows(seg, {lbl}, order, set(), {}, True), order
        ):
            if kind in ("set", "reg"):
                out.append({"when": when, "sets": [list(x) for x in sets]})
    return out


def channelrows(rows, key, seed):
    """``(rows, commit)``: what the tick's own channel keeps, and what it sends.

    The tick's channel has no voice to commit through, so a register a channel
    row names is one entry of ``globals.commit`` (§3.7).  That commit runs after
    all of the channel, so the row stages the value in a cell of its own where
    it computed it and the entry sends that cell under the row's own guard.
    """
    out, commit = [], []
    for r in rows:
        keep = [list(s) for s in r.get("sets", ()) if not _needed(s[0])]
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
    sh = shadow.of(art["t0"], prog, art["view"])
    if sh is not None:
        voc.shadow = (sh.base, sh.size)
    low = PNFReader(prog, proc, cells, voc)
    # every cell read is the cell: a store forwarded into a block a second path
    # also reaches is a *may* fact read as a must.  The later levels specialise
    low.reach = {lbl: {} for lbl in low.proc.blocks}
    return low, voc, sh


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
    low.planall([list(g) for _n, g, _c in segs] + [before, after])
    preds = predicates(low, [l for _n, g, _c in segs for l in g] + before + after)
    for name, cond in preds.values():
        voc.terms[repr(cond)] = {"cell": name}
    out, tick, pre, post, commit, staged = _Out(), [], [], [], [], {}
    for i, (name, blocks, group) in enumerate(segs):
        got = segrows(seg, set(blocks), order, preds)
        if got:
            tick.append({"stream": out.stream("%s%d" % (name, i), got)})
        if group:
            tick.append("commit")
    for key, blocks, into in (("pre", before, pre), ("post", after, post)):
        for i, lbl in enumerate(blocks):
            got, sent = channelrows(segrows(seg, {lbl}, order, preds), key, staged)
            commit += sent
            if got:
                into.append(out.stream("%s%d" % (key, i), got))
    cells = l1.facts["cells"]
    cellseed, globseed = cells.seed(prog.reads())
    cellseed[CLOCK] = [0] * cells.voices
    for name, _c in preds.values():
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
            "channel": (tuple(before), tuple(after)),
            "flush": tuple(sh.registers) if sh is not None else (),
            "stage_guard": lead(low, _sch(prog, proc, fetchblocks, art["t0"], order), fetchblocks),
            "reader": low,
            "vocab": voc,
            "predicates": {l: n for l, (n, _c) in preds.items()},
            "refused": sorted(low.bad),
        },
    )


def _sch(prog, proc, fetchblocks, t0, order):
    """B6's own schedule over the structured tick, for the fetch's own guard."""
    got = [l for l in order if l in fetchblocks]
    if not got:
        return schedule.Schedule(proc)
    return schedule.derive(prog, proc, fetchblocks, t0, got[0])
