"""The canonical call suite: one call form, many machine spellings, one procedure.

A **shape** is a call form realised as a minimal complete driver; a **variant** is one
machine spelling of it. ``violations`` reads the docs/denotation-solve.md 8.4
invariant off the parsed program, and ``touches`` computes off the opcode table
which opcodes a driver must answer for.
"""

from __future__ import annotations

import itertools
from collections import namedtuple
from functools import lru_cache

import _idiomgen as I
from deity_informant import c64 as C
from deity_informant import frameproc, frameprog, frameval
from deity_informant import structured as S
from deity_informant.asm6502 import AsmIllegal as Asm
from deity_informant.lifter import OPS, lift
from deity_informant.vm import PcodeVM, run_irq, run_sub

SID = I.SID
ORG = I.ORG
FRAMES = I.FRAMES  # the suite's clock: long enough for the row cursor to wrap
CAP = I.CAP  # variants kept per shape: the axis product, strided, never sampled
ROW, CNT, FLG, TMP, PTR = I.ROW, I.CNT, I.FLG, I.TMP, I.PTR
SP = frameproc._SP  # the local a surviving stack fabric is threaded through

# ---- the stack-touching opcode set, read off the lift -------------------------
_SPVN = ("r", 3, 1)  # lifter.SP: the stack pointer is a named register varnode
_PAGE_ONE = range(0x0100, 0x0200)
_PROBE = 0x2000  # where an opcode is lifted to be read
_NEUTRAL = 0x3020  # an operand word naming no page-one address
_PAGE_BASE = 0x0100  # the page's own base: the operand that makes an EA page one
_WIDE = 1024  # an abstract value set wider than this is top

_BIN = {
    "INT_ADD": lambda a, b: a + b,
    "INT_SUB": lambda a, b: a - b,
    "INT_AND": lambda a, b: a & b,
    "INT_OR": lambda a, b: a | b,
    "INT_XOR": lambda a, b: a ^ b,
    "INT_LEFT": lambda a, b: a << b,
    "INT_RIGHT": lambda a, b: a >> b,
    "INT_EQUAL": lambda a, b: int(a == b),
    "INT_NOTEQUAL": lambda a, b: int(a != b),
    "INT_LESSEQUAL": lambda a, b: int(a <= b),
    "INT_CARRY": lambda a, b: int(a + b > 0xFF),
}


def _top(size):
    """Every value a ``size``-byte varnode can hold; ``None`` where that is too wide."""
    return frozenset(range(256)) if size == 1 else None


def _abstract(ops):
    """``{sp, page1}``: what one lifted instruction's P-Code does to the stack.

    A forward value analysis -- constants exactly, a byte-wide unknown as its whole
    range, anything wider as top -- answering the one question asked of it: is this
    LOAD/STORE address necessarily in page one."""
    env, out = {}, set()

    def val(vn):
        space, off, size = vn
        return frozenset((off,)) if space == "c" else env.get(tuple(vn), _top(size))

    for mn, dst, ins in ops:
        if _SPVN in [tuple(v) for v in ins] or (dst is not None and tuple(dst) == _SPVN):
            out.add("sp")
        if mn in ("LOAD", "STORE"):
            a = val(ins[0])
            if a and all(v in _PAGE_ONE for v in a):
                out.add("page1")
        if dst is None:
            continue
        if mn in ("COPY", "INT_ZEXT"):
            r = val(ins[0])
        elif mn in _BIN:
            a, b = val(ins[0]), val(ins[1])
            mask = (1 << (8 * dst[2])) - 1
            r = None
            if a is not None and b is not None and len(a) * len(b) <= _WIDE:
                r = frozenset(_BIN[mn](x, y) & mask for x in a for y in b)
        else:
            r = _top(dst[2])
        env[tuple(dst)] = None if r is not None and len(r) > _WIDE else r
    return out


@lru_cache(maxsize=None)
def touches(op, word=_NEUTRAL):
    """How opcode ``op`` reaches the stack when its operand bytes spell ``word``.

    ``sp`` names the stack pointer register, ``page1`` a load or store the lift puts
    in ``$0100..$01FF``, ``ctrl:`` the continuation the lifter records in ``stk``.
    Only ``page1`` can depend on ``word``."""
    mem = bytearray(0x10000)
    mem[_PROBE], mem[_PROBE + 1], mem[_PROBE + 2] = op, word & 0xFF, word >> 8
    rec = lift(mem, _PROBE)
    found = _abstract(rec["ops"])
    if rec["stk"]:
        found.add("ctrl:" + rec["stk"])
    return frozenset(found)


STACK_OPCODES = frozenset(op for op in OPS if touches(op))
STACK_MNEMONICS = frozenset(OPS[op][0] for op in STACK_OPCODES)
PAGE_ONE_OPCODES = frozenset(op for op in OPS if "page1" in touches(op, _PAGE_BASE))
# an operand alone puts these in page one, so coverage is per mode, not per opcode
PAGE_ONE_MODES = frozenset(OPS[op][1] for op in PAGE_ONE_OPCODES - STACK_OPCODES)

