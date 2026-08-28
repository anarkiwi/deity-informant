"""T1 -- one Acc's recurrence as an exact per-tick step, and its proof.

A read's epoch is its tick rank against the writes of its cell: last tick's value
before them all, this tick's after them all, and between them the cell's own
clauses so far applied to last tick's. What no named cell states is :class:`Inexact`.
"""

from __future__ import annotations

from collections import namedtuple
from itertools import product

import numpy as np

from ..trackerprog.resolve import Program, walkx
from .accdelta import _cellref
from .accdelta import unscratch as tabfree
from .accguard import cellof, valnames
from .acchist import _lag, evalarr, interval
from .accrule import copies_of
from .accshape import key_of
from .facts import SID_VOICES, elem_count
from .ir import Bin, Const, Load, R16, REGVAR, Var
from .irwalk import addr_split, walk
from .nodes import At, Sel

State = namedtuple("State", "env parts snaps prev site disp where")
COPY = "#copy"  # the target's own copy index, bound per copy by :func:`prove`
END = (float("inf"),)  # a rank past every clause of the tick


class Inexact(Exception):
    """A term no named cell states, at the site it stands."""

    def __init__(self, why, site):
        super().__init__("%s at %s" % (why, site))
        self.why, self.site = why, site


def _pc(c):
    return "$%04X" % c.site.stmt.src


def _key(at):
    return "/".join(map(str, at))


def _isborrow(e):
    return type(e) is Bin and e.op == "-" and type(e.b) is Const and e.b.v == 1


def _reads(y):
    """The constant-address cells one node reads, for a reaching-store cycle test."""
    if type(y) is Load:
        return {cellof(y)} - {None}
    return {tuple(y.lo), tuple(y.hi)} if type(y) is R16 else set()


def _byte(k):
    return Load("ram", Const(k[1], 2), 1, k[1], k[1], k[0])


def _where(c):
    return (c.site.proc, c.site.block, c.site.at, c.chain)


def parts_of(acc):
    """``{cell: (shift, bits)}``: where each byte of the target sits in its value."""
    tgt, hi = acc["target_cells"], acc.get("hi")
    if hi is not None:
        k, kh = acc["target"]["split"]
        return {tgt.cells[0]: (0, k), (hi["region"], int(hi["addr"][1:], 16)): (k, kh)}
    if tgt.kind == "pair":
        return {tgt.cells[0]: (0, 8), tgt.cells[1]: (8, 8)}
    return {tgt.cells[0]: (0, acc["width"])}


