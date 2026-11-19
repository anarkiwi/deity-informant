"""6502/6510 machine code as C: a static whole-program lift, MLIR, then DREAM (``docs/cgen.md``).

:func:`translate` gives a ``.c`` of the code alone and a ``.h`` of the data regions, so programs
that differ only in their data or their origin give the same ``.c``.
"""

from __future__ import annotations

import copy
import itertools
import time
from dataclasses import dataclass
from types import SimpleNamespace

import numpy as np
from pycparser import c_ast
from wayward_eagle.c import format_c

from .. import c64devices, masks, polling, postinit, wholeprog
from ..trace import Facts

from . import cnodes as cn, cost, emit, mlir, printer, traps as trapinfo, units
from .cnodes import hexes
from ..lifter import MODE_LEN, OPS
from .regions import Memory, background, code_bytes, pointer_pairs

__all__ = ["Program", "jump_facts", "lift", "render", "translate"]

PROLOGUE = """#include <stdint.h>
#include "{header}"

uint8_t io_read(uint16_t a);
void io_write(uint16_t a, uint8_t v);
__attribute__((noreturn, leaf)) void trap(uint16_t k);
"""
OOB = {"oob_rd": "uint8_t oob_rd(uint16_t a);", "oob_wr": "void oob_wr(uint16_t a, uint8_t v);"}
#: Trap code of a read outside every array the program holds (not a ``CGEN_TRAPS`` index).
OOB_TRAP = 0xFFFF
#: The access helpers whose runtime traps ``OOB_TRAP`` on a byte it cannot keep exact.
OOB_HELPERS = frozenset({"rd", "oob_rd", "wr", "oob_wr"})
OOB_RD_TRAP = "uint8_t oob_rd(uint16_t a) {{ (void)a; trap(0x{:04X}); }}".format(OOB_TRAP)

#: The element of array ``a`` at a 16-bit offset ``b`` plus a byte index ``y``, modulo 2^16 as
#: the 6502's ``(zp),Y`` wraps: with 16-bit pointers the array's place at ``b`` indexed by
#: ``y``, which the compiler can make a ``(zp),Y``.
IX = """#if UINTPTR_MAX == 0xFFFF
#define CGEN_IX(a, b, y) ((a) + (uint16_t)(b))[y]
#else
#define CGEN_IX(a, b, y) (a)[(uint16_t)((uint16_t)(b) + (y))]
#endif"""


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


def _slices(mem, used):
    """Per region, the held span it is a slice of (none unless ``used`` holds the code)."""
    return [mem.holding(org, org + n - 1) if "CODE" in used else None for org, n in mem.regions]


def _header(mem, used, traps, konsts, store):
    """The ``.h``: origins, lengths and initial bytes of the regions (those inside a held span
    are its slices) and of the held spans when ``used``, the constant bytes ``konsts``
    (``{name: byte}``) the ``.c`` reads by name and the arrays' definitions ``store``."""
    out, slices = ["#ifndef CGEN_DATA_H", "#define CGEN_DATA_H", "#include <stdint.h>"], _slices(
        mem, used
    )
    for k, (org, n) in enumerate(mem.regions):
        out += [f"#define R{k}_ORG 0x{org:04X}", f"#define R{k}_LEN {n}"]
        if slices[k] is None and _block(mem, org, org + n) is None:
            out.append(f"#define R{k}_INIT {hexes(mem.init[org : org + n])}")
    for b in sorted(mem.blocks_used):
        lo, hi = mem.blocks[b]
        out += [f"#define B{b}_ORG 0x{lo:04X}", f"#define B{b}_LEN {hi - lo}"]
        out.append(f"#define B{b}_INIT {hexes(mem.block_init(b, 'CODE' in used))}")
    if "CODE" in used or "CODE_ORG" in used:
        out.append(f"#define CODE_ORG 0x{mem.code_span[0]:04X}")
    for j, (lo, hi) in enumerate(mem.held if "CODE" in used else ()):
        out += [f"#define C{j}_ORG 0x{lo:04X}", f"#define C{j}_LEN {hi - lo}"]
        if _block(mem, lo, hi) is None:
            out.append(f"#define C{j}_INIT {hexes(mem.init[lo:hi])}")
        if j in slices:
            # Past the span's last constant byte every byte is data: the bits stop there.
            top = lo + int(np.flatnonzero(mem.constant[lo:hi]).max(initial=-1)) + 1
            bits = np.packbits(mem.owner[lo : max(top, lo + 1)] >= 0, bitorder="little")
            out += [f"#define C{j}_BITS {top - lo}", f"#define C{j}_DATA_INIT {hexes(bits)}"]
    if "IMG_ORG" in used:
        out.append(f"#define IMG_ORG 0x{mem.loaded[0]:04X}")
    out += [f"#define {k} 0x{v:02X}" for k, v in sorted(konsts.items())]
    out.append(f"#define CGEN_TRAPS {hexes(traps, 4)}")
    return "\n".join([*out, *store, "#endif"]) + "\n"


def _inside(r):
    return f"(uint16_t)(a - {r}_ORG) < {r}_LEN"


def _obj_h(j, obj, parts):
    """The ``.h`` bounds of object ``j`` (``obj``, ``(lo, hi)`` inclusive): its origin and
    limit, or of one of several runs ``parts`` (``hi`` exclusive) their origins and limits."""
    if not parts:
        return "\n".join(
            f"#define CGEN_OBJ{j}_{k} 0x{v:04X}"
            for k, v in (("ORG", obj[0]), ("LIM", obj[1] - obj[0]))
        )
    los = ", ".join(f"0x{lo:04X}" for lo, _ in parts)
    lims = ", ".join(f"0x{hi - 1 - lo:X}" for lo, hi in parts)
    return f"#define CGEN_OBJ{j}_LO {los}\n#define CGEN_OBJ{j}_LIM {lims}"


def _data(j):
    off = f"(uint16_t)(a - C{j}_ORG)"
    return f"({off} >= C{j}_BITS || C{j}_DATA[{off} >> 3] >> ({off} & 7) & 1)"


def _var(cases, ext=False):
    """``cgen_var``: the promoted cell at ``a``, by the ``cases`` (``[(offset, lvalue)]``, the
    offset from ``CODE_ORG``), else a null pointer; ``cgen_var_at`` tests their range first."""
    lo, hi = cases[0][0], cases[-1][0]
    return [
        "static __attribute__((noinline)) uint8_t *cgen_var(uint16_t a) {",
        "switch ((uint16_t)(a - CODE_ORG)) {",
        *(f"case 0x{d:X}: return &{ref};" for d, ref in cases),
        "}",
        "return 0;",
        "}",
        f"{'' if ext else 'static inline '}uint8_t *cgen_var_at(uint16_t a) {{",
        f"return (uint16_t)(a - (CODE_ORG + 0x{lo:X})) <= 0x{hi - lo:X} ? cgen_var(a) : 0;",
        "}",
    ]


#: A reader's test for a promoted cell at ``a`` (``cgen_var``), and a writer's.
VAR_RD = ["uint8_t *c = cgen_var_at(a);", "if (c) return *c;"]
VAR_WR = ["uint8_t *c = cgen_var_at(a);", "if (c) {", "*c = v;", "return;", "}"]


def _runs(t):
    """The tables of the runs ``CGEN_t_*``."""
    return [f"static const uint16_t {t}_{k}[] = {{CGEN_{t}_{k}}};" for k in ("ORG", "LEN")]


