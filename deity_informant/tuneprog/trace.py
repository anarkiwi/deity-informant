"""S1 -- the tracer: init plus ``n`` ticks of one entry, and the :class:`Trace` it makes.

:class:`Tracer` drives :class:`~.tracevm.TraceVM` through ``init(song)`` and then
tick after tick, hashing the write footprint of each so a state repeat is a
periodicity witness, and pickles itself so a long run splits into CPU-budget
chunks. :func:`run_trace` is the one-call form.

Sites are keyed by :func:`~.tracedata.site_key`, so two executions of one pc merge
only when they differ in cell bytes; a variant with a different *fixed* operand is
a separate site with its own access sets. A cell is an instruction byte *any*
traced procedure writes, init included, so an operand init patches between two
executions is one site that loads it, not two sites with two constants.

An NMI taken between two ticks interrupts the host, so its pushed return address
is the host's idle pc: ``init``'s own ``JMP *`` where the tune has one, and
:data:`IDLE_PC` -- a convention, because no host model here says where else the
machine waits -- where it does not.
"""

from __future__ import annotations

import pickle
from array import array
from dataclasses import replace
from pathlib import Path

import numpy as np

from ..lifter import lift
from .. import c64
from . import nmi as N
from .machine import (
    Entry,
    FRAME,
    STATUS,
    Refusal,
    entry_frame,
    init_runner,
    kernal_mapped,
    vector_gate,
)
from .tracedata import Footprint, Stream, Trace, site_key
from .tracesite import S_IDX, S_N, S_PH, S_PH0, S_RS, S_WS
from .tracevm import PH_PLAY, TraceVM

CALL_BUDGET = 400_000
VECTOR_BYTES = set(c64.IRQ_VEC) | set(c64.HW_IRQ_VEC)
VERSION = 7  # resume-state layout; an older pickle restarts rather than resumes
IDLE_PC = 0x0000  # the idle pc convention for a host with no ``JMP *`` of its own
IDLE_INDEX = 0xFFFFFFFF  # instruction index of an NMI no tick was running at
# VM register slot -> SLEIGH register name, for the post-init CPU state
CPU_REGS = {0: "A", 1: "X", 2: "Y", 3: "S", 8: "C", 9: "Z", 10: "I", 11: "D", 13: "V", 14: "N"}
NMI_COLS = ("call", "insn", "cyc", "addr", "st", "sp", "p", "pc", "a", "x", "y")
NMI_TYPES = (np.uint32,) * 3 + (np.uint16, np.uint32) + (np.uint8,) * 2 + (np.uint16,)
NMI_TYPES += (np.uint8,) * 3


