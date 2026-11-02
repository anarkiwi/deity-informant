"""Random multi-byte arithmetic stays exact whether or not its carry chains are fused.

Operations two or three bytes wide on adjacent, indexed, split or self-overlapping words, with
carries live between bytes (pushed flags, carry branches) or after them, in or out of decimal
mode; every call starts from new random words, some with the decimal flag set at entry.
"""

import random

import pytest

from test_wholeprog import differential, facts_for
from wholeprog_progs import IO, assemble
from deity_informant import NATIVE, wholeprog

pytestmark = pytest.mark.skipif(not NATIVE, reason="needs the native extension")

DATA = (0x20, 0x60)
TAB = 0x1820
# X from memory: indexed addresses are unknown to the lifter, so aliasing is not resolved.
LDX = ["        LDA $1E", "        AND #$03", "        TAX"]
FLAGS = 0x10


def _addr(rng, n):
    """Operand byte addresses, least significant first, a shifted copy of them (a destination
    overlapping the operand) and an index register load."""
    how = rng.choice(["zp", "abs", "idx", "stride", "split"])
    base = rng.randrange(DATA[0] + 1, DATA[1] - 2 * n)
    fmt, step, pre = {
        "zp": ("$%02X", 1, []),
        "abs": ("$%04X", 1, []),
        "idx": ("$%04X,X", 1, LDX),
        "stride": ("$%04X,X", 2, LDX),
        "split": ("$%04X,X", 8, LDX),
    }[how]
    if how != "zp":
        base = TAB + rng.randrange(-1, 12) if how == "abs" else TAB
    d = rng.choice([1, -1])
    return (
        [fmt % (base + step * i) for i in range(n)],
        [fmt % (base + step * i + d) for i in range(n)],
        pre,
    )


def _live(rng, k):
    """Code between links that may read the carry and flags left so far."""
    return rng.choice(
        [
            [],
            [],
            ["        PHP", "        PLA", "        STA $%02X" % (FLAGS + k % 15)],
            ["        BCC c%d" % k, "        INC $%02X" % (FLAGS + k % 15), "c%d:" % k],
            ["        BNE c%d" % k, "        INC $%02X" % (FLAGS + k % 15), "c%d:" % k],
            ["        LDA $%04X,X" % (TAB + rng.randrange(-1, 12)), "        STA $1D"],
            ["        LDA $%04X,X" % (TAB + rng.randrange(-1, 12)), "        STA $1D"],
        ]
    )


def _op(rng, k):
    """One multi-byte operation numbered ``k``."""
    n = rng.choice([2, 2, 3])
    a, ashift, pre = _addr(rng, n)
    b, _, pre2 = _addr(rng, n)
    c = rng.choice([a, a, b, ashift])
    lines = pre + (pre2 if not pre else [])
    kind = rng.choice(["adc", "sbc", "cmp", "inc", "dec", "adci", "sbci", "asl", "lsr"])

    def live(i):
        return _live(rng, 8 * k + i) if rng.random() < 0.5 else []

    if kind in ("adc", "sbc", "cmp"):
        lines.append("        " + ("CLC" if kind == "adc" else "SEC"))
        for i in range(n):
            op = {"adc": "ADC", "sbc": "SBC", "cmp": "CMP" if i == 0 else "SBC"}[kind]
            lines += ["        LDA %s" % a[i], "        %s %s" % (op, b[i])]
            if kind != "cmp":
                lines.append("        STA %s" % c[i])
            if i < n - 1:
                lines += live(i)
    elif kind in ("adci", "sbci"):
        add = kind == "adci"
        lines += [
            "        LDA %s" % a[0],
            "        " + ("CLC" if add else "SEC"),
            "        %s #$%02X" % ("ADC" if add else "SBC", rng.randrange(256)),
            "        STA %s" % a[0],
            "        %s s%d" % ("BCC" if add else "BCS", k),
            "        %s %s" % ("INC" if add else "DEC", a[1]),
            "s%d:" % k,
        ]
    elif kind == "inc":
        lines += [
            "        INC %s" % a[0],
            "        BNE s%d" % k,
            "        INC %s" % a[1],
            "s%d:" % k,
        ]
    elif kind == "dec":
        lines += [
            "        LDA %s" % a[0],
            "        BNE s%d" % k,
            "        DEC %s" % a[1],
            "s%d:     DEC %s" % (k, a[0]),
        ]
    else:
        order = list(range(n)) if kind == "asl" else list(range(n - 1, -1, -1))
        for j, i in enumerate(order):
            shift = ("ASL", "ROL") if kind == "asl" else ("LSR", "ROR")
            lines.append("        %s %s" % (shift[j > 0], a[i]))
            if j > 0:
                lines += live(i)
    return lines + _live(rng, 8 * k + 7)


