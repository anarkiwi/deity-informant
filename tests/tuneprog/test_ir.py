"""The IR: P-Code -> statements is byte-exact against the VM; JSON; envelope; Interp."""

import json

import numpy as np
import pytest

import deity_informant as P
from deity_informant.lifter import ILLEGAL_OPCODES, MODE_LEN, OPS
from deity_informant.tuneprog import ir
from deity_informant.tuneprog.lower import ops_to_stmts, straightline
from deity_informant.tuneprog.interp import Interp, Machine
from deity_informant.tuneprog.ir import (
    Bin,
    Block,
    Const,
    Goto,
    Let,
    Load,
    Proc,
    R16,
    Return,
    Store,
    Switch,
    Trap,
    TrapError,
    Var,
    W16,
)

import _common as H
from _prog import counter, front, tuneprog

PC = H.PC
STRAIGHT = [
    op
    for op in range(256)
    if OPS[op][0] not in ("JMP", "JSR", "RTS", "RTI", "BRK", "JAM") and OPS[op][1] != "rel"
]


def _machine(mem, regs):
    m = Machine(mem)
    m.regs[:] = regs
    return m


def _vm_state(vm):
    return tuple(vm.reg[i] for i in (0, 1, 2, 3, 8, 9, 10, 11, 13, 14))


def _prog_of(mem, n, pc=PC):
    """A one-block procedure holding ``n`` consecutive lifted instructions."""
    ops, srcs = [], []
    for i in range(n):
        rec = P.lift(mem, pc)
        ops.extend(ops_to_stmts(rec["ops"], blk="i%d" % i))
        srcs.append(pc)
        pc = (pc + rec["len"]) & 0xFFFF
    regs = tuple(range(16))
    blk = Block("b0", ops, Return(tuple(Var(ir.REGVAR[i]) for i in regs)), srcs[0])
    return ir.Tuneprog(procs={"f": Proc("f", regs, regs, {"b0": blk}, "b0")})


@pytest.mark.parametrize("seed", [1, 2])
def test_lifted_pcode_matches_the_vm_on_random_programs(seed):
    # the fuzz shape of tests/test_lifter.py, but comparing the IR interpreter
    # against PcodeVM over three-instruction straight-line programs.
    rng = np.random.default_rng(0xC64 ^ seed)
    ops = np.array(STRAIGHT)
    bad = []
    for _ in range(400):
        mem = bytearray(bytes(rng.integers(0, 256, 0x10000, dtype=np.uint8)))
        pc = PC
        for _k in range(3):
            op = int(rng.choice(ops))
            mem[pc] = op
            pc += MODE_LEN[OPS[op][1]]
        regs = [int(v) for v in rng.integers(0, 256, 16)]
        regs[3] = 0x80  # keep pushes/pulls inside the stack page
        vm = P.PcodeVM(bytes(mem))
        vm.volatile = False
        vm.reg[:] = regs
        pc = PC
        for _k in range(3):
            pc = vm.step(pc, {}, P.lift)
        prog = _prog_of(bytearray(mem), 3)
        m = _machine(bytes(mem), regs)
        got = Interp(prog, m).run("f", regs)
        if _vm_state(vm) != tuple(got[i] for i in (0, 1, 2, 3, 8, 9, 10, 11, 13, 14)):
            bad.append(("state", bytes(mem[PC : PC + 6]).hex(), _vm_state(vm), got))
        elif bytes(m.m) != bytes(vm.mem):
            d = next(i for i in range(0x10000) if m.m[i] != vm.mem[i])
            bad.append(("mem", bytes(mem[PC : PC + 6]).hex(), hex(d), m.m[d], vm.mem[d]))
    assert not bad, bad[:4]


def test_every_illegal_opcode_is_covered_by_the_fuzz_set():
    assert len(set(STRAIGHT) & set(ILLEGAL_OPCODES)) >= 90


def test_straightline_helper_runs_one_instruction():
    mem = bytearray(0x10000)
    mem[PC : PC + 2] = bytes([0xA9, 0x42])  # LDA #$42
    proc = straightline(P.lift(mem, PC)["ops"])
    prog = ir.Tuneprog(procs={"f": proc})
    out = Interp(prog, Machine(bytes(mem))).run("f", [0] * 16)
    assert out[0] == 0x42 and out[9] == 0 and out[14] == 0


def test_json_round_trip_is_exact():
    _T, prog = tuneprog(counter("INC cnt", "LDA cnt", "STA $D400"), calls=3)
    doc = json.loads(json.dumps(prog.to_json()))
    back = ir.Tuneprog.from_json(doc)
    assert back.to_json() == prog.to_json()
    assert bytes(back.image()) == bytes(prog.image())
    assert set(back.procs) == set(prog.procs)


def test_the_sixteen_bit_view_round_trips_through_json():
    """S6's own two nodes are tagged like the rest: T0 serialises expressions."""
    pair = ((3, 0x14CA), (3, 0x14CB))
    w = W16(*pair, Var("X"), Bin("+", R16(*pair, Var("X")), Const(1, 2), 2), 0x1234, True, (1, 2))
    back = ir.dec(json.loads(json.dumps(ir.enc(w))))
    assert type(back) is W16 and type(back.e.a) is R16
    assert ir.enc(back) == ir.enc(w) and back.src == 0x1234 and back.hifirst
    assert ir.enc(w)[0] == "$w16" and ir.enc(w.e.a)[0] == "$r16"


