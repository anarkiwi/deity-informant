"""Structuring over the dominator tree (Ramsey, ICFP 2022) with short-circuit conditions:
fall-through, ``if``/``else``, natural loops with ``break``/``continue``, ``return``, and a
``goto`` for every other edge; branch conditions are BDDs.

Statements: ``ops``, ``copy``, ``return``, ``break``, ``continue``, ``goto`` ``(node,
source)``, ``label`` ``(node)``, ``if`` ``(f, ctx, then, else, source)``, ``switch``
``(family, arms, source)`` and ``loop`` ``(body, head)``: ``source`` the node whose branch
it prints.
"""

from __future__ import annotations

import heapq
import itertools
from collections import Counter

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

    def implies(self, f, g):
        """Whether ``f`` implies ``g``."""
        return (f & ~g) == self.bdd.false

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
        self.entry, self.composed, self.back = None, set(), set()
        self.effects, self.selectors = {}, {}

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

    def dfs(self, entry=None, within=None, key=None):
        """``(preorder, postorder, back edges)`` from ``entry`` in edge order (by ``key`` of
        the targets when given)."""
        entry = self.entry if entry is None else entry
        pre, post, back, state = [entry], [], set(), {entry: 1}
        succ = (
            self.succ
            if key is None
            else {n: sorted(es, key=lambda e: key(e[0])) for n, es in self.succ.items()}
        )
        stack = [(entry, iter(succ[entry]))]
        while stack:
            n, it = stack[-1]
            for t, _ in it:
                if within is not None and t not in within:
                    continue
                s = state.get(t)
                if s is None:
                    state[t] = 1
                    pre.append(t)
                    stack.append((t, iter(succ[t])))
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


def subtree(kids, n):
    """The nodes of ``n``'s dominator subtree."""
    out, work = set(), [n]
    while work:
        x = work.pop()
        out.add(x)
        work += kids.get(x, ())
    return out


def _ret_key(g, t):
    """What node ``t`` does when it only returns (the values it returns) or only copies into
    one successor (the copies and the successor), else ``t``."""
    items, succ = g.items[t], g.succ[t]
    if len(items) != 1:
        return t
    if not succ and items[0][0] == "return":
        return ("return", tuple(v[0] if isinstance(v, tuple) else v for v in items[0][1]))
    if items[0][0] == "copy" and len(succ) == 1 and succ[0][1] is None:
        return ("copy", tuple((d, v) for d, v, _ in items[0][1]), succ[0][0])
    return t


def prune(g):
    """Drop the edges no execution takes: an edge whose condition contradicts every path to
    its branch (a test an earlier test of the same values decides), the path conditions taken
    over forward edges from the entry and from each loop head, where they restart."""
    _, post, back = g.dfs()
    heads = {t for _, t in back}
    conds, path = g.conds, {}
    for n in reversed(post):
        p = conds.bdd.true if n == g.entry or n in heads else path.get(n, conds.bdd.false)
        keep = []
        for t, c in g.succ[n]:
            e = p & g.cond(c)
            if (n, t) in back or (e & conds.care) != conds.bdd.false:
                keep.append([t, c])
                if (n, t) not in back:
                    path[t] = path.get(t, conds.bdd.false) | e
        if len(keep) == 1:
            keep[0][1] = None
        g.succ[n] = keep
    g.remove(set(g.items) - set(g.dfs()[0]))


