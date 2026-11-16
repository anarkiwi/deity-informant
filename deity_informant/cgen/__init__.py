"""6502/6510 machine code as C: a static whole-program lift, MLIR, then DREAM (``docs/cgen.md``).

:func:`translate` gives a ``.c`` of the code alone and a ``.h`` of the data regions, so programs
that differ only in their data or their origin give the same ``.c``.
"""

from __future__ import annotations

import itertools
import shutil
import subprocess
import time
from dataclasses import dataclass
from types import SimpleNamespace

import numpy as np

from .. import polling, postinit, wholeprog
from ..trace import Facts
from . import emit, mlir, printer
from .regions import Memory, code_bytes, pointer_pairs

__all__ = ["Program", "jump_facts", "lift", "render", "translate"]

PROLOGUE = """#include <stdint.h>
#include "{header}"

uint8_t io_read(uint16_t a);
void io_write(uint16_t a, uint8_t v);
__attribute__((noreturn, leaf)) void trap(uint16_t k);
"""
OOB = {"oob_rd": "uint8_t oob_rd(uint16_t a);", "oob_wr": "void oob_wr(uint16_t a, uint8_t v);"}
OOB_WR = ["static void oob_wr(uint16_t a, uint8_t v) {", "(void)a;", "(void)v;", "}"]
#: Trap code of a read outside every array the program holds (not a ``CGEN_TRAPS`` index).
OOB_TRAP = 0xFFFF
OOB_RD_TRAP = f"static uint8_t oob_rd(uint16_t a) {{ (void)a; trap(0x{OOB_TRAP:04X}); }}"

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


def _hex(data):
    return ", ".join(f"0x{b:02X}" for b in data)


def _slice(mem, used, k):
    """The held span region ``k`` is a slice of, or None."""
    return mem.span_of(*mem.regions[k]) if "CODE" in used else None


def _sliced(mem, used, j):
    """Whether some region is a slice of held span ``j``."""
    return any(_slice(mem, used, k) == j for k in range(len(mem.regions)))


def _header(mem, used, traps, konsts=None, store=()):
    """The ``.h``: origins, lengths and initial bytes of the regions (those inside a held span
    are its slices) and of the held spans when ``used``, the constant bytes ``konsts``
    (``{name: byte}``) the ``.c`` reads by name and the arrays' definitions ``store``."""
    out = ["#ifndef CGEN_DATA_H", "#define CGEN_DATA_H"]
    for k, (org, n) in enumerate(mem.regions):
        out += [f"#define R{k}_ORG 0x{org:04X}", f"#define R{k}_LEN {n}"]
        if _slice(mem, used, k) is None:
            out.append(f"#define R{k}_INIT {_hex(mem.init[org : org + n])}")
    if "CODE" in used or "CODE_ORG" in used:
        out.append(f"#define CODE_ORG 0x{mem.code_span[0]:04X}")
    for j, (lo, hi) in enumerate(mem.held if "CODE" in used else ()):
        out += [f"#define C{j}_ORG 0x{lo:04X}", f"#define C{j}_LEN {hi - lo}"]
        out.append(f"#define C{j}_INIT {_hex(mem.init[lo:hi])}")
        if _sliced(mem, used, j):
            # Past the span's last constant byte every byte is data: the bits stop there.
            top = lo + int(np.flatnonzero(mem.constant[lo:hi]).max(initial=-1)) + 1
            bits = np.packbits(mem.owner[lo : max(top, lo + 1)] >= 0, bitorder="little")
            out += [f"#define C{j}_BITS {top - lo}", f"#define C{j}_DATA_INIT {_hex(bits)}"]
    if "IMG_ORG" in used:
        out.append(f"#define IMG_ORG 0x{mem.loaded[0]:04X}")
    out += [f"#define {k} 0x{v:02X}" for k, v in sorted((konsts or {}).items())]
    out.append(f"#define CGEN_TRAPS {', '.join(f'0x{pc:04X}' for pc in traps)}")
    return "\n".join([*out, *store, "#endif"]) + "\n"


def _inside(r):
    return f"(uint16_t)(a - {r}_ORG) < {r}_LEN"


def _data(j):
    off = f"(uint16_t)(a - C{j}_ORG)"
    return f"({off} >= C{j}_BITS || C{j}_DATA[{off} >> 3] >> ({off} & 7) & 1)"


