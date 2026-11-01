"""Progressively generate oracle-labelled witness programs covering the feasible cells.

Targeted gadgets synthesize a writer (a store of the given channel), a reader (code, a
pointer, or a clobbered return) and a timing harness; the oracle labels what each exhibits.
Seeded fuzzing mutates SMC-heavy random programs and composition combines witnesses. A
program is kept only when it adds a new tuple; generation is deterministic.
"""

from __future__ import annotations

import random

from tqdm import tqdm

from .asm import assemble
from .feasible import decide
from .oracle import run_cpu
from .space import cells
from .synth import witness
from .machine import core as machine_core
from .synthm import witnesses as machine_witnesses

__all__ = ["Program", "MachineProgram", "targeted", "fuzz", "generate", "coverage"]

ORG = 0x1000


class Program:
    """A CPU witness: source, entries, calls, init and the jennings-oracle label."""

    oracle = "cpu"

    def __init__(self, name, source, entries, calls, init=()):
        self.name = name
        self.source = source
        self.entries = list(entries)
        self.calls = [tuple(c) for c in calls]
        self.init = [tuple(c) for c in init]
        self.image, self.labels = assemble(source)
        self.pcs = [self.labels[e] for e in entries]
        self.run = run_cpu(
            self.image, self.pcs, self.calls, [(self.labels.get(p, p), a) for p, a in self.init]
        )
        self.cells = self.run.cells if self.run.returned else set()

    @property
    def sids(self):
        return {c.sid for c in self.cells}

    def to_dict(self):
        return {
            "name": self.name,
            "oracle": self.oracle,
            "source": self.source,
            "entries": self.entries,
            "calls": self.calls,
            "init": self.init,
            "cells": sorted(self.sids),
        }


class MachineProgram:
    """A machine witness (vagrant-moses oracle), wrapping a :class:`synthm.MachineWitness`."""

    oracle = "machine"

    def __init__(self, wit):
        self.wit = wit
        self.name = "machine.%s" % wit.name
        run = wit.run()
        self.cells = run.cells if run.returned else set()
        self.returned = run.returned

    @property
    def sids(self):
        return {c.sid for c in self.cells}

    def to_dict(self):
        return {
            "name": self.name,
            "oracle": self.oracle,
            "source": self.wit.source,
            "cells": sorted(self.sids),
        }


_PATCH_OPCODE = """
entry:  LDA #$60
        STA r_op
r_op:   LDA #$11
        STA $C000
        RTS
"""

_PATCH_OPERAND = """
entry:  LDA #$2A
        STA r_imm+1
r_imm:  LDA #$11
        STA $C000
        RTS
"""

_PATCH_ZP = """
entry:  LDA #$EA
        STA r_op
        JMP r_op
r_op:   .org $0040
        LDA #$11
        STA $C000
        RTS
"""

_PATCH_PTR = """
entry:  LDA #lo(targ)
        STA $F0
        LDA #hi(targ)
        STA $F1
        LDY #$00
        LDA ($F0),Y
        STA $C000
        RTS
targ:   .byte $42
"""

_PATCH_PTR_IND = """
entry:  LDA #lo(targ)
        STA ptr
        LDA #hi(targ)
        STA ptr+1
        JMP (ptr)
targ:   LDA #$42
        STA $C000
        RTS
ptr:    .byte $00,$00
"""

_RMW_OPERAND = """
entry:  INC r_op+1
r_op:   LDA #$10
        STA $C000
        RTS
"""

_RET_ADDR = """
entry:  JSR sub
        STA $C000
        RTS
sub:    TSX
        LDA #lo(dest-1)
        STA $0101,X
        LDA #hi(dest-1)
        STA $0102,X
        RTS
dest:   LDA #$77
        STA $C001
        RTS
"""

_TARGETS = [
    ("patch_opcode", _PATCH_OPCODE, ["entry"], [(0, 0)] * 2),
    ("patch_operand", _PATCH_OPERAND, ["entry"], [(0, 0)] * 2),
    ("patch_zp_code", _PATCH_ZP, ["entry"], [(0, 0)] * 2),
    ("patch_ptr_indy", _PATCH_PTR, ["entry"], [(0, 0)] * 2),
    ("patch_ptr_jmpind", _PATCH_PTR_IND, ["entry"], [(0, 0)] * 2),
    ("rmw_operand", _RMW_OPERAND, ["entry"], [(0, 0)] * 2),
    ("ret_addr", _RET_ADDR, ["entry"], [(0, 0)] * 2),
]