class Stepper:
    """The exact reading of every clause over one program's tick order."""

    def __init__(self, ctx, cells, raw):
        self.ctx, self.cells, self.program = ctx, cells, Program(ctx, mark=True)
        self.by, ranks = {}, {}
        for tgt, cs in raw.items():
            for rid, _a in tgt.cells:
                self.by.setdefault(rid, []).extend((tgt, c) for c in cs)
            for c in cs:
                _n, scale, copies = copies_of(cells, [c])
                for rid, a in tgt.cells:
                    for k in range(copies):
                        ranks.setdefault((rid, a + k * max(scale, 1)), set()).add((c.rank, c.chain))
        self.writes = {k: sorted(v) for k, v in ranks.items()}
        self.memo, self.busy, self.inputs = {}, set(), {}
        self.bad, self.unnamed = np.zeros(cells.ticks, bool), {}

    def epoch(self, cell, at, chain):
        """``pre``/``post``/``mid``: the read against the writes a tick can make beside it."""
        ws = [r for r, ch in self.writes.get(cell) or () if not self.ctx.exclusive(chain, ch)]
        if not ws or ws[0] > at:
            return "pre"
        return "post" if ws[-1] < at else "mid"

    def full(self, v):
        return np.full(self.cells.ticks, int(v), np.int64)

    def ref(self, rid, addr):
        return _cellref(self.ctx, self.cells, _byte((rid, addr)))

    # ---- expressions ----------------------------------------------------------
    def value(self, e, at, st):
        """``(per-tick value, its JSON)`` of one expression read at rank ``at``."""
        t = type(e)
        if t is Const:
            return self.full(e.v), {"const": e.v}
        if t is Var:
            if e.n in st.env:
                return self.full(st.env[e.n]), {"index": e.n}
            x, st = self.open(e, st)
            return self.value(x, at, st)
        if t is Bin:
            a, ja = self.value(e.a, at, st)
            b, jb = self.value(e.b, at, st)
            got = evalarr(e.op, a, b, e.w or 2)
            if got is None:
                raise Inexact("operator %s" % e.op, st.site)
            return got, {"op": e.op, "a": ja, "b": jb, "w": e.w}
        if t is Load:
            if e.w == 2:  # a pointer held in data: its two bytes
                lo = Load(e.cls, e.a, 1, e.lo, e.hi, e.r)
                hi = Load(e.cls, Bin("+", e.a, Const(1, 1), 2), 1, e.lo, e.hi + 1, e.r)
                return self.value(Bin("|", lo, Bin("<<", hi, Const(8, 1), 2), 2), at, st)
            if e.w != 1:
                raise Inexact("load of %d bytes" % e.w, st.site)
            if cellof(e) in self.ctx.scratch:
                x = self.unscratch(e, st)
                if x is not None:
                    return self.value(x[0], at, x[1])
            a, ja = self.value(e.a, at, st)
            return self.bytes(e.r, a, ja, at, st, e.lo, e.hi)
        if t is R16:
            x = self.unscratch(e, st) if set(map(tuple, (e.lo, e.hi))) & self.ctx.scratch else None
            if x is not None:
                return self.value(x[0], at, x[1])
            a, ja = self.value(e.a, at, st)
            lo, jl = self.bytes(e.lo[0], a, ja, at, st, None, None)
            hi, jh = self.bytes(e.hi[0], a + (e.hi[1] - e.lo[1]), ja, at, st, None, None)
            return lo | (hi << 8), {"pair": [jl, jh]}
        if t is Sel:
            return self.select(e, at, st)
        if t is At:
            return self.value(e.e, *self.site(e, st))
        raise Inexact("expression %s" % t.__name__, st.site)

    def site(self, e, st):
        """``(rank, state)`` of a definition's own site, on the chain the use is in.

        A return's site is one level down the chain through the call; a definition
        of a caller's (an argument opened there) is at the level that owns it.
        """
        proc, lbl, idx = e.site
        chain, levels = tuple(st.where[3]), [st.where[0]] + [h[0] for h in st.where[3]]
        if e.via is not None:
            chain = (e.via,) + chain
        elif proc != st.where[0]:
            if proc not in levels:
                raise Inexact("definition of %s off the chain" % proc, st.site)
            chain = chain[levels.index(proc):]
        n = len(self.ctx.prog.procs[proc].blocks[lbl].stmts)
        key = (proc, lbl) if idx >= n else (proc, lbl, idx)
        return self.ctx.at(chain, key), st._replace(where=(proc, lbl, idx, chain))

    def open(self, e, st):
        """A free name as the value that reaches its site, with the site it is read in.

        A parameter is the argument the visit's own call passed, read in the caller;
        any other name is what :class:`~.trackerprog.resolve.Resolver` finds reaching
        the site -- a call's return as one alternative per exit.
        """
        proc, lbl, idx, chain = st.where
        while True:
            p = self.ctx.prog.procs[proc]
            names = [REGVAR[i] for i in p.params]
            if e.n in names:
                if not chain:
                    raise Inexact("entry register %s" % e.n, st.site)
                cproc, clbl, ci = chain[0]
                call = self.ctx.prog.procs[cproc].blocks[clbl].stmts[ci]
                where = (cproc, clbl, ci, chain[1:])
                return call.args[names.index(e.n)], st._replace(where=where)
            x = self.program.of(proc).open(e, lbl, idx)
            if not (type(x) is Var and x.n == e.n):
                return x, st._replace(where=(proc, lbl, idx, chain))
            if not chain:  # a caller's name the arm substituted: read at its own level
                raise Inexact("free name %s" % e.n, st.site)
            (proc, lbl, idx), chain = chain[0], chain[1:]

    def unscratch(self, e, st):
        """A scratch cell's read as the stores reaching it up the chain, or ``None``.

        A cell a copy loop rewrites holds one copy's value at a time: the store that
        filled it is the value, in this procedure or in a caller's before the call. A
        read no store reaches falls back to the column, which the replay then holds
        to the last copy's value.
        """
        proc, lbl, idx, chain = st.where
        cells = {cellof(e)} if type(e) is Load else {tuple(e.lo), tuple(e.hi)}
        while True:
            x = self.program.of(proc).open(e, lbl, idx)
            if not any(_reads(y) & cells for y in walkx(x)):
                return x, st._replace(where=(proc, lbl, idx, chain))
            if not chain:
                return None
            (proc, lbl, idx), chain = chain[0], chain[1:]

    def select(self, e, at, st):
        """A resolved value's alternatives, each under its own guards at their epochs."""
        out, alts, bad = None, [], self.bad
        for gs, x in e.alts:
            self.bad = np.zeros(self.cells.ticks, bool)
            v, jv = self.value(x, at, st)
            when, tests = np.ones(self.cells.ticks, bool), []
            for c, t, _w, *d in gs if out is not None else ():
                here = self.ctx.at(st.where[3], d[0]) if d and d[0][0] == st.where[0] else at
                g, jg = self.value(c, here, st)
                when &= (g != 0) if t else (g == 0)
                tests.append({"test": jg, "truth": t, "at": here})
            out = v if out is None else np.where(when, v, out)
            bad = np.where(when, self.bad, bad) if alts else bad | self.bad
            alts.append({"when": tests, "value": jv})
        self.bad = bad
        if out is None:
            raise Inexact("no reaching value", st.site)
        return out, {"sel": alts}

    def bytes(self, rid, addr, ja, at, st, lo, hi):
        """One byte read at a per-tick address, each address at its own epoch."""
        out, kinds, base = np.zeros(self.cells.ticks, np.int64), {}, None
        r = self.cells.rgn.get(rid)
        for x in np.unique(addr):
            x, here = int(x), addr == x
            base = x if base is None else min(base, x)
            part = st.parts.get((rid, x))
            inside = r is None or r.base <= x < r.base + r.size or (lo is not None and lo <= x <= hi)
            if not inside:
                self.bad |= here
                self.unnamed[(rid, x)] = here
                continue
            if part is not None:
                v = (self.running(st, at) >> part[0]) & ((1 << part[1]) - 1)
                kinds[x] = "self"
            elif self.cells.col(rid, x) is None:
                v = self.cells.byte(rid, x, lo, hi)
                if v is None:
                    self.bad |= here
                    self.unnamed[(rid, x)] = here
                    continue
                kinds[x] = "image"
            else:
                ep, col = self.epoch((rid, x), at, st.where[3]), self.cells.col(rid, x)
                v = _lag(col) if ep == "pre" else col if ep == "post" else self.prefix(rid, x, at, st)
                kinds[x] = ep
            out = np.where(here, v, out)
        if not kinds:
            return out, {"unnamed": rid}
        kind, ref = set(kinds.values()), self.ref(rid, base)
        if kind == {"self"}:
            part = st.parts[(rid, base)]
            return out, {"self": ref["name"], "shift": part[0], "bits": part[1]}
        if kind == {"image"}:
            return out, {"table": ref["name"], "region": rid, "addr": ja}
        got = {"cell": ref, "addr": ja, "epoch": kind.pop() if len(kind) == 1 else None}
        if got["epoch"] is None:
            got["epochs"] = {"$%04X" % x: k for x, k in sorted(kinds.items())}
        if "mid" in kinds.values():
            got["before"] = at
        return out, got

    def running(self, st, at):
        """The target's own value as a read at rank ``at`` sees it."""
        return next((v for r, v in reversed(st.snaps) if r < at), st.prev)

    # ---- the epoch between a cell's own writes ------------------------------------
    def prefix(self, rid, x, at, st):
        """``(rid, x)`` after its clauses of rank below ``at``, from last tick's value.

        The record carries those clauses as the input's own recurrence, and
        ``complete`` says whether the cell's whole clause set reproduces its column
        -- a cell the score decoder moves per pattern byte does not, and the acc's
        own proof is what covers the part it reads.
        """
        env = tuple(sorted(st.env.items()))
        key = (rid, x, at, env)
        if key in self.memo:
            got, entry = self.memo[key]
            self.inputs[entry[0]] = entry[1]
            return got
        if key in self.busy:
            raise Inexact("cyclic epoch of $%04X" % x, st.site)
        self.busy.add(key)
        was, self.bad = self.bad, np.zeros(self.cells.ticks, bool)
        try:
            val, out = self._prefix(rid, x, at, st)
        finally:
            self.busy.discard(key)
            self.bad = was
        entry = ("%s@%s" % (self.ref(rid, x)["name"], _key(at)), {"before": at, "clauses": out})
        self.memo[key] = (val, entry)
        if at != END:
            entry[1]["complete"] = self.complete(rid, x, st)
            self.inputs[entry[0]] = entry[1]
        return val

    def complete(self, rid, x, st):
        """True when a cell's whole clause set reproduces its column, ``None`` if unreadable."""
        env = tuple(sorted(st.env.items()))
        if (rid, x, END, env) in self.busy:
            return None
        try:
            full = self.prefix(rid, x, END, st)
        except Inexact:
            return None
        return bool((full[1:] == self.cells.col(rid, x)[1:]).all())

    def _prefix(self, rid, x, at, st):
        prev = _lag(self.cells.col(rid, x))
        sub = State(dict(st.env), {(rid, x): (0, 8)}, [], prev, st.site, 0, st.where)
        val, out = prev, []
        for tgt, c in sorted(self.by[rid], key=lambda tc: (tc[1].rank, _pc(tc[1]))):
            cell = self._cellof(tgt, c, rid, x) if c.rank < at else None
            if cell is None or self.ctx.exclusive(st.where[3], c.chain):
                continue
            here = sub._replace(env=self._bind(c, x, sub.env), site=_pc(c), where=_where(c))
            self.bad[:] = False
            when, jc = self.clause(c, here, x)
            if tgt.kind == "byte":
                got = self.apply(c, val, here, jc, 0xFF)
            else:
                shift = 8 * tgt.cells.index(cell)
                other = tgt.cells[1 - tgt.cells.index(cell)]
                rest = self.prefix(other[0], other[1] + x - cell[1], c.rank, st) << (8 - shift)
                got = (self.apply(c, val << shift | rest, here, jc, 0xFFFF) >> shift) & 0xFF
            self.check(when, here)
            val = np.where(when, got, val)
            sub.snaps.append((c.rank, val))
            out.append(jc)
        return val, out

    def _cellof(self, tgt, c, rid, x):
        """The target byte a clause writes, when ``x`` is one of its copies."""
        _n, scale, copies = copies_of(self.cells, [c])
        for k in tgt.cells:
            if k[0] == rid and 0 <= x - k[1] < max(copies, 1) * max(scale, 1):
                return k
        return None

    @staticmethod
    def _bind(c, x, env):
        """A clause's own copy index bound to the byte it must write."""
        base, idx = addr_split(c.addr)
        if not {v.n for v in walk(c.addr) if type(v) is Var} - set(env):
            return env
        if type(idx) is not Var or base is None:
            raise Inexact("index shape %r" % (c.addr,), _pc(c))
        return {**env, idx.n: x - base}

    # ---- clauses ------------------------------------------------------------------
    def check(self, when, st):
        """Refuse a tick the clause runs on with an address no name covers."""
        bad = np.flatnonzero(self.bad & when)
        bad = bad[bad > 0]
        if bad.size:
            hit = next((k for k, m in self.unnamed.items() if m[bad[0]]), (0, 0))
            raise Inexact("unnamed address $%04X (region %d) on tick %d" % (hit[1], hit[0], bad[0]), st.site)
        self.unnamed = {}

    def guards(self, guards, at, st):
        """``(the ticks a guard path holds, its JSON)``: each condition read at its decider.

        The condition is the branch's own, resolved at the end of its deciding block
        on the chain the clause runs under -- not the opened one the classifier reads.
        """
        when, tests = np.ones(self.cells.ticks, bool), []
        for i, (g, t, _w) in enumerate(guards):
            rank, where = at[i] if i < len(at) else ((), st.where)
            if i < len(at):
                g = self.ctx.prog.procs[where[0]].blocks[where[1]].term.c
            v, j = self.value(g, rank, st._replace(where=where))
            when &= (v != 0) if t else (v == 0)
            tests.append({"test": j, "truth": t, "at": rank})
        return when, tests

    def clause(self, c, st, want):
        """``(the ticks a clause writes address ``want``, its JSON)``."""
        when, tests = self.guards(c.guards, c.at, st)
        base, idx = addr_split(c.addr)
        if base is None:
            raise Inexact("address %r" % (c.addr,), st.site)
        if want is None:
            cell = key_of(c.site.stmt).cells[0]
            if cell not in st.parts:
                raise Inexact("clause writes $%04X, off its target" % cell[1], st.site)
            want = cell[1] + st.disp
        addr, ja = (self.full(0), {"const": 0}) if idx is None else self.value(idx, c.rank, st)
        when &= addr + base == want
        return when, {"site": _pc(c), "rank": c.rank, "kind": c.kind, "when": tests, "copy": ja}

    def apply(self, c, val, st, jc, m):
        """The value one clause leaves, given the running value it reads."""
        tab = self.cells.tabstep
        if c.kind == "step":
            d, jd = self.value(tabfree(c.delta if c.dexact is None else c.dexact, tab), c.rank, st)
            k = self.full(0)
            jc.update(sign=c.sign, delta=jd, comp=c.comp, carry=None, times=None)
            carry = c.carry if c.cexact is None else c.cexact
            if carry is not None:
                inner = carry.a if _isborrow(carry) else carry
                k, jk = self.value(tabfree(inner, tab), c.rank, st)
                k = k - 1 if _isborrow(carry) else k
                jc["carry"] = {"borrow": _isborrow(carry), "flag": jk}
            if c.times is not None:
                n, jc["times"] = self.value(c.times, c.rank, st)
                d = d * n
            return (((val ^ m) if c.comp else val) + c.sign * d + k) & m
        v, jc["value"] = self.value(tabfree(c.value if c.exact is None else c.exact, tab), c.rank, st)
        if c.kind == "half":
            jc["shift"] = c.shift
            mask = (0xFF << c.shift) & m
            return ((val & ~mask) | ((v << c.shift) & mask)) & m
        return v & m

    def run(self, acc, plan, env, prev, disp):
        """``(the value after every tick, the step record)`` of one copy of an Acc.

        A clause under a copy loop's own index that the target does not share (a
        global cell written for one voice) runs once per value the loop gives it.
        """
        self.inputs = {}
        st = State(env, parts_of(acc), [], prev, None, disp, None)
        val, out, m = prev, [], (1 << acc["width"]) - 1
        loops = indexes(self.cells, [x for c in plan for x in exprs_of(c)])
        for c in sorted(plan, key=lambda c: (c.rank, _pc(c))):
            free = sorted(set(loops) & {v.n for e in exprs_of(c) for v in walk(e) if type(v) is Var})
            free = [n for n in free if n not in env]
            for binding in product(*[[k * loops[n] for k in range(SID_VOICES)] for n in free]):
                bound = dict(zip(free, binding))
                here = st._replace(env={**env, **bound}, site=_pc(c), where=_where(c))
                self.bad[:] = False
                when, jc = self.clause(c, here, None)
                got = self.apply(c, val, here, jc, m)
                self.check(when, here)
                val = np.where(when, got, val)
                st.snaps.append((c.rank, val))
                out.append(dict(jc, bind=bound) if bound else jc)
        step = {
            "width": acc["width"],
            "value": [
                {"cell": self.ref(*k), "shift": s, "bits": b}
                for k, (s, b) in sorted(st.parts.items(), key=lambda kv: kv[1])
            ],
            "clauses": out,
            "inputs": dict(sorted(self.inputs.items())),
        }
        return val, step


