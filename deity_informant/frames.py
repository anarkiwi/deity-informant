"""Interrupt frames of a C64 run, from an IRQ or NMI's entry to its RTI, recorded and lifted.

A :class:`FrameHost` points each interrupt's pushed return address at the machine's sentinel, so the
handler's RTI closes the frame's record as a call's return does, and resumes the interrupted code.
An interrupt entered inside a frame is part of that frame's record: its I/O accesses and RAM
changes join the record in the order the machine made them, and the entry is kept as an event at
the position of the record's log it was taken at (:data:`EVENT`). The lifted program takes it
there: ``Program.call(..., nested=...)`` runs the entry before the volatile access of that
position, so the log, the device reads replayed and the final state are the machine's.
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
#: An interrupt entered inside a frame: the position in the frame's I/O log it was taken at,
#: the entry address and the registers before the interrupt.
EVENT = np.dtype([("pos", "<u4"), ("at", "<u2")] + [(r, "u1") for r in ("a", "x", "y", "sp", "p")])
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
        """Append a record: the halt, end registers ``(a, x, y, sp, p)``, I/O log, RAM changes
        (``idx`` and ``vals``: the parts of the record in the order the machine made them)."""
        self.meta += bytes([_CALL, _HALT[halt], *end])
        for addr, v, w in log:
            self.log += bytes([addr & 0xFF, addr >> 8, v, w])
        self.nlog.append(len(log))
        self.idx.append(b"".join(idx))
        self.vals.append(b"".join(vals))

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
    :attr:`image`, the interrupts entered inside each frame in :attr:`taken` (by frame, as
    :data:`EVENT`) and their sources in :attr:`nested`, and those entered outside any (the run
    is then no sequence of frames) in :attr:`foreign`; :attr:`dirty` is set when RAM outside
    page 1 changed between two frames (the interrupted code does real work). A run that
    outlasts its ``deadline`` (a ``time.perf_counter`` time) stops at the next check, with
    :attr:`budget` set: a song of interrupts at the rate of an NMI digi player has more frames
    than the time allowed to record them."""

    def __init__(self, *args, **kw):
        super().__init__(*args, sentinel=c64.SENTINEL, **kw)
        self.entries, self.nested, self.foreign, self.taken = [], {}, set(), {}
        self.end, self.dirty = None, False
        self.image, self.chunk, self.limit = None, None, 0
        self.open, self.parts, self.traps = [], None, 0
        self.deadline, self.budget, self.table, self.cause = None, False, None, {}

    def drive_frames(self, boot, frames, max_cycles=2_000_000, deadline=None):
        """Run ``boot`` (:class:`~deity_informant.c64.Boot`), then ``frames`` video frames of
        interrupts. A frame that jams or runs past ``max_cycles`` ends the run, as does the
        ``deadline``; one the run's end cuts is dropped."""
        self.limit, self.deadline = max_cycles, deadline
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
        mc.watch = self._watch(mc)

    def _watch(self, mc):
        """Where a frame starts (the targets of the IRQ and NMI vectors) and, inside one, ends."""
        words = ((self._read(mc.bits, a)[0] for a in (v, v + 1)) for v in (0xFFFA, 0xFFFE))
        targets = {lo | hi << 8 for lo, hi in words}
        return targets | {self.sentinel} if self.cur == "call" else targets

    def _window(self):
        super()._window()
        if self.cur != "call":
            self._flush()
        self._expire()

    def _expire(self):
        """Stop the run when its deadline has passed."""
        if self.deadline is not None and time.perf_counter() > self.deadline:
            self.m.stop = self.budget = True  # pylint: disable=no-member

    def _flush(self):
        """Move the closed records to the stream."""
        if self.chunk is not None and self.chunk.meta:
            self.stream.add(self.chunk.records())
            self.chunk = None

    def _frame_trap(self, mc):
        if self.cur == "call" and mc.mpu.pc == self.sentinel:
            self._leave(mc)
            return True
        entered = _sources(mc.events)
        mc.clear_events()
        if not entered or entered[-1] == "brk":
            self.foreign |= set(entered)
            return False
        self.foreign |= set(entered[:-1])
        self._enter(mc, entered[-1])
        self.traps += 1
        if not self.traps & 0x3FF:
            self._expire()
        return False

    def _segment(self):
        """Take the machine's I/O accesses and RAM changes since the last segment into the
        open frame's record."""
        for part, new in zip(self.parts, self.tr.snap()):
            part.append(new)

    def _enter(self, mc, source):
        """Open the record of the frame the machine has just entered, or in a frame the event of
        the interrupt it took."""
        r, sp = mc.mpu, mc.mpu.sp
        at = [0x100 | (sp + k) & 0xFF for k in (1, 2, 3)]
        p, lo, hi = (int(self.ram[a]) for a in at)
        state = (r.a, r.x, r.y, (sp + 3) & 0xFF, p)
        if self.cur == "call" and lo | hi << 8 == self.sentinel:
            lo, hi = self._pop(mc, state).to_bytes(2, "little")
        if self.cur == "call":
            self._segment()
            pos = sum(map(len, self.parts[0]))
            self.taken[len(self.entries) - 1].append((pos, r.pc, *state))
            self.nested.setdefault(len(self.entries) - 1, set()).add(source)
        else:
            if self.end is not None and not self.dirty:
                diff = self.ram.view(np.uint64) != self.end
                diff[0x20:0x40] = False
                self.dirty = bool(diff.any())
            self.entries.append((r.pc, lo | hi << 8, *state))
            self.taken[len(self.entries) - 1] = []
            self.parts = ([], [], [])
            if self.image is None:
                self.image = bytes(self.ram)
            if self.chunk is None:
                self.tr.snap()
                self.chunk = _Chunk(bytes(self.ram))
            self.rec.deadline = mc.cycle + self.limit
        self.open.append((lo | hi << 8, sp))
        for a, v in zip(at[1:], (self.sentinel & 0xFF, self.sentinel >> 8)):
            self.rec.host_write(a, v)
        self.entry_sp[r.pc].add(sp)
        self.entry_port[r.pc].add((mc.port_dir, mc.port_data))
        self.cur = "call"
        self.tr.phase(c64.Tracer.CALL, sp)
        mc.watch = self._watch(mc)

    def _leave(self, mc):
        """The handler's RTI: resume the interrupted code."""
        mc.mpu.pc = self._pop(mc)
        mc.watch = self._watch(mc)

    def _pop(self, mc, end=None):
        """Return from the innermost open frame: the interrupted pc it resumes at. Its record
        closes when it was not inside another, with the registers ``end`` (default: the
        machine's)."""
        resume = self.open[-1][0]
        if len(self.open) > 1:
            self._segment()
            self.tr.phase(c64.Tracer.CALL, self.open[-2][1])
            self.open.pop()
        else:
            self._close("return", end)
        return resume

    def _close(self, halt, end=None):
        """Close the open frame's record with the registers ``end`` (default: the machine's)."""
        self.m.clear_events()  # pylint: disable=no-member
        self._segment()
        self.tr.phase(c64.Tracer.NONE)
        r = self.m.mpu
        log = [e for part in self.parts[0] for e in part]
        self.chunk.add(halt, end or (r.a, r.x, r.y, r.sp, r.p), log, *self.parts[1:])
        self.rec.deadline = None
        self.end = np.frombuffer(bytes(self.ram), np.uint64)
        self.cur, self.open = None, []

    @property
    def frames(self):
        """The entry states of the recorded frames (:data:`ENTRY`)."""
        if self.table is None or len(self.table) != len(self.stream):
            self.table = np.array(self.entries[: len(self.stream)], ENTRY)
        return self.table

    def inside(self, k):
        """The interrupts entered inside frame ``k`` (:data:`EVENT`)."""
        return np.array(self.taken.get(k, ()), EVENT)


