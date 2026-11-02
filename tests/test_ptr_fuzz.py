"""Random programs that define, rewrite and read zero-page pointers stay exact.

Every program has a pointer at $20F8 (set on entry or left by the previous call) and, per input
bit, redefines its high byte (from the input, a table or arithmetic), overwrites it through an
index, or calls a subroutine that reads or writes through the pointer by ``(zp),Y`` and
``(zp,X)``. The facts see only the first call, which enables none of the blocks, so stores that
leave the pointer in the stack page and entries with a stale pointer must keep every access
exact.
"""

import random

import pytest

from test_wholeprog import differential, facts_for
from wholeprog_progs import IO, assemble
from deity_informant import NATIVE, wholeprog

pytestmark = pytest.mark.skipif(not NATIVE, reason="needs the native extension")

HI = 0xFC
SUBS = (0x1100, 0x1140)
TABLE = "hitab"


def _sub(rng, i):
    """A subroutine that reads or writes bytes through the pointer."""
    ops = []
    for n in range(rng.randint(4, 7)):
        how = rng.choice(
            ["LDA ($FB),Y", "STA ($FB),Y", "ORA ($FB),Y", "LDA ($FB,X)", "STA ($FB,X)"]
        )
        ops += ["        LDY #$%02X" % rng.randint(0, 8), "        LDX #$00", "        " + how]
        if how.startswith("LDA") or how.startswith("ORA"):
            ops.append("        STA $18%02X" % (i * 16 + n))
    return ["        .org $%04X" % SUBS[i], "sub%d:" % i] + ops + ["        RTS"]


def _block(rng, n):
    """One fragment of the entry routine."""
    kind = rng.choice(["input", "table", "bump", "arith", "index", "call", "call"])
    if kind == "input":
        return [
            "        LDA $F0",
            "        AND #$%02X" % rng.choice([1, 3, 7, 0x1F]),
            "        STA $%02X" % HI,
        ]
    if kind == "table":
        return [
            "        LDA $F0",
            "        AND #$03",
            "        TAX",
            "        LDA %s,X" % TABLE,
            "        STA $%02X" % HI,
        ]
    if kind == "bump":
        return ["        %s $%02X" % (rng.choice(["INC", "DEC"]), HI)]
    if kind == "arith":
        return [
            "        LDA $%02X" % HI,
            "        %s" % rng.choice(["CLC", "SEC"]),
            "        %s #$%02X" % (rng.choice(["ADC", "SBC"]), rng.randint(0, 3)),
            "        STA $%02X" % HI,
        ]
    if kind == "index":
        return [
            "        LDA $F0",
            "        AND #$0F",
            "        TAX",
            "        LDA %s,X" % TABLE,
            "        STA $F0,X",
        ]
    return [
        "        LDX #$%02X" % rng.randint(0, 3),
        "        JSR sub%d" % rng.randrange(len(SUBS)),
    ]


def program(seed):
    """The source of random program ``seed``, the inputs it is called with and the byte
    each call finds at ``$FC``."""
    rng = random.Random(seed)
    body = ["        STA $F0", "        LDA #$F8", "        STA $FB"]
    if rng.random() < 0.5:
        body += ["        LDA #$20", "        STA $%02X" % HI]
    for n in range(rng.randint(1, 4)):
        body += ["        LDA $F0", "        AND #$%02X" % (1 << n), "        BEQ skip%d" % n]
        body += _block(rng, n) + ["skip%d:" % n]
    body += ["        JSR sub0", "        RTS"]
    lines = ["entry:"] + body
    for i in range(len(SUBS)):
        lines += _sub(rng, i)
    lines += ["        .org $1180", TABLE + ": .byte $20, $01, $00, $02"]
    lines += ["        .org $%04X" % HI, "        .byte $20"]
    calls = [(0, 0)] + [(0, rng.choice([1, 2, 3, 4, 8, 5, 6, 7, 9, 0xF, 0x1F])) for _ in range(8)]
    return "\n".join(lines), calls, [rng.choice([0, 1, 2, 0x20]) for _ in calls]


@pytest.mark.parametrize("cycles", [False, True])
# Seeds past 60: a deopt at a pointer access left a register the instruction reads unset.
@pytest.mark.parametrize("seed", [*range(60), 2, 17, 27, 103, 165, 192, 193, 265])
def test_random_pointer_programs_are_exact(seed, cycles):
    src, calls, hi = program(seed)
    img, labels = assemble(src)
    pcs = [labels["entry"]]
    prog = wholeprog.lift(img, pcs, [IO], facts_for(img, pcs, calls[:1]), cycles=cycles)
    for call, start in zip(calls, hi):
        mem = bytearray(img)
        mem[HI] = start
        differential(img, prog, pcs, [call], mem=mem)
