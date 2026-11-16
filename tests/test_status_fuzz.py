"""Random status and data bytes through the same stack slots stay exact.

Pushes and pulls of the status and of data among flag-setting, transfer and store instructions,
in straight lines, loops, a subroutine and a ``BRK`` handler (``docs/wholeprog.md``, *Status
lanes*); ``tools/fuzzsweep.py --module test_status_fuzz`` sweeps more seeds.
"""

import random

import pytest

from test_wholeprog import differential, facts_for
from wholeprog_progs import IO, assemble
from deity_informant import NATIVE, wholeprog

pytestmark = pytest.mark.skipif(not NATIVE, reason="needs the native extension")

FLAGS = ["SEC", "CLC", "SEI", "CLI", "CLV", "SED", "CLD"]
SETS = ["CMP", "LDA", "ADC", "SBC", "AND", "ORA", "EOR", "LDX", "LDY", "CPX", "CPY"]
MOVES = ["TAY", "TAX", "TYA", "TXA", "INX", "DEY", "INY", "DEX", "ASL A", "ROR A"]
STORES = ["STA", "STX", "STY"]
BRANCHES = ["BCC", "BCS", "BNE", "BEQ", "BMI", "BPL", "BVC", "BVS"]
DEPTH = 6


class _Gen:
    """Instructions drawn from ``rng`` with the stack depth they leave."""

    def __init__(self, rng, tag):
        self.rng, self.tag, self.depth, self.labels = rng, tag, 0, 0

    def plain(self):
        """One instruction that leaves the stack as it is."""
        r = self.rng
        kind = r.choice(["flag", "set", "set", "move", "move", "store"])
        if kind == "flag":
            return r.choice(FLAGS)
        if kind == "set":
            return "%s #$%02X" % (r.choice(SETS), r.randrange(256))
        if kind == "move":
            return r.choice(MOVES)
        if r.random() < 0.5:
            return "%s $C0%02X" % (r.choice(STORES), r.randrange(256))
        return "%s $%02X" % (r.choice(STORES), r.randrange(0x20, 0x30))

    def stack(self):
        """A push, or a pull of a byte pushed in this sequence."""
        r = self.rng
        if self.depth and (self.depth >= DEPTH or r.random() < 0.5):
            self.depth -= 1
            return r.choice(["PLA", "PLP"])
        self.depth += 1
        return r.choice(["PHA", "PHP"])

    def body(self, n):
        """``n`` steps, pulling what they pushed at the end."""
        out = []
        for _ in range(n):
            x = self.rng.random()
            if x < 0.45:
                out.append(self.stack())
            elif x < 0.55:
                self.labels += 1
                lab = "%s%d" % (self.tag, self.labels)
                out += ["%s %s" % (self.rng.choice(BRANCHES), lab), self.plain(), lab + ":"]
            else:
                out.append(self.plain())
        while self.depth:
            self.depth -= 1
            out.append(self.rng.choice(["PLA", "PLP"]))
        return out


def program(seed):
    """The source of random program ``seed`` and its calls as ``(a, status)``."""
    rng = random.Random(seed)
    main = _Gen(rng, "m").body(rng.randint(3, 12))
    sub = _Gen(rng, "s").body(rng.randint(2, 8))
    irq = ["PLA"] + _Gen(rng, "i").body(rng.randint(0, 6)) + ["PHA"]
    if rng.random() < 0.5:
        main.insert(rng.randrange(len(main) + 1), "JSR sub")
    if rng.random() < 0.3:
        i = rng.randrange(len(main) + 1)
        main[i:i] = ["BRK", ".byte $EA"]
    if rng.random() < 0.3:
        main = ["LDA #$%02X" % rng.randint(1, 3), "STA $30", "top:"] + main
        main += ["DEC $30", "BNE top"]
    lines = (
        ["entry:"]
        + main
        + ["PHP", "PLA", "STA $1F", "STX $1E", "STY $1D", "RTS", "sub:"]
        + sub
        + ["RTS", "irq:"]
        + irq
        + ["RTI", ".org $FFFE", ".byte lo(irq), hi(irq)"]
    )
    calls = [(rng.randrange(256), rng.randrange(256) & 0xCF) for _ in range(4)]
    return "\n".join(ln if ln.endswith(":") else "        " + ln for ln in lines), calls


def _reg(a, status):
    """Registers with ``A`` and the flags of ``status``."""
    return [a, 0, 0, 0xFF] + [0] * 4 + [status >> i & 1 for i in (0, 1, 2, 3, 4, 6, 7)] + [0]


@pytest.mark.parametrize("cycles", [False, True])
@pytest.mark.parametrize("seed", range(120))
def test_random_status_programs_are_exact(seed, cycles):
    src, calls = program(seed)
    img, labels = assemble(src)
    pcs = [labels["entry"]]
    prog = wholeprog.lift(img, pcs, [IO], facts_for(img, pcs, [(0, calls[0][0])]), cycles=cycles)
    for a, status in calls:
        differential(img, prog, pcs, [(0, a)], reg=_reg(a, status))