Shape = namedtuple("Shape", "row doc data variants lifts", defaults=(True,))
Variant = namedtuple("Variant", "label seed play subs irq")


def V(label, play, subs=None, seed=None, irq=False):
    """One variant: its spelling label, its init staging, its play body and its subs."""
    return Variant(label, seed, play, subs, irq)


def bump(p):
    """``row = (row + 1) & 7``: the frame cursor, spelled with no label of its own."""
    p.i("INC", "zp", ROW).i("LDA", "zp", ROW).i("AND", "imm", 7).i("STA", "zp", ROW)


def idx(p, reg, off=0):
    """``reg = (row + off) & 7``: the caller's own index into a declared column."""
    if off:
        p.i("LDA", "zp", ROW).i("CLC").i("ADC", "imm", off).i("AND", "imm", 7)
        p.i("TA" + reg[-1])
    else:
        p.i("LD" + reg, "zp", ROW)


def col(p, reg, table="tone", off=0):
    """``a = table[reg]``: the declared read every callee is built around."""
    p.i("LDA", I.ixmode(reg), ("L", table, off))


def vec_data(p):
    """The declared lo/hi columns a computed call's target is read out of."""
    I.tab_data(p)
    p.label("veclo").byte(("LOL", "vt0"), ("LOL", "vt1"))
    p.label("vechi").byte(("HIL", "vt0"), ("HIL", "vt1"))


def trick_data(p):
    """The same columns holding ``target - 1``: what an RTS trick pushes.

    ``trkw`` is the same words interleaved, which is what a loop or a pointer walks;
    ``st2``/``st3`` sit in it unreached, so the trace closes two of four rows."""
    I.tab_data(p)
    p.label("trklo").byte(("LOL", "st0", -1), ("LOL", "st1", -1))
    p.label("trkhi").byte(("HIL", "st0", -1), ("HIL", "st1", -1))
    p.label("trkw")
    for nm in ("st0", "st1", "st2", "st3"):
        p.byte(("LOL", nm, -1), ("HIL", nm, -1))


def _stub(p, name, table):
    """One leaf a computed transfer may land in; it returns to play's own caller."""
    p.label(name).i("LDX", "zp", ROW)
    col(p, "X", table)
    p.i("STA", "abs", SID).i("RTS")


def _vec_stubs(p):
    """The pair a vector call's declared columns name."""
    _stub(p, "vt0", "tone")
    _stub(p, "vt1", "alt")


def _trick_stubs(p):
    """The leaves an RTS trick's declared columns name, one short of each."""
    _stub(p, "st0", "tone")
    _stub(p, "st1", "alt")
    _stub(p, "st2", "tone")
    _stub(p, "st3", "alt")


def _leaf_call():
    """`leaf-call`: one static JSR to a leaf, at each place in the caller's body."""

    def sub(p, sink):
        p.label("leaf").i("LDX", "zp", ROW)
        col(p, "X")
        if sink == "sid":
            p.i("STA", "abs", SID)
        else:
            p.i("STA", "zp", TMP)
        p.i("RTS")

    def play(p, where, sink):
        if where == "mid":
            p.i("LDA", "zp", ROW).i("STA", "abs", SID + 1)
        if where != "tail":
            p.i("JSR", "abs", ("L", "leaf"))
        bump(p)
        if where == "tail":
            p.i("JSR", "abs", ("L", "leaf"))
        if sink == "cell":
            p.i("LDA", "zp", TMP).i("STA", "abs", SID)

    return I.cap(
        V("%s/%s" % (w, sk), lambda p, a=w, b=sk: play(p, a, b), lambda p, b=sk: sub(p, b))
        for w, sk in itertools.product(("head", "mid", "tail"), ("sid", "cell"))
    )


def _multi_site():
    """`multi-site`: two and three static sites on one leaf -- the duplication case."""

    def sub(p, arg):
        p.label("leaf")
        if arg is None:
            p.i("LDX", "zp", ROW)
        col(p, "X" if arg is None else arg)
        p.i("STA", "abs", SID).i("RTS")

    def play(p, n, arg):
        for k in range(n):
            if arg is not None:
                idx(p, arg, k)
            p.i("JSR", "abs", ("L", "leaf"))
        bump(p)

    return I.cap(
        V(
            "%d-site/%s" % (n, arg or "no-arg"),
            lambda p, a=n, b=arg: play(p, a, b),
            lambda p, b=arg: sub(p, b),
        )
        for n, arg in itertools.product((2, 3), ("X", "Y", None))
    )