def _var(cases):
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
        "static inline uint8_t *cgen_var_at(uint16_t a) {",
        f"return (uint16_t)(a - (CODE_ORG + 0x{lo:X})) <= 0x{hi - lo:X} ? cgen_var(a) : 0;",
        "}",
    ]


#: A reader's test for a promoted cell at ``a`` (``cgen_var``).
VAR_RD = ["uint8_t *c = cgen_var_at(a);", "if (c) return *c;"]


def _rd(regions, vol, spans, var=False):
    """``cgen_rd``: the byte the 6502 reads at ``a``, by the promoted cells (with ``var``), the
    volatile condition ``vol``, then the origins and lengths of ``regions`` and the held
    ``spans`` (0 where the program holds none)."""
    arrays = [*regions, *(f"C{j}" for j in spans)]
    out = ["static __attribute__((noinline)) uint8_t cgen_rd(uint16_t a) {"]
    out += VAR_RD if var else []
    out += [f"if ({vol}) return io_read(a);"] if vol else []
    if not arrays:
        return out + ([] if vol else ["(void)a;"]) + ["return 0;", "}"]
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
            "return 0;",
            "}",
        ]
    )


def _wr(vol, var=False):
    """``wr``: a write dispatcher over the promoted cells (with ``var``), the regions
    (``CGEN_REGIONS``, slices of held spans included: those are the spans' data bytes) and the
    volatile condition ``vol``."""
    out = ["static uint8_t *const WR_AT[] = {CGEN_REGIONS};"] + [
        f"static const uint16_t WR_{k}[] = {{CGEN_REGIONS_{k}}};" for k in ("ORG", "LEN")
    ]
    out += ["static __attribute__((noinline)) void wr(uint16_t a, uint8_t v) {"]
    out += ["uint8_t *c = cgen_var_at(a);", "if (c) {", "*c = v;", "return;", "}"] if var else []
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
    return out + ["oob_wr(a, v);", "}"]


def _storage(mem, used, helpers, regions):
    """The ``.h``'s arrays, laid out by where the song puts its data: ``CGEN_STORAGE`` (the
    ``.c`` defines them by it) holds the held spans, the ``regions`` not inside one and the
    spans' data bits; a region inside a span is its slice; ``rd`` and ``wr`` find them by
    the ``CGEN_ARRAYS`` and ``CGEN_REGIONS`` lists."""
    spans = range(len(mem.held)) if "CODE" in used else range(0)
    defs = [f"uint8_t C{j}[C{j}_LEN] = {{C{j}_INIT}};" for j in spans]
    out = []
    for k in regions:
        j = _slice(mem, used, k)
        if j is not None:
            out.append(f"#define R{k} (C{j} + (R{k}_ORG - C{j}_ORG))")
        else:
            defs.append(f"uint8_t R{k}[R{k}_LEN] = {{R{k}_INIT}};")
    for j in spans if "codechk" in helpers else ():
        if _sliced(mem, used, j):
            defs.append(f"static const uint8_t C{j}_DATA[] = {{C{j}_DATA_INIT}};")
    out.append(f"#define CGEN_STORAGE {' '.join(defs)}")
    whole = [f"R{k}" for k in regions if _slice(mem, used, k) is None]
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


