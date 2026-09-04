"""L1 -- the certified tick as one structured procedure.

Inlining (:mod:`..callee`), rerolling -- a run of calls whose stepping argument
closes it, and a chain of sibling blocks that differ only in an arithmetic
progression of their own constants -- and the facts the later levels read off
the result: the voice loop and its induction variables, the per-voice arrays,
the tuning, and the blocks only the first call runs.
"""

from __future__ import annotations

from dataclasses import fields, is_dataclass

from ...tuneprog.graph import preds_of, rpo, succs
from ...tuneprog.ir import (
    Bin,
    Block,
    Const,
    Goto,
    If,
    Let,
    Load,
    Store,
    Tuneprog,
    Var,
    retarget,
)
from ...tuneprog.irwalk import addr_split, stmt_uses, term_uses, walk
from .. import callee, record, schedule, tables
from ..cells import Cells
from .ir import Level

SKIP = ("src",)  # a statement's own pc: no part of what two sibling copies share
ENV = ("lo", "hi")  # a load's or a store's envelope: the copies' own extent, joined
IDX = "$v"  # the index a rerolled chain steps, one name a step


def _locals(stmts):
    """The names one block binds, each at the position it binds them: alpha equivalence."""
    return {s.n: "$l%d" % i for i, s in enumerate(stmts) if type(s) is Let}


def _key(x, out, env, sub=None):
    """A hashable skeleton of one IR value, with its constants collected in ``out``."""
    sub = sub or {}
    if type(x) is Const:
        out.append(x.v)
        return ("$c", x.w)
    if type(x) is Var:
        return ("Var", sub.get(x.n, x.n), x.w)
    if is_dataclass(x) and not isinstance(x, type):
        got = []
        for f in fields(x):
            if f.name in SKIP:
                continue
            v = getattr(x, f.name)
            if f.name in ENV and isinstance(v, int):
                env.append(v)
                got.append("$e")
            elif type(x) is Let and f.name == "n":
                got.append(sub.get(v, v))
            else:
                got.append(_key(v, out, env, sub))
        return (type(x).__name__,) + tuple(got)
    if isinstance(x, (list, tuple)):
        return tuple(_key(y, out, env, sub) for y in x)
    if isinstance(x, dict):
        return tuple((k, _key(v, out, env, sub)) for k, v in sorted(x.items()))
    return x


def _steps(cols):
    """``[step]`` per column, where each column is constant or one progression."""
    out = []
    for col in cols:
        d = {(b - a) for a, b in zip(col, col[1:])}
        if len(d) != 1:
            return None
        out.append(d.pop())
    return out


def _chain(p, lbl, seen):
    """The straight-line run of blocks one label opens: ``Goto`` and one predecessor."""
    preds, out = preds_of(p), [lbl]
    while True:
        b = p.blocks[out[-1]]
        if type(b.term) is not Goto or b.term.to not in p.blocks:
            return out
        nxt = b.term.to
        if nxt in out or nxt in seen or len(preds.get(nxt, ())) != 1:
            return out
        out.append(nxt)


def _match(p, run):
    """``(k, steps, envelopes)`` for the longest prefix of a run that is one loop.

    ``k`` is 0 where the run is no loop.
    """
    keys, consts, envs = [], [], []
    for lbl in run:
        c, e = [], []
        stmts = p.blocks[lbl].stmts
        keys.append(_key(stmts, c, e, _locals(stmts)))
        consts.append(c)
        envs.append(e)
    for k in range(len(run), 1, -1):
        if len(set(keys[:k])) != 1:
            continue
        got = _steps(list(zip(*consts[:k]))) if consts[0] else []
        if got is None or not any(got):
            continue
        env = [(min(col), max(col)) for col in zip(*envs[:k])] if envs[0] else []
        return (k, got, env)
    return (0, [], [])


def _rewrite(x, prog, env, idx):
    """One IR value with each progressing constant read off the index it steps with."""
    if type(x) is Const:
        v, d = next(prog)
        return x if not d else Bin("+", Const(v, x.w), Var(idx[d], x.w), x.w)
    if is_dataclass(x) and not isinstance(x, type):
        got = {}
        for f in fields(x):
            v = getattr(x, f.name)
            if f.name in SKIP:
                got[f.name] = v
            elif f.name in ENV and isinstance(v, int):
                got[f.name] = next(env)[0 if f.name == "lo" else 1]
            else:
                got[f.name] = _rewrite(v, prog, env, idx)
        return type(x)(**got)
    if isinstance(x, list):
        return [_rewrite(y, prog, env, idx) for y in x]
    if isinstance(x, tuple):
        return tuple(_rewrite(y, prog, env, idx) for y in x)
    return x


