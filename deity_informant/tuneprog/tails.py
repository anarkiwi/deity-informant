"""S6 -- shared tails become procedures: the routine a 6510 player enters by JMP.

A region several jumps reach and leave one way (or not at all) is the ``goto``
residue of structuring (GoatTracker's ``mt_loadregs``, JCH's write-out join).
Promoting it to a procedure each jump calls costs one call per predecessor.
"""

from __future__ import annotations

import copy

from .closure import closed_blocks
from .fold import exit_returns
from .graph import cfg, idoms, natural_loops, preds_of
from .ir import (
    Block,
    Call,
    COPYVAR,
    Goto,
    Let,
    Proc,
    REGIDX,
    REGVAR,
    Return,
    Var,
    retarget,
    retval,
    succs,
)
from .irwalk import apply_stmt, apply_term, defs_of, stmt_uses, term_uses, unique_name
from .live import needed, printable
from .ssa import prune
from .structure import Jump, structure_proc, walk

SPARE = tuple(i for i in range(16) if REGVAR[i].startswith("r"))


def promote_tails(prog, names=None, rounds=256):
    """Promote shared tails while the ``goto`` residue does not grow, best state kept.

    A promotion can leave a residue of its own, so the helper it makes is queued
    behind the procedure it came out of.
    """
    todo, seen, n = list(prog.procs), set(), 0
    while todo:
        name = todo.pop(0)
        if name in seen or name not in prog.procs:
            continue
        seen.add(name)
        was = set(prog.procs)
        n += _promote_proc(prog, name, names, rounds)
        todo.extend(sorted(set(prog.procs) - was))
    return n


def _promote_proc(prog, name, names, rounds):
    """Promote while the residue does not grow, then keep the best state seen."""
    proc = prog.procs[name]
    if retval(proc) is not None:
        return 0  # the text shows what this procedure returns: keep its exits
    cur = best = _gotos(proc)
    live = needed(prog)[0].get(name) if cur else None
    steps, mark = [], 0
    for _ in range(rounds):
        if not cur:
            break
        hit = _one(prog, name, names, cur, live)
        if hit is None:
            break
        cur, hname, undo = hit
        steps.append((hname, undo))
        if cur < best:
            best, mark = cur, len(steps)
    for hname, undo in reversed(steps[mark:]):
        _revert(prog, proc, hname, undo, names)
    return mark


def _revert(prog, proc, hname, undo, names=None):
    """Put back the blocks a promotion moved, and drop the procedure it made."""
    proc.blocks = undo[0]
    for p, term in undo[1].items():
        proc.blocks[p].term = term
    del prog.procs[hname]
    if names is not None:
        names.procs.pop(hname, None)


def _gotos(proc):
    return sum(1 for n in walk(structure_proc(proc)) if type(n) is Jump and n.kind == "goto")


def _one(prog, name, names, cur, live):
    """Promote the smallest tail that leaves the residue no worse; its undo record.

    An exit-free tail may break even, since it is what makes the next one exit
    free; a tail with a way out has to pay for the call the exit becomes.
    """
    proc = prog.procs[name]
    for kind, _size, lbl, region, out in _tails(proc):
        hit = _promote(prog, name, lbl, region, out, live)
        if hit is None:
            continue
        made, undo = hit
        now = _gotos(proc)
        if now < cur or (not kind and now == cur):
            if names is not None:
                names.procs[made] = made
            return now, made, undo
        _revert(prog, proc, made, undo)
    return None


def _tails(proc):
    """``[(has an exit, statements, label, region, exit)]`` for every promotable region.

    A closed arm is not part of the covered program's shape (:mod:`.closure`), so
    it moves with the region that holds it and neither enters nor leaves another.
    Exit-free regions come first: they are what promotion has always done.
    """
    shut = closed_blocks(proc)
    g, preds, full = cfg(proc, shut=shut), preds_of(proc, shut=shut), preds_of(proc)
    idom = idoms(proc, g)
    heads = set(natural_loops(g, idom, preds))
    dom = {}
    for n in g:
        cur = n
        while idom.get(cur, cur) != cur:
            cur = idom[cur]
            dom.setdefault(cur, set()).add(n)
    out = []
    for lbl in proc.blocks:
        region = dom.get(lbl, set()) | {lbl}
        if lbl in heads or lbl == proc.entry or len(set(full[lbl])) < 2:
            continue
        gone = {s for l in region if l not in shut for s in succs(proc.blocks[l].term)} - region
        back = {s for l in region if l in shut for s in succs(proc.blocks[l].term)} - region
        into = [l for l in region if l != lbl and any(p in shut - region for p in full[l])]
        if len(gone) > 1 or back or into:
            continue
        size = sum(len(proc.blocks[l].stmts) for l in region)
        out.append((bool(gone), size, lbl, sorted(region), gone.pop() if gone else None))
    return sorted(out)


