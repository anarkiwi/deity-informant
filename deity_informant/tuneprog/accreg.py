"""T1 -- the register a scratch producer's value lands in.

A value cell a copy loop reloads before every use keeps nothing across ticks and
:mod:`.history` holds only its last copy, so its recurrence has no column to replay
against. The SID register T0 says the value reaches has one, per voice.
"""

from __future__ import annotations

import numpy as np

from . import grid
from .accguard import valnames
from .acchist import truth
from .accshape import arms
from .facts import GLOBAL_REG, SID_REG_LO, SID_VOICE, SID_VOICES, VOICE_REG, elem_count
from .ir import Load, Var, W16
from .irwalk import walk


def observable(obs):
    """One verified run's :class:`~.grid.TickObs` values as ``(ticks, columns)``."""
    return np.array([[-1 if v is None else v for v in o.values] for o in obs], np.int64)


def column(name, voice):
    """``(value column, the first and last bit the register owns in it)``, or ``None``.

    :func:`~.grid.reduce_tick` folds a register pair into one value, so a producer
    of one half claims one field of that value and a competing site's write only
    disturbs it where their fields overlap.
    """
    half = name[-3:] if name.endswith(("_lo", "_hi")) else ""
    got = _regindex(name if half else "%s_lo" % name, voice)
    got = _regindex(name, voice) if got is None else got
    i = None if got is None else grid.value_index(got)
    if i is None:
        return None
    k = grid.PAIRS[i][2] if i < len(grid.PAIRS) else 8
    w = grid.PAIRS[i][3].bit_length() if i < len(grid.PAIRS) else 8
    return (i, k, w) if half == "_hi" else ((i, 0, k) if half == "_lo" else (i, 0, w))


def _regindex(name, voice):
    """The SID register index one name and one voice reach."""
    if name in VOICE_REG:
        return SID_VOICE * voice + VOICE_REG.index(name)
    return next((a - SID_REG_LO for a, g in GLOBAL_REG.items() if g == name), None)


def _overlaps(a, b):
    """True when two register fields of the observable share a bit."""
    return a is not None and b is not None and a[0] == b[0] and a[1] < b[2] and b[1] < a[2]


def indexes(cells, exprs):
    """``{name: stride}`` of every index a read of a three-copy region takes.

    :func:`~.accshape.arms` leaves a copy loop's own index standing in the value
    and in the guards, because every copy shares them; the name a region of three
    elements is indexed by is the binding that says which copy a reading is for.
    """
    out = {}
    for e in exprs:
        for x in walk(e):
            r = cells.rgn.get(x.r) if type(x) is Load else None
            if r is not None and elem_count(r) == SID_VOICES:
                for n in valnames(x.a):
                    out[n] = max(out.get(n, 0), r.stride, 1)
    return out


def others(ctx, t0_doc, rid):
    """``[(register, voices, [guard path])]`` for the sites that write, but not this cell.

    A register is no one producer's: what a tick leaves in it is the last site that
    wrote it, so a tick another site wrote is no reading of this one's value.
    """
    out = []
    for w in t0_doc.get("writes") or ():
        if not w.get("register") or any(c["region"] == rid for c in w["cells"]):
            continue
        site = w["site"]
        s = _stmt(ctx, site)
        if s is None:
            return None
        skip = frozenset(x.n for x in walk(s.a) if type(x) is Var)
        raw = s.e if type(s) is W16 else s.v
        gs = [a.guards for a in arms(ctx, site["proc"], site["block"], raw, s.a, skip)]
        out.append((w["register"], w["voices"], gs))
    return out


def _stmt(ctx, site):
    """The statement one T0 site names, found back in its own block."""
    p = ctx.prog.procs.get(site["proc"])
    b = None if p is None else p.blocks.get(site["block"])
    pc = int(site["pc"][1:], 16)
    return None if b is None else next((s for s in b.stmts if getattr(s, "src", None) == pc), None)


def series(cells, acc, plan, ctx, t0_doc):
    """``([(voice, env, the register's values, the ticks another site wrote it)], blind)``.

    ``(None, 0)`` where T0 names no register or the observable has no column for it.
    ``blind`` counts the competing guards no history reads, which widen the ticks
    the record leaves to another producer exactly as :func:`~.acchist.truth` does.
    """
    reg, voices = acc["target"]["register"], acc["target"]["voices"]
    if cells.obs is None or not reg or not voices:
        return None, 0
    rest = others(ctx, t0_doc, acc["cell"]["region"])
    if rest is None:
        return None, 0
    guards = [g for _r, _v, gs in rest for path in gs for g, _t, _w in path]
    names = indexes(cells, [x for c in plan for x in _exprs(c)] + guards)
    out, seen, was = [], 0, cells.subst
    cells.subst, mask = dict(cells.epochs()), (1 << acc["width"]) - 1
    for v in voices:
        got = column(reg, v)
        if got is None or got[0] >= cells.obs.shape[1] or acc["width"] > got[2] - got[1]:
            cells.subst = was
            return None, 0
        env = {n: v * s for n, s in names.items()}
        wrote = np.zeros(cells.ticks, bool)
        for other, vs, gs in rest:
            if v not in vs or not _overlaps(column(other, v), got):
                continue
            for g in gs:
                held, _gone, blind = truth(cells, g, env)
                seen, wrote = seen + blind, wrote | held
        out.append((v, env, (cells.obs[: cells.ticks, got[0]] >> got[1]) & mask, wrote))
    cells.subst = was
    return out, seen


def _exprs(c):
    """Every expression one clause stands on: its value, its delta and its guards."""
    got = (c.value, c.delta, c.carry, c.times, c.addr)
    return [x for x in got if x is not None] + [g for g, _t, _w in c.guards]


def bound(per, complete, period, ticks):
    """The interval the register itself kept: a scratch cell's only evidence of one.

    A cell with no column has no guard on its value and no mask at its store, so
    what the run shows the register held is the whole of the interval evidence --
    the period where the horizon repeats, the horizon itself where it does not.
    """
    got = [c for _v, _e, c, _w in per if c.size]
    if not got:
        return []
    why = "period %s" % period if complete else "horizon %d ticks" % ticks
    lo, hi = min(int(c.min()) for c in got), max(int(c.max()) for c in got)
    return [{"interval": [lo, hi], "from": "observed", "witness": why}]
