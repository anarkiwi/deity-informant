"""B6/B7 -- the whole lift: certified artefacts in, one trackerprog out.

Reads S4, S6, T0, T1, T2 and the certificate, derives the schedule (B6), lowers
the tick outside the fetch regions (B7), materialises the score the fetches read
and assembles the object ``universal.py`` renders. Hints supply what it cannot.
"""

from __future__ import annotations

from ..tuneprog.graph import cfg, idoms, natural_loops, preds_of, rpo, succs
from . import build, callee, emit, lower, record, recognise, region, schedule, shadow, tables
from .report import coverage
from .refuse import Refusal
from .cells import Cells
from .universal import CHIP
from .vocab import Vocab

TRAP = "Trap"


class Refused(Exception):
    """A lift that will not emit: the residue, named (section 8)."""

    def __init__(self, refusals):
        super().__init__("; ".join(r.why for r in refusals))
        self.refusals = refusals


def _need(got, why, cell, detail):
    if got in (None, (), []):
        raise Refused([Refusal(why, cell, "", detail)])
    return got


def _defined(p, blocks):
    """The names one group of blocks binds: what a score can supply and no more."""
    return {s.n for l in blocks for s in p.blocks[l].stmts if hasattr(s, "n")}


def _live(prog, proc, blocks):
    """The blocks of a set the program can reach: a trap is no block of a phase."""
    p = prog.procs[proc]
    return [l for l in blocks if type(p.blocks[l].term).__name__ != TRAP]


