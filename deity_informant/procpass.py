"""Interprocedural procedure/ownership pass over the committed model.

``plan`` builds the call graph, marks single-call-site procedures for inlining
at their call site (dominance-proven), and homes every block in the procedure
whose entry dominates it considering all callers.
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


class Plan:
    """``entries``: proc entry pcs (play first, then ascending); ``inline``:
    static callee -> its sole call-site pc; ``homes``: block pc -> proc entry."""

    __slots__ = ("entries", "inline", "homes")

    def __init__(self, entries, inline, homes):
        self.entries = entries
        self.inline = inline
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
    count = {}  # dyn-site multiplicity per target (mirrors render._dispatch_gates)
    for tgts in model.dyn_targets.values():
        for t in set(tgts):
            count[t] = count.get(t, 0) + 1
    inline = {}
    parent = {}  # nested entry -> call-site pc whose proc owns its body
    procs = set()
    for t in sorted(targets):
        if t == play or t not in pcs:
            continue
        ss = static_sites.get(t, ())
        if len(ss) == 1 and count.get(t, 0) == 0 and idom.get(t) in ss:
            inline[t] = idom[t]
            parent[t] = idom[t]
        elif count.get(t) == 1 and t not in static_sites and t in dyn_sites:
            parent[t] = min(dyn_sites[t])  # sole dyn site: switch-call arm home
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
    return Plan([play] + sorted(procs - {play}), inline, homes)


def plan(model):
    """Memoized :class:`Plan` for a committed model (or a serialization view)."""
    p = getattr(model, "_proc_plan", None)
    if p is None:
        p = _plan(model)
        model._proc_plan = p
    return p
