"""S6 -- naming the frames :mod:`.frames` proves: a push and its pop are one value.

The certified program has no stack left unless the analysis called it residual, so
this pass names what remains for the view: each slot becomes a ``$saved`` value,
and a push keeps its write while some reachable procedure may read a foreign frame.
"""

from __future__ import annotations

from .frames import analyse, apply_reads, deltas, drop_regions, fresh
from .ir import Let, Store, Var
from .irwalk import reachable

__all__ = ["analyse", "deltas", "fresh", "frames"]


def _as_value(s, name, keep):
    """A push as the value it pushed, keeping the write while a frame may be foreign."""
    out = [Let(name, s.v)]
    if keep:
        out.append(Store(s.cls, s.a, Var(name), s.w, s.lo, s.hi, s.r, s.src))
    return out


def frames(prog, info=None, make=None):
    """Forward every frame slot a procedure pushes and pops; returns the slot count."""
    make = make or fresh(prog)
    plans, out = analyse(prog, info), 0
    for name, frame in plans.items():
        proc = prog.procs[name]
        keep = any(plans[c].foreign for c in reachable(prog, name) - {name} if c in plans)
        edits, sub = {}, {}
        for pushes, keys in frame.plan:
            var = make()
            for lbl, i in pushes:
                edits[(lbl, i)] = _as_value(proc.blocks[lbl].stmts[i], var, keep)
            sub.update({k: Var(var) for k in keys})
            out += 1
        apply_reads(proc, sub)
        for lbl in {l for l, _i in edits}:
            b = proc.blocks[lbl]
            b.stmts = [x for i, s in enumerate(b.stmts) for x in edits.get((lbl, i), [s])]
    drop_regions(prog)
    return out