class Tracer:
    """Drives init and ``n`` ticks of one entry under :class:`TraceVM`."""

    def __init__(self, image, entry, song=None, policy="record", inputs=None, override=None):
        self.image = image
        self.entry = entry
        self.song = image.startsong - 1 if song is None else song
        self.vm = TraceVM(image.mem, image, policy=policy, inputs=inputs, override=override)
        self.cache = {}
        self.image_post_init = None
        self.cycles_init = None
        self.post_init_regs = None
        self.calls_done = 0
        self.idle = None  # where an init that never returns sits: its ``JMP *``
        self.period = None
        self.first_repeat = None
        self.free = Stream()
        self.full = Stream()
        self.fp = Footprint()
        self.nmi = None
        self.nmi_addrs = []  # every handler the line dispatched to, in first-seen order
        self.nmilog = tuple(array(t) for t in "IIIHIBBHBBB")  # the preemption schedule
        self._bind()

    def _bind(self):
        """Freeze what every tick needs from the entry: the frame is fixed once it settles."""
        e = self.entry
        self.tick = (
            e.kind == "sub",
            e.addr,
            e.cycles_per_tick,
            e.kernal,
            entry_frame(e),
            "video" in e.source,
        )

    def run_init(self, budget=None):
        vm = self.vm
        vm.reg[0], vm.reg[1], vm.reg[2] = self.song, 0, 0
        vm.push_frame(None, 0x0002, self.image.init)
        kw = {} if budget is None else {"budget": budget}
        self.idle = init_runner(vm, self.image.init, self.cache, lift, **kw)
        self.nmi = N.entry(vm.cia[1], vm.mem)
        if self.nmi is not None:
            vm.nmi_at = N.STALE
        vm.clear_frames()
        vm.enter_play()
        if self.entry.kind == "irq":
            self.entry = self._settle()
            if not any(self.image.lo <= a < self.image.hi for a in (0xEA31, 0xEA81, 0xFEBC)):
                c64.install_kernal_irq_stubs(vm)
            self._bind()
        vm.invalidate()  # init's own writes and the stubs went in behind the step loop
        self.image_post_init = bytes(vm.mem)
        self.cycles_init = vm.cycles  # where tick 0's frame starts
        self.post_init_regs = {n: int(vm.reg[i]) for i, n in CPU_REGS.items()}
        return self

    def _settle(self):
        """The entry the machine really has, now that init has had the 6510 port.

        :func:`~.machine.find_entries` decides on the pre-init image, so a tune
        whose init banks the KERNAL out was handed a frame the machine never
        pushes. Where init installed a vector the gate is re-run against what it
        wrote; otherwise only the port's verdict on the frame settles.
        """
        img, vm = self.image, self.vm
        wrote = vm.written_init & VECTOR_BYTES
        e = self._cadence()
        if not wrote:
            return replace(e, kernal=kernal_mapped(vm.mem))
        vec, kernal = vector_gate(vm.mem, wrote, (img.lo, img.hi))
        handler = c64.read_vector(vm.mem, vec)
        if not handler:
            raise Refusal("no entry", "vector $%04X is installed but null" % vec)
        return replace(e, addr=handler, kernal=kernal)

    def _cadence(self):
        """The tick period the traced machine leaves, where the container only guessed.

        A ``*_host_cia`` source is what :func:`~.machine._cadence` returns when the
        tune programs no interrupt of its own; a raster IRQ it armed is one, and
        only the traced machine sees it -- ``$D01A`` with no ``$D012`` write is
        invisible to the init trace the guess reads.
        """
        e = self.entry
        std = e.source[: -len("_host_cia")]
        if not e.source.endswith("_host_cia") or std not in FRAME:
            return e
        d01a = next((v for a, v, _c in reversed(self.vm.init_writes) if a == 0xD01A), 0)
        if not d01a & 1:
            return e
        cycles, source = FRAME[std]
        return replace(e, cycles_per_tick=cycles, source=source)

    def run_calls(self, n, budget=CALL_BUDGET):
        for _ in range(n):
            self._one_call(budget)
        return self

    def _one_call(self, budget):
        vm = self.vm
        reg = vm.reg
        sub, addr, cpt, kernal, frame, video = self.tick
        vm.begin_tick(self.calls_done)
        if vm.sep is not None:
            vm.sep.begin()  # the idle NMIs of the last tick close its final window
        start = reg[3]
        # the interrupt keeps its own grid: a tick an NMI made overrun delays the
        # next one, it does not move the frame the writes after it are attributed to
        c0 = self.cycles_init + self.calls_done * cpt
        if vm.cycles < c0:
            vm.cycles = c0
        vm._push(0x00)
        if sub:
            vm._push(0x01)
            vm.push_frame(None, 0x0002, addr)
        else:
            if kernal_mapped(vm.mem) != kernal:
                # the frame is the tick's contract: it has to hold at every tick
                raise Refusal("port moved", "call %d changed the dispatch" % self.calls_done)
            vm._push(0x00)
            for what in frame:
                if what is STATUS:
                    vm._push_status()
                else:
                    vm._push(reg[what])
            vm.push_frame(None, 0x0000, addr)
            reg[10] = 1
            if video:
                vm.vicirq = 0x81  # a raster IRQ has fired: handlers poll $D019
        pc = addr
        step = vm.step
        cache = self.cache
        for i in range(budget + 1):
            if reg[3] >= start:
                break
            if vm.cycles >= vm.nmi_at:
                pc = self._nmi(pc, i)
            pc = step(pc, cache, lift)
        else:
            raise Refusal("play runaway", "call %d at $%04X" % (self.calls_done, pc))
        vm.clear_frames()
        end = c0 + cpt
        if vm.nmi_at != N.NEVER:
            self._idle(end, budget)
        if vm.cycles < end:
            vm.cycles = end
        self._hash()
        self.calls_done += 1

    def _nmi(self, pc, index):
        """Take the CIA #2 NMI due at this instruction boundary, or re-date it."""
        vm = self.vm
        cia = vm.cia[1]
        at = cia.edge_at(vm.cycles)
        if at is None or at > vm.cycles:
            vm.nmi_at = N.NEVER if at is None else at
            return pc
        N.check(cia)
        return self._enter_nmi(pc, index)

    def _enter_nmi(self, pc, index):
        """Push the 6510's NMI frame and enter the handler the live vector names."""
        vm = self.vm
        handler = self._handler()
        vm.cia[1].raise_line()
        vm.nmi_at = N.NEVER  # the line stays asserted until an ICR read releases it
        vm.cycles += N.dispatch_cycles(vm.mem)
        sp, status = vm.reg[3], vm._status()
        if vm.sep is None:
            vm.sep = N.Separable()
        vm.sep.enter(sp, handler)
        pc &= 0xFFFF
        vm._push(pc >> 8)
        vm._push(pc & 0xFF)
        vm._push(status)
        vm.reg[10] = 1
        vm.push_frame(None, pc, handler)
        r = vm.reg
        row = (self.calls_done, index, vm.cycles, handler, vm.tick_stores(), sp, status, pc)
        for col, v in zip(self.nmilog, row + (r[0], r[1], r[2])):
            col.append(v & 0xFFFFFFFF)
        return handler

    def _handler(self):
        """The address this NMI enters, read from the live vector as the 6510 reads it.

        A vector the handlers repoint is one schedule with several entries, so each
        address it takes is an entry of its own and the log says which one ran.
        """
        vm = self.vm
        vec, handler = N.vector(vm.mem)
        if not handler:
            raise Refusal("nmi vector banked out", "$%04X carries no handler" % vec)
        if self.nmi is None:
            found = N.entry(vm.cia[1], vm.mem)
            self.nmi = Entry("nmi", handler, 0, "") if found is None else found
        if handler not in self.nmi_addrs:
            self.nmi_addrs.append(handler)
        return handler

    def _idle(self, end, budget):
        """Run the NMIs the host's idle time before the next tick holds.

        The play routine has returned, so the interrupted program is the host's own
        idle loop, which is ``init``'s ``JMP *`` where it has one; each handler runs
        to the ``RTI`` that balances its frame, and one that acknowledges early can
        be preempted inside it exactly as in a tick.
        """
        vm, cia = self.vm, self.vm.cia[1]
        step, cache = vm.step, self.cache
        pc, sp = None, 0
        for _ in range(budget + 1):
            if pc is None or vm.reg[3] >= sp:
                if pc is not None:
                    vm.clear_frames()
                at = cia.edge_at(vm.cycles)
                if at is None or at >= end:
                    vm.nmi_at = N.NEVER if at is None else at
                    return
                N.check(cia)
                if vm.cycles < at:
                    vm.cycles = at
                sp = vm.reg[3]
                pc = self._enter_nmi(self.idle or IDLE_PC, IDLE_INDEX)
                continue
            if vm.cycles >= vm.nmi_at:
                pc = self._nmi(pc, IDLE_INDEX)
            pc = step(pc, cache, lift)
        raise Refusal("play runaway", "call %d in the idle NMI" % self.calls_done)

    def _nmi_entries(self):
        """One entry per address the NMI vector took; the first is the entry itself."""
        if self.nmi is None:
            return []
        addrs = self.nmi_addrs or [self.nmi.addr]
        return [replace(self.nmi, addr=a).to_dict() for a in addrs]

    def _hash(self):
        """Hash this tick under both footprints, with and without the stack page.

        Which one a certificate may claim periodicity on depends on a program that
        does not exist yet (:func:`~.stack.eliminate`), so both witnesses are kept.
        """
        vm = self.vm
        buf, n, fbuf, fn = self.fp.gather(vm)
        ninp = len(vm.inputs)
        self.full.hash(buf, n, ninp, self.calls_done)
        self.free.hash(fbuf, fn, ninp, self.calls_done)
        self.period, self.first_repeat = self.full.period, self.full.first_repeat

    def witness(self, free=True):
        """The earliest tick a footprint repeated at, or ``None``.

        A program S4 proved stack-free may claim the page-free witness, so either
        footprint will do; a residual one must claim the page-inclusive repeat.
        """
        streams = (self.full, self.free) if free else (self.full,)
        hits = [s.first_repeat for s in streams if s.first_repeat is not None]
        return min(hits) if hits else None

    # ---- resume ------------------------------------------------------------
    def save(self, path):
        Path(path).write_bytes(pickle.dumps((VERSION, self), protocol=pickle.HIGHEST_PROTOCOL))
        return path

    @staticmethod
    def load(path):
        """The pickled tracer, or ``None`` when an older version wrote it."""
        obj = pickle.loads(Path(path).read_bytes())
        return obj[1] if isinstance(obj, tuple) and obj[0] == VERSION else None

    def __getstate__(self):
        d = dict(self.__dict__)
        d["cache"] = None
        return d

    def __setstate__(self, d):
        self.__dict__.update(d)
        self.cache = {}
        self._bind()

    # ---- result ------------------------------------------------------------
    def trace(self):
        vm = self.vm
        # design S2: an operand byte any traced procedure writes -- in any phase,
        # init included -- is a variable, so it drops out of the site key and the
        # lift loads it. Init-only cells fold back to constants in S4, per phase.
        code = {a for a, c in enumerate(vm.code) if c}
        cells = code & (vm.written_play | vm.written_init)
        sites = {}
        for k, t in vm.blocks.items():
            pc = k[0]
            bb = k[1:]
            key = site_key(pc, bb[0], bb, cells)
            s = sites.get(key)
            if s is None:
                s = sites[key] = {
                    "pc": pc,
                    "opcode": bb[0],
                    "count": 0,
                    "phases": 0,
                    "variants": [],
                    "idx": set(),
                    "reads": {},
                    "writes": {},
                }
            s["count"] += t[S_N]
            s["phases"] |= t[S_PH] | (PH_PLAY if t[S_N] > t[S_PH0] else 0)
            s["variants"].append(bytes(bb))
            if t[S_IDX]:
                s["idx"] |= t[S_IDX]
            for name, src in (("reads", t[S_RS]), ("writes", t[S_WS])):
                for i, a in src.items():
                    s[name].setdefault(i, set()).update(a)
        for s in sites.values():
            s["idx"] = sorted(s["idx"])
            s["variants"].sort()
        jsr_targets = {t for c in vm.calls.values() for t in c["targets"]}
        edges = {k: list(v) for k, v in vm.edges.items()}
        for (_f, _o, t), e in edges.items():
            if t in jsr_targets and e[0] in ("fall", "br_taken", "br_not", "jmp"):
                e[0] = "tail"
        meta = {
            "entry": self.entry.to_dict(),
            "schedule": [self.entry.to_dict()] + self._nmi_entries(),
            "song": self.song,
            "calls": self.calls_done,
            "insns": vm.insn_count(),
            "cycles": vm.cycles,
            "cycles_init": self.cycles_init,
            "period": self.full.period,
            "first_repeat": self.full.first_repeat,
            "period_free": self.free.period,
            "first_repeat_free": self.free.first_repeat,
            "unmatched_rts": vm.unmatched_rts,
            "max_depth": vm.max_depth,
            "post_init_regs": self.post_init_regs,
            **self.image.meta(),
        }
        if self.nmi is not None:
            meta["nmis"] = len(self.nmilog[0])
        if self.idle is not None:
            meta["init_idle"] = self.idle
        return Trace(
            meta=meta,
            image_pre=self.image.mem,
            image_post_init=self.image_post_init or self.image.mem,
            sites=sites,
            edges=edges,
            calls={k: dict(v) for k, v in vm.calls.items()},
            rets={k: dict(v) for k, v in vm.rets.items()},
            summaries=dict(vm.summaries),
            inputs=vm.inputs,
            input_sites=dict(vm.input_sites),
            init_writes=vm.init_writes,
            written_init=set(vm.written_init),
            written_play=set(vm.written_play),
            chip_ops=set(vm.chip_ops),
            cells=cells,
            code=code,
            cell_values={a: set(v) for a, v in vm.wr_values.items() if a in cells},
            jsr_targets=jsr_targets,
            wlog=_arrays(vm.sidlog),
            iolog=_arrays(vm.iolog),
            nmilog=_arrays(self.nmilog, NMI_COLS, NMI_TYPES),
            state_hash=np.frombuffer(self.full.rows, dtype=np.uint64).copy(),
            footprint_size=np.frombuffer(self.full.nrows, dtype=np.uint32).copy(),
            state_hash_free=np.frombuffer(self.free.rows, dtype=np.uint64).copy(),
            footprint_free=np.frombuffer(self.free.nrows, dtype=np.uint32).copy(),
        )


def _arrays(cols, names=("call", "addr", "val", "cyc"), types=None):
    """Column arrays of one log, named and typed."""
    types = types or (np.uint32, np.uint16, np.uint8, np.uint32)
    return {n: np.frombuffer(c, dtype=t).copy() for n, c, t in zip(names, cols, types)}


def run_trace(
    image,
    entry,
    calls,
    song=None,
    policy="record",
    inputs=None,
    override=None,
    resume=None,
    budget=CALL_BUDGET,
):
    """Trace ``calls`` ticks of ``entry`` (init first); returns a :class:`Trace`.

    With ``resume=path`` the tracer state is pickled after each invocation and
    reloaded by the next, so a long run splits into chunks that each stay inside
    a CPU budget; ``calls`` is always the total number of ticks from the start.
    """
    p = Path(resume) if resume else None
    if p is not None and p.exists():
        t = Tracer.load(p)
    else:
        t = Tracer(image, entry, song=song, policy=policy, inputs=inputs, override=override)
        t.run_init()
    t.run_calls(max(0, calls - t.calls_done), budget=budget)
    if p is not None:
        t.save(p)
    return t.trace()
