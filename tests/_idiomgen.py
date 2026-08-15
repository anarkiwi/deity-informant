"""The canonical idiom suite: one catalog row, many machine spellings, one denotation.

A **shape** is a catalog row realised as a minimal complete driver; a **variant** is one
machine spelling of it, from explicit axes (index register, mode, store order, carry
crossing, loop form, stride, deref kind, illegals) and never from randomness.
"""

from __future__ import annotations

import itertools
from collections import namedtuple
from functools import lru_cache

from deity_informant import denote, frameprog
from deity_informant import structured as S
from deity_informant.asm6502 import AsmIllegal as Asm

ORG = 0x1000
SID = 0xD400
FRAMES = 12  # long enough for the row cursor to wrap the block twice; the suite's clock
CAP = 8  # variants kept per shape: the axis product, strided, never sampled

PTR = 0xF0  # the pointer word every address row is spelled through ($F0/$F1)
ROW = 0xF2  # the row cursor into a block
PAT = 0xF3  # the entry index into the declared pointer table
CNT = 0xF5  # a counted quantity, and the scratch a mask or step is staged in
FLG = 0xF6  # a bit field
TMP = 0xF7  # the byte cell most shapes are observed at
WRD = 0xF8  # a 16-bit scalar: $F8/$F9
ZTAB = 0xE0  # a page-zero table init stages out of ``tone``

STATE = (ROW, PAT, CNT, FLG, TMP, WRD, WRD + 1)

Shape = namedtuple("Shape", "row cell want doc data variants")
Variant = namedtuple("Variant", "label seed play")


def ptr_data(p):
    """A declared lo/hi pointer table over two stream blocks."""
    p.label("tab_lo").byte(("LOL", "blk0"), ("LOL", "blk1"))
    p.label("tab_hi").byte(("HIL", "blk0"), ("HIL", "blk1"))
    p.label("blk0").byte(0x10, 0x20, 0x30, 0x40, 0x50, 0x60, 0x70, 0x80)
    p.label("blk1").byte(0x11, 0x22, 0x33, 0x44, 0x55, 0x66, 0x77, 0x88)


def tab_data(p):
    """Two constant byte columns: the datum a scalar row is read out of."""
    p.label("tone").byte(0x21, 0x41, 0x11, 0x81, 0x15, 0x33, 0x51, 0x71)
    p.label("alt").byte(0x0F, 0xF0, 0x3C, 0xC3, 0x55, 0xAA, 0x18, 0x81)


def all_data(p):
    """Both declarations, for the shapes that read a stream through a pointer."""
    ptr_data(p)
    tab_data(p)


def index(p, ix, mode, arg):
    """Load the index register ``ix`` names, directly or through the accumulator."""
    if ix in ("X", "Y"):
        return p.i("LD" + ix, mode, arg)
    return p.i("LDA", mode, arg).i(ix)  # TAX / TAY: the same index, one hop further


def ixmode(ix):
    """The absolute-indexed mode the register ``ix`` names indexes through."""
    return "abs" + ix[-1].lower()


def reload_word(p, ix="X", order="lo", ill=False):
    """``ptr = tab[pat]``: the declared pair read at one index, stored in ``order``."""
    index(p, ix, "zp", PAT)
    md = ixmode(ix)

    def lo():
        p.i("LAX" if ill else "LDA", md, ("L", "tab_lo")).i("STA", "zp", PTR)

    def hi():
        p.i("LDA", md, ("L", "tab_hi")).i("STA", "zp", PTR + 1)

    for half in (lo, hi) if order == "lo" else (hi, lo):
        half()


def deref(p, kind="indy", dst=None):
    """Read the stream through the pointer; ``smc`` patches an absolute operand."""
    if kind == "indy":
        p.i("LDY", "zp", ROW).i("LDA", "indy", PTR)
    elif kind == "lax":
        p.i("LDY", "zp", ROW).i("LAX", "indy", PTR)
    elif kind == "indx":
        p.i("LDX", "imm", 0).i("LDA", "indx", PTR)
    else:
        p.i("LDA", "zp", PTR).i("STA", "abs", ("L", "smc", 1))
        p.i("LDA", "zp", PTR + 1).i("STA", "abs", ("L", "smc", 2))
        p.i("LDY", "zp", ROW).label("smc").i("LDA", "absy", 0)
    p.i("STA", "abs", SID)
    if dst is not None:
        p.i("STA", "zp", dst)


def wrap(p, then=None):
    """``row += 1``; at the block's end wrap to 0 and run ``then``."""
    p.i("INC", "zp", ROW)
    p.i("LDA", "zp", ROW).i("CMP", "imm", 8).i("BNE", "rel", ("L", "done"))
    p.i("LDA", "imm", 0).i("STA", "zp", ROW)
    if then is not None:
        then(p)
    p.label("done")


