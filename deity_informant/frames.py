"""Interrupt frames of a C64 run, from an IRQ or NMI's entry to its RTI, recorded and lifted.

A :class:`FrameHost` points each interrupt's pushed return address at the machine's sentinel, so the
handler's RTI closes the frame's record as a call's return does, and resumes the interrupted code.
"""

from __future__ import annotations

import time

import numpy as np

from . import c64, wholeprog
from .smc.machine import _ENTRY, MachineOracle, weak

__all__ = ["ENTRY", "FrameHost", "check", "lift", "regs"]

#: A frame's entry: the entry address, the interrupted PC and the registers before the interrupt.
ENTRY = np.dtype([("at", "<u2"), ("pc", "<u2")] + [(r, "u1") for r in ("a", "x", "y", "sp", "p")])
_CALL = c64._PHASES.index("call")  # pylint: disable=protected-access
_HALT = {h: i for i, h in enumerate(c64._HALTS)}  # pylint: disable=protected-access


def _sources(events):
    """The interrupt kinds (``video_irq``, ``cia_irq``, ``nmi``, ``brk``) of machine events."""
    return [n[:-6] for _, n in events if n in _ENTRY]


class _Chunk:
    """The records of a stretch of frames in the call queue's packed form."""

    def __init__(self, origin):
        self.origin, self.meta, self.log = origin, bytearray(), bytearray()
        self.idx, self.vals, self.nlog = [], [], []

    def add(self, halt, end, log, idx, vals):
        """Append a record: the halt, end registers ``(a, x, y, sp, p)``, I/O log, RAM changes."""
        self.meta += bytes([_CALL, _HALT[halt], *end])
        for addr, v, w in log:
            self.log += bytes([addr & 0xFF, addr >> 8, v, w])
        self.nlog.append(len(log))
        self.idx.append(idx)
        self.vals.append(vals)

    def records(self):
        """The ``vagrant_moses.smc.Records`` of the chunk."""
        ends = (
            np.cumsum(n, dtype="<u8").tobytes() for n in (self.nlog, [len(v) for v in self.vals])
        )
        log_end, delta_end = ends
        return c64.Records(
            self.origin, bytes(self.meta), bytes(self.log), log_end,
            b"".join(self.idx), b"".join(self.vals), delta_end,
        )  # fmt: skip


class FrameHost(c64._Host):  # pylint: disable=protected-access,too-many-instance-attributes
    """A machine run of an interrupt-driven song's frames (:meth:`drive_frames`): one record
    per frame in :attr:`stream`, entry states in :attr:`frames`, the RAM at the first entry in
    :attr:`image`, interrupts entered inside a frame in :attr:`nested` by frame, and those
    entered outside any (the run is then no sequence of frames) in :attr:`foreign`; :attr:`dirty`
    is set when RAM outside page 1 changed between two frames (the interrupted code does real
    work)."""

    def __init__(self, *args, **kw):
        super().__init__(*args, sentinel=c64.SENTINEL, **kw)
        self.entries, self.nested, self.foreign = [], {}, set()
        self.end, self.dirty = None, False
        self.image, self.chunk, self.resume, self.limit = None, None, 0, 0

    def drive_frames(self, boot, frames, max_cycles=2_000_000):
        """Run ``boot`` (:class:`~deity_informant.c64.Boot`), then ``frames`` video frames of
        interrupts. A frame that jams or runs past ``max_cycles`` ends the run; one the run's
        end cuts is dropped."""
        self.limit = max_cycles
        self.result = MachineOracle.run(
            self, [], frames=frames, call_cycles=max_cycles, **boot.run_kw()
        )
        if self.cur == "call" and (self.m.jammed or self.rec.timed_out):
            self._close("jam" if self.m.jammed else "cycles")
        super()._window()
        self._flush()
        self.records = self.stream
        return self

    def _frames_phase(self, index_frames):
        super()._frames_phase(index_frames)
        if self.cur is not None:
            self._snap("cycles")
        self.m.clear_events()  # pylint: disable=no-member
        self.m.set_trap(weak(self._frame_trap), self._watch(self.m))  # pylint: disable=no-member
        self.m.on_port_write(weak(self._port))  # pylint: disable=no-member

    def _port(self, mc):
        """The CPU port changed: the vectors' targets may be another view of memory."""
        if self.cur != "call":
            mc.watch = self._watch(mc)

    def _watch(self, mc):
        """Where a frame starts (the targets of the IRQ and NMI vectors) or, inside one, ends."""
        if self.cur == "call":
            return {self.sentinel}
        words = ((self._read(mc.bits, a)[0] for a in (v, v + 1)) for v in (0xFFFA, 0xFFFE))
        return {lo | hi << 8 for lo, hi in words}

    def _window(self):
        super()._window()
        if self.cur != "call":
            self._flush()

    def _flush(self):
        """Move the closed records to the stream."""
        if self.chunk is not None and self.chunk.meta:
            self.stream.add(self.chunk.records())
            self.chunk = None

    def _frame_trap(self, mc):
        if self.cur == "call":
            self._close("return")
            mc.mpu.pc = self.resume
            mc.watch = self._watch(mc)
            return True
        entered = _sources(mc.events)
        mc.clear_events()
        if not entered or entered[-1] == "brk":
            self.foreign |= set(entered)
            return False
        self.foreign |= set(entered[:-1])
        self._enter(mc)
        return False

    def _enter(self, mc):
        """Open the record of the frame the machine has just entered."""
        r, sp = mc.mpu, mc.mpu.sp
        at = [0x100 | (sp + k) & 0xFF for k in (1, 2, 3)]
        p, lo, hi = (int(self.ram[a]) for a in at)
        self.resume = lo | hi << 8
        if self.end is not None and not self.dirty:
            diff = self.ram.view(np.uint64) != self.end
            diff[0x20:0x40] = False
            self.dirty = bool(diff.any())
        self.entries.append((r.pc, self.resume, r.a, r.x, r.y, (sp + 3) & 0xFF, p))
        if self.image is None:
            self.image = bytes(self.ram)
        if self.chunk is None:
            self.chunk = _Chunk(bytes(self.ram))
        for a, v in zip(at[1:], (self.sentinel & 0xFF, self.sentinel >> 8)):
            self.rec.host_write(a, v)
        self.entry_sp[r.pc].add(sp)
        self.entry_port[r.pc].add((mc.port_dir, mc.port_data))
        self.cur = "call"
        self.tr.phase(c64.Tracer.CALL, sp)
        self.rec.deadline = mc.cycle + self.limit
        mc.watch = {self.sentinel}

    def _close(self, halt):
        """Close the open frame's record with the machine's registers as they are."""
        if nested := _sources(self.m.events):
            self.nested[len(self.entries) - 1] = sorted(set(nested))
        self.m.clear_events()  # pylint: disable=no-member
        log, idx, vals = self.tr.snap()
        self.tr.phase(c64.Tracer.NONE)
        r = self.m.mpu
        self.chunk.add(halt, (r.a, r.x, r.y, r.sp, r.p), log, idx, vals)
        self.rec.deadline = None
        self.end = np.frombuffer(bytes(self.ram), np.uint64)
        self.cur = None

    @property
    def frames(self):
        """The entry states of the recorded frames (:data:`ENTRY`)."""
        return np.array(self.entries[: len(self.stream)], ENTRY)