#: The tables of a byte no array holds: the program's runs (``CGEN_PROG_*``) and the
#: background table (``CGEN_BG*``).
UNHELD = [
    *_runs("PROG"),
    *(f"static const uint16_t BG_{k}[] = {{CGEN_BG_{k}}};" for k in ("ORG", "AT", "M")),
    "static const uint8_t BG[] = {CGEN_BG_INIT};",
]
#: Not the host's ``oob_wr``.
NOOB = "!defined(CGEN_OOB)"
#: ``cgen_ram``: the byte at ``a`` of the bytes no array holds a store may write
#: (``CGEN_RAM_*``: their runs, each run's offset into ``RAM`` and the entry bytes), else a
#: null pointer.
RAM = [
    "static uint8_t *cgen_ram(uint16_t a) {",
    "#if CGEN_RAM_N",
    *_runs("RAM"),
    "static const uint16_t RAM_AT[] = {CGEN_RAM_AT};",
    "static uint8_t RAM[CGEN_RAM_N] = {CGEN_RAM_INIT};",
    "for (uint8_t i = 0; i < sizeof RAM_ORG / sizeof *RAM_ORG; ++i) {",
    "uint16_t o = a - RAM_ORG[i];",
    "if (o < RAM_LEN[i])",
    "return RAM + RAM_AT[i] + o;",
    "}",
    "#endif",
    "(void)a;",
    "return 0;",
    "}",
]


def _within(t):
    """A test of ``a`` in one of the runs ``t`` (``t_ORG``, ``t_LEN``)."""
    return [
        f"for (uint16_t i = 0; i < sizeof {t}_ORG / sizeof *{t}_ORG; ++i) {{",
        f"if ((uint16_t)(a - {t}_ORG[i]) < {t}_LEN[i])",
    ]


def _unheld(ram):
    """``cgen_unheld``: the byte read at ``a`` where no array holds it: a byte of the program
    traps (``OOB_TRAP``), one a store may write (where the conditions ``ram`` hold) reads
    ``cgen_ram``, any other the machine's entry byte, its run's in the background table."""
    out = ["static uint8_t cgen_unheld(uint16_t a) {", *_within("PROG"), f"trap(0x{OOB_TRAP:04X});"]
    out.append("}")
    out += _guarded(["uint8_t *r = cgen_ram(a);", "if (r)", "return *r;"], ram)
    return out + [
        "uint16_t i = sizeof BG_ORG / sizeof *BG_ORG - 1;",
        "while (a < BG_ORG[i])",
        "--i;",
        "return BG[(uint16_t)(BG_AT[i] + (a & BG_M[i]))];",
        "}",
    ]


def _unheld_wr(spans):
    """``cgen_unheld_wr``: the write of ``v`` at ``a`` where no region holds it, exact as
    :func:`_unheld` reads it back or a trap (``OOB_TRAP``): a byte a store may write
    (``cgen_ram``) takes it; a constant byte of a held span ``spans`` or of the program, or
    one outside every store's addresses, keeps its value or traps."""
    out = ["static __attribute__((noinline)) void cgen_unheld_wr(uint16_t a, uint8_t v) {"]
    for j in spans:
        out += [f"if ({_inside(f'C{j}')}) {{", f"if (C{j}[a - C{j}_ORG] != v)"]
        out += [f"trap(0x{OOB_TRAP:04X});", "return;", "}"]
    out += ["uint8_t *r = cgen_ram(a);", "if (r) {", "*r = v;", "return;", "}"]
    return out + ["if (cgen_unheld(a) != v)", f"trap(0x{OOB_TRAP:04X});", "}"]


def _rd(regions, vol, spans, var=False):
    """``cgen_rd``: the byte the 6502 reads at ``a``, by the promoted cells (with ``var``), the
    volatile condition ``vol``, the origins and lengths of ``regions`` and the held ``spans``,
    then where the program holds none :func:`_unheld`."""
    arrays = [*regions, *(f"C{j}" for j in spans)]
    out = ["static __attribute__((noinline)) uint8_t cgen_rd(uint16_t a) {"]
    out += VAR_RD if var else []
    out += [f"if ({vol}) return io_read(a);"] if vol else []
    if not arrays:
        return out + ["return cgen_unheld(a);", "}"]
    tables = ["static uint8_t *const OOB_AT[] = {CGEN_ARRAYS};"] + [
        f"static const uint16_t OOB_{k}[] = {{CGEN_ARRAYS_{k}}};" for k in ("ORG", "LEN")
    ]
    return (
        tables
        + out
        + [
            "for (uint8_t i = 0; i < sizeof OOB_ORG / sizeof *OOB_ORG; ++i) {",
            "uint16_t o = a - OOB_ORG[i];",
            "if (o < OOB_LEN[i]) return OOB_AT[i][o];",
            "}",
            "return cgen_unheld(a);",
            "}",
        ]
    )


def _unheld_h(mem, used, cases, program, ram=None):
    """The ``.h``'s tables of :func:`_unheld` over the bytes ``cgen_rd`` finds in no array
    (none in a region, a held span when ``used`` holds the code, or a promoted cell of
    ``cases``) outside the volatile ranges: those of ``loaded`` and the ``program`` mask
    (init's stores) as runs ``CGEN_PROG_*`` (joined across the held bytes), unless ``ram`` is
    None the others a store may write (the ``ram`` mask, every byte when True) as runs
    ``CGEN_RAM_*`` (joined across the held and the program's, each run's offset ``_AT`` into
    the entry bytes ``_INIT``, ``_N`` of them), the machine's entry bytes (``Memory.image``)
    of the rest as :func:`regions.background`'s table."""
    held = (mem.owner >= 0) | mem.vol
    masks.mask(
        mem.held if "CODE" in used else (),
        at=[(mem.code_span[0] + d) & 0xFFFF for d, _ in cases],
        out=held,
    )
    prog = program.copy()
    prog[mem.loaded[0] : mem.loaded[1]] = True
    prog &= ~held
    out, kinds = [], [("PROG", prog, held)]
    rest = ~prog & ~held
    if ram is not None:
        kinds.append(("RAM", rest & ram, np.zeros_like(held)))
    for name, mask, join in kinds:
        runs = []
        for o, n in masks.runs(mask | join):
            t = np.flatnonzero(mask[o : o + n])
            if t.size:
                runs.append((o + int(t[0]), int(t[-1] - t[0]) + 1))
        # A run of every byte is two: a length is 16 bits.
        runs = [(0, 0xFFFF), (0xFFFF, 1)] if runs == [(0, 0x10000)] else runs
        out += [
            f"#define CGEN_{name}_ORG {hexes((o for o, _ in runs), 4) or '0'}",
            f"#define CGEN_{name}_LEN {hexes((n for _, n in runs), 0) or '0'}",
        ]
        if name == "RAM":
            at = np.cumsum([0] + [n for _, n in runs])
            data = b"".join(bytes(mem.image[o : o + n]) for o, n in runs)
            out += [
                f"#define CGEN_RAM_AT {hexes(at[:-1], 0) or '0'}",
                f"#define CGEN_RAM_N {len(data)}",
                f"#define CGEN_RAM_INIT {hexes(data) or '0'}",
            ]
            rest &= ~mask
    entries, pool = background(mem.image, rest)
    out += [
        f"#define CGEN_BG_{k} {hexes((e[i] for e in entries), 0)}"
        for i, k in enumerate(("ORG", "AT", "M"))
    ]
    return out + [f"#define CGEN_BG_INIT {hexes(pool)}"]


def _wr(vol, var=False, ext=False):
    """``wr``: a write dispatcher over the promoted cells (with ``var``), the regions
    (``CGEN_REGIONS``, slices of held spans included: those are the spans' data bytes) and the
    volatile condition ``vol``, then the host's ``oob_wr`` (``CGEN_OOB``) or
    :func:`_unheld_wr`."""
    out = ["static uint8_t *const WR_AT[] = {CGEN_REGIONS};"] + [
        f"static const uint16_t WR_{k}[] = {{CGEN_REGIONS_{k}}};" for k in ("ORG", "LEN")
    ]
    out += [
        f"{'' if ext else 'static '}__attribute__((noinline)) void wr(uint16_t a, uint8_t v) {{"
    ]
    out += VAR_WR if var else []
    out += [
        "for (uint8_t i = 0; i < sizeof WR_ORG / sizeof *WR_ORG; ++i) {",
        "uint16_t o = a - WR_ORG[i];",
        "if (o < WR_LEN[i]) {",
        "WR_AT[i][o] = v;",
        "return;",
        "}",
        "}",
    ]
    out += [f"if ({vol}) {{ io_write(a, v); return; }}"] if vol else []
    tail = ["#ifdef CGEN_OOB", "oob_wr(a, v);", "#else", "cgen_unheld_wr(a, v);", "#endif"]
    return out + tail + ["}"]


