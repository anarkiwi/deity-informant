"""Adverse shapes the lift must refuse, and the record a wrong lift would move.

Tripwires for the capabilities the residue wants next -- a reaching-definition
query with joins, provenance keyed by e-class, an index range lattice -- so a
too-eager version fails here in seconds rather than in a corpus sweep.
"""

from deity_informant import framefuse as FF
from deity_informant import frameprog
from deity_informant import frameval
import _fuzzgen as G

from test_frameprog import _fuzz_model

LO, HI = 0x10, 0x11
OUT = 0xD404
SIDS = tuple(G.SID + k for k in range(0x19))


def _prog(name, asm, data=(), outs=(OUT, OUT + 1), frames=8):
    """``(model, program, text)``; the gate must hold on the shape as lifted."""
    player = G.Player(
        name, G.ORG, asm.assemble(), set(outs), {"indexed"}, data=dict(data), frames=frames
    )
    model = _fuzz_model(player)
    prog = frameprog.program(model)
    assert frameval.gate_fp(model, 8, prog) is None
    return model, prog, frameprog.dumps(prog)


def _lane_line(text):
    """The one indexed lane store in the emitted text; asserts there is one.

    Unwidened, it rides the ``sid.regNN`` byte view (docs/frameprog.md 7.7 (5));
    widened, it names the register at ``:2``. Reading the line rather than a fixed
    spelling keeps the check honest when the index folds to a constant."""
    hits = ("sid.reg[", "sid.v1.freq_lo[")
    lines = [ln for ln in text.splitlines() if not ln.lstrip().startswith(";")]
    got = [ln.strip() for ln in lines if any(h in ln for h in hits)]
    assert len(got) == 1, "expected exactly one indexed lane store, got %r" % (got,)
    return got[0]


def _hand(stmts, cells):
    """A one-procedure frame program over a seeded image."""
    mem0 = bytearray(0x10000)
    for addr, v in cells.items():
        mem0[addr] = v
    procs = [(0x1000, [], [], list(stmts) + [("ret", False)])]
    return frameprog.FrameProgram(0x1000, 0x0F00, procs=procs, mem0=mem0)


# ---- a join must meet the arms, not pick one -------------------------------------
def test_two_arms_setting_the_index_differently_must_not_widen():
    """One arm lane-aligned and one not: taking either arm widens onto freq hi.

    The tripwire for a reaching-definition query with joins -- the meet is
    ``{$00, $01}``, and ``$01`` is freq hi, so the word would straddle pw lo."""
    a = G.Asm(G.ORG)
    a.i("DEC", "abs", G.CNT).i("BNE", "rel", ("L", "odd"))
    a.i("LDY", "imm", 0x00).i("JMP", "abs", ("L", "done"))
    a.label("odd").i("LDY", "imm", 0x01)
    a.label("done")
    a.i("LDA", "abs", G.TBL).i("STA", "absy", G.SID).i("RTS")
    _m, _p, text = _prog("two_arms", a, {G.CNT: 0x02, G.TBL: 0x42}, SIDS)
    assert ":2" not in _lane_line(text)


def test_an_index_the_loop_body_rebinds_must_not_widen():
    """A back edge re-reads what the body wrote, so the entry value is not in force."""
    a = G.Asm(G.ORG)
    a.i("LDX", "imm", 1).i("LDY", "imm", 0x00).label("lp")
    a.i("LDA", "absx", G.TBL).i("STA", "absy", G.SID)
    a.i("LDY", "imm", 0x01)
    a.i("DEX").i("BPL", "rel", ("L", "lp")).i("RTS")
    _m, _p, text = _prog("loop_rebind", a, {G.TBL: 0x30, G.TBL + 1: 0x31}, SIDS)
    assert ":2" not in _lane_line(text)


def test_an_index_table_entry_outside_the_register_file_must_not_widen():
    """``$20`` is past ``$D418``: every reaching value must land on a lo lane."""
    a = G.Asm(G.ORG)
    a.i("LDX", "imm", 1).label("lp")
    a.i("LDY", "absx", G.TBL)
    a.i("LDA", "absx", G.TBL + 4).i("STA", "absy", G.SID)
    a.i("DEX").i("BPL", "rel", ("L", "lp")).i("RTS")
    data = {G.TBL: 0x00, G.TBL + 1: 0x20, G.TBL + 4: 0x30, G.TBL + 5: 0x31}
    _m, _p, text = _prog("idx_off_file", a, data, SIDS)
    assert ":2" not in _lane_line(text)


def test_an_index_read_back_from_a_cell_the_play_code_rewrites_must_not_widen():
    """A spilled index is only the store's value where no later store may reach it."""
    a = G.Asm(G.ORG)
    a.i("LDA", "imm", 0x07).i("STA", "abs", G.CNT)
    a.i("LDA", "abs", G.TBL).i("STA", "abs", G.CNT)
    a.i("LDY", "abs", G.CNT)
    a.i("LDA", "abs", G.TBL + 1).i("STA", "absy", G.SID).i("RTS")
    _m, _p, text = _prog("respilled", a, {G.TBL: 0x01, G.TBL + 1: 0x42}, SIDS)
    assert ":2" not in _lane_line(text)


# ---- forcing a wrong fusion moves the record -------------------------------------
def test_fusing_halves_a_wrapping_store_may_alias_moves_the_record():
    """Wizball's shape forced: the refusal is load-bearing, not decoration.

    The hi half is read through a cell the lo store may reach, so packing the two
    reads the byte the lo store just wrote."""
    deref = ("op", "INT_ADD", (FF._word(0x04), ("const", 1, 2)), 2)
    stmts = [
        ("st", ("const", 0x04, 2), ("mem", ("const", G.TBL, 2), 1)),
        ("st", ("const", 0x05, 2), ("mem", deref, 1)),
        ("st", ("const", G.SID, 2), ("mem", ("const", 0x05, 2), 1)),
    ]
    cells = {G.TBL: 0x40, 0x04: 0x00, 0x05: 0x14, 0x1401: 0x55, 0x1441: 0x66}
    prog = _hand(stmts, cells)
    pair = FF._Pair(0x04, 0x05, "pointer", "hand")
    FF._visit(prog.procs[0][3], pair, False)
    assert pair.hazard == 1 and "may read the first cell" in pair.refusal()
    good = frameval.eval_fp(prog, {}, 1)
    forced = list(stmts)
    forced[0:2] = [("st", ("const", 0x04, 2), FF._pack(stmts[0][2], stmts[1][2], hi_first=False))]
    assert frameval.eval_fp(_hand(forced, cells), {}, 1) != good