def regs(entry):
    """The ``Program.call`` register file of an :data:`ENTRY` row: the registers before the
    interrupt, the flags from the status the hardware pushed."""
    reg = [int(entry[k]) for k in ("a", "x", "y", "sp")] + [0] * 12
    for i, bit in c64._FLAGS:  # pylint: disable=protected-access
        reg[i] = int(entry["p"]) >> bit & 1
    return reg


def lift(host, roms, facts=None, **kw):
    """The banked program of ``host``'s frames: an entry per entry address, lifted from the RAM
    at the first frame through the bank map of ``roms`` with ``facts`` (default: the host's);
    ``kw`` are ``wholeprog.lift``'s options. The lift keeps no memory value across a device
    access: the interrupts the frames took inside them are taken there."""
    facts = facts or host.facts()
    kw = {"interrupts": True} | kw
    return wholeprog.lift(
        host.image, sorted(facts.entry_sp), [c64.DEVICES], facts, bank=c64.bank_map(roms), **kw
    )


def nested(prog, host, k):
    """The ``Program.call`` interrupts of frame ``k``: ``(pos, entry, a, reg)`` per interrupt
    the machine entered inside it; ``prog`` None gives entry addresses, as
    ``wholeprog.reference_call`` takes them."""
    at = {pc: i for i, pc in enumerate(prog.entries)} if prog else None
    return [
        (int(e["pos"]), at[int(e["at"])] if prog else int(e["at"]), int(e["a"]), regs(e))
        for e in host.inside(k)
    ]


