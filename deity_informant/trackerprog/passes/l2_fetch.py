"""The fetch region specialised where the level order puts it: before L2.

A fetch region is the specialiser's input and not code to predicate: it reads the
score through a pointer the order sets, which no term of section 3.3 states.  So
it is excised at the L1 -> L2 boundary and replayed, its visits become section
3.6's events, its other statements ``meta.row``, and L2 predicates the residual.
"""

from __future__ import annotations

from ...tuneprog.graph import succs
from ...tuneprog.ir import Let, Load, Store
from ...tuneprog.irwalk import addr_split
from .. import build, record, schedule, tables
from ..events import Score, _same, _scorecells, fields_of, masks_of, terms_of, tie_of
from ..read import Unlowerable
from ..rows import blockrows, guards
from ..shape import _instruments, _offsets, _order_cursor, _rename, _resets


def freqpair(art, voices):
    """The per-voice pair a frequency record moves: the player's own ``freq``."""
    for a in art["t1"].get("accs") or ():
        if a["width"] != 16 or int(a["cell"]["copies"]) != voices:
            continue
        if (a["target"] or {}).get("register") not in ("freq", "freq_lo", "freq_hi"):
            continue
        hi = next((r for r in a["regions"] if r != a["cell"]["region"]), None)
        if hi is not None:
            lo = int(str(a["cell"].get("addr", "0")).lstrip("$"), 16)
            return lo, art["view"].by_id()[hi].base
    return None, None


def copied(low, addr):
    """The per-voice cell a scalar the tick reads a role off is copied from.

    A family that stages its row moves the byte into a scratch a later phase
    reads, so the cell the player's slot names is the copy's own source.
    """
    got = [
        (lbl, s)
        for lbl, blk in low.proc.blocks.items()
        for s in blk.stmts
        if type(s) is Store and s.cls == "ram" and addr_split(s.a)[0] == addr
    ]
    if len(got) != 1:
        return addr
    low.lbl, low.local, low.pick, low.sub = got[0][0], {}, {}, {}
    e = low.expand(got[0][1].v)
    base, idx = addr_split(e.a) if type(e) is Load else (None, None)
    return base if base is not None and idx is not None and low.isvoice(idx) else addr


def region_reach(low, blocks):
    """The reaching stores of one region only.

    Outside a fetch every cell read is the cell (``l2_phases.reader``); inside it
    what a store carries is the score's own byte, which is the read to follow.
    """
    return {lbl: (low.reaching.get(lbl, {}) if lbl in blocks else {}) for lbl in low.proc.blocks}


def supplied(low, blocks, region):
    """The names no cell of the tune holds: the bytes a fetch read (the score's)."""
    got, deep = set(), low.deep
    low.deep, low.v.finding = False, True
    low.reach = region_reach(low, region)
    low.bad.clear()
    low.gate, low.scope, low.local, low.pick, low.sub = frozenset(), frozenset(), {}, {}, {}
    for lbl in blocks:
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
    low.bad.clear()
    low.temps.clear()
    low.wide.clear()
    low.deep, low.v.finding = deep, False
    low.reach = region_reach(low, frozenset())
    return got


def visits(l1, low, rowblocks, ticks):
    """``(the visits, the name the voice index is bound to, the trip counts)``."""
    p = low.proc
    blocks = [l for l in low.rpo if l in rowblocks]
    exits = sorted({s for l in blocks for s in succs(p.blocks[l].term) if s not in rowblocks})
    exits = [e for e in exits if type(p.blocks[e].term).__name__ != "Trap"]
    cells, vnames = l1.facts["cells"], sorted(l1.facts["vidx"])
    R, fetches, _trap, _obs = record.run(
        l1.prog,
        l1.proc,
        [(blocks[0], blocks, exits)],
        ticks,
        inputs=(l1.art or {}).get("inputs") or {},
        envvars={(l1.proc, blocks[0]): vnames},
    )
    recs = fetches[(l1.proc, blocks[0])]
    return recs, record.voice_name(recs, vnames, cells.voices, cells.stride), dict(R.trips)


