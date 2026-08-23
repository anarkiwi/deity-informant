"""Equality saturation over the S6 expression layer (experimental; ``--eqsat``).

:func:`~.idioms.fold`, :func:`~.texture.zerocarry` and
:func:`~.texture.propagate` through an e-graph over :mod:`.eqrules`, gated by
what :mod:`.ranges` proves; extraction is by printed-token cost, byte-stably.
"""

from __future__ import annotations

import json
from egglog import EGraph, i64, set_, union

from .eqrules import E, ETYPE, MARK, RULES, WEIGHT, hi, lo, pick
from .gated import diamonds
from .ir import Assert, Bin, Call, Const, Goto, If, Let, Load, MASK, Return, Store, Switch, Var
from .irwalk import defs_of, single_defs
from .ranges import cell_ranges, expr_range


class _Graph:
    """One procedure's expressions as an e-graph, and the way back out."""

    def __init__(self, copies, mem=None, defs=None):
        self.eg = EGraph()
        self.copies, self.mem, self.defs = copies, mem, defs
        self.pay, self.key, self.memo, self.asked = [], {}, {}, []
        self.nodes, self.lit, self.best, self.at = {}, {}, {}, {}

    def _payload(self, e):
        k = self.key.get(e)
        if k is None:
            k = self.key[e] = len(self.pay)
            self.pay.append(e)
        return k

    def lower(self, e):
        hit = self.memo.get(e)
        if hit is not None:
            return hit
        hit = self.memo[e] = self._build(e)
        if self.mem is not None:
            a, b = expr_range(e, self.mem, self.defs, frozenset())
            if b < MASK[getattr(e, "w", 2)]:
                self.eg.register(set_(hi(hit)).to(i64(int(b))))
            if a > 0:
                self.eg.register(set_(lo(hit)).to(i64(int(a))))
        return hit

    def _build(self, e):
        t = type(e)
        if t is Const:
            return E.k(e.v, MASK[e.w])
        if t is Var:
            return (E.cv if e.n in self.copies else E.v)(e.n, MASK[e.w])
        if t is Load:
            return E.ld(self._payload(e), MASK[e.w], self.lower(e.a))
        if t is Bin:
            return E.op(e.op, MASK[e.w], self.lower(e.a), self.lower(e.b))
        return E.blob(self._payload(e), MASK[getattr(e, "w", 2)])

    def root(self, e):
        """Register ``e`` and return the handle its class answers to."""
        return self.root_of(self.lower(e))

    def root_of(self, term):
        i = len(self.asked)
        self.asked.append(term)
        self.eg.register(union(pick(i)).with_(term))
        return i

    def copy_of(self, name, w, e):
        self.eg.register(union(E.cv(name, MASK[w])).with_(self.lower(e)))

    def run(self, rules):
        """Saturate, then read the e-graph out (``_serialize`` is egglog's only dump)."""
        self.eg.run(rules.saturate())
        assert not self.eg.run(rules).updated, "eqsat did not saturate"
        doc = json.loads(self.eg._serialize().to_json())
        self.nodes = doc["nodes"]
        self.lit = {
            n: _lit(d["op"])
            for n, d in self.nodes.items()
            if not d["children"] and doc["class_data"].get(d["eclass"], {}).get("type") != ETYPE
        }
        self.best = _cheapest(self.nodes, self.lit)
        self.at = {
            self.lit[d["children"][0]]: d["eclass"]
            for d in self.nodes.values()
            if d["op"] == "pick"
        }

    def out(self, i):
        """The IR node the ``i``-th expression extracts to, or ``None``."""
        cls = self.at.get(i)
        return None if cls is None else self._lift(cls, {})

    def _lift(self, cls, memo):
        hit = memo.get(cls)
        if hit is not None or cls in memo:
            return hit
        best = self.best.get(cls)
        node = self.nodes[best[2]] if best is not None else None
        memo[cls] = None
        if node is None:
            return None
        args = [
            self.lit[c] if c in self.lit else self._lift(self.nodes[c]["eclass"], memo)
            for c in node["children"]
        ]
        if any(z is None for z in args):
            return None
        memo[cls] = out = _node(node["op"], args, self.pay)
        return out


def _lit(text):
    """One serialized primitive: an ``i64`` or a quoted ``String``."""
    return text[1:-1] if text[:1] == '"' else int(text)


def _node(op, args, pay):
    """The IR node one extracted e-node denotes, or ``None`` for a marker."""
    if op == "E.k":
        return Const(args[0], 1 if args[1] <= 0xFF else 2)
    if op in ("E.v", "E.cv"):
        return Var(args[0], 1 if args[1] <= 0xFF else 2)
    if op == "E.ld":
        src = pay[args[0]]
        return src if args[2] == src.a else Load(src.cls, args[2], src.w, src.lo, src.hi, src.r)
    if op == "E.op":
        return Bin(args[0], args[2], args[3], 1 if args[1] <= 0xFF else 2)
    return pay[args[0]] if op == "E.blob" else None


