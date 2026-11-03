"""Random programs whose indexed stores may reach their own subroutines stay exact.

Each program indexes a table that lies below three subroutines by loop counters, inputs and
registers carried through calls. Input bit `n` enables block `n`; the lift's facts see only the
first call, which enables none, so a store the analysis cannot place outside the subroutines
must keep its code guard for the later calls to be exact.
"""

import random

import pytest

from test_wholeprog import differential, facts_for
from wholeprog_progs import IO, assemble
from deity_informant import NATIVE, wholeprog

pytestmark = pytest.mark.skipif(not NATIVE, reason="needs the native extension")

TABLE = 0x10E0
SUBS = (0x1100, 0x1120, 0x1140)
# Stored bytes may become opcodes the program runs. Those that set SP (TXS, TAS, LAS) expose
# stack bytes below SP, which a lifted private stack does not hold (docs/wholeprog.md,
# Limitations), so the generator stores a neighbouring opcode instead.
NOT_SP = {0x9A: 0x99, 0x9B: 0x9C, 0xBB: 0xBC}


def _byte(rng):
    """A random nonzero byte that is not an SP-setting opcode."""
    v = rng.randint(1, 255)
    return NOT_SP.get(v, v)


def _sub(rng, i):
    """A subroutine at ``SUBS[i]`` that keeps, clobbers or saves and restores ``X``."""
    lines = ["        LDA #$%02X" % _byte(rng), "        STA $C0%02X" % i]
    if i + 1 < len(SUBS) and rng.random() < 0.5:
        lines.append("        JSR sub%d" % (i + 1))
    how = rng.choice(["keep", "clobber", "save"])
    if how == "clobber":
        lines.append("        LDX #$%02X" % rng.randint(0, 4))
    elif how == "save":
        lines = ["        TXA", "        PHA"] + lines + ["        PLA", "        TAX"]
    return ["        .org $%04X" % SUBS[i], "sub%d:" % i] + lines + ["        RTS"]


def _block(rng, n):
    """One random fragment of the entry routine."""
    kind = rng.choice(["down", "up", "input", "call", "rmw", "bcd"])
    index = rng.choice("XY")
    value = _byte(rng)
    if kind == "down":
        return [
            "        LDX #$%02X" % rng.choice([3, 7, 0x1F, 0x3F, 0x5F]),
            "d%d:     LDA #$%02X" % (n, value),
            "        STA tbl,X",
            "        DEX",
            "        BPL d%d" % n,
        ]
    if kind == "up":
        return [
            "        LDX #$00",
            "u%d:     LDA #$%02X" % (n, value),
            "        STA tbl,X",
            "        INX",
            "        CPX #$%02X" % rng.choice([4, 0x10, 0x20, 0x21, 0x41]),
            "        BNE u%d" % n,
        ]
    if kind == "input":
        return [
            "        LDA $F0",
            "        TA%s" % index,
            "        LDA #$%02X" % value,
            "        STA tbl,%s" % index,
        ]
    if kind == "call":
        return [
            "        LDX #$%02X" % rng.randint(0, 2),
            "        JSR sub%d" % rng.randrange(len(SUBS)),
            "        STA tbl+%d,X" % rng.choice([0, 0x20, 0x21, 0x40]),
        ]
    if kind == "bcd":
        return (
            [
                "        SED",
                "        LDA $F0",
                "        %s" % rng.choice(["CLC", "SEC"]),
                "        %s #$%02X" % (rng.choice(["ADC", "SBC"]), rng.randrange(256)),
                "        AND #$03",
                "        T%s" % ("AX" if index == "X" else "AY"),
            ]
            + ["        CLD"] * (rng.random() < 0.8)
            + ["        LDA #$%02X" % value, "        STA tbl,%s" % index]
        )
    return [
        "        LDX #$%02X" % rng.randint(0, 3),
        "        INC tbl+%d,X" % rng.choice([0, 0x20]),
    ]


def program(seed):
    """The source of random program ``seed`` and the inputs it is called with."""
    rng = random.Random(seed)
    body = ["        STA $F0"]
    for n in range(rng.randint(1, 4)):
        body += ["        LDA $F0", "        AND #$%02X" % (1 << n), "        BEQ skip%d" % n]
        body += _block(rng, n) + ["skip%d:" % n]
    body += ["        JSR sub0", "        JSR sub%d" % rng.randrange(len(SUBS)), "        RTS"]
    lines = ["entry:"] + body + [".org $%04X" % TABLE, "tbl:    .byte 0"]
    for i in range(len(SUBS)):
        lines += _sub(rng, i)
    calls = [(0, 0)] + [(0, rng.choice([1, 2, 3, 4, 8, 0x20, 0x21, 0x22, 0x41])) for _ in range(6)]
    return "\n".join(lines), calls


@pytest.mark.parametrize("cycles", [False, True])
# 4813: the reference stores into constant code after a deopt at an opcode cell; the next
# call must re-validate the lifted code.
@pytest.mark.parametrize("seed", [*range(60), 4813, 4875, 5978])
def test_random_programs_are_exact(seed, cycles):
    src, calls = program(seed)
    img, labels = assemble(src)
    pcs = [labels["entry"]]
    prog = wholeprog.lift(img, pcs, [IO], facts_for(img, pcs, calls[:1]), cycles=cycles)
    differential(img, prog, pcs, calls)
