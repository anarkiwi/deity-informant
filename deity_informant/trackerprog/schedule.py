"""B6 -- the schedule recovered from the certified tick: is it derivable?

The hypothesis of docs/trackerprog-backlog.md B6: a voice's phases are the
maximal segments of the tick's reverse postorder between the T0 commit sites,
with the fetch regions as ``row``. This states it as a procedure and reports what
it derives -- ``voice_order``, ``commit_order``, ``tempo``, ``tick``.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..tuneprog.accguard import guardpath
from ..tuneprog.graph import cfg, idoms, natural_loops, preds_of, rpo, succs
from ..tuneprog.ir import Bin, Const, Let, Load, Store, Var
from ..tuneprog.irwalk import addr_split, walk
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
    step: int = -1
    inloop: bool = True  # whether the counter the tick steps is a voice's own
    boundary: tuple = ()  # ((condition, its truth), ...): the guard the fetch stands under
    resets: tuple = ()  # ((store, ((condition, its truth), ...)), ...): section 3.6's clauses
    reads: frozenset = frozenset()  # the names that read the counter after its own step
    spent: tuple = ()  # the conditions ``rate`` and ``phase`` already state
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
            "tempo.step": self.step,
            "tempo.resets": len(self.resets),
            "tempo.boundary_terms": len(self.boundary),
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


def _defs(p):
    """``{name: value}``: what each ``Let`` of one procedure binds."""
    return {s.n: s.e for b in p.blocks.values() for s in b.stmts if type(s) is Let}


def _decrement(s):
    """``address`` where a store is ``cell = cell - 1`` at a constant base, else ``None``."""
    got = _stepped(s, None)
    return None if got is None or got[1] != -1 else got[0]


def _stepped(s, defs, vidx=frozenset()):
    """``(address, step)`` where a store is ``cell = cell + k`` at a constant base.

    The value's own name must read that very cell, so a store of one counter's
    step into another is not a step of the cell it lands in.
    """
    if type(s) is not Store or s.cls != "ram":
        return None
    base, idx = addr_split(s.a)
    v = s.v
    if base is None or not (idx is None or (type(idx) is Var and idx.n in vidx)):
        return None
    if type(v) is not Bin or v.op not in ("+", "-") or type(v.b) is not Const:
        return None
    if type(v.a) is not Var or (defs is not None and _reads(defs, v.a) != {base}):
        return None
    return base, -v.b.v if v.op == "-" else v.b.v


def _reads(defs, e, depth=3):
    """The constant base addresses one expression reads, through the names it names."""
    out = set()
    for x in walk(e):
        if type(x) is Load and x.cls in ("ram", "chk"):
            base = addr_split(x.a)[0]
            if base is not None:
                out.add(base)
        elif type(x) is Var and depth and x.n in defs:
            out |= _reads(defs, defs[x.n], depth - 1)
    return out


def counters(p, vidx):
    """``{address: (label, the Let that read it, the store that steps it, step)}``.

    One counter is one address a store moves by a constant: what section 3.6's
    row clock is a value of, and what a tick-level divider is another.
    """
    defs, out = _defs(p), {}
    for lbl, b in p.blocks.items():
        for s in b.stmts:
            got = _stepped(s, defs, vidx)
            if got is None or got[0] in out:
                continue
            pre = next(
                (x for x in b.stmts if type(x) is Let and _reads(defs, x.e, 0) == {got[0]}), None
            )
            out[got[0]] = (lbl, pre, s, got[1])
    return out


def clock_of(p, vidx, entry, guards=None):
    """Section 3.6's row clock, from the guard the fetch's own path stands under.

    The fetch is read at a step of one counter, so the counter is the address a
    term of that path reads and a store moves by a constant. A term comparing
    such a counter with the cell **its own reload reads** is a **divider**
    (``rate``, ``phase``) -- that compare is the reload and not a boundary -- and
    of the rest the clock is the counter whose own step stands under the fewest
    guards, the outer counter of a nest being the tick's.
    """
    guards = guardpath(p, sites=True) if guards is None else guards
    defs, steps = _defs(p), counters(p, vidx)
    terms = [(d, c, t, _reads(defs, c)) for d, c, t, _w in guards.get(entry, ())]
    div = {
        a
        for _d, c, _t, seen in terms
        if type(c) is Bin and len(seen & set(steps)) == 1 and len(seen) > 1
        for a in seen & set(steps)
        if _reloaded_from(p, defs, steps, a, seen - {a})
    }
    got = [a for _d, _c, _t, seen in terms for a in sorted(seen & set(steps)) if a not in div]
    if not got:
        return None
    base = min(got, key=lambda a: (len(guards.get(steps[a][0], ())), -got.count(a), a))
    lbl, pre, store, step = steps[base]
    keep = tuple((c, t) for _d, c, t, seen in terms if not seen & div)
    spent = tuple(c for _d, c, _t, seen in terms if seen & div)
    return base, lbl, pre, store, step, keep, div, spent


def _reloaded_from(p, defs, steps, addr, others):
    """Whether a counter is refilled from one of the cells a term compares it with.

    Section 3.6's ``rate`` is a counter's own reload plus one, so the compare that
    states a divider is the compare against that reload. A counter a path tests
    against some *other* cell -- the lead a fetch runs ahead by -- is the clock
    itself, and the test is its own boundary.
    """
    got = _reload(p, addr, steps[addr][2])
    return got is not None and bool(_reads(defs, got[0].v) & others)


def resets_of(p, addr, skip, outside, guards):
    """Section 3.6's ``reset`` clauses: what the tick does to the counter at its end.

    A store the *tick* makes outside the voice loop is the clock's own; one a
    voice makes is a row of that voice's phase like any other.
    """
    out = []
    for lbl in [l for l in rpo(p) if l not in outside]:
        for s in p.blocks[lbl].stmts:
            if type(s) is not Store or s is skip or addr_split(s.a)[0] != addr:
                continue
            out.append((s, tuple((c, t) for _d, c, t, _w in guards.get(lbl, ()))))
    return tuple(out)


def _reload(p, addr, skip):
    """The store that fills a counter again, where one does: section 3.6's reset."""
    for lbl, b in p.blocks.items():
        for s in b.stmts:
            if type(s) is not Store or s is skip or addr_split(s.a)[0] != addr:
                continue
            if type(s.v) in (Var, Load):
                return s, lbl
    return None