def _block(mem, lo, hi):
    """The block array the ``.c`` uses holding ``[lo, hi)``, else None."""
    b = mem.block_of(lo, hi - 1)
    return b if b in mem.blocks_used else None


def _storage(mem, used, helpers, regions):
    """The ``.h``'s arrays, laid out by where the song puts its data: ``CGEN_STORAGE`` (the
    ``.c`` defines them by it) holds the held spans, the ``regions`` not inside one and the
    spans' data bits; a region inside a span is its slice; ``rd`` and ``wr`` find them by
    the ``CGEN_ARRAYS`` and ``CGEN_REGIONS`` lists; ``CGEN_EXTERN`` declares them."""
    spans = range(len(mem.held)) if "CODE" in used else range(0)
    blocks = sorted(mem.blocks_used)
    defs = [("uint8_t", f"B{b}", f"B{b}_LEN", f"B{b}_INIT") for b in blocks]
    out, slices = [], _slices(mem, used)
    for j in spans:
        b = _block(mem, *mem.held[j])
        if b is None:
            defs.append(("uint8_t", f"C{j}", f"C{j}_LEN", f"C{j}_INIT"))
        else:
            out.append(f"#define C{j} (B{b} + (C{j}_ORG - B{b}_ORG))")
    for k in regions:
        j, (org, n) = slices[k], mem.regions[k]
        b = _block(mem, org, org + n)
        if j is not None:
            out.append(f"#define R{k} (C{j} + (R{k}_ORG - C{j}_ORG))")
        elif b is not None:
            out.append(f"#define R{k} (B{b} + (R{k}_ORG - B{b}_ORG))")
        else:
            defs.append(("uint8_t", f"R{k}", f"R{k}_LEN", f"R{k}_INIT"))
    for j in spans if "codechk" in helpers else ():
        if j in slices:
            defs.append(("const uint8_t", f"C{j}_DATA", "", f"C{j}_DATA_INIT"))
    out.append(
        f"#define CGEN_STORAGE {' '.join(f'{t} {n}[{d}] = {{{i}}};' for t, n, d, i in defs)}"
    )
    out.append(f"#define CGEN_EXTERN {' '.join(f'extern {t} {n}[{d}];' for t, n, d, _ in defs)}")
    whole = [f"R{k}" for k in regions if slices[k] is None]
    for name, arrays in (
        ("ARRAYS", [*whole, *(f"C{j}" for j in spans)]),
        ("REGIONS", [f"R{k}" for k in regions]),
    ):
        out.append(f"#define CGEN_{name} {', '.join(arrays)}")
        out += [
            f"#define CGEN_{name}_{x} {', '.join(f'{a}_{x}' for a in arrays)}"
            for x in ("ORG", "LEN")
        ]
    return out


def _guarded(lines, conds):
    """``lines`` compiled where one of the preprocessor conditions ``conds`` holds (``"1"``:
    always), none when there are none."""
    if not lines or not conds:
        return []
    if "1" in conds:
        return lines
    test = conds[0] if len(conds) == 1 else " || ".join(f"({c})" for c in conds)
    return [f"#if {test}", *lines, "#endif"]


def _dispatch(
    mem, helpers, used, cases, program, ram, ext=False
):  # pylint: disable=too-many-arguments,too-many-positional-arguments,too-many-locals
    """``(c lines, h lines)``: ``oob_rd`` (unless the host's) and ``wr`` over the regions
    outside the held spans, the volatile ranges and the held spans (whose data bytes ``Cj_DATA``
    marks), ``wr`` then :func:`_unheld_wr` (unless the host's ``oob_wr``, which ``wr`` then
    calls), ``oob_wr`` (unless the host's) ``wr``; ``rd``, ``oob_rd`` for a load the lift
    places nowhere; and ``codechk``: whether a held constant byte would change. ``cgen_rd``
    and ``oob_wr`` find the promoted cells ``cases`` (``cgen_var``) first, as ``wr`` does
    where an access needs it, and the bytes in no array by :func:`_unheld_h` (``program``
    init's stores, ``ram`` the bytes a store may write, every one when True); ``ext``: the
    helpers other units call external."""
    out, h, link = [], [], "" if ext else "static "
    vol = " || ".join(
        f"(a >= 0x{lo:04X} && a < 0x{hi:04X})" if hi < 0x10000 else f"a >= 0x{lo:04X}"
        for lo, hi in mem.volatile
    )
    spans = range(len(mem.held)) if "CODE" in used else range(0)
    slices = _slices(mem, used)
    sliced = [j for j in spans if j in slices]
    regions = [f"R{k}" for k, j in enumerate(slices) if j is None]
    writes = bool(helpers & {"wr", "oob_wr"})
    exact = NOOB + " && defined(CGEN_OOB_EXACT)"
    rd = ["1"] if "rd" in helpers else [exact] if "oob_rd" in helpers else []
    unheld = rd + ([NOOB] if writes else [])
    var = ["1"] if helpers & {"cgen_var", "rd"} else rd + ([NOOB] if "oob_wr" in helpers else [])
    var = var if cases else []
    if var:
        used.add("CODE_ORG")
        out += _guarded(_var(cases, ext), var)
    if unheld:
        program = np.zeros(0x10000, dtype=bool) if program is None else program
        h = _unheld_h(mem, used, cases if var else (), program, ram if writes else None)
        inner = ([NOOB] if unheld != [NOOB] else ["1"]) if writes else []
        out += _guarded(UNHELD + _guarded(RAM, inner) + _unheld(inner), unheld)
    out += _guarded(_rd(regions, vol, spans, bool(var)), rd)
    if "rd" in helpers:
        out.append(
            f"{'' if ext else 'static inline '}uint8_t rd(uint16_t a) {{ return cgen_rd(a); }}"
        )
    oob = sorted(helpers & set(OOB) | ({"oob_wr"} if writes else set()))
    if oob:
        out += ["#ifdef CGEN_OOB", *(OOB[h] for h in oob)]
        if "oob_rd" in oob:
            out += ["#else", "#ifdef CGEN_OOB_EXACT"]
            out += [link + "uint8_t oob_rd(uint16_t a) { return cgen_rd(a); }", "#else"]
            out += [link + OOB_RD_TRAP, "#endif"]
        out.append("#endif")
    if writes:
        out += _guarded(_unheld_wr(spans), [NOOB])
        out += _guarded(_wr(vol, "cgen_var" in helpers, ext), ["1" if "wr" in helpers else NOOB])
        own = VAR_WR if var and "cgen_var" not in helpers else []
        oob_wr = [link + "void oob_wr(uint16_t a, uint8_t v) {", *own, "wr(a, v);", "}"]
        out += _guarded(oob_wr, [NOOB] if "oob_wr" in helpers else [])
    if "codechk" in helpers:
        out += [link + "__attribute__((noinline)) uint8_t codechk(uint16_t a, uint8_t v) {"]
        for j in spans:
            nd = f" && !{_data(j)}" if j in sliced else ""
            out += [f"if ({_inside(f'C{j}')}) return C{j}[a - C{j}_ORG] != v{nd};"]
        out += ["(void)a;", "(void)v;", "return 0;", "}"]
    return out, h


#: The init entry's argument ``a`` as an index into the ``.h``'s range of its values.
SONG = "(uint8_t)(a - CGEN_SONG0)"


