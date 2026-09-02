"""B6 -- the schedule recovered from the certified tick: is it derivable?

The hypothesis of docs/trackerprog-backlog.md B6: a voice's phases are the
maximal segments of the tick's reverse postorder between the T0 commit sites,
with the fetch regions as ``row``. This states it as a procedure and reports what
it derives -- ``voice_order``, ``commit_order``, ``tempo``, ``tick``.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..tuneprog.graph import cfg, idoms, natural_loops, preds_of, rpo, succs
from ..tuneprog.ir import Bin, Const, If, Let, Load, Store, Var
from ..tuneprog.irwalk import addr_split
from .emit import commit_order

EDGE = ("ctrl", "ad", "sr")


@dataclass
class Schedule:
    """One tune's derived schedule: the voice loop, its phases and its clock."""

    proc: str
    head: str = ""
    body: frozenset = frozenset()
    vidx: frozenset = frozenset()
    voice_order: tuple = ()
    commit_order: tuple = ()
    segments: list = field(default_factory=list)
    tick: list = field(default_factory=list)
    clock: tuple = ()  # (block, the Let that reads the counter, the store that steps it)
    boundary: object = None
    divider: tuple = ()  # (cell address, reload value)
    rate: int = 1
    phase: int = 0
    row_consumes_tick: bool = True

    def datums(self):
        """The schedule as the rows B6 compares, one datum a row."""
        return {
            "voice_order": list(self.voice_order),
            "commit_order": list(self.commit_order),
            "tick": list(self.tick),
            "row_consumes_tick": self.row_consumes_tick,
            "tempo.rate": self.rate,
            "tempo.phase": self.phase,
            "segments": [(n, len(b)) for n, b in self.segments],
        }


def voice_loop(prog, proc, inside):
    """The loop one pass over the voices is: the outermost holding the fetch blocks."""
    p = prog.procs[proc]
    g = cfg(p)
    loops = natural_loops(g, idoms(p, g), preds_of(p))
    got = [(h, v) for h, v in loops.items() if inside & v[0]]
    return max(
        got,
        key=lambda x: (len(inside & x[1][0]), len(x[1][0])),
        default=(None, (frozenset(), frozenset())),
    )


def induction(p, head, latches):
    """The names a latch rebinds and a pre-header binds: the loop's own index."""
    lat = {s.n for l in latches for s in p.blocks[l].stmts if type(s) is Let}
    pre = {
        s.n
        for l, b in p.blocks.items()
        if l not in latches and head in succs(b.term)
        for s in b.stmts
        if type(s) is Let
    }
    return frozenset(lat & pre)


def copies(p, seed):
    """The closure of a name set under ``Let(n, Var(m))``: one value, many names."""
    out = set(seed)
    for _ in range(len(p.blocks)):
        more = {
            s.n
            for b in p.blocks.values()
            for s in b.stmts
            if type(s) is Let and type(s.e) is Var and s.e.n in out
        } - out
        if not more:
            return frozenset(out)
        out |= more
    return frozenset(out)


def _decrement(s):
    """``address`` where a store is ``cell = cell - 1`` at a constant base, else ``None``."""
    if type(s) is not Store or s.cls != "ram":
        return None
    base, _idx = addr_split(s.a)
    v = s.v
    if base is None or type(v) is not Bin or v.op != "-":
        return None
    if type(v.b) is not Const or v.b.v != 1 or type(v.a) is not Var:
        return None
    return base


def clock_of(p, body, vidx, entry):
    """The row clock: the block whose counter step decides the fetch, and its guard."""
    for lbl in body:
        b = p.blocks[lbl]
        if type(b.term) is not If or entry not in (b.term.t, b.term.f):
            continue
        for s in b.stmts:
            if type(s) is not Store or s.cls != "ram":
                continue
            base, idx = addr_split(s.a)
            if base is None or not (type(idx) is Var and idx.n in vidx):
                continue
            got = [x for x in p.blocks[lbl].stmts if type(x) is Let and type(x.e) is Load]
            pre = next((x for x in got if addr_split(x.e.a)[0] == base), None)
            return lbl, pre, s, base, b.term.c, entry == b.term.t
    return None


def _reload(p, addr, skip):
    """The store that fills a counter again, where one does: section 3.6's reset."""
    for lbl, b in p.blocks.items():
        for s in b.stmts:
            if type(s) is not Store or s is skip or addr_split(s.a)[0] != addr:
                continue
            if type(s.v) in (Var, Load):
                return s, lbl
    return None


def divider_of(p, body):
    """The tick-level counter a reload gates: ``(address, reload)`` -- section 3.6's rate."""
    for lbl, b in p.blocks.items():
        if lbl in body:
            continue
        for s in b.stmts:
            a = _decrement(s)
            got = None if a is None else _reload(p, a, s)
            if got is not None:
                return a, got[0], got[1]
    return None


def segments(order, fetchblocks, sites):
    """The RPO split of one voice's pass: ``prelude`` before the fetch, ``machine`` after."""
    idx = [i for i, l in enumerate(order) if l in fetchblocks]
    if not idx:
        return [("machine", order)]
    lo, hi = min(idx), max(idx)
    out = []
    if order[:lo]:
        out.append(("prelude", order[:lo]))
    out.append(("row", order[lo : hi + 1]))
    if order[hi + 1 :]:
        out.append(("machine", order[hi + 1 :]))
    del sites
    return out


def tick_list(segs, p, sites):
    """``meta.tick``: each segment, and a ``commit`` where the segment ends a group."""
    out = []
    for name, blocks in segs:
        out.append(name)
        if name != "machine" and any(
            s.src in sites for l in blocks for s in p.blocks[l].stmts if type(s) is Store
        ):
            out.append("commit")
    return out


def derive(prog, proc, fetchblocks, t0, entry):
    """The whole schedule of one certified tick, from the program and T0 alone."""
    p = prog.procs[proc]
    head, (body, latches) = voice_loop(prog, proc, frozenset(fetchblocks))
    sch = Schedule(proc, head=head or "", body=frozenset(body))
    sch.vidx = copies(p, induction(p, head, latches)) if head else frozenset()
    sch.commit_order = commit_order(t0)
    order = [l for l in rpo(p, cfg(p)) if l in body]
    sites = {int(w["site"]["pc"][1:], 16) for w in t0.get("writes") or () if w["register"] in EDGE}
    sch.segments = segments(order, set(fetchblocks), sites)
    sch.tick = tick_list(sch.segments, p, sites)
    got = clock_of(p, body, sch.vidx, entry)
    if got:
        lbl, pre, store, base, cond, taken = got
        sch.clock = (lbl, pre, store, base)
        sch.boundary = (cond, taken)
    got = divider_of(p, body)
    if got:
        sch.divider = (got[0], got[1])
    sch.row_consumes_tick = not _reaches(p, fetchblocks, sch.segments, head)
    return sch


def _reaches(p, fetchblocks, segs, head):
    """Whether the fetch's own exit still runs the machine: a row that spends no tick."""
    first = next((b[0] for n, b in segs if n == "machine"), None)
    seen, stack = set(), [s for l in fetchblocks for s in succs(p.blocks[l].term)]
    while stack:
        l = stack.pop()
        if l in seen or l not in p.blocks or l == head or l in fetchblocks:
            continue
        seen.add(l)
        if l == first:
            return True
        stack += list(succs(p.blocks[l].term))
    return False
