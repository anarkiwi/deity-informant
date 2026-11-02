"""Interrupts taken at an instruction boundary: events counted in retired instructions."""

import pytest

from deity_informant import NATIVE, wholeprog

pytestmark = pytest.mark.skipif(not NATIVE, reason="needs the native extension")

MAIN, HANDLER, INNER = 0x1000, 0x1100, 0x1110
ENTRIES = [MAIN, HANDLER, INNER]
# LDA #1; STA $0200; LDA #2; STA $0200; RTS
CODE = bytes.fromhex("a901 8d0002 a902 8d0002 60")
# LDA $0200; STA $0202; RTI: records the value the interrupted code has stored so far
SEEN = bytes.fromhex("ad0002 8d0202 40")
RET = 0x4FFF


def image(handler=SEEN):
    img = bytearray(0x10000)
    img[MAIN : MAIN + len(CODE)] = CODE
    img[HANDLER : HANDLER + len(handler)] = handler
    return img


def regs(a, sp=0xFD):
    return [a, 0, 0, sp] + [0] * 12


def event(icount, a, pos=0, depth=0, entry=1):
    return (icount, pos, entry, a, regs(a, 0xFB), depth)


def lifted(img, nested, **kw):
    prog = wholeprog.lift(img, [MAIN, HANDLER])
    mem = bytearray(img)
    return prog.call(mem, 0, reg=regs(0), ret=RET, log=True, nested=nested, **kw), mem


def reference(img, nested):
    """The reference interpreter takes entry addresses where the lift takes entry numbers."""
    mem = bytearray(img)
    nested = [(i, p, ENTRIES[e], a, r, d) for i, p, e, a, r, d in nested]
    out = wholeprog.reference_call(mem, MAIN, reg=regs(0), ret=RET, log=True, nested=nested)
    return out, mem


#: the instructions retired -> the A register and the byte at $0200 the handler finds
STATE = {0: (0, 0), 1: (1, 0), 2: (1, 1), 3: (2, 1), 4: (2, 2)}


@pytest.mark.parametrize("retired", sorted(STATE))
def test_handler_sees_the_state_after_exactly_that_many_instructions(retired):
    a, seen = STATE[retired]
    nested = [event(retired, a)]
    out, mem = lifted(image(), nested)
    ref, rmem = reference(image(), nested)
    assert out["boundary"] is None and ref["boundary"] is None
    assert mem[0x202] == rmem[0x202] == seen
    assert mem[0x200:] == rmem[0x200:] and out["reg"] == ref["reg"] and out["halt"] == "return"


def test_registers_the_machine_did_not_have_are_a_boundary_mismatch():
    out, _ = lifted(image(), [event(2, 9)])
    assert out["boundary"] == 0
    assert reference(image(), [event(2, 9)])[0]["boundary"] == 0


def test_a_device_access_count_the_machine_did_not_have_is_a_boundary_mismatch():
    assert lifted(image(), [event(2, 1, pos=1)])[0]["boundary"] == 0


def test_an_interrupt_after_the_last_instruction_never_comes():
    assert lifted(image(), [event(99, 2)])[0]["boundary"] == 0


def test_a_handler_interrupted_in_turn_counts_its_own_instructions():
    # the handler's LDA, then (depth 1) the inner handler: INC $0203; RTI
    handler = (SEEN + bytes(INNER - HANDLER - len(SEEN))) + bytes.fromhex("ee0302 40")
    inner = regs(1, 0xF8)
    inner[10] = 1
    nested = [event(2, 1), (1, 0, 2, 1, inner, 1)]
    prog = wholeprog.lift(image(handler), ENTRIES)
    mem = bytearray(image(handler))
    out = prog.call(mem, 0, reg=regs(0), ret=RET, nested=nested)
    ref, ref_mem = reference(image(handler), nested)
    assert out["boundary"] is None and ref["boundary"] is None
    assert mem[0x200:] == ref_mem[0x200:] and mem[0x203] == 1