def _song_trap(k):
    """The init entry's trap ``k`` on a song outside the ``.h``'s range."""
    return [f"if ({SONG} >= CGEN_SONGS) {{", f"trap({k});", "}"]


def _song_check(k):
    """The init entry's trap ``k`` on a song outside the ``.h``'s range, as a statement."""
    song = cn.cast("uint8_t", cn.binop("-", cn.ident("a"), cn.ident("CGEN_SONG0")))
    test = cn.binop(">=", song, cn.ident("CGEN_SONGS"))
    return cn.when(test, [cn.call("trap", cn.const(k))])


def _checked_reader(j, var):
    """``rdCj``: held span ``j``'s bounds-checked reader of a base plus a byte, the promoted
    cells first with ``var``."""
    p, y, a, o = (cn.ident(x) for x in "pyao")
    org, n = cn.ident(f"C{j}_ORG"), cn.ident(f"C{j}_LEN")
    body = []
    if var:
        cell = cn.ident("c")
        body += [
            cn.decl("a", "uint16_t", cn.binop("+", p, y)),
            cn.decl("c", "uint8_t *", cn.call("cgen_var_at", a)),
            cn.when(cell, [cn.ret(c_ast.UnaryOp("*", cell))]),
        ]
    body += [
        cn.decl("o", "uint16_t", cn.binop("+", cn.binop("-", p, org), y)),
        cn.ret(
            c_ast.TernaryOp(
                cn.binop("<", o, n), cn.index(f"C{j}", o), cn.call("oob_rd", cn.binop("+", p, y))
            )
        ),
    ]
    params = [("p", "uint16_t"), ("y", "uint8_t")]
    head = cn.function(f"rdC{j}", "uint8_t", params, ("static",), ("noinline",))
    return cn.define(head, body)


def _tagged(node, rec):
    """``node`` printing marker record ``rec``."""
    cn.prov(node).rec = rec
    return node


def _songs_h(state):
    """The ``.h``'s range of init's songs (those of :class:`postinit.Init` ``state``)."""
    return [f"#define CGEN_SONG0 0x{state.songs[0]:02X}", f"#define CGEN_SONGS {len(state.songs)}"]


def _init_c(name, k):
    """The init entry ``name``: it traps (``k``) on a song outside the ``.h``'s range, then
    replays init's volatile writes and makes the song's copies into the variables, from the
    ``.h`` (``CGEN_IO_*``, ``CGEN_CP_*``, ``CGEN_LIT*``)."""
    return [
        "#if CGEN_IO_N",
        "static const uint16_t IO_AT[CGEN_IO_N] = {CGEN_IO_AT};",
        "static const uint8_t IO_VAL[CGEN_IO_N] = {CGEN_IO_VAL};",
        "static const uint16_t IO_FROM[CGEN_IO_ROWS + 1] = {CGEN_IO_FROM};",
        "#if CGEN_IO_ROWS > 1",
        "static const uint8_t IO_ROW[CGEN_SONGS] = {CGEN_IO_ROW};",
        "#endif",
        "#endif",
        "#if CGEN_LIT_N",
        "static const uint8_t LIT[CGEN_LIT_N] = {CGEN_LIT};",
        "#endif",
        "#if CGEN_CP_N",
        "static uint8_t *const CP_DST[CGEN_CP_N] = {CGEN_CP_DST};",
        "static const uint8_t *const CP_SRC[CGEN_CP_N] = {CGEN_CP_SRC};",
        "static const uint16_t CP_LEN[CGEN_CP_N] = {CGEN_CP_LEN};",
        "static const uint16_t CP_FROM[CGEN_SONGS + 1] = {CGEN_CP_FROM};",
        "#endif",
        f"void {name}(uint8_t a) {{",
        *_song_trap(k),
        "#if CGEN_IO_N",
        "#if CGEN_IO_ROWS > 1",
        f"uint8_t r = IO_ROW[{SONG}];",
        "#else",
        "uint8_t r = 0;",
        "#endif",
        "for (uint16_t i = IO_FROM[r]; i < IO_FROM[r + 1]; ++i) {",
        "io_write(IO_AT[i], IO_VAL[i]);",
        "}",
        "#endif",
        "#if CGEN_CP_N",
        f"for (uint16_t i = CP_FROM[{SONG}]; i < CP_FROM[{SONG} + 1]; ++i) {{",
        "for (uint16_t j = 0; j < CP_LEN[i]; ++j) {",
        "CP_DST[i][j] = CP_SRC[i][j];",
        "}",
        "}",
        "#endif",
        "}",
    ]


def _io_h(state):
    """The ``.h``'s replay of init's volatile writes: each distinct sequence once, the songs'
    rows into them."""
    rows = list(dict.fromkeys(tuple(w) for w in state.writes))
    flat = [w for r in rows for w in r]
    out = [f"#define CGEN_IO_N {len(flat)}"]
    if flat:
        ends = np.cumsum([0] + [len(r) for r in rows])
        out += [
            f"#define CGEN_IO_ROWS {len(rows)}",
            f"#define CGEN_IO_AT {hexes((x for x, _ in flat), 4)}",
            f"#define CGEN_IO_VAL {hexes(v for _, v in flat)}",
            f"#define CGEN_IO_FROM {', '.join(str(e) for e in ends)}",
        ]
        if len(rows) > 1:
            index = [rows.index(tuple(w)) for w in state.writes]
            out.append(f"#define CGEN_IO_ROW {', '.join(map(str, index))}")
    return out


def _array_of(mem, a):
    """``(name, origin)`` of the region holding byte ``a``, None when none does."""
    k = int(mem.owner[a])
    return (f"R{k}", mem.regions[k][0]) if k >= 0 else None


def _copies_h(mem, plan, var):
    """The ``.h``'s copies (``plan``, :meth:`deity_informant.postinit.Init.copies`) into the
    regions the program holds and its promoted cells (``var``: an address's lvalue, or None),
    cut where a region ends, the literal bytes they copy, and the regions they name."""
    dst, src, lens, ends, lit, used = [], [], [], [0], bytearray(), set()
    for ops in plan:
        for d, s, n in ops:
            literal = isinstance(s, bytes)

            def key(i, d=d, s=s, literal=literal):
                ref = var((d + i) & 0xFFFF)
                to = (f"&{ref}", d + i) if ref is not None else _array_of(mem, d + i)
                frm = "LIT" if literal else _array_of(mem, s + i)
                return (to, frm) if to is not None and frm is not None else None

            for k, run in itertools.groupby(range(n), key):
                if k is None:
                    continue
                i = next(run)
                m = 1 + sum(1 for _ in run)
                if k[0][0].startswith("&"):
                    dst.append(k[0][0])
                else:
                    dst.append(f"{k[0][0]} + {d + i - k[0][1]}")
                    used.add(int(mem.owner[d + i]))
                if literal:
                    src.append(f"LIT + {len(lit)}")
                    lit += s[i : i + m]
                else:
                    src.append(f"{k[1][0]} + {s + i - k[1][1]}")
                    used.add(int(mem.owner[s + i]))
                lens.append(m)
        ends.append(len(dst))
    out = [f"#define CGEN_LIT_N {len(lit)}", f"#define CGEN_CP_N {len(dst)}"]
    if lit:
        out.append(f"#define CGEN_LIT {hexes(lit)}")
    if dst:
        out += [
            f"#define CGEN_CP_DST {', '.join(dst)}",
            f"#define CGEN_CP_SRC {', '.join(src)}",
            f"#define CGEN_CP_LEN {', '.join(map(str, lens))}",
            f"#define CGEN_CP_FROM {', '.join(map(str, ends))}",
        ]
    return out, used


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
    written: list = ()
    bounds: dict | None = None
    addr_sets: dict | None = None
    #: The accesses into the data block, ``{(pc, store): runs}`` (``Program.data``).
    data: dict | None = None
    #: Per ``(zp),Y`` access, the words its pointer may hold (``Program.ptr_sets``).
    ptr_sets: dict | None = None
    #: The pointer objects ``[(lo, hi)]`` the functions' ``objchk`` checks name, by index.
    objects: list = ()
    #: Per object of several runs (a data block), its runs ``[(lo, hi)]`` (``hi`` exclusive).
    parts: dict | None = None
    #: Per object of a pointer's check after its definition, ``(block, lo, k)``: the block it
    #: checks the pointer plus ``lo`` in with ``k`` bytes past it (``Program.slack``).
    slack: dict | None = None
    reach: dict | None = None
    proven: dict | None = None
    #: Per access of no one address its hull and its base's (``Program.bases``).
    bases: dict | None = None
    #: The instruction starts whose accesses the memory map checks (``Program.machine``).
    machine: frozenset = frozenset()
    regs: dict | None = None
    bounded: dict | None = None
    init: tuple | None = None
    #: The lift each function comes from (0: the entries', 1: init's), when init is lifted.
    origin: list | None = None
    #: The memory init's lift starts from, and its constant bytes, starts and facts.
    start: bytes | None = None
    lifted: tuple | None = None
    #: The bytes init leaves constant for the other entries (*Init*), ascending.
    fixed: np.ndarray | None = None
    #: The stores of the entries' lift and init's (``"play"``, ``"init"``: :func:`traps.stores`).
    stores: dict | None = None
    #: The guards ``(kind, at, reg)`` of init's lift its declared argument values imply.
    entailed: frozenset = frozenset()
    #: The bytes init stores for some argument value it takes (:func:`_domain`).
    init_stored: np.ndarray | None = None
    #: Per lift (the entries', init's), its pointer definitions and where they reach
    #: (``Program.pdefs``, ``pdefs_at``, ``pdefs_past``).
    pdefs: list = ()


