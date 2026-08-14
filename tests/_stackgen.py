"""Drivers for the two refusals docs/denotation-solve.md 8.4 names in advance.

Class 1 is a self-call not in tail position; class 2 an RTS dispatch under the
three closures of its target set. The shapes are built in ``_callgen``'s form and
registered in its ``BY_ROW``, the only registry its build path reads.
"""

from __future__ import annotations

import itertools
import re

import _callgen as G
import _idiomgen as I
from deity_informant.lifter import lift
from deity_informant.vm import PcodeVM

_P1 = re.compile(r"\$01[0-9A-F]{2}")
_TGT = re.compile(r"^ targets \$([0-9A-F]{4}): (.+)$", re.M)

SID, ROW, CNT = I.SID, I.ROW, I.CNT
OSC3 = 0xD41B  # the volatile input: its value is not in the image
VSTK = 0xE0  # a value stack the driver declares in page zero, never page one
STRIDE = 16  # each open-dispatch arm is padded to a power-of-two displacement
_ARM = 9  # LDX zp, LDA abs,X, STA abs, RTS

V, bump, col = G.V, G.bump, G.col


def _seed(p, depth):
    """``cnt`` = the recursion's depth, from a constant or from the frame cursor."""
    if depth == "const":
        p.i("LDA", "imm", 3)
    else:
        p.i("LDA", "zp", ROW).i("AND", "imm", 3)
    p.i("STA", "zp", CNT)


def _mutual():
    """`mutual-recursion`: A -> B -> A, each with work after its own call."""

    def subs(p):
        for name, other, tab, reg in (("mra", "mrb", "tone", SID), ("mrb", "mra", "alt", SID + 1)):
            p.label(name).i("LDA", "zp", CNT).i("BEQ", "rel", ("L", name + "x"))
            p.i("DEC", "zp", CNT).i("JSR", "abs", ("L", other))
            p.i("INC", "zp", CNT).i("LDX", "zp", CNT)
            col(p, "X", tab)
            p.i("STA", "abs", reg)
            p.label(name + "x").i("RTS")

    def play(p, depth, first):
        _seed(p, depth)
        p.i("JSR", "abs", ("L", "mr" + first))
        bump(p)

    return I.cap(
        V("%s/%s" % (d, f), lambda p, a=d, b=f: play(p, a, b), subs)
        for d, f in itertools.product(("const", "row"), ("a", "b"))
    )


def _saved():
    """`saved-recursion`: a live value carried across the self-call.

    ``pha`` parks it on the machine stack, ``zp`` in an array the driver indexes
    by the depth counter -- the same recursion with and without page one."""

    def sub(p, how):
        p.label("srec").i("LDA", "zp", CNT).i("BEQ", "rel", ("L", "srx"))
        p.i("LDX", "zp", CNT)
        col(p, "X")
        if how == "pha":
            p.i("PHA")
        else:
            p.i("STA", "zpx", VSTK)
        p.i("DEC", "zp", CNT).i("JSR", "abs", ("L", "srec")).i("INC", "zp", CNT)
        if how == "pha":
            p.i("PLA")
        else:
            p.i("LDX", "zp", CNT).i("LDA", "zpx", VSTK)
        p.i("STA", "abs", SID)
        p.label("srx").i("RTS")

    def play(p, depth):
        _seed(p, depth)
        p.i("JSR", "abs", ("L", "srec"))
        bump(p)

    return I.cap(
        V("%s/%s" % (h, d), lambda p, a=d: play(p, a), lambda p, b=h: sub(p, b))
        for h, d in itertools.product(("pha", "zp"), ("const", "row"))
    )


def _slot(p, kind, k, store):
    """Save (``store``) or recover the ``k``th carried byte, in page one or page zero."""
    if kind == "pha":
        p.i("PHA" if store else "PLA")
        return
    p.i("LDX", "zp", CNT)
    p.i("STA" if store else "LDA", "zpx", VSTK - 0x10 * k)


