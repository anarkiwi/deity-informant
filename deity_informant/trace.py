"""Run-time facts for whole-program lifting, from the jennings native core.

:func:`collect` runs a host-supplied driver twice over a copy of an entry-state image: a traced
pass for instruction starts and ``(pc, next pc)`` transfers, then an observed pass for the
values stored to each address and the instructions whose accesses other than pushes and pulls
reach page 1. Heights are SP at an instruction's start minus SP at its call's entry.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field

import numpy as np
from jennings.devices.mpu6502 import NativeMPU
from jennings.memory import NativeMemory
from tqdm import tqdm

__all__ = ["Facts", "Session", "collect"]


@dataclass
class Facts:
    """Executed ``starts``, ``(pc, next)`` transfer ``edges``, stored values ``writes``, the
    ``page1`` instructions whose accesses reached $0100-$01FF with the ``[lo, hi]`` page-1
    addresses each reached, the stack
    ``heights`` each start was seen at (relative to its call's entry, pushes negative), and
    the ``entry_sp`` values (SP after the call's return address) each entry was called at; on a
    banked host, the bank configuration indices ``banks`` each start ran in and the
    ``entry_port`` ``[dir, data]`` port register values each entry was called with."""

    starts: list = field(default_factory=list)
    edges: list = field(default_factory=list)
    writes: dict = field(default_factory=dict)
    page1: dict = field(default_factory=dict)
    heights: dict = field(default_factory=dict)
    entry_sp: dict = field(default_factory=dict)
    banks: dict = field(default_factory=dict)
    entry_port: dict = field(default_factory=dict)

    def to_dict(self):
        return {
            "starts": list(self.starts),
            "edges": [list(e) for e in self.edges],
            "writes": {int(a): sorted(v) for a, v in self.writes.items()},
            "page1": {int(a): list(r) for a, r in self.page1.items()},
            "heights": {int(a): sorted(v) for a, v in self.heights.items()},
            "entry_sp": {int(a): sorted(v) for a, v in self.entry_sp.items()},
            "banks": {int(a): sorted(v) for a, v in self.banks.items()},
            "entry_port": {int(a): sorted(map(list, v)) for a, v in self.entry_port.items()},
        }

    @classmethod
    def from_dict(cls, d):
        return cls(
            starts=[int(s) for s in d.get("starts", [])],
            edges=[(int(p), int(q)) for p, q in d.get("edges", [])],
            writes={int(a): sorted(int(x) for x in v) for a, v in d.get("writes", {}).items()},
            page1={int(a): [int(lo), int(hi)] for a, (lo, hi) in d.get("page1", {}).items()},
            heights={int(a): sorted(int(x) for x in v) for a, v in d.get("heights", {}).items()},
            entry_sp={int(a): sorted(int(x) for x in v) for a, v in d.get("entry_sp", {}).items()},
            banks={int(a): sorted(int(x) for x in v) for a, v in d.get("banks", {}).items()},
            entry_port={
                int(a): sorted([int(p), int(q)] for p, q in v)
                for a, v in d.get("entry_port", {}).items()
            },
        )

    def dumps(self):
        return json.dumps(self.to_dict())

    @classmethod
    def loads(cls, s):
        return cls.from_dict(json.loads(s))


class Session:
    """A native core over a copy of the image; ``call(pc, a)`` is ``run_to_rts``."""

    def __init__(self, image, max_cycles, traced, pbar):
        self.mem = NativeMemory(bytes(image))
        self.mpu = NativeMPU(memory=self.mem)
        self.max_cycles = max_cycles
        self.traces = [] if traced else None
        self.keys = np.zeros(1 << 25, bool)
        self.edges = np.zeros(0, np.uint32)
        self.pending = 0
        self._bar = pbar
        self.entry_sp = defaultdict(set)

    def call(self, pc, a=0):
        if self.traces is None:
            self.mpu.run_to_rts(pc, a, self.max_cycles)
        else:
            sp0 = (self.mpu.sp - 2) & 0xFF
            self.entry_sp[pc].add(sp0)
            pcs, _, regs = self.mpu._core.run_to_rts_traced(  # pylint: disable=protected-access
                pc, a, self.max_cycles, True
            )
            self.traces.append((pcs, regs[3], sp0))
            self.pending += len(regs[3])
            if self.pending > 1 << 20:
                self.fold()
        if self._bar is not None:
            self._bar.update()

    def fold(self):
        """``(height keys, edges)``: the unique ``pc << 9 | height + 256`` and
        ``pc << 16 | next`` over every traced call so far."""
        if self.traces:
            pcs, sps, sp0 = zip(*self.traces)
            n = np.fromiter(map(len, sps), np.int64, len(sps))
            first = (np.cumsum(n) - n)[n > 0]
            pc = np.frombuffer(b"".join(pcs), np.uint16).astype(np.int32)
            sp = np.frombuffer(b"".join(sps), np.uint8).astype(np.int32)
            h = np.empty_like(sp)
            h[1:] = sp[:-1]
            h -= np.repeat(np.array(sp0, np.int32), n)
            h[first] = 0
            p32 = pc.astype(np.uint32)
            edges = (p32[:-1] << 16) | p32[1:]
            keep = np.ones(len(edges), bool)
            keep[first[1:] - 1] = False
            self.keys[(pc << 9) | (h + 0x100)] = True
            self.edges = np.unique(np.concatenate((self.edges, edges[keep])))
            self.traces, self.pending = [], 0
        return np.flatnonzero(self.keys), self.edges


def collect(
    image, driver, max_cycles=10_000_000, progress=False, total=None, skip=None
):  # pylint: disable=too-many-arguments,too-many-positional-arguments
    """Facts from running ``driver(session)`` twice on ``image`` (64 KiB).

    ``progress`` shows calls per pass (``total`` per pass, if known). ``skip(starts, ports)``,
    when given, sees the traced pass's instruction starts and the set of CPU port addresses
    ($00/$01) the driver stores to, from a store-only pass; true returns None, not the facts.
    """
    pbar = tqdm(total=total, desc="facts trace", unit="call") if progress else None
    s = traced = Session(image, max_cycles, True, pbar)
    driver(s)
    if pbar is not None:
        pbar.close()
    ph, edges = s.fold()
    starts = np.unique(ph >> 9)
    heights = defaultdict(list)
    for k, v in zip((ph >> 9).tolist(), ((ph & 0x1FF) - 0x100).tolist()):
        heights[k].append(v)
    if skip is not None:
        ports, s = set(), Session(image, max_cycles, False, None)
        s.mem.observe_data_access(on_read=None, on_write=lambda a, v: ports.add(a), writes=range(2))
        driver(s)
        if skip(starts.tolist(), ports):
            return None
    writes = defaultdict(set)
    page1 = {}

    def on_page1(a):
        lo, hi = page1.get(s.mpu.pc, (a, a))
        page1[s.mpu.pc] = (min(lo, a), max(hi, a))

    pbar = tqdm(total=total, desc="facts writes", unit="call") if progress else None
    s = Session(image, max_cycles, False, pbar)

    def on_write(a, v):
        if 0x100 <= a < 0x200:
            on_page1(a)
        writes[a].add(v)

    s.mem.observe_data_access(
        on_read=on_page1,
        on_write=on_write,
        reads=range(0x100, 0x200),
    )
    driver(s)
    if pbar is not None:
        pbar.close()
    return Facts(
        starts=starts.tolist(),
        edges=[(int(e >> 16), int(e & 0xFFFF)) for e in edges],
        writes={a: sorted(v) for a, v in sorted(writes.items())},
        page1={p: list(r) for p, r in sorted(page1.items())},
        heights={a: sorted(v) for a, v in heights.items()},
        entry_sp={a: sorted(v) for a, v in traced.entry_sp.items()},
    )
