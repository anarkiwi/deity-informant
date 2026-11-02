"""Random programs that define, rewrite and read zero-page pointers stay exact.

Every program has a pointer at $FB/$FC (set on entry to $20F8 or a zero-page target, or left by
the previous call) and, per input bit, redefines its high or low byte (from the input, a table or
arithmetic), both from a pair of tables, overwrites a byte through an index, or calls a
subroutine that reads or writes through the pointer by ``(zp),Y`` and ``(zp,X)``. The facts see
only the first call, which enables none of the blocks, so stores that move the pointer into
the window of its indices and entries with a stale pointer must keep every access exact.
"""

import random

import pytest

from test_wholeprog import differential, facts_for
from wholeprog_progs import IO, assemble
from deity_informant import NATIVE, wholeprog

pytestmark = pytest.mark.skipif(not NATIVE, reason="needs the native extension")

LO, HI = 0xFB, 0xFC
SUBS = (0x1100, 0x1140)
TABLE = "hitab"
# Pairs: in page $20, zero-page targets (past $FF for large Y), the stack page.
PAIRS = (
    (0xF8, 0x20),
    (0x10, 0x00),
    (0x02, 0x00),
    (0xFC, 0x00),
    (0xF8, 0x01),
    (0xFE, 0x00),
    (0x00, 0x01),
    (0xF4, 0x20),
)


def _sub(rng, i):
    """A subroutine that reads or writes bytes through the pointer."""
    ops = []
    for n in range(rng.randint(4, 7)):
        how = rng.choice(
            ["LDA ($FB),Y", "STA ($FB),Y", "ORA ($FB),Y", "LDA ($FB,X)", "STA ($FB,X)"]
        )
        y = rng.randint(0, 8) if rng.random() < 0.75 else rng.randint(0xF9, 0xFF)
        ops += ["        LDY #$%02X" % y, "        LDX #$00", "        " + how]
        if how.startswith("LDA") or how.startswith("ORA"):
            ops.append("        STA $18%02X" % (i * 16 + n))
    return ["        .org $%04X" % SUBS[i], "sub%d:" % i] + ops + ["        RTS"]


def _block(rng, n):
    """One fragment of the entry routine."""
    kind = rng.choice(["input", "low", "table", "pair", "bump", "arith", "index", "call", "call"])
    if kind == "input":
        return [
            "        LDA $F0",
            "        AND #$%02X" % rng.choice([1, 3, 7, 0x1F]),
            "        STA $%02X" % HI,
        ]
    if kind == "low":
        return [
            "        LDA $F0",
            "        ORA #$%02X" % rng.choice([0x10, 0xF0, 0xF8]),
            "        STA $%02X" % LO,
        ]
    if kind == "pair":
        return [
            "        LDA $F0",
            "        AND #$07",
            "        TAX",
            "        LDA lotab,X",
            "        STA $%02X" % LO,
            "        LDA %s,X" % TABLE,
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
    """The source of random program ``seed``, the inputs it is called with and the bytes
    each call finds at ``$FB`` and ``$FC``."""
    rng = random.Random(seed)
    lo, hi = rng.choice(PAIRS[:3])
    # The input pushed stays live below the return address while the subroutines run.
    push = rng.random() < 0.5
    body = ["        STA $F0"] + (["        PHA"] if push else [])
    body += ["        LDA #$%02X" % lo, "        STA $FB"]
    if rng.random() < 0.5:
        body += ["        LDA #$%02X" % hi, "        STA $%02X" % HI]
    for n in range(rng.randint(1, 4)):
        body += ["        LDA $F0", "        AND #$%02X" % (1 << n), "        BEQ skip%d" % n]
        body += _block(rng, n) + ["skip%d:" % n]
    body += ["        JSR sub0"] + (["        PLA", "        STA $181F"] if push else [])
    body += ["        RTS"]
    lines = ["entry:"] + body
    for i in range(len(SUBS)):
        lines += _sub(rng, i)
    lines += ["        .org $1180", TABLE + ": .byte " + ", ".join("$%02X" % p[1] for p in PAIRS)]
    lines += ["lotab: .byte " + ", ".join("$%02X" % p[0] for p in PAIRS)]
    lines += ["        .org $%04X" % HI, "        .byte $%02X" % hi]
    calls = [(0, 0)] + [(0, rng.choice([1, 2, 3, 4, 8, 5, 6, 7, 9, 0xF, 0x1F])) for _ in range(8)]
    starts = [(rng.choice([lo, 0xF8, 0xFC]), rng.choice([0, 1, 2, 0x20, hi])) for _ in calls]
    return "\n".join(lines), calls, starts


@pytest.mark.parametrize("cycles", [False, True])
# Seeds past 60: a deopt at a pointer access left a register the instruction reads unset; a
# code check's deopt handed over only the registers the unchanged code reads (8342); windows
# of index 0 (159, 352) and a pair checked after its first store only (930, 1444, 2031).
@pytest.mark.parametrize(
    "seed", [*range(60), 2, 17, 27, 103, 165, 192, 193, 265, 8342, 159, 352, 930, 1444, 2031]
)
def test_random_pointer_programs_are_exact(seed, cycles):
    src, calls, hi = program(seed)
    img, labels = assemble(src)
    pcs = [labels["entry"]]
    prog = wholeprog.lift(img, pcs, [IO], facts_for(img, pcs, calls[:1]), cycles=cycles)
    for call, start in zip(calls, hi):
        mem = bytearray(img)
        mem[LO], mem[HI] = start
        differential(img, prog, pcs, [call], mem=mem)