def step16(p, how, cell=PTR, step=1):
    """``cell:2 += step``, spelled as a carry chain, an INC chain or an ADC pair."""
    if how == "adc":
        p.i("LDA", "zp", cell).i("CLC").i("ADC", "imm", step).i("STA", "zp", cell)
        p.i("BCC", "rel", ("L", "nohi")).i("INC", "zp", cell + 1)
        p.label("nohi")
    elif how == "inc":
        for _ in range(step):
            p.i("INC", "zp", cell)
        p.i("BNE", "rel", ("L", "nohi")).i("INC", "zp", cell + 1)
        p.label("nohi")
    else:
        p.i("LDA", "zp", cell).i("CLC").i("ADC", "imm", step).i("STA", "zp", cell)
        p.i("LDA", "zp", cell + 1).i("ADC", "imm", 0).i("STA", "zp", cell + 1)


def zero_word(p):
    """``wrd = 0``: the play-visible constant store a stepped quantity is reset by."""
    p.i("LDA", "imm", 0).i("STA", "zp", WRD).i("STA", "zp", WRD + 1)


def lane(p, src, how, dst):
    """One byte moved from ``src`` to ``dst`` through each path the machine has."""
    if how == "lda":
        p.i("LDA", "zp", src).i("STA", "zp", dst)
    elif how == "ldx":
        p.i("LDX", "zp", src).i("STX", "zp", dst)
    elif how == "ldy":
        p.i("LDY", "zp", src).i("STY", "zp", dst)
    elif how == "lax":
        p.i("LAX", "zp", src).i("STA", "zp", dst)
    elif how == "tay":
        p.i("LDA", "zp", src).i("TAY").i("TYA").i("STA", "zp", dst)
    else:
        p.i("LDA", "zp", src).i("PHA").i("PLA").i("STA", "zp", dst)
    p.i("LDA", "zp", dst).i("STA", "abs", SID + 1)


def row_read(p, ix, table, dst=TMP, stride=1):
    """``dst = table[row]`` at ``stride``, indexed through the register ``ix`` names."""
    if stride == 1:
        index(p, ix, "zp", ROW)
    else:
        p.i("LDA", "zp", ROW).i("ASL", "acc").i("TA" + ix[-1])
    p.i("LDA", ixmode(ix), ("L", table)).i("STA", "zp", dst).i("STA", "abs", SID)


def cap(seq):
    """The axis product cut to ``CAP`` by a fixed stride: deterministic, never sampled."""
    got = list(seq)
    if len(got) <= CAP:
        return tuple(got)
    k = len(got) / CAP
    return tuple(got[int(i * k)] for i in range(CAP))


def V(label, play, seed=None):
    """One variant: its spelling label, its init staging and its play body."""
    return Variant(label, seed, play)


def _pair_row():
    """`pair-row`: the pointer word rebuilt from the declared pair every frame."""
    out = []
    axes = itertools.product(("X", "Y", "TAX", "TAY"), ("lo", "hi"), ("indy", "indx", "smc"))
    for ix, order, dk in axes:
        out.append(
            V(
                "%s/%s/%s" % (ix, order, dk),
                lambda p, i=ix, o=order, k=dk: (reload_word(p, i, o), deref(p, k), wrap(p)),
            )
        )
    for order, dk in itertools.product(("lo", "hi"), ("indy", "smc")):
        out.append(
            V(
                "LAX/%s/%s" % (order, dk),
                lambda p, o=order, k=dk: (reload_word(p, "Y", o, True), deref(p, k), wrap(p)),
            )
        )
    return cap(out)


def _word_pack():
    """`word-pack`: both lanes assembled through a hop rather than stored direct."""

    def via_reg(p, dk):
        p.i("LDX", "zp", PAT)
        p.i("LDA", "absx", ("L", "tab_lo")).i("TAY")
        p.i("LDA", "absx", ("L", "tab_hi")).i("STA", "zp", PTR + 1)
        p.i("TYA").i("STA", "zp", PTR)
        deref(p, dk)
        wrap(p)

    def via_cell(p, dk):
        p.i("LDX", "zp", PAT)
        p.i("LDA", "absx", ("L", "tab_lo")).i("STA", "zp", CNT)
        p.i("LDA", "absx", ("L", "tab_hi")).i("STA", "zp", PTR + 1)
        p.i("LDA", "zp", CNT).i("STA", "zp", PTR)
        deref(p, dk)
        wrap(p)

    def via_stack(p, dk):
        p.i("LDX", "zp", PAT)
        p.i("LDA", "absx", ("L", "tab_lo")).i("PHA")
        p.i("LDA", "absx", ("L", "tab_hi")).i("STA", "zp", PTR + 1)
        p.i("PLA").i("STA", "zp", PTR)
        deref(p, dk)
        wrap(p)

    def via_lax(p, dk):
        p.i("LDY", "zp", PAT)
        p.i("LAX", "absy", ("L", "tab_lo")).i("STX", "zp", PTR)
        p.i("LDA", "absy", ("L", "tab_hi")).i("STA", "zp", PTR + 1)
        deref(p, dk)
        wrap(p)

    out = []
    for dk in ("indy", "indx", "smc"):
        out.append(V("via-Y/%s" % dk, lambda p, k=dk: via_reg(p, k)))
        out.append(V("via-cell/%s" % dk, lambda p, k=dk: via_cell(p, k)))
        out.append(V("via-stack/%s" % dk, lambda p, k=dk: via_stack(p, k)))
        out.append(V("via-lax/%s" % dk, lambda p, k=dk: via_lax(p, k)))
    return cap(out)


