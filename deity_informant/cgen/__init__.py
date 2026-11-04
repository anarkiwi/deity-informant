"""6502/6510 machine code as C: a static whole-program lift, MLIR, then DREAM (``docs/cgen.md``).

:func:`translate` gives a ``.c`` of the code alone and a ``.h`` of the data regions, so programs
that differ only in their data or their origin give the same ``.c``.
"""

from __future__ import annotations

import shutil
import subprocess
import time
from dataclasses import dataclass

import numpy as np

from .. import wholeprog
from ..trace import Facts
from . import emit, mlir, printer
from .regions import Memory, code_bytes, pointer_pairs

__all__ = ["Program", "jump_facts", "lift", "render", "translate"]

PROLOGUE = """#include <stdint.h>
#include "{header}"

uint8_t io_read(uint16_t a);
void io_write(uint16_t a, uint8_t v);
void trap(uint16_t k);
"""
OOB = {
    "oob_rd": (
        "uint8_t oob_rd(uint16_t a);",
        "static uint8_t oob_rd(uint16_t a) {\n"
        "return (uint16_t)(a - CODE_ORG) < CODE_LEN ? CODE[a - CODE_ORG] : 0;\n}",
    ),
    "oob_wr": (
        "void oob_wr(uint16_t a, uint8_t v);",
        "static void oob_wr(uint16_t a, uint8_t v) {\n(void)a;\n(void)v;\n}",
    ),
}


def jump_facts(facts, image):
    """The computed-jump edges of ``facts``: those leaving ``JMP (ind)``, ``RTS`` and ``RTI``
    and those of ``JMP``/``JSR`` going elsewhere than their operand, as ``Facts``."""
    f = facts if isinstance(facts, Facts) else Facts.from_dict(facts)
    edges = []
    for src, dst in f.edges:
        op = image[src]
        if op in (0x6C, 0x60, 0x40):
            edges.append((src, dst))
        elif op in (0x4C, 0x20) and dst != image[src + 1] | image[(src + 2) & 0xFFFF] << 8:
            edges.append((src, dst))
    return Facts(edges=sorted(set(edges)))


def _hex(data):
    return ", ".join(f"0x{b:02X}" for b in data)


def _slice(mem, used, k):
    """Whether region ``k`` lies in the code span held in memory: a slice of ``CODE``."""
    org, n = mem.regions[k]
    lo, hi = mem.code_span
    return "CODE" in used and lo <= org and org + n <= hi


def _header(mem, used, traps):
    """The ``.h``: origins, lengths and initial bytes of the regions (those inside the code
    span are its slices) and of the code span in ``used``."""
    out = ["#ifndef CGEN_DATA_H", "#define CGEN_DATA_H"]
    for k, (org, n) in enumerate(mem.regions):
        out += [f"#define R{k}_ORG 0x{org:04X}", f"#define R{k}_LEN {n}"]
        if not _slice(mem, used, k):
            out.append(f"#define R{k}_INIT {_hex(mem.image[org : org + n])}")
    if "CODE" in used:
        lo, hi = mem.code_span
        out += [f"#define CODE_ORG 0x{lo:04X}", f"#define CODE_LEN {hi - lo}"]
        out.append(f"#define CODE_INIT {_hex(mem.image[lo:hi])}")
        out.append(
            f"#define CODE_DATA_INIT {_hex(np.packbits(mem.owner[lo:hi] >= 0, bitorder='little'))}"
        )
    elif "CODE_ORG" in used:
        out.append(f"#define CODE_ORG 0x{mem.code_span[0]:04X}")
    if "IMG_ORG" in used:
        out.append(f"#define IMG_ORG 0x{mem.loaded[0]:04X}")
    out.append(f"#define CGEN_TRAPS {', '.join(f'0x{pc:04X}' for pc in traps)}")
    return "\n".join([*out, "#endif"]) + "\n"


