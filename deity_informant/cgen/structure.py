"""Goto-free structuring: DREAM over BDD reaching conditions.

DREAM is the pattern-independent structuring of Yakdan et al., "No More Gotos" (NDSS 2015).
Node statements: ``ops``, ``copy``, ``set``, ``return``, ``break``,
``continue``, ``if`` ``(f, ctx, then, else)``, ``switch`` ``(family, arms)`` and ``loop``.
"""

from __future__ import annotations

import itertools

import networkx as nx
from dd import cudd


class Conds:
    """Branch conditions as BDD variables: an ``i1`` value ``("c", v)``, or an atom
    ``("e", family, k)`` (selector ``family`` equals ``k``); a family's atoms exclude each
    other under :attr:`care`."""

    def __init__(self):
        self.bdd = cudd.BDD()
        self.bdd.configure(reordering=False)
        self.keys, self.vars, self.families = {}, {}, {}
        self.care = self.bdd.true
        self.nsyn, self.syn = 0, set()
        self.memo = {}

    def var(self, key):
        """The BDD variable of ``key``, declared at the next level on first use."""
        if key not in self.vars:
            name = f"x{len(self.vars)}"
            self.bdd.declare(name)
            new = self.bdd.var(name)
            if key[0] == "e":
                fam = self.families.setdefault(key[1], [])
                none = self.bdd.true
                for k in fam:
                    none &= ~self.vars[("e", key[1], k)]
                fam.append(key[2])
                self.care &= ~new | none
            self.vars[key], self.keys[name] = new, key
        return self.vars[key]

    def close(self):
        """Release every BDD so the manager can go."""
        self.vars, self.memo, self.care = {}, {}, None
        self.bdd = None

    def sel(self, fam, k):
        """The condition that selector ``fam`` is ``k``; ``None``: none of its atoms."""
        if k is not None:
            return self.var(("e", fam, k))
        out = self.bdd.true
        for j in self.families[fam]:
            out &= ~self.vars[("e", fam, j)]
        return out

    def synthetic(self, n):
        """A new structuring variable family, exactly one of ``0 .. n-1``."""
        fam = ("s", self.nsyn)
        self.nsyn += 1
        some = self.bdd.false
        for k in range(n):
            some |= self.var(("e", fam, k))
            self.syn.add(self.vars[("e", fam, k)].var)
        self.care &= some
        return fam

    def fixed(self, f):
        """``f`` with the structuring variables quantified out."""
        return self.bdd.exist(self.syn & f.support, f) if self.syn & f.support else f

    def implies(self, f, g):
        """Whether ``f`` implies ``g``."""
        return (f & ~g) == self.bdd.false

    def first_literal(self, f, ctx):
        """The literal of least level that ``f`` implies where ``ctx`` holds and ``ctx``
        alone does not decide, or None."""
        fc = f & ctx
        for name in sorted(f.support, key=self.bdd.level_of_var):
            v = self.bdd.var(name)
            if self.implies(ctx, v) or self.implies(ctx, ~v):
                continue
            if self.implies(fc, v):
                return v
            if self.implies(fc, ~v):
                return ~v
        return None

    def width(self, f):
        """The products of ``f``'s cover, those that differ only in which outcome of one
        selector family they test counted once (one set-membership test)."""
        keys = set()
        for cube in self.cover(f, self.care):
            sel = [k for k, p in cube if p and k[0] == "e"]
            fam = sel[0][1] if len(sel) == 1 else None
            rest = tuple((k, p) for k, p in cube if fam is None or k != sel[0])
            keys.add((fam, rest))
        return len(keys)

    def cover(self, f, ctx):
        """An irredundant sum of products (Minato-Morreale ISOP) equal to ``f`` where ``ctx``
        holds: ``[[(key, polarity), ...], ...]``, literals and cubes by level."""
        cubes = sorted(self._isop(f & ctx, f | ~ctx)[0])
        return [[(self.keys[self.bdd.var_at_level(lv)], p) for lv, p in c] for c in cubes]

    def _isop(self, lo, hi):
        bdd = self.bdd
        if lo == bdd.false:
            return (), bdd.false
        if hi == bdd.true:
            return ((),), bdd.true
        if (lo, hi) in self.memo:
            return self.memo[(lo, hi)]
        level = min(lo.level, hi.level)
        l0, l1 = _cof(lo, level)
        h0, h1 = _cof(hi, level)
        c0, f0 = self._isop(l0 & ~h1, h0)
        c1, f1 = self._isop(l1 & ~h0, h1)
        cs, fs = self._isop((l0 & ~f0) | (l1 & ~f1), h0 & h1)
        v = bdd.var(bdd.var_at_level(level))
        cubes = tuple(((level, False),) + c for c in c0)
        cubes += tuple(((level, True),) + c for c in c1) + cs
        out = cubes, (~v & f0) | (v & f1) | fs
        self.memo[(lo, hi)] = out
        return out