def _tiny(stmts, term=None, params=()):
    proc = Proc("f", params, (), {"b0": Block("b0", stmts, term or Return(()))}, "b0")
    return ir.Tuneprog(procs={"f": proc})


def test_envelope_violation_traps():
    prog = _tiny([Let("t", Load("ram", Const(0x2000, 2), 1, 0x1000, 0x1FFF, 3))])
    with pytest.raises(TrapError) as e:
        Interp(prog, Machine(bytes(0x10000))).run("f")
    assert e.value.why == "envelope"
    prog = _tiny([Store("ram", Const(0x900, 2), Const(1), 1, 0x1000, 0x1FFF, 3)])
    with pytest.raises(TrapError) as e:
        Interp(prog, Machine(bytes(0x10000))).run("f")
    assert e.value.why == "envelope"


def test_trap_terminator_and_switch_default():
    with pytest.raises(TrapError, match="unverified"):
        Interp(_tiny([], Trap("unverified")), Machine(bytes(0x10000))).run("f")
    prog = _tiny([Let("t", Const(9))], Switch(Var("t"), ((1, "b0"),), ""))
    with pytest.raises(TrapError, match="switch"):
        Interp(prog, Machine(bytes(0x10000))).run("f")


def test_assert_statement_traps():
    from deity_informant.tuneprog.ir import Assert

    prog = _tiny([Assert(Const(0), "nope")])
    with pytest.raises(TrapError, match="nope"):
        Interp(prog, Machine(bytes(0x10000))).run("f")


def test_input_stream_is_consumed_in_order_and_checked():
    m = Machine(bytes(0x10000), inputs=[(0, 0, 0, 0xD012, 7)])
    assert m.take_input(0xD012) == 7
    with pytest.raises(TrapError, match="exhausted"):
        m.take_input(0xD012)
    m2 = Machine(bytes(0x10000), inputs=[(0, 0, 0, 0xD012, 7)])
    with pytest.raises(TrapError, match="mismatch"):
        m2.take_input(0xD41B)
    m3 = Machine(bytes(0x10000), override={0xD41B: 0x55})
    assert m3.ioload(0xD41B) == 0x55


def test_io_store_splits_sid_writes_from_schedule_effects():
    m = Machine(bytes(0x10000))
    m.iostore(0xD400, 0x12, 0x1234)
    m.iostore(0xDC04, 0x34)
    assert m.sid == [(0xD400, 0x12)] and m.src == [0x1234] and m.io == [(0xDC04, 0x34)]
    m.m[0] = 0x2F
    m.m[1] = 0x34  # bank I/O out: the same store is now RAM
    m.setbank()
    m.iostore(0xD400, 0x99)
    assert m.sid == [(0xD400, 0x12)] and m.m[0xD400] == 0x99 and 0xD400 in m.W


def test_hash_matches_the_tracers_keying():
    T, _tr, _L, _R, _P = front(counter("INC cnt", "LDA cnt", "STA $D400"), calls=3)
    m = Machine(bytes(T.image_pre))
    m.play_phase()
    for a in sorted(T.written_play):
        m.m[a] = T.image_post_init[a]
        m.W.add(a)
    assert m.hash()[0] == len(T.written_play)


def test_block_order_and_retarget():
    blocks = {
        "a": Block("a", [], Goto("b")),
        "b": Block("b", [], Return(())),
        "c": Block("c", [], Return(())),
    }
    p = Proc("f", (), (), blocks, "a")
    assert p.order() == ["a", "b"]
    blocks["a"].term = ir.retarget(blocks["a"].term, "b", "c")
    assert p.order() == ["a", "c"]


def test_save_and_load_a_tuneprog(tmp_path):
    _T, prog = tuneprog(counter("INC cnt", "LDA cnt", "STA $D400"), calls=2)
    p = prog.save(tmp_path / "tuneprog.json")
    back = ir.Tuneprog.load(p)
    assert back.to_json() == prog.to_json()


def test_retarget_rewrites_every_kind_of_successor():
    sw = Switch(Const(1), ((1, "a"), (2, "b")), "a")
    out = ir.retarget(sw, "a", "z")
    assert out.cases == ((1, "z"), (2, "b")) and out.default == "z"
    assert ir.retarget(Return(()), "a", "z") == Return(())


def test_evalbin_covers_the_whole_op_vocabulary():
    cases = {
        ("+", 250, 10, 1): 4,
        ("-", 1, 2, 1): 255,
        ("&", 0xF0, 0x3C, 1): 0x30,
        ("|", 0xF0, 0x0C, 1): 0xFC,
        ("^", 0xFF, 0x0F, 1): 0xF0,
        ("<<", 0x80, 1, 1): 0,
        (">>", 0x80, 1, 1): 0x40,
        ("==", 3, 3, 1): 1,
        ("!=", 3, 3, 1): 0,
        ("<", 1, 2, 1): 1,
        ("<=", 2, 2, 1): 1,
        ("carry", 200, 100, 1): 1,
        ("carry", 100, 100, 1): 0,
    }
    for (op, a, b, w), want in cases.items():
        assert ir.evalbin(op, a, b, w) == want, op
    with pytest.raises(TrapError, match="bad op"):
        ir.evalbin("**", 1, 2, 1)


def test_ram_under_io_is_read_from_memory_not_the_input_stream():
    m = Machine(bytes(0x10000))
    m.m[0xD400] = 0x5A
    m.m[0] = 0x2F
    m.m[1] = 0x34  # I/O banked out
    m.setbank()
    assert m.ioload(0xD400) == 0x5A