def lift(art, ticks=None, hints=None):  # noqa: C901 - one clause per derived datum
    """``(object, report)``: the trackerprog of one certified tune."""
    hints = dict(hints or {})
    view, names, t0 = art["view"], art["names"], art["t0"]
    proc = art["prog"].meta["tick_proc"]
    prog, inlined = callee.inline(art["prog"], proc)
    sh = shadow.of(t0, prog, view)
    shb = sh.blocks if sh else frozenset()
    score = emit.tables_of(art["t2"], view, names)
    fetch, refusals = region.fetch(prog, score)
    fb = {l for r in fetch.regions.values() for l in r.blocks}
    fb |= {r.exit for r in fetch.regions.values()}
    rowr = _channels(prog, proc, fetch, emit.tables_of(art["t2"], view, names, ("pattern",)))
    rowfb = _rowblocks(prog, proc, rowr)
    order = [l for l in rpo(prog.procs[proc]) if l in rowfb]
    _need(order, "score not cursor-shaped", proc, "the tick reaches no fetch region")
    sch = schedule.derive(prog, proc, rowfb, t0, order[0])
    _need(sch.clock, "unclassified update", proc, "no row clock steps the voice loop")
    _need(sch.vidx, "unclassified update", proc, "the tick keeps no voice index")
    p = prog.procs[proc]

    pit = tables.pitch_of(art, view, names)
    ins = tables.instrument_table(art, view, names)
    pwcols = tables.pw_columns(art, view, names)
    _need(pit, "unclassified update", "pitch", "T2 materialised no tuning")
    _need(ins, "command residue", "instruments", "T2 found no instrument selector")
    n = pit.n
    entry0 = tuple(b + pit.step * pit.base for b in pit.obases)
    cells = Cells(
        view,
        names,
        pitch=(pit.rids, entry0, pit.step, n),
        inspw=pwcols,
        words=tables.word_widths(prog, proc),
    )
    voc = Vocab(cells, prog.reads(), build.registers(), sch.vidx)
    voc.shadow = (sh.base, sh.size) if sh else ()
    voc.pitch, voc.inspw = (pit.rids, pit.obases, pit.step, n), pwcols
    voc.insbase, voc.inscol, voc.insstride = ins[0], ins[1], ins[2]
    low = lower.Lower(prog, proc, cells, voc)
    voc.notebase = tables.note_base(low, pit, [p])
    _need(voc.notebase, "unclassified update", "note", "no cell indexes the tuning")
    cells.rename = {voc.notebase: "note", voc.insbase: "ins"}
    clockcell = _clockcell(cells, sch)
    voc.dropstores = {sch.clock[2].src} | {st.src for st, _g in sch.resets}
    # the counter as the object reads it: its own step before the clock takes it,
    # and the cell every later read of it is (section 3.6)
    voc.subst = {sch.clock[1].n: {"cell": "phase"}}
    if not sch.inloop:  # a scalar the tick keeps: the object's cell is the clock's own
        voc.subst.update({n: {"cell": clockcell} for n in sch.reads})

    img = record.interp.Player(prog, region.Fetch()).run_init().m
    inputs, badinputs = build.pinned_inputs(prog, img)
    refusals += [Refusal("external input", a, site, kind) for a, site, kind in badinputs]
    body = set(sch.body)
    # the tune's own init, where its first call runs the reset and spends the tick
    pro = record.firstonly(prog, proc, inputs)
    pro = pro if pro and not pro & body else frozenset()
    out = shb | pro
    segs = {
        name: _live(prog, proc, [l for l in blocks if l not in out])
        for name, blocks in sch.segments
    }
    voc.rowblocks = frozenset(segs["row"])
    glob = _live(prog, proc, [l for l in rpo(p) if l not in body | out])
    prol = _live(prog, proc, [l for l in rpo(p) if l in pro])
    low.gate, low.scope, low.local = frozenset(), frozenset(), {}
    build._supplied(low, sum(segs.values(), []) + glob + prol)
    inrow = _defined(p, segs["row"])
    low.v.supplied = {n for n in low.bad if n in low.assigned and n in inrow}
    low = lower.Lower(prog, proc, cells, voc)
    low.scalars = frozenset(_defined(p, glob + prol)) - _defined(p, sum(segs.values(), []))
    low.stated = frozenset(id(c) for c in sch.spent)

    trips = {}
    loops = record.headers(prog, proc, set(sum(segs.values(), []) + glob + prol))
    turnof = low.turnsof(frozenset(segs["row"]))
    marks = [(nm, low.defs[nm], turnof.get(nm)) for nm in voc.supplied if nm in low.defs]
    sch.rate, sch.phase = 1, 0
    rowblocks = segs["row"]
    exits = sorted({s for l in rowblocks for s in succs(p.blocks[l].term) if s not in rowblocks})
    exits = [e for e in exits if type(p.blocks[e].term).__name__ != TRAP]
    vnames = sorted(sch.vidx)
    key = (proc, rowblocks[0])
    groups = [(rowblocks[0], rowblocks, exits)]
    R, fetches, trap, _obs = record.run(
        prog,
        proc,
        groups,
        ticks or art["t2"]["horizon"]["ticks"],
        inputs=inputs,
        envvars={(proc, g[0]): vnames for g in groups},
        loops=loops,
        marks=marks,
    )
    trips = dict(R.trips)
    vvar = record.voice_name(fetches.get(key, []), vnames, cells.voices, cells.stride)
    _need(vvar, "unclassified update", proc, "no name the fetch carries is the voice")
    if trap is not None:
        refusals.append(Refusal("external input", trap["detail"], "", trap["trap"]))

    rate = build.divider_rate(sch.divider[1], low, img) if sch.divider else 1
    phase = (
        build.divider_phase(img, sch.divider[0], rate - 1, rate) if sch.divider and rate > 1 else 0
    )
    voices = cells.voices

    ph = build.Phases(low, trips)
    groups = [segs.get("prelude", []), rowblocks, segs.get("machine", [])]
    flags = low.planall(groups)
    # the join flags are cleared at the head of the first phase that runs on every
    # tick: the row's is the boundary's, so it takes them only where it is the one
    at = next((i for i, g in enumerate(groups) if g and i != 1), 1)
    pre = ph.add("prelude", groups[0], False, reset=flags if at == 0 else ())
    rows = ph.add("rowprog", rowblocks, False, gate=sch.boundary, reset=flags if at == 1 else ())
    ph.add("machine", groups[2], True, reset=flags if at == 2 else ())
    gl = ph.add("global", glob, False)
    pl = ph.add("prologue", prol, False, gate=_oneshot(low, prol)) if prol else []
    streams, accs = ph.streams, ph.accs
    ph.beyond(tables.beyond_words(cells, low, pit, tables.beyond_limit(cells, low, pit)))
    scratch = {c[1:] for c in low.temps.values() if c[:1] == "#"}
    build.dce(list(streams.values()), _keep(low, accs, sch), scratch)
    join = recognise.Join(art, view, cells, ph)
    t1got = join.run()

    ordernames = build.order_letters(low, _order_region(art, view, names))
    build.dce(list(streams.values()), _keep(low, accs, sch), scratch)
    alive = set()
    for st in streams.values():
        for r in st["rows"]:
            alive |= build._cellnames(r.get("when", [])) | build._cellnames(
                [x[1] for x in r["sets"]]
            )
    for a in accs.values():
        alive |= build._cellnames(list(a.values()))
    orders, pats = record.score_of(
        [r for k, v in sorted(fetches.items()) for r in v],
        low,
        vvar,
        ordernames,
        sch.clock[3],
        voices,
        _order_cursor(art, view, names),
        alive,
        cells.stride,
    )
    prologue = _prologue(streams, pl)
    commits = _commits(streams, gl)
    cellseed, globseed = build.widen(*cells.seed(img), join.merged, img, cells)
    obj = {
        "$trackerprog": 1,
        "meta": {
            "tune": prog.meta.get("name"),
            "song": prog.meta.get("song"),
            "family": "lifted",
            "cycles_per_tick": prog.meta["entry"]["cycles_per_tick"],
            "voices": voices,
            "horizon": ticks or art["t2"]["horizon"]["ticks"],
            "voice_order": build.voice_order(
                p, sch.head, _latches(prog, proc, sch), sch.vidx, voices, cells.stride
            ),
            "commit_order": list(sch.commit_order),
            "instrument": {},
            "tempo": {
                "cell": clockcell,
                "step": sch.step,
                "rate": rate,
                "phase": phase,
                "boundary": [low.guard(c, t) for c, t in sch.boundary],
                **_resets(low, clockcell, sch),
            },
            "tick": _tick(sch.tick, pre),
            "row_consumes_tick": sch.row_consumes_tick,
            "row_command": "spent",
            "row": [{"commands": True}] + [{"stream": nm} for _k, nm in rows],
            "wide": sorted(set(low.wide) | set(join.wide)),
            **({"shadow": {"registers": sh.registers}} if sh else {}),
        },
        "pitch": {"base": pit.base, "freq": list(art["t2"]["pitch"]["entries"])},
        "streams": {**build.unsite(streams), **build.table_streams(voc, img)},
        "accs": accs,
        "instruments": _instruments(art, view, names, ins, pwcols, img, accs),
        "score": {"patterns": record.patterns_of(pats), "orders": orders},
        "globals": {
            "streams": [nm for k, nm in gl if k == "stream"],
            **({"commit": commits} if commits else {}),
        },
        "state0": {
            "cells": cellseed,
            "globals": globseed,
            **({"shadow": shadow.seed(img, sh)} if sh else {}),
            **prologue,
        },
    }
    for k, v in hints.items():
        _apply(obj, k, v)
    sch.rate, sch.phase = rate, phase
    sch.voice_order = tuple(obj["meta"]["voice_order"])
    for site in sorted(low.bad - set(voc.supplied)):
        refusals.append(Refusal("unclassified update", site, site, "no section 5 cell holds it"))
    build.prune(obj)
    cov = coverage(low, prog, proc, segs, glob, list(streams.values()), accs, t1got)
    report = {
        "schedule": sch.datums(),
        "supplied": sorted(voc.supplied),
        "refusals": [r.to_dict() for r in refusals],
        "coverage": cov,
        "trips": trips,
        "inlined": inlined,
        "rows": sum(len(s["rows"]) for s in streams.values()),
        "accs": len(accs),
        "patterns": len(pats),
    }
    return obj, report


