"""Device polling loops of an entry run once (``docs/cgen.md``, *Init*).

A polling loop is a strongly connected set of constant instructions that write nothing, read
constant addresses and have one instruction reading the device: each pass through it is the
same function of the byte the device answers, which :func:`settle` decides from its entry over
the values the device model gives (``deity_informant.devices``), else every byte.
"""

from __future__ import annotations

from dataclasses import dataclass

import networkx as nx
from jennings.devices.mpu6502 import NativeMPU
from jennings.memory import NativeMemory

from .lifter import STATUS_BITS, bus_only, lift

__all__ = ["REGS", "Loop", "flow", "loops", "registers", "settle"]

#: Register-file indices of ``A``, ``X``, ``Y``, ``SP`` and the flags ``NV-DIZC``.
REGS = (0, 1, 2, 3, *(i for i, _ in STATUS_BITS))
_ALL = frozenset(REGS)
_FLAGS = frozenset(i for i, _ in STATUS_BITS)
#: Registers the stack-transfer instructions read (``BRK`` pushes the flags) and write.
_STACK = {
    "jsr": ({3}, {3}),
    "rts": ({3}, {3}),
    "brk": ({3} | _FLAGS, {3, 10}),
    "rti": ({3}, {3} | _FLAGS),
}


def flow(mem, pc):
    """``(use, defs, rec)`` of the instruction at ``pc`` of ``mem``: the registers
    (:data:`REGS` indices) it reads before writing, those it writes, its lifted record."""
    rec = lift(mem, pc)
    use, defs = set(), set()
    dummy = bus_only(rec["ops"])
    for i, (_, out, ins) in enumerate(rec["ops"]):
        if i not in dummy:
            use |= {vn[1] for vn in ins if vn[0] == "r"} - defs
            if out is not None and out[0] == "r":
                defs.add(out[1])
    ctrl = rec["ctrl"]
    if ctrl[0] == "br":
        use |= {ctrl[1][1]} - defs
    u, d = _STACK.get(ctrl[0], ((), ()))
    return frozenset(use | (set(u) - defs)), frozenset(defs | set(d)), rec


@dataclass(frozen=True)
class Loop:
    """A polling loop: its instructions ``pcs``, the one instruction ``at`` reading the
    device at ``addr``, and the registers ``live`` into each instruction (all of them live
    where the loop is left)."""

    pcs: frozenset
    at: int
    addr: int
    live: dict


def _succ(pc, rec):
    """The successors of the instruction ``rec`` at ``pc`` when it may be in a polling loop
    (no store, no ``SP`` write, constant load addresses, no transfer but branch and jump)."""
    ctrl, ops = rec["ctrl"], rec["ops"]
    if ctrl[0] not in ("next", "br", "jmp") or any(mn == "STORE" for mn, _, _ in ops):
        return None
    if any(mn == "LOAD" and ins[0][0] != "c" or out == ["r", 3, 1] for mn, out, ins in ops):
        return None
    return {"next": ((pc + rec["len"]) & 0xFFFF,), "br": ctrl[3:5], "jmp": ctrl[1:2]}[ctrl[0]]


def loops(image, starts, constant, volatile):
    """``{pc: Loop}`` of the polling loops among the instructions at ``starts`` of ``image``
    whose bytes are all in ``constant``, the device the ``volatile`` ``[(lo, hi)]`` ranges."""
    mem, constant = bytes(image), set(constant)
    g, facts = nx.DiGraph(), {}
    for pc in starts:
        use, defs, rec = flow(mem, pc)
        nxt = _succ(pc, rec)
        if nxt is None or any((pc + i) & 0xFFFF not in constant for i in range(rec["len"])):
            continue
        dev = [
            ins[0][1]
            for mn, _, ins in rec["ops"]
            if mn == "LOAD" and any(lo <= ins[0][1] < hi for lo, hi in volatile)
        ]
        facts[pc] = (use, defs, dev, nxt)
        g.add_node(pc)
    g.add_edges_from((pc, s) for pc, f in facts.items() for s in f[3] if s in facts)
    out = {}
    for scc in nx.strongly_connected_components(g):
        pc = next(iter(scc))
        dev = [(p, a) for p in scc for a in facts[p][2]]
        if len(dev) != 1 or (len(scc) == 1 and not g.has_edge(pc, pc)):
            continue
        loop = Loop(frozenset(scc), *dev[0], _live(scc, facts))
        out.update(dict.fromkeys(scc, loop))
    return out


def _live(scc, facts):
    """The registers live into each instruction of the loop ``scc``, all live on leaving."""
    live = dict.fromkeys(scc, frozenset())
    changed = True
    while changed:
        changed = False
        for p in scc:
            use, defs, _, succ = facts[p]
            after = frozenset().union(*(live[s] if s in scc else _ALL for s in succ))
            new = use | (after - defs)
            changed |= new != live[p]
            live[p] = new
    return live


def registers(mpu):
    """``{index: value}`` of :data:`REGS` on ``mpu``."""
    regs = {0: mpu.a, 1: mpu.x, 2: mpu.y, 3: mpu.sp}
    regs.update({i: mpu.p >> b & 1 for i, b in STATUS_BITS})
    return regs


def settle(loop, mem, regs, pc, dev=None):
    """``(byte, varying)`` of ``loop`` entered at ``pc`` with registers ``regs`` on ``mem``:
    the least device byte it leaves on and the registers differing between such bytes, of the
    bytes device state ``dev`` answers (else all); None unless a pass it stays on keeps the
    live registers, it leaves to one instruction and on a set ``dev`` says a read hits."""
    scratch = NativeMemory(bytes(mem[0:0x10000]))
    mpu = NativeMPU(memory=scratch)
    ends = {}
    held = None if dev is None else dev.read(loop.addr)
    for v in range(256) if held is None else sorted(held):
        scratch[loop.addr] = v
        mpu.a, mpu.x, mpu.y, mpu.sp, mpu.pc = regs[0], regs[1], regs[2], regs[3], pc
        mpu.p = 0x30 | sum(regs[i] << b for i, b in STATUS_BITS)
        for _ in loop.pcs:
            mpu.step()
            if mpu.pc == pc or mpu.pc not in loop.pcs:
                break
        else:
            return None
        got = registers(mpu)
        if mpu.pc != pc:
            ends[v] = (mpu.pc, got)
        elif any(got[r] != regs[r] for r in loop.live[pc]):
            return None
    recur = [] if dev is None else dev.recur(loop.addr)
    if (
        len({e for e, _ in ends.values()}) != 1
        or recur
        and not any(r <= ends.keys() for r in recur)
    ):
        return None
    varying = frozenset(r for r in REGS if len({s[r] for _, s in ends.values()}) > 1)
    return min(ends), varying
