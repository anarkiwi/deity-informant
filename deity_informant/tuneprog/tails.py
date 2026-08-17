"""S6 -- shared tails become procedures: the routine a 6510 player enters by JMP.

A region several jumps reach and nothing leaves is the ``goto`` residue of
structuring (GoatTracker's ``mt_loadregs``, ``mt_done``, its effect tails).
Promoting it to a procedure each jump tail-calls costs one call per predecessor.
"""

from __future__ import annotations

import copy

from .graph import cfg, idoms, natural_loops, preds_of
from .ir import Block, Call, Proc, REGIDX, REGVAR, Return, Var, retarget, retval, succs
from .irwalk import apply_stmt, apply_term, defs_of, stmt_uses, term_uses, unique_name
from .ssa import prune
from .structure import Jump, structure_proc, walk

SPARE = tuple(i for i in range(16) if REGVAR[i].startswith("r"))


def promote_tails(prog, names=None, rounds=256):
    """Promote shared tails while the ``goto`` residue does not grow, best state kept."""
    return sum(_promote_proc(prog, n, names, rounds) for n in list(prog.procs))


def _promote_proc(prog, name, names, rounds):
    """Promote while the residue does not grow, then keep the best state seen."""
    proc = prog.procs[name]
    if retval(proc) is not None:
        return 0  # the text shows what this procedure returns: keep its exits
    cur = best = _gotos(proc)
    steps, mark = [], 0
    for _ in range(rounds):
        if not cur:
            break
        hit = _one(prog, name, names, cur)
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


def _one(prog, name, names, cur):
    """Promote the smallest tail that leaves the residue no worse; its undo record."""
    proc = prog.procs[name]
    for _size, lbl, region in _tails(proc):
        hit = _promote(prog, name, lbl, region)
        if hit is None:
            continue
        made, undo = hit
        now = _gotos(proc)
        if now <= cur:
            if names is not None:
                names.procs[made] = made
            return now, made, undo
        _revert(prog, proc, made, undo)
    return None


def _tails(proc):
    """``[(statements, label, region)]`` for every multi-entry region with no exit."""
    g, preds = cfg(proc), preds_of(proc)
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
        if lbl in heads or lbl == proc.entry or len(set(preds[lbl])) < 2:
            continue
        if any(s not in region for l in region for s in succs(proc.blocks[l].term)):
            continue
        out.append((sum(len(proc.blocks[l].stmts) for l in region), lbl, sorted(region)))
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


def _promote(prog, name, lbl, region):
    """Move ``region`` into its own procedure; every edge into it becomes a call."""
    proc = prog.procs[name]
    slots = _slots(_crossers(proc, region))
    if slots is None:
        return None
    hname = unique_name("p_%04X" % proc.blocks[lbl].src, prog.procs, sep="_")
    blocks = copy.deepcopy({l: proc.blocks[l] for l in region})
    helper = Proc(hname, tuple(sorted(slots.values())), proc.rets, blocks, lbl, "helper")
    _rename(helper, {k: REGVAR[v] for k, v in slots.items()})
    prog.procs[hname] = helper
    undo = (dict(proc.blocks), {})
    args = tuple(Var(n) for n in sorted(slots, key=lambda k: slots[k]))
    for p in [q for q in preds_of(proc)[lbl] if q not in region]:
        stub = "%s$t%s" % (lbl, p)
        undo[1][p] = proc.blocks[p].term
        rets = tuple("%s$%s" % (REGVAR[i], stub) for i in proc.rets)
        proc.blocks[stub] = Block(
            stub,
            [Call(hname, args, rets)],
            Return(tuple(Var(r) for r in rets)),
            proc.blocks[lbl].src,
            proc.blocks[lbl].count,
        )
        proc.blocks[p].term = retarget(proc.blocks[p].term, lbl, stub)
    for l in region:
        del proc.blocks[l]
    prune(proc)
    return hname, undo


def _rename(proc, sub):
    def fn(e):
        return Var(sub[e.n], e.w) if type(e) is Var and e.n in sub else e

    for b in proc.blocks.values():
        for s in b.stmts:
            apply_stmt(s, fn)
        apply_term(b.term, fn)
    return proc