def _nested():
    """`nested`: play -> A -> B, the two-deep chain one inline cannot flatten."""

    def subs(p, reg, post):
        p.label("suba").i("LDA", "zp", ROW).i("STA", "abs", SID + 1)
        idx(p, reg)
        p.i("JSR", "abs", ("L", "subb"))
        if post:
            p.i("LDX", "zp", ROW)
            col(p, "X", "alt")
            p.i("STA", "abs", SID + 2)
        p.i("RTS")
        p.label("subb")
        col(p, reg)
        p.i("STA", "abs", SID).i("RTS")

    def play(p):
        p.i("JSR", "abs", ("L", "suba"))
        bump(p)

    return I.cap(
        V("%s/%s" % (r, "post" if q else "tail"), play, lambda p, a=r, b=q: subs(p, a, b))
        for r, q in itertools.product(("X", "Y"), (0, 1))
    )


def _arg_pass():
    """`arg-pass`: one argument, in each place the machine has to put one in."""

    def sub(p, how):
        p.label("leaf")
        if how == "a":
            p.i("TAX")
        elif how == "cell":
            p.i("LDX", "zp", CNT)
        elif how == "stack-tsx":
            p.i("TSX").i("LDA", "absx", 0x0103).i("TAX")
        elif how == "stack-pla":
            p.i("PLA").i("STA", "zp", CNT).i("PLA").i("STA", "zp", FLG)
            p.i("PLA").i("TAX")
            p.i("LDA", "zp", FLG).i("PHA").i("LDA", "zp", CNT).i("PHA")
        col(p, "Y" if how == "y" else "X")
        p.i("STA", "abs", SID).i("RTS")

    def play(p, how):
        if how in ("a", "stack-tsx", "stack-pla"):
            p.i("LDA", "zp", ROW)
            if how != "a":
                p.i("PHA")
        elif how == "cell":
            p.i("LDA", "zp", ROW).i("STA", "zp", CNT)
        else:
            idx(p, how.upper())
        p.i("JSR", "abs", ("L", "leaf"))
        if how == "stack-tsx":
            p.i("PLA")  # stack-pla's callee consumed the argument itself
        bump(p)

    spells = ("a", "x", "y", "cell", "stack-tsx", "stack-pla")
    return I.cap(V(s, lambda p, h=s: play(p, h), lambda p, h=s: sub(p, h)) for s in spells)


def _ret_value():
    """`ret-value`: one result, in each place the machine has to hand one back in."""

    def sub(p, how):
        p.label("leaf").i("LDX", "zp", ROW)
        col(p, "X")
        if how == "x":
            p.i("TAX")
        elif how == "y":
            p.i("TAY")
        elif how == "carry":
            p.i("CMP", "imm", 0x40)
        elif how == "cell":
            p.i("STA", "zp", TMP)
        p.i("RTS")

    def play(p, how):
        p.i("JSR", "abs", ("L", "leaf"))
        if how == "x":
            p.i("STX", "abs", SID)
        elif how == "y":
            p.i("STY", "abs", SID)
        elif how == "carry":
            p.i("LDA", "imm", 0).i("ROL", "acc").i("STA", "abs", SID)
        elif how == "cell":
            p.i("LDA", "zp", TMP).i("STA", "abs", SID)
        else:
            p.i("STA", "abs", SID)
        bump(p)

    spells = ("a", "x", "y", "carry", "cell")
    return I.cap(V(s, lambda p, h=s: play(p, h), lambda p, h=s: sub(p, h)) for s in spells)


def _tail_call():
    """`tail-call`: a JMP into a subroutine -- the call the author spelled as a goto."""

    def subs(p):
        _stub(p, "leaf", "tone")
        _stub(p, "leaf2", "alt")

    def play(p, how):
        if how == "shared":
            p.i("JSR", "abs", ("L", "leaf2"))
        bump(p)
        if how == "cond":
            p.i("LDA", "zp", ROW).i("AND", "imm", 1).i("BEQ", "rel", ("L", "even"))
            p.i("JMP", "abs", ("L", "leaf2"))
            p.label("even")
        p.i("JMP", "abs", ("L", "leaf"))

    spells = ("only", "shared", "cond")
    return I.cap(V(s, lambda p, h=s: play(p, h), subs) for s in spells)


