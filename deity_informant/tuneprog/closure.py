"""Bounded static closure of the branch directions the trace never took.

Where the post-init image states what an untaken direction does, its instructions
join the trace as zero-coverage sites and edges and the front end builds them as
code no execution covers; elsewhere the walk stops and the trap survives.
"""

from __future__ import annotations

from collections import Counter

from ..lifter import lift
from .cfg import branch_arms
from .ir import STACK_HI, STACK_LO, Trap, succs
from .tracedata import site_key

PH_PLAY = 2
SPREG = 3  # the stack pointer's slot in the register file
_TAIL = ("fall", "br_taken", "br_not", "jmp")  # kinds an edge into an entry promotes


# ---- the address abstraction -------------------------------------------------
def _mask(w):
    return (1 << (8 * w)) - 1


def _iv(vn, seen, holes=()):
    """The interval a varnode holds: a constant is exact, anything else its width.

    A copy hole (:mod:`.copymerge`) is exact too -- the column's values are the
    only ones the copies name.
    """
    if vn[0] == "c":
        return vn[1], vn[1]
    if vn[0] == "u":
        return seen.get(vn[1], (0, _mask(vn[2])))
    if vn[0] == "h" and vn[1] in holes:
        return holes[vn[1]]
    return 0, _mask(vn[2])


def _apply(mn, ins, seen, w, holes=()):
    """The interval one P-Code op computes, or its full width when it is not affine."""
    m = _mask(w)
    a = _iv(ins[0], seen, holes)
    b = _iv(ins[1], seen, holes) if len(ins) > 1 else (0, 0)
    if mn in ("COPY", "INT_ZEXT"):
        return a
    if mn == "INT_ADD":
        r = (a[0] + b[0], a[1] + b[1])
    elif mn == "INT_SUB":
        r = (a[0] - b[1], a[1] - b[0])
    elif mn == "INT_LEFT":
        r = (a[0] << b[0], a[1] << b[1])
    elif mn == "INT_OR":  # x | y >= max(x, y) and < 2 ** max(bit lengths)
        r = (max(a[0], b[0]), (1 << max(a[1].bit_length(), b[1].bit_length())) - 1)
    elif mn == "INT_AND":
        r = (0, min(a[1], b[1]))
    else:
        return 0, m
    return r if 0 <= r[0] <= r[1] <= m else (0, m)


def envelopes(ops, src_map=None, holes=()):
    """``{op index: (lo, hi)}`` -- the addresses every memory op in ``ops`` can touch.

    An absolute access is exact, an indexed one spans its index domain, a pointer
    dereference spans the address space and ``$0100 | SP`` spans the stack page.
    """
    seen, out = {}, {}
    for i, (mn, res, ins) in enumerate(ops):
        if mn in ("LOAD", "STORE"):
            lo, hi = _iv(ins[0], seen, holes)
            w = ins[1][2] if mn == "STORE" else res[2]
            j = src_map[i] if src_map is not None else i
            if j >= 0:  # a residualised op is nobody's access: -1 is not a key
                out[j] = (lo, min(0xFFFF, hi + w - 1))
        if mn != "STORE" and res is not None and res[0] == "u":
            seen[res[1]] = _apply(mn, ins, seen, res[2], holes)
    return out


def static_resolver(ls, fam=None):
    """Access typing for a closed site: ``chk`` -- no accessor -- over the stated envelope.

    The trace never saw the access, which is what ``chk`` says; the envelope comes
    from the image instead, which is what keeps :mod:`.frames` able to place it.
    """
    holes = {} if fam is None else {c: (min(v), max(v)) for c, (_w, v) in enumerate(fam.cols)}
    env = envelopes(ls.ops, ls.src_map, holes)

    def resolve(i, size, _store):
        lo, hi = env.get(i, (0, 0xFFFF))
        return "chk", lo, max(hi, lo + size - 1), -1

    return resolve