def _cof(f, level):
    """The cofactors of ``f`` at ``level``."""
    if f.level != level:
        return f, f
    lo, hi = f.low, f.high
    return (~lo, ~hi) if f.negated else (lo, hi)


class Graph:
    """Nodes of statements, edges ``[target, condition]`` (None: unconditional) and an
    entry."""

    def __init__(self, conds, ids):
        self.conds, self.ids = conds, ids
        self.items, self.succ = {}, {}
        self.entry = None

    def add(self, items, succ=()):
        """A new node."""
        n = next(self.ids)
        self.items[n], self.succ[n] = list(items), [list(e) for e in succ]
        return n

    def cond(self, c):
        """Edge condition ``c`` as a BDD."""
        return self.conds.bdd.true if c is None else c

    def preds(self):
        """``{node: [predecessor, ...]}``, each once."""
        out = {n: [] for n in self.items}
        for n, es in self.succ.items():
            for t, _ in es:
                if n not in out[t]:
                    out[t].append(n)
        return out

    def dfs(self, entry=None, within=None):
        """``(preorder, postorder, back edges)`` from ``entry`` in edge order."""
        entry = self.entry if entry is None else entry
        pre, post, back, state = [entry], [], set(), {entry: 1}
        stack = [(entry, iter(self.succ[entry]))]
        while stack:
            n, it = stack[-1]
            for t, _ in it:
                if within is not None and t not in within:
                    continue
                s = state.get(t)
                if s is None:
                    state[t] = 1
                    pre.append(t)
                    stack.append((t, iter(self.succ[t])))
                    break
                if s == 1:
                    back.add((n, t))
            else:
                stack.pop()
                state[n] = 2
                post.append(n)
        return pre, post, back

    def dominators(self, pre):
        """``{node: [child, ...]}`` of the dominator tree, children in preorder ``pre``."""
        g = nx.DiGraph()
        g.add_node(self.entry)
        g.add_edges_from((n, t) for n, es in self.succ.items() for t, _ in es)
        rank = {n: i for i, n in enumerate(pre)}
        kids = {n: [] for n in pre}
        idom = nx.immediate_dominators(g, self.entry)
        for n in pre:
            if idom.get(n, n) != n:
                kids[idom[n]].append(n)
        assert all(n in rank for n in idom)
        return kids

    def remove(self, nodes):
        """Drop ``nodes``."""
        for n in nodes:
            del self.items[n], self.succ[n]


def subtree(kids, n, alive):
    """The nodes of ``n``'s dominator subtree still in ``alive``."""
    out, work = [], [n]
    while work:
        x = work.pop()
        if x in alive:
            out.append(x)
            work += kids.get(x, ())
    return out


def topo(g, n, region):
    """``region``'s nodes in reverse postorder from ``n``."""
    return g.dfs(n, region)[1][::-1]


