"""T3 -- the fetch region: the blocks of the certified program that *are* the score.

A tune's score is read by a bounded piece of its tick -- the blocks that load the
order, pointer and pattern tables T2 named, the innermost loops around them, and
every block a byte those loads produced decides. Its cursors are the cells no
block outside it touches. The region is single-entry, single-exit; what it
computes for one entry is one *fetch* (:mod:`.player` records it as data), and
the rest of the tick is the player's producer program.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..tuneprog.accguard import _domsets
from ..tuneprog.graph import EXIT, cfg, idoms, natural_loops, postdoms, preds_of
from ..tuneprog.ir import Call, If, Let, Load, Phi, Return, Store, Switch, Trap, Var, succs
from ..tuneprog.irwalk import addr_split, node_exprs, reachable, walk
from .refuse import Refusal


@dataclass
class Region:
    """One proc's fetch region: its blocks, entry, exit, cursor cells and live-out temps."""

    proc: str
    blocks: frozenset
    entry: str
    exit: str
    exits: frozenset = frozenset()
    cells: frozenset = frozenset()
    liveout: tuple = ()
    callees: frozenset = frozenset()


@dataclass
class Fetch:
    """Everything one proc's tick does with its score: the regions, keyed by entry."""

    regions: dict = field(default_factory=dict)
    pcs: frozenset = frozenset()
    tables: tuple = ()

    def of(self, proc, lbl):
        return self.regions.get((proc, lbl))


def _in_tables(x, tables):
    return any(lo <= x.lo and x.hi <= hi for lo, hi in tables)


def score_loads(proc, tables):
    """The blocks of ``proc`` reading a score table, by their loads' envelopes."""
    return {
        lbl
        for lbl, b in proc.blocks.items()
        for s in list(b.stmts) + [b.term]
        for x in (y for e in node_exprs(s) for y in walk(e))
        if type(x) is Load and _in_tables(x, tables)
    }


def _deps(proc):
    """``{name: names its definition reads}`` over ``Let`` and ``Call``; a join is fresh.

    A ``Phi`` merges a value the score produced with one it did not: the name after
    the join is the player's, and the fetch exports what it left there.
    """
    out = {}
    for b in proc.blocks.values():
        for s in b.stmts:
            t = type(s)
            if t is Let:
                out[s.n] = {x.n for x in walk(s.e) if type(x) is Var}
            elif t is Call:
                for n in s.rets:
                    out[n] = {x.n for a in s.args for x in walk(a) if type(x) is Var}
    return out


def _control(proc, g):
    """``{label: {(decider, edge index)}}``: the branch edges a block depends on."""
    ipd = postdoms(g, proc, EXIT)
    pd = _domsets(ipd, [n for n in ipd if n in proc.blocks or n == EXIT])
    out = {lbl: set() for lbl in proc.blocks}
    for d, b in proc.blocks.items():
        t = b.term
        if type(t) not in (If, Switch):
            continue
        for k, s in enumerate(succs(t)):
            if s not in pd:
                continue
            for lbl in proc.blocks:
                if lbl in pd[s] and lbl not in pd.get(d, ()):
                    out[lbl].add((d, k))
    return out


def _cells(stmt, rgn):
    """The state cells a statement touches at a constant base: ``(lo, hi)`` envelopes."""
    out = []
    for e in node_exprs(stmt):
        for x in walk(e):
            if type(x) is Load and _state(x.r, rgn) and addr_split(x.a)[0] is not None:
                out.append((x.lo, x.hi))
    if type(stmt) is Store and _state(stmt.r, rgn) and addr_split(stmt.a)[0] is not None:
        out.append((stmt.lo, stmt.hi))
    return out


def _state(rid, rgn):
    r = rgn.get(rid)
    return r is not None and r.kind == "state"


def _names(e):
    return {x.n for x in walk(e) if type(x) is Var}


