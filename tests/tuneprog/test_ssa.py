"""S4: SSA construction, DCE, copy/constant propagation -- all Interp-preserving."""

import numpy as np
import pytest

import deity_informant as P
from deity_informant.lifter import MODE_LEN, OPS
from deity_informant.tuneprog import ir, ssa
from deity_informant.tuneprog.lower import ops_to_stmts
from deity_informant.tuneprog.idioms import rewrite
from deity_informant.tuneprog.interp import Interp, Machine
from deity_informant.tuneprog.ir import (
    Bin,
    Block,
    Const,
    Goto,
    If,
    Let,
    Phi,
    Proc,
    Return,
    Var,
)
from deity_informant.tuneprog.verify import verify

import _common as H
from _prog import PLAY, counter, tuneprog
from _asm import asm
from test_ir import STRAIGHT

REGS = tuple(range(16))
NAMES = tuple(ir.REGVAR[i] for i in REGS)


def _rand_block(rng, mem, pc, tag, n):
    """``n`` random straight-line instructions lifted into one block's statements."""
    out = []
    for i in range(n):
        op = int(rng.choice(STRAIGHT))
        mem[pc] = op
        rec = P.lift(mem, pc)
        out.extend(ops_to_stmts(rec["ops"], blk="%s%d" % (tag, i)))
        pc += MODE_LEN[OPS[op][1]]
    return out, pc


def _rand_proc(rng, mem):
    """A diamond CFG over random instructions: the shape that needs phi nodes."""
    pc = H.PC
    a, pc = _rand_block(rng, mem, pc, "a", 2)
    b, pc = _rand_block(rng, mem, pc, "b", 2)
    c, pc = _rand_block(rng, mem, pc, "c", 2)
    d, pc = _rand_block(rng, mem, pc, "d", 1)
    ret = Return(tuple(Var(n) for n in NAMES))
    blocks = {
        "A": Block("A", a, If(Bin("==", Var("C"), Const(1)), "B", "C"), H.PC),
        "B": Block("B", b, Goto("D"), H.PC),
        "C": Block("C", c, Goto("D"), H.PC),
        "D": Block("D", d, ret, H.PC),
    }
    return ir.Tuneprog(procs={"f": Proc("f", REGS, REGS, blocks, "A")})


def _run(prog, mem, regs):
    m = Machine(bytes(mem))
    m.regs[:] = regs
    return Interp(prog, m).run("f", regs), bytes(m.m)


@pytest.mark.parametrize("seed", [3, 4])
def test_passes_preserve_semantics_on_random_cfgs(seed):
    rng = np.random.default_rng(0xC64 ^ seed)
    for _ in range(120):
        mem = bytearray(bytes(rng.integers(0, 256, 0x10000, dtype=np.uint8)))
        regs = [int(v) for v in rng.integers(0, 256, 16)]
        regs[3] = 0x80
        prog = _rand_proc(rng, mem)
        want = _run(prog, mem, regs)
        proc = prog.procs["f"]
        for step in (ssa.merge_chains, ssa.split_critical, ssa.to_ssa):
            step(proc)
            assert _run(prog, mem, regs) == want, step.__name__
        for step in (ssa.copyprop, ssa.dce, rewrite, ssa.constprop, ssa.from_ssa):
            step(proc)
            assert _run(prog, mem, regs) == want, step.__name__
        assert not any(type(s) is Phi for b in proc.blocks.values() for s in b.stmts)


def test_phi_nodes_appear_only_where_a_register_is_live():
    rng = np.random.default_rng(7)
    mem = bytearray(bytes(rng.integers(0, 256, 0x10000, dtype=np.uint8)))
    proc = _rand_proc(rng, mem).procs["f"]
    ssa.split_critical(proc)
    ssa.to_ssa(proc)
    phis = [s for b in proc.blocks.values() for s in b.stmts if type(s) is Phi]
    assert phis, "a diamond over register writes must need phis"
    assert all(len(s.args) == 2 for s in phis)
    assert all(s.n.split("#")[0] in ir.REGIDX for s in phis)


def test_dce_removes_every_flag_computation_of_a_flag_free_program():
    _T, prog = tuneprog(counter("LDA #$07", "STA $D400", "LDX #$01"), calls=2, s4=True)
    flags = {"C", "Z", "N", "V"}
    defs = {
        s.n.split("#")[0]
        for p in prog.procs.values()
        for b in p.blocks.values()
        for s in b.stmts
        if type(s) is Let
    }
    assert not defs & flags
    # the flags a flag-free tick still returns are folded to constants, not computed
    tick = prog.procs["tick"]
    vals = dict(zip(tick.rets, [b.term.vals for b in tick.blocks.values() if b.term.vals][0]))
    assert all(type(vals[i]) is Const for i in (9, 14) if i in vals)


def test_constprop_folds_a_const_table_load():
    code = asm(
        PLAY,
        "init: RTS",
        "play: LDA tab+1",
        "STA $D400",
        "RTS",
        "tab: BRK",
        "BRK",
    )
    _T, prog = tuneprog(code, calls=2, s4=True, data={code.labels["tab"] + 1: 0x5A})
    consts = [
        s.e.v
        for p in prog.procs.values()
        for b in p.blocks.values()
        for s in b.stmts
        if type(s) is Let and type(s.e) is Const
    ]
    st = [
        s
        for p in prog.procs.values()
        for b in p.blocks.values()
        for s in b.stmts
        if type(s) is ir.Store and s.cls == "io"
    ]
    assert 0x5A in consts or any(type(s.v) is Const and s.v.v == 0x5A for s in st)


