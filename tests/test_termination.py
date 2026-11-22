"""Lifts end: programs with stores over their own operand cells lift within a wall-clock limit
and replay against the reference interpreter (``docs/wholeprog.md``, *Termination*)."""

import pytest

import wholeprog_progs as W
from test_valuesets import lift_and_replay
from deity_informant import NATIVE

ProcessPool = pytest.importorskip("pebble").ProcessPool

pytestmark = pytest.mark.skipif(not NATIVE, reason="needs the native extension")

LIMIT = 60

# `STA q,X` with X up to 15 writes its own operand bytes, with a value read from I/O.
SELF = """
entry:  LDA $C000
        AND #$0F
        TAX
        LDA $C001
q:      STA q,X
        RTS
end:
"""

# The callee returns to the byte after the `JSR`: its argument bytes run as `STA arg,X`.
RETDATA = """
entry:  LDA $C000
        AND #$0F
        TAX
        LDA $C001
        JSR ret
arg:    .byte $9D, lo(arg), hi(arg)
        RTS
ret:    RTS
end:
"""

# An inline argument read through the pulled return address; from the return site its byte
# decodes with the next instruction as `AND #$BD; JSR $1818`, onto `ISC $1812,X`.
INLINE = """
entry:  LDX #14
next:   JSR body
        TXA
        SEC
        SBC #7
        TAX
        BPL next
        RTS
body:   LDA $1800,X
        STA $C000,X
        LDA $1810,X
        CLC
        ADC #$3F
        STA $1810,X
        LDA $1820,X
        AND #$10
        BEQ skip
        JSR arg
        .byte $29
        LDA $1820,X
        CLC
        ADC #$07
        STA $1800,X
skip:   LDY #$02
count:  LDA $1800,X
        CLC
        ADC #$01
        STA $1800,X
        DEY
        BPL count
        LDA $1820,X
        STA $C001,X
        RTS
arg:    PLA
        STA $F0
        PLA
        STA $F1
        LDY #1
        LDA ($F0),Y
        CLC
        ADC $1810,X
        STA $1810,X
        LDA $F0
        CLC
        ADC #1
        TAY
        LDA $F1
        ADC #0
        PHA
        TYA
        PHA
        RTS
end:
        .org $1800
        .byte $5B,0,0,0,0,0,0,$9C,0,0,0,0,0,0,$EC,0
        .byte $B6,0,0,0,0,0,0,$AB,$FF,$12,$18,0,0,0,$9E,0
        .byte $D6,0,0,0,0,0,0,$6E,0,0,0,0,0,0,$BD,0
"""

# A branch to itself on a clear N: the call never returns.
SPIN = """
entry:  LDA $C000
        AND #$01
        BEQ spin
        RTS
spin:   BPL spin
        RTS
end:
"""


@pytest.mark.parametrize(
    "src,calls,exact",
    [
        (SELF, [(0, 0)] * 6, False),
        (RETDATA, [(0, 0)] * 6, False),
        (INLINE, [(0, 0)] * 6, True),
        (SPIN, [(0, 0)] * 2, True),
        (W.INLINE_ARGS, [(0, 0)] * 4, True),
        (W.RET_UNKNOWN, [(0, 0)] * 8, False),
        (W.ARG_WRITTEN, [(0, 0)] * 4, True),
        (W.ARG_DEOPT, [(0, 0)] * 8, False),
        (W.PUSH_RET, [(0, 0)] * 2, True),
        (W.BRK_SIG, [(0, 0)] * 4, True),
        (W.BRK_SIG1, [(0, 0)] * 4, True),
        (W.RTI_TABLE, [(0, a) for a in range(4)], True),
    ],
    ids=[
        "self",
        "retdata",
        "inline",
        "spin",
        "inline-args",
        "ret-unknown",
        "arg-written",
        "arg-deopt",
        "push-ret",
        "brk-sig",
        "brk-sig1",
        "rti-table",
    ],
)
def test_lift_ends_and_replays(src, calls, exact):
    with ProcessPool(max_workers=1) as pool:
        deopts = pool.schedule(lift_and_replay, (src, calls), timeout=LIMIT).result()
    assert (not deopts) == exact
