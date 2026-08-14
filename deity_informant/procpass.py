"""Interprocedural procedure/ownership pass over the committed model.

``plan`` builds the call graph, places a callee's body at the site that dominates
it or, where none does, a copy at every site (``Carry``), and homes every block in
the procedure whose entry dominates it considering all callers.
"""

from __future__ import annotations

_ROOT = -1  # virtual super-root of the interprocedural graph


def _dyn_targets(model, blk):
    """Resolved in-procedure successors of a computed jump (jump-table dispatch)."""
    tg = model.dyn_targets.get(blk.pcs[-1])
    if tg:
        return list(tg)
    t = blk.term
    if t[0] == "jmpind" and t[1] is not None:  # static indirect: read the vector
        m, ptr = model.mem0, t[1]
        return [m[ptr] | (m[(ptr & 0xFF00) | ((ptr + 1) & 0xFF)] << 8)]
    return []


def _succs(model, blk):
    t = blk.term
    if t[0] in ("goto", "jmp"):
        return [t[1]]
    if t[0] == "br":
        return ([t[2]] if t[2] is not None else []) + [t[3]]
    if t[0] == "jsr":
        return [(t[2] + 1) & 0xFFFF]  # a call returns; its targets are separate subs
    if t[0] in ("jmpd", "jmpind"):
        return _dyn_targets(model, blk)
    return []  # rts: procedure exit


def _postorder(entry, succ):
    order = []
    seen = {entry}
    stack = [(entry, iter(succ.get(entry, ())))]
    while stack:
        node, it = stack[-1]
        nxt = next(it, None)
        if nxt is None:
            order.append(node)
            stack.pop()
        elif nxt not in seen:
            seen.add(nxt)
            stack.append((nxt, iter(succ.get(nxt, ()))))
    return order


def _idoms(entry, succ, nodes):
    po = _postorder(entry, succ)
    rpo_num = {n: i for i, n in enumerate(reversed(po))}
    pred = {n: [] for n in nodes}
    for n in nodes:
        for s in succ.get(n, ()):
            if s in pred:
                pred[s].append(n)
    idom = {entry: entry}
    order = [n for n in reversed(po) if n != entry]
    changed = True
    while changed:
        changed = False
        for n in order:
            ps = [p for p in pred[n] if p in idom]
            if not ps:
                continue
            new = ps[0]
            for p in ps[1:]:
                a, b = new, p
                while a != b:
                    while rpo_num[a] > rpo_num[b]:
                        a = idom[a]
                    while rpo_num[b] > rpo_num[a]:
                        b = idom[b]
                new = a
            if idom.get(n) != new:
                idom[n] = new
                changed = True
    return idom, rpo_num


def copyable(model, pc):
    """A block a copy may carry on its own text: one variant, no dispatch, no call.

    A dispatch pc and a call line are each named by the statement that carries them
    (``opsw``, ``call``), so a second copy would name them twice."""
    variants = getattr(model, "all_variants", model.variants)(pc)
    if len(variants) != 1 or pc in getattr(model, "dispatch_pcs", ()):
        return False
    return model.blocks[variants[0]].term[0] != "jsr"