def _rts_trick():
    """`rts-trick`: the target word pushed and returned into (``_fuzzgen.t_rts_trick``).

    Seven spellings of one question -- what value reaches the cell the ``RTS`` reads:
    adjacent, arithmetic, split across branch arms, in a loop, out of a table, out of
    a pointer, and ``open``, whose four-row table the trace closes two rows of."""

    def push(p, name):
        p.i("LDA", "imm", ("HIL", name, -1)).i("PHA")
        p.i("LDA", "imm", ("LOL", name, -1)).i("PHA")

    def word_ptr(p):
        """``ptr = trkw + 2 * (row & 1)``: the pointer the pushed word is read through."""
        p.i("LDA", "zp", ROW).i("AND", "imm", 1).i("ASL", "acc")
        p.i("CLC").i("ADC", "imm", ("LOL", "trkw")).i("STA", "zp", PTR)
        p.i("LDA", "imm", ("HIL", "trkw")).i("ADC", "imm", 0).i("STA", "zp", PTR + 1)

    def push_ptr(p):
        """``(ptr),Y`` hi then lo: the real tunes' spelling, blind to the static base."""
        p.i("LDY", "imm", 1).i("LDA", "indy", PTR).i("PHA")
        p.i("DEY").i("LDA", "indy", PTR).i("PHA")

    def play(p, how):
        bump(p)
        if how == "arith":  # the pushed byte is an op node, not a const/loc/mem one
            p.i("LDA", "imm", ("HIL", "st0", -1)).i("PHA")
            p.i("LDA", "zp", ROW).i("AND", "imm", 1).i("STA", "zp", CNT)
            p.i("ASL", "acc").i("ASL", "acc").i("ASL", "acc")
            p.i("CLC").i("ADC", "zp", CNT)
            p.i("CLC").i("ADC", "imm", ("LOL", "st0", -1)).i("PHA")
        elif how == "const":
            push(p, "st0")
        elif how == "two-arm":
            p.i("LDA", "zp", ROW).i("AND", "imm", 1).i("BEQ", "rel", ("L", "even"))
            push(p, "st1")
            p.i("JMP", "abs", ("L", "go"))
            p.label("even")
            push(p, "st0")
            p.label("go")
        elif how == "loop":
            p.i("LDA", "zp", ROW).i("AND", "imm", 1).i("ASL", "acc").i("ORA", "imm", 1)
            p.i("TAX").i("LDY", "imm", 1)
            p.label("tlp").i("LDA", "absx", ("L", "trkw")).i("PHA")
            p.i("DEX").i("DEY").i("BPL", "rel", ("L", "tlp"))
        elif how == "table":
            p.i("LDA", "zp", ROW).i("AND", "imm", 1).i("TAX")
            p.i("LDA", "absx", ("L", "trkhi")).i("PHA")
            p.i("LDA", "absx", ("L", "trklo")).i("PHA")
        else:
            word_ptr(p)
            if how == "open":
                p.i("LDA", "zp", CNT).i("CLC").i("ADC", "zp", PTR).i("STA", "zp", PTR)
                p.i("LDA", "imm", 0).i("ADC", "zp", PTR + 1).i("STA", "zp", PTR + 1)
            push_ptr(p)
        p.i("RTS")

    spells = ("const", "arith", "two-arm", "loop", "table", "ptr", "open")
    return I.cap(V(s, lambda p, h=s: play(p, h), _trick_stubs) for s in spells)


def _vector_call():
    """`vector-call`: the transfer whose target is a cell -- de-SMC's declared table."""

    def play(p, how):
        bump(p)
        p.i("LDA", "zp", ROW).i("AND", "imm", 1).i("TAX")
        if how == "jmpind":
            p.i("LDA", "absx", ("L", "veclo")).i("STA", "zp", PTR)
            p.i("LDA", "absx", ("L", "vechi")).i("STA", "zp", PTR + 1)
            p.i("JMP", "ind", PTR)
            return
        p.i("LDA", "absx", ("L", "veclo")).i("STA", "abs", ("L", "site", 1))
        p.i("LDA", "absx", ("L", "vechi")).i("STA", "abs", ("L", "site", 2))
        p.label("site").i("JSR" if how == "smc-jsr" else "JMP", "abs", ("L", "vt0"))

    spells = ("jmpind", "smc-jsr", "smc-jmp")
    return I.cap(V(s, lambda p, h=s: play(p, h), _vec_stubs) for s in spells)


def _tail_recursion():
    """`tail-recursion`: the recursive call the return follows -- the corpus's only one.

    Every call site is immediately the exit, so the removal is a loop rather than an
    inline and no bound on the depth is needed to state it."""

    def sub(p, ctr):
        p.label("rec")
        if ctr == "cell":
            p.i("LDX", "zp", CNT)
        col(p, "X")
        p.i("STA", "abs", SID)
        if ctr == "cell":
            p.i("DEC", "zp", CNT)
        else:
            p.i("DEX")
        p.i("BMI", "rel", ("L", "recout"))
        p.i("JSR", "abs", ("L", "rec"))
        p.label("recout").i("RTS")

    def play(p, ctr, depth):
        if depth == "row":
            p.i("LDA", "zp", ROW).i("AND", "imm", 3)
        else:
            p.i("LDA", "imm", 3)
        if ctr == "cell":
            p.i("STA", "zp", CNT)
        else:
            p.i("TAX")
        p.i("JSR", "abs", ("L", "rec"))
        bump(p)

    return I.cap(
        V("%s/%s" % (c, d), lambda p, a=c, b=d: play(p, a, b), lambda p, a=c: sub(p, a))
        for c, d in itertools.product(("x", "cell"), ("const", "row"))
    )


