"""Block arguments computed again where their block starts, where that empties a block.

An argument every edge gives as one expression of the block's other arguments, of values
available there and of memory cells no possible write follows on the edge's path is that
expression at the block's start; it is rewritten when a predecessor is then only a branch.
"""

from __future__ import annotations

from dataclasses import replace
from itertools import product

#: Calls that write no memory of the lift: I/O, the checks, ``trap``.
QUIET = ("@io_read", "@io_write", "@codechk", "@objchk", "@trap")


def _meets(a, b):
    return a[0] <= b[1] and b[0] <= a[1]


class Joins:
    """The rewriting of the block arguments of printer function ``f`` (``printer.Func``),
    ``live`` the values something needs."""

    def __init__(self, f, live):
        self.f, self.mod, self.made, self.live = f, f.mod, 0, live
        self.preds = {b.name: {} for b in f.blocks}
        for b in f.blocks:
            for succ in b.ops[-1].succs:
                self.preds[succ][b.name] = b

    def edges(self, blk):
        """``[(predecessor, [value passed per argument])]`` of the edges into ``blk``."""
        out = []
        for p in self.preds[blk.name].values():
            t = p.ops[-1]
            out += [(p, [t.args[i] for i in idx]) for s, idx, _ in t.edges() if s == blk.name]
        return out

    def memory(self, m):
        """The memory a ``memref`` value names: its global's symbol, else the value."""
        d = self.f.defs.get(m)
        return d.prop("name") if d is not None and d.name == "memref.get_global" else m

    def writes(self, x, mem, iv):
        """Whether operation ``x`` may write a byte of interval ``iv`` of memory ``mem``."""
        if x.name == "memref.store":
            return self.memory(x.args[1]) == mem and _meets(self.f.addr_iv(x), iv)
        if x.name != "func.call" or x.prop("callee") in QUIET:
            return False
        callee = x.prop("callee")[1:]
        return callee not in self.mod.by_name or any(
            _meets(w, iv) for w in self.mod.written(callee)
        )

    def accesses(self, x, end):
        """``[(memref, address, op)]``, latest first: the load defining ``x`` and the stores of
        ``x`` no possible write of their bytes follows up to the end of block ``end``, on the
        path of blocks each its successor's one predecessor."""
        f, out, after, blk, seen = self.f, [], [], end, set()
        while blk.name not in seen:
            seen.add(blk.name)
            for o in reversed(blk.ops[:-1] if blk is end else blk.ops):
                load = o.name == "memref.load" and o.res == [x]
                if (load or o.name == "memref.store" and o.args[0] == x) and (
                    o.args[-2] in f.globals
                ):
                    iv, mem = f.addr_iv(o), self.memory(o.args[-2])
                    if not self.mod.mem.vol[iv[0] : iv[1] + 1].any() and not any(
                        self.writes(y, mem, iv) for y in after
                    ):
                        out.append((o.args[-2], o.args[-1], o))
                    if load:
                        return out
                after.append(o)
            if len(self.edges(blk)) != 1:
                return out
            blk = next(iter(self.preds[blk.name].values()))
        return out

    def available(self, v, blk):
        """Whether value ``v`` is defined in a block strictly dominating ``blk``."""
        f = self.f
        d = f.defs.get(v)
        home = f.where[id(d)][0] if d is not None else f.arg_block.get(v)
        return home is not None and home is not blk and f.dominates(home.name, blk.name)

    def match(self, xs, edges, blk, args):
        """The expression each edge of ``edges`` gives its value of ``xs`` as, of ``blk``'s
        arguments ``args`` (``{position: value}``) and values available there: ``("leaf",
        v)``, ``("op", ops, kids)`` or ``("load", ops, memref, address)``, ``ops`` per edge."""
        f, leaves = self.f, None
        for x, (_, vals) in zip(xs, edges):
            mine = {args[k] for k, v in enumerate(vals) if v == x and k in args}
            mine |= {x} if self.available(x, blk) else set()
            leaves = mine if leaves is None else leaves & mine
        if leaves:
            return ("leaf", min(leaves, key=lambda v: (v not in args.values(), v)))
        ds = [f.defs.get(x) for x in xs]
        if all(d is not None and d.name.startswith("arith.") for d in ds):
            if len({(d.name, d.props, str(d.types)) for d in ds}) != 1:
                return None
            kids = [self.match(list(a), edges, blk, args) for a in zip(*(d.args for d in ds))]
            return None if None in kids else ("op", ds, kids)
        for pick in product(*(self.accesses(x, b) for x, (b, _) in zip(xs, edges))):
            m = pick[0][0]
            if len({self.memory(n) for n, _, _ in pick}) != 1 or not self.available(m, blk):
                continue
            addr = self.match([a for _, a, _ in pick], edges, blk, args)
            if addr is not None:
                return ("load", [o for _, _, o in pick], m, addr)
        return None

    def facts(self, o):
        """What the lift states of access ``o``'s instruction (every copy's) that the
        printer reads by its address, or None for a store or no instruction."""
        if o.pc is None or o.name != "memref.load":
            return None
        fx, pcs = self.mod.fx(self.f.name), self.mod.copies_of(self.f.name, o.pc)
        per = [(fx.addr_sets.get((q, False)), fx.data.get((q, False))) for q in pcs]
        per += [(fx.reach.get(q), fx.proven.get(q)) for q in pcs]
        return per, self.mod.loaded_iv(self.f, o)

    def build(self, e, r, blk, out):
        """The value of expression ``e`` (:meth:`match`), its operations those of edge ``r``,
        appended to ``out``; a load keeps its instruction where every edge's access states
        the same of it as a load (:meth:`facts`), else it has none."""
        if e[0] == "leaf":
            return e[1]
        self.made += 1
        res, o = f"%j{self.made}", e[1][r]
        if e[0] == "op":
            args = [self.build(k, r, blk, out) for k in e[2]]
            out.append(replace(o, res=[res], args=args, parent=blk, regions=[], succs=[]))
            return res
        args = [e[2], self.build(e[3], r, blk, out)]
        t = self.f.types[o.res[0]] if o.name == "memref.load" else o.types[0][0]
        at = {}
        if self.facts(o) is None or any(self.facts(x) != self.facts(o) for x in e[1]):
            at = {"pc": None, "loc": (), "where": ""}
        types = ((o.types[0][-2], "index"), (t,))
        load = replace(o, name="memref.load", res=[res], args=args, types=types, props="")
        out.append(replace(load, attrs="", parent=blk, regions=[], succs=[], **at))
        return res

    def emptied(self, blk, edges, found):
        """``{edge index: argument positions}``: the edges from a predecessor that only
        branches once the live arguments (:attr:`live`) it computes are computed in ``blk``
        (all of them in ``found``)."""
        f, out = self.f, {}
        for r, (p, vals) in enumerate(edges):
            t, mine = p.ops[-1], {id(o) for o in p.ops[:-1]}
            if t.name != "cf.br" or p is f.blocks[0]:
                continue
            need = {
                k
                for k, v in enumerate(vals)
                if blk.args[k][0] in self.live and v in f.defs and id(f.defs[v]) in mine
            }
            gone = {v for k, v in enumerate(vals) if k in need or blk.args[k][0] not in self.live}
            dead = set()
            for o in reversed(p.ops[:-1]):
                uses = [u for x in o.res for u in f.users.get(x, []) if u is not t]
                passed = set(o.res) & set(t.args)
                if f.pure(o) and passed <= gone and all(id(u) in dead for u in uses):
                    dead.add(id(o))
            if need and need <= set(found) and len(dead) == len(mine):
                out[r] = need
        return out

    def rewrite(self, blk):
        """Rewrite the arguments of block ``blk`` whose computing there empties a
        predecessor (:meth:`emptied`); whether one was."""
        edges = self.edges(blk)
        if not edges or blk is self.f.blocks[0]:
            return False
        args = {k: n for k, (n, _) in enumerate(blk.args)}
        found = {}
        for k in args:
            rest = {j: v for j, v in args.items() if j != k and j not in found}
            e = self.match([vals[k] for _, vals in edges], edges, blk, rest)
            if e is not None:
                found[k] = e
        empty = self.emptied(blk, edges, found)
        if not empty:
            return False
        r = min(empty)
        sub, new = {}, []
        for k in sorted(set().union(*empty.values())):
            ops = []
            sub[args[k]] = self.build(found[k], r, blk, ops)
            new[0:0] = ops
        blk.ops[0:0] = new
        self.f.substitute(sub)
        self.f.reindex()
        return True


def rematerialise(f, live):
    """Rewrite the block arguments of printer function ``f`` (:class:`Joins`) given the
    values something needs (``live``, ``printer.Module.mark``); whether one was."""
    j = Joins(f, live)
    done = [j.rewrite(b) for b in f.blocks]
    return any(done)
