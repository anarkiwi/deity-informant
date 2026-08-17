"""S6 frames: pushes and pops as values, and the flag byte a PHP/PLP round trip packs."""

import re

from deity_informant.tuneprog import frame, idioms, live, pipeline, printer, structure
from deity_informant.tuneprog.ir import Let, Var

from _asm import asm
from _prog import PLAY, tuneprog


def _text(code, calls=6, **kw):
    """The printed tuneprog of a snippet, through the whole presentation stack."""
    _T, prog = tuneprog(code, calls=calls, s4=True, **kw)
    view, st, names = pipeline.present(prog)
    return printer.render(view, st, names, pcs=False)


def _view(code, calls=6, **kw):
    """The presentation copy after the frame pass alone."""
    _T, prog = tuneprog(code, calls=calls, s4=True, **kw)
    exits = frame.deltas(prog)
    view = structure.view(prog, live.needed(prog)[0])
    return view, frame.frames(view, exits)


def _stmts(view):
    return [s for p in view.procs.values() for b in p.blocks.values() for s in b.stmts]


def test_a_push_and_its_pop_are_one_value_across_a_call():
    # the callee's frame lies below the caller's slot, so the call cannot touch it
    code = asm(
        PLAY,
        "init: LDA #$00",
        "STA cnt",
        "RTS",
        "play: LDA cnt",
        "PHA",
        "JSR work",
        "PLA",
        "STA $D400",
        "INC cnt",
        "RTS",
        "work: LDA #$07",
        "STA $D404",
        "RTS",
        "cnt: BRK",
    )
    doc = _text(code)
    assert not re.search(r"\bsp\d*\b", doc), doc
    assert "saved = " in doc and "sid[0].freq_lo = saved" in doc


def test_two_pushes_one_pop_reads_is_one_value():
    # the pop is a phi: both arms wrote the slot, so both name the same value
    code = asm(
        PLAY,
        "init: LDA #$00",
        "STA cnt",
        "RTS",
        "play: LDA cnt",
        "AND #$03",
        "BEQ zero",
        "LDA #$11",
        "PHA",
        "JMP join",
        "zero: LDA #$22",
        "PHA",
        "join: PLA",
        "STA $D400",
        "INC cnt",
        "RTS",
        "cnt: BRK",
    )
    view, slots = _view(code)
    assert slots == 1
    names = {s.n for s in _stmts(view) if type(s) is Let and s.n.startswith("$saved")}
    assert len(names) == 1  # one name, two definitions
    doc = _text(code)
    assert not re.search(r"\bsp\d*\b", doc) and "sid[0].freq_lo = saved" in doc


def test_two_unrelated_slots_do_not_share_a_name():
    code = asm(
        PLAY,
        "init: LDA #$00",
        "STA cnt",
        "RTS",
        "play: LDA cnt",
        "PHA",
        "PLA",
        "STA $D400",
        "LDA #$09",
        "PHA",
        "PLA",
        "STA $D404",
        "INC cnt",
        "RTS",
        "cnt: BRK",
    )
    view, slots = _view(code)
    assert slots == 2
    names = {s.n for s in _stmts(view) if type(s) is Let and s.n.startswith("$saved")}
    assert len(names) == 2


def test_a_push_inside_a_loop_keeps_the_stack_pointer():
    # a scratch *area*: the pointer is not a constant offset, so no slot is named
    code = asm(
        PLAY,
        "init: LDA #$00",
        "STA cnt",
        "RTS",
        "play: LDX #$02",
        "lp: TXA",
        "PHA",
        "DEX",
        "BPL lp",
        "PLA",
        "STA $D400",
        "PLA",
        "PLA",
        "INC cnt",
        "RTS",
        "cnt: BRK",
    )
    view, slots = _view(code)
    assert slots == 0
    assert "sp" in _text(code)


def test_a_frame_another_procedure_reads_keeps_its_push():
    # `TSX; LDA $0103,X` reads the caller's slot: no push in the program may go
    code = asm(
        PLAY,
        "init: LDA #$00",
        "STA cnt",
        "RTS",
        "play: LDA cnt",
        "PHA",
        "JSR peek",
        "PLA",
        "INC cnt",
        "RTS",
        "peek: TSX",
        "LDA $0103,X",
        "STA $D400",
        "RTS",
        "cnt: BRK",
    )
    doc = _text(code)
    assert "freq_lo = counter" in doc and "sid[0].freq_lo = freq_lo" in doc


def test_a_php_plp_round_trip_leaves_the_carry_and_drops_the_flag_byte():
    # the anatomy's 11-bit cutoff idiom: PHP over an intervening AND, PLP for the ADC
    code = asm(
        PLAY,
        "init: LDA #$00",
        "STA cnt",
        "STA hi",
        "RTS",
        "play: LDA cnt",
        "CLC",
        "ADC #$40",
        "PHP",
        "AND #$07",
        "STA $D415",
        "LDA hi",
        "PLP",
        "ADC #$00",
        "STA hi",
        "STA $D416",
        "INC cnt",
        "RTS",
        "hi: BRK",
        "cnt: BRK",
    )
    doc = _text(code)
    assert not re.search(r"\bsp\d*\b", doc), doc
    assert not re.search(r"\b[icdvnz]\d*\b = ", doc), doc  # no flag byte, no flag copies
    assert re.search(r"\$?saved_b0 = carry\(", doc), doc
    assert "cutoff_hi += saved_b0" in doc, doc


def test_a_bit_of_a_packed_value_folds_to_the_value_that_packed_it():
    defs = {"p": [_pack()]}
    assert idioms.bit(Var("p"), 2, defs) == Var("I")
    assert idioms.bit(Var("p"), 0, defs) == Var("C")
    assert idioms.bit(Var("p"), 5, defs).v == 1  # the unused bit PHP always sets
    assert idioms.bit(Var("x"), 0, {}) is None


def _pack():
    """The byte ``PHP`` pushes: ``$30 | C | Z<<1 | I<<2 | D<<3 | V<<6 | N<<7``."""
    from deity_informant.tuneprog.ir import Bin, Const

    out = Const(0x30, 1)
    for name, sh in (("C", 0), ("Z", 1), ("I", 2), ("D", 3), ("V", 6), ("N", 7)):
        f = Var(name, 1)
        out = Bin("|", out, f if not sh else Bin("<<", f, Const(sh, 1), 1), 1)
    return out
