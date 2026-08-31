"""T1 -- the register a scratch producer's value lands in.

A value cell a copy loop reloads before every use keeps nothing across ticks and
:mod:`.history` holds only its last copy, so its recurrence has no column to replay
against. The SID register T0 says the value reaches has one, per voice.
"""

from __future__ import annotations

import numpy as np

from . import grid
from .accshape import arms
from .accstep import State, exprs_of, indexes
from .facts import GLOBAL_REG, SID_REG_LO, SID_VOICE, VOICE_REG
from .ir import Store, Var, W16
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


def others(ctx, t0_doc, rid):
    """``[(register, voices, [(guard path, ranks)])]`` for the sites that write, but not this cell.

    A register is no one producer's: what a tick leaves in it is the last site that
    wrote it, so a tick another site wrote is no reading of this one's value.
    """
    out = []
    for w in t0_doc.get("writes") or ():
        if not w.get("register") or any(c["region"] == rid for c in w["cells"]):
            continue
        site = w["site"]
        got = _stmt(ctx, site)
        if got is None:
            return None
        lbl, i, s = got
        skip = frozenset(x.n for x in walk(s.a) if type(x) is Var)
        raw = s.e if type(s) is W16 else s.v
        arms_ = arms(ctx, site["proc"], lbl, raw, s.a, skip)
        gs = [(a.guards, at) for a, _r, at in ctx.ranked(site["proc"], lbl, i, arms_)]
        out.append((w["register"], w["voices"], gs))
    return out


def _stmt(ctx, site):
    """``(block, index, statement)`` of one T0 site, found back by its pc.

    The block is looked up by pc, not by the label T0 carries: :func:`~.pipeline.present`
    labels its blocks per run.
    """
    p = ctx.prog.procs.get(site["proc"])
    pc = int(site["pc"][1:], 16)
    for lbl, b in (p.blocks.items() if p is not None else ()):
        for i, s in enumerate(b.stmts):
            if getattr(s, "src", None) == pc and type(s) in (Store, W16):
                return lbl, i, s
    return None


def series(cells, acc, plan, ctx, t0_doc, stepper):
    """``[(voice, env, the register's values, the ticks another site wrote it)]``.

    ``None`` where T0 names no register or the observable has no column for it. A
    competing site's guards are read exactly, at their own epochs; one the stepper
    cannot read raises :class:`~.accstep.Inexact` at its site.
    """
    reg, voices = acc["target"]["register"], acc["target"]["voices"]
    if cells.obs is None or not reg or not voices:
        return None
    rest = others(ctx, t0_doc, acc["cell"]["region"])
    if rest is None:
        return None
    guards = [g for _r, _v, gs in rest for path, _at in gs for g, _t, _w in path]
    names = indexes(cells, [x for c in plan for x in exprs_of(c)] + guards)
    out, mask = [], (1 << acc["width"]) - 1
    for v in voices:
        got = column(reg, v)
        if got is None or got[0] >= cells.obs.shape[1] or acc["width"] > got[2] - got[1]:
            return None
        env = {n: v * s for n, s in names.items()}
        wrote = np.zeros(cells.ticks, bool)
        for other, vs, gs in rest:
            if v not in vs or not _overlaps(column(other, v), got):
                continue
            for path, at in gs:
                st = State(env, {}, [], None, other, 0, None)
                stepper.bad[:] = False
                held = stepper.guards(path, at, st)[0]
                stepper.check(held, st)
                wrote |= held
        out.append((v, env, (cells.obs[: cells.ticks, got[0]] >> got[1]) & mask, wrote))
    return out


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
