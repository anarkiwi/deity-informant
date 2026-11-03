"""Interrupt frames of a C64 run, from an IRQ or NMI's entry to its RTI, recorded and lifted.

An interrupt entered inside a frame is an event of its record, taken at the instruction
boundary the machine took it at; ``Program.call(..., nested=...)`` takes it there.
"""

from __future__ import annotations

import time
from functools import cached_property

import numpy as np

from . import c64, wholeprog
from .smc.machine import MachineOracle

__all__ = ["ENTRY", "FrameHost", "check", "lift", "regs"]

ENTRY, EVENT = c64.ENTRY, c64.EVENT


class FrameHost(c64._Host):  # pylint: disable=protected-access,too-many-instance-attributes
    """A machine run of an interrupt-driven song's frames (:meth:`drive_frames`).

    One record per frame in :attr:`stream`, their entry states and the interrupts entered
    inside them kept beside it window by window in two :class:`~deity_informant.c64.Spill`
    (:meth:`chunks`): memory is bounded however many frames run. :attr:`frames` and
    :attr:`inner` materialize them whole, for diagnosis; :attr:`image` is the RAM at the first
    entry, :attr:`foreign` the kinds entered outside any frame, :attr:`stop` why the run stopped.
    """

    def __init__(self, *args, **kw):
        super().__init__(*args, sentinel=c64.SENTINEL, **kw)
        self.fr, self.image, self.dirty, self.foreign, self.stop = None, None, False, set(), None
        self.budget, self.ninner = None, 0
        self.entry_log, self.nested_log = c64.Spill(kw.get("spill", c64.SPILL)), c64.Spill(
            kw.get("spill", c64.SPILL)
        )

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
        return self

    def _frames_phase(self, index_frames):
        super()._frames_phase(index_frames)
        self.m.clear_events()  # pylint: disable=no-member
        self.fr = c64.Frames(self.rec, self.sentinel, self.budget)

    def _window(self):
        super()._window()
        if self.fr is not None:
            self._take()
            self.m.clear_events()  # pylint: disable=no-member

    def _take(self):
        """Move the closed frames to the stream, their entry states and events beside it."""
        chunk = self.fr.take()
        if chunk.records.meta:
            self.stream.add(chunk.records)
            self.entry_log.append(bytes(chunk.entries))
            self.nested_log.append(bytes(chunk.events))
            self.ninner += len(chunk.events) // EVENT.itemsize

    def chunks(self):
        """``(first, records, entries, events)`` of each window of frames: its first frame's index,
        its :class:`~deity_informant.c64.CallRecords`, their :data:`ENTRY` rows and the
        :data:`EVENT` rows of the interrupts inside them, ``record`` counted from the window."""
        n = 0
        for rec, ent, ev in zip(self.stream.chunks(), self.entry_log, self.nested_log):
            yield n, rec, np.frombuffer(ent, ENTRY), np.frombuffer(ev, EVENT)
            n += len(rec)

    @cached_property
    def frames(self):
        """The entry states of all recorded frames (:data:`ENTRY`), materialized."""
        return np.concatenate(
            [np.frombuffer(c, ENTRY) for c in self.entry_log] or [np.zeros(0, ENTRY)]
        )

    @cached_property
    def inner(self):
        """The interrupts entered inside all frames (:data:`EVENT`, ``record`` the frame),
        materialized."""
        out = []
        for n, _, _, ev in self.chunks():
            out.append(ev.copy())
            out[-1]["record"] += n
        return np.concatenate(out or [np.zeros(0, EVENT)])

    def inside(self, k):
        """The interrupts entered inside frame ``k`` (:data:`EVENT`)."""
        lo, hi = np.searchsorted(self.inner["record"], [k, k + 1])
        return self.inner[lo:hi]

    def sources(self, k):
        """The kinds of interrupt entered inside frame ``k``."""
        return sorted({c64.SOURCES[i] for i in self.inside(k)["kind"]})


def reg_rows(rows):
    """The ``Program.call`` register files of :data:`ENTRY` or :data:`EVENT` ``rows`` (flags from
    the pushed status), as lists."""
    reg = np.zeros((len(rows), 16), np.int64)
    for i, r in enumerate(("a", "x", "y", "sp")):
        reg[:, i] = rows[r]
    for i, bit in c64._FLAGS:  # pylint: disable=protected-access
        reg[:, i] = rows["p"] >> bit & 1
    return reg.tolist()


def regs(entry):
    """The ``Program.call`` register file of an :data:`ENTRY` row."""
    return reg_rows(np.array([entry]))[0]


def lift(host, roms, facts=None, **kw):
    """The banked program of ``host``'s frames, an entry per handler entry address.

    Lifted from the RAM at the first frame through the bank map of ``roms`` with ``facts``
    (default: the host's); ``kw`` are ``wholeprog.lift``'s options.
    """
    facts = facts or host.facts()
    return wholeprog.lift(
        host.image, sorted(facts.entry_sp), [c64.DEVICES], facts, bank=c64.bank_map(roms), **kw
    )