class Fetch:
    """One tune's fetch region as section 3.6's score, its row program and its clock."""

    def __init__(self, l1, low, rowblocks, ticks):
        self.l1, self.low, self.v = l1, low, low.v
        self.cells, self.art = l1.facts["cells"], l1.art
        self.rowblocks = frozenset(rowblocks)
        self.ticks = ticks
        entry = [l for l in low.rpo if l in rowblocks][0]
        self.sch = schedule.derive(l1.prog, l1.proc, self.rowblocks, l1.art["t0"], entry)
        self.slots, self.clockcell, self.score = {}, None, None
        self.roles, self.drop, self.packed, self.sc, self.tiemask = {}, set(), set(), {}, None
        self.trips, self._words = {}, None

    def bind(self):
        """The player's slots, bound to the cells S6, T1 and T2 name (sections 4, 5).

        ``False`` where the region's visits name no note and no record: it is no
        score, and nothing of the level's own vocabulary has been moved to say so.
        """
        low, voc, sch = self.low, self.v, self.sch
        lo, hi = freqpair(self.art, self.cells.voices)
        voc.notebase = copied(low, voc.notebase) if voc.notebase is not None else None
        voc.insbase = copied(low, voc.insbase) if voc.insbase is not None else None
        clock = sch.clock[3] if sch.clock else None
        if clock is not None and not any(
            type(x) is Store and addr_split(x.a)[0] == clock
            for l in self.rowblocks
            for x in low.proc.blocks[l].stmts
        ):
            clock = None  # a clock the row's own length does not reload is no ``rowsleft``
        got = {
            "note": voc.notebase,
            "ins": voc.insbase,
            "rowsleft": clock,
            "orderpos": _order_cursor(self.art, self.art["view"], self.art["names"]),
            "freq.lo": lo,
            "freq.hi": hi,
        }
        if got["note"] is None or got["ins"] is None:
            return False
        _rename(self.cells, got)
        self.slots = {k: v for k, v in got.items() if v is not None}
        self.clockcell = self._clockcell(clock)
        if sch.clock:
            voc.subst = {sch.clock[1].n: {"cell": "phase"}}
            self.drop = {sch.clock[2].src} | {st.src for st, _g in sch.resets}
        own = {v for k, v in self.slots.items() if not k.startswith("freq.")}
        for lbl in self.rowblocks:
            for s in low.proc.blocks[lbl].stmts:
                if type(s) is Store and addr_split(s.a)[0] in own:
                    self.drop.add(s.src)
        voc.dropstores = set(self.drop)
        voc.rowblocks = self.rowblocks
        low.stated = frozenset(id(c) for c in sch.spent)
        return True

    def _clockcell(self, clock):
        """The cell ``meta.tempo`` steps: the player's ``rowsleft``, or the tune's own."""
        sch = self.sch
        if not sch.clock:
            return None
        if clock is not None:
            return "rowsleft"
        cells = self.cells
        return cells.voicecell(sch.clock[3]) if sch.inloop else cells.scalarcell(sch.clock[3])

    def fields(self, recs, vvar):
        """Section 3.6's event fields, and what a masked score byte is of them."""
        low, cells = self.low, self.cells
        # the fields are read off the region's own stores: a byte a visit staged in
        # a cell is that byte where a guard masks it, and no cell of the tune's own
        low.reach = region_reach(low, self.rowblocks)
        pit = self.l1.facts["pitch"]
        top = (pit.base + pit.n) if pit is not None else 0x100
        img = self.l1.prog.reads()
        ordpos = self.slots.get("orderpos")
        roles = {
            "dur": self.slots.get("rowsleft", -1),
            "note": self.slots.get("note"),
            "ins": self.slots.get("ins"),
        }
        own = {a for a in roles.values() if a and (cells.at(a) or (None,))[0] == "voice"}
        seed = (
            [int(img[ordpos + v * cells.stride]) for v in range(cells.voices)]
            if ordpos is not None
            else None
        )
        self.score = Score(recs, vvar, roles, cells.voices, cells.stride, ordpos, top, seed, own)
        self.sc = _scorecells(low, self.rowblocks, self.v.supplied)
        base, temps0 = self.score.facts()
        self.packed = {
            n
            for n, m in masks_of(low)
            if n in temps0
            and _same([None if v is None else v & (m or 0xFF) for v in temps0[n]], base["dur"])
        }
        self._arms(set(roles.values()) | {ordpos})
        facts, temps = self.score.facts()
        got, left = fields_of(masks_of(low), facts, temps)
        self.tiemask, self.v.fields = tie_of(got, left)
        rows = [r for v in range(cells.voices) for r in self.score.rows[v]]
        pairs = {(l, id(c)): (l, c) for l, gs in low.guards.items() for _d, c, _t, _w in gs}
        self.v.terms = terms_of(low, sorted(pairs.values(), key=lambda x: x[0]), facts, rows)
        low.reach = region_reach(low, frozenset())

    def _arms(self, own):
        """The cells a visit's own stores arm: what the event carries besides its fields."""
        arms = {k: v for k, v in self.sc.items() if v[1] not in own and v[2] not in self.packed}
        for v in range(self.cells.voices):
            for r in self.score.rows[v]:
                r["sets"] = [
                    [cell, r["sites"][src]]
                    for src, (cell, _base, _n) in sorted(arms.items())
                    if src in r["sites"]
                ]

    def tie(self, row):
        """One row's ``tie``: the field of the packed byte no other field explains."""
        if self.tiemask is None:
            return False
        return bool(row["temps"].get(self.tiemask[0], 0) & self.tiemask[1])

    def program(self, seg, order):
        """``meta.row``: the region's own steps, over the fields the score carries."""
        low, roles = self.low, self._roles()
        low.gate = frozenset((id(c), t) for c, t in self.sch.boundary)
        low.scope, low.v.payload, low.deep = set(self.rowblocks), True, False
        low.reach = region_reach(low, self.rowblocks)
        out, ncmd, nst, streams = [], 0, 0, {}
        for _l, kind, when, sets, _d in guards(
            seg, blockrows(seg, set(self.rowblocks), order, self.drop, roles, True), order
        ):
            if kind == "note":
                out.append({"note": True, **({"when": when} if when else {})})
            elif kind == "ins":
                out.append({"ins": True})
            elif kind == "arm":
                ncmd += 1
                if ncmd == 1:
                    out.append({"commands": True})
            elif kind == "reg":
                name = "note_on%d" % nst
                streams[name] = {"rows": [{"when": when, "sets": [list(x) for x in sets]}]}
                streams[name]["all"] = True
                out.append({"stream": name})
                nst += 1
            else:
                out.append({"sets": [list(x) for x in sets], **({"when": when} if when else {})})
        low.gate, low.scope, low.v.payload, low.deep = frozenset(), frozenset(), False, True
        low.reach = region_reach(low, frozenset())
        return out, streams

    def _roles(self):
        """``{site: the step of the row program that store is}``."""
        out = {}
        for lbl in self.rowblocks:
            for s in self.low.proc.blocks[lbl].stmts:
                if type(s) is not Store:
                    continue
                base = addr_split(s.a)[0]
                if base is not None and base == self.slots.get("note"):
                    out[s.src] = "note"
                elif base is not None and base == self.slots.get("ins"):
                    out[s.src] = "ins"
                elif s.src in self.sc:
                    out[s.src] = "arm"
        return out

    def tempo(self):
        """Section 3.6's one counter: the cell the tick moves, its boundary, its clauses."""
        sch, low = self.sch, self.low
        img = self.l1.prog.reads()
        rate = build.divider_rate(sch.divider[1], low, img) if sch.divider else 1
        phase = (
            build.divider_phase(img, sch.divider[0], rate - 1, rate)
            if sch.divider and rate > 1
            else 0
        )
        return {
            "cell": self.clockcell,
            "step": sch.step,
            "rate": rate,
            "phase": phase,
            "boundary": [low.guard(c, t) for c, t in sch.boundary],
            **_resets(low, self.clockcell, sch),
        }

    def words(self):
        """Section 3.2's words past the tuning: the cells there, and the byte the score keeps."""
        if self._words is not None:
            return self._words
        low, cells, pit = self.low, self.cells, self.l1.facts["pitch"]
        if pit is None:
            self._words = []
            return self._words
        got = tables.beyond_words(cells, low, pit, tables.beyond_limit(cells, low, pit))
        names = {v[0].lstrip("@#") for v in self.sc.values() if v[2] in self.packed}
        self._words = [
            (
                {"trap": "the packed row byte, which the score keeps as an event's own fields"}
                if any(
                    isinstance(h, dict) and (h.get("cell") or [""])[0] in names
                    for h in w.get("u16", ())
                )
                else w
            )
            for w in got
        ]
        return self._words

    def beyond(self, obj):
        """Every stream's words past the tuning, as far as a transposition can reach."""
        words = self.words()
        if not words:
            return
        n = max(*_offsets([obj["streams"], obj["meta"]["row"], obj["score"]], [0]), 1)
        for st in obj["streams"].values():
            st["beyond"] = {"id": "the fused tuning", "words": words[:n]}

    def pitched(self, instruments):
        """An instrument whose sound the tuning has no note for: its own pitch (§3.5)."""
        pit, words = self.l1.facts["pitch"], self.words()
        if pit is None:
            return instruments
        top, want = pit.base + pit.n, set()
        img, base = self.l1.prog.reads(), self.slots.get("ins")
        cur = (
            {v: int(img[base + v * self.cells.stride]) for v in range(self.cells.voices)}
            if base is not None
            else {}
        )
        for v in range(self.cells.voices):
            for r in self.score.rows[v]:
                if r["ins"] is not None:
                    cur[v] = r["ins"]
                if r["note"] is not None and r["note"] >= top and v in cur:
                    want.add((cur[v], r["note"] - top))
        for key, d in sorted(want):
            rec = instruments.get(str(key))
            if rec is None or d >= len(words) or "trap" in words[d]:
                continue
            rec["pitch"] = {"value": words[d]}
            if d + 12 < len(words) and "trap" not in words[d + 12]:
                rec["pitch"]["octave"] = words[d + 12]
        return instruments

    def instruments(self):
        """T2's selector as section 3.5's records: one an entry the horizon selected."""
        ins = tables.instrument_table(self.art, self.art["view"], self.art["names"])
        if not ins:
            return {}
        img = self.l1.prog.reads()
        got = _instruments(
            self.art, self.art["view"], self.art["names"], ins, self.v.inspw, img, {}
        )
        seen = {r["ins"] for v in range(self.cells.voices) for r in self.score.rows[v]} - {None}
        if not seen:
            return self.pitched(got)
        seen |= {int(x) for x in img[ins[0] : ins[0] + self.cells.voices]}
        return self.pitched({k: v for k, v in got.items() if int(k) in seen})


def specialise(l1, low, rowblocks, blocks, ticks):
    """``Fetch`` over one tune's region, or ``None`` where the level has none."""
    if not rowblocks or not (l1.art or {}).get("t0"):
        return None
    fx = Fetch(l1, low, rowblocks, ticks)
    if not fx.sch.clock:
        return None
    if not fx.bind():
        return None
    low.v.supplied = supplied(low, [l for l in low.rpo if l in blocks], rowblocks)
    recs, vvar, trips = visits(l1, low, rowblocks, ticks)
    if not recs:
        return None
    fx.trips = trips
    fx.fields(recs, vvar)
    return fx
