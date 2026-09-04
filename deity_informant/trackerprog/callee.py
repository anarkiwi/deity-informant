"""B7 -- the callees the tick reaches, as blocks of the tick's own graph.

A ``Call`` is inlined where it stands. A *run* of calls to one procedure whose
live arguments agree but for one constant that steps is one pass over that
constant, so the run is inlined once inside the loop its own step closes.
"""

from __future__ import annotations

import copy

from ..tuneprog.graph import rpo
from ..tuneprog.ir import (
    Bin,
    Block,
    Call,
    Const,
    Goto,
    If,
    Let,
    Phi,
    REGVAR,
    Return,
    Tuneprog,
    Var,
    retarget,
    succs,
)
from ..tuneprog.irwalk import apply_stmt, apply_term, defs_of, renamer, stmt_uses, term_uses

PASSES = 512
IN, OUT = "$p$", "$r$"  # what a copy reads a register in, and what it leaves it in


def _uses(proc):
    """Every name the body of one procedure reads."""
    out = set()
    for b in proc.blocks.values():
        for s in b.stmts:
            stmt_uses(s, out)
        term_uses(b.term, out)
    return out


def _bound(proc):
    """Every name the body of one procedure binds."""
    return {n for b in proc.blocks.values() for s in b.stmts for n in defs_of(s)}


def livein(proc):
    """The parameter registers the body reads: what a caller must state."""
    used = _uses(proc)
    return tuple(i for i in proc.params if REGVAR[i] in used)


def _relabel(term, pfx):
    for l in list(dict.fromkeys(succs(term))):
        term = retarget(term, l, pfx + l)
    return term


def copyproc(proc, pfx, bind):
    """One copy of a procedure's blocks, every name and label under ``pfx``.

    ``bind`` names the value each parameter register is read as in the copy.
    """
    p = copy.deepcopy(proc)
    sub = {n: Var(pfx + n) for n in _bound(p)}
    sub.update(bind)
    fn = renamer(sub)
    out = {}
    for lbl, b in p.blocks.items():
        for s in b.stmts:
            apply_stmt(s, fn)
            if type(s) is Phi:
                s.n, s.args = pfx + s.n, {pfx + k: fn(v) for k, v in s.args.items()}
            elif type(s) is Let:
                s.n = pfx + s.n
            elif type(s) is Call:
                s.rets = tuple(pfx + n for n in s.rets)
        apply_term(b.term, fn)
        b.term = _relabel(b.term, pfx)
        b.label = pfx + lbl
        out[b.label] = b
    return out, pfx + proc.entry


def _returns(blocks):
    return [b for b in blocks.values() if type(b.term) is Return]


def _site(proc):
    """``(label, index, run)``: the first call of the graph, and the run it opens."""
    for lbl in rpo(proc):
        b = proc.blocks.get(lbl)
        if b is None:
            continue
        for i, s in enumerate(b.stmts):
            if type(s) is not Call:
                continue
            run = [s]
            for t in b.stmts[i + 1 :]:
                if type(t) is not Call or t.proc != s.proc:
                    break
                run.append(t)
            return lbl, i, run
    return None


def _progression(col):
    """``(start, step)`` where a column of constants steps by one value, else ``None``."""
    if not all(type(x) is Const for x in col):
        return None
    vs = [x.v for x in col]
    d = {(b - a) & 0xFF for a, b in zip(vs, vs[1:])}
    if len(set(vs)) != len(vs) or len(d) != 1 or d == {0}:
        return None
    return vs[0], d.pop()


def _chained(col, run, slot):
    """Whether each argument after the first is what the call before it left there."""
    return all(
        type(col[k]) is Var and col[k].n == run[k - 1].rets[slot] for k in range(1, len(col))
    )


def shape(callee, run):
    """``(index, carried, invariant)`` where a run of calls is one loop, else ``None``.

    Over the parameters the callee reads: a column of constants that steps is the
    loop's index, a column each call takes from the one before it is carried, and
    a column every call agrees on is invariant.
    """
    if len(run) < 2 or any(len(c.rets) < len(callee.rets) for c in run):
        return None
    slot = {r: k for k, r in enumerate(callee.params)}
    ret = {r: k for k, r in enumerate(callee.rets)}
    index, carried, inv = None, {}, {}
    for r in livein(callee):
        col = [c.args[slot[r]] for c in run]
        got = _progression(col)
        if got is not None:
            if index is not None:
                return None
            index = (r,) + got
        elif all(x == col[0] for x in col):
            inv[r] = col[0]
        elif r in ret and _chained(col, run, ret[r]):
            carried[r] = col[0]
        else:
            return None
    return None if index is None else (index, carried, inv)