def _lane_insert():
    """`lane-insert`: the word updated one lane at a time -- the step and the lo patch."""

    def lo_patch(p):
        p.i("LDX", "zp", PAT)
        p.i("LDA", "absx", ("L", "tab_lo")).i("STA", "zp", PTR)

    def near_page(p):
        """Stage the word one step short of a page so the carry arm is observed."""
        reload_word(p)
        p.i("LDA", "imm", 0xFC).i("STA", "zp", PTR)

    out = []
    axes = itertools.product(("adc", "inc", "pair"), ("indy", "smc"), (0, 1))
    for how, dk, cross in axes:
        out.append(
            V(
                "step-%s/%s%s" % (how, dk, "+cross" if cross else ""),
                lambda p, h=how, k=dk: (deref(p, k), step16(p, h), wrap(p, reload_word)),
                seed=near_page if cross else reload_word,
            )
        )
    for dk in ("indy", "smc"):
        out.append(
            V(
                "lo-patch/%s" % dk,
                lambda p, k=dk: (deref(p, k), wrap(p, lo_patch)),
                seed=reload_word,
            )
        )
    return cap(out)


def _lane_extract(hi):
    """`hi-byte`/`lo-byte`: one lane read back out of the pointer word."""
    src = PTR + 1 if hi else PTR
    return cap(
        V(
            "%s/%s" % ("hi" if hi else "lo", how),
            lambda p, h=how: (reload_word(p), lane(p, src, h, TMP), deref(p), wrap(p)),
        )
        for how in ("lda", "ldx", "ldy", "lax", "tay", "pha")
    )


def _shift_pair():
    """`shift-pair`: one 16-bit quantity shifted as two bytes, both directions."""

    def emit(p, spell):
        if spell == "lsr-ror-mem":
            p.i("LSR", "zp", WRD + 1).i("ROR", "zp", WRD)
        elif spell == "asl-rol-mem":
            p.i("ASL", "zp", WRD).i("ROL", "zp", WRD + 1)
        elif spell == "lsr-ror-acc":
            p.i("LDA", "zp", WRD + 1).i("LSR", "acc").i("STA", "zp", WRD + 1)
            p.i("LDA", "zp", WRD).i("ROR", "acc").i("STA", "zp", WRD)
        else:
            p.i("LDA", "zp", WRD).i("ASL", "acc").i("STA", "zp", WRD)
            p.i("LDA", "zp", WRD + 1).i("ROL", "acc").i("STA", "zp", WRD + 1)
        p.i("LDA", "zp", WRD).i("STA", "abs", SID)
        p.i("LDA", "zp", WRD + 1).i("STA", "abs", SID + 1)

    def seed(p):
        p.i("LDA", "imm", 0x34).i("STA", "zp", WRD)
        p.i("LDA", "imm", 0x12).i("STA", "zp", WRD + 1)

    spells = ("lsr-ror-mem", "asl-rol-mem", "lsr-ror-acc", "asl-rol-acc")
    return cap(V(s, lambda p, sp=s: emit(p, sp), seed=seed) for s in spells)