def divider_of(p, div):
    """The tick-level counter a reload gates: ``(address, reload)`` -- section 3.6's rate.

    ``div`` is what :func:`clock_of` read off the fetch's own guard path: a
    counter a term compares with a second cell, which is the reload that says
    how many ticks one step of the clock is.
    """
    for addr in sorted(div):
        for b in p.blocks.values():
            for s in b.stmts:
                if _decrement(s) != addr:
                    continue
                got = _reload(p, addr, s)
                if got is not None:
                    return addr, got[0], got[1]
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
    guards = guardpath(p, sites=True)
    got = clock_of(p, sch.vidx, entry, guards)
    if got:
        base, lbl, pre, store, step, keep, div, spent = got
        sch.clock = (lbl, pre, store, base)
        sch.step, sch.boundary, sch.inloop, sch.spent = step, keep, lbl in body, spent
        sch.reads = frozenset(
            x.n
            for l, b in p.blocks.items()
            for x in b.stmts
            if type(x) is Let and x is not pre and _reads(_defs(p), x.e, 0) == {base} and l in body
        )
        # a clause is the clock's own where the clock is the tick's: a counter a
        # voice steps is refilled by a row of that voice's phase, not by the clock
        sch.resets = () if sch.inloop else resets_of(p, base, store, body, guards)
        got = divider_of(p, div)
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