def _deep_recursion():
    """`deep-recursion`: the control fixture -- work after the call, so no loop holds."""

    def sub(p, around):
        p.label("rec").i("LDA", "zp", CNT).i("BEQ", "rel", ("L", "recout"))
        if around:
            p.i("LDX", "zp", CNT)
            col(p, "X")
            p.i("STA", "abs", SID)
        p.i("DEC", "zp", CNT)
        p.i("JSR", "abs", ("L", "rec"))
        p.i("INC", "zp", CNT).i("LDX", "zp", CNT)
        col(p, "X", "alt")
        p.i("STA", "abs", SID + 1)
        p.label("recout").i("RTS")

    def play(p, depth):
        if depth == "row":
            p.i("LDA", "zp", ROW).i("AND", "imm", 3)
        else:
            p.i("LDA", "imm", 3)
        p.i("STA", "zp", CNT).i("JSR", "abs", ("L", "rec"))
        bump(p)

    return I.cap(
        V(
            "%s/%s" % (d, "around" if a else "after"),
            lambda p, b=d: play(p, b),
            lambda p, c=a: sub(p, c),
        )
        for d, a in itertools.product(("const", "row"), (0, 1))
    )


def _two_callers():
    """`two-callers`: one leaf under two callers, so the copies land in two contexts."""

    def arg(p, how, twist):
        if how == "cell":
            p.i("LDA", "zp", ROW)
            if twist:
                p.i("EOR", "imm", 3)
            p.i("STA", "zp", CNT)
        elif twist:
            p.i("LDA", "zp", ROW).i("EOR", "imm", 3).i("TA" + how[-1])
        else:
            idx(p, how)

    def subs(p, how, sink):
        for name, reg, twist in (("suba", SID + 1, 0), ("subb", SID + 2, 1)):
            p.label(name).i("LDA", "zp", ROW).i("STA", "abs", reg)
            arg(p, how, twist)
            p.i("JSR", "abs", ("L", "leaf")).i("RTS")
        p.label("leaf")
        if how == "cell":
            p.i("LDX", "zp", CNT)
        col(p, "X" if how == "cell" else how)
        if sink == "cell":
            p.i("STA", "zp", TMP).i("LDA", "zp", TMP)
        p.i("STA", "abs", SID).i("RTS")

    def play(p):
        p.i("JSR", "abs", ("L", "suba")).i("JSR", "abs", ("L", "subb"))
        bump(p)

    return I.cap(
        V("%s/%s" % (h, sk), play, lambda p, a=h, b=sk: subs(p, a, b))
        for h, sk in itertools.product(("X", "Y", "cell"), ("sid", "cell"))
    )


def _branchy_callee():
    """`branchy-callee`: a callee with an internal branch and loop, on two sites.

    The shape that decides whether duplicated block labels obstruct inlining: the body
    carries interior pcs a copy would have to re-name, and its emitted text is the
    evidence the question is answered against."""

    def sub(p, sense, loop):
        taken, skip = ("BCC", "BCS") if sense == "cc" else ("BCS", "BCC")
        p.label("bsub").i("LDA", "imm", 0).i("STA", "zp", TMP)
        p.i("LDY", "imm", 3 if loop == "dey" else 0)
        p.label("blp").i("TYA").i("CLC").i("ADC", "zp", CNT).i("AND", "imm", 7).i("TAX")
        col(p, "X")
        p.i("CMP", "imm", 0x40).i(taken, "rel", ("L", "blo"))
        p.i("ORA", "imm", 0x01).i("JMP", "abs", ("L", "bjoin"))
        p.label("blo").i("AND", "imm", 0x7F)
        p.label("bjoin").i("EOR", "zp", TMP).i("STA", "zp", TMP)
        if loop == "dey":
            p.i("DEY").i("BPL", "rel", ("L", "blp"))
        else:
            p.i("INY").i("CPY", "imm", 4).i("BNE", "rel", ("L", "blp"))
        p.i("LDA", "zp", TMP).i("STA", "abs", SID)
        p.i(skip, "rel", ("L", "bend"))
        p.i("LDA", "zp", TMP).i("STA", "abs", SID + 3)
        p.label("bend").i("RTS")

    def play(p):
        p.i("LDA", "zp", ROW).i("STA", "zp", CNT).i("JSR", "abs", ("L", "bsub"))
        p.i("LDA", "zp", ROW).i("EOR", "imm", 4).i("STA", "zp", CNT)
        p.i("JSR", "abs", ("L", "bsub"))
        bump(p)

    return I.cap(
        V("%s/%s" % (s, l), play, lambda p, a=s, b=l: sub(p, a, b))
        for s, l in itertools.product(("cc", "cs"), ("dey", "iny"))
    )


def _save_s(p):
    """``cnt = s``: what a player that moves the stack has to keep to put it back."""
    p.i("TSX").i("STX", "zp", CNT)


def _restore_s(p):
    """``s = cnt``: the move undone, so the frame still returns where it entered."""
    p.i("LDX", "zp", CNT).i("TXS")


def _flag_record():
    """`flag-record`: the status byte parked in page one and read back (PHP/PLP)."""

    def play(p, pull, span):
        p.i("LDX", "zp", ROW)
        col(p, "X")
        p.i("CMP", "imm", 0x40).i("PHP")
        if span == "across":
            bump(p)
        if pull == "plp":
            p.i("PLP").i("LDA", "imm", 0).i("ROL", "acc")
        else:
            p.i("PLA").i("AND", "imm", 1)
        p.i("STA", "abs", SID)
        if span != "across":
            bump(p)

    return I.cap(
        V("%s/%s" % (a, b), lambda p, x=a, y=b: play(p, x, y))
        for a, b in itertools.product(("plp", "pla"), ("tight", "across"))
    )