def _chain(sub):
    """`adc-chain`/`sbc-chain`: the wide add or subtract the machine spells as two."""
    op, undo = ("SBC", "DEC") if sub else ("ADC", "INC")
    clr, br = ("SEC", "BCS") if sub else ("CLC", "BCC")

    def emit(p, how, mode, arg, reset):
        p.i("LDA", "zp", WRD).i(clr).i(op, mode, arg).i("STA", "zp", WRD)
        if how == "pair":
            p.i("LDA", "zp", WRD + 1).i(op, "imm", 0).i("STA", "zp", WRD + 1)
        else:
            p.i(br, "rel", ("L", "nohi")).i(undo, "zp", WRD + 1).label("nohi")
        if reset:
            wrap(p, zero_word)
        p.i("LDA", "zp", WRD).i("STA", "abs", SID)
        p.i("LDA", "zp", WRD + 1).i("STA", "abs", SID + 1)

    def seed(p):
        p.i("LDA", "imm", 0x02 if sub else 0xFA).i("STA", "zp", WRD)
        p.i("LDA", "imm", 0x80).i("STA", "zp", WRD + 1)
        p.i("LDA", "imm", 0x05).i("STA", "zp", CNT)

    steps = (("imm", "imm", 5), ("cell", "zp", CNT), ("tab", "abs", ("L", "tone", 1)))
    return cap(
        V(
            "%s/%s%s" % (how, nm, "+reset" if rs else ""),
            lambda p, h=how, m=md, a=ar, r=rs: emit(p, h, m, a, r),
            seed=seed,
        )
        for how, (nm, md, ar), rs in itertools.product(("pair", "branch"), steps, (0, 1))
    )


def _shift_chain():
    """`shift-chain`: constant shifts nested one on another -- the machine shifts by one."""

    def emit(p, spell):
        mn, n = ("LSR", 4) if spell.startswith("lsr") else ("ASL", 2)
        p.i("LDX", "zp", ROW)
        if spell.endswith("mem"):
            p.i("LDA", "absx", ("L", "tone")).i("STA", "zp", TMP)
            for _ in range(n):
                p.i(mn, "zp", TMP)
        else:
            if spell.endswith("lax"):
                p.i("LDY", "zp", ROW).i("LAX", "absy", ("L", "tone"))
            else:
                p.i("LDA", "absx", ("L", "tone"))
            for _ in range(n):
                p.i(mn, "acc")
            p.i("STA", "zp", TMP)
        p.i("LDA", "zp", TMP).i("STA", "abs", SID)
        wrap(p)

    spells = ("lsr4-acc", "lsr4-mem", "asl2-acc", "asl2-mem", "lsr4-lax")
    return cap(V(s, lambda p, sp=s: emit(p, sp)) for s in spells)


def _flag_bit():
    """`flag-bit`: one bit of a state byte, recombined and then tested."""

    def emit(p, spell):
        if spell == "eor-imm":
            p.i("LDA", "zp", FLG).i("EOR", "imm", 0x40).i("STA", "zp", FLG)
        elif spell == "eor-abs":
            p.i("LDA", "zp", FLG).i("EOR", "abs", ("L", "alt", 1)).i("STA", "zp", FLG)
        elif spell == "ora-and":
            p.i("LDA", "zp", FLG).i("AND", "imm", 0xBF).i("ORA", "imm", 0x40)
            p.i("STA", "zp", FLG)
        elif spell == "asl-adc":
            p.i("LDA", "zp", FLG).i("ASL", "acc").i("ADC", "imm", 0).i("STA", "zp", FLG)
        else:
            p.i("LDA", "imm", 0x20).i("SLO", "zp", FLG).i("STA", "zp", FLG)
        p.i("LDA", "zp", FLG).i("AND", "imm", 0x40)
        p.i("BEQ", "rel", ("L", "off"))
        p.i("LDA", "imm", 0x11).i("STA", "abs", SID).i("JMP", "abs", ("L", "done"))
        p.label("off").i("LDA", "imm", 0x22).i("STA", "abs", SID)
        p.label("done")

    spells = ("eor-imm", "eor-abs", "ora-and", "asl-adc", "slo")
    return cap(V(s, lambda p, sp=s: emit(p, sp)) for s in spells)


def _table_row():
    """`table-row`: a row of a declared const table, in every loop form the drivers use."""

    def loop(p, kind, reg):
        md, other = ixmode(reg), "Y" if reg == "X" else "X"
        if kind == "dex-bpl":
            p.i("LD" + reg, "imm", 7).label("lp")
            p.i("LDA", md, ("L", "tone")).i("STA", "zp", TMP).i("STA", "abs", SID)
            p.i("DE" + reg).i("BPL", "rel", ("L", "lp"))
        elif kind == "inx-cpx-bne":
            p.i("LD" + reg, "imm", 0).label("lp")
            p.i("LDA", md, ("L", "tone")).i("STA", "zp", TMP).i("STA", "abs", SID)
            p.i("IN" + reg).i("CP" + reg, "imm", 8).i("BNE", "rel", ("L", "lp"))
        elif kind == "dec-bne":
            p.i("LDA", "imm", 8).i("STA", "zp", CNT).i("LD" + reg, "imm", 0).label("lp")
            p.i("LDA", md, ("L", "tone")).i("STA", "zp", TMP).i("STA", "abs", SID)
            p.i("IN" + reg).i("DEC", "zp", CNT).i("BNE", "rel", ("L", "lp"))
        elif kind == "unrolled":
            for k in range(4):
                p.i("LDA", "abs", ("L", "tone", k)).i("STA", "zp", TMP)
                p.i("STA", "abs", SID)
        else:
            p.i("LD" + other, "zp", ROW).i("T" + other + "A").i("TA" + reg)
            p.i("LDA", md, ("L", "tone")).i("STA", "zp", TMP).i("STA", "abs", SID)
            wrap(p)

    kinds = ("dex-bpl", "inx-cpx-bne", "dec-bne", "unrolled", "cursor")
    out = [
        V("%s/%s" % (k, r), lambda p, kk=k, rr=r: loop(p, kk, rr))
        for k, r in itertools.product(kinds, "XY")
        if not (k == "unrolled" and r == "Y")
    ]
    out.append(V("stride2/X", lambda p: (row_read(p, "X", "tone", stride=2), wrap(p))))
    return cap(out)