def _crossers(proc, region):
    """The names the region reads but does not define: its parameters."""
    inside, used = set(), set()
    for lbl in region:
        b = proc.blocks[lbl]
        for s in b.stmts:
            inside.update(defs_of(s))
            stmt_uses(s, used)
        term_uses(b.term, used)
    return used - inside


def _slots(crossers):
    """``{name: parameter register}`` -- its own register, or a spare slot."""
    out, used = {}, set()
    for n in sorted(crossers):
        i = REGIDX.get(n.split("#")[0])
        if i is None or i in used:
            i = next((f for f in SPARE if f not in used), None)
        if i is None:
            return None
        out[n], used = i, used | {i}
    return out


def _defs(proc, labels):
    return {n for l in labels for s in proc.blocks[l].stmts for n in defs_of(s)}


def _leaving(proc, region, entry, out, live, params):
    """The names the region hands its exit, or ``None`` when it cannot hand them over.

    A value something after the exit reads is a return value, so the region must
    have it wherever it takes the edge to ``out``: given to it, set by its entry
    (which dominates it), or set by the block that leaves. A copy index is the
    loop's own machinery (:func:`~.loops.copies`), never a helper's work.
    """
    inside, used = _defs(proc, region), set()
    for lbl, b in proc.blocks.items():
        if lbl in region:
            continue
        for s in b.stmts:
            stmt_uses(s, used)
        term_uses(b.term, used)
    gives = sorted(inside & used & (used if live is None else set(live)))
    if any(n.startswith(COPYVAR) for n in gives):
        return None
    have = params | _defs(proc, [entry])
    for lbl in region:
        if out in succs(proc.blocks[lbl].term) and set(gives) - have - _defs(proc, [lbl]):
            return None
    return gives


def _promote(prog, name, lbl, region, out=None, live=None):
    """Move ``region`` into its own procedure; every edge into it becomes a call.

    A region with one way out returns there instead, handing back what the exit
    reads -- sound only where no path inside it returns from ``name``, and worth
    a call only where the reader would see something in it.
    """
    proc = prog.procs[name]
    params = _crossers(proc, region)
    gives = []
    if out is not None:
        if any(type(proc.blocks[l].term) is Return for l in region):
            return None
        if not any(printable(s, live or ()) for l in region for s in proc.blocks[l].stmts):
            return None
        gives = _leaving(proc, region, lbl, out, live, params)
        if gives is None:
            return None
    slots = _slots(params | set(gives))
    if slots is None:
        return None
    hname = unique_name("p_%04X" % proc.blocks[lbl].src, prog.procs, sep="_")
    blocks = copy.deepcopy({l: proc.blocks[l] for l in region})
    rets = tuple(slots[n] for n in gives) if out is not None else proc.rets
    takes = sorted(params, key=lambda k: slots[k])
    helper = Proc(hname, tuple(slots[n] for n in takes), rets, blocks, lbl, "helper")
    _rename(helper, {k: REGVAR[v] for k, v in slots.items()})
    if out is not None:
        exit_returns(helper, out, tuple(Var(REGVAR[i]) for i in rets))
    prog.procs[hname] = helper
    undo = (dict(proc.blocks), {})
    args = tuple(Var(n) for n in takes)
    for p in dict.fromkeys(q for q in preds_of(proc)[lbl] if q not in region):
        stub = "%s$t%s" % (lbl, p)  # a block reaching the tail twice retargets once
        undo[1][p] = proc.blocks[p].term
        names = (
            tuple(gives) if out is not None else tuple("%s$%s" % (REGVAR[i], stub) for i in rets)
        )
        term = Goto(out) if out is not None else Return(tuple(Var(r) for r in names))
        proc.blocks[stub] = Block(
            stub,
            [Call(hname, args, names)],
            term,
            proc.blocks[lbl].src,
            proc.blocks[lbl].count,
        )
        proc.blocks[p].term = retarget(proc.blocks[p].term, lbl, stub)
    for l in region:
        del proc.blocks[l]
    prune(proc)
    return hname, undo


def _rename(proc, sub):
    """Every name the helper takes or hands back becomes its register, defs included."""

    def fn(e):
        return Var(sub[e.n], e.w) if type(e) is Var and e.n in sub else e

    for b in proc.blocks.values():
        for s in b.stmts:
            apply_stmt(s, fn)
            if type(s) is Let and s.n in sub:
                s.n = sub[s.n]
            elif type(s) is Call:
                s.rets = tuple(sub.get(r, r) for r in s.rets)
        apply_term(b.term, fn)
    return proc