def reroll(prog, proc):
    """Sibling copies of one body, at a stride, as the pass over that stride they are.

    A chain of blocks whose statements are equal but for an arithmetic
    progression of their own constants is one turn of a loop: one index a step,
    seeded in the pre-header and moved in the latch, and a counter that closes it.
    """
    p = prog.procs[proc]
    seen, n, rolled = set(), 0, []
    for lbl in list(rpo(p)):
        if lbl in seen or lbl not in p.blocks:
            continue
        run = _chain(p, lbl, seen)
        seen |= set(run)
        k, steps, envs = _match(p, run) if len(run) > 1 else (0, [], [])
        if k < 2 or _escapes(p, run[1:k]):
            continue
        rolled += _close(p, run, k, steps, envs)
        n += 1
    got = Tuneprog(prog.meta, prog.storage, prog.inputs, dict(prog.procs, **{proc: p}))
    return got, n, tuple(rolled)


def _escapes(p, inner):
    """Whether a name the copies after the first bind is read outside the chain."""
    bound = {s.n for l in inner for s in p.blocks[l].stmts if type(s) is Let}
    if not bound:
        return False
    out = set()
    for lbl, b in p.blocks.items():
        if lbl in inner:
            continue
        for s in b.stmts:
            stmt_uses(s, out)
        term_uses(b.term, out)
    return bool(bound & out)


def _close(p, run, k, steps, envs):
    """One matched chain rewritten to a single copy, an index a step and a counter."""
    b, tail = p.blocks[run[0]], p.blocks[run[k - 1]].term.to
    idx = {d: "%s%d$%s" % (IDX, d, run[0]) for d in set(steps) if d}
    consts, env = [], []
    _key(b.stmts, consts, env, _locals(b.stmts))
    head, latch = run[0] + "$h", run[0] + "$l"
    cnt = "%sn$%s" % (IDX, run[0])
    p.blocks[head] = Block(
        head,
        [Let(n, Const(0, 2)) for n in idx.values()] + [Let(cnt, Const(k, 1))],
        Goto(run[0]),
        b.src,
    )
    b.stmts = _rewrite(b.stmts, iter(list(zip(consts, steps))), iter(envs), idx)
    b.term = Goto(latch)
    p.blocks[latch] = Block(
        latch,
        [Let(n, Bin("+", Var(n, 2), Const(d, 2), 2)) for d, n in sorted(idx.items())]
        + [Let(cnt, Bin("-", Var(cnt, 1), Const(1, 1), 1))],
        If(Bin("!=", Var(cnt, 1), Const(0, 1), 1), run[0], tail),
        b.src,
    )
    for lbl, blk in p.blocks.items():
        if lbl not in (head, latch) and run[0] in succs(blk.term):
            blk.term = retarget(blk.term, run[0], head)
    if p.entry == run[0]:
        p.entry = head
    for lbl in run[1:k]:
        del p.blocks[lbl]
    return [(n, d, k) for d, n in sorted(idx.items())]


def arrays(cells, p):
    """``{base: (name, copies)}``: the per-voice arrays the tick reads at an index."""
    out = {}
    for b in p.blocks.values():
        for s in b.stmts:
            for e in ((s.a, s.v) if type(s) is Store else (s.e,) if type(s) is Let else ()):
                for x in _loads(e):
                    got = cells.at(x)
                    if got and got[0] == "voice":
                        name = got[1][0] if isinstance(got[1], tuple) else got[1]
                        out[x - (got[1][1] if isinstance(got[1], tuple) else 0)] = (
                            name,
                            cells.voices,
                        )
    return out


def _loads(e):
    """The constant base addresses one expression reads or writes."""
    out = []
    for x in walk(e):
        if type(x) in (Load, Store):
            base = addr_split(x.a)[0]
            if base is not None:
                out.append(base)
    return out


def structure(art, fetchblocks=(), ticks=3):
    """L0 to L1: one procedure, its loops closed, and the facts the levels read."""
    prog, proc = art["prog"], art["prog"].meta["tick_proc"]
    prog, loops = callee.inline(prog, proc)
    prog, folded, rolled = reroll(prog, proc)
    p = prog.procs[proc]
    inside = frozenset(fetchblocks) or frozenset(p.blocks)
    head, (body, latches) = schedule.voice_loop(prog, proc, inside)
    vidx = schedule.copies(p, schedule.induction(p, head, latches)) if head else frozenset()
    # a chain the structuring rerolled at the voice stride, as many turns as
    # there are voices, is a pass over the voices and its index is a voice index
    pit = tables.pitch_of(art, art["view"], art["names"])
    entry0 = tuple(b + pit.step * pit.base for b in pit.obases) if pit else ()
    cells = Cells(
        art["view"],
        art["names"],
        pitch=(pit.rids, entry0, pit.step, pit.n) if pit else None,
        words=tables.word_widths(prog, proc),
    )
    vidx |= {n for n, d, k in rolled if d == cells.stride and k == cells.voices}
    pro = record.firstonly(prog, proc, art.get("inputs") or {}, ticks)
    return Level(
        1,
        art=art,
        prog=prog,
        proc=proc,
        facts={
            "inlined_loops": loops,
            "rerolled": folded,
            "head": head or "",
            "body": frozenset(body),
            "latches": frozenset(latches),
            "vidx": vidx,
            "cells": cells,
            "pitch": pit,
            "arrays": arrays(cells, p),
            "prologue": frozenset(pro or ()),
        },
    )
