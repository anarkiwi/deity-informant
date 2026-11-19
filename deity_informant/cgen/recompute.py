"""Loads recomputed where they are used instead of held in a variable: a load of a fixed cell,
a promoted variable or field at each use, an indexed byte of a region or held span of known
address set at its one use, when no operation that may write a byte of it lies on any path
from the load to a use (a forward dataflow over the function's blocks)."""

from __future__ import annotations

CHECKS = ("@codechk", "@objchk", "@io_read")


class Recompute:
    """The loads of printed function ``c`` (:class:`printer.CFunc`) to print at their uses."""

    def __init__(self, c):
        self.c, self.f = c, c.f
        self.blocks = [b for b in self.f.blocks if b.name in c.owned]
        self.points = self._points()
        self.sets = {}

    def _inline(self, v):
        c, f = self.c, self.f
        return v in c.inline or (f.remat(v) and v not in c.names)

    def _points(self):
        """``{value: [(block, index)]}``: where each value's printing is evaluated, through
        the values printed inline into others."""
        f, out = self.f, {}

        def reach(v, at, seen):
            if v in seen:
                return
            seen.add(v)
            out.setdefault(v, []).append(at)
            d = f.defs.get(v)
            if d is not None and self._inline(v):
                for a in f.reads(d):
                    reach(a, at, seen)

        for b in self.blocks:
            for i, o in enumerate(b.ops):
                term = i == len(b.ops) - 1
                if not term and (id(o) in f.dropped or not f.printed(o)):
                    continue
                if not term and f.pure(o) and all(self._inline(r) for r in o.res):
                    continue
                for a in o.args if term else f.reads(o):
                    reach(a, (b, i), set())
        return out

    def place(self, o):
        """What load ``o`` reads when it may print at its uses: its promoted scalar
        ``("s", k)`` or struct field ``("v", s, d)``, or ``("m", runs)``, the address set of
        a non-volatile access of a region or held span whose address reads no memory."""
        f, c = self.f, self.c
        if o.name != "memref.load" or id(o) in f.dropped or f.named(o) is not None:
            return None
        if id(o) in f.aware and c.mod.var_at:
            return None
        p = f.promoted.get(id(o))
        if p is not None:
            return p[:2] if p[0] == "s" else p[:3]
        if c.target(o)[0] not in ("region", "held"):
            return None
        if any(a in c.inline and c.memf.get(a) for a in f.reads(o)):
            return None
        runs, vol = self.runs(o)
        return None if vol or runs is None else ("m", runs)

    def runs(self, o):
        """:meth:`printer.Func.addr_runs` of access ``o``, cached."""
        if id(o) not in self.sets:
            self.sets[id(o)] = self.f.addr_runs(o)
        return self.sets[id(o)]

    def writes(self, x, place):
        """Whether operation ``x`` may write ``place``."""
        f = self.f
        if id(x) in f.dropped:
            return False
        if x.name == "func.call":
            callee = x.prop("callee")
            if callee in CHECKS:
                return False
            if callee != "@io_write":
                return True
            if place[0] != "m":
                return False
        elif x.name != "memref.store":
            return False
        p = f.promoted.get(id(x))
        if place[0] == "v" and p is None:
            return bool(self.c.mod.var_at)
        if p is not None or place[0] != "m":
            return p is not None and p[: len(place)] == place
        return any(a < hi and lo < b for a, b in self.runs(x)[0] for lo, hi in place[1])

    def _clobbers(self, b, i, j, place):
        return any(self.writes(x, place) for x in b.ops[i:j])

    def clear(self, o, place):
        """Whether every use of load ``o``'s value sees ``place`` as ``o`` does."""
        f = self.f
        home, k = f.where[id(o)]
        dirty = {home.name: self._clobbers(home, k + 1, len(home.ops), place)}
        work = [home]
        entry = {}
        while work:
            b = work.pop()
            for s in b.ops[-1].succs:
                if s == home.name or s not in self.c.owned:
                    continue
                d = dirty[b.name]
                if entry.get(s) is None or (d and not entry[s]):
                    entry[s] = entry.get(s, False) or d
                    blk = f.block_of[s]
                    dirty[s] = entry[s] or self._clobbers(blk, 0, len(blk.ops), place)
                    work.append(blk)
        for b, i in self.points.get(o.res[0], ()):
            if b is home:
                if i <= k or self._clobbers(home, k + 1, i, place):
                    return False
            elif entry.get(b.name, True) or self._clobbers(b, 0, i, place):
                return False
        return True

    def loads(self):
        """The values of the loads to recompute at each use, none whose address reads
        another's."""
        out = {}
        for b in self.blocks:
            for o in b.ops[:-1]:
                v = o.res[0] if len(o.res) == 1 else None
                if v is None or v in self.c.inline or not self.c.uses.get(v) or self.f.carry(v):
                    continue
                place = self.place(o)
                if place is None or self.shared(o, place) or not self.clear(o, place):
                    continue
                out[v] = o
        return {v for v, o in out.items() if not self.reaches(o, out)}

    def shared(self, o, place):
        """Whether load ``o`` of ``place`` is an indexed byte of memory used more than once
        (a variable, one subscript)."""
        indexed = place[0] == "m" and self.f.split(self.f.address(o))[1] is not None
        return indexed and self.c.uses[o.res[0]] > 1

    def reaches(self, o, loads):
        """Whether printing access ``o``'s address reads a value of ``loads``."""
        todo, seen = list(self.f.reads(o)), set()
        while todo:
            v = todo.pop()
            if v in loads:
                return True
            d = self.f.defs.get(v)
            if v not in seen and d is not None and v in self.c.inline:
                seen.add(v)
                todo += self.f.reads(d)
        return False