def closure(prog, proc, tables):
    """The seed of one proc's fetch regions: every block that touches a score byte.

    A score byte is a score-table load or a name derived from one; a block that
    reads either in a statement or its branch is a seed, and a loop whose back edge
    a score byte decides -- the loop that walks a row -- is a seed whole.
    """
    p = prog.procs[proc]
    g = cfg(p)
    loops = natural_loops(g, idoms(p, g), preds_of(p))
    deps = _deps(p)
    tainted = set()
    for b in p.blocks.values():
        for s in b.stmts:
            reads = [x for e in node_exprs(s) for x in walk(e) if type(x) is Load]
            if any(_in_tables(x, tables) for x in reads):
                tainted |= {s.n} if type(s) is Let else set(s.rets) if type(s) is Call else set()
    while True:
        more = {n for n, ds in deps.items() if ds & tainted} - tainted
        if not more:
            break
        tainted |= more
    seed = set()
    for lbl, b in p.blocks.items():
        for s in list(b.stmts) + [b.term]:
            if type(s) in (Phi, Return) or (type(s) is Let and type(s.e) is Var):
                continue  # a join, an exit and a register copy consume nothing
            reads = [x for e in node_exprs(s) for x in walk(e)]
            if any(
                (type(x) is Load and _in_tables(x, tables)) or (type(x) is Var and x.n in tainted)
                for x in reads
            ):
                seed.add(lbl)
                break
    for h, (body, latches) in loops.items():
        if (latches | {h}) & seed:
            seed |= body
    return frozenset(seed)


def _overlap(a, b):
    return a[0] <= b[1] and b[0] <= a[1]


def _iscell(x, cells, rgn):
    return _state(x.r, rgn) and any(c[0] <= x.lo and x.hi <= c[1] for c in cells)


def _common(chains, seeds, skip=frozenset()):
    """The nearest element of one chain that every seed's chain holds."""
    first = min(seeds, key=lambda l: len(chains.get(l, ())))
    return [
        x
        for x in chains.get(first, ())
        if x not in skip and all(x in chains.get(l, ()) for l in seeds)
    ]


def _region(p, dom, pd, entry, exit_):
    """The blocks between an entry and an exit, the traps they alone reach included."""
    inner = {l for l in p.blocks if entry in dom[l] and exit_ in pd.get(l, ())} - {exit_}
    traps = {
        l for l in p.blocks if type(p.blocks[l].term) is Trap and entry in dom[l] and l != entry
    }
    return inner | traps


def _leaves(p, blocks, exit_):
    """Where the region's edges go outside it, traps aside: the exit and its side doors."""
    return {
        s
        for l in blocks
        for s in succs(p.blocks[l].term)
        if s not in blocks and s != exit_ and type(p.blocks[s].term) is not Trap
    }


def sese(p, seed, dom, pd):
    """The smallest single-entry region holding ``seed``: ``(entry, exit, blocks, exits)``.

    The exit post-dominates the seed; a side door is an edge out to a block the
    exit still post-dominates -- the fetch either rejoins at the exit or through
    the play code before it, and records which.
    """
    for entry in _common(dom, seed):
        for exit_ in _common(pd, seed, skip=seed | {entry}):
            blocks = _region(p, dom, pd, entry, exit_)
            doors = _leaves(p, blocks, exit_)
            if seed <= blocks and all(exit_ in pd.get(d, ()) for d in doors):
                return entry, exit_, frozenset(blocks), frozenset(doors | {exit_})
    return None


def clusters(p, seed):
    """Disjoint regions, one per group of seed blocks no smaller region separates."""
    g = cfg(p)
    dom = _domsets(idoms(p, g), p.blocks)
    ipd = postdoms(g, p, EXIT)
    pd = _domsets(ipd, [n for n in ipd if n in p.blocks or n == EXIT])
    groups = [frozenset([l]) for l in sorted(seed)]
    while True:
        got = [(grp, sese(p, grp, dom, pd)) for grp in groups]
        merged, used = [], set()
        for i, (grp, r) in enumerate(got):
            if i in used:
                continue
            here = grp
            for j in range(i + 1, len(got)):
                if j in used or r is None or got[j][1] is None:
                    continue
                r2 = got[j][1]
                touching = r[2] & r2[2] or r[1] == r2[0] or r2[1] == r[0]
                if touching and sese(p, here | got[j][0], dom, pd) is not None:
                    here |= got[j][0]
                    used.add(j)
            merged.append(here)
        if len(merged) == len(groups):
            return got
        groups = merged


