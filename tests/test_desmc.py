"""The de-SMC invariant: no emitted store reaches executable memory (frameprog 2).

One generated 6502 driver per self-modification class -- operand, immediate, vector,
opcode -- lifted, gated and round-tripped, plus the evaluator's own refusal when a
store into an instruction stream is put in front of it.
"""

import pytest

from deity_informant import desmc, framefuse, frameproc, frameprog, frameval
from deity_informant import structured as S
from deity_informant.asm6502 import Asm

ORG = 0x1000
SID = 0xD400
TICK, ROW = 0xF2, 0xF3
FRAMES = 24
BNE, BEQ = 0xD0, 0xF0


def _head(p):
    p.label("init").i("LDA", "imm", 0).i("STA", "zp", TICK).i("STA", "zp", ROW).i("RTS")
    p.label("play")


def _tail(p):
    p.i("INC", "zp", TICK)
    p.i("LDA", "zp", TICK).i("AND", "imm", 7).i("STA", "zp", ROW).i("RTS")


def operand():
    """The absolute operand of a table read is patched: a pointer in code space."""
    p = Asm(ORG)
    _head(p)
    p.i("LDX", "zp", ROW)
    p.i("LDA", "absx", ("L", "lo")).i("STA", "abs", ("L", "rd", 1))
    p.i("LDA", "absx", ("L", "hi")).i("STA", "abs", ("L", "rd", 2))
    p.i("LDY", "zp", ROW).label("rd").i("LDA", "absy", 0).i("STA", "abs", SID)
    _tail(p)
    p.label("lo").byte(*[("LOL", "blk")] * 8)
    p.label("hi").byte(*[("HIL", "blk")] * 8)
    p.label("blk").byte(0x10, 0x20, 0x30, 0x40, 0x50, 0x60, 0x70, 0x80)
    return p


def immediate():
    """The immediate operand of a load is patched: a value cell in code space."""
    p = Asm(ORG)
    _head(p)
    p.i("LDX", "zp", ROW)
    p.i("LDA", "absx", ("L", "tab")).i("STA", "abs", ("L", "imm", 1))
    p.label("imm").i("LDA", "imm", 0).i("STA", "abs", SID)
    _tail(p)
    p.label("tab").byte(0x21, 0x41, 0x11, 0x81, 0x15, 0x33, 0x51, 0x71)
    return p


def vector():
    """A two-target JMP whose operand bytes are patched: an indirect call."""
    p = Asm(ORG)
    _head(p)
    p.i("LDA", "zp", TICK).i("AND", "imm", 1).i("TAX")
    p.i("LDA", "absx", ("L", "veclo")).i("STA", "abs", ("L", "vec", 1))
    p.i("LDA", "absx", ("L", "vechi")).i("STA", "abs", ("L", "vec", 2))
    p.label("vec").i("JMP", "abs", ("L", "h0"))
    p.label("h0").i("LDA", "imm", 0x33).i("STA", "abs", SID).i("JMP", "abs", ("L", "out"))
    p.label("h1").i("LDA", "imm", 0x44).i("STA", "abs", SID)
    p.label("out")
    _tail(p)
    p.label("veclo").byte(("LOL", "h0"), ("LOL", "h1"))
    p.label("vechi").byte(("HIL", "h0"), ("HIL", "h1"))
    return p


def opcode():
    """A branch opcode toggled between BNE and BEQ: a mode flag at the site."""
    p = Asm(ORG)
    _head(p)
    p.i("LDA", "zp", TICK).i("AND", "imm", 1).i("BEQ", "rel", ("L", "setbne"))
    p.i("LDA", "imm", BEQ).i("BNE", "rel", ("L", "put"))
    p.label("setbne").i("LDA", "imm", BNE)
    p.label("put").i("STA", "abs", ("L", "br", 0))
    p.i("LDA", "zp", ROW).i("CMP", "imm", 4)
    p.label("br").i("BNE", "rel", ("L", "skip"))
    p.i("LDA", "imm", 0x11).i("STA", "abs", SID)
    p.label("skip")
    _tail(p)
    return p


CLASSES = {"operand": operand, "immediate": immediate, "vector": vector, "opcode": opcode}


def build(make):
    """``(model, program)`` of one generated driver, lifted at the suite's clock."""
    p = make()
    code = p.assemble()
    mem = bytearray(0x10000)
    mem[ORG : ORG + len(code)] = code
    model, _ev = S.decompile(mem, p.labels["init"], p.labels["play"], FRAMES)
    return model, frameprog.program(model)


def _page(mem, p):
    """The 256 bytes of page ``p`` of an image."""
    return bytes(mem[p << 8 : (p << 8) + 0x100])


def stores(prog):
    """Every store statement of ``prog``, nested bodies included."""
    for _e, _p, _r, stmts in prog.procs:
        for s in framefuse.stmts_of(stmts):
            if s[0] == "st":
                yield s


@pytest.mark.parametrize("name", sorted(CLASSES))
def test_the_class_patches_code_and_the_lift_moves_it_out(name):
    """Each class really self-modifies, and no emitted store lands in an instruction."""
    model, prog = build(CLASSES[name])
    assert set(desmc.code_bytes(model)) & set(model.written), "the driver does not patch code"
    code = desmc.executable(prog)
    for s in stores(prog):
        base, _idx = frameproc.addr_split(s[1])
        assert base is None or base not in code, "store into executable memory at $%04X" % base


@pytest.mark.parametrize("name", sorted(CLASSES))
def test_the_relocated_program_still_passes_gate_fp(name):
    """Relocation is a renaming: the canonical write record may not move (spec 1.4)."""
    model, prog = build(CLASSES[name])
    assert frameval.gate_fp(model, FRAMES, prog) is None


@pytest.mark.parametrize("name", sorted(CLASSES))
def test_the_artifact_round_trips_and_rebuilds(name):
    """The text is still a canonical fixpoint and still rebuilds its own block model."""
    _model, prog = build(CLASSES[name])
    text = frameprog.dumps(prog)
    assert frameprog.dumps(frameprog.loads(text)) == text
    assert frameprog.dumps(frameprog.program(frameprog.block_model(frameprog.loads(text)))) == text


@pytest.mark.parametrize("name", sorted(CLASSES))
def test_a_relocated_cell_is_seeded_from_the_image(name):
    """The pre-first-patch value is the image's, so no frame reads an invented byte."""
    model, prog = build(CLASSES[name])
    assert prog.relocated, "no run moved, so the class was never relocated"
    code = desmc.code_bytes(model)
    for lo, hi, dst in prog.relocated:
        moved = [c for c in range(lo, hi + 1) if c in code]
        assert moved
        assert all(prog.mem0[c - lo + dst] == model.mem0[c] for c in moved)


def test_the_evaluator_refuses_a_store_into_executable_memory():
    """The invariant is machine-checked: a store put back into code faults at build."""
    model, prog = build(operand)
    code = sorted(desmc.executable(prog))
    entry, params, rets, stmts = prog.procs[0]
    hurt = [("st", ("const", code[0], 2), ("const", 0, 1))] + list(stmts)
    prog.procs[0] = (entry, params, rets, hurt)
    with pytest.raises(frameval.FrameFault, match="store into executable memory"):
        frameval.gate_fp(model, FRAMES, prog)


def test_the_relocation_names_its_refusals():
    """A refusal is named and counted, never worked around silently."""
    model, _prog = build(operand)
    rel = desmc.Relocation(model)
    rel.free = set()
    rel.spans = [(0x1000, 0x10FF)]
    assert not rel.allocate(model.mem0)
    assert set(rel.refusals) == {"no-free-run"}