def _dispatch(mem, helpers, used, cases=()):
    """``oob_rd`` (unless the host's) and ``wr`` over the regions outside the held spans, the
    volatile ranges and the held spans (whose data bytes ``Cj_DATA`` marks), ``wr`` then
    ``oob_wr``; ``rd``, ``oob_rd`` for a load the lift places nowhere; and ``codechk``: whether
    a held constant byte would change. ``cgen_rd`` finds the promoted cells ``cases``
    (``cgen_var``) first, as ``wr`` does where an access needs it."""
    out = []
    vol = " || ".join(
        f"(a >= 0x{lo:04X} && a < 0x{hi:04X})" if hi < 0x10000 else f"a >= 0x{lo:04X}"
        for lo, hi in mem.volatile
    )
    spans = range(len(mem.held)) if "CODE" in used else range(0)
    sliced = [j for j in spans if _sliced(mem, used, j)]
    regions = [f"R{k}" for k in range(len(mem.regions)) if _slice(mem, used, k) is None]
    var = _var(cases) if cases and helpers & {"cgen_var", "rd", "oob_rd"} else []
    exact = _rd(regions, vol, spans, bool(var))
    if helpers & {"cgen_var", "rd"}:
        out, var = out + var, []
        used.add("CODE_ORG")
    if "rd" in helpers:
        out += [*exact, "static inline uint8_t rd(uint16_t a) { return cgen_rd(a); }"]
    oob = sorted(helpers & set(OOB))
    if oob:
        out += ["#ifdef CGEN_OOB", *(OOB[h] for h in oob), "#else"]
        out += OOB_WR if "oob_wr" in oob else []
        if "oob_rd" in oob:
            used |= {"CODE_ORG"} if var else set()
            out += ["#ifdef CGEN_OOB_EXACT", *([] if "rd" in helpers else var + exact)]
            out += ["static uint8_t oob_rd(uint16_t a) { return cgen_rd(a); }", "#else"]
            out += [OOB_RD_TRAP, "#endif"]
        out.append("#endif")
    if "wr" in helpers:
        out += _wr(vol, "cgen_var" in helpers)
    if "codechk" in helpers:
        out += ["static __attribute__((noinline)) uint8_t codechk(uint16_t a, uint8_t v) {"]
        for j in spans:
            nd = f" && !{_data(j)}" if j in sliced else ""
            out += [f"if ({_inside(f'C{j}')}) return C{j}[a - C{j}_ORG] != v{nd};"]
        out += ["(void)a;", "(void)v;", "return 0;", "}"]
    return out


#: The init entry's argument ``a`` as an index into the ``.h``'s range of its values.
SONG = "(uint8_t)(a - CGEN_SONG0)"


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
        f"if ({SONG} >= CGEN_SONGS) {{",
        f"trap({k});",
        "}",
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
            f"#define CGEN_IO_AT {', '.join(f'0x{x:04X}' for x, _ in flat)}",
            f"#define CGEN_IO_VAL {_hex(v for _, v in flat)}",
            f"#define CGEN_IO_FROM {', '.join(str(e) for e in ends)}",
        ]
        if len(rows) > 1:
            index = [rows.index(tuple(w)) for w in state.writes]
            out.append(f"#define CGEN_IO_ROW {', '.join(map(str, index))}")
    return out


def _volatile_mask(volatile):
    """The bytes of the ``volatile`` ranges, as a boolean array."""
    vol = np.zeros(0x10000, dtype=bool)
    for lo, hi in volatile:
        vol[lo:hi] = True
    return vol


def _array_of(mem, a):
    """``(name, origin)`` of the region holding byte ``a``, None when none does."""
    k = int(mem.owner[a])
    return (f"R{k}", mem.regions[k][0]) if k >= 0 else None


def _copies_h(mem, plan, var=lambda a: None):
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
        out.append(f"#define CGEN_LIT {_hex(lit)}")
    if dst:
        out += [
            f"#define CGEN_CP_DST {', '.join(dst)}",
            f"#define CGEN_CP_SRC {', '.join(src)}",
            f"#define CGEN_CP_LEN {', '.join(map(str, lens))}",
            f"#define CGEN_CP_FROM {', '.join(map(str, ends))}",
        ]
    return out, used


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
    written: list = ()
    bounds: dict | None = None
    addr_sets: dict | None = None
    reach: dict | None = None
    proven: dict | None = None
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


def _ranked(progs):
    """The functions of the lifts ``progs`` sorted by entry PC, their ``CALL`` s renumbered,
    and the index of the lift each comes from."""
    items = [(fn, k, i) for k, prog in enumerate(progs) for i, fn in enumerate(prog.functions)]
    order = sorted(range(len(items)), key=lambda j: items[j][0]["pc"])
    remap = {items[j][1:]: n for n, j in enumerate(order)}
    funcs, origin = [], []
    for j in order:
        fn, k, _ = items[j]
        for op in (op for b in fn["blocks"] for op in b["ops"] if op[0] == "CALL"):
            op[3] = remap[(k, op[3])]
        funcs.append(fn)
        origin.append(k)
    return funcs, origin


