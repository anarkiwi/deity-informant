"""T1 -- one named-cell expression evaluated over the certified horizon.

:mod:`.history` gives the per-tick value of every named cell; a table the program
never writes is its own image byte. :class:`Cells` reads an S6 expression as one
array per tick -- what an accumulator's interval and its replay are checked on.
"""

from __future__ import annotations

import numpy as np

from .facts import Facts, scales
from .ir import Bin, Const, Load, MASK, R16, Var


def _cmp(op, a, b):
    if op == "==":
        return a == b
    if op == "!=":
        return a != b
    return a < b if op == "<" else a <= b


def evalarr(op, a, b, w):
    """One IR binary op over whole-horizon arrays, byte-exactly as :func:`~.ir.evalbin`."""
    m = MASK[w]
    if op == "+":
        return (a + b) & m
    if op == "-":
        return (a - b) & m
    if op == "&":
        return a & b
    if op == "|":
        return a | b
    if op == "^":
        return a ^ b
    if op == "<<":
        return (a << b) & m
    if op == ">>":
        return a >> b
    if op == "carry":
        return ((a + b) > m).astype(np.int64)
    return _cmp(op, a, b).astype(np.int64) if op in ("==", "!=", "<", "<=") else None


class Cells:
    """Every named cell of one program as a column, and expressions over them."""

    def __init__(self, prog, names, hist, facts=None):
        self.prog = prog
        self.names = names
        self.hist = hist
        self.rgn = prog.by_id()
        self.scale = scales(facts or Facts(prog))
        self.img = np.frombuffer(bytes(prog.reads()), np.uint8).astype(np.int64)
        self.ticks = min((a.shape[0] for a in hist.values()), default=0)
        self.group = {}
        for g, v in (names.groups or {}).items():
            for rid in v["members"]:
                self.group[rid] = (g, max(int(v["stride"]), 1), int(v["n"]))
        self._cols = {}
        self.zeros = np.zeros(self.ticks, np.int64)
        self.bad = np.zeros(self.ticks, bool)
        self.subst = {}
        self.counters = {}
        self._lag = {}

    def lagged(self, rids):
        """Every sampled byte of ``rids`` one tick back: what a guard beside a store read.

        A producer's guard runs before the block's own writes, and the tick's
        post-values are the only ones :mod:`.history` keeps.
        """
        out = {}
        for rid in rids:
            if rid not in self._lag:
                r = self.rgn.get(rid)
                span = range(r.base, r.base + r.size) if r is not None else ()
                self._lag[rid] = {
                    (rid, a): np.concatenate(([c[0]], c[:-1]))
                    for a in span
                    for c in (self.col(rid, a),)
                    if c is not None
                }
            out.update(self._lag[rid])
        return out

    def col(self, rid, addr):
        """The per-tick values of one byte, where :mod:`.history` sampled it."""
        if (rid, addr) not in self._cols:
            got = self.hist.cell(rid, addr)
            self._cols[(rid, addr)] = None if got is None else got[: self.ticks].astype(np.int64)
        return self._cols[(rid, addr)]

    def byte(self, rid, addr, lo, hi):
        """One byte of state or of the image, inside the region or the access's envelope."""
        got = self.subst.get((rid, addr))
        got = self.col(rid, addr) if got is None else got
        if got is not None:
            return got
        r = self.rgn.get(rid)
        here = r is not None and r.base <= addr < r.base + r.size
        if r is None or r.kind == "state" or not (here or (lo is not None and lo <= addr <= hi)):
            return None
        return np.full(self.ticks, int(self.img[addr]), np.int64)

    def load(self, rid, addr, w, lo, hi):
        """A byte read at a per-tick address array, gathered over the addresses it took.

        An address no name covers is not a failure of the whole value: it is marked
        in :attr:`bad`, which a clause's own guard must exclude.
        """
        if addr is None or w != 1:
            return None
        out = np.zeros(self.ticks, np.int64)
        for a in np.unique(addr):
            c, here = self.byte(rid, int(a), lo, hi), addr == a
            if c is None:
                self.bad |= here
                continue
            out = np.where(here, c, out)
        return out

    def index(self, e, env):
        """An address expression; a copy index is this copy's own displacement."""
        t = type(e)
        if t is Const:
            return np.full(self.ticks, e.v, np.int64)
        if t is Var:
            got = env.get(e.n)
            return None if got is None else np.full(self.ticks, got, np.int64)
        if t is Bin:
            a, b = self.index(e.a, env), self.index(e.b, env)
            return None if a is None or b is None else evalarr(e.op, a, b, max(e.w, 2))
        return self.value(e, env)

    def value(self, e, env):
        """One value over the horizon, or ``None`` where a name it reads has none."""
        t = type(e)
        if t is Const:
            return np.full(self.ticks, e.v, np.int64)
        if t is Load:
            return self.load(e.r, self.index(e.a, env), e.w, e.lo, e.hi)
        if t is R16:
            lo, hi = self.half(e.lo, e, env), self.half(e.hi, e, env)
            return None if lo is None or hi is None else lo | (hi << 8)
        if t is Bin:
            a, b = self.value(e.a, env), self.value(e.b, env)
            return None if a is None or b is None else evalarr(e.op, a, b, e.w)
        return None

    def half(self, cell, e, env):
        """One half of a 16-bit view: its own cell's address, indexed as the low one is.

        The halves share neither region nor displacement (JCH's shift accumulator is
        two unrelated bytes), so the low half's index is rebased on each.
        """
        a = self.index(e.a, env)
        return None if a is None else self.load(cell[0], a + (cell[1] - e.lo[1]), 1, None, None)


