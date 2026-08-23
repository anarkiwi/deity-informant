"""S6 16-bit views: the cell pair, the byte shapes over it, and the pass that folds them."""

from deity_informant.tuneprog import halves
from deity_informant.tuneprog.ir import Bin, Const, Load, R16, Store, W16

from _asm import asm
from _prog import PLAY, printed, proc_body, stmts, tuneprog

ZP = {a: 0 for a in range(0xFB, 0xFF)}
LO, HI = 0x0010, 0x0011


def _ld(addr, rid, cls="ram"):
    return Load(cls, Const(addr, 2), 1, addr, addr, rid)


def _at(addr, rid, v, cls="ram"):
    return Store(cls, Const(addr, 2), v, 1, addr, addr, rid)


PAIR = ((0, LO), (1, HI))
L, H = _ld(LO, 0), _ld(HI, 1)
WORD = R16(PAIR[0], PAIR[1], Const(LO, 2))


def _carry(x, y):
    return Bin("carry", x, y, 1)


# ---- the cell pair -----------------------------------------------------------
def test_two_accesses_at_one_index_and_two_bases_are_a_pair():
    assert halves.cells(L, H) == PAIR


def test_two_accesses_at_one_address_are_not_a_pair():
    assert halves.cells(L, _ld(LO, 0)) is None


def test_two_accesses_at_different_indices_are_not_a_pair():
    a = Load("ram", Bin("+", Const(LO, 2), Load("ram", Const(3, 2), 1, 3, 3, 5), 2), 1, LO, LO, 0)
    b = Load("ram", Bin("+", Const(HI, 2), Const(1, 1), 2), 1, HI, HI, 1)
    assert halves.cells(a, b) is None


def test_a_pair_straddling_the_io_band_is_refused():
    reg = Load("io", Const(0xD400, 2), 1, 0xD400, 0xD400, 9)
    assert halves.cells(L, reg) is None and halves.cells(reg, L) is None


# ---- the byte shapes ---------------------------------------------------------
def test_a_carry_chain_over_a_pair_is_one_16_bit_add():
    vlo = Bin("+", L, Const(0x34), 1)
    vhi = Bin("+", Bin("+", H, Const(0x12), 1), _carry(L, Const(0x34)), 1)
    got, flags = halves.value(PAIR, vlo, vhi)
    assert got == Bin("+", WORD, Const(0x1234, 2), 2)
    assert halves.same(flags[0], _carry(L, Const(0x34)))


def test_an_add_whose_high_half_takes_no_operand_widens_the_low_one():
    vlo = Bin("+", L, _ld(4, 6), 1)
    vhi = Bin("+", H, _carry(L, _ld(4, 6)), 1)
    assert halves.value(PAIR, vlo, vhi)[0] == Bin("+", WORD, _ld(4, 6), 2)


def test_a_high_half_that_does_not_take_the_low_half_s_carry_is_no_word():
    vlo = Bin("+", L, Const(0x34), 1)
    vhi = Bin("+", Bin("+", H, Const(0x12), 1), _carry(L, Const(0x35)), 1)
    assert halves.value(PAIR, vlo, vhi) is None


def test_a_borrow_chain_over_a_pair_is_one_16_bit_subtract():
    vlo = Bin("-", L, Const(0x34), 1)
    borrow = Bin("<=", Const(0x34), L, 1)
    vhi = Bin("-", H, Bin("+", Const(0x12), Bin("-", Const(1), borrow, 1), 1), 1)
    assert halves.value(PAIR, vlo, vhi)[0] == Bin("-", WORD, Const(0x1234, 2), 2)


def test_a_borrow_the_high_half_does_not_continue_is_no_word():
    vlo = Bin("-", L, Const(0x34), 1)
    vhi = Bin("-", H, Bin("+", Const(0x12), Bin("-", Const(1), Const(0), 1), 1), 1)
    assert halves.value(PAIR, vlo, vhi) is None


def test_a_borrow_in_prints_as_the_negated_compare():
    """``1 - (a <= b)`` inside a borrow is ``b < a``, not a subtraction of a flag."""
    flag = Bin("<=", _ld(3, 5), _ld(4, 6), 1)
    vlo = Bin("-", L, Bin("+", Const(0x34), Bin("-", Const(1), flag, 1), 1), 1)
    out = Bin("+", Const(0x34), Bin("-", Const(1), flag, 1), 1)
    vhi = Bin("-", H, Bin("+", Const(0x12), Bin("-", Const(1), Bin("<=", out, L, 1), 1), 1), 1)
    got = halves.value(PAIR, vlo, vhi)[0]
    assert got.b.b == Bin("<", _ld(4, 6), _ld(3, 5), 1)