def _carry():
    """`carry-recursion`: a byte across the self-call that restored state cannot re-derive.

    ``volatile`` re-reads differently, ``overwrite`` has its source overwritten by the
    call and ``lossy`` has it reduced by an AND; ``multi`` carries two at once. The
    descent writes the SID, so no sound removal may re-run it to recover them."""

    def produce(p, what):
        if what in ("volatile", "multi"):
            p.i("LDA", "abs", OSC3)
        else:
            p.i("LDA", "zp", I.TMP if what == "overwrite" else I.FLG)

    def destroy(p, what):
        if what == "multi":
            return
        p.i("LDX", "zp", CNT)
        col(p, "X")
        if what == "overwrite":
            p.i("STA", "zp", I.TMP)
        elif what == "lossy":
            p.i("AND", "zp", I.FLG).i("STA", "zp", I.FLG)
        p.i("STA", "abs", SID + 2)

    def sub(p, what, kind):
        p.label("crec").i("LDA", "zp", CNT).i("BEQ", "rel", ("L", "crx"))
        produce(p, what)
        _slot(p, kind, 0, True)
        destroy(p, what)
        if what == "multi":
            produce(p, what)
            _slot(p, kind, 1, True)
        p.i("DEC", "zp", CNT).i("JSR", "abs", ("L", "crec")).i("INC", "zp", CNT)
        if what == "multi":
            _slot(p, kind, 1, False)
            p.i("STA", "abs", SID + 1)
        _slot(p, kind, 0, False)
        p.i("STA", "abs", SID)
        p.label("crx").i("RTS")

    def play(p, what):
        p.i("LDA", "imm", 3).i("STA", "zp", CNT)
        if what == "lossy":
            p.i("LDA", "zp", ROW).i("ORA", "imm", 0xF0).i("STA", "zp", I.FLG)
        p.i("JSR", "abs", ("L", "crec"))
        bump(p)

    return I.cap(
        V("%s/%s" % (w, k), lambda p, a=w: play(p, a), lambda p, a=w, b=k: sub(p, a, b))
        for w, k in itertools.product(("volatile", "overwrite", "lossy", "multi"), ("pha", "zp"))
    )


def _input_rec():
    """`input-recursion`: the depth is a volatile read, so no trace closes it."""

    def sub(p, around):
        p.label("irec").i("LDA", "zp", CNT).i("BEQ", "rel", ("L", "irx"))
        if around:
            p.i("LDX", "zp", CNT)
            col(p, "X")
            p.i("STA", "abs", SID)
        p.i("DEC", "zp", CNT).i("JSR", "abs", ("L", "irec"))
        p.i("INC", "zp", CNT).i("LDX", "zp", CNT)
        col(p, "X", "alt")
        p.i("STA", "abs", SID + 1)
        p.label("irx").i("RTS")

    def play(p, mask):
        p.i("LDA", "abs", OSC3).i("AND", "imm", mask).i("STA", "zp", CNT)
        p.i("JSR", "abs", ("L", "irec"))
        bump(p)

    return I.cap(
        V(
            "%d/%s" % (m, "around" if a else "after"),
            lambda p, x=m: play(p, x),
            lambda p, y=a: sub(p, y),
        )
        for m, a in itertools.product((1, 3), (0, 1))
    )


def _arms(p):
    """Four landings at a fixed displacement, so an arithmetic target can name one."""
    for k in range(4):
        p.label("sa%d" % k).i("LDX", "zp", ROW)
        col(p, "X", "tone" if k % 2 == 0 else "alt")
        p.i("STA", "abs", SID + k).i("RTS")
        for _ in range(STRIDE - _ARM):
            p.i("NOP")


def _open_data(p):
    """The declared lo/hi columns holding ``arm - 1``: what an RTS dispatch pushes."""
    I.tab_data(p)
    p.label("oplo").byte(*[("LOL", "sa%d" % k, -1) for k in range(4)])
    p.label("ophi").byte(*[("HIL", "sa%d" % k, -1) for k in range(4)])


