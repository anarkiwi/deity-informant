"""The SMC context constructors must reproduce ``tuneprog.lift``'s residualisation.

Same program, same cell set, two independent engines: our Python lifter and the
6510 SLEIGH spec under ``smc_*=1``. Both must turn the hello-world demo's
self-modified ``STA $0400`` into a store through the operand bytes it modifies.
"""

import pytest

from deity_informant import PcodeVM, lift
from deity_informant.tuneprog.lift import lift_site

from examples.hello_world import ORG, PROGRAM, STA_PC

pypcode = pytest.importorskip("pypcode")

CELL = STA_PC + 1  # $100A, the operand byte ISC increments
EOR_PC = 0x1007  # EOR #$FF -- an immediate cell
BNE_PC = 0x1010  # BNE $1002 -- a relative-branch cell


def _image():
    m = bytearray(0x10000)
    m[ORG : ORG + len(PROGRAM)] = PROGRAM
    return m


def _lifted(pc, cells):
    site = {
        "pc": pc,
        "opcode": PROGRAM[pc - ORG],
        "variants": [bytes(PROGRAM[pc - ORG : pc - ORG + 3])],
    }
    return lift_site(_image(), site, cells, key=(pc, site["opcode"], ()))


def _ops(ctx, pc, **context):
    """Raw P-Code of the instruction at ``pc`` under the given context values."""
    for k, v in context.items():
        ctx.setVariableDefault(k, v)
    try:
        return list(ctx.translate(PROGRAM[pc - ORG :], pc, max_instructions=1).ops)
    finally:
        for k in context:
            ctx.setVariableDefault(k, 0)


def _kinds(ops):
    return [op.opcode.name for op in ops]


def _defs(ops):
    """``{(space, offset): op}`` for every op output."""
    return {(op.output.space.name, op.output.offset): op for op in ops if op.output is not None}


def test_default_context_stores_to_a_constant_address(ctx6510):
    ops = _ops(ctx6510, STA_PC)
    assert _kinds(ops) == ["IMARK", "COPY"]
    assert ops[1].output.space.name == "RAM"  # a direct global, no LOAD/STORE


def test_smc_addr_stores_through_the_operand_cell(ctx6510):
    ops = _ops(ctx6510, STA_PC, smc_addr=1)
    store = [op for op in ops if op.opcode.name == "STORE"]
    assert len(store) == 1
    ptr = store[0].inputs[1]
    load = _defs(ops)[(ptr.space.name, ptr.offset)]
    assert load.opcode.name == "LOAD" and load.output.size == 2
    # the loaded pointer is the instruction's own operand: inst_start + 1
    src = _defs(ops)[(load.inputs[1].space.name, load.inputs[1].offset)]
    assert src.opcode.name == "INT_ADD"
    assert [v.offset for v in src.inputs] == [STA_PC, 1]


def test_lifter_and_sleigh_agree_on_the_smc_store(ctx6510):
    ls = _lifted(STA_PC, {CELL})
    assert [o[0] for o in ls.ops].count("LOAD") == 1
    load = next(o for o in ls.ops if o[0] == "LOAD")
    assert load[2][0][1] == CELL and load[1][2] == 2
    store = next(o for o in ls.ops if o[0] == "STORE")
    assert store[2][0] == load[1]  # the store addresses through the loaded cell
    sleigh = _ops(ctx6510, STA_PC, smc_addr=1)
    assert _kinds(sleigh).count("LOAD") == 1 and _kinds(sleigh).count("STORE") == 1


def test_lifter_without_cells_and_sleigh_without_context_agree(ctx6510):
    ls = _lifted(STA_PC, set())
    assert "LOAD" not in [o[0] for o in ls.ops]
    store = next(o for o in ls.ops if o[0] == "STORE")
    assert store[2][0][0] == "c" and store[2][0][1] == 0x0400
    assert "LOAD" not in _kinds(_ops(ctx6510, STA_PC))


def test_smc_imm_reads_the_immediate_from_memory(ctx6510):
    ops = _ops(ctx6510, EOR_PC, smc_imm=1)
    assert "LOAD" in _kinds(ops)
    assert "LOAD" not in _kinds(_ops(ctx6510, EOR_PC))
    ls = _lifted(EOR_PC, {EOR_PC + 1})
    assert [o[0] for o in ls.ops].count("LOAD") == 1


def test_smc_ctrl_makes_a_branch_computed(ctx6510):
    ops = _ops(ctx6510, BNE_PC, smc_ctrl=1)
    assert "BRANCHIND" in _kinds(ops) and "LOAD" in _kinds(ops)
    assert "BRANCHIND" not in _kinds(_ops(ctx6510, BNE_PC))


