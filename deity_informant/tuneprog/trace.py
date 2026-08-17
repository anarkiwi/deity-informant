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
"""

from __future__ import annotations

import pickle
from array import array
from hashlib import blake2b
from pathlib import Path

import numpy as np

from ..lifter import lift
from .. import c64
from .machine import Refusal, init_runner
from .tracedata import Trace, site_key
from .tracevm import PH_PLAY, TraceVM

CALL_BUDGET = 400_000


class Tracer:
    """Drives init and ``n`` ticks of one entry under :class:`TraceVM`."""

    def __init__(self, image, entry, song=None, policy="record", inputs=None, override=None):
        self.image = image
        self.entry = entry
        self.song = image.startsong - 1 if song is None else song
        self.vm = TraceVM(image.mem, image, policy=policy, inputs=inputs, override=override)
        self.cache = {}
        self.image_post_init = None
        self.calls_done = 0
        self.hashes = {}
        self.period = None
        self.first_repeat = None
        self.state_hash = array("Q")
        self.footprint = array("I")
        self._fp = ()

    def run_init(self, budget=None):
        vm = self.vm
        vm.reg[0], vm.reg[1], vm.reg[2] = self.song, 0, 0
        vm.push_frame(None, 0x0002, self.image.init)
        kw = {} if budget is None else {"budget": budget}
        init_runner(vm, self.image.init, self.cache, lift, **kw)
        vm.shadow.clear()
        vm.phase = PH_PLAY
        if self.entry.kind == "irq" and not any(
            self.image.lo <= a < self.image.hi for a in (0xEA31, 0xEA81, 0xFEBC)
        ):
            c64.install_kernal_irq_stubs(vm)
        self.image_post_init = bytes(vm.mem)
        return self

    def run_calls(self, n, budget=CALL_BUDGET):
        for _ in range(n):
            self._one_call(budget)
        return self

    def _one_call(self, budget):
        vm = self.vm
        reg = vm.reg
        vm.call = self.calls_done
        vm.tick_rd = vm.tick_wr = 0
        start = reg[3]
        c0 = vm.cycles
        vm._push(0x00)
        if self.entry.kind == "sub":
            vm._push(0x01)
            vm.push_frame(None, 0x0002, self.entry.addr)
        else:
            vm._push(0x00)
            vm._push_status()
            vm.push_frame(None, 0x0000, self.entry.addr)
            reg[10] = 1
            if "video" in self.entry.source:
                vm.vicirq = 0x81  # a raster IRQ has fired: handlers poll $D019
        pc = self.entry.addr
        n = 0
        while reg[3] < start:
            pc = vm.step(pc, self.cache, lift)
            n += 1
            if n > budget:
                raise Refusal("play runaway", "call %d at $%04X" % (self.calls_done, pc))
        vm.shadow.clear()
        if vm.cycles - c0 < self.entry.cycles_per_tick:
            vm.cycles = c0 + self.entry.cycles_per_tick
        self._hash()
        self.calls_done += 1

    def _hash(self):
        vm = self.vm
        if len(self._fp) != len(vm.written_play):
            self._fp = tuple(sorted(vm.written_play))
        mem = vm.mem
        n = len(self._fp)
        h = blake2b(
            bytes(map(mem.__getitem__, self._fp)), digest_size=8, key=n.to_bytes(4, "little")
        ).digest()
        v = int.from_bytes(h, "little")
        self.state_hash.append(v)
        self.footprint.append(n)
        if self.period is None:
            ninp = len(vm.inputs)
            prev = self.hashes.get((n, v))
            if prev is None:
                self.hashes[(n, v)] = (self.calls_done, ninp)
            elif prev[1] == ninp:
                # design S1: a repeat is a witness only with no inputs consumed
                # between the two calls (otherwise the next tick may differ).
                self.period = self.calls_done - prev[0]
                self.first_repeat = self.calls_done

    # ---- resume ------------------------------------------------------------
    def save(self, path):
        Path(path).write_bytes(pickle.dumps(self, protocol=pickle.HIGHEST_PROTOCOL))
        return path

    @staticmethod
    def load(path):
        return pickle.loads(Path(path).read_bytes())

    def __getstate__(self):
        d = dict(self.__dict__)
        d["cache"] = None
        return d

    def __setstate__(self, d):
        self.__dict__.update(d)
        self.cache = {}

    # ---- result ------------------------------------------------------------
    def trace(self):
        vm = self.vm
        # design S2: an operand byte any traced procedure writes -- in any phase,
        # init included -- is a variable, so it drops out of the site key and the
        # lift loads it. Init-only cells fold back to constants in S4, per phase.
        code = {a for a, c in enumerate(vm.code) if c}
        cells = code & (vm.written_play | vm.written_init)
        sites = {}
        for sk, count in vm.count.items():
            pc, bb = sk
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
            s["count"] += count
            s["phases"] |= vm.sitephase[sk]
            s["variants"].append(bytes(bb))
            s["idx"] |= vm.idx.get(sk, set())
            for name, src in (("reads", vm.reads), ("writes", vm.writes)):
                for i, a in src.get(sk, {}).items():
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
            "schedule": [self.entry.to_dict()],
            "song": self.song,
            "calls": self.calls_done,
            "insns": vm.insns,
            "cycles": vm.cycles,
            "period": self.period,
            "first_repeat": self.first_repeat,
            "unmatched_rts": vm.unmatched_rts,
            "max_depth": vm.max_depth,
            **self.image.meta(),
        }
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
            cells=cells,
            code=code,
            cell_values={a: set(v) for a, v in vm.wr_values.items() if a in cells},
            jsr_targets=jsr_targets,
            wlog=_arrays(vm.sidlog),
            iolog=_arrays(vm.iolog),
            state_hash=np.frombuffer(self.state_hash, dtype=np.uint64).copy(),
            footprint_size=np.frombuffer(self.footprint, dtype=np.uint32).copy(),
        )


def _arrays(cols):
    names = ("call", "addr", "val", "cyc")
    types = (np.uint32, np.uint16, np.uint8, np.uint32)
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