def test_an_init_patched_immediate_folds_to_a_constant_in_the_tick():
    # the SID Wizard shape: init writes an immediate operand of the play code once.
    # The site loads the cell (it is not a constant of the file), and S4 folds that
    # load back to the byte init left there -- but only outside init.
    code = asm(
        PLAY,
        "init: LDA #$2A",
        "STA vol+1",
        "RTS",
        "play: vol: LDA #$00",
        "STA $D418",
        "RTS",
    )
    T, prog = tuneprog(code, calls=2, s4=True)
    cell = code.labels["vol"] + 1
    assert cell in T.cells and cell not in T.written_play
    tick = prog.procs["tick"]
    assert not [
        s
        for b in tick.blocks.values()
        for s in b.stmts
        if type(s) is Let and type(s.e) is ir.Load and type(s.e.a) is Const and s.e.a.v == cell
    ]
    assert any(
        type(s) is ir.Store and s.cls == "io" and type(s.v) is Const and s.v.v == 0x2A
        for b in tick.blocks.values()
        for s in b.stmts
    )
    # init itself never folds: the cell has no value there until its own store runs
    assert any(
        type(s) is ir.Store and type(s.a) is Const and s.a.v == cell
        for b in prog.procs["init"].blocks.values()
        for s in b.stmts
    )
    assert verify(prog, T, calls=2).div is None


def test_an_init_written_variable_is_not_folded_away():
    # only *cells* fold: an ordinary byte init writes stays a named load, so the
    # printer can still show the variable (and --songs all stays sound).
    code = asm(
        PLAY,
        "init: LDA #$2A",
        "STA v",
        "RTS",
        "play: LDA v",
        "STA $D418",
        "RTS",
        "v: BRK",
    )
    _T, prog = tuneprog(code, calls=2, s4=True)
    loads = [
        s.e.a.v
        for b in prog.procs["tick"].blocks.values()
        for s in b.stmts
        if type(s) is Let and type(s.e) is ir.Load and type(s.e.a) is Const
    ]
    assert code.labels["v"] in loads


def test_copyprop_chains_and_merge_chains():
    blocks = {
        "A": Block("A", [Let("t1", Var("A")), Let("t2", Var("t1"))], Goto("B")),
        "B": Block("B", [Let("X", Var("t2"))], Return((Var("X"),))),
    }
    proc = Proc("f", (0,), (1,), blocks, "A")
    ssa.merge_chains(proc)
    assert list(proc.blocks) == ["A"]
    ssa.to_ssa(proc)
    ssa.copyprop(proc)
    ssa.dce(proc)
    # the whole copy chain collapses onto the parameter itself
    assert proc.blocks["A"].stmts == []
    assert proc.blocks["A"].term.vals[0].n.split("#")[0] == "A"
    prog = ir.Tuneprog(procs={"f": proc})
    assert Interp(prog, Machine(bytes(0x10000))).run("f", [0x42]) == (0x42,)


def test_split_critical_breaks_multi_way_to_multi_entry_edges():
    blocks = {
        "A": Block("A", [], If(Bin("==", Var("C"), Const(1)), "M", "B")),
        "B": Block("B", [], Goto("M")),
        "M": Block("M", [], Return(())),
    }
    proc = Proc("f", (8,), (), blocks, "A")
    ssa.split_critical(proc)
    assert len(proc.blocks) == 4
    assert proc.blocks["A"].term.t == "A$M" and proc.blocks["A$M"].term.to == "M"


def test_unreachable_blocks_are_pruned():
    blocks = {
        "A": Block("A", [], Return(())),
        "dead": Block("dead", [Let("A", Const(1))], Return(())),
    }
    proc = Proc("f", (), (), blocks, "A")
    ssa.prune(proc)
    assert list(proc.blocks) == ["A"]


@pytest.mark.parametrize(
    "name,src",
    [
        (
            "loop",
            (
                "init: RTS",
                "play: LDX #$02",
                "lp: TXA",
                "STA $D400",
                "DEX",
                "BPL lp",
                "RTS",
            ),
        ),
        (
            "call_and_tail",
            (
                "init: RTS",
                "play: JSR sub",
                "JSR two",
                "RTS",
                "sub: LDA #$01",
                "JMP two",
                "two: STA $D401",
                "RTS",
            ),
        ),
        (
            "variant_switch",
            (
                "init: LDA #$60",
                "STA gate",
                "RTS",
                "play: LDA cnt",
                "AND #$01",
                "BEQ open",
                "LDA #$60",
                "JMP set",
                "open: LDA #$A9",
                "set: STA gate",
                "JSR sub",
                "INC cnt",
                "RTS",
                "sub: LDA #$07",
                "STA $D400",
                "gate: LDA #$00",
                "STA $D401",
                "RTS",
                "cnt: BRK",
            ),
        ),
        (
            "indexed_table",
            (
                "init: RTS",
                "play: LDY cnt",
                "LDA tab,Y",
                "STA $D400",
                "INC cnt",
                "LDA cnt",
                "AND #$03",
                "STA cnt",
                "RTS",
                "cnt: BRK",
                "tab: BRK",
                "BRK",
                "BRK",
                "BRK",
            ),
        ),
        (
            "rts_trick",
            (
                "init: RTS",
                "play: LDA #>back-1",
                "PHA",
                "LDA #<back-1",
                "PHA",
                "JMP trick",
                "trick: RTS",
                "back: LDA #$09",
                "STA $D402",
                "RTS",
            ),
        ),
    ],
)
def test_snippet_programs_verify_before_and_after_s4(name, src):
    code = asm(PLAY, *src)
    for s4 in (False, True):
        T, prog = tuneprog(code, calls=6, s4=s4)
        v = verify(prog, T, calls=6)
        assert v.div is None, (name, s4, v.div)
        assert v.call == 6