def short_circuit(g, hoistable, exprable=lambda items: False):
    """Compose each conditional branch ``b`` entered only from ``a`` that shares a target with
    ``a``'s other edges into ``a``: an edge of ``b`` becomes an edge of ``a`` under both tests
    (``a && b``, ``a || b``), joined with ``a``'s edge to the same target (two edges to returns
    of the same values are one target). ``b``'s operations must be ``hoistable`` (they may run
    before ``a``'s test), or ``exprable`` effects of ``b``'s one test literal (:func:`_verify`)."""
    changed = True
    while changed:
        changed = False
        preds = g.preds()
        for a in sorted(g.items):
            if a not in g.items:
                continue
            others = {_ret_key(g, t) for t, _ in g.succ[a]}
            for b, cab in g.succ[a]:
                if (
                    b in (a, g.entry)
                    or preds[b] != [a]
                    or len(g.succ[b]) < 2
                    or not others & {_ret_key(g, t) for t, _ in g.succ[b]}
                ):
                    continue
                pure = all(hoistable([it]) for it in g.items[b])
                if (pure or exprable(g.items[b])) and _try(g, a, b, g.cond(cab), pure):
                    changed = True
                    break
            if changed:
                break


def _try(g, a, b, cab, pure):
    """Compose ``b`` into ``a`` unless an effect of the result fails :func:`_verify`; one
    with effects (not ``pure``) makes its statements the effects of its test literal."""
    conds = g.conds
    fx = [(its, key, cab & reached) for its, key, reached in g.effects.get(b, ())]
    if not pure:
        sup = set().union(*(g.cond(c).support for _, c in g.succ[b]))
        if len(sup) != 1:
            return False
        fx.append((list(g.items[b]), conds.keys[next(iter(sup))], cab))
    saved = [list(e) for e in g.succ[a]], list(g.items[a]), set(g.composed)
    keep = {n: (g.items[n], g.succ[n]) for n in {b, *(t for t, _ in g.succ[b])}}
    _compose(g, a, b, cab)
    if not pure:
        g.items[a] = saved[1]
    fx = list(g.effects.get(a, ())) + fx
    if not fx or _verify(g, a, fx):
        if fx:
            g.effects[a] = fx
        g.effects.pop(b, None)
        return True
    g.succ[a], g.items[a], g.composed = saved
    for n, (its, es) in keep.items():
        g.items[n], g.succ[n] = its, es
    return False


def _verify(g, a, fx):
    """Whether ``a``'s two edge conditions, printed as sums of products and evaluated left to
    right (C's ``&&``/``||``) with no don't-cares, read each effectful literal once, exactly
    where its statements ran in the machine code."""
    conds = g.conds
    if len(g.succ[a]) != 2:
        return False
    covers = [conds.cover(g.cond(c), conds.bdd.true) for _, c in g.succ[a]]
    return all(_evaluated_once(conds, cv, key, reached) for cv in covers for _, key, reached in fx)


def _evaluated_once(conds, cubes, key, reached):
    """Whether ``key`` appears once in ``cubes`` (a sum of products evaluated left to right)
    and is evaluated exactly where ``reached`` holds."""
    hits = [(i, j) for i, c in enumerate(cubes) for j, (k, _) in enumerate(c) if k == key]
    if len(hits) != 1:
        return False
    i, j = hits[0]
    at = conds.bdd.true
    for c in cubes[:i]:
        at &= ~_cube(conds, c)
    at &= _cube(conds, cubes[i][:j])
    return (at & conds.care) == (reached & conds.care)


def _cube(conds, lits):
    out = conds.bdd.true
    for k, p in lits:
        v = conds.vars[k]
        out &= v if p else ~v
    return out


def split_tails(g, tail):
    """Give each edge into a tail call its own copy, so it prints in place as a call then
    ``return``: a node whose statements ``tail`` holds (calls and copies only) that returns or
    continues unconditionally into such a node. Each copy starts with ``("site", pred)``."""
    tails, grown = set(), True
    while grown:
        grown = False
        for n, es in g.succ.items():
            ok = not es and g.items[n][-1][0] == "return" or (
                len(es) == 1 and es[0][1] is None and es[0][0] in tails
            )
            if n not in tails and n != g.entry and ok and tail(g.items[n]):
                tails.add(n)
                grown = True
    work = sorted(tails)
    while work:
        t = work.pop()
        base = [it for it in g.items[t] if it[0] != "site"]
        preds = [p for p in sorted(g.items) if any(u == t for u, _ in g.succ[p])]
        for i, p in enumerate(preds):
            n = t if i == 0 else g.add(base, g.succ[t])
            g.items[n] = [("site", p, n), *base]
            if n != t:
                g.succ[p] = [[n if u == t else u, c] for u, c in g.succ[p]]
                work += [u for u, _ in g.succ[n] if u not in work]