def program(seed):
    """The source of random program ``seed`` and its calls as ``(a, d, words)``."""
    rng = random.Random(seed)
    body = ["        SED"] if rng.random() < 0.2 else []
    for k in range(rng.randint(1, 4)):
        body += _op(rng, k)
        if rng.random() < 0.1:
            body.append("        " + rng.choice(["SED", "CLD"]))
    body += ["        CLD", "        PHP", "        PLA", "        STA $1F", "        RTS"]
    calls = [
        (
            rng.randrange(256),
            int(rng.random() < 0.15),
            bytes(rng.randrange(256) for _ in range(0x60)),
        )
        for _ in range(6)
    ]
    return "\n".join(["entry:"] + body), calls


def _mem(img, words):
    mem = bytearray(img)
    mem[DATA[0] : DATA[1]] = words[: DATA[1] - DATA[0]]
    mem[0x1800 + DATA[0] : 0x1800 + DATA[1]] = words[: DATA[1] - DATA[0]]
    mem[TAB : TAB + 0x20] = words[0x40:]
    return mem


@pytest.mark.parametrize("cycles", [False, True])
# Seeds past 80 fail when the fusion's alias, store-order or liveness checks are dropped, or a
# merged join loses the temporaries its successors read.
@pytest.mark.parametrize("seed", [*range(80), 745, 1491, 2551, 4236, 2054, 292, 334])
def test_random_carry_programs_are_exact(seed, cycles):
    src, calls = program(seed)
    img, labels = assemble(src)
    pcs = [labels["entry"]]
    first = _mem(img, calls[0][2])
    prog = wholeprog.lift(
        first, pcs, [IO], facts_for(first, pcs, [(0, calls[0][0])]), cycles=cycles
    )
    if src.startswith("entry:\n        SED") and "CLD" not in src[: src.rindex("CLD")]:
        assert prog.stats["fused_chains"] == 0
    for a, d, words in calls:
        reg = [a, 0, 0, 0xFF, 0, 0, 0, 0, 0, 0, 0, d, 0, 0, 0, 0]
        differential(img, prog, pcs, [(0, a)], mem=_mem(img, words), reg=reg)


def test_fuses_adjacent_words():
    src = """
entry:  CLC
        LDA $20
        ADC $30
        STA $20
        LDA $21
        ADC $31
        STA $21
        INC $40
        BNE s
        INC $41
s:      ASL $50
        ROL $51
        LDA $1820,X
        CLC
        ADC #$10
        STA $1820,X
        LDA $1828,X
        ADC #$00
        STA $1828,X
        RTS
"""
    img, labels = assemble(src)
    pcs = [labels["entry"]]
    prog = wholeprog.lift(img, pcs, [IO], facts_for(img, pcs, [(0, 0)]))
    chains = prog.chains
    assert prog.stats["chains"] == len(chains) == 4
    assert prog.stats["fused_chains"] == 3
    fused = {(c["kind"], tuple(tuple(p) for p in c["x"])) for c in chains if c["fused"]}
    assert ("add", (("abs", 0x20), ("abs", 0x21))) in fused
    assert ("add", (("abs", 0x40), ("abs", 0x41))) in fused
    split = [c for c in chains if not c["fused"]][0]
    assert [tuple(p) for p in split["x"]] == [("x", 0x1820), ("x", 0x1828)]
    for a in (0, 0xFF):
        mem = bytearray(img)
        mem[0x20:0x60] = bytes([a] * 0x40)
        differential(img, prog, pcs, [(0, 0)], mem=mem)
