"""CI integration test for the self-contained hello-world demo.

Verifies end to end: the VM produces the exact HELLO, WORLD! screen codes; the two
load-bearing illegal opcodes actually execute; the self-modifying STA re-lifts once
per store target; and the 6510 SLEIGH engine (pypcode = libsla, Ghidra's own engine)
decodes the demo's illegals (stock-6502 rejection is covered in test_coverage).
"""

from deity_informant import ILLEGAL_OPCODES, PcodeVM, lift, run_sub

from examples.hello_world import EXPECTED, ORG, PROGRAM, STA_PC

import _common as H


def _run():
    vm = PcodeVM(H.image(PROGRAM, ORG))
    cache = {}
    run_sub(vm, ORG, cache, lift)
    return vm, cache


def test_vm_prints_hello_world():
    vm, _ = _run()
    assert vm.mem[0x0400 : 0x0400 + len(EXPECTED)] == EXPECTED


def test_uses_illegal_opcodes():
    _, cache = _run()
    executed = set(k[1] for k in cache)
    assert 0xBF in executed and 0xBF in ILLEGAL_OPCODES  # LAX
    assert 0xEF in executed and 0xEF in ILLEGAL_OPCODES  # ISC


def test_self_modifying_code_relifts():
    _, cache = _run()
    # ISC's INC rewrites the STA operand each pass, so the (pc,bytes) cache key at
    # $1009 changes -> a fresh re-lift per store target (13 distinct operands).
    assert sum(1 for k in cache if k[0] == STA_PC) == len(EXPECTED)


def test_disassembles_through_ghidra_sleigh(ctx6510):
    d = ctx6510.disassemble(PROGRAM, 0x1000, 0)
    by_addr = {i.addr.offset: i.mnem for i in d.instructions}
    assert by_addr.get(0x1002) == "LAX"  # $BF
    assert by_addr.get(0x100C) == "ISC"  # $EF
    # Stock-6502 rejection of these bytes is test_coverage::test_stock_6502_rejects_illegals.