def window_events(events, n):
    """Per frame of a window of ``n``, its slice bounds in ``events`` (``record`` from 0)."""
    return np.searchsorted(events["record"].copy(), np.arange(n + 1)).tolist()


def nested(prog, events):
    """The ``Program.call`` interrupts of one frame's ``events``: ``(icount, pos, entry, a, reg,
    depth)``. ``prog`` None gives entry addresses, as ``wholeprog.reference_call`` takes them.
    """
    at = {pc: i for i, pc in enumerate(prog.entries)} if prog else None
    return [
        (i, p, at[pc] if prog else pc, a, r, d)
        for i, p, pc, a, r, d in zip(
            events["icount"].tolist(), events["pos"].tolist(), events["at"].tolist(),
            events["a"].tolist(), reg_rows(events), events["depth"].tolist(),
        )
    ]  # fmt: skip


#: ``Program.check``'s start of a record: its entry index and the registers before the interrupt.
_START = np.dtype([("entry", "<u2")] + [(r, "u1") for r in ("a", "x", "y", "sp", "p")])


def _starts(prog, ent, events):
    """``(starts, nested)`` bytes of a window's frames and events for ``Program.check``."""
    table = np.zeros(1 << 16, "<u2")
    table[prog.entries] = np.arange(len(prog.entries))
    starts = np.zeros(len(ent), _START)
    starts["entry"] = table[ent["at"]]
    for r in ("a", "x", "y", "sp", "p"):
        starts[r] = ent[r]
    return starts.tobytes(), c64.nested_rows(prog, events)


def _call(prog, host, k, rec, mem, bank=None):
    """Frame ``k``, recorded as ``rec``, run from ``mem`` with its reads replayed and interrupts.

    On ``prog``, or on the reference interpreter through the bank map ``bank`` when ``prog`` is
    None; ``mem`` is left as the frame leaves it.
    """
    e = host.frames[k]
    kw = {"a": int(e["a"]), "reg": regs(e), "log": True, "reads": c64.read_entries(rec)}
    kw |= {"irq": True, "ret": host.sentinel - 1, "nested": nested(prog, host.inside(k))}
    if prog is not None:
        at = {pc: i for i, pc in enumerate(prog.entries)}
        return prog.call(mem, at[int(e["at"])], **kw)
    return wholeprog.reference_call(mem, int(e["at"]), volatile=[c64.DEVICES], bank=bank, **kw)


def check(prog, host, max_cycles=2_000_000):
    """Check ``prog`` against ``host``'s frames natively, interrupts at the machine's boundaries.

    ``[frame, *mismatch]`` of the first frame it differs from (``boundary``: an interrupt not at
    the machine's boundary), or None, as :func:`c64.check`.
    """
    mem, reg = bytearray(host.image), [0] * 16
    for n, r, ent, ev in host.chunks():
        bad, reg = prog.check(
            mem, reg, r.packed, r.first, None, max_cycles, None, host.sentinel - 1,
            *_starts(prog, ent, ev),
        )  # fmt: skip
        if bad is not None:
            return [bad[0] + n, *bad[1:]]
    return None


def timed(prog, host, bank, max_cycles=2_000_000):
    """``(reference s, lifted s, reference cycles, frames, interrupted frames)`` of the first
    window.

    The frames run through the reference interpreter (bank map ``bank``) and through ``prog``,
    each from the machine's entry states with its device reads and interrupts, after one warm-up
    frame; a frame with interrupts runs on the reference interpreter in both.
    """
    _, first, ents, events = next(host.chunks())
    recs = [c64.read_entries(r) for r in first]
    n = len(recs)
    bounds, regfiles = window_events(events, n), reg_rows(ents)
    at, ret = {pc: i for i, pc in enumerate(prog.entries)}, host.sentinel - 1
    kw = {"max_cycles": max_cycles, "ret": ret, "irq": True}
    entry, acc = ents["at"].tolist(), ents["a"].tolist()
    inside = [events[lo:hi] for lo, hi in zip(bounds, bounds[1:])]

    def lifted(mem, k):
        prog.call(mem, at[entry[k]], a=acc[k], reg=regfiles[k], reads=recs[k],
                  nested=nested(prog, inside[k]), **kw)  # fmt: skip
        return 0

    def reference(mem, k):
        r = wholeprog.reference_call(mem, entry[k], a=acc[k], reg=regfiles[k], reads=recs[k],
                                     volatile=[c64.DEVICES], bank=bank,
                                     nested=nested(None, inside[k]), **kw)  # fmt: skip
        return r["cycles"]

    out = []
    for run in (reference, lifted):
        run(bytearray(host.image), 0)
        mem, cycles, t = bytearray(host.image), 0, time.perf_counter()
        for k in range(n):
            cycles += run(mem, k)
        out += [time.perf_counter() - t, cycles]
    return out[0], out[2], out[1], n, len(events)