def evaluate(cells, e, env):
    """``(value, the ticks it had no name for)``: one expression over the horizon."""
    cells.bad = np.zeros(cells.ticks, bool)
    got = cells.value(e, env)
    return got, cells.bad


def truth(cells, guards, env):
    """``(per-tick mask, how many conditions had no history)`` for one guard path.

    Index algebra and a flag no caller resolved are not conditions over cells; they
    are counted, and a replay they falsify refuses.
    """
    out, gone, base = np.ones(cells.ticks, bool), 0, dict(cells.subst)
    for g, t, wr in guards:
        cells.subst = {**base, **cells.lagged(wr)}
        v, bad = evaluate(cells, g, env)
        cells.subst = base
        if v is None or bad.any():
            gone += 1
            continue
        out &= (v != 0) if t else (v == 0)
    return out, gone


def _lag(a):
    """``a`` one tick back: the value the same expression had over last tick's cells."""
    return np.concatenate(([a[0]], a[:-1]))


def replay(cur, prev, plan, width):
    """Which ticks the record's own clauses regenerate the value the cell took.

    The record states what a producer does, not when the tick runs it (section 4's
    commit order does), so a value that did not move is no divergence; a move no
    clause of the record produces is one.
    """
    m = (1 << width) - 1
    ok = cur == prev
    for c in plan:
        if c["kind"] == "any":
            ok |= c["when"]
            continue
        if c["kind"] == "step":
            base = (prev ^ m) if c["comp"] else prev
            got = (base + c["sign"] * c["delta"] + c["carry"]) & m
            alt = (base + c["sign"] * _lag(c["delta"]) + c["carry"]) & m
            if c["live"]:
                ok |= c["when"] & (cur == ((got + 1) & m))
        else:
            got, alt = c["value"] & m, _lag(c["value"]) & m
        ok |= c["when"] & ((cur == got) | (cur == alt))
    ok |= cur == _sequence(prev, plan, m)
    ok[0] = True
    return ok


def _sequence(prev, plan, m):
    """The value the clauses leave when the tick runs every one whose guards hold.

    A reload and the step that follows it are one tick (JCH re-points a pulse
    segment and moves it in the same call), which no single clause states.
    """
    val = prev
    for c in plan:
        if c["kind"] == "any":
            continue
        if c["kind"] == "step":
            base = (val ^ m) if c["comp"] else val
            got = (base + c["sign"] * c["delta"] + c["carry"]) & m
        else:
            got = c["value"] & m
        val = np.where(c["when"], got, val)
    return val


def interval(cells, bound, env, elem):
    """``(lo, hi)`` of a bound as arrays, or ``None`` where a cell of it has none."""
    out = []
    for v in bound["interval"]:
        if isinstance(v, int):
            out.append(np.full(cells.ticks, v, np.int64))
            continue
        got = cells.byte(v["region"], int(v["addr"][1:], 16) + elem * v.get("scale", 0), None, None)
        if got is None:
            return None
        out.append(got)
    return tuple(out)


