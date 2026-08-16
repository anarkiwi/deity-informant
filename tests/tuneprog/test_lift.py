"""S2a: residualisation of SMC operand cells, opcode variants, computed control."""

from deity_informant.lifter import lift
from deity_informant.tuneprog.lift import lift_site, lift_trace

from _asm import asm, trace_prog

PLAY = 0x1000


def _loads(ls):
    return {(a, sz) for a, sz in ls.cell_loads}


def _mem_consts(ops):
    """Address constants of every LOAD op (residualised cell reads show up here)."""
    return [ins[0][1] for mn, _out, ins in ops if mn == "LOAD" and ins[0][0] == "c"]


def test_immediate_cell_becomes_a_one_byte_load():
    play = asm(PLAY, "smc: LDA #$00", "STA $D400", "INC $1001", "RTS")
    T, _ = trace_prog({PLAY: play, 0x1020: asm(0x1020, "RTS")}, init=0x1020, play=PLAY, calls=3)
    L = lift_trace(T)
    ls = L[T.site_at(PLAY)[0]]
    assert _loads(ls) == {(0x1001, 1)}
    assert 0x1001 in _mem_consts(ls.ops)
    # the COPY into A now takes the loaded value, not a constant
    copy = [o for o in ls.ops if o[0] == "COPY" and o[1] == ["r", 0, 1]][0]
    assert copy[2][0][0] == "u"


def test_untouched_site_lifts_identically():
    play = asm(PLAY, "LDA #$07", "STA $D400", "RTS")
    T, _ = trace_prog({PLAY: play, 0x1020: asm(0x1020, "RTS")}, init=0x1020, play=PLAY)
    L = lift_trace(T)
    ls = L[T.site_at(PLAY)[0]]
    ref = lift(bytearray(T.image_post_init), PLAY)
    assert ls.ops == ref["ops"] and ls.cell_loads == [] and ls.ctrl_cell is None
    assert ls.src_map == list(range(len(ref["ops"])))


def test_absolute_address_cells_become_a_sixteen_bit_load():
    # both operand bytes of "LDA $2000,Y" are patched: a broadcast pointer.
    play = asm(PLAY, "INC ptr+1", "ptr: LDA $2000,Y", "STA $D400", "RTS")
    T, _ = trace_prog(
        {PLAY: play, 0x1020: asm(0x1020, "RTS")},
        init=0x1020,
        play=PLAY,
        calls=3,
        data={0x2000: 1, 0x2001: 2, 0x2002: 3},
    )
    L = lift_trace(T)
    ls = L[T.site_at(play.labels["ptr"])[0]]
    assert _loads(ls) == {(play.labels["ptr"] + 1, 2)}
    assert ls.mode == "absy"


def test_zero_page_pointer_cell_and_pointer_pairs():
    play = asm(PLAY, "INC ind+1", "ind: LDA ($10),Y", "STA $D400", "RTS")
    init = asm(0x1100, "LDA #$00", "STA $10", "LDA #$20", "STA $11", "STA $12", "RTS")
    T, _ = trace_prog(
        {PLAY: play, 0x1100: init}, init=0x1100, play=PLAY, calls=2, data={0x2000: 0x11}
    )
    L = lift_trace(T)
    ind = L[T.site_at(play.labels["ind"])[0]]
    assert _loads(ind) == {(play.labels["ind"] + 1, 1)}
    assert ind.ptr_pairs and len(ind.ptr_pairs[0]) == 2
    assert ind.ops[ind.ptr_pairs[0][0]][0] == "LOAD"


def test_opcode_cell_yields_one_lifted_variant_per_opcode():
    play = asm(PLAY, "LDA $1010", "EOR #$C9", "STA $1010", "JSR $1010", "RTS")
    T, _ = trace_prog(
        {PLAY: play, 0x1010: asm(0x1010, "LDA #$00", "RTS"), 0x1020: asm(0x1020, "RTS")},
        init=0x1020,
        play=PLAY,
        calls=4,
        data={0x1010: 0xA9},
    )
    L = lift_trace(T)
    keys = T.site_at(0x1010)
    assert len(keys) == 2
    kinds = {L[k].mnemonic for k in keys}
    assert kinds == {"LDA", "RTS"}
    assert {L[k].ctrl[0] for k in keys} == {"next", "rts"}


def test_patched_jump_operand_becomes_computed_control():
    play = asm(PLAY, "jmp1: JMP $1020", "back: RTS")
    body = asm(0x1020, "LDA $1001", "EOR #$06", "STA $1001", "RTS")  # patches the JMP low byte
    T, _ = trace_prog(
        {PLAY: play, 0x1020: body, 0x1030: asm(0x1030, "RTS")}, init=0x1030, play=PLAY, calls=3
    )
    L = lift_trace(T)
    ls = L[T.site_at(PLAY)[0]]
    assert ls.computed_control
    assert ls.ctrl_cell[:2] == (PLAY + 1, 2)
    assert (PLAY + 1, 2) in _loads(ls)


def test_lift_site_accepts_a_bare_site_record():
    play = asm(PLAY, "LDA #$07", "STA $D400", "RTS")
    T, _ = trace_prog({PLAY: play, 0x1020: asm(0x1020, "RTS")}, init=0x1020, play=PLAY)
    key = T.site_at(PLAY)[0]
    ls = lift_site(T.image_post_init, T.sites[key], set())
    assert ls.pc == PLAY and ls.opcode == 0xA9 and ls.length == 2 and ls.cyc == 2
    assert ls.key == (PLAY, 0xA9, (0x07,))