def _dispatch(mem, helpers, used):
    """``rd``/``wr`` over the regions outside the code span, the volatile ranges, the code
    span in memory (whose data bytes ``CODE_DATA`` marks) and the out-of-bounds hooks, and
    ``codechk``: whether a byte of the code span outside the data regions would change."""
    out = []
    regions = [f"R{k}" for k in range(len(mem.regions)) if not _slice(mem, used, k)]
    vol = " || ".join(
        f"(a >= 0x{lo:04X} && a < 0x{hi:04X})" if hi < 0x10000 else f"a >= 0x{lo:04X}"
        for lo, hi in mem.volatile
    )
    span = "CODE" in used
    data = "(CODE_DATA[(a - CODE_ORG) >> 3] >> ((a - CODE_ORG) & 7) & 1)"
    inside = "(uint16_t)(a - CODE_ORG) < CODE_LEN"
    if "rd" in helpers:
        out += ["static uint8_t rd(uint16_t a) {"]
        out += [f"if ((uint16_t)(a - {r}_ORG) < {r}_LEN) return {r}[a - {r}_ORG];" for r in regions]
        out += [f"if ({vol}) return io_read(a);"] if vol else []
        out += [f"if ({inside}) return CODE[a - CODE_ORG];"] if span else []
        out += ["return oob_rd(a);", "}"]
    if "wr" in helpers:
        out += ["static void wr(uint16_t a, uint8_t v) {"]
        out += [
            f"if ((uint16_t)(a - {r}_ORG) < {r}_LEN) {{ {r}[a - {r}_ORG] = v; return; }}"
            for r in regions
        ]
        out += [f"if ({vol}) {{ io_write(a, v); return; }}"] if vol else []
        out += [f"if ({inside} && {data}) {{ CODE[a - CODE_ORG] = v; return; }}"] if span else []
        out += ["oob_wr(a, v);", "}"]
    if "codechk" in helpers:
        out += ["static uint8_t codechk(uint16_t a, uint8_t v) {"]
        out += [f"return {inside} && !{data} && CODE[a - CODE_ORG] != v;", "}"]
    return out


def clang_format(text):
    """``text`` formatted by ``clang-format --style=LLVM``."""
    exe = shutil.which("clang-format")
    if not exe:
        raise RuntimeError("needs clang-format on PATH")
    done = subprocess.run(
        [exe, "--style=LLVM", "--assume-filename=x.c"],
        input=text,
        capture_output=True,
        text=True,
        check=True,
    )
    return done.stdout


@dataclass
class Program:  # pylint: disable=too-many-instance-attributes
    """What :func:`render` prints: lifted functions (sorted by entry PC) and their machine."""

    funcs: list
    entries: list
    image: bytes
    starts: set
    volatile: list
    sensitive: tuple = ()
    loaded: tuple | None = None
    constant: frozenset | None = None
    jumps: dict | None = None
    sets: dict | None = None


def lift(image, entries, volatile, facts=None, loaded=None):
    """The static lift of ``entries`` as a :class:`Program` and its ``stats``; ``facts``
    give computed-jump targets only, ``loaded`` the ``(lo, hi)`` the program was loaded to."""
    image = bytes(image)
    pcs = [pc for pc, _ in entries]
    prog = wholeprog.lift(
        image,
        pcs,
        volatile,
        facts=facts,
        entry_sp=emit.ENTRY_REGS[3],
        loaded=loaded,
        fold=True,
    )
    lifted = prog.functions
    rank = sorted(range(len(lifted)), key=lambda i: lifted[i]["pc"])
    remap = {i: j for j, i in enumerate(rank)}
    funcs = [lifted[i] for i in rank]
    for b in (b for f in funcs for b in f["blocks"]):
        for op in b["ops"]:
            if op[0] == "CALL":
                op[3] = remap[op[3]]
    starts = {pc for p in wholeprog.plans(image, pcs, facts=facts) for pc in p["heights"]}
    p = Program(funcs, list(entries), image, starts, [tuple(v) for v in volatile])
    p.constant, p.jumps, p.sets = frozenset(prog.code), dict(prog.jumps), dict(prog.sets)
    return p, prog.stats