class Structurer:
    """DREAM's structuring of a :class:`Graph` (see the module docstring)."""

    def __init__(self, conds, made, quiet=lambda items: False):
        self.conds, self.region, self.made, self.quiet = conds, None, made, quiet

    def structure(self, g):
        """The statements of ``g``, reduced to one node."""
        while True:
            pre, post, back = g.dfs()
            g.remove(set(g.items) - set(pre))
            if len(g.items) == 1 and not back:
                return g.items[g.entry]
            heads, size = {v for _, v in back}, len(g.items)
            kids = g.dominators(pre)
            for n in post:
                if n not in g.items:
                    continue
                if n in heads:
                    pre, _, back = g.dfs()
                    if any(v == n for _, v in back):
                        self.cyclic(g, n, back, pre)
                        size = None
                        break
                region = subtree(kids, n, g.items)
                inside = set(region)
                out = {t for x in region for t, _ in g.succ[x] if t not in inside}
                if len(out) <= 1 < len(region):
                    self.acyclic(g, n, inside, out)
            if size == len(g.items):
                raise RuntimeError("structuring made no progress")

    def acyclic(self, g, n, inside, out):
        """Replace the single-successor region ``inside`` dominated by ``n`` by its
        refined statements. A join whose reaching condition needs more products than it has
        entering edges is reached by a flag those edges set instead."""
        conds, order = self.conds, topo(g, n, inside)
        preds = {m: [] for m in order}
        for m in order:
            for t, _ in g.succ[m]:
                if t in inside:
                    preds[t].append(m)
        cr, seq = {n: conds.bdd.true}, [n]
        for m in order[1:]:
            f = conds.bdd.false
            for p in preds[m]:
                f |= cr[p] & next(g.cond(c) for t, c in g.succ[p] if t == m)
            if len(preds[m]) > 1 and conds.width(f) > len(preds[m]):
                self.made["flags"] += 1
                fam = conds.synthetic(2)
                g.items[n].insert(0, ("set", fam, 0))
                for k, p in enumerate(preds[m]):
                    e = next(e for e in g.succ[p] if e[0] == m)
                    s = g.add([("set", fam, 1)], [[m, None]])
                    cr[s], e[0], preds[s], preds[m][k] = cr[p] & g.cond(e[1]), s, [p], s
                    seq.append(s)
                f = conds.var(("e", fam, 1))
            cr[m] = f
            seq.append(m)
        self.region = (cr, preds, g.items, {m for m in seq if not g.succ[m]})
        items, _ = self.refine(seq, self.conds.care, not out)
        g.remove([m for m in seq if m != n])
        g.items[n] = items
        g.succ[n] = [[t, None] for t in out]

    def refine(self, seq, ctx, whole=False):
        """Condition-based refinement of ``seq`` (topologically ordered) where ``ctx`` holds:
        ``(statements, condition under which they jump away)``. Once a node that jumps away
        is placed, its reaching condition no longer holds; the last node of a ``whole``
        region without successor is where every path still running ends."""
        cr, preds, items, ends = self.region
        conds, out, rest, gone = self.conds, [], list(seq), self.conds.bdd.false
        while rest:
            m = rest[0]
            c = cr[m]
            if (c & ctx) == conds.bdd.false and not (whole and len(rest) == 1):
                rest.pop(0)
                continue
            if conds.implies(ctx, c) or (whole and len(rest) == 1):
                out += items[m]
                rest.pop(0)
                if m in ends:
                    gone |= ctx
                    ctx &= ~c
                continue
            lit = conds.first_literal(c, ctx)
            done = self.switch(rest, lit, ctx) if lit is not None else None
            if done is None:
                cond = c if lit is None else lit
                pos, neg, rest = take(rest, preds, (conds, ctx, cr), cond, lit is not None)
                then, g1 = self.refine(pos, ctx & cond)
                els, g2 = self.refine(neg, ctx & ~cond) if neg else ([], conds.bdd.false)
                done, jumped = ("if", cond, ctx, then, els), g1 | g2
            else:
                done, rest, jumped = done
            out.append(done)
            gone |= jumped
            ctx &= ~jumped
        return out, gone

    def switch(self, rest, lit, ctx):  # pylint: disable=too-many-locals
        """``(switch statement, remaining nodes, jumped condition)`` over the family of atom
        ``lit`` when ``rest`` falls into three or more groups of disjoint outcomes, else None."""
        cr, preds, items, _ = self.region
        conds, bdd = self.conds, self.conds.bdd
        key = conds.keys[lit.var]
        if key[0] != "e":
            return None
        fam = key[1]
        order = [*conds.families[fam], None]
        outcomes = [k for k in order if (ctx & conds.sel(fam, k)) != bdd.false]
        groups, members, live = [], set(), set(rest)
        for x in rest:
            if any(p in live and p not in members for p in preds[x]):
                continue
            f = ctx & cr[x]
            got = frozenset(o for o in outcomes if (f & conds.sel(fam, o)) != bdd.false)
            if self.quiet(items[x]) and got:
                members.add(x)
                continue
            if len(got) == len(outcomes) or not got:
                continue
            group = next((g for g in groups if g[0] & got), None)
            if group is None:
                groups.append((got, [x]))
            elif group[0] == got:
                group[1].append(x)
            else:
                continue
            members.add(x)
        if len(groups) < 3:
            return None
        arms, gone = [], bdd.false
        for got, nodes in sorted(groups, key=lambda g: min(order.index(o) for o in g[0])):
            f = bdd.false
            for o in got:
                f |= conds.sel(fam, o)
            body, jumped = self.refine(nodes, ctx & f)
            if _breaks(body):
                return None
            arms.append(([o for o in order if o in got], body))
            gone |= jumped
        return ("switch", fam, arms), [x for x in rest if x not in members], gone

    def atom(self, f):
        """``(family, k, polarity)`` when ``f`` is a literal of a selector atom, else None."""
        if f.var is None:
            return None
        v = self.conds.bdd.var(f.var)
        key = self.conds.keys[f.var]
        if f not in (v, ~v) or key[0] != "e":
            return None
        return key[1], key[2], f == v

    def finish(self, items):
        """The statements of a function: tests its enclosing tests decide dropped, then
        :meth:`chains`."""
        return self.chains(self.decide(items, self.conds.care))

    def decide(self, items, path):
        """``items`` where ``path`` holds: an ``if`` that ``path`` and its own context decide
        is replaced by the branch taken; the others print where both hold. Structuring
        variables, which statements assign, are left out of ``path``."""
        out, conds = [], self.conds
        for it in items:
            if it[0] == "if":
                f, known = it[1], path & conds.fixed(it[2])
                if conds.implies(known, f):
                    out += self.decide(it[3], path)
                elif conds.implies(known, ~f):
                    out += self.decide(it[4], path)
                else:
                    then = self.decide(it[3], known & conds.fixed(f))
                    out.append(
                        ("if", f, it[2] & path, then, self.decide(it[4], known & conds.fixed(~f)))
                    )
            elif it[0] == "switch":
                arms = []
                for cases, body in it[2]:
                    sel = conds.bdd.false
                    for k in cases:
                        sel |= conds.sel(it[1], k)
                    arms.append((cases, self.decide(body, path & conds.fixed(sel))))
                out.append(("switch", it[1], arms))
            elif it[0] == "loop":
                out.append(("loop", self.decide(it[1], path)))
            else:
                out.append(it)
        return out

    def chains(self, items):
        """``items`` with each chain of three or more ``if``/``else`` tests of one selector
        family against constants as a ``switch``."""
        out = []
        for it in items:
            if it[0] == "if":
                arms, rest, fam = [], [it], None
                while True:
                    rest = [x for x in rest if not self.quiet([x])]
                    a = self.atom(rest[0][1]) if len(rest) == 1 and rest[0][0] == "if" else None
                    if a is None or fam not in (None, a[0]):
                        break
                    fam = a[0]
                    hit, miss = (rest[0][3], rest[0][4]) if a[2] else (rest[0][4], rest[0][3])
                    arms.append(([a[1]], hit))
                    rest = miss
                arms += [([None], rest)] if rest else []
                cases = [k for c, _ in arms for k in c]
                if (
                    len(arms) >= 3
                    and len(set(cases)) == len(cases)
                    and not any(_breaks(b) for _, b in arms)
                ):
                    it = ("switch", fam, [(c, self.chains(b)) for c, b in arms])
                else:
                    it = (*it[:3], self.chains(it[3]), self.chains(it[4]))
            elif it[0] == "switch":
                it = ("switch", it[1], [(c, self.chains(b)) for c, b in it[2]])
            elif it[0] == "loop":
                it = ("loop", self.chains(it[1]))
            out.append(it)
        return out

    def cyclic(self, g, h, back, pre):
        """Replace the loop headed by ``h`` by a ``loop`` statement."""
        rank = {n: i for i, n in enumerate(pre)}
        latches = sorted((u for u, v in back if v == h), key=rank.__getitem__)
        loop = loop_nodes(g, h, latches)
        preds = g.preds()
        entries = sorted(
            (v for v in loop if v != h and any(p not in loop for p in preds[v])),
            key=rank.__getitem__,
        )
        if entries:
            h = self.multi_entry(g, h, loop, [h, *entries], latches, preds)
            preds = g.preds()
        exits = self.successors(g, h, loop, preds)
        body = Graph(self.conds, g.ids)
        body.entry = h
        for n in loop:
            body.items[n], body.succ[n] = g.items[n], []
            for t, c in g.succ[n]:
                if t == h:
                    t = body.add([("continue",)])
                elif t not in loop:
                    t = body.add([("break",)])
                body.succ[n].append([t, c])
        stmts = self.structure(body)
        g.remove(loop)
        g.items[h] = [("loop", stmts)]
        g.succ[h] = [[exits[0], None]] if exits else []
        if g.entry in loop:
            g.entry = h

    def successors(self, g, h, loop, preds):
        """Grow ``loop`` by the exits only it reaches, keeping the last exit of its body as
        the successor; more than one left are joined by a structuring variable. The exits."""

        def exits():
            out = []
            for n in topo(g, h, loop):
                out += [t for t, _ in g.succ[n] if t not in loop and t not in out]
            return out

        grow = True
        while grow:
            grow = False
            found = exits()
            for t in found:
                if all(p in loop for p in preds[t]) and (
                    _is_copy(g, t) or (len(found) > 1 and t != found[-1])
                ):
                    loop.add(t)
                    grow = True
                    break
        found = exits()
        if len(found) < 2:
            return found
        self.made["exits"] += 1
        fam = self.conds.synthetic(len(found))
        x = g.add([], [[t, self.conds.var(("e", fam, j))] for j, t in enumerate(found)])
        for n in topo(g, h, loop):
            for e in g.succ[n]:
                if e[0] in found:
                    k = g.add([("set", fam, found.index(e[0]))], [[x, None]])
                    e[0] = k
                    loop.add(k)
        return [x]

    def multi_entry(
        self, g, h, loop, entries, latches, preds
    ):  # pylint: disable=too-many-arguments
        """Make ``loop`` single-entry: a dispatcher on a structuring variable set on each
        entering edge and on the back edges to ``h``. The dispatcher."""
        self.made["entries"] += 1
        fam = self.conds.synthetic(len(entries))
        d = g.add([], [[e, self.conds.var(("e", fam, j))] for j, e in enumerate(entries)])
        for j, e in enumerate(entries):
            for p in preds[e]:
                if p in loop and not (e == h and p in latches):
                    continue
                k = g.add([("set", fam, j)], [[d, None]])
                for edge in g.succ[p]:
                    if edge[0] == e:
                        edge[0] = k
                if p in loop:
                    loop.add(k)
        loop.add(d)
        return d


