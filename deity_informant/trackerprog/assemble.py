"""B6/B7 -- the whole lift: certified artefacts in, one trackerprog out.

Reads S4, S6, T0, T1, T2 and the certificate, derives the schedule (B6), lowers
the tick outside the fetch regions (B7), materialises the score the fetches read
and assembles the object ``universal.py`` renders. Hints supply what it cannot.
"""

from __future__ import annotations

from ..tuneprog.graph import cfg, idoms, natural_loops, preds_of, rpo, succs
from . import build, emit, lower, record, recognise, region, schedule
from .report import coverage
from .refuse import Refusal
from .cells import Cells
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
    prog, view, names, t0 = art["prog"], art["view"], art["names"], art["t0"]
    proc = prog.meta["tick_proc"]
    tables = emit.tables_of(art["t2"], view, names)
    fetch, refusals = region.fetch(prog, tables)
    fb = {l for r in fetch.regions.values() for l in r.blocks}
    fb |= {r.exit for r in fetch.regions.values()}
    order = [l for l in rpo(prog.procs[proc]) if l in fb]
    _need(order, "score not cursor-shaped", proc, "the tick reaches no fetch region")
    sch = schedule.derive(prog, proc, fb, t0, order[0])
    _need(sch.clock, "unclassified update", proc, "no row clock steps the voice loop")
    _need(sch.vidx, "unclassified update", proc, "the tick keeps no voice index")
    p = prog.procs[proc]

    pit = build.pitch_of(art, view, names)
    ins = build.instrument_table(art, view, names)
    pwcols = build.pw_columns(art, view, names)
    _need(pit, "unclassified update", "pitch", "T2 materialised no tuning")
    _need(ins, "command residue", "instruments", "T2 found no instrument selector")
    _need(pwcols, "unclassified update", "ins.pw", "no instrument-scoped pulse pair")
    rid, org, n, basenote = pit
    pstart = org + 2 * basenote
    cells = Cells(view, names, pitch=(rid, pstart, n), inspw=pwcols)
    voc = Vocab(cells, prog.reads(), build.registers(), sch.vidx)
    voc.pitch, voc.inspw = (rid, org, n), pwcols
    voc.insbase, voc.inscol, voc.insstride = ins[0], ins[1], ins[2]
    low = lower.Lower(prog, proc, cells, voc)
    voc.notebase = build.note_base(low, rid, org, [p])
    _need(voc.notebase, "unclassified update", "note", "no cell indexes the tuning")
    cells.rename = {voc.notebase: "note", voc.insbase: "ins"}
    clockcell = _clockcell(cells, sch)
    voc.dropstores = {sch.clock[2].src} | {st.src for st, _g in sch.resets}
    # the counter as the object reads it: its own step before the clock takes it,
    # and the cell every later read of it is (section 3.6)
    voc.subst = {sch.clock[1].n: {"cell": "phase"}}
    if not sch.inloop:  # a scalar the tick keeps: the object's cell is the clock's own
        voc.subst.update({n: {"cell": clockcell} for n in sch.reads})

    body = set(sch.body)
    segs = {name: _live(prog, proc, blocks) for name, blocks in sch.segments}
    voc.rowblocks = frozenset(segs["row"])
    glob = _live(prog, proc, [l for l in rpo(p) if l not in body])
    low.gate, low.scope, low.local = frozenset(), frozenset(), {}
    build._supplied(low, sum(segs.values(), []) + glob)
    inrow = _defined(p, segs["row"])
    low.v.supplied = {n for n in low.bad if n in low.assigned and n in inrow}
    low = lower.Lower(prog, proc, cells, voc)
    low.stated = frozenset(id(c) for c in sch.spent)
    img = record.interp.Player(prog, region.Fetch()).run_init().m
    inputs, badinputs = build.pinned_inputs(prog, img)
    refusals += [Refusal("external input", a, site, kind) for a, site, kind in badinputs]

    trips = {}
    loops = record.headers(prog, proc, set(sum(segs.values(), []) + glob))
    turnof = low.turnsof(frozenset(segs["row"]))
    marks = [(nm, low.defs[nm], turnof.get(nm)) for nm in voc.supplied if nm in low.defs]
    sch.rate, sch.phase = 1, 0
    rowblocks = segs["row"]
    exits = sorted({s for l in rowblocks for s in succs(p.blocks[l].term) if s not in rowblocks})
    exits = [e for e in exits if type(p.blocks[e].term).__name__ != TRAP]
    vnames = sorted(sch.vidx)
    key = (proc, rowblocks[0])
    R, fetches, trap, _obs = record.run(
        prog,
        proc,
        [(rowblocks[0], rowblocks, exits)],
        ticks or art["t2"]["horizon"]["ticks"],
        inputs=inputs,
        envvars={key: vnames},
        loops=loops,
        marks=marks,
    )
    trips = dict(R.trips)
    vvar = record.voice_name(fetches.get(key, []), vnames, cells.voices)
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
    streams, accs = ph.streams, ph.accs
    limit = max(0, (view.by_id()[rid].size - 2 * n) // 2)
    ph.beyond(build.beyond_words(cells, pstart, n, limit))
    build.dce(list(streams.values()), _keep(low, accs, sch))
    join = recognise.Join(art, view, cells, ph)
    t1got = join.run()

    ordernames = build.order_letters(low, _order_region(art, view, names))
    build.dce(list(streams.values()), _keep(low, accs, sch))
    alive = set()
    for st in streams.values():
        for r in st["rows"]:
            alive |= build._cellnames(r.get("when", [])) | build._cellnames(
                [x[1] for x in r["sets"]]
            )
    for a in accs.values():
        alive |= build._cellnames(list(a.values()))
    orders, pats = record.score_of(
        fetches.get(key, []),
        low,
        vvar,
        ordernames,
        sch.clock[3],
        voices,
        _order_cursor(art, view, names),
        alive,
    )
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
                p, sch.head, _latches(prog, proc, sch), sch.vidx, voices
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
        },
        "pitch": {"base": basenote, "freq": list(art["t2"]["pitch"]["entries"])},
        "streams": {**build.unsite(streams), **build.table_streams(voc, img)},
        "accs": accs,
        "instruments": _instruments(art, view, names, ins, pwcols, img, accs),
        "score": {"patterns": record.patterns_of(pats), "orders": orders},
        "globals": {"streams": [nm for k, nm in gl if k == "stream"]},
        "state0": {"cells": cellseed, "globals": globseed},
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
        "rows": sum(len(s["rows"]) for s in streams.values()),
        "accs": len(accs),
        "patterns": len(pats),
    }
    return obj, report


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
    byid = view.by_id()
    out = {}
    for i in range(entries):
        rec = {name: int(img[byid[rid].base + i * stride]) for rid, name in cols.items()}
        pw = [0, 0]
        for rid, part in pwcols.items():
            pw[0 if part == "lo" else 1] = int(img[byid[rid].base + i * stride])
        rec["pw"] = pw
        rec["accs"] = [{"acc": k} for k in accs]
        out[str(keys[i])] = rec
    del art, names, addr
    return out


def _apply(obj, path, value):
    """One hint: a datum of section 3.1, written where the schema puts it."""
    node = obj
    parts = path.split(".")
    for k in parts[:-1]:
        node = node[k]
    node[parts[-1]] = value
