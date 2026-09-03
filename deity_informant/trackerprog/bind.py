"""B7 -- the trackerprog as the binding of a certified tune's planes to the player.

There is one player (:mod:`.universal`) with a fixed tick and a fixed state
vector, and a certified tuneprog has the same slots under other names.  This
module binds them: S6's roles and T2's cursors name the player's own cells, T2's
selector is the instrument table, T2's score is the cursor nest, T1's records are
section 5's accumulators and T0's write sites are what produces.  Nothing is
lowered: a value is read at its own site and expressed over a named cell, an
instrument column or a row fact, and a name two paths bind differently splits
the row rather than becoming a cell of the object.
"""

from __future__ import annotations

from ..tuneprog.graph import rpo, succs
from ..tuneprog.ir import Let, Load, Store
from ..tuneprog.irwalk import addr_split
from . import build, emit, record, region, schedule, sections, tables
from .cells import Cells
from .events import Score, _same, _scorecells, fields_of, masks_of, terms_of, tie_of
from .read import Reader, Unlowerable
from .records import Accs, _addr
from .refuse import Refusal
from .rows import ambiguous
from .shape import _channels, _need, _order_cursor, _rename, _rowblocks, _u16name
from .vocab import Vocab


class Binder:
    """One certified tune's planes, bound to the player's own slots (§4, §5)."""

    def __init__(self, art, ticks=None):
        self.art = art
        view, names, t0 = art["view"], art["names"], art["t0"]
        prog, proc = art["prog"], art["prog"].meta["tick_proc"]
        p = prog.procs[proc]
        self.view, self.names, self.t0 = view, names, t0
        self.prog, self.proc, self.p = prog, proc, p
        fetch, self.refusals = region.fetch(prog, emit.tables_of(art["t2"], view, names))
        rowr = _channels(prog, proc, fetch, emit.tables_of(art["t2"], view, names, ("pattern",)))
        self.rowfb = _rowblocks(prog, proc, rowr)
        order = [l for l in rpo(p) if l in self.rowfb]
        _need(order, "score not cursor-shaped", proc, "the tick reaches no fetch region")
        self.sch = sch = schedule.derive(prog, proc, self.rowfb, t0, order[0])
        _need(sch.clock, "unclassified update", proc, "no row clock steps the voice loop")
        self.pit = tables.pitch_of(art, view, names)
        self.ins = tables.instrument_table(art, view, names)
        self.pwcols = tables.pw_columns(art, view, names)
        _need(self.pit, "unclassified update", "pitch", "T2 materialised no tuning")
        _need(self.ins, "command residue", "instruments", "T2 found no instrument selector")
        pit = self.pit
        entry0 = tuple(b + pit.step * pit.base for b in pit.obases)
        self.cells = Cells(
            view,
            names,
            pitch=(pit.rids, entry0, pit.step, pit.n),
            inspw=self.pwcols,
            words=tables.word_widths(prog, proc),
        )
        self.voc = Vocab(self.cells, prog.reads(), build.registers(), sch.vidx)
        self.voc.pitch, self.voc.inspw = (pit.rids, pit.obases, pit.step, pit.n), self.pwcols
        self.voc.insbase, self.voc.inscol, self.voc.insstride = (
            self.ins[0],
            self.ins[1],
            self.ins[2],
        )
        self.low = Reader(prog, proc, self.cells, self.voc)
        self.voc.notebase = tables.note_base(self.low, pit, [p])
        _need(self.voc.notebase, "unclassified update", "note", "no cell indexes the tuning")
        self.img = record.interp.Player(prog, region.Fetch()).run_init().m
        self.voc.img = self.img
        self.ticks = ticks or art["t2"]["horizon"]["ticks"]
        self.segs = {n: list(b) for n, b in sch.segments}
        # what each step of the binding fills in, in the order ``run`` fills it
        self.orderbase = self.clockbase = self.clockcell = self.vvar = None
        self.slots, self.trips, self.inputs, self.badinputs, self.trap = {}, {}, {}, [], None
        self.score, self.sc, self.armcells, self.packed = None, {}, {}, set()
        self.tiemask, self.left, self.amb = None, [], {}
        self.pro, self.accs, self.accat = frozenset(), {}, {}

    def freqpair(self):
        """The per-voice pair a frequency accumulator moves: the player's ``freq``."""
        n = self.cells.voices
        for a in self.art["t1"].get("accs") or ():
            if a["width"] != 16 or int(a["cell"]["copies"]) != n:
                continue
            if (a["target"] or {}).get("register") not in ("freq", "freq_lo", "freq_hi"):
                continue
            lo = _addr(a["cell"])
            hi = next((r for r in a["regions"] if r != a["cell"]["region"]), None)
            if hi is None:
                continue
            return lo, self.view.by_id()[hi].base
        return None, None

    def copied(self, addr):
        """The per-voice cell a scalar the tick reads a role off is copied from.

        A family that stages its row moves the byte into a scratch the machine
        reads, so the cell the player's slot names is the copy's own source.
        """
        low, got = self.low, []
        for lbl, blk in low.proc.blocks.items():
            for s in blk.stmts:
                if type(s) is Store and s.cls == "ram" and addr_split(s.a)[0] == addr:
                    got.append((lbl, s))
        if len(got) != 1:
            return addr
        low.lbl, low.local, low.pick, low.sub = got[0][0], {}, {}, {}
        e = low.expand(got[0][1].v)
        base, idx = addr_split(e.a) if type(e) is Load else (None, None)
        if base is not None and idx is not None and low.isvoice(idx):
            return base
        return addr

    def roles(self):
        """The player's own slots, bound to the cells S6, T1 and T2 name (§4, §5)."""
        sch, voc = self.sch, self.voc
        lo, hi = self.freqpair()
        self.orderbase = _order_cursor(self.art, self.view, self.names)
        self.clockbase = sch.clock[3]
        voc.notebase = self.copied(voc.notebase)
        voc.insbase = self.copied(voc.insbase)
        # the clock is the player's ``rowsleft`` only where the row's own length
        # reloads it: a tune whose clock the tick keeps has no ``dur`` field
        rows = set(self.segs["row"])
        if not any(
            type(x) is Store and addr_split(x.a)[0] == self.clockbase
            for l in rows
            for x in self.p.blocks[l].stmts
        ):
            self.clockbase = None
        got = {
            "note": voc.notebase,
            "ins": voc.insbase,
            "rowsleft": self.clockbase,
            "orderpos": self.orderbase,
            "freq.lo": lo,
            "freq.hi": hi,
        }
        _rename(self.cells, got)
        self.clockcell = (
            "rowsleft"
            if self.clockbase
            else (
                self.cells.voicecell(sch.clock[3])
                if sch.inloop
                else self.cells.scalarcell(sch.clock[3])
            )
        )
        self.slots = {k: v for k, v in got.items() if v is not None}
        voc.subst = {sch.clock[1].n: {"cell": "phase"}}
        drop = {sch.clock[2].src} | {st.src for st, _g in sch.resets}
        own = {v for k, v in self.slots.items() if k != "freq.lo" and k != "freq.hi"}
        for lbl in self.segs["row"]:
            for s in self.p.blocks[lbl].stmts:
                if type(s) is Store and addr_split(s.a)[0] in own:
                    drop.add(s.src)
        voc.dropstores = drop
        self.low.stated = frozenset(id(c) for c in sch.spent)

    def supplied(self):
        """The names no cell of the tune holds: the bytes a fetch read (the score's)."""
        low, got = self.low, set()
        low.gate, low.scope, low.local, low.pick, low.sub = frozenset(), frozenset(), {}, {}, {}
        for lbl in sum(self.segs.values(), []):
            low.lbl = lbl
            for s in low.proc.blocks[lbl].stmts:
                try:
                    if type(s) is Let:
                        low.value(s.e)
                    elif low.v.target(low, s) is not None:
                        low.value(s.v)
                except Unlowerable:
                    got.add(s.n if type(s) is Let else "$%04X" % s.src)
        got = {n for n in got | set(low.bad) if n in low.defs}
        self.low = Reader(self.prog, self.proc, self.cells, self.voc)
        self.low.stated = frozenset(id(c) for c in self.sch.spent)
        self.voc.supplied = {n for n in got if n in self.low.defs or n in self.low.assigned}
        return self.voc.supplied

    def visits(self):
        """The horizon recorded over the fetch regions: one visit a row of a voice."""
        rowblocks = self.segs["row"]
        exits = sorted(
            {s for l in rowblocks for s in succs(self.p.blocks[l].term) if s not in rowblocks}
        )
        exits = [e for e in exits if type(self.p.blocks[e].term).__name__ != "Trap"]
        inputs, bad = build.pinned_inputs(self.prog, self.img)
        vnames = sorted(self.sch.vidx)
        groups = [(rowblocks[0], rowblocks, exits)]
        R, fetches, trap, _obs = record.run(
            self.prog,
            self.proc,
            groups,
            self.ticks,
            inputs=inputs,
            envvars={(self.proc, rowblocks[0]): vnames},
        )
        self.trips, self.inputs, self.badinputs, self.trap = dict(R.trips), inputs, bad, trap
        recs = fetches[(self.proc, rowblocks[0])]
        self.vvar = record.voice_name(recs, vnames, self.cells.voices, self.cells.stride)
        return recs

    def bind_fields(self, recs):
        """Section 3.6's event fields, and what a masked score byte is of them."""
        top = self.pit.base + self.pit.n
        roles = {"dur": self.clockbase or -1, "note": self.voc.notebase, "ins": self.voc.insbase}
        seed = (
            [
                int(self.img[self.orderbase + v * self.cells.stride])
                for v in range(self.cells.voices)
            ]
            if self.orderbase
            else None
        )
        own = {
            a
            for a in roles.values()
            if a is not None and (self.cells.at(a) or (None,))[0] == "voice"
        }
        self.score = Score(
            recs,
            self.vvar,
            roles,
            self.cells.voices,
            self.cells.stride,
            self.orderbase,
            top,
            seed,
            own,
        )
        own = {self.clockbase, self.voc.notebase, self.voc.insbase, self.orderbase}
        self.sc = _scorecells(self.low, self.segs["row"], self.voc.supplied)
        # the byte the row's own fields are read off is the row and not a command:
        # a cell it lands in carries no datum the event's fields do not (§3.6)
        base, temps0 = self.score.facts()
        packed = {
            n
            for n, m in masks_of(self.low)
            if n in temps0
            and _same([None if v is None else v & (m or 0xFF) for v in temps0[n]], base["dur"])
        }
        self.packed = packed
        self.armcells = {k: v for k, v in self.sc.items() if v[1] not in own and v[2] not in packed}
        for v in range(self.cells.voices):
            for r in self.score.rows[v]:
                r["sets"] = [
                    [cell, r["sites"][src]]
                    for src, (cell, _base, _n) in sorted(self.armcells.items())
                    if src in r["sites"]
                ]
        facts, temps = self.score.facts()
        got, left = fields_of(masks_of(self.low), facts, temps)
        self.tiemask, self.voc.fields = tie_of(got, left)
        rows = [r for v in range(self.cells.voices) for r in self.score.rows[v]]
        pairs = {
            (lbl, id(c)): (lbl, c) for lbl, gs in self.low.guards.items() for _d, c, _t, _w in gs
        }
        self.voc.terms = terms_of(self.low, sorted(pairs.values(), key=lambda x: x[0]), facts, rows)
        self.left = [(n, m) for n, m, _v in left if (n, m) != self.tiemask]
        return self.voc.fields

    def tie(self, row):
        """One row's ``tie``: the field of the packed byte no other field explains."""
        if self.tiemask is None:
            return False
        return bool(row["temps"].get(self.tiemask[0], 0) & self.tiemask[1])

    def plan(self, order=()):
        """One guard plan over the segments: a join folds, or its paths raise a cell."""
        body = set(self.sch.body)
        groups = [
            self.segs.get("prelude", []),
            self.segs["row"],
            self.segs.get("machine", []),
            [l for l in order if l not in body],
        ]
        flags = self.low.planall(groups)
        if flags:
            self.refusals.append(
                Refusal("unclassified update", ",".join(flags), "", "a join no path folds")
            )
        return flags

    def run(self):  # noqa: C901 - one clause per section of the object
        """The bound object, and the report of what each plane supplied."""
        self.roles()
        self.supplied()
        recs = self.visits()
        self.bind_fields(recs)
        self.amb = ambiguous(self.p)
        pro = record.firstonly(self.prog, self.proc, self.inputs)
        self.pro = pro if pro and not pro & set(self.sch.body) else frozenset()
        order = self.low.rpo
        self.plan(order)
        A = Accs(self.low, self.art, self.names, self.view)
        accs, drop, accat = {}, set(), {}
        for a in A.order(order):
            rec, d, why = A.record(a, 0)
            if rec is None:
                self.refusals.append(
                    Refusal("unclassified update", a["cell"]["name"], ",".join(a["sites"]), why)
                )
                continue
            nm = _u16name(self.names, a["cell"]["region"]) or a["id"]
            if rec["cell"].lstrip("#").startswith("c"):
                rec["cell"] = ("#" if rec["cell"][:1] == "#" else "") + nm
            accs[a["id"]] = rec
            accat[a["id"]] = min(order.index(l) for l in A.siteblocks(a))
            drop |= d
        self.accs, self.accat = accs, accat
        sc = self.sc
        roles = {}
        for lbl in self.segs["row"]:
            for s in self.p.blocks[lbl].stmts:
                if type(s) is not Store:
                    continue
                base = addr_split(s.a)[0]
                if base == self.voc.notebase:
                    roles[s.src] = "note"
                elif base == self.voc.insbase:
                    roles[s.src] = "ins"
                elif base in (self.clockbase, self.orderbase):
                    drop.add(s.src)
                elif s.src in sc:
                    roles[s.src] = "arm"

        return sections.assemble(self, order, drop, roles)


def lift(art, ticks=None, hints=None):
    """``(object, report)``: one certified tune's planes, bound to the player."""
    b = Binder(art, ticks)
    obj = b.run()
    for k, v in (hints or {}).items():
        node, parts = obj, k.split(".")
        for x in parts[:-1]:
            node = node[x]
        node[parts[-1]] = v
    # a store no cell of section 5 holds is named, and the object is emitted
    # without it: the certificate is what says whether it was worth a tick
    b.refusals += [
        Refusal("unclassified update", x, x, "no section 5 cell holds it")
        for x in sorted(b.low.bad)
    ]
    report = {
        "schedule": b.sch.datums(),
        "refusals": [r.to_dict() for r in b.refusals],
        "coverage": sections.coverage(b, obj),
        "trips": b.trips,
        "rows": sum(len(s["rows"]) for s in obj["streams"].values()),
        "accs": len(obj["accs"]),
        "patterns": len(obj["score"]["patterns"]),
    }
    return obj, report