def _rowblocks(prog, proc, rowr):
    """The blocks the ``row`` segment is: the fetch regions, and where they rejoin.

    A region's exit is the row's where the fetch alone reaches it, and the
    machine's where the voice loop closes on it -- a latch runs on every turn.
    """
    got = {l for r in rowr for l in r.blocks}
    latches = schedule.voice_loop(prog, proc, frozenset(got))[1][1]
    return got | ({r.exit for r in rowr} - set(latches))


def _channels(prog, proc, fetch, pattables):
    """The fetch regions the ``row`` is: those a pattern table is read in.

    T2 names the table each channel of the score reads, so a region that reads no
    pattern table is a walk of the order list, which the object states as a table
    read at a cursor (section 3.3) like any other.
    """
    got = region.score_loads(prog.procs[proc], pattables) if pattables else set()
    rowr = [r for r in fetch.regions.values() if r.proc == proc and r.blocks & got]
    return rowr or [r for r in fetch.regions.values() if r.proc == proc]


def _oneshot(low, blocks):
    """The terms every block of the prologue stands under: the first call's own test."""
    got = None
    for l in blocks:
        here = {(id(c), t): (c, t) for _d, c, t, _w in low.guards.get(l, ())}
        got = here if got is None else {k: v for k, v in got.items() if k in here}
    return tuple((got or {}).values())