def _escapes(proc, run, lbl, at):
    """Whether a name an inner call of the run left is read outside the run."""
    inner = {n for c in run[:-1] for n in c.rets}
    if not inner:
        return False
    outside = set()
    for l, b in proc.blocks.items():
        for i, s in enumerate(b.stmts):
            if l == lbl and at <= i < at + len(run):
                continue
            stmt_uses(s, outside)
        term_uses(b.term, outside)
    return bool(inner & outside)


class Inliner:
    """One tick proc rewritten until it holds no ``Call``."""

    def __init__(self, prog, proc):
        self.prog = prog
        self.proc = copy.deepcopy(prog.procs[proc])
        self.n, self.loops = 0, 0

    def run(self):
        for _ in range(PASSES):
            got = _site(self.proc)
            if got is None:
                return self.proc
            self.splice(*got)
        raise RecursionError("the tick's call graph did not close")

    def splice(self, lbl, at, run):
        """One call site, or the whole run where the run is a loop."""
        callee = self.prog.procs[run[0].proc]
        got = shape(callee, run) if len(run) > 1 else None
        if got is not None and _escapes(self.proc, run, lbl, at):
            got = None
        take = len(run) if got is not None else 1
        b = self.proc.blocks[lbl]
        tail = "%s$i%d" % (lbl, self.n)
        self.n += 1
        pfx = tail + "$"
        self.proc.blocks[tail] = Block(tail, b.stmts[at + take :], b.term, b.src)
        b.stmts = b.stmts[:at]
        if got is None:
            self.straight(b, pfx, tail, callee, run[0])
        else:
            self.loop(b, pfx, tail, callee, run, got)
            self.loops += 1

    def straight(self, head, pfx, tail, callee, call):
        """One call: the callee's blocks, its returns the goto of what follows."""
        bind = {}
        for r in livein(callee):
            n = pfx + IN + REGVAR[r]
            head.stmts.append(Let(n, call.args[callee.params.index(r)]))
            bind[REGVAR[r]] = Var(n)
        blocks, entry = copyproc(callee, pfx, bind)
        for b in _returns(blocks):
            vals = b.term.vals
            b.stmts += [Let(n, vals[j]) for j, n in enumerate(call.rets) if j < len(vals)]
            b.term = Goto(tail)
        self.proc.blocks.update(blocks)
        head.term = Goto(entry)

    def loop(self, head, pfx, tail, callee, run, got):
        """A run of calls as the pass it is: one copy, closed by the run's own step."""
        (reg, start, step), carried, inv = got
        head.stmts.append(Let(pfx + IN + REGVAR[reg], Const(start, 1)))
        for r, e in sorted(list(carried.items()) + list(inv.items())):
            head.stmts.append(Let(pfx + IN + REGVAR[r], e))
        bind = {REGVAR[r]: Var(pfx + IN + REGVAR[r]) for r in livein(callee)}
        blocks, entry = copyproc(callee, pfx, bind)
        end = (start + len(run) * step) & 0xFF
        for b in _returns(blocks):
            self._latch(b, pfx, tail, callee, (reg, step, end), entry, sorted(carried))
        self.proc.blocks.update(blocks)
        head.term = Goto(entry)
        self.proc.blocks[tail].stmts = [
            Let(n, Var(pfx + OUT + REGVAR[callee.rets[j]]))
            for j, n in enumerate(run[-1].rets)
            if j < len(callee.rets)
        ] + self.proc.blocks[tail].stmts

    def _latch(self, b, pfx, tail, callee, step, entry, carried):
        """One return of the copy: what it leaves, the index stepped, the turn tested."""
        reg, by, end = step
        vals, held = b.term.vals, []
        for j, r in enumerate(callee.rets):
            if j >= len(vals):
                break
            held.append((r, "%s$t%d" % (b.label, j)))
            b.stmts.append(Let(held[-1][1], vals[j]))
        b.stmts += [Let(pfx + OUT + REGVAR[r], Var(n)) for r, n in held]
        b.stmts += [
            Let(pfx + IN + REGVAR[r], Var(pfx + OUT + REGVAR[r]))
            for r in carried
            if r in callee.rets
        ]
        nxt = b.label + "$x"
        b.stmts.append(Let(nxt, Bin("+", Var(pfx + IN + REGVAR[reg]), Const(by, 1), 1)))
        b.stmts.append(Let(pfx + IN + REGVAR[reg], Var(nxt)))
        b.term = If(Bin("!=", Var(pfx + IN + REGVAR[reg]), Const(end, 1), 1), entry, tail)


def inline(prog, proc):
    """``(program, loops)``: the tick with every callee it reaches as blocks of its own."""
    p = prog.procs.get(proc)
    if p is None or not any(type(s) is Call for b in p.blocks.values() for s in b.stmts):
        return prog, 0
    it = Inliner(prog, proc)
    got = it.run()
    procs = dict(prog.procs)
    procs[proc] = got
    return Tuneprog(prog.meta, prog.storage, prog.inputs, procs), it.loops