def _stack_move():
    """`stack-move`: a player that relocates S over its body and puts it back (TXS)."""

    def play(p, dest, body):
        _save_s(p)
        if dest == "const":
            p.i("LDX", "imm", 0x40)
        else:
            p.i("LDA", "zp", ROW).i("ORA", "imm", 0x40).i("TAX")
        p.i("TXS").i("LDX", "zp", ROW)
        col(p, "X")
        if body == "push":
            p.i("PHA").i("PLA")
        else:
            p.i("STA", "absx", 0x0100).i("LDA", "absx", 0x0100)
        p.i("STA", "abs", SID)
        _restore_s(p)
        bump(p)

    return I.cap(
        V("%s/%s" % (a, b), lambda p, x=a, y=b: play(p, x, y))
        for a, b in itertools.product(("const", "row"), ("push", "cell"))
    )


_S_BASE = {("LAS", "far"): ("L", "tone"), ("TAS", "far"): 0x0300}


def _s_illegal():
    """`s-illegal`: the two illegals whose operand is S itself (TAS $9B, LAS $BB)."""

    def play(p, mn, site):
        base = 0x0100 if site == "page1" else _S_BASE[(mn, site)]
        _save_s(p)
        p.i("LDY", "zp", ROW)
        if mn == "LAS":
            p.i("LAS", "absy", base)
        else:
            col(p, "Y")
            p.i("LDX", "imm", 0x0F).i("TAS", "absy", base).i("LDA", "absy", base)
        p.i("STA", "abs", SID)
        _restore_s(p)
        bump(p)

    return I.cap(
        V("%s/%s" % (a.lower(), b), lambda p, x=a, y=b: play(p, x, y))
        for a, b in itertools.product(("LAS", "TAS"), ("page1", "far"))
    )


def _page_one_cell():
    """`page-one-cell`: page one addressed as plain memory, by no stack opcode at all."""

    def at(p, mn, mode):
        if mode == "abs":
            p.i(mn, "abs", 0x0108)
        elif mode == "indy":
            p.i("LDY", "zp", ROW).i(mn, "indy", PTR)
        else:
            p.i("LD" + mode[-1].upper(), "zp", ROW).i(mn, mode, 0x0100)

    def play(p, mode, span):
        if mode == "indy":
            p.i("LDA", "imm", 0x00).i("STA", "zp", PTR)
            p.i("LDA", "imm", 0x01).i("STA", "zp", PTR + 1)
        if span == "cross":
            at(p, "LDA", mode)
            p.i("STA", "abs", SID)
        p.i("LDX", "zp", ROW)
        col(p, "X")
        at(p, "STA", mode)
        if span != "cross":
            at(p, "LDA", mode)
            p.i("STA", "abs", SID)
        bump(p)

    return I.cap(
        V("%s/%s" % (a, b), lambda p, x=a, y=b: play(p, x, y))
        for a, b in itertools.product(("abs", "absx", "absy", "indy"), ("same", "cross"))
    )


_VECS = {"cinv": (0x0314, 0x0315), "hw": (0xFFFE, 0xFFFF)}


def _handler_seed(p, vec, name="play"):
    """Init writes the entry ``name`` into the vector ``vec``: what makes it the frame."""
    lo, hi = _VECS[vec]
    p.i("LDA", "imm", ("LOL", name)).i("STA", "abs", lo)
    p.i("LDA", "imm", ("HIL", name)).i("STA", "abs", hi)


def _irq_frame():
    """`irq-frame`: the player that IS the handler, so its exit is an RTI, not an RTS.

    A write at each end of the body, either side of the cursor bump, so the pair one
    frame emits names its own frame: a boundary off by one shows as a shifted pair."""

    def play(p, vec):
        p.i("LDX", "zp", ROW)
        col(p, "X")
        p.i("STA", "abs", SID)
        bump(p)
        p.i("LDX", "zp", ROW)
        col(p, "X", "alt")
        p.i("STA", "abs", SID + 1)
        if vec == "cinv":
            p.i("PLA").i("TAY").i("PLA").i("TAX").i("PLA")  # the KERNAL prologue's frame
        p.i("RTI")

    return I.cap(
        V(
            v,
            lambda p, x=v: play(p, x),
            seed=lambda p, x=v: _handler_seed(p, x),
            irq=True,
        )
        for v in ("hw", "cinv")
    )


def _brk_vector():
    """`brk-vector`: BRK answered by the hardware vector -- the software interrupt."""

    def sub(p):
        p.label("hnd").i("LDX", "zp", ROW)
        col(p, "X")
        p.i("STA", "abs", SID).i("RTI")

    def play(p, where):
        if where == "tail":
            bump(p)
        p.i("BRK").byte(0xEA)  # BRK's own skipped operand byte: it returns to pc + 2
        if where != "tail":
            bump(p)

    return I.cap(
        V(w, lambda p, x=w: play(p, x), sub, seed=lambda p: _handler_seed(p, "hw", "hnd"))
        for w in ("head", "tail")
    )


