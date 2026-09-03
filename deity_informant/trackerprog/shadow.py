"""B7 -- the register file a tune's writes land in, and the flush that empties it.

Section 3.1's ``meta.shadow``. T0 states, of every write that does not reach the
chip, the image it lands in and the ``delta`` to the chip's own base; the write
that copies the whole file is the flush, and its order is one tick's own.
"""

from __future__ import annotations

from collections import namedtuple

from ..tuneprog.graph import cfg, idoms, natural_loops, preds_of
from ..tuneprog.ir import Store
from . import interp, region
from .universal import REGNAME

SIDBASE = 0xD400

Shadow = namedtuple("Shadow", "rid base size registers blocks")


class _Flush(interp.Player):
    """One tick of the certified program, with each chip write's own site kept."""

    def __init__(self, prog):
        super().__init__(prog, region.Fetch())
        self.io = []

    def iostore(self, a, v, src):
        self.io.append((src, a))
        super().iostore(a, v, src)


def _order(prog, pc):
    """The registers the flush sends, in the order it sends them."""
    p = _Flush(prog).run_init()
    p.tick()
    out = []
    for src, a in p.io:
        name = REGNAME.get(a - SIDBASE)
        if src == pc and name is not None and name not in out:
            out.append(name)
    return out


def _blocks(p, pc):
    """The flush's own blocks: the store's, and the loop that turns it, whole."""
    got = {l for l, b in p.blocks.items() for s in b.stmts if type(s) is Store and s.src == pc}
    if not got:
        return frozenset()
    g = cfg(p)
    out = set(got)
    for h, (body, _lat) in natural_loops(g, idoms(p, g), preds_of(p)).items():
        if got & body and len(body) < len(p.blocks):
            out |= body | {h}
    return frozenset(out)


def of(t0, prog, view):
    """``Shadow`` where T0 names one image every write of the tune lands in."""
    imgs = {
        (w["image"]["region"], w["image"]["delta"], w["image"]["flush_pc"])
        for w in t0.get("writes") or ()
        if w.get("image")
    }
    if len(imgs) != 1:
        return None
    rid, delta, pc = imgs.pop()
    r = view.by_id().get(rid)
    if r is None or r.base != (SIDBASE - delta) & 0xFFFF:
        return None
    pc = int(pc.lstrip("$"), 16)
    regs, blocks = _order(prog, pc), _blocks(prog.procs[prog.meta["tick_proc"]], pc)
    return Shadow(rid, r.base, r.size, regs, blocks) if regs and blocks else None


def seed(img, sh):
    """``state0.shadow``: the image the post-init state left, register by register."""
    return [int(img[sh.base + r]) for r in range(sh.size)]