def _cheapest(nodes, lit):
    """``{e-class: (marks, tokens, node id)}`` under the pass's total order."""
    use = sorted(n for n, d in nodes.items() if d["op"] in WEIGHT or d["op"] in MARK)
    best, keys, changed = {}, {}, True
    while changed:
        changed = False
        for n in use:
            d = nodes[n]
            marks, toks, key = MARK.get(d["op"], 0), WEIGHT.get(d["op"], 1), [d["op"]]
            for c in d["children"]:
                if c in lit:
                    key.append(lit[c])
                    continue
                hit = best.get(nodes[c]["eclass"])
                if hit is None:
                    break
                marks, toks = marks + hit[0], toks + hit[1]
                key.append(keys[nodes[c]["eclass"]])
            else:
                cls, k = d["eclass"], tuple(key)
                cur = best.get(cls)
                if cur is None or (marks, toks, k) < (cur[0], cur[1], keys[cls]):
                    best[cls], keys[cls], changed = (marks, toks, n), k, True
    return best


def _edit(node, fn):
    """Apply ``fn`` to each expression a statement or terminator evaluates."""
    t = type(node)
    if t is Let or t is Assert or t is Switch:
        node.e = fn(node.e)
    elif t is Store:
        node.a, node.v = fn(node.a), fn(node.v)
    elif t is Call:
        node.args = tuple(fn(z) for z in node.args)
    elif t is If:
        node.c = fn(node.c)
    elif t is Return:
        node.vals = tuple(fn(z) for z in node.vals)


def _noop(s):
    """A store of a cell's own value straight back to it: only I/O could notice."""
    if type(s) is not Store or s.cls != "ram" or type(s.v) is not Load or s.v.cls != s.cls:
        return False
    return s.v.a == s.a and (s.v.w, s.v.lo, s.v.hi, s.v.r) == (s.w, s.lo, s.hi, s.r)


def _forwarded(proc):
    """The names :func:`~.texture.propagate` forwards: a lone constant or copy."""
    defs = {}
    for b in proc.blocks.values():
        for s in b.stmts:
            for name in defs_of(s):
                defs.setdefault(name, []).append(s.e if type(s) is Let else None)
    one = {k: v[0] for k, v in defs.items() if len(v) == 1 and v[0] is not None}
    return {
        k: e
        for k, e in one.items()
        if type(e) is Const or (type(e) is Var and e.n in one and type(one[e.n]) is not Load)
    }


def saturate(prog, gated=True):
    """Fold, propagate and if-convert every procedure of ``prog`` to saturation.

    Stands in for :func:`~.texture.zerocarry`, :func:`~.idioms.fold` and
    :func:`~.texture.propagate`; ``gated`` seeds the rules with what
    :func:`cell_ranges` proves. Returns the number of expressions changed.
    """
    mem = cell_ranges(prog) if gated else None
    return sum(_proc(p, mem) for p in prog.procs.values())


def saturate_proc(proc, mem):
    """One procedure, with the gated rules under the program's cell ranges."""
    return _proc(proc, mem)


def _proc(proc, mem):
    sub = _forwarded(proc)
    g = _Graph(set(sub), mem, single_defs(proc) if mem is not None else None)
    nodes = [s for b in proc.blocks.values() for s in list(b.stmts) + [b.term]]
    roots = {}
    for node in nodes:
        _edit(node, lambda e: (roots.setdefault(e, g.root(e)), e)[1])
    for name, e in sorted(sub.items()):
        g.copy_of(name, e.w, e)
    picks = diamonds(proc) if mem is not None else []
    sels = [
        g.root_of(
            E.sel(
                g.lower(proc.blocks[lbl].term.c),
                g.lower(proc.blocks[t].stmts[0].e),
                g.lower(proc.blocks[f].stmts[0].e),
            )
        )
        for lbl, t, f, _join, _name in picks
    ]
    g.run(RULES)
    table = {}
    for e, i in roots.items():
        got = g.out(i)
        table[e] = e if got is None else got
    hits = [0]

    def fn(e):
        got = table.get(e, e)
        hits[0] += got != e
        return got

    for node in nodes:
        _edit(node, fn)
    for b in proc.blocks.values():
        keep = [s for s in b.stmts if not _noop(s)]
        hits[0] += len(b.stmts) - len(keep)
        b.stmts = keep
        if type(b.term) is If and type(b.term.c) is Const:
            b.term = Goto(b.term.t if b.term.c.v else b.term.f)
            hits[0] += 1
    for (lbl, t, f, join, name), i in zip(picks, sels):
        got = g.out(i)
        if got is None:
            continue
        proc.blocks[lbl].stmts.append(Let(name, got))
        proc.blocks[lbl].term = Goto(join)
        del proc.blocks[t], proc.blocks[f]
        hits[0] += 1
    return hits[0]