def _ranked(progs):
    """The functions of the lifts ``progs`` sorted by entry PC, their ``CALL`` s renumbered,
    and the index of the lift each comes from."""
    items = [(fn, k, i) for k, prog in enumerate(progs) for i, fn in enumerate(prog.functions)]
    order = sorted(range(len(items)), key=lambda j: items[j][0]["pc"])
    remap = {items[j][1:]: n for n, j in enumerate(order)}
    funcs, origin = [], []
    for j in order:
        fn, k, _ = items[j]
        for op in (op for op in emit.ops(fn) if op[0] == "CALL"):
            op[3] = remap[(k, op[3])]
        funcs.append(fn)
        origin.append(k)
    return funcs, origin


def _reads_fixed(image, starts, fixed):
    """The instructions of ``starts`` (in ``image``) whose data operand holds a byte of
    ``fixed``: not a branch, jump or call, whose operand is a target deciding the code."""
    fixed = masks.mask(at=fixed)
    out = set()
    for pc in starts:
        mn, mode = OPS.get(image[pc], ("", "impl"))
        n = MODE_LEN[mode]
        if (
            mode != "rel"
            and mn not in ("JMP", "JSR")
            and fixed[(pc + np.arange(1, n)) & 0xFFFF].any()
        ):
            out.add(pc)
    return out


def _exit_tests(fn, pcs):
    """The ops of ``fn`` testing a side exit at an instruction of ``pcs``: those of that
    instruction ending in a ``CBRANCH`` to a block that deopts there."""
    blocks = fn["blocks"]
    for ops in (b["ops"] for b in blocks):
        for t, op in enumerate(ops):
            last = blocks[op[3]]["ops"][-1] if op[0] == "CBRANCH" and op[4] in pcs else None
            if last is None or last[0] != "CALLOTHER" or last[2][0] != ["c", op[4], 2]:
                continue
            yield from itertools.takewhile(lambda g, pc=op[4]: g[4] == pc, reversed(ops[:t]))


def _guard_symbols(funcs, pcs):
    """``funcs`` with the byte constants of each side exit's test at an instruction of
    ``pcs`` (:func:`_exit_tests`) made symbols ``["k", j, 1]``, and ``[(pc, value)]`` of the
    symbols: the bounds the lift draws from bytes init leaves constant are named by the
    ``.h``."""
    out = []
    for g in (g for fn in funcs for g in _exit_tests(fn, pcs)):
        for i, v in enumerate(g[2]):
            if v and v[0] == "c" and v[2] == 1:
                g[2][i] = ["k", len(out), 1]
                out.append((g[4], v[1]))
    return out


def _renumber(funcs, origin, base):
    """Number the objects the ``objchk`` checks of the functions of init's lift name (origin 1)
    after the ``base`` of the other entries' lift."""
    for fn in (fn for fn, k in zip(funcs, origin) if k):
        for op in emit.ops(fn):
            if op[0] == "CALLOTHER" and op[3] == "objchk":
                op[2][1] = [op[2][1][0], op[2][1][1] + base, op[2][1][2]]


#: The facts a lift gives the layout, as :class:`Program` holds them.
_FACTS = ("sets", "bounds", "regs", "reach", "proven", "bounded", "addr_sets", "bases", "data")


def _raw(src):
    """``{name: dict}`` of the :data:`_FACTS` of ``src``, a lift or a :class:`Program`."""
    return {k: dict(getattr(src, k) or {}) for k in _FACTS}


def _facts(src):
    """The facts of ``src`` (:func:`_raw`) the layout reads (:meth:`printer.Module.fx`)."""
    fx = _raw(src)
    sets = {a: (min(v), max(v)) for a, v in fx.pop("sets").items() if v}
    fx["sets"] = {**fx.pop("bounds"), **sets}
    fx["regs"] = {pc: tuple(bytes(x) for x in r) for pc, r in fx["regs"].items()}
    return SimpleNamespace(**fx)


def _lift_init(once, start, entry, state, runs, bare):
    """Init's lift (``once`` at ``entry`` from ``start``, for any argument value: the host's
    check of the declared ones is the ``.h``'s range) when it is exact for each declared value:
    its lift without throw-away reads ``bare`` reproduces each of the reference's ``runs`` per
    value (:func:`deity_informant.postinit.reproduces`: polls and probes answered as there,
    one run the machine's) and their accesses lie inside what it bounds them to; else None."""
    if None in runs or not postinit.reproduces(bare, start, state, runs):
        return None
    lone = once(entry)
    return lone if postinit.bounded(lone, [r for rs in runs for r in rs]) else None


def _guards(prog):
    """``{(kind, at, reg)}`` of the guards the deopts and switch defaults of lift ``prog``'s
    functions leave by."""
    out = set()
    for fn in prog.functions:
        whys = fn["whys"]
        for op in emit.ops(fn):
            if op[0] in ("CALLOTHER", "BRANCHIND"):
                out |= {
                    (whys[j]["kind"], whys[j]["at"], whys[j]["reg"]) for j in emit.exits(fn, op)
                }
    return out


def _entailed(once, entry, lone, songs):
    """The guards of init's lift ``lone`` (:func:`_guards`) its lift (``once`` at ``entry``)
    for the declared argument values ``songs`` alone leaves out (``checked``: the host's check
    of them is the entry's): implied by the entry's declaration. That lift decides nothing of
    the ``.c``."""
    return frozenset(_guards(lone) - _guards(once((entry, {"a": songs}), checked=True)))