def _facts(prog):
    """The facts of lift ``prog`` the layout reads (:meth:`printer.Module.fx`)."""
    sets = {a: (min(v), max(v)) for a, v in dict(prog.sets).items() if v}
    return SimpleNamespace(
        sets={**dict(prog.bounds), **sets},
        regs={pc: tuple(bytes(x) for x in r) for pc, r in dict(prog.regs).items()},
        reach=dict(prog.reach),
        proven=dict(prog.proven),
        bounded=dict(prog.bounded),
        addr_sets=dict(prog.addr_sets),
    )


def _lift_init(
    start, entry, volatile, facts, sensitive, kw, state, runs, bare
):  # pylint: disable=too-many-arguments,too-many-positional-arguments
    """Init's lift (at ``entry``, run once from ``start``, any ``A``: the host's check of the
    argument is the ``.h``'s range) when it is exact for every declared argument value: its
    lift without throw-away reads ``bare`` (every volatile read it makes one it uses)
    reproduces the machine's run (:func:`deity_informant.postinit.reproduces`, its polling
    loops answered as in the reference's ``runs``) and the accesses of ``runs`` lie inside
    what it bounds them to (:func:`deity_informant.postinit.bounded`); else None."""
    if None in runs or not postinit.reproduces(bare, start, state, runs):
        return None
    lone = wholeprog.lift(start, [entry], volatile, facts, sensitive=sensitive, **kw, once=True)
    return lone if postinit.bounded(lone, runs) else None


