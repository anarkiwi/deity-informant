"""Out of SSA by coalescing (Boissinot et al., "Revisiting Out-of-SSA Translation for
Correctness, Code Quality, and Efficiency", CGO 2009): each block argument shares one C
variable with the values its incoming edges pass where their live ranges do not intersect or
they hold the same value, so the edge copies between them vanish."""

from __future__ import annotations


class Coalescer:  # pylint: disable=too-many-instance-attributes
    """The congruence classes of a printed function's SSA values (:class:`printer.CFunc`
    ``c``): values connected by block-argument copies, merged where they do not interfere."""

    def __init__(self, c):
        self.c, self.f = c, c.f
        self.blocks = [b for b in self.f.blocks if b.name in c.owned]
        self.memo, self.at, self.edge, self.uses = {}, {}, {}, {}
        for b in self.blocks:
            self._block(b)
        self.live_in, self.live_out = self._liveness()
        self.value = self._values()
        self.cls = {}

    def is_var(self, v):
        """Whether value ``v`` prints as a variable."""
        c, f = self.c, self.f
        if v in f.arg_block:
            return True
        d = f.defs.get(v)
        if d is None or v in c.inline or f.remat(v) or not c.uses.get(v):
            return False
        return id(d) not in f.dropped

    def reads(self, v):
        """The variables printing value ``v`` reads."""
        if v not in self.memo:
            d = self.f.defs.get(v)
            if self.is_var(v):
                self.memo[v] = frozenset([v])
            elif d is None or self.f.remat(v):
                self.memo[v] = frozenset()
            else:
                self.memo[v] = frozenset().union(*map(self.reads, self.f.reads(d)))
        return self.memo[v]

    def _block(self, b):
        """Record block ``b``'s per-operation variable uses and definitions and its edges'."""
        f, at = self.f, []
        for i, o in enumerate(b.ops[:-1]):
            if id(o) in f.dropped or not f.printed(o):
                continue
            res = [r for r in o.res if self.is_var(r)]
            if f.pure(o) and not res:
                continue
            used = frozenset().union(*map(self.reads, f.reads(o)))
            at.append((i, used, frozenset(res)))
        term = b.ops[-1]
        cond = [a for k, a in enumerate(term.args) if k not in _edge_args(term)]
        at.append((len(b.ops) - 1, frozenset().union(*map(self.reads, cond)), frozenset()))
        self.at[b.name] = at
        for succ, idx, _ in term.edges():
            if succ in self.c.owned:
                args = [a for a, _ in f.block_of[succ].args]
                pairs = [(a, term.args[i]) for a, i in zip(args, idx)]
                self.edge[(b.name, succ)] = pairs
                for v in (x for _, x in pairs):
                    self.uses.setdefault(b.name, set()).update(self.reads(v))

    def _liveness(self):
        """``(live in, live out)`` per block of the variables, a block argument defined at its
        block's entry and an edge's incoming values used at its source's end."""
        gen, kill = {}, {}
        for b in self.blocks:
            g, k = set(), {a for a, _ in b.args}
            for _, used, defs in self.at[b.name]:
                g |= used - k
                k |= defs
            gen[b.name], kill[b.name] = g, k
        live_in = {b.name: set() for b in self.blocks}
        live_out = {b.name: set() for b in self.blocks}
        changed = True
        while changed:
            changed = False
            for b in reversed(self.blocks):
                out = set(self.uses.get(b.name, ()))
                for (p, s), _ in self.edge.items():
                    if p == b.name:
                        out |= live_in[s] - {a for a, _ in self.f.block_of[s].args}
                new = gen[b.name] | (out - kill[b.name])
                if out != live_out[b.name] or new != live_in[b.name]:
                    live_out[b.name], live_in[b.name], changed = out, new, True
        return live_in, live_out

    def _values(self):
        """Each variable's value: a block argument every incoming edge passes one value to
        holds that value, else its own."""
        val = {}

        def of(v):
            return val.get(v, ("k", self.f.const_of(v)) if self.f.const_of(v) is not None else v)

        changed = True
        while changed:
            changed = False
            for b in self.blocks:
                for a, _ in b.args:
                    ins = {
                        of(v)
                        for (_, s), ps in self.edge.items()
                        if s == b.name
                        for x, v in ps
                        if x == a
                    }
                    new = ins.pop() if len(ins) == 1 and a not in ins else a
                    if val.get(a, a) != new:
                        val[a], changed = new, True
        return {v: of(v) for v in self.reads_all()}

    def reads_all(self):
        """Every variable."""
        out = {a for b in self.blocks for a, _ in b.args}
        for at in self.at.values():
            for _, used, defs in at:
                out |= used | defs
        return out

    def where(self, v):
        """``(block name, index)`` defining variable ``v`` (-1: a block argument)."""
        if v in self.f.arg_block:
            return self.f.arg_block[v].name, -1
        b, i = self.f.where[id(self.f.defs[v])]
        return b.name, i

    def live_after(self, x, blk, i):
        """Whether variable ``x`` is live just after position ``i`` of block ``blk``."""
        if x in self.live_out[blk] or x in self.uses.get(blk, ()):
            return True
        return any(j > i and x in used for j, used, _ in self.at[blk])

    def interfere(self, a, b):
        """Whether variables ``a`` and ``b`` hold different values while both are live: one
        is live where the other, which its definition dominates, is defined."""
        if self.value.get(a, a) == self.value.get(b, b):
            return False
        (ba, ia), (bb, ib) = self.where(a), self.where(b)
        if ba == bb:
            return self.live_after(a, bb, ib) if ia <= ib else self.live_after(b, ba, ia)
        if self.f.dominates(ba, bb):
            return self.live_after(a, bb, ib)
        if self.f.dominates(bb, ba):
            return self.live_after(b, ba, ia)
        return False

    def classes(self):
        """``{variable: representative}``: each block argument merged with the variables its
        edges pass, back edges first, where no two of the merged classes interfere and the
        variables are of one type."""
        rep = {}

        def find(v):
            while rep.get(v, v) != v:
                v = rep[v]
            return v

        members = {}
        copies = [
            (not self.f.dominates(s, p), a, v)
            for (p, s), ps in self.edge.items()
            for a, v in ps
            if self.is_var(v) and v != a
        ]
        for _, a, v in sorted(copies, key=lambda x: x[0]):
            ra, rv = find(a), find(v)
            if ra == rv or not self.same_type(a, v):
                continue
            ca, cv = members.get(ra, [ra]), members.get(rv, [rv])
            if any(self.interfere(x, y) for x in ca for y in cv):
                continue
            rep[rv] = ra
            members[ra] = ca + cv
            members.pop(rv, None)
        return {v: find(v) for v in list(rep)}

    def same_type(self, a, b):
        """Whether variables ``a`` and ``b`` print as one C type and naming."""
        f = self.f
        kind = [(f.width(x) > 8, x in f.kptrs, f.carry(x)) for x in (a, b)]
        return kind[0] == kind[1]


def _edge_args(term):
    """The operand indices of terminator ``term`` its edges pass to block arguments."""
    return {i for _, idx, _ in term.edges() for i in idx}