class Carry:
    """Which bodies a copy per call site may carry (docs/denotation-solve.md 8.4).

    A call line says nothing until its callee is judged, because inside a copy the
    callee's body is a copy in turn and the line goes with it: a body is judged
    callees-first over the call graph, and a cycle of calls is carried by none."""

    __slots__ = (
        "model",
        "intra",
        "preds",
        "landings",
        "counts",
        "calls",
        "static",
        "dyn_sites",
        "dispatch",
        "_memo",
    )

    def __init__(self, model):
        self.model = model
        graph = _graph(model)
        self.intra = graph[1]
        self.static = set(graph[3])
        self.dyn_sites = graph[4]
        self.preds = {}
        for pc, outs in self.intra.items():
            for s in outs:
                self.preds.setdefault(s, set()).add(pc)
        self.landings = set(getattr(model, "need", ()))
        for tgts in model.dyn_targets.values():
            self.landings.update(tgts)
        self.counts, self.calls = dyn_gates(model)
        self.dispatch = set(getattr(model, "dispatch_pcs", ()))
        self._memo = {}

    def body(self, target):
        """Whether every block ``target`` reaches may be carried by a copy per site."""
        got = self._memo.get(target)
        if got is None:
            self._memo[target] = False  # in progress: a recursive callee carries nothing
            self._memo[target] = got = self._body(target)
        return got

    def _body(self, target):
        if target not in self.intra:
            return False
        seen, stack = set(), [target]
        while stack:
            n = stack.pop()
            if n in seen:
                continue
            seen.add(n)
            stack.extend(s for s in self.intra.get(n, ()) if s in self.intra)
        return all(self.block(n, seen) for n in seen) and all(
            self.reached_inside(d, seen) for d in seen & self.dispatch
        )

    def reached_inside(self, pc, body):
        """Whether only ``body``'s own flow reaches ``pc``.

        A relocated dispatch keeps a label at the pc it stood at (``desmc``), which is
        one binding for however many copies carry it: a transfer from outside would
        have to pick one of them, so no copy carries a dispatch reached from outside."""
        return pc not in self.landings and not self.preds.get(pc, set()) - body

    def block(self, pc, body):
        """Whether a copy of ``body`` may carry ``pc``: each variant's transfer, and the cell.

        A declared dispatch is a ``switch`` over a state cell the copy reads like any
        other, so what a second one must not name twice is a pc, not the cell -- and a
        one-variant dispatch is a plain block leaf in a copy, which drops its ``opsw``."""
        variants = getattr(self.model, "all_variants", self.model.variants)(pc)
        if not variants or (pc in self.dispatch and len(variants) < 2):
            return False
        if len(variants) > 1 and pc not in self.dispatch:
            return False
        return all(self.term(self.model.blocks[key], body) for key in variants)

    def term(self, blk, body):
        """Whether a copy of ``body`` may carry one block variant's terminator."""
        if blk.term[0] != "jsr":
            return True
        if blk.term[1] is None:
            return self.armed(blk.pcs[-1], body)
        if self.counts.get(blk.term[1], 0):
            return False  # a callee a computed goto also lands on is that flow's
        return self.body(blk.term[1])

    def armed(self, site, body):
        """Whether a copy of ``body`` may carry the computed call at ``site``.

        Every arm is the copy's own text, so the dispatch is the statement-scoped
        ``switch goto`` and binds no arm pc program-wide -- which a site outside the
        body that left the same arm bare would still be resolving through, so every
        site dispatching over the arm has to be inside the body too."""
        tgts = set(self.model.dyn_targets.get(site, ()))
        return bool(tgts) and all(
            self.calls.get(t) == self.counts.get(t)
            and t not in self.static
            and t != getattr(self.model, "play", None)
            and self.dyn_sites.get(t, set()) <= body
            and self.body(t)
            for t in tgts
        )


def carry(model):
    """Memoized :class:`Carry` for a model (or a serialization view)."""
    c = getattr(model, "_proc_carry", None)
    if c is None:
        c = Carry(model)
        model._proc_carry = c
    return c


def dyn_gates(model):
    """``(sites, calls)``: dyn-site multiplicity per target, and the call-site half.

    A target some computed *goto* also reaches is a landing of the reaching
    procedure's own flow, so only a target every dyn site *calls* may be placed as a
    dispatch arm; where several call it, each site takes its own copy."""
    jsr = {b.pcs[-1] for b in model.blocks.values() if b.term[0] == "jsr" and b.term[1] is None}
    sites, calls = {}, {}
    for site, tgts in model.dyn_targets.items():
        for t in set(tgts):
            sites[t] = sites.get(t, 0) + 1
            if site in jsr:
                calls[t] = calls.get(t, 0) + 1
    return sites, calls


class Plan:
    """``entries``: proc entry pcs (play first, then ascending); ``inline``:
    static callee -> the call-site pcs its body is placed at; ``sole``: the callees
    of those their one site dominates, which own their body; ``homes``: block pc
    -> proc entry."""

    __slots__ = ("entries", "inline", "sole", "homes")

    def __init__(self, entries, inline, sole, homes):
        self.entries = entries
        self.inline = inline
        self.sole = sole
        self.homes = homes