def select_entries(g, place):
    """Give each cycle entered at two nodes from one two-way branch ``s`` (both of its edges,
    perhaps through copies) a head choosing the entry by ``s``'s condition, saved on entry in a
    selector variable and reset on the retreating edges to the entry they take (Ghouls' sweeps
    entered by a self-modified direction). ``g.selectors``: ``{head: (false, true) entries}``."""
    nx_g = nx.DiGraph([(n, t) for n, es in g.succ.items() for t, _ in es])
    nx_g.add_nodes_from(g.items)
    preds = g.preds()
    for scc in sorted(nx.strongly_connected_components(nx_g), key=min):
        ins = {(p, t) for t in scc for p in nx_g.predecessors(t) if p not in scc}
        sides = set()
        for p, _ in ins:
            while _copies_only(g, p) and len(preds[p]) == 1:
                p = preds[p][0]
            sides.add(p)
        if len({t for _, t in ins}) != 2 or len(sides) != 1 or g.entry in scc:
            continue
        (s,) = sides
        if len(g.succ[s]) != 2:
            continue
        (lo, _), (hi, chi) = sorted(g.succ[s], key=lambda e: place(e[0]))
        _, _, back = g.dfs(s, within=scc | {lo, hi}, key=place)
        back = {(n, t) for n, t in back if n in scc}
        if any(t in scc and t not in (lo, hi) and t in {u for _, u in ins} for _, t in back):
            continue
        conds = g.conds
        head = g.add([])
        var = conds.var(("v", head))
        g.succ[head] = [[hi, var], [lo, ~var]]
        g.succ[s] = [[g.add([("set", head, g.cond(chi))], [[head, None]]), None]]
        g.selectors[head] = (lo, hi)
        for n, t in sorted(back):
            if t in (lo, hi):
                val = conds.bdd.true if t == hi else conds.bdd.false
                b = g.add([("set", head, val)], [[head, None]])
                g.succ[n] = [[b if u == t else u, c] for u, c in g.succ[n]]


def _copies_only(g, n):
    """Whether node ``n`` only copies into one successor."""
    return len(g.succ[n]) == 1 and all(it[0] == "copy" for it in g.items[n])


def _compose(g, a, b, cab):
    """Move ``b`` (entered from ``a`` under ``cab``) into ``a``."""
    bdd = g.conds.bdd
    out = [[t, g.cond(c)] for t, c in g.succ[a] if t != b]
    for t, c in g.succ[b]:
        k = _ret_key(g, t)
        hit = next((e for e in out if _ret_key(g, e[0]) == k), None)
        if hit is None:
            out.append([t, cab & g.cond(c)])
        else:
            hit[1] |= cab & g.cond(c)
            if hit[0] != t:
                g.remove([t])
    g.succ[a] = [[t, None if c == bdd.true else c] for t, c in out]
    g.items[a] += g.items[b]
    g.composed.add(a)
    g.remove([b])