def _commits(streams, items):
    """Section 3.7's ``globals.commit``: the registers the tick's own channel sends.

    A register the channel names outright is the chip's and no voice's, so the
    channel commits it once the voices have run and no row of it states it.
    """
    out = []
    for kind, nm in items:
        if kind != "stream":
            continue
        for r in streams[nm]["rows"]:
            at = [k for k, s in enumerate(r["sets"]) if s[0] in CHIP]
            out += [[r["sets"][k][0], r["sets"][k][1], r.get("when", [])] for k in at]
            keep = [k for k in range(len(r["sets"])) if k not in set(at)]
            r["sets"] = [r["sets"][k] for k in keep]
            if build.SITES in r:
                r[build.SITES] = [r[build.SITES][k] for k in keep]
        streams[nm]["rows"] = [r for r in streams[nm]["rows"] if r["sets"]]
    return out


def _prologue(streams, items):
    """``state0.prologue``: the reset the first call runs, as rows every voice takes."""
    rows = [r for k, nm in items if k == "stream" for r in streams.pop(nm)["rows"]]
    return {"prologue": {"rows": rows}} if rows else {}


def _clockcell(cells, sch):
    """The cell ``meta.tempo`` moves: a voice's own counter, or the tick's own.

    A counter the tick steps outside the voice loop is one value the whole tune
    keeps, and every voice's copy of it steps by the same rule (section 3.6).
    """
    base = sch.clock[3]
    return cells.voicecell(base) if sch.inloop else cells.scalarcell(base)


def _resets(low, cell, sch):
    """Section 3.6's ``reset`` clauses: what the tick does to the counter at its end."""
    out = [
        {
            "when": [low.guard(c, t) for c, t in guard],
            "sets": [["@" + cell, low.guard_value(st.v)]],
        }
        for st, guard in sch.resets
    ]
    return {"reset": out} if out else {}


def _tick(tick, pre):
    """``meta.tick``: the segment before the row is a ``{stream}`` at its own position.

    It is not ``prelude``: the lift keeps the tune's own divider as a cell, so the
    segment's guards say when it runs and the phase must run on every tick.
    """
    out = []
    for name in tick:
        if name != "prelude":
            out.append(name)
            continue
        out += [{"stream": nm} for k, nm in pre if k == "stream"]
    return out


def _latches(prog, proc, sch):
    """The blocks that close the voice loop: where its index is rebound."""
    p = prog.procs[proc]
    g = cfg(p)
    return natural_loops(g, idoms(p, g), preds_of(p)).get(sch.head, (set(), set()))[1]


def _order_cursor(art, view, names):
    """The cell the fetch steps at a pattern's end: T2's own order cursor."""
    regs = emit.by_name(view, names)
    for v in art["t2"]["score"]:
        for ch in v.get("order", ()):
            name, _at, addr = ch["cursor"].partition("@$")
            if addr:
                return int(addr, 16)
            r = regs.get(name)
            if r is not None:
                return r.base
    return None


def _order_region(art, view, names):
    regs = emit.by_name(view, names)
    for v in art["t2"]["score"]:
        for ch in v.get("order", ()):
            r = regs.get(ch["table"])
            if r is not None:
                return r.id
    return -1


def _keep(low, accs, sch):
    """The cells a reader outside the lowered rows still has: the object's own."""
    out = {"note", "ins", "phase", "voice_index", _clockcell(low.cells, sch)}
    for a in accs.values():
        out |= build._cellnames(list(a.values()))
        out |= {a["cell"].lstrip("#") + h for h in ("", ".lo", ".hi")}
    out |= build._cellnames([low.guard(c, t) for c, t in sch.boundary])
    return out


def _instruments(art, view, names, ins, pwcols, img, accs):
    """One record per entry of T2's selector: its columns, and its pulse pair.

    A record is named by what the cell that selects it holds, which T2 states.
    """
    addr, cols, stride, entries, keys = ins
    org = {rid: tables._origin_of(view, rid) for rid in set(cols) | set(pwcols)}
    # a record stands where the selecting cell's own value puts it: the value is
    # the record's number in one family and the offset it already is in another
    offsets = all(k == j * stride for j, k in enumerate(keys))
    out = {}
    for i in range(entries):
        at = keys[i] if offsets else keys[i] * stride
        rec = {name: int(img[org[rid] + at]) for rid, name in cols.items()}
        pw = [0, 0]
        for rid, part in pwcols.items():
            pw[0 if part == "lo" else 1] = int(img[org[rid] + at])
        rec["pw"] = pw
        rec["accs"] = [{"acc": k} for k in accs]
        out[str(keys[i])] = rec
    del art, names, addr, view
    return out


def _apply(obj, path, value):
    """One hint: a datum of section 3.1, written where the schema puts it."""
    node = obj
    parts = path.split(".")
    for k in parts[:-1]:
        node = node[k]
    node[parts[-1]] = value