SHAPES = (
    Shape("leaf-call", "one static site on a leaf", I.tab_data, _leaf_call()),
    Shape("multi-site", "two and three sites on one leaf", I.tab_data, _multi_site()),
    Shape("nested", "play -> A -> B", I.tab_data, _nested()),
    Shape("arg-pass", "one argument, every register", I.tab_data, _arg_pass()),
    Shape("ret-value", "one result, every register", I.tab_data, _ret_value()),
    Shape("tail-call", "a JMP into a subroutine", I.tab_data, _tail_call()),
    Shape("rts-trick", "the pushed target returned into", trick_data, _rts_trick()),
    Shape("vector-call", "the target read out of a cell", vec_data, _vector_call()),
    Shape("tail-recursion", "the recursive call a return follows", I.tab_data, _tail_recursion()),
    Shape("deep-recursion", "work after the recursive call", I.tab_data, _deep_recursion()),
    Shape("two-callers", "one leaf under two callers", I.tab_data, _two_callers()),
    Shape(
        "branchy-callee",
        "an internal branch and loop, two sites",
        I.tab_data,
        _branchy_callee(),
    ),
    Shape("flag-record", "the status byte parked in page one", I.tab_data, _flag_record()),
    Shape("stack-move", "a player that relocates S", I.tab_data, _stack_move()),
    Shape("s-illegal", "the illegals whose operand is S", I.tab_data, _s_illegal()),
    Shape("page-one-cell", "page one as plain memory", I.tab_data, _page_one_cell()),
    Shape("irq-frame", "the player that is the handler", I.tab_data, _irq_frame()),
    Shape("brk-vector", "BRK answered by a vector", I.tab_data, _brk_vector(), False),
)

BY_ROW = {s.row: s for s in SHAPES}
EVERY = tuple((s.row, v.label) for s in SHAPES for v in s.variants)
ALL = tuple((s.row, v.label) for s in SHAPES if s.lifts for v in s.variants)


def _asm(row, label):
    """The assembler of one variant: init, play, its subs and its declared data."""
    shape = BY_ROW[row]
    var = next(v for v in shape.variants if v.label == label)
    p = Asm(ORG)
    p.label("init").i("LDA", "imm", 0)
    for c in I.STATE:
        p.i("STA", "zp", c)
    if var.seed is not None:
        var.seed(p)
    p.i("RTS")
    p.label("play")
    var.play(p)
    p.i("RTS")
    if var.subs is not None:
        var.subs(p)
    shape.data(p)
    return p, var


def image(row, label):
    """``(mem, init, play, size)``: one variant assembled at ``ORG``, subs then data.

    ``play`` is ``0`` where the variant's frame is an interrupt: the entry is then
    the vector its init installed, which is what makes an RTI a frame boundary."""
    p, var = _asm(row, label)
    code = p.assemble()
    mem = bytearray(0x10000)
    mem[ORG : ORG + len(code)] = code
    return mem, p.labels["init"], 0 if var.irq else p.labels["play"], len(code)


def instructions(row, label):
    """``(opcode, operand word)`` of every instruction one variant's image holds.

    Read off the assembled bytes, so a driver covers exactly what it encodes and a
    label claiming more than its spelling cannot lie about it."""
    p, _var = _asm(row, label)
    code = p.assemble() + b"\0\0"
    out, off = [], 0
    for it in p.items:
        if it[0] == "i":
            out.append((code[off], code[off + 1] | (code[off + 2] << 8)))
        off += p._size(it)
    return tuple(out)


COVERED = frozenset(op for v in EVERY for op, _w in instructions(*v))
PAGE_ONE_COVERED = frozenset(
    OPS[op][1]
    for v in EVERY
    for op, word in instructions(*v)
    if op not in STACK_OPCODES and "page1" in touches(op, word)
)


@lru_cache(maxsize=None)
def machine_frames(row, label, nframes=FRAMES):
    """Per-frame ``(reg, val)`` SID writes of the raw 6510, at the machine's cadence.

    Init runs as a subroutine, then one interrupt per frame straight into the vector
    the driver installed -- no model, no artifact, so the frames it cuts are the
    machine's own and an artifact frame that moved a write disagrees with it."""
    mem, init, play, _size = image(row, label)
    if play:
        raise ValueError("%s:%s is not an interrupt frame" % (row, label))
    vm = PcodeVM(bytearray(mem))
    vm.wlog = []
    cache = {}
    run_sub(vm, init, cache, lift)
    kernal = label == "cinv"
    handler = C.read_vector(vm.mem, _VECS[label][0])
    out = []
    for _f in range(nframes):
        at = len(vm.wlog)
        run_irq(vm, handler, cache, lift, kernal)
        out.append([(r, v) for _c, r, v in vm.wlog[at:]])
    return out