def _fixed(start, prog, lifted, every):
    """The bytes init (on ``start``) leaves constant for the entries lifted as ``prog`` from its
    runs (*Init*): of the bytes it stores on those runs (``lifted``: their
    :class:`postinit.Init` and init's lift), those holding one value after it for every value
    of ``A`` it returns with on the machine (``every``) that no store of ``prog`` may write (its
    address set, else its address interval), outside init's instructions when it is lifted. A
    pair of bytes an instruction reads as one address is constant as a whole or not at all: one
    of its bytes a variable keeps the other a variable, the pair the word init's runs bound."""
    state, lone = lifted
    cand = np.intersect1d(np.intersect1d(state.free, every.same()), state.same())
    if lone is not None:
        cand = cand[~code_bytes(start, lone.starts)[cand]]
    var, held = masks.mask(prog.stored), np.zeros(0x10000, dtype=bool)
    held[cand] = ~var[cand]
    var[state.free] |= ~held[state.free]
    image = state.states[0].tobytes()
    pairs = [(lo, hi) for lo, hi in pointer_pairs(image, prog.starts).items() if lo >> 8 != 1]
    while split := [(lo, hi) for lo, hi in pairs if held[lo] != held[hi] and var[[lo, hi]].any()]:
        for lo, hi in split:
            held[[lo, hi]], var[[lo, hi]] = False, True
    return frozenset(np.flatnonzero(held).tolist())


def _domain(lift_, image, every, songs):
    """The argument values the other entries are lifted for: of those init returns with on the
    machine (``every``, every value of ``A``), each whose stores outside the stack page and the
    volatile ranges lie in what a store of its lift ``lift_`` may write and on which that lift,
    run from ``image``, does not deopt (an index past its tables: a value init's translation
    traps on), and the declared ``songs``. Values whose machine runs leave one memory and
    volatile writes share the verdict of the least of them."""
    may = masks.mask(lift_.stored, out=~every.kept)
    alike = {}
    for a, s, st, w in zip(every.songs, every.stored, every.states, every.writes):
        if may[s].all():
            alike.setdefault((st.tobytes(), tuple(w)), []).append(a)
    out = set(songs)
    for vals in alike.values():
        if lift_.call(bytearray(image), 0, a=vals[0])["deopt"] is None:
            out.update(vals)
    return sorted(out)


def _aliases(runs, states):
    """``[(lo, g, delta)]`` of :func:`postinit.groups` of ``runs`` (init's runs of one argument
    value) holding on each of ``states``: pair ``lo``'s word is pair ``g``'s plus ``delta``."""

    def word(lo):
        """Pair ``lo``'s word on each state: 16 bits, its differences wrapping as the 6502's."""
        return states[:, lo].astype(np.uint16) | states[:, (lo + 1) & 0xFFFF].astype(np.uint16) << 8

    return [
        (lo, g, d)
        for lo, (g, d) in sorted(postinit.groups(runs).items())
        if (word(lo) - word(g) == d).all()
    ]


def lift(  # pylint: disable=too-many-arguments,too-many-positional-arguments,too-many-locals
    image,
    entries,
    volatile,
    facts=None,
    loaded=None,
    sensitive=(),
    init=None,
    songs=None,
    machine=None,
    devices=c64devices.MODEL,
):
    """The static lift of ``entries`` as a :class:`Program` and its ``stats``; ``facts``
    give computed-jump targets only, ``loaded`` the ``(lo, hi)`` the program was loaded to,
    ``sensitive`` the volatile addresses whose throw-away reads are kept and ``devices`` what
    they answer (:mod:`deity_informant.devices`, None: any byte). ``entries[init]`` (``None``:
    none) is called once, first, with ``A`` each of the range ``songs`` (default ``[0]``): it
    runs on ``machine`` (:func:`deity_informant.postinit.run`), the other entries are lifted
    from what it leaves, every byte it stores a variable but those it leaves constant
    (:func:`_fixed`), and init from ``image`` where its lift reproduces every run (*Init*)."""
    image, entries = bytes(image), list(entries)
    played, state, alias, start, lone, fixed = entries, None, [], image, None, frozenset()
    bare = None
    entailed = frozenset()
    pc = None if init is None else entries[init][0]
    kw = {"entry_sp": emit.ENTRY_REGS[3], "loaded": loaded, "fold": True, "devices": devices}
    other = state

    def once(entry, **more):
        """The lift of init at ``entry`` from ``start``, called once."""
        more = {"sensitive": sensitive, **kw, **more}
        return wholeprog.lift(start, [entry], volatile, facts, once=True, **more)

    if init is not None:
        vals = sorted(set(range(1) if songs is None else songs))
        if not vals or vals[-1] - vals[0] + 1 != len(vals):
            raise ValueError("songs must be a range")
        state = postinit.run(image, pc, vals, volatile, machine)
        every = postinit.run(image, pc, range(256), volatile, machine, skip=True)
        bare = once(pc, sensitive=())
        polls = polling.loops(image, bare.starts, bare.code, volatile)
        runs = [postinit.forks(image, pc, a, volatile, polls, devices) for a in vals]
        over = _domain(bare, image, every, vals)
        rep = over[0]
        first = (
            runs[vals.index(rep)]
            if rep in vals
            else postinit.forks(image, pc, rep, volatile, polls, devices)
        )
        others = [r for a, rs in zip(vals, runs) if a != rep for r in rs or ()]
        flat = [] if None in runs or first is None else [*first, *others]
        decided = every.select(over)
        image = decided.states[0].tobytes()
        played = [e for i, e in enumerate(entries) if i != init]
        lone = _lift_init(once, start, pc, state, runs, bare)
        if lone is not None:
            entailed = _entailed(once, pc, lone, vals)
        # A translated init leaves the state of any value its probes read.
        other = decided if lone is None else decided.extended(flat)
        if flat:
            alias = _aliases(first, other.states)
    free = None if other is None else other.values()

    # The values init's translation may leave in its variables, where its lift knows them.
    known = None if lone is None else lone.values

    def entries_lift(held):
        """The lift of the entries but init, the bytes ``held`` constant (:func:`_fixed`)."""
        return wholeprog.lift(
            image,
            [q for q, _ in played],
            volatile,
            facts=facts,
            sensitive=sensitive,
            free=None if free is None else {a: v for a, v in free.items() if a not in held},
            alias=[g for g in alias if held.isdisjoint({g[0], g[0] + 1, g[1], g[1] + 1})],
            held=held,
            holds=[] if lone is None else list(lone.written),
            player=lone is not None,
            known=known,
            driven=None if bare is None else bare.exit,
            **kw,
        )

    prog = entries_lift(fixed)
    if state is not None:
        fixed = _fixed(start, prog, (other, lone), every)
        prog = entries_lift(fixed) if fixed else prog
    lifts = [prog] + ([lone] if lone is not None else [])
    funcs, origin = _ranked(lifts)
    starts = set(prog.starts)
    p = Program(funcs, played, image, starts, [tuple(v) for v in volatile])
    p.constant, p.jumps, p.written = frozenset(prog.code), dict(prog.jumps), list(prog.written)
    vars(p).update(_raw(prog))
    p.ptr_sets = {pc: set(w) for pc, w in prog.ptr_sets.items()}
    p.objects, p.parts, p.slack = list(prog.objects), dict(prog.parts), dict(prog.slack)
    p.machine = frozenset(prog.machine)
    p.pdefs = [_pdefs(x) for x in (prog, lone) if x is not None]
    p.stores = {"play": trapinfo.stores(prog)}
    if init is not None:
        p.stores["init"] = trapinfo.stores(lone if lone is not None else bare)
    if state is not None:
        p.init, p.fixed = (entries[init], state), np.array(sorted(fixed), dtype=np.int64)
        p.entailed, p.init_stored = entailed, decided.mask
    if lone is not None:
        p.entries = [*played, entries[init]]
        p.origin, p.start = origin, start
        p.lifted = (frozenset(lone.code), set(lone.starts), _facts(lone))
        p.jumps.update(dict(lone.jumps))
        for pc, w in lone.ptr_sets.items():
            p.ptr_sets.setdefault(pc, set()).update(w)
        _renumber(funcs, origin, len(p.objects))
        p.parts.update({j + len(p.objects): r for j, r in lone.parts.items()})
        p.objects += list(lone.objects)
        p.written += list(lone.written)
    return p, prog.stats


