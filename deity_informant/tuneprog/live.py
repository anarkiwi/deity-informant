"""S6/S7 -- what a reader must see: the live values, arguments and return registers.

A procedure's return values are live only where a caller reads them and a stack
frame nothing reads is machine plumbing, so the printer computes a fixpoint over
the call graph and drops the rest. The certified program is never edited.
"""

from __future__ import annotations

from .graph import latches as _latches
from .ir import Assert, Call, Let, REGIDX, REGVAR, Return, Store, Var, W16, copyval, retval
from .ir import succs
from .irwalk import apply_stmt, apply_term, call_order, defs_of, pure, renamer
from .irwalk import stmt_uses, term_uses, use_counts, uses_of


def wants(prog, live):
    """``{procedure: the return registers a caller reads}`` (design section 6.2)."""
    out = {n: set() for n in prog.procs}
    for name, p in prog.procs.items():
        for b in p.blocks.values():
            for s in b.stmts:
                if type(s) is Call and s.proc in out:
                    q = prog.procs[s.proc]
                    out[s.proc] |= {i for i, r in zip(q.rets, s.rets) if r in live[name]}
    return out


def _hostret(prog):
    """A play entry's ``A`` is the tune's return value: nobody reads it, the host does."""
    return {n: ({0} if retval(p) is not None else set()) for n, p in prog.procs.items()}


def _stmt_roots(prog, s, defs, params, out):
    """Record a statement's definition and, when it has an effect, what it reads."""
    t = type(s)
    if t is Let:
        defs.setdefault(s.n, []).append(s.e)
        if not pure(s.e):
            uses_of(s.e, out)
    elif t is W16 or t is Assert or (t is Store and s.cls != "raw"):
        stmt_uses(s, out)
    elif t is Call:
        for i, a in zip(prog.procs[s.proc].params, s.args):
            if i in params.get(s.proc, ()):
                uses_of(a, out)


def _roots(prog, name, params, rets):
    """The values a procedure must compute: stores, tests, inputs, wanted returns."""
    p = prog.procs[name]
    defs, out = {}, set()
    for b in p.blocks.values():
        for s in b.stmts:
            _stmt_roots(prog, s, defs, params, out)
        if type(b.term) is Return:
            for i, v in zip(p.rets, b.term.vals):
                if i in rets[name]:
                    uses_of(v, out)
        else:
            term_uses(b.term, out)
    work = list(out)
    while work:
        n = work.pop()
        for e in defs.pop(n, ()):
            add = uses_of(e, set())
            out |= add
            work += list(add)
    return out


def needed(prog, rounds=3):
    """``({proc: live names}, {proc: params it reads})`` -- the plumbing left out.

    A procedure's return values are live only where a caller reads them, which
    takes one pass per call-graph level to settle.
    """
    rets = _hostret(prog)
    used, params = {}, {}
    for _ in range(rounds):
        used, params = {}, {}
        for name in call_order(prog):
            used[name] = _roots(prog, name, params, rets)
            params[name] = tuple(i for i in prog.procs[name].params if REGVAR[i] in used[name])
        want = _hostret(prog)
        for name, regs in wants(prog, used).items():
            want[name] |= regs
        if want == rets:
            break
        rets = want
    return used, params


def dead(prog):
    """Delete every ``Let`` nothing reads whose value has no effect; returns the count.

    :func:`needed` answers the same question over the call graph and from the roots
    down; this is the same use relation (:func:`~.irwalk.use_counts`, which is
    :func:`~.irwalk.stmt_uses` over every node) counted per name, which is what a
    view already shaped for reading needs. A ``Call`` return is not a ``Let``, and a
    load of a pinned input is not pure, so neither is ever dropped.
    """
    n = 0
    for proc in prog.procs.values():
        while True:
            uses, gone = use_counts(proc), 0
            for b in proc.blocks.values():
                keep = [s for s in b.stmts if type(s) is not Let or uses[s.n] or not pure(s.e)]
                gone += len(b.stmts) - len(keep)
                b.stmts[:] = keep
            n += gone
            if not gone:
                break
    return n