def test_one_minus_a_compare_is_the_negated_compare_and_nothing_else_is():
    c = Bin("<=", L, H, 1)
    assert halves.zerofold(Bin("-", Const(1), c, 1)) == Bin("<", H, L, 1)
    assert halves.zerofold(Bin("-", Const(1), L, 1)) == Bin("-", Const(1), L, 1)
    assert halves._notc(c) == Bin("<", H, L, 1)
    assert halves._notc(L) == Bin("-", Const(1), L, 1)


def test_a_borrow_chain_reads_the_folded_spelling_of_its_borrow_in():
    vlo = Bin("-", L, Const(0x34), 1)
    vhi = Bin("-", H, Bin("+", Const(0x12), Bin("<", L, Const(0x34), 1), 1), 1)
    assert halves.value(PAIR, vlo, vhi)[0] == Bin("-", WORD, Const(0x1234, 2), 2)


def test_a_rotate_through_both_halves_is_one_16_bit_shift():
    vhi = Bin(">>", H, Const(1), 1)
    vlo = Bin("|", Bin(">>", L, Const(1), 1), Bin("<<", Bin("&", H, Const(1), 1), Const(7), 1), 1)
    assert halves.value(PAIR, vlo, vhi)[0] == Bin(">>", WORD, Const(1, 1), 2)


def test_a_shift_of_one_half_alone_is_no_word():
    assert halves.value(PAIR, Bin(">>", L, Const(1), 1), Bin(">>", H, Const(1), 1)) is None


def test_a_high_byte_shifted_over_a_low_one_is_a_16_bit_read():
    assert halves.read(Bin("|", Bin("<<", H, Const(8), 2), L, 2)) == WORD
    assert halves.read(Bin("|", L, Bin("<<", H, Const(8), 2), 2)) == WORD


def test_a_high_byte_shifted_over_a_byte_of_its_own_cell_is_not_a_read():
    e = Bin("|", Bin("<<", H, Const(8), 2), _ld(HI, 1), 2)
    assert halves.read(e) is e


# ---- the pass ----------------------------------------------------------------
def _chain(op, clc="CLC"):
    return asm(
        PLAY,
        "init: LDA #$00",
        "STA $FB",
        "STA $FC",
        "RTS",
        "play: " + clc,
        "LDA $FB",
        op + " #$34",
        "STA $FB",
        "LDA $FC",
        op + " #$12",
        "STA $FC",
        "LDA $FC",
        "STA $D401",
        "RTS",
    )


def test_an_add_chain_prints_as_one_16_bit_statement():
    body = "\n".join(proc_body(printed(_chain("ADC"), data=ZP), "tick"))
    assert "acc += $1234" in body and "carry(" not in body


def test_a_subtract_chain_prints_as_one_16_bit_statement():
    body = "\n".join(proc_body(printed(_chain("SBC", "SEC"), data=ZP), "tick"))
    assert "acc -= $1234" in body and "carry(" not in body


def test_an_increment_over_a_branch_is_one_16_bit_increment():
    code = asm(
        PLAY,
        "init: LDA #$FD",
        "STA $FB",
        "LDA #$00",
        "STA $FC",
        "RTS",
        "play: INC $FB",
        "BNE skip",
        "INC $FC",
        "skip: LDA $FC",
        "STA $D401",
        "RTS",
    )
    body = "\n".join(proc_body(printed(code, calls=8, data=ZP), "tick"))
    assert "acc += 1" in body and "if " not in body


def test_a_rotate_across_two_cells_prints_as_one_shift():
    code = asm(
        PLAY,
        "init: LDA #$FF",
        "STA $FB",
        "STA $FC",
        "RTS",
        "play: LSR $FC",
        "ROR $FB",
        "LDA $FC",
        "STA $D401",
        "RTS",
    )
    body = "\n".join(proc_body(printed(code, data=ZP), "tick"))
    assert "acc >>= 1" in body


def test_a_pointer_pair_reads_the_table_it_addresses():
    code = asm(
        PLAY,
        "init: LDA #<tab",
        "STA $FB",
        "LDA #>tab",
        "STA $FC",
        "RTS",
        "play: LDY cnt",
        "LDA ($FB),Y",
        "STA $D401",
        "INC cnt",
        "RTS",
        "cnt: BRK",
        "tab: BRK",
        "BRK",
        "BRK",
        "BRK",
    )
    doc = printed(code, calls=4, data=ZP)
    assert "<< 8)" not in doc and "[ptr" in doc