def _stacky(ops, env):
    """True when the instruction can see the machine stack (bar ``JSR``/``RTS``/``RTI``).

    :func:`~.stack.eliminate` must keep proving the program stack-free: a residual
    stack changes the write footprint the certificate claims periodicity on. The
    exemption is the lifter's: ``JSR``/``RTS``/``RTI`` push and pop in ``ctrl``,
    not in ops, so they name no ``SP`` and :mod:`.build` writes their frames.
    """
    if any(
        vn is not None and vn[0] == "r" and vn[1] == SPREG
        for _mn, res, ins in ops
        for vn in (res, *ins)
    ):
        return True
    return any(lo <= STACK_HI and hi >= STACK_LO for lo, hi in env.values())


# ---- the walk ----------------------------------------------------------------
class _Closure:
    """One pass: decode from every untaken direction, then wire what was decoded."""

    def __init__(self, trace):
        self.trace = trace
        self.img = trace.image_post_init
        self.lo, self.hi = trace.meta["load"]
        self.have = {k[0] for k in trace.sites}
        # a byte any decompiled procedure writes is not a byte the image states,
        # whether or not the trace ever executed it (then it is already a cell)
        self.written = trace.written_play | trace.written_init
        self.recs = {}
        self.bad = set()
        self.stops = Counter()

    def run(self):
        seeds = list(self._seeds())
        for _pc, _op, target, _kind in seeds:
            self._reach(target)
        self._settle()
        return self._emit(seeds)

    def _seeds(self):
        """``(pc, opcode, target, kind)`` for every branch direction with no edge."""
        seen = set()
        for key, site in self.trace.sites.items():
            pc, op = key[0], key[1]
            arms = branch_arms(None, site, pc, op) if (pc, op) not in seen else None
            if arms is None:
                continue
            seen.add((pc, op))
            stated = ((pc + 1) & 0xFFFF) not in self.written  # else a writer picks the arm
            for arm, kind in ((arms[0], "br_taken"), (arms[1], "br_not")):
                if (pc, op, arm) not in self.trace.edges and (stated or kind == "br_not"):
                    yield pc, op, arm, kind

    def _reach(self, start):
        work = [start]
        while work:
            pc = work.pop()
            if pc in self.have or pc in self.recs or pc in self.bad:
                continue
            rec = self._decode(pc)
            if rec is None:
                self.bad.add(pc)
                continue
            self.recs[pc] = rec
            work.extend(_succ_pcs(rec, pc))

    def _decode(self, pc):
        """The instruction at ``pc``, or ``None`` (recording why) where the image is silent.

        Silent means: outside the load band, a byte a writer decides, an access the
        stack could see, ``BRK``/``JAM``, a computed target, or a ``JSR`` no traced
        procedure answers. A patched operand is a cell, so ``smc_cell`` covers it.
        """
        if not self.lo <= pc < self.hi:
            return self._stop("outside_image")
        try:
            rec = lift(self.img, pc)
        except NotImplementedError:
            return self._stop("undecodable")
        end = pc + rec["len"]
        if end > self.hi:
            return self._stop("outside_image")
        if any(a in self.written for a in range(pc, end)):
            return self._stop("smc_cell")
        if _stacky(rec["ops"], envelopes(rec["ops"])):
            return self._stop("stack")
        kind = rec["ctrl"][0]
        if kind in ("brk", "jam", "jmpind"):
            return self._stop(kind)
        if kind == "jsr" and rec["ctrl"][1] not in self.trace.jsr_targets:
            return self._stop("foreign_jsr")
        return rec

    def _stop(self, why):
        """Record a stop and answer ``None``: the walk leaves the trap where it is."""
        self.stops[why] += 1

    def _settle(self):
        """A ``JSR`` needs its return point: drop the ones whose continuation stopped."""
        while True:
            drop = [
                pc
                for pc, rec in self.recs.items()
                if rec["ctrl"][0] == "jsr" and not self._has(pc + rec["len"])
            ]
            if not drop:
                return
            for pc in drop:
                del self.recs[pc]
                self.bad.add(pc)
                self.stops["jsr_return"] += 1

    def _has(self, pc):
        pc &= 0xFFFF
        return pc in self.have or pc in self.recs

    def _edge(self, pc, op, to, kind):
        """Add the edge when its target has a node; an edge into an entry is a tail call."""
        if not self._has(to) or (pc, op, to) in self.trace.edges:
            return False
        if to in self.trace.jsr_targets and kind in _TAIL:
            kind = "tail"
        self.trace.edges[(pc, op, to)] = [kind, 0]
        return True

    def _emit(self, seeds):
        t = self.trace
        for pc, rec in sorted(self.recs.items()):
            n = rec["len"]
            b = bytes(self.img[pc : pc + n])
            op, nxt = b[0], (pc + n) & 0xFFFF
            t.sites[site_key(pc, op, b, t.cells)] = {
                "pc": pc,
                "opcode": op,
                "count": 0,
                "closed": True,  # what the image stated, not what ran: see cfg._node
                "phases": PH_PLAY,
                "variants": [b],
                "idx": [],
                "reads": {},
                "writes": {},
            }
            t.code.update(range(pc, pc + n))
            self._wire(pc, op, nxt, rec["ctrl"])
        closed = sum(self._edge(pc, op, to, kind) for pc, op, to, kind in seeds)
        return {
            "arms": len(seeds),
            "closed": closed,
            "instructions": len(self.recs),
            "stops": dict(sorted(self.stops.items())),
        }

    def _wire(self, pc, op, nxt, ctrl):
        """The edges (and the call/return record :mod:`.cfg` reads) of one closed pc."""
        if ctrl[0] == "next":
            self._edge(pc, op, nxt, "fall")
        elif ctrl[0] == "br":
            self._edge(pc, op, ctrl[3], "br_taken")
            self._edge(pc, op, ctrl[4], "br_not")
        elif ctrl[0] == "jmp":
            self._edge(pc, op, ctrl[1], "jmp")
        elif ctrl[0] == "jsr":
            self._edge(pc, op, ctrl[1], "jsr")
            self.trace.calls[(pc, op)] = {
                "targets": Counter({ctrl[1]: 0}),
                "ret_pc": nxt,
                "count": 0,
            }
        else:  # rts | rti
            self.trace.rets[(pc, op)] = {
                "matched": Counter(),
                "targets": Counter(),
                "unmatched": 0,
                "loose": Counter(),
            }