def render(p, header="cgen.h", prefix=None):  # pylint: disable=too-many-locals
    """``(c_text, h_text, info)`` of :class:`Program` ``p``: MLIR, structured and printed."""
    times, t0 = {}, time.perf_counter()
    text, traps = emit.emit(p.funcs, p.entries, keep_dummy=bool(p.sensitive))
    run = mlir.runner(build=True) if prefix is None else prefix
    if run is None:
        raise RuntimeError("needs mlir-opt on PATH or Docker")
    top = mlir.parse(mlir.optimize(text, run))
    times["mlir"] = time.perf_counter() - t0
    t1 = time.perf_counter()
    code = code_bytes(p.image, p.starts)
    cells = np.zeros_like(code)
    tables = None
    if p.constant is not None:
        constant = np.zeros(0x10000, dtype=bool)
        constant[list(p.constant)] = True
        cells = code & ~constant
        code &= constant
        tables = constant & ~code_bytes(p.image, p.starts)
    loaded = p.loaded
    if loaded is None:
        span = np.flatnonzero(code)
        loaded = (int(span[0]), int(span[-1]) + 1) if len(span) else (0, 0)
    pairs = pointer_pairs(p.image, p.starts)
    mem = Memory(p.image, code, p.volatile, loaded, p.sensitive, pairs, tables)
    mod = printer.Module(top, mem)
    mod.sets = {a: (min(v), max(v)) for a, v in (p.sets or {}).items() if v}
    mod.lifted = {f"f{j}": (fn["pc"], list(fn.get("copies", ()))) for j, fn in enumerate(p.funcs)}
    mod.lifted.update({name: (pc, []) for pc, name in p.entries})
    mod.copy_pcs = {
        f"f{j}": {q: pcs for pcs in fn.get("copy_pcs", ()) for q in pcs if q}
        for j, fn in enumerate(p.funcs)
    }
    mod.prune()
    mod.partition()
    times["layout"] = time.perf_counter() - t1
    t2 = time.perf_counter()
    mod.structure()
    times["structure"] = time.perf_counter() - t2
    t1 = time.perf_counter()
    fns = mod.functions()
    used, helpers = set(mod.used), mod.helpers
    helpers |= {"oob_rd"} if "rd" in helpers else set()
    helpers |= {"oob_wr"} if "wr" in helpers else set()
    if helpers & {"oob_rd", "codechk"}:
        used.add("CODE")
    regions = sorted(
        mod.regions_used | (set(range(len(mem.regions))) if helpers & {"rd", "wr"} else set())
    )
    c = [PROLOGUE.format(header=header)]
    # With the code span in memory, the regions inside it are its slices: one copy of each byte.
    if "CODE" in used:
        c.append("uint8_t CODE[CODE_LEN] = {CODE_INIT};")
    for k in regions:
        if _slice(mem, used, k):
            c.append(f"#define R{k} (CODE + (R{k}_ORG - CODE_ORG))")
        else:
            c.append(f"uint8_t R{k}[R{k}_LEN] = {{R{k}_INIT}};")
    oob = [OOB[h] for h in sorted(helpers & set(OOB))]
    if oob:
        c += ["#ifdef CGEN_OOB", *(d for d, _ in oob), "#else", *(f for _, f in oob)]
        c.append("#endif")
    if "CODE" in used and helpers & {"wr", "codechk"}:
        c.append("static const uint8_t CODE_DATA[] = {CODE_DATA_INIT};")
    c += _dispatch(mem, helpers, used)
    c += fns
    c_text = clang_format("\n".join(c) + "\n")
    h_text = clang_format(_header(mem, used, [key[1] for key in traps]))
    times["print"] = time.perf_counter() - t1
    info = {
        "times": times,
        "regions": mem.regions,
        "code_span": mem.code_span,
        "traps": {k: key for key, k in traps.items() if k in mod.traps},
        "unknown_access": mem.unknown,
        "jumps": p.jumps or {},
        "structure": dict(mod.made),
        "provenance": {**mod.prov, "cells": [int(a) for a in np.flatnonzero(cells)]},
    }
    return c_text, h_text, info


def translate(  # pylint: disable=too-many-arguments
    image,
    entries,
    volatile,
    sensitive_reads=(),
    facts=None,
    *,
    loaded=None,
    header="cgen.h",
    prefix=None,
):
    """``(c_text, h_text, info)`` of the program in ``image`` (64 KiB) called at ``entries``
    (``(pc, name)``, ``A`` the argument) with ``volatile`` ``[(lo, hi)]`` I/O ranges whose
    reads at ``sensitive_reads`` are kept; ``facts`` supply computed-jump targets only."""
    t0 = time.perf_counter()
    jf = jump_facts(facts, image) if facts is not None else None
    p, stats = lift(image, entries, volatile, jf, loaded)
    p.sensitive, p.loaded = tuple(sensitive_reads), loaded
    lift_time = time.perf_counter() - t0
    c, h, info = render(p, header, prefix)
    info["times"] = {"lift": lift_time, **info["times"]}
    info["facts_edges"] = list(jf.edges) if jf is not None else []
    info["stats"] = stats
    return c, h, info
