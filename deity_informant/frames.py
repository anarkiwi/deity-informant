"""Interrupt frames of a C64 run, from an IRQ or NMI's entry to its RTI, recorded and lifted.

An interrupt entered inside a frame is an event of its record, taken at the instruction
boundary the machine took it at; ``Program.call(..., nested=...)`` takes it there.
"""

from __future__ import annotations

import time

import numpy as np

from . import c64, wholeprog
from .smc.machine import MachineOracle

__all__ = ["ENTRY", "FrameHost", "check", "lift", "regs"]

ENTRY, EVENT = c64.ENTRY, c64.EVENT


class FrameHost(c64._Host):  # pylint: disable=protected-access,too-many-instance-attributes
    """A machine run of an interrupt-driven song's frames (:meth:`drive_frames`).

    One record per frame in :attr:`stream`, their entry states in :attr:`frames` and the
    interrupts entered inside them in :attr:`inner`; :attr:`image` is the RAM at the first
    entry, :attr:`foreign` the kinds entered outside any frame, :attr:`stop` why the run stopped.
    """

    def __init__(self, *args, **kw):
        super().__init__(*args, sentinel=c64.SENTINEL, **kw)
        self.fr, self.image, self.dirty, self.foreign, self.stop = None, None, False, set(), None
        self.table, self.inner, self.budget = [], [], None

    def drive_frames(self, boot, frames, max_cycles=2_000_000):
        """Run ``boot`` (:class:`~deity_informant.c64.Boot`), then ``frames`` video frames.

        A frame may run ``max_cycles`` cycles and half as many instructions (an instruction takes
        two cycles at least), the run ``frames`` times as many; at a limit the run stops,
        :attr:`stop` naming it and the frame it cuts closed as halted.
        """
        unit = (max_cycles + 1) // 2
        self.budget = c64.Budget(max_cycles, unit, frames * max_cycles, frames * unit)
        self.result = MachineOracle.run(
            self, [], frames=frames, call_cycles=max_cycles, **boot.run_kw()
        )
        super()._window()
        self.fr.close()
        self._take()
        self.image, self.dirty, self.stop = self.fr.image, self.fr.dirty, c64.STOPS[self.fr.outcome]
        kinds = (*c64.SOURCES, "brk")
        self.foreign = {k for i, k in enumerate(kinds) if self.fr.foreign >> i & 1}
        self.records = self.stream
        sp, port = self.fr.entries()
        for pc, v in sp:
            self.entry_sp[pc].add(v)
        for pc, d, v in port:
            self.entry_port[pc].add((d, v))
        self.table = np.concatenate(self.table) if self.table else np.zeros(0, ENTRY)
        self.inner = np.concatenate(self.inner) if self.inner else np.zeros(0, EVENT)
        return self

    def _frames_phase(self, index_frames):
        super()._frames_phase(index_frames)
        self.m.clear_events()  # pylint: disable=no-member
        self.fr = c64.Frames(self.rec, self.sentinel, self.budget)

    def _window(self):
        super()._window()
        if self.fr is not None:
            self._take()

    def _take(self):
        """Move the closed frames to the stream."""
        chunk = self.fr.take()
        if chunk.records.meta:
            n = len(self.stream)
            self.stream.add(chunk.records)
            self.table.append(np.frombuffer(chunk.entries, ENTRY).reshape(-1))
            events = np.frombuffer(chunk.events, EVENT).reshape(-1).copy()
            events["record"] += n
            self.inner.append(events)

    @property
    def frames(self):
        """The entry states of the recorded frames (:data:`ENTRY`)."""
        return self.table

    def inside(self, k):
        """The interrupts entered inside frame ``k`` (:data:`EVENT`)."""
        lo, hi = np.searchsorted(self.inner["record"], [k, k + 1])
        return self.inner[lo:hi]

    def sources(self, k):
        """The kinds of interrupt entered inside frame ``k``."""
        return sorted({c64.SOURCES[i] for i in self.inside(k)["kind"]})


def regs(entry):
    """The ``Program.call`` register file of an :data:`ENTRY` row (flags from the pushed status)."""
    reg = [int(entry[k]) for k in ("a", "x", "y", "sp")] + [0] * 12
    for i, bit in c64._FLAGS:  # pylint: disable=protected-access
        reg[i] = int(entry["p"]) >> bit & 1
    return reg


def lift(host, roms, facts=None, **kw):
    """The banked program of ``host``'s frames, an entry per handler entry address.

    Lifted from the RAM at the first frame through the bank map of ``roms`` with ``facts``
    (default: the host's); ``kw`` are ``wholeprog.lift``'s options.
    """
    facts = facts or host.facts()
    return wholeprog.lift(
        host.image, sorted(facts.entry_sp), [c64.DEVICES], facts, bank=c64.bank_map(roms), **kw
    )