def _succ_pcs(rec, pc):
    ctrl = rec["ctrl"]
    if ctrl[0] == "br":
        return [ctrl[3], ctrl[4]]
    if ctrl[0] == "jmp":
        return [ctrl[1]]
    return [(pc + rec["len"]) & 0xFFFF] if ctrl[0] in ("next", "jsr") else []


def close_static(trace):
    """Close what the image states of every untaken branch direction, in place.

    Mutates ``trace`` -- the front end is driven entirely by its sites and edges --
    and records the statistics under ``meta['static_closure']``; idempotent.
    """
    stats = trace.meta.get("static_closure")
    if stats is None:
        stats = trace.meta["static_closure"] = _Closure(trace).run()
    return stats


def closed_blocks(proc):
    """The labels of ``proc`` only a statically closed path reaches.

    Marked blocks plus everything downstream of them alone -- the split edges and
    prologues later passes make out of a closed edge belong to the closure too.
    """
    seeds = {l for l, b in proc.blocks.items() if b.closed and not b.count and not any(b.cover)}
    if not seeds:
        return set()  # a program the walk never ran on has no closed path to be off
    live, work = set(), [proc.entry]
    while work:
        lbl = work.pop()
        if lbl in live or lbl in seeds or lbl not in proc.blocks:
            continue
        live.add(lbl)
        work.extend(succs(proc.blocks[lbl].term))
    return set(proc.blocks) - live


def report(prog):
    """Static-closure accounting for a built program (JSON-safe).

    Empty for a program the walk never ran on. ``blocks``/``statements`` are what
    only a closed path reaches, ``untaken`` the trap blocks left under that name,
    ``frontier`` where a closed path ran out of stated answers.
    """
    stats = prog.meta.get("static_closure")
    if stats is None:
        return {}
    blocks = stmts = live = untaken = frontier = 0
    for p in prog.procs.values():
        shut = closed_blocks(p)
        for lbl, b in p.blocks.items():
            if lbl in shut:
                blocks += 1
                stmts += len(b.stmts)
            else:
                live += len(b.stmts)
            if type(b.term) is Trap:
                untaken += b.term.why == "untaken"
                frontier += b.term.why == "unstated"
    d = dict(stats)
    d.update(
        blocks=blocks,
        statements=stmts,
        verified_statements=live,
        untaken=untaken,
        frontier=frontier,
    )
    return d