def take(rest, preds, env, cond, both):
    """``(nodes implying cond, nodes implying not cond when ``both``, the others)`` of
    ``rest``; a node joins only when its predecessors in ``rest`` have joined."""
    conds, ctx, cr = env
    pos, neg, left = [], [], []
    inset, live = set(), set(rest)
    for x in rest:
        if any(p in live and p not in inset for p in preds[x]):
            left.append(x)
            continue
        f = ctx & cr[x]
        if conds.implies(f, cond):
            pos.append(x)
        elif both and conds.implies(f, ~cond):
            neg.append(x)
        else:
            left.append(x)
            continue
        inset.add(x)
    return pos, neg, left


def _breaks(items):
    """Whether ``items`` hold a ``break`` of the enclosing loop."""
    for it in items:
        if it[0] == "break":
            return True
        if it[0] == "if" and (_breaks(it[3]) or _breaks(it[4])):
            return True
        if it[0] == "switch" and any(_breaks(b) for _, b in it[2]):
            return True
    return False


def _is_copy(g, n):
    """Whether node ``n`` only assigns, with one successor."""
    return len(g.succ[n]) == 1 and all(it[0] in ("copy", "set") for it in g.items[n])


def loop_nodes(g, h, latches):
    """The loop of ``h``: the nodes reachable from ``h`` that reach a latch without ``h``."""
    fwd, work = {h}, [h]
    while work:
        for t, _ in g.succ[work.pop()]:
            if t not in fwd:
                fwd.add(t)
                work.append(t)
    preds = g.preds()
    loop, work = {h}, [u for u in latches if u != h]
    loop.update(work)
    while work:
        for p in preds[work.pop()]:
            if p not in loop and p in fwd:
                loop.add(p)
                work.append(p)
    return loop


def new_ids():
    """A node id counter."""
    return itertools.count()