def nested(prog, host, k):
    """The ``Program.call`` interrupts of frame ``k``: ``(icount, pos, entry, a, reg, depth)``.

    ``prog`` None gives entry addresses, as ``wholeprog.reference_call`` takes them.
    """
    at = {pc: i for i, pc in enumerate(prog.entries)} if prog else None
    return [
        (int(e["icount"]), int(e["pos"]), at[int(e["at"])] if prog else int(e["at"]),
         int(e["a"]), regs(e), int(e["depth"]))
        for e in host.inside(k)
    ]  # fmt: skip


#: ``Program.check``'s start of a record: its entry index and the registers before the interrupt.
_START = np.dtype([("entry", "<u2")] + [(r, "u1") for r in ("a", "x", "y", "sp", "p")])


def _starts(prog, host, lo, hi):
    """``(starts, nested)`` bytes of frames ``lo`` to ``hi`` for ``Program.check``."""
    table = np.zeros(1 << 16, "<u2")
    table[prog.entries] = np.arange(len(prog.entries))
    ent = host.frames[lo:hi]
    starts = np.zeros(hi - lo, _START)
    starts["entry"] = table[ent["at"]]
    for r in ("a", "x", "y", "sp", "p"):
        starts[r] = ent[r]
    i, j = np.searchsorted(host.inner["record"], [lo, hi])
    return starts.tobytes(), c64.nested_rows(prog, host.inner[i:j], lo)


def _call(prog, host, k, rec, mem, bank=None):
    """Frame ``k``, recorded as ``rec``, run from ``mem`` with its reads replayed and interrupts.

    On ``prog``, or on the reference interpreter through the bank map ``bank`` when ``prog`` is
    None; ``mem`` is left as the frame leaves it.
    """
    e = host.frames[k]
    kw = {"a": int(e["a"]), "reg": regs(e), "log": True, "reads": c64.read_entries(rec)}
    kw |= {"irq": True, "ret": host.sentinel - 1, "nested": nested(prog, host, k)}
    if prog is not None:
        at = {pc: i for i, pc in enumerate(prog.entries)}
        return prog.call(mem, at[int(e["at"])], **kw)
    return wholeprog.reference_call(mem, int(e["at"]), volatile=[c64.DEVICES], bank=bank, **kw)


def check(prog, host, max_cycles=2_000_000):
    """Check ``prog`` against ``host``'s frames natively, interrupts at the machine's boundaries.

    ``[frame, *mismatch]`` of the first frame it differs from (``boundary``: an interrupt not at
    the machine's boundary), or None, as :func:`c64.check`.
    """
    mem, reg, n = bytearray(host.image), [0] * 16, 0
    for r in host.stream.chunks():
        bad, reg = prog.check(
            mem, reg, r.packed, r.first, None, max_cycles, None, host.sentinel - 1,
            *_starts(prog, host, n, n + len(r)),
        )  # fmt: skip
        if bad is not None:
            return [bad[0] + n, *bad[1:]]
        n += len(r)
    return None


def timed(prog, host, bank, max_cycles=2_000_000):
    """``(reference s, lifted s, reference cycles, frames, interrupted frames)`` of the first window.

    The frames run through the reference interpreter (bank map ``bank``) and through ``prog``,
    each from the machine's entry states with its device reads and interrupts, after one warm-up
    frame; a frame with interrupts runs on the reference interpreter in both.
    """
    first = next(host.stream.chunks())
    recs = [c64.read_entries(r) for r in first]
    ents = host.frames
    at = {pc: i for i, pc in enumerate(prog.entries)}
    kw = {"max_cycles": max_cycles, "ret": host.sentinel - 1, "irq": True}

    def lifted(mem, k):
        e = ents[k]
        prog.call(mem, at[int(e["at"])], a=int(e["a"]), reg=regs(e), reads=recs[k],
                  nested=nested(prog, host, k), **kw)  # fmt: skip
        return 0

    def reference(mem, k):
        e = ents[k]
        r = wholeprog.reference_call(mem, int(e["at"]), a=int(e["a"]), reg=regs(e),
                                     reads=recs[k], volatile=[c64.DEVICES], bank=bank,
                                     nested=nested(None, host, k), **kw)  # fmt: skip
        return r["cycles"]

    out, n = [], len(recs)
    for run in (reference, lifted):
        run(bytearray(host.image), 0)
        mem, cycles, t = bytearray(host.image), 0, time.perf_counter()
        for k in range(n):
            cycles += run(mem, k)
        out += [time.perf_counter() - t, cycles]
    hit = int(np.count_nonzero(host.inner["record"] < n))
    return out[0], out[2], out[1], n, hit