def test_smc_var_guards_the_instruction_with_a_return(ctx6510):
    ops = _ops(ctx6510, EOR_PC, smc_var=1)
    assert "RETURN" in _kinds(ops)
    assert "RETURN" not in _kinds(_ops(ctx6510, EOR_PC))


def test_jsr_rts_use_the_hardware_stack_convention(ctx6510):
    """A 6510 program can read its own return address, so it must be ``ret - 1``."""
    ops = list(ctx6510.translate(bytes.fromhex("201234"), 0x2000, max_instructions=1).ops)
    store = next(o for o in ops if o.opcode.name == "STORE")
    src = _defs(ops)[(store.inputs[2].space.name, store.inputs[2].offset)]
    assert src.opcode.name == "INT_SUB" and src.inputs[0].offset == 0x2003
    rts = list(ctx6510.translate(bytes.fromhex("60"), 0x2000, max_instructions=1).ops)
    ret = next(o for o in rts if o.opcode.name == "RETURN")
    add = _defs(rts)[(ret.inputs[0].space.name, ret.inputs[0].offset)]
    assert add.opcode.name == "INT_ADD" and add.inputs[1].offset == 1


def test_default_context_decodes_the_whole_demo_unchanged(ctx6510):
    d = ctx6510.disassemble(PROGRAM, ORG, 0)
    assert [i.mnem for i in d.instructions][:6] == ["LDY", "LAX", "BEQ", "EOR", "STA", "ISC"]


BIN = {
    "INT_ADD": lambda a, b: a + b,
    "INT_SUB": lambda a, b: a - b,
    "INT_AND": lambda a, b: a & b,
    "INT_OR": lambda a, b: a | b,
    "INT_XOR": lambda a, b: a ^ b,
    "INT_EQUAL": lambda a, b: int(a == b),
    "INT_NOTEQUAL": lambda a, b: int(a != b),
    "INT_LESS": lambda a, b: int(a < b),
    "INT_LESSEQUAL": lambda a, b: int(a <= b),
    "BOOL_AND": lambda a, b: a & b,
    "BOOL_OR": lambda a, b: a | b,
}
UN = {"COPY": int, "INT_ZEXT": int, "INT_NEGATE": lambda a: ~a, "INT_2COMP": lambda a: -a}


def _sext(v, size):
    return v - (1 << (8 * size)) if v >> (8 * size - 1) else v


def _run(ops, state):
    """Evaluate raw P-Code over a ``{(space, offset): value}`` file, in place."""
    for op in ops:
        if op.output is None:
            continue
        name = op.opcode.name
        a = [
            v.offset if v.space.name == "const" else state.get((v.space.name, v.offset), 0)
            for v in op.inputs
        ]
        if name in UN:
            r = UN[name](a[0])
        elif name == "INT_SEXT":
            r = _sext(a[0], op.inputs[0].size)
        elif name == "BOOL_NEGATE":
            r = a[0] ^ 1
        elif name in ("INT_SLESS", "INT_SLESSEQUAL"):
            x, y = (_sext(a[i], op.inputs[i].size) for i in (0, 1))
            r = int(x < y) if name == "INT_SLESS" else int(x <= y)
        else:
            r = BIN[name](a[0], a[1])
        state[(op.output.space.name, op.output.offset)] = r & (1 << (8 * op.output.size)) - 1
    return state


def _sleigh_sbc(ctx, acc, imm, carry):
    """``(A, C)`` the SLEIGH spec leaves after ``SBC #imm``."""
    reg = {n: (v.space.name, v.offset) for n, v in ctx.registers.items()}
    state = _run(
        ctx.translate(bytes([0xE9, imm]), 0x2000, max_instructions=1).ops,
        {reg["A"]: acc, reg["C"]: carry},
    )
    return state[reg["A"]], state[reg["C"]]


def _lifter_sbc(acc, imm, carry):
    """``(A, C)`` our own lifter leaves after the same instruction."""
    vm = PcodeVM(bytearray(0x10000))
    vm.mem[0x2000:0x2002] = bytes([0xE9, imm])
    vm.reg[0], vm.reg[8] = acc, carry
    vm.step(0x2000, {}, lift)
    return vm.reg[0], vm.reg[8]


# C clear exactly when the subtraction borrowed; the stock spec has the borrow
@pytest.mark.parametrize(
    "acc,imm,carry,want",
    [
        (0x00, 0x01, 1, (0xFF, 0)),
        (0x05, 0x03, 1, (0x02, 1)),
        (0x05, 0x05, 1, (0x00, 1)),
        (0x00, 0x00, 0, (0xFF, 0)),
        (0x80, 0x01, 1, (0x7F, 1)),
    ],
)
def test_sbc_borrow_matches_the_hardware_and_the_lifter(ctx6510, acc, imm, carry, want):
    assert _lifter_sbc(acc, imm, carry) == want
    assert _sleigh_sbc(ctx6510, acc, imm, carry) == want