def _pdefs(prog):
    """``(definitions, {pc: [id]} reaching, {pc: [id]} past)`` of a lift (``Program.pdefs``)."""
    return list(prog.pdefs), dict(prog.pdefs_at), dict(prog.pdefs_past)


def _song_guard(p):
    """``{key: guard}`` of the init entry's trap on an argument outside its songs."""
    if p.init is None:
        return {}
    (pc, _), state = p.init
    guard = dict.fromkeys(("pair", "obj", "def", "addr", "seen", "held", "implied_by"))
    guard.update(kind="entry", at=pc, fn=pc, reg="a", tables=[], copies=[], writers=None)
    guard["proven"] = False
    guard["ok"] = [(state.songs[0], state.songs[-1])]
    return {("deopt", pc): guard}


def render(  # pylint: disable=too-many-locals,too-many-arguments,too-many-positional-arguments
    p, header="cgen.h", prefix=None, lines=False, split=False
):
    """``(c, h_text, info)`` of :class:`Program` ``p``: MLIR, structured and printed;
    ``lines``: with ``#line`` markers (*Translation cost*); ``split``: ``c`` the translation
    units ``{file: text}`` (:mod:`.units`), else one text."""
    times, t0 = {}, time.perf_counter()
    symbols, funcs = [], p.funcs
    if p.fixed is not None and p.fixed.size:
        funcs = copy.deepcopy(funcs)
        own = [fn for j, fn in enumerate(funcs) if not p.lifted or not p.origin[j]]
        symbols = _guard_symbols(own, _reads_fixed(p.image, p.starts, p.fixed))
    text, traps, whys, regfile = emit.emit(funcs, p.entries, bool(p.sensitive), len(symbols))
    run = mlir.runner(build=True) if prefix is None else prefix
    if run is None:
        raise RuntimeError("needs mlir-opt on PATH or Docker")
    top = mlir.parse(mlir.optimize(text, run))
    times["mlir"] = time.perf_counter() - t0
    t1 = time.perf_counter()
    insn = code_bytes(p.image, p.starts)
    code, ran = insn.copy(), insn.copy()
    cells = np.zeros_like(code)
    tables = None
    if p.constant is not None:
        constant = masks.mask(at=p.constant)
        if p.lifted is not None:
            # Init's code reads and writes the bytes it leaves constant: they are data.
            constant[p.fixed] = False
        cells = code & ~constant
        code &= constant
        tables = constant & ~ran
    loaded = p.loaded
    if loaded is None:
        span = np.flatnonzero(code)
        loaded = (int(span[0]), int(span[-1]) + 1) if len(span) else (0, 0)
    pairs = pointer_pairs(p.image, p.starts)
    data, plan, fixed = p.image, None, code

    def held(b):
        """``b`` holding the values a replayed init leaves in the bytes it leaves constant."""
        b = np.frombuffer(bytes(b), np.uint8).copy()
        if p.fixed is not None and p.lifted is None:
            b[p.fixed] = np.frombuffer(p.image, np.uint8)[p.fixed]
        return b

    if p.lifted is not None:
        # Init's lift: its instructions decode from the image it runs on; the bytes it patches
        # are cells unless the entries' lift reads them as constants, which init leaves.
        iconst, istarts, _ = p.lifted
        icode = code_bytes(p.start, istarts)
        imask = masks.mask(at=iconst)
        ran |= icode
        cells |= icode & ~imask & ~code
        fixed = code | (icode & imask)
        pairs = {**pointer_pairs(p.start, istarts), **pairs}
        data = p.start
    elif p.init is not None:
        # The .h holds the image or the first song's state, whichever needs the smaller copies.
        state = p.init[1]
        source = ~(code if tables is None else code | tables) & ~masks.mask(p.volatile)
        source[:0x200] = False
        source[: loaded[0]] = source[loaded[1] :] = False
        data, plan = min(
            (
                (b.tobytes(), state.copies(source, b))
                for b in map(held, (state.image, state.states[0]))
            ),
            key=lambda x: postinit.cost(x[1]),
        )
    mem = Memory(held(data), code, p.volatile, loaded, p.sensitive, pairs, tables, p.written)
    mem.init = bytes(data)
    mod = printer.Module(top, mem)
    mod.insn, mod.fixed = ran, fixed
    if p.fixed is not None:
        folded = masks.mask(at=p.fixed)
        mod.folded = (folded, p.image, folded & insn)
    mod.symbols = symbols
    if p.init is not None:
        mod.init_stored = p.init_stored
    # Promotion candidates: the cells and the other variable bytes inside the code span.
    lo, hi = mem.code_span
    mod.cells = cells.copy()
    mod.opcodes = {
        int(a): sorted(v) for a, v in sorted((p.sets or {}).items()) if a in p.starts and cells[a]
    }
    mod.cells[lo:hi] |= ~mem.constant[lo:hi] & ~mem.vol[lo:hi]
    mod.lifted = {f"f{j}": (fn["pc"], list(fn.get("copies", ()))) for j, fn in enumerate(p.funcs)}
    mod.lifted.update({name: (pc, []) for pc, name in p.entries})
    mod.copy_pcs = {
        f"f{j}": {q: pcs for pcs in fn.get("copy_pcs", ()) for q in pcs if q}
        for j, fn in enumerate(p.funcs)
    }
    vars(mod).update(vars(_facts(p)))
    mod.ptr_objects, mod.ptr_parts = list(p.objects), dict(p.parts or {})
    mod.ptr_slack = dict(p.slack or {})
    mod.regfile = regfile = [r for r, _ in regfile]
    mod.kpos = regfile.index(emit.KREG) if emit.KREG in regfile else None
    song = None
    if p.lifted is not None:
        (pc, name), _ = p.init
        song = {"fn": pc, "lo": p.init[1].songs[0], "hi": p.init[1].songs[-1]}
        mod.scope = {f"f{j}": p.lifted[2] for j, k in enumerate(p.origin) if k}
        mod.scope[name] = p.lifted[2]
        names = {f"f{j}" for j, k in enumerate(p.origin) if not k}
        mod.after = frozenset(names | {name for _, name in p.entries[:-1]})
        k = song["k"] = traps.setdefault(("deopt", pc), len(traps))
        mod.traps.add(k)
        mod.guards[name] = [_song_check(k)]
    elif p.init is not None:
        mod.traps.add(traps.setdefault(("deopt", p.init[0][0]), len(traps)))
        mod.held_runs = sorted(
            {(s, s + n - 1) for ops in plan for _, s, n in ops if isinstance(s, int)}
        )
    mod.combine(run)
    mod.prune()
    mod.partition()
    times["layout"] = time.perf_counter() - t1
    t2 = time.perf_counter()
    mod.structure()
    times["structure"] = time.perf_counter() - t2
    t1 = time.perf_counter()
    fns = mod.functions()
    used, helpers = set(mod.used), mod.helpers
    helpers |= {"oob_rd"} if mod.checked else set()
    if helpers & {"oob_rd", "rd", "codechk"} and mem.held:
        used.add("CODE")
    regions = sorted(
        mod.regions_used
        | (set(range(len(mem.regions))) if helpers & {"oob_rd", "rd", "wr", "oob_wr"} else set())
    )
    runtime, access = (mod.marks.add(("tag", f)) for f in ("runtime", "access"))
    c = [units.Part((runtime, PROLOGUE.format(header=header)), units.EVERY)]
    if "CGEN_IX" in used:
        c.append(units.Part((runtime, IX), units.EVERY))
    c.append(units.Part((runtime, "CGEN_STORAGE"), units.STATE))
    for x in mod.state_c():
        c.append(units.Part(_tagged(x, runtime), units.STATE if x.name else units.HEADER))
    ram = mod.stored()
    ram = True if ram is None else ram
    dispatch, unheld = _dispatch(mem, helpers, used, mod.var_at, mod.init_stored, ram)
    if dispatch:
        given = frozenset(HELPERS) & (
            helpers | {"oob_rd", "oob_wr"} | ({"cgen_var_at"} if mod.var_at else set())
        )

        def runtime_c(ext):
            return "\n".join(
                _dispatch(mem, helpers, set(used), mod.var_at, mod.init_stored, ram, ext)[0]
            )

        protos = tuple(HELPERS[n]() for n in sorted(given))
        c.append(units.Part((access, runtime_c), units.CODE, given, frozenset(), protos))
    c += [units.part(_tagged(x, access)) for x in mod.block_c()]
    c += [
        units.part(_tagged(_checked_reader(j, j in mod.var_spans), access))
        for j in sorted(mod.checked)
    ]
    sets = [] if p.init is None else _songs_h(p.init[1])
    if p.init is not None and p.lifted is None:
        (pc, name), state = p.init
        copies, named = _copies_h(mem, plan, mod.var_ref)
        regions = sorted(set(regions) | named)
        sets += [*_io_h(state), *copies]
        init_c = "\n".join(_init_c(name, traps[("deopt", pc)]))
        c.append(
            units.Part((mod.marks.add(("tag", "runtime")), init_c), units.CODE, frozenset({name}))
        )
    c += [
        units.part(
            x,
            (
                units.STATE
                if isinstance(x, c_ast.Decl) and not isinstance(x.type, c_ast.FuncDecl)
                else units.CODE
            ),
        )
        for x in fns
    ]
    c_text, order = cn.render(units.single(c), mod.marks.recs, pieces=mod.pieces)
    store = _storage(mem, used, helpers, regions) + mod.state_h() + sets + unheld
    objs = [_obj_h(j, mod.ptr_objects[j], mod.ptr_parts.get(j)) for j in sorted(mod.objs_used)]
    h_store = objs + store
    times["print"] = time.perf_counter() - t1
    trap_info = trapinfo.trap_info(
        funcs,
        traps,
        whys,
        mod.traps,
        {
            "origin": p.origin,
            "stores": p.stores or {},
            "jumps": p.jumps or {},
            "song": song,
            "entailed": p.entailed,
            "extra": _song_guard(p),
        },
    )
    emission, line = cost.emission(p, mod, order, trap_info)
    line = line if lines else None
    entries = [name for _, name in p.entries]
    stem = header.rsplit(".", 1)[0]

    def unit_of(e):
        if e is units.STATE:
            return f"{stem}_state.c"
        return f"{stem}_{'shared' if e is None else e.removeprefix('cgen_')}.c"

    files = None
    if split:
        parts, head = units.split(c, entries, unit_of)
        files = {u: cn.render(xs, mod.marks.recs, line)[0] for u, xs in sorted(parts.items())}
        h_store += ["CGEN_EXTERN", *(cn.Printer(mod.marks.recs).visit(d) + ";" for d in head)]
    elif lines:
        c_text = cn.render(units.single(c), mod.marks.recs, line)[0]
    h_text = format_c(_header(mem, used, [key[1] for key in traps], mod.konsts, h_store))
    info = {
        "times": times,
        "regions": mem.regions,
        "code_span": mem.code_span,
        "code_held": mem.held,
        "blocks": {
            b: {
                "org": mem.blocks[b][0],
                "len": mem.blocks[b][1] - mem.blocks[b][0],
                "padding": [
                    (mem.blocks[b][0] + int(lo), mem.blocks[b][0] + int(lo) + int(n) - 1)
                    for lo, n in masks.runs(mem.padding(b, "CODE" in used))
                ],
            }
            for b in sorted(mem.blocks_used)
        },
        "traps": {k: key for key, k in traps.items() if k in mod.traps},
        "trap_info": trap_info,
        "runtime_traps": {OOB_TRAP: "unheld"} if helpers & OOB_HELPERS else {},
        "emission": emission,
        "unknown_access": mem.unknown,
        "jumps": p.jumps or {},
        "ptr_sets": {pc: sorted(w) for pc, w in sorted((p.ptr_sets or {}).items())},
        "structure": dict(mod.made),
        "provenance": {
            **mod.prov,
            "functions": _files(mod.prov.get("functions", {}), c, entries, unit_of, split),
            "cells": [int(a) for a in np.flatnonzero(cells)],
        },
        "fixed": [] if p.fixed is None else p.fixed.tolist(),
        "state": {
            "cells": mod.state.counts(cells),
            "span": mod.state.counts(mod.cells & ~cells),
            "vars": {mod.var_ref(a): a for a in sorted(mod.state.at)},
            "opcodes": {
                a: {
                    "values": v,
                    "name": mod.var_ref(a),
                    "enum": list((mod.enum_of(a) or {}).values()),
                }
                for a, v in mod.opcodes.items()
            },
        },
    }
    return (files if split else c_text), h_text, info