def _zp_row():
    """`zp-row`: the same row off a page-zero table, whose index wraps in 8 bits."""

    def seed(p):
        p.i("LDX", "imm", 7).label("cp")
        p.i("LDA", "absx", ("L", "tone")).i("STA", "zpx", ZTAB)
        p.i("DEX").i("BPL", "rel", ("L", "cp"))

    def emit(p, spell):
        if spell == "zpx":
            p.i("LDX", "zp", ROW).i("LDA", "zpx", ZTAB)
        elif spell == "zpx-tax":
            p.i("LDA", "zp", ROW).i("TAX").i("LDA", "zpx", ZTAB)
        elif spell == "zpy-ldx":
            p.i("LDY", "zp", ROW).i("LDX", "zpy", ZTAB).i("TXA")
        elif spell == "zpy-lax":
            p.i("LDY", "zp", ROW).i("LAX", "zpy", ZTAB)
        else:
            p.i("LDA", "zp", ROW).i("ASL", "acc").i("AND", "imm", 7).i("TAX")
            p.i("LDA", "zpx", ZTAB)
        p.i("STA", "zp", TMP).i("STA", "abs", SID)
        wrap(p)

    spells = ("zpx", "zpx-tax", "zpy-ldx", "zpy-lax", "zpx-stride2")
    return cap(V(s, lambda p, sp=s: emit(p, sp), seed=seed) for s in spells)


def _cell_read():
    """`cell-read`: one named cell, read at a constant address."""

    def seed(p, spell):
        p.i("LDA", "imm", 0x3A).i("STA", "zp", CNT)
        p.i("LDA", "imm", 0x3A if spell == "zp-staged" else 0x11).i("STA", "zp", TMP)

    def emit(p, spell):
        p.i("LDX", "zp", ROW).i("LDA", "absx", ("L", "tone")).i("STA", "abs", SID + 1)
        if spell == "abs":
            p.i("LDA", "abs", ("L", "tone", 2))
        elif spell == "zp-staged":
            p.i("LDA", "zp", CNT)
        elif spell == "lax-abs":
            p.i("LAX", "abs", ("L", "tone", 2))
        elif spell == "ldx-abs":
            p.i("LDX", "abs", ("L", "tone", 2)).i("TXA")
        else:
            p.i("LDY", "abs", ("L", "tone", 2)).i("TYA")
        p.i("STA", "zp", TMP).i("STA", "abs", SID)
        wrap(p)

    spells = ("abs", "zp-staged", "lax-abs", "ldx-abs", "ldy-abs")
    return cap(V(s, lambda p, sp=s: emit(p, sp), seed=lambda p, sp=s: seed(p, sp)) for s in spells)


def _stack_slot():
    """`stack-slot`: a byte of the $0100 page carrying a value between two statements."""

    def emit(p, spell):
        p.i("LDX", "zp", ROW)
        if spell == "push-pull":
            p.i("LDA", "absx", ("L", "tone")).i("PHA").i("LDA", "imm", 0).i("PLA")
        elif spell == "two-deep":
            p.i("LDA", "absx", ("L", "tone")).i("PHA")
            p.i("LDA", "absx", ("L", "alt")).i("PHA").i("PLA").i("STA", "abs", SID + 1)
            p.i("PLA")
        elif spell == "across-branch":
            p.i("LDA", "zp", ROW).i("AND", "imm", 1).i("BEQ", "rel", ("L", "even"))
            p.i("LDA", "absx", ("L", "tone")).i("PHA").i("JMP", "abs", ("L", "join"))
            p.label("even").i("LDA", "absx", ("L", "alt")).i("PHA")
            p.label("join").i("PLA")
        else:
            p.i("LDA", "absx", ("L", "tone")).i("PHA").i("TSX")
            p.i("LDA", "absx", 0x0101).i("PLA").i("TAX").i("TXA")
        p.i("STA", "zp", TMP).i("STA", "abs", SID)
        wrap(p)

    spells = ("push-pull", "two-deep", "across-branch", "tsx-read")
    return cap(V(s, lambda p, sp=s: emit(p, sp)) for s in spells)


