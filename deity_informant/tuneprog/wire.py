"""The procedure interface: params, rets and call arguments, by liveness over the call graph.

Registers and flags are procedure-local values, so a procedure takes its live-in
registers as ``params`` and returns the ones it or a callee defines. A call site
reads its callee's, and a callee may be its own caller, so the answer is a fixpoint.
"""

from __future__ import annotations

import heapq

from .ir import Call, Let, REGIDX, REGVAR, Return, Var
from .irwalk import callees
from .machine import Refusal
from .ssa import liveness


def _isreg(s):
    return type(s) is Let and s.n in REGIDX


def wire_one(procs, n):
    """Re-derive one procedure's rets, call sites and params; True when they moved."""
    p = procs[n]
    was = (p.params, p.rets)
    rets = {REGIDX[s.n] for b in p.blocks.values() for s in b.stmts if _isreg(s)}
    for c in callees(p):
        rets |= set(procs[c].rets)
    p.rets = tuple(sorted(rets))
    vals = tuple(Var(REGVAR[i]) for i in p.rets)
    for b in p.blocks.values():
        if type(b.term) is Return:
            b.term = Return(vals)
        for s in b.stmts:
            if type(s) is Call:
                q = procs[s.proc]
                s.args = tuple(Var(REGVAR[i]) for i in q.params)
                s.rets = tuple(REGVAR[i] for i in q.rets)
    live = liveness(p)[p.entry]
    if not live <= set(REGIDX):
        raise Refusal("copy index", "%s live at %s: %s" % (p.name, p.entry, sorted(live)))
    p.params = tuple(sorted({REGIDX[n] for n in live} | set(p.rets)))
    return (p.params, p.rets) != was


def wire(procs):
    """Fill in params/rets/args by liveness over the call graph, to a fixpoint.

    A *recursive* procedure's params are an input to its own call sites, which no
    post-order pass settles. ``params`` can shrink (a callee's new ``rets`` kill
    liveness at the call), so termination rests on ``rets``: monotone, bounded by 16.
    """
    order, seen = [], set()

    def visit(n):
        if n in seen:
            return
        seen.add(n)
        for c in callees(procs[n]):
            visit(c)
        order.append(n)

    for n in procs:
        visit(n)
    rank = {n: i for i, n in enumerate(order)}
    callers = {n: set() for n in procs}
    for n in procs:
        for c in callees(procs[n]):
            callers[c].add(n)
    heap, queued = list(range(len(order))), set(order)
    while heap:  # callees first; a caller re-queued when its callee moves
        n = order[heapq.heappop(heap)]
        queued.discard(n)
        if wire_one(procs, n):
            for c in callers[n] - queued:
                queued.add(c)
                heapq.heappush(heap, rank[c])
    return procs
