"""Loads recomputed where they are used: a load of a fixed memory cell read more than once is
printed at each use instead of held in a variable when no operation that may write the cell
lies on any path from the load to a use (a forward dataflow over the function's blocks)."""

from __future__ import annotations

CHECKS = ("@codechk", "@objchk", "@io_read")


class Recompute:
    """The loads of printed function ``c`` (:class:`printer.CFunc`) to print at their uses."""

    def __init__(self, c):
        self.c, self.f = c, c.f
        self.blocks = [b for b in self.f.blocks if b.name in c.owned]
        self.points = self._points()

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

    def cell(self, o):
        """The cell load ``o`` reads, when it is one fixed, non-volatile byte: its address, or
        its promoted scalar; else None."""
        f, mod = self.f, self.c.mod
        if o.name != "memref.load" or id(o) in f.dropped or f.named(o) is not None:
            return None
        if id(o) in f.aware and mod.var_at:
            return None
        p = f.promoted.get(id(o))
        if p is not None:
            return ("s", p[1]) if p[0] == "s" else None
        k, rest = f.split(f.address(o))
        if rest is not None or self.c.target(o)[0] not in ("region", "held"):
            return None
        k &= 0xFFFF
        return None if mod.mem.is_volatile(k, k) else k

    def writes(self, x, cell):
        """Whether operation ``x`` may write ``cell``."""
        f = self.f
        if id(x) in f.dropped:
            return False
        if x.name == "func.call":
            callee = x.prop("callee")
            if callee in CHECKS:
                return False
            if callee != "@io_write":
                return True
        elif x.name != "memref.store":
            return False
        p = f.promoted.get(id(x))
        if p is not None or isinstance(cell, tuple):
            return p is not None and p[0] == "s" and ("s", p[1]) == cell
        lo, hi = f.addr_iv(x)
        return lo <= cell <= hi

    def _clobbers(self, b, i, j, cell):
        return any(self.writes(x, cell) for x in b.ops[i:j])

    def clear(self, o, cell):
        """Whether every use of load ``o``'s value sees ``cell`` as ``o`` does."""
        f = self.f
        home, k = f.where[id(o)]
        dirty = {home.name: self._clobbers(home, k + 1, len(home.ops), cell)}
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
                    dirty[s] = entry[s] or self._clobbers(blk, 0, len(blk.ops), cell)
                    work.append(blk)
        for b, i in self.points.get(o.res[0], ()):
            if b is home:
                if i <= k or self._clobbers(home, k + 1, i, cell):
                    return False
            elif entry.get(b.name, True) or self._clobbers(b, 0, i, cell):
                return False
        return True

    def loads(self):
        """The values of the loads to recompute at each use."""
        out = set()
        for b in self.blocks:
            for o in b.ops[:-1]:
                v = o.res[0] if len(o.res) == 1 else None
                if v is None or v in self.c.inline or (self.c.uses.get(v) or 0) < 2:
                    continue
                if self.f.carry(v):
                    continue
                cell = self.cell(o)
                if cell is not None and self.clear(o, cell):
                    out.add(v)
        return out