class Structurer:
    """A :class:`Graph` as statements, after Ramsey, "Beyond Relooper: recursive translation
    of unstructured control flow to structured control flow" (ICFP 2022): each node is
    followed by the nodes placed after it, in the layout order (:func:`_layout`, by
    ``place``): the joins it immediately dominates (two or more forward edges in), and when
    it heads a natural loop, the loop's follow. A loop's follow is the exit it dominates where
    execution continues after the loop: of the exits that do not end the procedure, the one
    all the others reach, else the one most edges leave the loop to (each edge to another
    placed exit is a ``goto``), else the first in layout order. An edge to a node placed so,
    or a back edge, is a jump, any other edge the target's code in place. A jump to the
    innermost loop's head is ``continue``, to what follows it ``break``, to what runs next
    nothing when :meth:`structured`, else a ``goto``. Conditional branches entered from a
    branch sharing one of their targets are composed first (:func:`short_circuit`), so their
    shared target is reached by one edge. ``falls(p, y)``: whether an edge is the machine
    code's fall-through; ``hoistable(items)``: whether a block may run before the branch
    leading to it; ``quiet(items)``: whether it prints nothing."""

    def __init__(self, conds, quiet=lambda items: False, hoistable=lambda items: False,
                 falls=lambda p, y: False, place=lambda n: 0,
                 tail=lambda items: False, exprable=lambda items: False):  # fmt: skip
        self.conds, self.quiet, self.hoistable, self.falls = conds, quiet, hoistable, falls
        self.place, self.tail, self.exprable = place, tail, exprable
        self.g = self.kids = self.loops = self.back = self.rank = None
        self.at, self.after, self.follows, self.idom = {}, {}, set(), {}

    def structure(self, g):
        """The statements of ``g``, with ``label`` statements where jumps go."""
        self.g = g
        prune(g)
        short_circuit(g, self.hoistable, self.exprable)
        split_tails(g, self.tail)
        select_entries(g, self.place)
        pre, _, back = g.dfs(key=self.place)
        g.remove(set(g.items) - set(pre))
        self.rank = _layout(g, pre, back, self.place)
        self.kids = g.dominators(pre)
        idom = {k: n for n, ks in self.kids.items() for k in ks}
        self.back = g.back = back
        fwd, latches = dict.fromkeys(pre, 0), {}
        for n in pre:
            for t, _ in g.succ[n]:
                if (n, t) in back:
                    latches.setdefault(t, []).append(n)
                else:
                    fwd[t] += 1
        self.loops = {
            h: loop_nodes(g, h, us)
            for h, us in latches.items()
            if all(_dominated(idom, u, h) for u in us)
        }
        self.at = {n: idom[n] for n in pre if fwd[n] > 1 or n in latches and n not in self.loops}
        self.follows = set()
        for h in sorted(self.loops, key=self.rank.__getitem__):
            f = self.follow(h, idom)
            if f is not None:
                self.at[f] = h
                self.follows.add(f)
        self.idom = idom
        self.cases = {
            t: d
            for d, es in g.succ.items()
            if len(es) > 2
            for t, _ in es
            if self.at.get(t) == d and t not in self.follows and t not in latches
        }
        for t in self.cases:
            del self.at[t]
        self.after = {}
        for y in sorted(self.at, key=self.rank.__getitem__):
            self.after.setdefault(self.at[y], []).append(y)
        return self.simplify(self.tree(g.entry), frozenset(), None)

    def follow(self, h, idom):
        """The follow of the loop headed by ``h``, or None."""
        loop, g = self.loops[h], self.g
        edges = Counter(
            t
            for n in loop
            for t, _ in g.succ[n]
            if t not in loop and _dominated(idom, t, h) and t not in self.follows
        )
        going = [t for t in edges if not self.terminal(t)]
        if not going:
            return None
        reach = {t: self.reach(t, loop) for t in going}
        return min(
            going,
            key=lambda t: (
                not all(t in reach[u] for u in going),
                -edges[t],
                self.rank[t],
            ),
        )

    def structured(self, x, t):
        """Whether the edge from ``x`` to ``t`` has a structured form where ``t`` runs next:
        the machine code's fall-through, or the end of an arm of the branch nearest ``x``
        (``x``'s own, else its nearest dominator's) whose paths rejoin at ``t``."""
        if self.falls(x, t):
            return True
        b = x
        while len(self.g.succ[b]) < 2 and b in self.idom:
            b = self.idom[b]
        return len(self.g.succ[b]) < 2 or self.rejoins(b, t)

    def rejoins(self, x, y):
        """Whether the paths from ``x`` meet at ``y`` (its post-dominator): none reaches what
        follows ``y`` without passing it, a back edge, an edge into a loop's latch (the
        loop continues) and an exit of ``x``'s innermost loop ending a path."""
        inner = min((lp for lp in self.loops.values() if x in lp), key=len, default=None)
        latches = {u for u, _ in self.back}

        def walk(work, stop):
            seen = set()
            while work:
                n = work.pop()
                if n in seen or n == stop:
                    continue
                seen.add(n)
                for t, _ in self.g.succ[n]:
                    if (n, t) not in self.back and (inner is None or t in inner):
                        if t not in latches or t == stop:
                            work.append(t)
            return seen

        return not walk([t for t, _ in self.g.succ[x]], y) & walk([y], None)

    def terminal(self, t):
        """Whether every forward edge from ``t``'s dominator subtree stays in it: its code
        ends the procedure or continues an enclosing loop."""
        sub = subtree(self.kids, t)
        return all(u in sub or (n, u) in self.back for n in sub for u, _ in self.g.succ[n])

    def reach(self, t, avoid):
        """The nodes forward edges reach from ``t`` outside ``avoid``."""
        seen, work = {t}, [t]
        while work:
            n = work.pop()
            for u, _ in self.g.succ[n]:
                if u not in seen and u not in avoid and (n, u) not in self.back:
                    seen.add(u)
                    work.append(u)
        return seen

    def tree(self, x):
        """The statements of ``x`` and the nodes placed after it."""
        ys = self.after.get(x, [])
        body = [("label", x), *self.g.items[x], *self.term(x)]
        if x not in self.loops:
            return body + [it for y in ys for it in self.tree(y)]
        inside = [it for y in ys if y in self.loops[x] for it in self.tree(y)]
        outside = [it for y in ys if y not in self.loops[x] for it in self.tree(y)]
        return [("loop", body + inside, x), *outside]

    def term(self, x):
        """``x``'s branch: an ``if`` chain over its edges' conditions, the last taken else."""
        es = self.g.succ[x]
        if not es:
            return []
        es = sorted(
            es,
            key=lambda e: (
                e[1] is not None and None in (self.atoms(e[1]) or [0, [None]])[1],
                self.place(e[0]) if len(es) > 2 else 0,
            ),
        )
        out = self.branch(x, es[-1][0])
        for t, c in reversed(es[:-1]):
            out = [("if", c, self.conds.care, self.branch(x, t), out, x)]
        return out

    def branch(self, x, t):
        """The edge from ``x`` to ``t``: a jump, or ``t``'s code in place."""
        if (x, t) in self.back:
            return [("continue", t, x) if t in self.loops else ("goto", t, x)]
        if t in self.at or self.cases.get(t, x) != x:
            return [("goto", t, x)]
        return self.tree(t)

    def simplify(self, items, nxt, loop):
        """``items`` with each jump to a label control reaches next (``nxt`` after the last)
        dropped, and as ``continue``/``break`` of the innermost loop ``loop`` ``(head, labels
        after it)`` where it is one."""
        out = []
        for i, it in enumerate(items):
            kind = it[0]
            follow = (
                _labels(items[i + 1 :], nxt) if kind in ("goto", "continue", "if", "loop") else nxt
            )
            if kind in ("goto", "continue"):
                t = it[1]
                if t in follow and (kind == "continue" or self.structured(it[2], t)):
                    continue
                if loop is not None and t == loop[0]:
                    it = ("continue",)
                elif loop is not None and t in loop[1]:
                    it = ("break",)
                else:
                    it = ("goto", t, it[2])
            elif kind == "if":
                then, els = self.simplify(it[3], follow, loop), self.simplify(it[4], follow, loop)
                it = (*it[:3], then, els, it[5])
            elif kind == "loop":
                it = ("loop", self.simplify(it[1], frozenset([it[2]]), (it[2], follow)), it[2])
            out.append(it)
        return out

    def atoms(self, f):
        """``(family, outcomes)`` when ``f`` is a selector family taking one of
        ``outcomes`` (atoms of one family, ``None`` none of them), else None."""
        conds = self.conds
        keys = {conds.keys[n] for n in f.support}
        if not keys or any(k[0] != "e" for k in keys) or len({k[1] for k in keys}) > 1:
            return None
        fam = next(iter(keys))[1]
        got, union = [], conds.bdd.false
        for k in [*conds.families[fam], None]:
            s = conds.sel(fam, k) & conds.care
            if conds.implies(s, f):
                got.append(k)
                union |= s
        return (fam, got) if union == f & conds.care else None

    def finish(self, items):
        """The statements of a function: tests its enclosing tests decide dropped, then
        :meth:`chains`, the labels no ``goto`` names dropped."""
        out = self.chains(self.decide(items, self.conds.care), frozenset())
        return _unlabel(out, set(_walk(out, lambda it: it[1] if it[0] == "goto" else None)))

    def decide(self, items, path):
        """``items`` where ``path`` holds: an ``if`` that ``path`` and its own context decide
        is replaced by the branch taken; the others print where both hold."""
        out, conds = [], self.conds
        for it in items:
            if it[0] == "if" and it[5] in self.g.effects:
                then = self.decide(it[3], path & it[1])
                els = self.decide(it[4], path & ~it[1])
                out.append(("if", it[1], conds.bdd.true, then, els, it[5]))
            elif it[0] == "if":
                f, known = it[1], path & it[2]
                if conds.implies(known, f):
                    out += self.decide(it[3], path)
                elif conds.implies(known, ~f):
                    out += self.decide(it[4], path)
                else:
                    then = self.decide(it[3], known & f)
                    els = self.decide(it[4], known & ~f)
                    out.append(("if", f, it[2] & path, then, els, it[5]))
            elif it[0] == "loop":
                out.append(("loop", self.decide(it[1], path), it[2]))
            else:
                out.append(it)
        return out

    def chains(self, items, nxt):
        """``items`` with each chain of three or more ``if``/``else`` tests of one selector
        family against sets of its constants as a ``switch``, a ``goto`` in an arm to what
        follows the switch its ``break``; ``nxt``: the labels reached after ``items``."""
        out = []
        for i, it in enumerate(items):
            follow = _labels(items[i + 1 :], nxt)
            if it[0] == "if":
                arms, rest, fam = [], [it], None
                while True:
                    rest = [x for x in rest if not self.quiet([x])]
                    head = rest[0] if len(rest) == 1 and rest[0][0] == "if" else None
                    head = None if head and head[5] in self.g.effects else head
                    hit, a = (head[3], self.atoms(head[1])) if head else (None, None)
                    if head and a is None:
                        hit, a = head[4], self.atoms(~head[1])
                    if a is None or fam not in (None, a[0]) or None in a[1]:
                        break
                    fam = a[0]
                    arms.append((a[1], hit))
                    rest = head[4] if hit is head[3] else head[3]
                arms += [([None], rest)] if rest else []
                cases = [k for c, _ in arms for k in c]
                if (
                    len(arms) >= 3
                    and len(set(cases)) == len(cases)
                    and not any(_breaks(b) for _, b in arms)
                ):
                    arms = [(c, _arm_breaks(self.chains(b, follow), follow)) for c, b in arms]
                    arms = [
                        (c, _fall_into(b, arms[j + 1][1] if j + 1 < len(arms) else []))
                        for j, (c, b) in enumerate(arms)
                    ]
                    it = ("switch", fam, arms, it[5])
                else:
                    it = (*it[:3], self.chains(it[3], follow), self.chains(it[4], follow), it[5])
            elif it[0] == "loop":
                it = ("loop", self.chains(it[1], frozenset()), it[2])
            out.append(it)
        return out