#: ``Program.check``'s start of a record: its entry index and the registers before the interrupt.
_START = np.dtype([("entry", "<u2")] + [(r, "u1") for r in ("a", "x", "y", "sp", "p")])
#: ``Program.check``'s interrupt inside a record, padded to 16 bytes.
_INSIDE = np.dtype(
    [("record", "<u4"), ("pos", "<u4"), ("entry", "<u2")]
    + [(r, "u1") for r in ("a", "x", "y", "sp", "p", "pad")]
)


def _starts(prog, host, lo, hi):
    """``(starts, nested)`` bytes of frames ``lo`` to ``hi`` for ``Program.check``."""
    table = np.zeros(1 << 16, "<u2")
    table[prog.entries] = np.arange(len(prog.entries))
    ent = host.frames[lo:hi]
    starts = np.zeros(hi - lo, _START)
    starts["entry"] = table[ent["at"]]
    for r in ("a", "x", "y", "sp", "p"):
        starts[r] = ent[r]
    inside = np.zeros(sum(len(host.taken.get(k, ())) for k in range(lo, hi)), _INSIDE)
    n = 0
    for k in range(lo, hi):
        ev = host.inside(k)
        inside[n : n + len(ev)]["record"] = k - lo
        for f in ("pos", "a", "x", "y", "sp", "p"):
            inside[f][n : n + len(ev)] = ev[f]
        inside["entry"][n : n + len(ev)] = table[ev["at"]]
        n += len(ev)
    return starts.tobytes(), inside.tobytes()


def _call(prog, host, k, rec, mem, bank=None):
    """Frame ``k``, recorded as ``rec``, run from ``mem`` (left as the frame leaves it) with its
    device reads replayed and its interrupts taken: on ``prog``, or on the reference
    interpreter through the bank map ``bank`` when ``prog`` is None."""
    e = host.frames[k]
    kw = {"a": int(e["a"]), "reg": regs(e), "log": True, "reads": c64.read_entries(rec), "irq": True}
    kw |= {"ret": host.sentinel - 1, "nested": nested(prog, host, k)}
    if prog is not None:
        at = {pc: i for i, pc in enumerate(prog.entries)}
        return prog.call(mem, at[int(e["at"])], **kw)
    return wholeprog.reference_call(mem, int(e["at"]), volatile=[c64.DEVICES], bank=bank, **kw)