def shape(prog, proc, seed, outside):
    """The regions around the seed blocks, each a :class:`Region` or a refusal."""
    p, rgn = prog.procs[proc], prog.by_id()
    touched = {lbl: _cells(b.term, rgn) for lbl, b in p.blocks.items()}
    for lbl, b in p.blocks.items():
        for s in b.stmts:
            touched[lbl] += _cells(s, rgn)
    out = []
    for grp, got in clusters(p, seed):
        if got is None:
            out.append(
                Refusal("score not cursor-shaped", proc, min(grp), "fetch blocks form no region")
            )
            continue
        entry, exit_, blocks, exits = got
        elsewhere = [c for lbl in p.blocks if lbl not in blocks for c in touched[lbl]]
        elsewhere += list(outside)
        cells = {
            c for lbl in blocks for c in touched[lbl] if not any(_overlap(c, o) for o in elsewhere)
        }
        defined = {s.n for l in blocks for s in p.blocks[l].stmts if type(s) in (Let, Phi)}
        defined |= {n for l in blocks for s in p.blocks[l].stmts if type(s) is Call for n in s.rets}
        used = set()
        for l, b in p.blocks.items():
            if l in blocks:
                continue
            for s in list(b.stmts) + [b.term]:
                if type(s) is Phi:
                    used |= {x.n for x in s.args.values() if type(x) is Var}
                else:
                    used |= {x.n for e in node_exprs(s) for x in walk(e) if type(x) is Var}
        callees = frozenset(s.proc for l in blocks for s in p.blocks[l].stmts if type(s) is Call)
        out.append(
            Region(
                proc,
                blocks,
                entry,
                exit_,
                exits,
                frozenset(cells),
                tuple(sorted(defined & used)),
                callees,
            )
        )
    return out


def fetch(prog, tables, procs=None):
    """``(Fetch, refusals)``: every proc's fetch regions over the score ``tables``.

    A proc called only from inside a region is fetched with its caller and gets
    no regions of its own.
    """
    tables = tuple(tables)
    procs = sorted(procs or reachable(prog, prog.meta.get("tick_proc")) or prog.procs)
    rgn = prog.by_id()
    seeds = {name: closure(prog, name, tables) for name in procs}
    seeds = {n: s for n, s in seeds.items() if s}
    out, refusals = Fetch(tables=tables), []
    for name in seeds:
        others = [
            c
            for n2 in procs
            if n2 != name
            for b in prog.procs[n2].blocks.values()
            for s in list(b.stmts) + [b.term]
            for c in _cells(s, rgn)
        ]
        for r in shape(prog, name, seeds[name], others):
            if isinstance(r, Refusal):
                refusals.append(r)
            else:
                out.regions[(name, r.entry)] = r
    inside = {(r.proc, l) for r in out.regions.values() for l in r.blocks}
    calls = {}
    for n2 in procs:
        for lbl, b in prog.procs[n2].blocks.items():
            for s in b.stmts:
                if type(s) is Call:
                    calls.setdefault(s.proc, []).append((n2, lbl))
    for key in list(out.regions):
        sites = calls.get(key[0])
        if sites and all(site in inside for site in sites):
            del out.regions[key]
    pcs = set()
    for r in out.regions.values():
        for l in r.blocks:
            for s in prog.procs[r.proc].blocks[l].stmts:
                if type(s) is Store:
                    pcs.add(s.src)
    out.pcs = frozenset(pcs)
    return out, refusals