MIXED = "SEC; PHP; PLA; TAY; LDA #$01; PHA; PLP; STY $C003"
CASES = {
    "mixed": MIXED,
    "status_as_data": "CMP #$40; PHP; PLA; TAX; LDA #$01; STX $C003",
    "data_as_status": "PHA; PLP; PHP; PLA; STA $C003",
    "loop": "LDX #$03; top: CPX #$02; PHP; PLA; TAY; TXA; PHA; PLP; STY $40,X; DEX; BNE top",
    "call": "SEC; PHP; PLA; TAY; JSR sub; PHA; PLP; STY $C003",
    "calls": "JSR pull; JSR push; STY $C003",
    "brk": "SEC; BRK; .byte $EA; STY $C003",
    "pulled_decimal": (
        "ADC #$40; PHP; PLA; LSR A; LSR A; LSR A; PHA; PLP; PHP; INY; PLP; PHP; PLA; STA $C003"
    ),
}
SUBS = """
sub:    LDA #$01
        RTS
pull:   SEC
        PHP
        PLA
        TAY
        RTS
push:   LDA #$C1
        PHA
        PLP
        RTS
irq:    PLA
        TAY
        LDA #$01
        PHA
        RTI
        .org $FFFE
        .byte lo(irq), hi(irq)
"""


def _src(body):
    """``body``'s ``;``-separated instructions as the program at ``entry``."""
    lines = ("        " + s.strip() for s in body.split(";"))
    return "entry:\n" + "\n".join(lines).replace("        top: ", "top:    ") + "\n        RTS"


@pytest.mark.parametrize("backend", ["jit", "interp"])
@pytest.mark.parametrize("cycles", [False, True])
@pytest.mark.parametrize("name", CASES)
def test_status_and_data_share_a_slot(name, cycles, backend, monkeypatch):
    monkeypatch.setattr(wholeprog, "BACKEND", backend)
    img, labels = assemble(_src(CASES[name]) + SUBS)
    pcs = [labels["entry"]]
    calls = [(0, a) for a in (0x00, 0x40, 0x41, 0x80, 0xC3)]
    prog = wholeprog.lift(img, pcs, [IO], facts_for(img, pcs, calls), cycles=cycles)
    differential(img, prog, pcs, calls)


def test_status_read_as_data_keeps_every_bit():
    img, labels = assemble(_src(MIXED))
    pcs = [labels["entry"]]
    prog = wholeprog.lift(img, pcs, [IO], facts_for(img, pcs, [(0, 0)]))
    out = prog.call(bytearray(img), 0, a=0, log=True)
    assert [tuple(e[:2]) for e in out["log"]] == [(0xC003, 0x31)]


HANDLED = {
    "sets_decimal": ("BRK; .byte $EA; ADC #$09; SED", "PLA; ORA #$08; PHA"),
    "clears_decimal": ("SED; BRK; .byte $EA; ADC #$09; CLD", "PLA; AND #$F7; PHA"),
    "keeps_decimal": ("SED; BRK; .byte $EA; ADC #$09; CLD", "PLA; PHA"),
    "caller_status": ("BRK; .byte $EA; ADC #$09; CLD; SED", "TAX; PLA; TXA; PHA"),
    "sets_carry": ("CLC; BRK; .byte $EA; BCS skip; LDA #$11; skip: ADC #$01", "PLA; ORA #$01; PHA"),
    "pulled_data": ("PHA; PLP; ADC #$09; CLD; SED", ""),
    "pulled_data_clear": ("PHA; PLP; ADC #$09; CLD", ""),
}


@pytest.mark.parametrize("backend", ["jit", "interp"])
@pytest.mark.parametrize("cycles", [False, True])
@pytest.mark.parametrize("name", HANDLED)
def test_pulled_status_sets_decimal_mode(name, cycles, backend, monkeypatch):
    monkeypatch.setattr(wholeprog, "BACKEND", backend)
    body, irq = HANDLED[name]
    src = _src(body + "; STA $C003; PHP; PLA; STA $C004").replace("        skip: ", "skip:   ")
    irq = "".join("        %s\n" % s.strip() for s in irq.split(";") if s.strip())
    img, labels = assemble(
        src + "\nirq:\n" + irq + "        RTI\n        .org $FFFE\n        .byte lo(irq), hi(irq)\n"
    )
    pcs = [labels["entry"]]
    calls = [(0, a) for a in (0x00, 0x08, 0x41, 0x49, 0xC3, 0xCB)]
    prog = wholeprog.lift(img, pcs, [IO], facts_for(img, pcs, calls), cycles=cycles)
    differential(img, prog, pcs, calls)