_CHANNELS = (
    "zp",
    "zp_indexed_wrap",
    "abs",
    "abs_indexed",
    "indirect",
    "unstable_high",
    "zp_rmw",
    "abs_rmw",
    "abs_indexed_rmw",
    "zp_indexed_wrap_rmw",
    "indirect_rmw",
)
_ROLES = ("opcode", "operand", "ptr_indirect", "ret_addr")
_TIMINGS = ("same_call_earlier", "earlier_call", "pre_lift", "same_insn")


def synthesized(progress=False):
    """Parametric witnesses over every (channel, role, timing) the synthesizer can express."""
    out = []
    combos = [(c, r, t) for c in _CHANNELS for r in _ROLES for t in _TIMINGS]
    for channel, role, timing in tqdm(combos, disable=not progress, desc="synth"):
        spec = witness(channel, role, timing)
        if spec is None:
            continue
        src, ents, calls, init = spec
        try:
            out.append(Program("%s.%s.%s" % (channel, role, timing), src, ents, calls, init))
        except Exception:  # pylint: disable=broad-exception-caught
            continue
    return out


def targeted(progress=False):
    """Deterministic targeted witnesses, one per writer/role/timing family."""
    out = []
    for name, src, ents, calls in tqdm(_TARGETS, disable=not progress, desc="targeted"):
        try:
            out.append(Program(name, src, ents, calls))
        except Exception:  # pylint: disable=broad-exception-caught
            continue
    return out


def _rand_prog(rng, n):
    """A random SMC-heavy straight-line program body ending in RTS."""
    lines = ["entry:"]
    for i in range(n):
        kind = rng.choice(["imm", "op", "inc_zp", "inc_abs", "io", "ptr", "absx"])
        tag = "s%d" % i
        if kind == "imm":
            lines += [
                "        LDA #$%02X" % rng.randint(0, 255),
                "        STA %s+1" % tag,
                "%s:  LDA #$00" % tag,
            ]
        elif kind == "op":
            lines += [
                "        LDA #$%02X" % rng.choice([0xEA, 0x18, 0x38, 0xA9]),
                "        STA %s" % tag,
                "%s:  NOP" % tag,
            ]
        elif kind == "inc_zp":
            lines += ["        INC $%02X" % rng.randint(0xE0, 0xFF)]
        elif kind == "inc_abs":
            lines += ["        INC %s+1" % tag, "%s:  LDA #$00" % tag]
        elif kind == "io":
            lines += [
                "        LDA #$%02X" % rng.randint(0, 255),
                "        STA $C0%02X" % rng.randint(0, 15),
            ]
        elif kind == "absx":
            lines += [
                "        LDX #$%02X" % rng.randint(0, 3),
                "        LDA #$%02X" % rng.randint(0, 255),
                "        STA %s,X" % tag,
                "%s:  NOP" % tag,
                "        NOP",
                "        NOP",
                "        NOP",
            ]
        else:
            lines += [
                "        LDA #$%02X" % rng.randint(0, 255),
                "        STA $%02X" % rng.randint(0xE0, 0xFF),
            ]
    lines += ["        RTS"]
    return "\n".join(lines)


def fuzz(seed=0, rounds=300, length=6, progress=False):
    """Seeded SMC-heavy random programs; those the oracle can run become witnesses."""
    rng = random.Random(seed)
    out = []
    for i in tqdm(range(rounds), disable=not progress, desc="fuzz"):
        try:
            out.append(
                Program("fuzz%04d" % i, _rand_prog(rng, length), ["entry"], [(0, 0), (0, 1)])
            )
        except Exception:  # pylint: disable=broad-exception-caught
            continue
    return out


def machine(progress=False):
    """Machine-level witnesses labelled by the vagrant-moses oracle, if it is available."""
    if not machine_core():
        return []
    out = []
    for wit in tqdm(machine_witnesses(), disable=not progress, desc="machine"):
        prog = MachineProgram(wit)
        if prog.returned:
            out.append(prog)
    return out


def coverage(programs):
    """``(covered_sids, feasible_sids)`` for a program list."""
    feasible = {c.sid for c in cells() if decide(c).feasible}
    covered = set()
    for p in programs:
        covered |= p.sids
    return covered, feasible


def generate(seed=0, fuzz_rounds=400, progress=False):
    """Synthesis + fuzz + machine witnesses to a coverage fixpoint; kept programs and sids."""
    feasible = {c.sid for c in cells() if decide(c).feasible}
    covered = set()
    kept = []
    pool = (
        synthesized(progress)
        + targeted(progress)
        + fuzz(seed, fuzz_rounds, progress=progress)
        + machine(progress)
    )
    for prog in pool:
        gained = prog.sids - covered
        if gained:
            covered |= prog.sids
            kept.append(prog)
    return kept, covered, feasible