def _fixed(start, pc, prog, lifted, volatile, machine):
    """The bytes init (at ``pc`` on ``start``) leaves constant for the entries lifted as
    ``prog`` from its declared runs (*Init*): of the bytes it stores on those runs
    (``lifted``: their :class:`postinit.Init` and init's lift), those holding one value after
    it for every value of ``A`` it returns with on ``machine`` that no store of ``prog`` may
    write (its address set, else its address interval), outside init's instructions when it
    is lifted. A pair of bytes an instruction reads as one address is constant as a whole or
    not at all: one of its bytes a variable keeps the other a variable, the pair the word
    init's runs bound."""
    state, lone = lifted
    every = postinit.run(start, pc, range(256), volatile, machine, skip=True)
    cand = np.intersect1d(state.free, every.same())
    if lone is not None:
        cand = cand[~code_bytes(start, lone.starts)[cand]]
    var, held = np.zeros(0x10000, dtype=bool), np.zeros(0x10000, dtype=bool)
    for lo, hi in prog.stored:
        var[lo:hi] = True
    held[cand] = ~var[cand]
    var[state.free] |= ~held[state.free]
    image = state.states[0].tobytes()
    pairs = [(lo, hi) for lo, hi in pointer_pairs(image, prog.starts).items() if lo >> 8 != 1]
    while split := [(lo, hi) for lo, hi in pairs if held[lo] != held[hi] and var[[lo, hi]].any()]:
        for lo, hi in split:
            held[[lo, hi]], var[[lo, hi]] = False, True
    return frozenset(np.flatnonzero(held).tolist())


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
):
    """The static lift of ``entries`` as a :class:`Program` and its ``stats``; ``facts``
    give computed-jump targets only, ``loaded`` the ``(lo, hi)`` the program was loaded to and
    ``sensitive`` the volatile addresses whose throw-away reads are kept. ``entries[init]``
    (``None``: none) is called once, first, with ``A`` each of the range ``songs`` (default
    ``[0]``): it runs on ``machine`` (:func:`deity_informant.postinit.run`), the other
    entries are lifted from the memory it leaves, every byte it stores a variable but those
    it leaves constant (:func:`_fixed`), and init is lifted from ``image`` where its lift
    reproduces every run (*Init*)."""
    image, entries = bytes(image), list(entries)
    played, state, alias, start, lone, fixed = entries, None, [], image, None, frozenset()
    pc = None if init is None else entries[init][0]
    kw = {"entry_sp": emit.ENTRY_REGS[3], "loaded": loaded, "fold": True}
    if init is not None:
        vals = sorted(set(range(1) if songs is None else songs))
        if not vals or vals[-1] - vals[0] + 1 != len(vals):
            raise ValueError("songs must be a range")
        state = postinit.run(image, pc, vals, volatile, machine)
        bare = wholeprog.lift(image, [pc], volatile, facts, sensitive=(), **kw, once=True)
        polls = polling.loops(image, bare.starts, bare.code, volatile)
        runs = [postinit.traced(image, pc, a, volatile, polls) for a in vals]
        if None not in runs:
            pairs = set().union(*(postinit.pointers(r) for r in runs))
            alias = [(lo, g, d) for lo, (g, d) in sorted(postinit.groups(runs, pairs).items())]
        image = state.states[0].tobytes()
        played = [e for i, e in enumerate(entries) if i != init]
        lone = _lift_init(start, pc, volatile, facts, sensitive, kw, state, runs, bare)
    free = None if state is None else state.values()

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
            **kw,
        )

    prog = entries_lift(fixed)
    if state is not None:
        fixed = _fixed(start, pc, prog, (state, lone), volatile, machine)
        prog = entries_lift(fixed) if fixed else prog
    lifts = [prog] + ([lone] if lone is not None else [])
    funcs, origin = _ranked(lifts)
    starts = set(prog.starts)
    p = Program(funcs, played, image, starts, [tuple(v) for v in volatile])
    p.constant, p.jumps, p.sets = frozenset(prog.code), dict(prog.jumps), dict(prog.sets)
    p.written = list(prog.written)
    p.bounds, p.addr_sets, p.reach = dict(prog.bounds), dict(prog.addr_sets), dict(prog.reach)
    p.proven, p.regs, p.bounded = dict(prog.proven), dict(prog.regs), dict(prog.bounded)
    if state is not None:
        p.init, p.fixed = (entries[init], state), np.array(sorted(fixed), dtype=np.int64)
    if len(lifts) > 1:
        lone = lifts[1]
        p.entries = [*played, entries[init]]
        p.origin, p.start = origin, start
        p.lifted = (frozenset(lone.code), set(lone.starts), _facts(lone))
        p.jumps.update(dict(lone.jumps))
        p.written += list(lone.written)
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
    ran = code.copy()
    cells = np.zeros_like(code)
    tables = None
    if p.constant is not None:
        constant = np.zeros(0x10000, dtype=bool)
        constant[list(p.constant)] = True
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
        imask = np.zeros(0x10000, dtype=bool)
        imask[list(iconst)] = True
        ran |= icode
        cells |= icode & ~imask & ~code
        fixed = code | (icode & imask)
        pairs = {**pointer_pairs(p.start, istarts), **pairs}
        data = p.start
    elif p.init is not None:
        # The .h holds the image or the first song's state, whichever needs the smaller copies.
        state = p.init[1]
        source = ~(code if tables is None else code | tables) & ~_volatile_mask(p.volatile)
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
    # Promotion candidates: the cells and the other variable bytes inside the code span.
    lo, hi = mem.code_span
    mod.cells = cells.copy()
    mod.cells[lo:hi] |= ~mem.constant[lo:hi] & ~mem.vol[lo:hi]
    mod.sets = {**(p.bounds or {}), **{a: (min(v), max(v)) for a, v in (p.sets or {}).items() if v}}
    mod.lifted = {f"f{j}": (fn["pc"], list(fn.get("copies", ()))) for j, fn in enumerate(p.funcs)}
    mod.lifted.update({name: (pc, []) for pc, name in p.entries})
    mod.copy_pcs = {
        f"f{j}": {q: pcs for pcs in fn.get("copy_pcs", ()) for q in pcs if q}
        for j, fn in enumerate(p.funcs)
    }
    mod.addr_sets = p.addr_sets or {}
    mod.reach, mod.proven, mod.bounded = p.reach or {}, p.proven or {}, p.bounded or {}
    mod.regfile = regfile = [r for r, _ in emit.Context(p.funcs, False).regfile]
    mod.regs = {pc: tuple(bytes(x) for x in r) for pc, r in (p.regs or {}).items()}
    mod.kpos = regfile.index(emit.KREG) if emit.KREG in regfile else None
    if p.lifted is not None:
        mod.scope = {f"f{j}": p.lifted[2] for j, k in enumerate(p.origin) if k}
        if p.fixed.size:
            after = np.zeros(0x10000, dtype=bool)
            after[p.fixed] = True
            names = {f"f{j}" for j, k in enumerate(p.origin) if not k}
            mod.after = (after, p.image, names | {name for _, name in p.entries[:-1]})
        (pc, name), _ = p.init
        k = traps.setdefault(("deopt", pc), len(traps))
        mod.traps.add(k)
        mod.guards[name] = [f"if ({SONG} >= CGEN_SONGS) {{", f"trap({k});", "}"]
    elif p.init is not None:
        mod.traps.add(traps.setdefault(("deopt", p.init[0][0]), len(traps)))
        mod.held_runs = sorted(
            {(s, s + n - 1) for ops in plan for _, s, n in ops if isinstance(s, int)}
        )
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
    helpers |= {"oob_wr"} if "wr" in helpers else set()
    if helpers & {"oob_rd", "rd", "codechk"} and mem.held:
        used.add("CODE")
    regions = sorted(
        mod.regions_used
        | (set(range(len(mem.regions))) if helpers & {"oob_rd", "rd", "wr"} else set())
    )
    c = [PROLOGUE.format(header=header)]
    if "CGEN_IX" in used:
        c.append(IX)
    c.append("CGEN_STORAGE")
    c += mod.state_c()
    c += _dispatch(mem, helpers, used, mod.var_at)
    for j in sorted(mod.checked):
        var = ["uint16_t a = p + y;", *VAR_RD] if j in mod.var_spans else []
        c += [
            f"static __attribute__((noinline)) uint8_t rdC{j}(uint16_t p, uint8_t y) {{",
            *var,
            f"uint16_t o = p - C{j}_ORG + y;",
            f"return o < C{j}_LEN ? C{j}[o] : oob_rd(p + y);",
            "}",
        ]
    sets = []
    if p.lifted is not None:
        state = p.init[1]
        sets = [
            f"#define CGEN_SONG0 0x{state.songs[0]:02X}",
            f"#define CGEN_SONGS {len(state.songs)}",
        ]
    elif p.init is not None:
        (pc, name), state = p.init
        sets, named = _copies_h(mem, plan, mod.var_ref)
        regions = sorted(set(regions) | named)
        sets = [
            f"#define CGEN_SONG0 0x{state.songs[0]:02X}",
            f"#define CGEN_SONGS {len(state.songs)}",
            *_io_h(state),
            *sets,
        ]
        c += _init_c(name, traps[("deopt", pc)])
    c += fns
    c_text = clang_format("\n".join(c) + "\n")
    store = _storage(mem, used, helpers, regions) + mod.state_h() + sets
    h_text = clang_format(_header(mem, used, [key[1] for key in traps], mod.konsts, store))
    times["print"] = time.perf_counter() - t1
    info = {
        "times": times,
        "regions": mem.regions,
        "code_span": mem.code_span,
        "code_held": mem.held,
        "traps": {k: key for key, k in traps.items() if k in mod.traps},
        "unknown_access": mem.unknown,
        "jumps": p.jumps or {},
        "structure": dict(mod.made),
        "provenance": {**mod.prov, "cells": [int(a) for a in np.flatnonzero(cells)]},
        "fixed": [] if p.fixed is None else p.fixed.tolist(),
        "state": {
            "cells": mod.state.counts(cells),
            "span": mod.state.counts(mod.cells & ~cells),
            "vars": {mod.var_ref(a): a for a in sorted(mod.state.at)},
        },
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
    init=None,
    songs=None,
    machine=None,
):
    """``(c_text, h_text, info)`` of the program in ``image`` (the whole address space) called
    at ``entries`` (``(pc, name)``, ``A`` the argument) with ``volatile`` ``[(lo, hi)]`` I/O
    ranges whose reads at ``sensitive_reads`` are kept; ``facts`` supply computed-jump targets only;
    ``entries[init]`` is called once, before any other entry (``None``: entries are called in
    any order), with ``A`` each of the range ``songs`` (default ``[0]``), run on ``machine``
    (:func:`deity_informant.postinit.run`): the other entries are lifted from what it leaves."""
    t0 = time.perf_counter()
    jf = jump_facts(facts, image) if facts is not None else None
    p, stats = lift(image, entries, volatile, jf, loaded, sensitive_reads, init, songs, machine)
    p.sensitive, p.loaded = tuple(sensitive_reads), loaded
    lift_time = time.perf_counter() - t0
    c, h, info = render(p, header, prefix)
    info["times"] = {"lift": lift_time, **info["times"]}
    info["facts_edges"] = list(jf.edges) if jf is not None else []
    info["stats"] = stats
    info["starts"] = sorted(p.starts | (p.lifted[1] if p.lifted else set()))
    info["init"] = None if p.init is None else "translated" if p.lifted else "replayed"
    return c, h, info
