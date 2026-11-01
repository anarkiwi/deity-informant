"""Run-time facts for whole-program lifting, from the jennings native core.

:func:`collect` runs a host-supplied driver twice over a copy of an entry-state image: a traced
pass for instruction starts and ``(pc, next pc)`` transfers, then an observed pass for the
values stored to executed code bytes.
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
    """Executed ``starts``, ``(pc, next)`` transfer ``edges``, code-byte ``writes``."""

    starts: list = field(default_factory=list)
    edges: list = field(default_factory=list)
    writes: dict = field(default_factory=dict)

    def to_dict(self):
        return {
            "starts": list(self.starts),
            "edges": [list(e) for e in self.edges],
            "writes": {int(a): sorted(v) for a, v in self.writes.items()},
        }

    @classmethod
    def from_dict(cls, d):
        return cls(
            starts=[int(s) for s in d.get("starts", [])],
            edges=[(int(p), int(q)) for p, q in d.get("edges", [])],
            writes={int(a): sorted(int(x) for x in v) for a, v in d.get("writes", {}).items()},
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
        self._bar = pbar

    def call(self, pc, a=0):
        if self.traces is None:
            self.mpu.run_to_rts(pc, a, self.max_cycles)
        else:
            tr = self.mpu.run_to_rts_traced(pc, a, self.max_cycles)
            self.traces.append(np.frombuffer(tr.pc, dtype=np.uint16).copy())
        if self._bar is not None:
            self._bar.update()


def collect(image, driver, max_cycles=10_000_000, progress=False, total=None):
    """Facts from running ``driver(session)`` twice on ``image`` (64 KiB).

    ``progress`` shows calls per pass (``total`` per pass, if known).
    """
    pbar = tqdm(total=total, desc="facts trace", unit="call") if progress else None
    s = Session(image, max_cycles, True, pbar)
    driver(s)
    if pbar is not None:
        pbar.close()
    pcs = np.concatenate(s.traces) if s.traces else np.zeros(0, np.uint16)
    starts = np.unique(pcs)
    pairs = [(t[:-1].astype(np.uint32) << 16) | t[1:] for t in s.traces if len(t) > 1]
    edges = np.unique(np.concatenate(pairs)) if pairs else np.zeros(0, np.uint32)
    code = np.unique(np.concatenate([starts + k for k in range(3)]).astype(np.uint16))
    writes = defaultdict(set)
    pbar = tqdm(total=total, desc="facts writes", unit="call") if progress else None
    s = Session(image, max_cycles, False, pbar)
    if len(code):
        s.mem.observe_data_access(
            on_write=lambda a, v: writes[a].add(v), writes=[int(a) for a in code]
        )
    driver(s)
    if pbar is not None:
        pbar.close()
    return Facts(
        starts=starts.tolist(),
        edges=[(int(e >> 16), int(e & 0xFFFF)) for e in edges],
        writes={a: sorted(v) for a, v in sorted(writes.items())},
    )