def test_the_fold_leaves_one_word_statement_where_two_half_stores_were():
    _T, prog = tuneprog(_chain("ADC"), calls=6, s4=True, data=ZP)
    before = [s for s in stmts(prog) if type(s) is Store and s.r >= 0 and s.cls != "io"]
    body = proc_body(printed(_chain("ADC"), data=ZP), "tick")
    assert len(before) > 2 and not [l for l in body if "acc_lo" in l]


def test_the_word_view_is_a_pair_of_cells():
    assert isinstance(W16(PAIR[0], PAIR[1], Const(LO, 2), WORD).lo, tuple)
    assert _at(LO, 0, Const(0)).r == 0


# ---- the SID's own 16-bit registers ------------------------------------------
def test_the_two_halves_of_a_sid_register_name_it():
    assert halves.register(((9, 0xD400), (9, 0xD401))) == "freq"
    assert halves.register(((9, 0xD409), (9, 0xD40A))) == "pw"
    assert halves.register(((9, 0xD415), (9, 0xD416))) == "cutoff"


def test_the_halves_of_a_sid_register_in_the_wrong_order_name_nothing():
    assert halves.register(((9, 0xD401), (9, 0xD400))) is None


def test_two_registers_of_one_voice_are_not_a_16_bit_register():
    assert halves.register(((9, 0xD405), (9, 0xD406))) is None  # ad, sr


def test_two_voices_low_halves_are_not_a_16_bit_register():
    assert halves.register(((9, 0xD400), (9, 0xD407))) is None


def _sidwrite(order="lohi"):
    """A word the program holds -- a 16-bit counter -- written to voice 0's frequency."""
    hi = ["LDA hi", "STA $D401"]
    lo = ["LDA lo", "STA $D400"]
    return asm(
        PLAY,
        "init: LDA #$00",
        "STA lo",
        "STA hi",
        "RTS",
        "play: CLC",
        "LDA lo",
        "ADC #$01",
        "STA lo",
        "LDA hi",
        "ADC #$00",
        "STA hi",
        *(lo + hi if order == "lohi" else hi + lo),
        "RTS",
        "lo: BRK",
        "hi: BRK",
    )


def _sidbytes(order="lohi", src="tab"):
    """Two cells one index reaches at two bases, written to voice 0's frequency."""
    hi = ["LDA %s+1,Y" % src, "STA $D401"]
    lo = ["LDA %s,Y" % src, "STA $D400"]
    return asm(
        PLAY,
        "init: LDA #$00",
        "STA cnt",
        "RTS",
        "play: LDY cnt",
        *(lo + hi if order == "lohi" else hi + lo),
        "INC cnt",
        "RTS",
        "cnt: BRK",
        "tab: BRK",
        "BRK",
        "BRK",
        "BRK",
    )


def test_one_word_written_to_a_sid_register_is_one_statement():
    doc = printed(_sidwrite(), calls=3)
    body = "\n".join(proc_body(doc, "tick"))
    assert body.count("sid[0].") == 1 and "sid[0].freq = " in body, body
    assert "sid       16-bit registers written lo then hi" in doc, doc


def test_the_write_order_is_stated_once_and_the_odd_one_marked():
    doc = printed(_sidwrite(order="hilo"), calls=3)
    body = "\n".join(proc_body(doc, "tick"))
    assert body.count("sid[0].") == 1 and "# hi then lo" not in body, body
    assert "sid       16-bit registers written hi then lo" in doc, doc


def test_two_bytes_of_no_word_the_program_holds_stay_two_writes():
    """The two cells are a pair no fold named, so the print would have to join them."""
    body = "\n".join(proc_body(printed(_sidbytes(), calls=3), "tick"))
    assert "sid[0].freq_lo = " in body and "sid[0].freq_hi = " in body, body


def test_two_bytes_that_are_no_word_stay_two_writes():
    """The high half is computed, so no word the program holds reaches the pair."""
    code = asm(
        PLAY,
        "init: LDA #$00",
        "STA cnt",
        "RTS",
        "play: LDA cnt",
        "STA $D400",
        "AND #$0F",
        "STA $D401",
        "INC cnt",
        "RTS",
        "cnt: BRK",
    )
    body = "\n".join(proc_body(printed(code, calls=3), "tick"))
    assert "sid[0].freq_lo = " in body and "sid[0].freq_hi = " in body, body