def _deref_row():
    """`deref-row`: a row off a pointer the address computes rather than names."""

    def emit(p, dk, live):
        if live:
            reload_word(p)
        deref(p, dk, dst=TMP)
        wrap(p)

    return cap(
        V(
            "%s/%s" % ("reload" if live else "staged", dk),
            lambda p, k=dk, w=live: emit(p, k, w),
            seed=reload_word,
        )
        for live, dk in itertools.product((1, 0), ("indy", "indx", "lax", "smc"))
    )


def _field(set_bits):
    """`mask-const`/`set-const`: a field selected out of, or set into, a declared byte."""
    mn, k = ("ORA", 0x40) if set_bits else ("AND", 0x0F)

    def seed(p):
        p.i("LDA", "imm", k).i("STA", "zp", CNT)

    def emit(p, spell):
        p.i("LDX", "zp", ROW).i("LDA", "absx", ("L", "tone"))
        if spell == "imm":
            p.i(mn, "imm", k)
        elif spell == "abs":
            p.i(mn, "abs", ("L", "alt", 1))
        elif spell == "zp":
            p.i(mn, "zp", CNT)
        elif spell == "shift":
            for _ in range(4):
                p.i("ASL" if set_bits else "LSR", "acc")
            for _ in range(4):
                p.i("LSR" if set_bits else "ASL", "acc")
            p.i(mn, "imm", k if set_bits else 0xFF)
        elif set_bits:
            p.i("STA", "zp", TMP).i("LDA", "imm", k).i("SLO", "zp", TMP)
        else:
            p.i("LDX", "imm", k).i("SAX", "zp", TMP).i("LDA", "zp", TMP)
        p.i("STA", "zp", TMP).i("STA", "abs", SID)
        wrap(p)

    spells = ("imm", "abs", "zp", "shift", "illegal")
    return cap(V(s, lambda p, sp=s: emit(p, sp), seed=seed) for s in spells)


def _alu_op():
    """`alu-op`: the dialect's own operator over two declared bytes, spelled every way."""

    def seed(p):
        p.i("LDA", "abs", ("L", "alt", 3)).i("STA", "zp", CNT)

    def emit(p, spell):
        if spell == "absx":
            p.i("LDX", "zp", ROW).i("LDA", "absx", ("L", "tone")).i("EOR", "absx", ("L", "alt"))
        elif spell == "absy":
            p.i("LDY", "zp", ROW).i("LDA", "absy", ("L", "tone")).i("EOR", "absy", ("L", "alt"))
        elif spell == "zp":
            p.i("LDX", "zp", ROW).i("LDA", "absx", ("L", "tone")).i("EOR", "zp", CNT)
        elif spell == "via-cell":
            p.i("LDX", "zp", ROW).i("LDA", "absx", ("L", "tone")).i("STA", "zp", TMP)
            p.i("LDA", "absx", ("L", "alt")).i("EOR", "zp", TMP)
        else:
            p.i("LDY", "zp", ROW).i("LAX", "absy", ("L", "tone")).i("EOR", "absy", ("L", "alt"))
        p.i("STA", "zp", TMP).i("STA", "abs", SID)
        wrap(p)

    spells = ("absx", "absy", "zp", "via-cell", "lax")
    return cap(V(s, lambda p, sp=s: emit(p, sp), seed=seed) for s in spells)


def _widen():
    """`widen`: the byte a driver widens into an address, read at the index it becomes."""

    def emit(p, spell):
        if spell in ("absx", "absy"):
            reg = "X" if spell == "absx" else "Y"
            index(p, reg, "zp", ROW)
            p.i("LDA", ixmode(reg), ("L", "tone"))
        elif spell == "tax":
            p.i("LDA", "zp", ROW).i("TAX").i("LDA", "absx", ("L", "tone"))
        elif spell == "tay":
            p.i("LDA", "zp", ROW).i("TAY").i("LDA", "absy", ("L", "tone"))
        else:
            p.i("LDX", "zp", ROW).i("LDA", "absx", ("L", "tone"))
            p.i("EOR", "absx", ("L", "alt"))
        p.i("STA", "zp", TMP).i("STA", "abs", SID)
        wrap(p)

    spells = ("absx", "absy", "tax", "tay", "two-columns")
    return cap(V(s, lambda p, sp=s: emit(p, sp)) for s in spells)


