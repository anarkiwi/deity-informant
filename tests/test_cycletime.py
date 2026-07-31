"""CYCLETIME/EXTRACYCLES pinned by family rule, and by what the VM charges.

On the NMOS 6502 cost is a function of (class, addressing mode) alone, with no
per-opcode exceptions, so a per-family table pins all 256 entries -- and catches
a single-entry typo. References and audit method: docs/cycle-times.md.
"""

import pytest

import deity_informant as P

import _common as H

# mode -> (base cycles, page-cross penalty) for each instruction class.
READ = {
    "imm": (2, 0),
    "zp": (3, 0),
    "zpx": (4, 0),
    "zpy": (4, 0),
    "abs": (4, 0),
    "absx": (4, 1),
    "absy": (4, 1),
    "indx": (6, 0),
    "indy": (5, 1),
}
WRITE = {
    "zp": (3, 0),
    "zpx": (4, 0),
    "zpy": (4, 0),
    "abs": (4, 0),
    "absx": (5, 0),
    "absy": (5, 0),
    "indx": (6, 0),
    "indy": (6, 0),
}
RMW = {
    "zp": (5, 0),
    "zpx": (6, 0),
    "abs": (6, 0),
    "absx": (7, 0),
    "absy": (7, 0),
    "indx": (8, 0),
    "indy": (8, 0),
}

RMW_MN = {"ASL", "ROL", "LSR", "ROR", "DEC", "INC", "SLO", "RLA", "SRE", "RRA", "DCP", "ISC"}
WRITE_MN = {"STA", "STX", "STY", "SAX", "SHA", "SHX", "SHY", "TAS"}
# implied/accumulator forms cost 2 apart from these; a JAM never completes.
FIXED = {"BRK": 7, "JSR": 6, "RTI": 6, "RTS": 6, "PHA": 3, "PHP": 3, "PLA": 4, "PLP": 4, "JAM": 0}

BRANCHES = {op for op, (_mn, md) in P.OPS.items() if md == "rel"}
JAMS = {op for op, (mn, _md) in P.OPS.items() if mn == "JAM"}


def expect(mn, mode):
    """Reference ``(cycles, extracycles)`` for one (mnemonic, addressing mode)."""
    if mn == "JMP":
        return (3, 0) if mode == "abs" else (5, 0)
    if mn == "JSR":
        return (6, 0)
    if mode == "rel":
        return (2, 2)
    if mode in ("impl", "acc"):
        return (FIXED.get(mn, 2), 0)
    if mn in RMW_MN:
        return RMW[mode]
    if mn in WRITE_MN:
        return WRITE[mode]
    return READ[mode]


def test_tables_are_complete():
    assert len(P.CYCLETIME) == len(P.EXTRACYCLES) == 256
    assert len(P.OPS) == 256


def test_tables_follow_family_rules():
    bad = {
        "$%02X %s %s" % (op, mn, md): ((P.CYCLETIME[op], P.EXTRACYCLES[op]), expect(mn, md))
        for op, (mn, md) in sorted(P.OPS.items())
        if (P.CYCLETIME[op], P.EXTRACYCLES[op]) != expect(mn, md)
    }
    assert not bad, bad


def test_rmw_indexed_absolute_is_a_fixed_seven():
    """RMW abs,X / abs,Y always writes, so it pays the dummy read unconditionally."""
    idx = [op for op, (mn, md) in P.OPS.items() if mn in RMW_MN and md in ("absx", "absy")]
    assert len(idx) == 18
    assert all((P.CYCLETIME[op], P.EXTRACYCLES[op]) == (7, 0) for op in idx)


def _cycles(prog, a=0, x=0, y=0, sp=0xFF, p=0x20):
    """Cycles the VM charges for a single step of ``prog`` placed at ``H.PC``."""
    vm = P.PcodeVM(H.image(prog, H.PC))
    vm.volatile = False
    H.load_regs(vm, a, x, y, sp, p)
    vm.step(H.PC, {}, P.lift)
    return vm.cycles


def _operand(mode):
    """Operand bytes naming $1234 (zero page $34), so no indexed form crosses."""
    return {1: [], 2: [0x34], 3: [0x34, 0x12]}[P.MODE_LEN[mode]]


def test_vm_charges_the_table():
    """The table being right and the VM charging it are two different claims."""
    bad = {
        "$%02X %s %s" % (op, mn, md): (_cycles([op] + _operand(md)), P.CYCLETIME[op])
        for op, (mn, md) in sorted(P.OPS.items())
        if op not in BRANCHES | JAMS and _cycles([op] + _operand(md)) != P.CYCLETIME[op]
    }
    assert not bad, bad


def test_dec_abs_executes_in_six():
    """$CE was charged 3, inherited from py65; the NMOS 6502 takes 6."""
    assert _cycles([0xCE, 0x34, 0x12]) == 6


@pytest.mark.parametrize("op", (0x1E, 0x3E, 0x5E, 0x7E, 0xDE, 0xFE, 0xDB, 0xFB))
def test_vm_charges_no_page_cross_for_rmw_indexed(op):
    assert _cycles([op, 0xFF, 0x12], x=1, y=1) == 7


def test_vm_charges_the_page_cross_penalty():
    assert _cycles([0xBD, 0xFF, 0x12], x=0) == 4
    assert _cycles([0xBD, 0xFF, 0x12], x=1) == 5


@pytest.mark.parametrize("p,off,cyc", ((0x22, 0x01, 2), (0x20, 0x01, 3), (0x20, 0x80, 4)))
def test_vm_charges_the_branch_penalty(p, off, cyc):
    assert _cycles([0xD0, off], p=p) == cyc