def _graph(model):
    """Interprocedural pc graph: intra successors plus call/evidence targets,
    with intra-only edges, static/dyn call-site maps, targets, extra roots."""
    ev = getattr(model, "ev_targets", {}) or {}
    pcs = {key[0] for key in model.blocks}
    succ = {pc: [] for pc in sorted(pcs)}
    intra = {pc: set() for pc in pcs}
    have = {pc: set() for pc in pcs}
    rets = {(b.term[2] + 1) & 0xFFFF for b in model.blocks.values() if b.term[0] == "jsr"}
    roots = []
    static_sites = {}
    dyn_sites = {}
    targets = set()
    for key in sorted(model.blocks):
        blk = model.blocks[key]
        pc = key[0]
        t = blk.term
        site = blk.pcs[-1]
        outs = list(_succs(model, blk))
        if t[0] == "jsr":
            intra[pc].update(outs)
            if t[1] is not None:
                targets.add(t[1])
                static_sites.setdefault(t[1], set()).add(pc)
                outs.append(t[1])
            else:
                dyn = set(model.dyn_targets.get(site, ()))
                targets.update(dyn)
                for d in dyn:
                    dyn_sites.setdefault(d, set()).add(pc)
                outs.extend(sorted(dyn))
        else:
            if t[0] == "br" and t[5] is not None:
                outs.extend(sorted(model.dyn_targets.get(site, ())))
            elif t[0] == "rts":
                roots.extend(sorted(set(ev.get(site, ())) - rets))
            intra[pc].update(outs)
        for s in outs:
            if s in pcs and s not in have[pc]:
                have[pc].add(s)
                succ[pc].append(s)
    return succ, intra, roots, static_sites, dyn_sites, targets


def _plan(model):
    play = model.play
    succ, intra, roots, static_sites, dyn_sites, targets = _graph(model)
    pcs = set(succ)
    gsucc = dict(succ)
    gsucc[_ROOT] = [p for p in dict.fromkeys([play] + roots) if p in pcs]
    idom, _rpo = _idoms(_ROOT, gsucc, [_ROOT] + sorted(pcs))
    flown = set()  # pcs with a reachable intra in-edge: plain flow, never a proc
    for p in idom:
        if p != _ROOT:
            flown.update(intra[p])
    count, calls = dyn_gates(model)  # mirrors render._dispatch_gates
    inline = {}
    sole = set()  # callees their one call site dominates: that site owns the body
    parent = {}  # nested entry -> call-site pc whose proc owns its body
    procs = set()

    copies_ok = carry(model).body

    for t in sorted(targets):
        if t == play or t not in pcs:
            continue
        ss = static_sites.get(t, ())
        if len(ss) == 1 and count.get(t, 0) == 0 and idom.get(t) in ss:
            inline[t] = frozenset(ss)
            sole.add(t)
            parent[t] = idom[t]
        elif ss and count.get(t, 0) == 0 and copies_ok(t):
            inline[t] = frozenset(ss)  # duplication is exact where the body binds no pc
            parent[t] = min(ss)
        elif (
            t in dyn_sites
            and t not in static_sites
            and calls.get(t) == count.get(t)
            and (count[t] == 1 or copies_ok(t))
        ):
            parent[t] = min(dyn_sites[t])  # arm home: the sole site owns it, several copy it
        elif t not in flown:
            procs.add(t)
    entryset = {play} | set(parent) | procs
    memo = {}

    def home_entry(pc):
        """Nearest entry on the dominator chain of ``pc`` (itself included)."""
        chain = []
        n = pc
        e = None
        while True:
            if n in memo:
                e = memo[n]
                break
            if n in entryset:
                e = n
                break
            chain.append(n)
            n = idom.get(n)
            if n is None or n == _ROOT:
                break
        for c in chain:
            memo[c] = e
        return e

    pmemo = {}

    def proc_of(e):
        """Proc entry owning ``e``'s text (inline/arm chains collapse upward)."""
        seen = []
        while e is not None and e in parent and e not in pmemo:
            seen.append(e)
            e = home_entry(parent[e])
        base = pmemo.get(e, e) if e is not None else None
        for s in seen:
            pmemo[s] = base
        return base

    homes = {}
    for pc in sorted(pcs):
        e = home_entry(pc)
        p = proc_of(e) if e is not None else None
        if p is not None:
            homes[pc] = p
    return Plan([play] + sorted(procs - {play}), inline, sole, homes)


def plan(model):
    """Memoized :class:`Plan` for a committed model (or a serialization view)."""
    p = getattr(model, "_proc_plan", None)
    if p is None:
        p = _plan(model)
        model._proc_plan = p
    return p