def _carry_value():
    """`carry-value`: the carry outliving the operation that set it, kept as a value."""

    def seed(p):
        p.i("LDA", "imm", 0x80).i("STA", "zp", WRD)

    def emit(p, spell):
        p.i("LDA", "zp", WRD).i("CLC").i("ADC", "imm", 0x40).i("STA", "zp", WRD)
        if spell == "adc-capture":
            p.i("LDA", "imm", 0).i("ADC", "imm", 0).i("STA", "zp", FLG)
        elif spell == "rol-acc":
            p.i("LDA", "imm", 0).i("ROL", "acc").i("STA", "zp", FLG)
        elif spell == "rol-mem":
            p.i("LDA", "imm", 0).i("STA", "zp", FLG).i("ROL", "zp", FLG)
        elif spell == "branch":
            p.i("LDA", "imm", 0).i("BCC", "rel", ("L", "nc")).i("LDA", "imm", 1)
            p.label("nc").i("STA", "zp", FLG)
        else:
            p.i("LDA", "imm", 0).i("SBC", "imm", 0xFF).i("STA", "zp", FLG)
        wrap(p, zero_word)
        p.i("LDA", "zp", FLG).i("STA", "abs", SID)

    spells = ("adc-capture", "rol-acc", "rol-mem", "branch", "sbc-capture")
    return cap(V(s, lambda p, sp=s: emit(p, sp), seed=seed) for s in spells)


def _compare_value():
    """`compare-value`: a comparison kept as a value rather than folded into its use."""

    def seed(p):
        p.i("LDA", "abs", ("L", "alt", 2)).i("STA", "zp", CNT)

    def emit(p, spell):
        p.i("LDX", "zp", ROW)
        if spell == "cmp-rol":
            p.i("LDA", "absx", ("L", "tone")).i("CMP", "zp", CNT)
            p.i("LDA", "imm", 0).i("ROL", "acc").i("STA", "zp", FLG)
        elif spell == "cmp-branch":
            p.i("LDA", "absx", ("L", "tone")).i("CMP", "zp", CNT)
            p.i("LDA", "imm", 0).i("BCC", "rel", ("L", "lt")).i("LDA", "imm", 1)
            p.label("lt").i("STA", "zp", FLG)
        elif spell == "cpx-rol":
            p.i("CPX", "imm", 4).i("LDA", "imm", 0).i("ROL", "acc").i("STA", "zp", FLG)
        elif spell == "sbc-rol":
            p.i("LDA", "absx", ("L", "tone")).i("SEC").i("SBC", "zp", CNT)
            p.i("LDA", "imm", 0).i("ROL", "acc").i("STA", "zp", FLG)
        else:
            p.i("LDA", "absx", ("L", "tone")).i("DCP", "zp", CNT)
            p.i("LDA", "imm", 0).i("ROL", "acc").i("STA", "zp", FLG)
        p.i("LDA", "zp", FLG).i("STA", "abs", SID)
        wrap(p)

    spells = ("cmp-rol", "cmp-branch", "cpx-rol", "sbc-rol", "dcp-rol")
    return cap(V(s, lambda p, sp=s: emit(p, sp), seed=seed) for s in spells)


def _const_literal():
    """`const-literal`: one compile-time constant, reached through every register."""

    def seed(p):
        p.i("LDA", "imm", 0x40).i("STA", "zp", TMP)

    def emit(p, spell):
        if spell == "lda-imm":
            p.i("LDA", "imm", 0x40)
        elif spell == "ldx-imm":
            p.i("LDX", "imm", 0x40).i("TXA")
        elif spell == "ldy-imm":
            p.i("LDY", "imm", 0x40).i("TYA")
        elif spell == "shifted":
            p.i("LDA", "imm", 0x20).i("ASL", "acc")
        elif spell == "eor-imm":
            p.i("LDA", "imm", 0xFF).i("EOR", "imm", 0xBF)
        else:
            p.i("LDA", "imm", 0xC0).i("LDX", "imm", 0x60).i("SAX", "zp", TMP)
            p.i("LDA", "zp", TMP)
        p.i("STA", "zp", TMP).i("STA", "abs", SID)

    spells = ("lda-imm", "ldx-imm", "ldy-imm", "shifted", "eor-imm", "sax")
    return cap(V(s, lambda p, sp=s: emit(p, sp), seed=seed) for s in spells)


def _local_read():
    """`local-read`: one loaded byte reaching two sinks -- the web a register carries."""

    def emit(p, spell):
        p.i("LDX", "zp", ROW)
        if spell == "two-sinks":
            p.i("LDA", "absx", ("L", "tone")).i("STA", "zp", TMP).i("STA", "abs", SID)
        elif spell == "via-x":
            p.i("LDA", "absx", ("L", "tone")).i("TAX").i("STX", "zp", TMP)
            p.i("STX", "abs", SID)
        elif spell == "via-y":
            p.i("LDA", "absx", ("L", "tone")).i("TAY").i("STY", "zp", TMP)
            p.i("STY", "abs", SID)
        elif spell == "reloaded":
            p.i("LDA", "absx", ("L", "tone")).i("STA", "zp", TMP)
            p.i("LDA", "absx", ("L", "tone")).i("STA", "abs", SID)
        else:
            p.i("LDY", "zp", ROW).i("LAX", "absy", ("L", "tone"))
            p.i("STA", "zp", TMP).i("STX", "abs", SID)
        wrap(p)

    spells = ("two-sinks", "via-x", "via-y", "reloaded", "lax")
    return cap(V(s, lambda p, sp=s: emit(p, sp)) for s in spells)