def _files(funcs, parts, entries, unit_of, split):
    """Provenance ``funcs`` (``{name: record}``), each with the ``file`` defining it: its
    unit when ``split``, else the one ``.c`` (None)."""
    reach = units.owners(parts, entries)
    out = {}
    for name, rec in funcs.items():
        who = reach.get(name, frozenset())
        unit = unit_of(next(iter(who))) if len(who) == 1 else unit_of(None)
        out[name] = {**rec, "file": unit if split else None}
    return out


#: The runtime's helpers lifted code calls: their prototypes.
HELPERS = {
    "rd": lambda: cn.function("rd", "uint8_t", [("a", "uint16_t")]),
    "wr": lambda: cn.function("wr", "void", [("a", "uint16_t"), ("v", "uint8_t")]),
    "oob_rd": lambda: cn.function("oob_rd", "uint8_t", [("a", "uint16_t")]),
    "oob_wr": lambda: cn.function("oob_wr", "void", [("a", "uint16_t"), ("v", "uint8_t")]),
    "codechk": lambda: cn.function("codechk", "uint8_t", [("a", "uint16_t"), ("v", "uint8_t")]),
    "cgen_var_at": lambda: cn.function("cgen_var_at", "uint8_t *", [("a", "uint16_t")]),
}


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
    init=None,
    songs=None,
    machine=None,
    devices=c64devices.MODEL,
    lines=False,
    split=False,
):
    """``(c_text, h_text, info)`` of the program in ``image`` (the whole address space) called
    at ``entries`` (``(pc, name)``, ``A`` the argument) with ``volatile`` ``[(lo, hi)]`` I/O
    ranges whose reads at ``sensitive_reads`` (and the reads ``devices`` says change the
    device) are kept and ``devices`` what they answer; ``facts`` supply computed-jump targets only;
    ``entries[init]`` is called once, before any other entry (``None``: entries are called in
    any order), with ``A`` each of the range ``songs`` (default ``[0]``), run on ``machine``
    (:func:`deity_informant.postinit.run`): the other entries are lifted from what it leaves;
    ``lines`` marks the ``.c`` with ``#line`` directives (*Translation cost*); ``split`` gives
    it as translation units ``{file: text}`` (:mod:`.units`)."""
    t0 = time.perf_counter()
    jf = jump_facts(facts, image) if facts is not None else None
    if devices is not None:
        sensitive_reads = sorted(set(sensitive_reads) | set(devices.sensitive(volatile)))
    p, stats = lift(
        image, entries, volatile, jf, loaded, sensitive_reads, init, songs, machine, devices
    )
    p.sensitive, p.loaded = tuple(sensitive_reads), loaded
    lift_time = time.perf_counter() - t0
    c, h, info = render(p, header, prefix, lines, split)
    info["times"] = {"lift": lift_time, **info["times"]}
    info["facts_edges"] = list(jf.edges) if jf is not None else []
    info["stats"] = stats
    info["starts"] = sorted(p.starts | (p.lifted[1] if p.lifted else set()))
    info["init"] = None if p.init is None else "translated" if p.lifted else "replayed"
    return c, h, info