def _same_as_reference(prog, host, k, rec, mem, bank):
    """Whether, from ``mem`` before frame ``k``, ``prog`` and the reference interpreter leave
    the same log, registers and memory with the frame's interrupts taken at the positions of the
    machine's log."""
    got, want = bytearray(mem), bytearray(mem)
    out, ref = _call(prog, host, k, rec, got, bank), _call(None, host, k, rec, want, bank)
    ref["log"] = [(a, v, w) for a, v, _, w in ref["log"]]
    return c64.mismatch(ref | {"ram": want}, out | {"ram": got}) is None


def check(prog, host, bank, max_cycles=2_000_000):
    """Check ``prog`` against ``host``'s frames natively: each frame runs from the machine's
    entry state with its device reads replayed and the interrupts the machine entered inside it
    taken at the same place of its log. ``[frame, *mismatch]`` of the first frame it differs
    from, or None, as :func:`c64.check`.

    The machine takes an interrupt at an instruction boundary; the log identifies only the
    device access it precedes. A lift that differs after the first interrupt of a frame is no
    mismatch of the lift alone: the result is ``[frame, "interrupt", *sources]`` and
    ``host.cause[frame]`` says what the reference interpreter (through the bank map ``bank``)
    does with the interrupts at the same places: ``position`` when it equals the lift (the
    handler and the interrupted code share state between those two accesses, so the lift is
    the program run with the interrupt there), ``diverged`` when it does not (the lift kept
    something across the device access, or the reference's handler frame, pushed at the stack
    pointer the machine had at the boundary, lands on stack the reference's interrupted code
    has pushed by the access)."""
    mem, reg, n = bytearray(host.image), [0] * 16, 0
    for r in host.stream.chunks():
        start = bytearray(mem)
        bad, reg = prog.check(
            mem, reg, r.packed, r.first, None, max_cycles, None, host.sentinel - 1,
            *_starts(prog, host, n, n + len(r)),
        )  # fmt: skip
        if bad is None:
            n += len(r)
            continue
        k = bad[0] + n
        events = host.taken[k]
        before = bad[1] == "log" and events and bad[2] < events[0][0]
        if bad[1] == "halted" or not events or before:
            return [k, *bad[1:]]
        recs = iter(r)
        for j in range(n, k):
            _call(prog, host, j, next(recs), start)
        same = _same_as_reference(prog, host, k, next(recs), start, bank)
        host.cause[k] = "position" if same else "diverged"
        return [k, "interrupt", *sorted(host.nested[k])]
    return None


def timed(prog, host, bank, max_cycles=2_000_000, until=None):
    """``(reference seconds, lifted seconds, reference cycles, frames)`` of running the frames
    of the first window of the run through the reference interpreter (``bank``: the bank map)
    and through ``prog``, each from the machine's entry states with the machine's device reads
    and interrupts, after one warm-up frame. The reference stops at the ``time.perf_counter``
    time ``until``; the lift runs the frames it did."""
    first = next(host.stream.chunks())
    recs = [(c64.read_entries(r), r) for r in first]
    ents = host.frames
    at = {pc: i for i, pc in enumerate(prog.entries)}
    kw = {"max_cycles": max_cycles, "ret": host.sentinel - 1, "irq": True}

    def lifted(mem, k):
        e = ents[k]
        prog.call(mem, at[int(e["at"])], a=int(e["a"]), reg=regs(e), reads=recs[k][0],
                  nested=nested(prog, host, k), **kw)  # fmt: skip
        return 0

    def reference(mem, k):
        e = ents[k]
        r = wholeprog.reference_call(mem, int(e["at"]), a=int(e["a"]), reg=regs(e),
                                     reads=recs[k][0], volatile=[c64.DEVICES], bank=bank,
                                     nested=nested(None, host, k), **kw)  # fmt: skip
        return r["cycles"]

    out, n = [], len(recs)
    for run in (reference, lifted):
        run(bytearray(host.image), 0)
        mem, cycles, t = bytearray(host.image), 0, time.perf_counter()
        for k in range(n):
            cycles += run(mem, k)
            if run is reference and until is not None and time.perf_counter() > until:
                n = k + 1
                break
        out += [time.perf_counter() - t, cycles]
    return out[0], out[2], out[1], n