def regs(entry):
    """The ``Program.call`` register file of an :data:`ENTRY` row: the registers before the
    interrupt, the flags from the status the hardware pushed."""
    reg = [int(entry[k]) for k in ("a", "x", "y", "sp")] + [0] * 12
    for i, bit in c64._FLAGS:  # pylint: disable=protected-access
        reg[i] = int(entry["p"]) >> bit & 1
    return reg


def lift(host, roms, facts=None):
    """The banked program of ``host``'s frames: an entry per entry address, lifted from the RAM
    at the first frame through the bank map of ``roms`` with ``facts`` (default: the host's)."""
    facts = facts or host.facts()
    return wholeprog.lift(
        host.image, sorted(facts.entry_sp), [c64.DEVICES], facts, bank=c64.bank_map(roms)
    )


def check(prog, host, max_cycles=2_000_000):
    """Run ``prog`` once per frame of ``host`` from the machine's entry state with its device
    reads replayed: ``[frame, *mismatch]`` of the first it differs from, or None, as
    :func:`c64.check` (a mismatch in a frame that entered interrupts is ``interrupt``)."""
    mem, ret = bytearray(host.image), host.sentinel - 1
    at = {pc: i for i, pc in enumerate(prog.entries)}
    bad = None
    for n, (ref, e) in enumerate(zip(host.stream, host.frames)):
        o = prog.call(
            mem, at[int(e["at"])], a=int(e["a"]), reg=regs(e), log=True, max_cycles=max_cycles,
            reads=c64.reads(ref), ret=ret, irq=True,
        )  # fmt: skip
        bad = c64.mismatch(ref, {**o, "ram": mem})
        if bad is not None:
            bad = [n, *bad]
            break
    if c64.outcome(bad) == "mismatch" and host.nested.get(bad[0]):
        return [bad[0], "interrupt", *host.nested[bad[0]]]
    return bad


def timed(prog, host, bank, max_cycles=2_000_000):
    """``(reference seconds, lifted seconds, reference cycles)`` of running the recorded frames
    through the reference interpreter (``bank``: the bank map) and through ``prog``, each from
    the machine's entry states with the machine's device reads, after one warm-up frame."""
    reads, ents = [c64.reads(r) for r in host.stream], host.frames
    at = {pc: i for i, pc in enumerate(prog.entries)}
    kw = {"max_cycles": max_cycles, "ret": host.sentinel - 1, "irq": True}

    def lifted(mem, k):
        e = ents[k]
        prog.call(mem, at[int(e["at"])], a=int(e["a"]), reg=regs(e), reads=reads[k], **kw)
        return 0

    def reference(mem, k):
        e = ents[k]
        r = wholeprog.reference_call(mem, int(e["at"]), a=int(e["a"]), reg=regs(e), reads=reads[k],
                                     volatile=[c64.DEVICES], bank=bank, **kw)  # fmt: skip
        return r["cycles"]

    out = []
    for run in (reference, lifted):
        run(bytearray(host.image), 0)
        mem, t = bytearray(host.image), time.perf_counter()
        cycles = sum(run(mem, k) for k in range(len(reads)))
        out += [time.perf_counter() - t, cycles]
    return out[0], out[2], out[1]