_SELECT = {
    "closed": (ROW, 3, 0),  # the cursor over four arms: the trace sees every one
    "partial": (ROW, 1, 1),  # two of four; the other two are in the image, unrun
    "input": (OSC3, 1, 0),  # a selector no trace closes, over a table that does
    "wide": (OSC3, 3, 0),
}


def _open():
    """`open-dispatch`: one RTS dispatch, its target set closed five ways."""

    def lo(p):
        """``a = <(sa0 - 1) + STRIDE * osc3.bit0``: a target no table in the image names."""
        p.i("LDA", "abs", OSC3).i("AND", "imm", 1)
        for _ in range(STRIDE.bit_length() - 1):
            p.i("ASL", "acc")
        p.i("CLC").i("ADC", "imm", ("LOL", "sa0", -1))

    def play(p, how):
        bump(p)
        if how == "computed":
            p.i("LDA", "imm", ("HIL", "sa0", -1)).i("PHA")
            lo(p)
            p.i("PHA")
        elif how == "roundtrip":
            lo(p)
            p.i("TAX").i("LDA", "imm", ("HIL", "sa0", -1)).i("PHA")
            p.i("TXA").i("PHA")
        else:
            src, mask, shift = _SELECT[how]
            p.i("LDA", "zp" if src == ROW else "abs", src).i("AND", "imm", mask)
            for _ in range(shift):
                p.i("ASL", "acc")
            p.i("TAX")
            p.i("LDA", "absx", ("L", "ophi")).i("PHA")
            p.i("LDA", "absx", ("L", "oplo")).i("PHA")
        p.i("RTS")

    spells = tuple(_SELECT) + ("computed", "roundtrip")
    return I.cap(V(s, lambda p, h=s: play(p, h), _arms) for s in spells)


SHAPES = (
    G.Shape("mutual-recursion", "A -> B -> A, work after each", I.tab_data, _mutual()),
    G.Shape("saved-recursion", "a re-derivable value across the call", I.tab_data, _saved()),
    G.Shape("carry-recursion", "a value restored state cannot re-derive", I.tab_data, _carry()),
    G.Shape("input-recursion", "a depth read from a volatile input", I.tab_data, _input_rec()),
    G.Shape("open-dispatch", "an RTS dispatch, three closures", _open_data, _open()),
)

for _s in SHAPES:
    G.BY_ROW.setdefault(_s.row, _s)

ALL = tuple((s.row, v.label) for s in SHAPES for v in s.variants)
BY_ROW = {s.row: s for s in SHAPES}


def labels(row, label):
    """The assembled label table of one variant."""
    asm, _var = G._asm(row, label)
    asm.assemble()
    return dict(asm.labels)


def _run(vm, entry, cache, watch=None):
    """Run one subroutine to its balancing RTS; count the entries to ``watch``."""
    reg, n = vm.reg, 0
    start = reg[3]
    vm._push(0x00)
    vm._push(0x01)
    pc = entry
    while reg[3] < start:
        n += pc == watch
        pc = vm.step(pc, cache, lift)
    return n


def activations(row, label, name):
    """Per-frame entries to ``name``: the activation depth the trace observes."""
    mem, init, play, _size = G.image(row, label)
    pc = labels(row, label)[name]
    vm, cache = PcodeVM(bytearray(mem)), {}
    _run(vm, init, cache)
    return tuple(_run(vm, play, cache, pc) for _ in range(G.FRAMES))


def bodies(row, label):
    """The emitted procedures alone: the text from its first ``sub_`` line on."""
    txt = G.text(row, label)
    return txt[txt.index("\nsub_") + 1 :]


def page_one(row, label):
    """Every ``$0100..$01FF`` address the emitted procedures name."""
    return tuple(sorted({int(a[1:], 16) for a in _P1.findall(bodies(row, label))}))


def targets(row, label):
    """The observed target set the artifact's evidence channel states, per site."""
    return {
        int(pc, 16): tuple(int(t[1:], 16) for t in rest.split())
        for pc, rest in _TGT.findall(G.text(row, label))
    }