SHAPES = (
    Shape("pair-row", "zp_%02X" % PTR, "addr", "u16 cell/table row", all_data, _pair_row()),
    Shape("word-pack", "zp_%02X" % PTR, "addr", "u16 value", all_data, _word_pack()),
    Shape("lane-insert", "zp_%02X" % PTR, "addr", "u16 lane update", all_data, _lane_insert()),
    Shape("hi-byte", "zp_%02X" % TMP, "byte", "u16 high byte", all_data, _lane_extract(True)),
    Shape("lo-byte", "zp_%02X" % TMP, "byte", "u16 low byte", all_data, _lane_extract(False)),
    Shape("shift-pair", "zp_%02X" % WRD, "flags", "u16 shift", tab_data, _shift_pair()),
    Shape("adc-chain", "zp_%02X" % WRD, "acc", "wide add", tab_data, _chain(False)),
    Shape("sbc-chain", "zp_%02X" % WRD, "acc", "wide subtract", tab_data, _chain(True)),
    Shape("shift-chain", "zp_%02X" % TMP, "byte", "one shift", tab_data, _shift_chain()),
    Shape("flag-bit", "zp_%02X" % FLG, "flags", "flag", tab_data, _flag_bit()),
    Shape("table-row", "zp_%02X" % TMP, "byte", "table[i]", tab_data, _table_row()),
    Shape("zp-row", "zp_%02X" % TMP, "byte", "zp table[i]", tab_data, _zp_row()),
    Shape("cell-read", "zp_%02X" % TMP, "const", "cell", tab_data, _cell_read()),
    Shape("stack-slot", "zp_%02X" % TMP, "byte", "named-unknown", tab_data, _stack_slot()),
    Shape("deref-row", "zp_%02X" % TMP, "byte", "*ptr[i]", all_data, _deref_row()),
    Shape("mask-const", "zp_%02X" % TMP, "byte", "field select", tab_data, _field(False)),
    Shape("set-const", "zp_%02X" % TMP, "byte", "field set", tab_data, _field(True)),
    Shape("alu-op", "zp_%02X" % TMP, "byte", "wide operator", tab_data, _alu_op()),
    Shape("widen", "zp_%02X" % ROW, "idx", "width coercion", tab_data, _widen()),
    Shape("carry-value", "zp_%02X" % FLG, "pred", "named-unknown", tab_data, _carry_value()),
    Shape("compare-value", "zp_%02X" % FLG, "pred", "named-unknown", tab_data, _compare_value()),
    Shape("const-literal", "zp_%02X" % TMP, "const", "const", tab_data, _const_literal()),
    Shape("local-read", "zp_%02X" % TMP, "byte", "local", tab_data, _local_read()),
)

BY_ROW = {s.row: s for s in SHAPES}
ALL = tuple((s.row, v.label) for s in SHAPES for v in s.variants)


def image(row, label):
    """``(mem, init, play, size)``: one variant assembled at ``ORG``, its data declared."""
    shape = BY_ROW[row]
    var = next(v for v in shape.variants if v.label == label)
    p = Asm(ORG)
    p.label("init").i("LDA", "imm", 0)
    for c in STATE:
        p.i("STA", "zp", c)
    if var.seed is not None:
        var.seed(p)
    p.i("RTS")
    p.label("play")
    var.play(p)
    p.i("RTS")
    shape.data(p)
    code = p.assemble()
    mem = bytearray(0x10000)
    mem[ORG : ORG + len(code)] = code
    return mem, p.labels["init"], p.labels["play"], len(code)


def form(rec):
    """One record's denotation as the census reads it: the constructor, or top by cause."""
    return "TOP/" + (rec.cause or "?") if rec.den == denote.TOP else rec.den[0]


@lru_cache(maxsize=None)
def solved(row, label):
    """``({spell: denotation}, size)`` over one variant's whole solution."""
    mem, init, play, size = image(row, label)
    model, _ev = S.decompile(mem, init, play, FRAMES)
    sol = denote.solve(frameprog.program(model))
    return {rec.spell: form(rec) for rec in sol.rec.values()}, size


def denotation(row, label):
    """The denotation the row's own named cell carries in this spelling."""
    return solved(row, label)[0].get(BY_ROW[row].cell, "absent")