@lru_cache(maxsize=None)
def built(row, label):
    """``(model, artifact)`` of one variant, lifted and emitted at the suite's clock.

    The program is the text read back, so the invariant, the fault and Gate FP all
    judge the one object the emission ships."""
    mem, init, play, size = image(row, label)
    model, _ev = S.decompile(mem, init, play, FRAMES, img=(ORG, ORG + size))
    return model, frameprog.loads(frameprog.dumps(frameprog.program(model)))


def text(row, label):
    """The emitted frame program of one variant."""
    return frameprog.dumps(built(row, label)[1])


def parsed(row, label):
    """The variant's text read back: the program the invariant is asked of."""
    return built(row, label)[1]


_CALLS = {
    "call": "call",
    "callb": "call",
    "dcall": "computed call",
    "swc": "switch call",
    "pcall": "pcall",
}


def _target(s):
    """How a call statement names its callee, for the finding it raises."""
    if s[0] in ("call", "callb", "pcall"):
        return "$%04X" % s[1]
    if s[0] == "swc":
        return "%d arm(s)" % (len(s[1]) + len(s[2]))
    return "(computed)"


def _sp_finds(s):
    """One statement's stack fabric as the text can state it: the ``sp`` names.

    Page one is not asked of the text -- no static predicate decides whether a lifted
    address reaches it -- so ``violations`` asks the evaluator, where it is exact."""
    out = []
    if s[0] == "asg" and s[1] == SP:
        out.append("sp: update")
    for x in frameproc._stmt_exprs(s):
        if SP in frameproc._locset(x):
            out.append("sp: read")
    return out


def _walk(stmts, last):
    out = []
    for i, s in enumerate(stmts):
        k = s[0]
        if k in _CALLS:
            out.append("%s: %s" % (_CALLS[k], _target(s)))
        elif k == "ret" and not (stmts is last and i == len(stmts) - 1):
            out.append("interior ret")
        out += _sp_finds(s)
        for b in frameproc._stmt_bodies(s):
            out += _walk(b, last)
    return out


@lru_cache(maxsize=None)
def fault(row, label):
    """The memory-protection fault the variant's own text raises under its own trace.

    The page-one half of the invariant: every address is concrete at evaluation, so
    ``frameval`` answers there what no static predicate decides."""
    model, _prog = built(row, label)
    trace, _walker = frameprog.iota(model, FRAMES)
    try:
        frameval.eval_fp(parsed(row, label), trace, FRAMES)
    except frameval.FrameFault as exc:
        return ("fault: %s" % exc,)
    return ()


def violations(row, label):
    """Every reason the variant is not one call-free procedure; ``()`` where it is one.

    Exactly one procedure, no call of any kind, no ``ret`` but the procedure's own
    last, no ``sp`` in the text and no access the protected regions fault on. A
    finding is ``class: detail``, so the class survives the addresses moving."""
    prog = parsed(row, label)
    out = list(fault(row, label))
    if len(prog.procs) != 1:
        out.append("procedures: %d" % len(prog.procs))
    for entry, params, rets, stmts in prog.procs:
        if SP in set(params) | set(rets):
            out.append("sp: parameter of sub_$%04X" % entry)
        out += _walk(stmts, stmts)
    return tuple(sorted(set(out)))


def kinds(row, label):
    """The classes of one variant's violations: each finding stripped of its detail."""
    return tuple(sorted({f.split(":")[0] for f in violations(row, label)}))


_PC_AT = {"label": 1, "goto": 1, "unobs": 1, "opsw": 1, "call": 2, "callb": 2, "dcall": 2}
_ARMS = {"swg": 1, "opsw": 2, "swc": 2}


def pcs_bound(stmts, out=None):
    """The pcs a statement list *defines* as a transfer target, arms included.

    A label is a hex pc in the grammar, so two copies of a body that binds one bind
    it twice and every transfer to it becomes ambiguous."""
    out = [] if out is None else out
    for s in stmts:
        if s[0] == "label":
            out.append(s[1])
        at = _ARMS.get(s[0])
        if at is not None:
            out += [b[0] for b in s[at] if isinstance(b[0], int)]
        if s[0] == "swc":
            out += [lbl for lbl in s[1] if isinstance(lbl, int)]
        for b in frameproc._stmt_bodies(s):
            pcs_bound(b, out)
    return sorted(set(out))


def pcs_named(stmts, out=None):
    """Every pc a statement list spells, the bound ones and the referenced ones."""
    out = [] if out is None else out
    for s in stmts:
        at = _PC_AT.get(s[0])
        if at is not None and isinstance(s[at], int):
            out.append(s[at])
        if s[0] == "igoto" and isinstance(s[1], int):
            out.append(s[1])
        if s[0] == "dbr" and isinstance(s[4], int):
            out.append(s[4])
        for b in frameproc._stmt_bodies(s):
            pcs_named(b, out)
    return sorted(set(out + pcs_bound(stmts)))