def _fall_into(items, nxt):
    """A switch arm ending with a ``goto`` to the label the next arm ``nxt`` starts with, as
    a fall-through into it."""
    if not nxt or nxt[0][0] != "label" or not items:
        return items
    last = items[-1]
    if last[0] == "goto" and last[1] == nxt[0][1]:
        return [*items[:-1], ("fall",)]
    if last[0] == "if":
        for then, els, f in ((last[3], last[4], last[1]), (last[4], last[3], ~last[1])):
            inner = _fall_into(then, nxt)
            if inner is not then and _ends(els):
                return [*items[:-1], ("if", ~f, last[2], els, [], last[5]), *inner]
    return items


def _ends(items):
    """Whether control never runs past ``items``."""
    if not items:
        return False
    last = items[-1]
    if last[0] in ("return", "halt", "goto", "continue", "break"):
        return True
    return last[0] == "if" and _ends(last[3]) and _ends(last[4])


def _arm_breaks(items, follow):
    """A switch arm's ``items`` with each ``goto`` to a label of ``follow`` (what runs after
    the switch), outside loops nested in the arm, as ``break``."""
    out = []
    for it in items:
        if it[0] == "goto" and it[1] in follow:
            it = ("break",)
        elif it[0] == "if":
            it = (*it[:3], _arm_breaks(it[3], follow), _arm_breaks(it[4], follow), it[5])
        out.append(it)
    return out


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