# ---- live ranges over one procedure ------------------------------------------
def live_out(proc):
    """``{block: the names live on leaving it}``, by the backward fixpoint over the uses."""
    up, kill = {}, {}
    for lbl, b in proc.blocks.items():
        u, k = set(), set()
        for s in b.stmts:
            u |= stmt_uses(s, set()) - k
            k |= set(defs_of(s))
        up[lbl], kill[lbl] = u | (term_uses(b.term, set()) - k), k
    out = {lbl: set() for lbl in proc.blocks}
    work = True
    while work:
        work = False
        for lbl, b in proc.blocks.items():
            got = set()
            for nxt in succs(b.term):
                if nxt in proc.blocks:
                    got |= up[nxt] | (out[nxt] - kill[nxt])
            if got != out[lbl]:
                out[lbl], work = got, True
    return out


def _live_after(proc, outs):
    """``{(block, index): the names live just after that statement}``."""
    out = {}
    for lbl, b in proc.blocks.items():
        live = set(outs[lbl]) | term_uses(b.term, set())
        for i in range(len(b.stmts) - 1, -1, -1):
            out[(lbl, i)] = set(live)
            live -= set(defs_of(b.stmts[i]))
            stmt_uses(b.stmts[i], live)
    return out


# ---- the copies a join leaves ------------------------------------------------
def coalesce(prog, rounds=8):
    """A register copy goes when its two names never hold different values at once.

    Phi elimination leaves one copy per arm at every join, and neither name has a
    single definition, so :func:`~.texture.propagate` cannot forward it. Renaming
    the target to the source is sound exactly where their live ranges do not meet.
    """
    n = 0
    for proc in prog.procs.values():
        latched = _latches(proc)
        seen = (latched, _stepped(proc, latched))
        for _ in range(rounds):
            got = _coalesce(proc, seen)
            n += got
            if not got:
                break
    return n


def _stepped(proc, latched):
    """The names a latch copy joins: the induction chain :func:`~.loops.stepping` states."""
    return {
        x
        for l in latched
        for s in proc.blocks[l].stmts
        if type(s) is Let and type(s.e) is Var
        for x in (s.n, s.e.n)
    }


def _coalesce(proc, seen):
    """One pass: coalesce every copy whose pair clashes with nothing, names once each."""
    after, defs = _live_after(proc, live_out(proc)), {}
    for lbl, b in proc.blocks.items():
        for i, s in enumerate(b.stmts):
            for name in defs_of(s):
                defs.setdefault(name, []).append((lbl, i))
    done, n = set(), 0
    for lbl, i, tgt, src in _copy_pairs(proc, *seen):
        if tgt in done or src in done or _clash(defs, after, tgt, src, (lbl, i)):
            continue
        _rename_value(proc, tgt, src)
        done |= {tgt, src}
        n += 1
    return n


def _copy_pairs(proc, latched, stepped):
    """``[(block, index, target, source)]`` for every plain copy outside a latch.

    A latch's copy is the induction variable's step, which :mod:`.loops` reads; a
    copy-fold name and a flag name are not this procedure's to rename.
    """
    return [
        (lbl, i, s.n, s.e.n)
        for lbl in sorted(proc.blocks)  # a greedy choice may not depend on the dict's order
        if lbl not in latched
        for i, s in enumerate(proc.blocks[lbl].stmts)
        if type(s) is Let and type(s.e) is Var and s.n != s.e.n
        if _plain(s.n) and _plain(s.e.n) and not {s.n, s.e.n} & stepped
    ]


def _plain(name):
    """True for a name this procedure defines and no other pass keys on."""
    return not name.startswith("$") and not copyval(name) and name not in REGIDX


def _clash(defs, after, tgt, src, at):
    """True when the two names are both live at a definition of either, bar the copy."""
    for a, b in ((tgt, src), (src, tgt)):
        if any(pos != at and b in after[pos] for pos in defs.get(a, ())):
            return True
    return False


def _rename_value(proc, tgt, src):
    """Give every definition and use of ``tgt`` the name ``src``; drop the self-copies."""
    fn = renamer({tgt: Var(src)})
    for b in proc.blocks.values():
        for s in b.stmts:
            if type(s) is Call:
                s.rets = tuple(src if r == tgt else r for r in s.rets)
            elif getattr(s, "n", None) == tgt:
                s.n = src
            apply_stmt(s, fn)
        apply_term(b.term, fn)
        b.stmts[:] = [s for s in b.stmts if not (type(s) is Let and s.e == Var(src) and s.n == src)]


def printable(s, live):
    """True when a statement is the program, not the machine's own plumbing."""
    t = type(s)
    if t is Let:
        return s.n in live or not pure(s.e)
    return t is not Store or s.cls != "raw"
