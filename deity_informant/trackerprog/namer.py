"""T3 -- a cell's printed name from its address, off the presentation view."""

from __future__ import annotations

from ..tuneprog.ir import Bin, Const, Load, R16, Var
from ..tuneprog.irwalk import addr_split


class Namer:
    """A cell's printed name from its address, off the presentation view."""

    def __init__(self, view, names):
        self.names = names
        self.rgn = sorted((r for r in view.storage if r.id >= 0), key=lambda r: r.base)
        self.split = {
            d["split"]: (g, d) for g, d in names.groups.items() if d.get("split") is not None
        }

    def region(self, addr):
        """The region holding ``addr``: a state cell before a table that overlaps it."""
        hits = [r for r in self.rgn if r.base <= addr < r.base + r.size]
        return min(hits, key=lambda r: (r.kind != "state", r.size), default=None)

    def role(self, addr):
        r = self.region(addr)
        return None if r is None else self.names.role.get(r.id)

    def cell(self, addr):
        r = self.region(addr)
        if r is None:
            return "$%04X" % addr
        off = addr - r.base
        if r.id in self.split:
            g, d = self.split[r.id]
            stride, n = max(int(d["stride"]), 1), int(d["n"])
            fields = {int(k): f for k, f in d["fields"].items()}
            f = max((k for k in fields if k <= off and (off - k) // stride < n), default=None)
            if f is not None:
                return "%s[%d].%s" % (g, (off - f) // stride, fields[f])
        hit = self.names.view.get(r.id)
        if hit is not None:
            g, field = hit
            grp = self.names.groups.get(g) or {}
            k = off // max(int(grp.get("stride", 1)), 1)
            return "%s[%d].%s" % (g, k, field) if int(grp.get("n", 1)) > 1 else "%s.%s" % (g, field)
        name = self.names.of(r.id)
        return name if r.size == 1 or off == 0 else "%s[%d]" % (name, off)

    def expr(self, e):
        """A view expression, compactly."""
        t = type(e)
        if t is Const:
            return "$%X" % e.v if e.v > 9 else str(e.v)
        if t is Var:
            return e.n
        if t is Bin:
            return "(%s %s %s)" % (self.expr(e.a), e.op, self.expr(e.b))
        if t is R16:
            return self.names.of(e.lo[0])
        if t is Load:
            base, idx = addr_split(e.a)
            if base is not None and idx is None:
                return self.cell(base)
            r = self.rgn and next((r for r in self.rgn if r.id == e.r), None)
            name = self.names.of(e.r) if r is not None else "mem"
            return "%s[%s]" % (name, self.expr(e.a))
        return repr(e)


def by_name(view, names):
    """``{name: region}`` over the view's regions."""
    return {names.of(r.id): r for r in view.storage if r.id >= 0}