def _walk(items, fn):
    """``fn(statement)`` over ``items`` and the statements nested in them, yielding each
    result that is not None."""
    for it in items:
        r = fn(it)
        if r is not None:
            yield r
        if it[0] == "if":
            yield from _walk(it[3], fn)
            yield from _walk(it[4], fn)
        elif it[0] == "loop":
            yield from _walk(it[1], fn)
        elif it[0] == "switch":
            for _, b in it[2]:
                yield from _walk(b, fn)


def _unlabel(items, used):
    """``items`` without the labels not in ``used``."""
    out = []
    for it in items:
        if it[0] == "label" and it[1] not in used:
            continue
        if it[0] == "if":
            it = (*it[:3], _unlabel(it[3], used), _unlabel(it[4], used), it[5])
        elif it[0] == "loop":
            it = ("loop", _unlabel(it[1], used), it[2])
        elif it[0] == "switch":
            it = ("switch", it[1], [(c, _unlabel(b, used)) for c, b in it[2]], it[3])
        out.append(it)
    return out


def _labels(rest, nxt):
    """The labels control reaches first after a statement followed by ``rest``: those
    starting ``rest`` (or the loop it starts with), with ``nxt`` when nothing else does."""
    out = set()
    for it in rest:
        if it[0] == "loop":
            return frozenset(out) | _labels(it[1], frozenset())
        if it[0] != "label":
            return frozenset(out)
        out.add(it[1])
    return frozenset(out) | nxt


def _layout(g, pre, back, place):
    """``{node: rank}``: a topological order of the forward edges taking, of the nodes
    ready, the one ``place`` (the machine code's address) puts first."""
    deg = dict.fromkeys(pre, 0)
    for n in pre:
        for t, _ in g.succ[n]:
            if (n, t) not in back:
                deg[t] += 1
    ready = [(place(g.entry), g.entry)]
    rank = {}
    while ready:
        _, n = heapq.heappop(ready)
        rank[n] = len(rank)
        for t, _ in g.succ[n]:
            if (n, t) not in back:
                deg[t] -= 1
                if not deg[t]:
                    heapq.heappush(ready, (place(t), t))
    return rank


def _dominated(idom, n, h):
    """Whether ``h`` dominates ``n``."""
    while n != h and n in idom:
        n = idom[n]
    return n == h


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
