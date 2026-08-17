"""S6/S7 -- what a reader must see: the live values, arguments and return registers.

A procedure's return values are live only where a caller reads them and a stack
frame nothing reads is machine plumbing, so the printer computes a fixpoint over
the call graph and drops the rest. The certified program is never edited.
"""

from __future__ import annotations

from .ir import Assert, Call, Let, REGVAR, Return, Store, W16, retval
from .irwalk import call_order, pure, stmt_uses, term_uses, uses_of


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


def printable(s, live):
    """True when a statement is the program, not the machine's own plumbing."""
    t = type(s)
    if t is Let:
        return s.n in live or not pure(s.e)
    return t is not Store or s.cls != "raw"