def exprs_of(c):
    """Every expression one clause stands on: its value, its delta and its guards."""
    got = (c.value, c.exact, c.delta, c.carry, c.times, c.addr)
    return [x for x in got if x is not None] + [g for g, _t, _w in c.guards]


def indexes(cells, exprs):
    """``{name: stride}`` of every index a three-copy access takes: a voice loop's own.

    :func:`~.accshape.arms` leaves a copy loop's index standing in the value and in
    the guards, because every copy shares them; a name indexing a region of three
    elements, or an access whose envelope spans three, is bound per copy.
    """
    out = {}
    for e in exprs:
        for x in walk(e):
            r = cells.rgn.get(x.r) if type(x) is Load else None
            if r is None:
                continue
            for v in valnames(x.a):
                scale = max(cells.scale.get(v) or r.stride, 1)
                if SID_VOICES in ((x.hi - x.lo) // scale + 1, elem_count(r)):
                    out[v] = max(out.get(v, 0), scale)
    return out


def prove(cells, acc, plan, bounds, stepper, per=None):
    """``(bound, verdict, step)``: the recurrence replayed exactly, every copy, every tick.

    ``per`` replaces a scratch cell's column with the register series each voice
    is read off, with the ticks another site wrote it left out of the claim.
    """
    c, scale = acc["cell"], acc["scale"]
    out = {"ticks": cells.ticks, "copies": 0, "divergences": 0, "tick": None}
    escapes, step = [0] * len(bounds), None
    rows = per or [
        (e, {n: e * scale for n in acc["index"] + [COPY]}, None, None) for e in range(c["copies"])
    ]
    for elem, env, col, alien in rows:
        cur = col if col is not None else cells.value(acc["read"], env)
        if cur is None:
            return None, dict(out, escapes=0, why="read"), None
        try:
            val, step = stepper.run(acc, plan, env, _lag(cur), 0 if per else elem * scale)
        except Inexact as x:
            return None, dict(out, escapes=0, why=x.why, site=x.site), None
        ok = (val == cur) if alien is None else (val == cur) | alien
        ok[0] = True
        bad = np.flatnonzero(~ok)
        out["copies"] += 1
        out["divergences"] += int(bad.size)
        if bad.size and out["tick"] is None:
            out["tick"] = int(bad[0])
        spans = [interval(cells, b, env, elem) for b in bounds]
        if any(s is None for s in spans):
            return None, dict(out, escapes=0, why="bound not over named cells"), None
        for i, s in enumerate(spans):
            escapes[i] += int(((cur < s[0]) | (cur > s[1])).sum())
    if not out["copies"]:
        return None, dict(out, escapes=0, why="no history"), None
    keep = next((i for i, n in enumerate(escapes) if not n), len(bounds) - 1)
    return bounds[keep], dict(out, escapes=escapes[keep]), step