def plan_of(cells, clauses, env):
    """``([clause plan], dropped, unnamed)`` in tick order, or ``None`` for a step.

    A step whose delta no name reaches is the ``unclassified update`` T1 refuses; a
    step whose carry no name reaches is section 5's ``+ carry(site)``, one live bit
    either way; an absolute set is section 4's producer, and T1 states only when.
    """
    out, gone, blind = [], 0, 0
    for c in sorted(clauses, key=lambda c: c.rank):
        when, miss = truth(cells, c.guards, env)
        gone += miss
        if c.kind == "step":
            d, bad = evaluate(cells, c.delta, env)
            k, kbad = (cells.zeros, bad) if c.carry is None else evaluate(cells, c.carry, env)
            if d is None:
                return None, gone, blind
            live = k is None or (kbad & when).any()
            borrow = -1 if _isborrow(c.carry) else 0
            blind += int((bad & when).any())
            when = when & ~bad
            out.append(
                {
                    "when": when,
                    "kind": "step",
                    "sign": c.sign,
                    "delta": d,
                    "carry": np.full(cells.ticks, borrow, np.int64) if live else k,
                    "live": live,
                    "comp": c.comp,
                }
            )
        elif c.kind == "half":
            out.append({"when": when, "kind": "any"})
            blind += 1
        else:
            v, bad = evaluate(cells, c.value, env)
            if v is None or (bad & when).any():
                blind += 1
                out.append({"when": when, "kind": "any"})
            else:
                out.append({"when": when, "kind": "action", "value": v})
    return out, gone, blind


def _isborrow(e):
    """True for the ``C - 1`` a subtract's borrow adjustment is spelled as."""
    return type(e) is Bin and e.op == "-" and type(e.b) is Const and e.b.v == 1


def verify(cells, acc, clauses, bounds):
    """The recurrence replay and the first interval of ``bounds`` the horizon keeps.

    A cell is read before it is written, so inside its own update the accumulator
    reads last tick's value: :attr:`Cells.subst` is that epoch, and it also gives
    the previous value of whichever record a cursor selected this tick.
    """
    c, width, scale = acc["cell"], acc["width"], acc["scale"]
    out = {"ticks": cells.ticks, "copies": 0, "divergences": 0, "dropped": 0, "unnamed": 0}
    escapes = [0] * len(bounds)
    for elem in range(c["copies"]):
        env = {n: elem * scale for n in acc["index"]}
        cur = cells.value(acc["read"], env)
        cells.subst = _epoch(cells, acc)
        prev = cells.value(acc["read"], env)
        plan, gone, blind = plan_of(cells, clauses, env)
        cells.subst = {}
        spans = [interval(cells, b, env, elem) for b in bounds]
        out["dropped"] += gone
        out["unnamed"] = max(out["unnamed"], blind)
        if cur is None or prev is None or plan is None or any(s is None for s in spans):
            bad = "read" if cur is None or prev is None else ("delta" if plan is None else "bound")
            return None, dict(out, escapes=0, why="not over named cells: " + bad)
        out["copies"] += 1
        out["divergences"] += int((~replay(cur, prev, plan, width)).sum())
        for i, s in enumerate(spans):
            escapes[i] += int(((cur < s[0]) | (cur > s[1])).sum())
    if not out["copies"]:
        return None, dict(out, escapes=0, why="no history")
    keep = next((i for i, n in enumerate(escapes) if not n), len(bounds) - 1)
    return bounds[keep], dict(out, escapes=escapes[keep])


def _epoch(cells, acc):
    """The values one tick's own reads saw: last tick's, and a divider's own borrow.

    A countdown is read after it is stepped and before it is reloaded, and that
    value is no post-tick observable: it is last tick's, minus one where the tick
    ran the counter at all.
    """
    out = {}
    for rid, _base in cells.counters:
        kind = cells.counters[(rid, _base)][0]
        r = cells.rgn.get(rid)
        for a in () if r is None else range(r.base, r.base + r.size):
            col = cells.col(rid, a)
            if col is None:
                continue
            was = np.concatenate(([col[0]], col[:-1]))
            step = -1 if kind == "countdown" else 1
            out[(rid, a)] = np.where(col == was, was, (was + step) & 0xFF)
    for rid in acc["regions"]:
        r = cells.rgn.get(rid)
        for a in () if r is None else range(r.base, r.base + r.size):
            got = cells.col(rid, a)
            if got is not None:
                out[(rid, a)] = np.concatenate(([got[0]], got[:-1]))
    return out


def _widen(cells, acc, lo, off):
    """The accumulator's own value: one byte, or the pair or split its target names."""
    hi = acc.get("hi")
    if hi is None:
        return lo
    top = cells.col(hi["region"], int(hi["addr"][1:], 16) + off)
    return lo if top is None else lo | (top << (acc["target"]["split"] or (8, 8))[0])
