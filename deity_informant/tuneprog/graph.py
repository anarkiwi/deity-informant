"""The control-flow graph of one procedure: predecessors, dominators, natural loops.

One implementation for the passes that need a graph -- SSA's dominance frontiers,
the structurer, the inliner's loop latches, outlining and tail promotion.
"""

from __future__ import annotations

import networkx as nx

from .ir import Return, succs

EXIT = "$exit"


def edges_of(proc, shut=()):
    """``(label, successor)`` pairs; an edge out of ``shut`` into covered code is cut.

    ``shut`` are the blocks only a statically closed path reaches: they take the
    edges a branch offers them and give none back, so they shape nothing.
    """
    for lbl, b in proc.blocks.items():
        for s in succs(b.term):
            if lbl not in shut or s in shut:
                yield lbl, s


def preds_of(proc, shut=()):
    """``{label: predecessor labels}``, in the order the edges were built."""
    preds = {lbl: [] for lbl in proc.blocks}
    for lbl, s in edges_of(proc, shut):
        preds[s].append(lbl)
    return preds


def cfg(proc, exit_label=None, shut=()):
    """The control-flow graph; with ``exit_label`` every ``return`` also edges to it."""
    g = nx.DiGraph()
    g.add_nodes_from(proc.blocks)
    if exit_label is not None:
        g.add_node(exit_label)
    g.add_edges_from(edges_of(proc, shut))
    if exit_label is not None:
        g.add_edges_from(
            (lbl, exit_label) for lbl, b in proc.blocks.items() if type(b.term) is Return
        )
    return g


def idoms(proc, g=None):
    """Immediate dominators of every block, from the entry."""
    return nx.immediate_dominators(cfg(proc) if g is None else g, proc.entry)


def postdoms(g, proc, exit_label=EXIT):
    """Immediate post-dominators through a virtual exit; a trap never reaches it."""
    r = nx.DiGraph()
    r.add_nodes_from(g)
    r.add_edges_from((b, a) for a, b in g.edges)
    r.add_edges_from((exit_label, n) for n in g if type(proc.blocks[n].term) is Return)
    return nx.immediate_dominators(r, exit_label) if exit_label in r else {}


def natural_loops(g, idom, preds):
    """``{header: (body labels, latch labels)}`` from the back edges."""
    loops = {}
    for u, v in g.edges:
        d = u
        while d is not None and d != v and idom.get(d) != d:
            d = idom.get(d)
        if d != v:
            continue
        body, latches = loops.setdefault(v, (set([v]), set()))
        latches.add(u)
        stack = [u]
        while stack:
            x = stack.pop()
            if x in body:
                continue
            body.add(x)
            stack.extend(preds.get(x, ()))
    return loops
