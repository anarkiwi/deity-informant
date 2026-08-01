"""dmoracle: DefMON's sidTAB columns as generic lanes, and what the mapping refuses.

The staging rules are editor-independent once a row is decoded, so most of this
drives them from hand-built rows; the front-end is an optional extra and skips."""

from collections import namedtuple
from pathlib import Path

import pytest

from deity_informant import dmoracle as D
from deity_informant import gtoracle as G

HVSC = Path(__file__).resolve().parent.parent / ".oracle-cache" / "hvsc"
_COLS = ("WGh", "WGl", "AD", "SR", "TR", "AF", "PW", "PS", "RE", "FV", "CP", "ACID")
Row = namedtuple("Row", _COLS, defaults=(None,) * len(_COLS))


def _tables(rows):
    """The generic lanes a list of hand-built rows decomposes into."""
    out = {key: D._lane(rows, col, conv) for col, key, conv in D._COLUMNS}
    out[("pitch", "lo")] = (0x11, 0x22, 0x33)
    out[("pitch", "hi")] = (0x01, 0x02, 0x03)
    return out


def _op(role, voice=0):
    """The `(voice, register, role)` one band operand names."""
    reg = {"wave": 4, "xor": 4, "ad": 5, "sr": 6, "pwhi": 3, "pwlo": 2, "flo": 0, "fhi": 1}[role]
    if role in ("re", "fv"):
        return (None, 0x17 if role == "re" else 0x18, role)
    return (voice, 7 * voice + reg, role)


# ---- 1. one column, one generic lane ---------------------------------------------
def test_each_sidtab_column_becomes_the_generic_lane_it_drives():
    """WGh/AD/SR/PW/RE/FV are a waveform, an ADSR pair, a pulse and two filter bytes."""
    rows = [Row(WGh=0x40, AD=0x18, SR=0xF9, PW=0x84, RE=0xF0, FV=0x10)]
    tab = _tables(rows)
    assert tab[("sidtab", "wave")][0] == 0x41  # the waveform with the gate armed
    assert (tab[("ins", "ad")][0], tab[("ins", "sr")][0]) == (0x18, 0xF9)
    assert (tab[("pw", "hi")][0], tab[("pw", "lo")][0]) == (0x84, 0x80)
    assert (tab[("filt", "res")][0], tab[("filt", "vol")][0]) == (0xF0, 0x1F)


def test_a_column_with_no_override_is_not_a_lane_byte():
    """An unset bitmap bit means "inherit", so the lane holds nothing at that row."""
    tab = _tables([Row(AD=0x18), Row()])
    assert tab[("ins", "ad")] == (0x18, 0)


def test_the_16_bit_cutoff_command_is_two_lanes_not_one_word():
    """ACID is a step byte and a direction byte, so it decomposes into two lanes."""
    tab = _tables([Row(ACID=0x40C0)])
    assert (tab[("filt", "acid")][0], tab[("filt", "acidhi")][0]) == (0xC0, 0x40)


# ---- 2. staging: a lane at the row the cascade fetched ----------------------------
def test_a_staged_byte_is_the_lane_at_the_row_the_cascade_just_fetched():
    rows = [Row(), Row(AD=0x18)]
    tab, state = _tables(rows), {}
    D._stage(state, tab, rows, _op("ad"), 0x18, (1, None))
    assert D._cell(state, tab, rows, 5, 0x18) == G.Cell("select", ("ins", "ad"), 1, 0x18, 0)


def test_a_held_row_still_names_the_lane_after_the_cascade_moves_on():
    """The band re-emits every frame; the row the voice holds is what named it."""
    rows = [Row(), Row(AD=0x18)]
    tab, state = _tables(rows), {}
    D._stage(state, tab, rows, _op("ad"), 0x18, (1, None))
    D._stage(state, tab, rows, _op("ad"), 0x18, (0, None))
    assert D._cell(state, tab, rows, 5, 0x18).row == 1


def test_a_byte_no_fetched_row_holds_stays_raw():
    rows = [Row(AD=0x18)]
    tab, state = _tables(rows), {}
    D._stage(state, tab, rows, _op("ad"), 0x99, (0, None))
    assert D._cell(state, tab, rows, 5, 0x99) == G.Cell("raw", ("ghost", "adsr"), 0, 0x99, 0)


def test_a_register_the_song_never_programs_is_the_drivers_ghost():
    rows = [Row()]
    assert D._cell({}, _tables(rows), rows, 5, 0x00).lane == ("ghost", "held")


# ---- 3. the ctrl plane: two columns, one register ---------------------------------
def _ctrl(rows, wave, mask, emitted):
    tab, state = _tables(rows), {}
    D._stage(state, tab, rows, _op("wave"), wave, (0, None))
    D._stage(state, tab, rows, _op("xor"), mask, (0, None))
    return D._cell(state, tab, rows, 4, emitted)


def test_the_gate_bit_of_the_xor_mask_is_a_gate_image_of_the_waveform_lane():
    """`WGh ^ 1` is the lane and `WGh & ~1` its gate image, as docs/tracker.md §5 reads it."""
    rows = [Row(WGh=0x40, WGl=0x01), Row()]
    assert _ctrl(rows, 0x40, 0x01, 0x41) == G.Cell("ctrl", ("sidtab", "wave"), 0, 0x41, 0)
    assert _ctrl(rows, 0x40, 0x00, 0x40).row == len(rows)  # the gate image, one table on


def test_an_xor_mask_beyond_the_gate_bit_is_a_second_generator_and_is_refused():
    """`WGh ^ WGl` with a waveform bit moving is two generators on one plane."""
    rows = [Row(WGh=0x40, WGl=0x10)]
    assert _ctrl(rows, 0x40, 0x10, 0x50) == G.Cell("raw", ("xor", "mask"), 0, 0x50, 0)


# ---- 4. what else the mapping refuses --------------------------------------------
def test_the_resonance_byte_ored_with_a_routing_mask_is_refused():
    rows = [Row(RE=0xF0)]
    tab, state = _tables(rows), {}
    D._stage(state, tab, rows, (None, 0x17, "re"), 0xF1, (0, None))
    assert D._cell(state, tab, rows, 0x17, 0xF1).lane == ("res", "routing")


def test_the_mode_nibble_ored_with_a_constant_volume_is_still_a_declared_byte():
    """$18's second generator is the driver's own `ORA #$0F`, so the lane survives."""
    rows = [Row(FV=0x10)]
    tab, state = _tables(rows), {}
    D._stage(state, tab, rows, (None, 0x18, "fv"), 0x10, (0, None))
    assert D._cell(state, tab, rows, 0x18, 0x1F) == G.Cell("select", ("filt", "vol"), 0, 0x1F, 0)


def test_the_cutoff_slide_is_refused_whole():
    """$16 is a byte view of an accumulator whose step is itself accumulated."""
    rows = [Row(CP=0x04)]
    assert D._cell({}, _tables(rows), rows, 0x16, 0x30).lane == ("cutoff", "slide")


def test_a_pulse_row_carrying_a_sweep_depth_is_a_ramp_over_the_declared_step():
    rows = [Row(PW=0x80, PS=0x03)]
    tab, state = _tables(rows), {}
    D._stage(state, tab, rows, _op("pwlo"), 0x80, (0, None))
    assert D._cell(state, tab, rows, 2, 0x80) == G.Cell("ramp", ("pw", "step"), 0, 0x80, 3)


def test_a_freq_byte_the_note_table_does_not_hold_is_a_slide():
    rows = [Row()]
    tab, state = _tables(rows), {}
    D._stage(state, tab, rows, _op("flo"), 0x23, (None, 0))
    assert D._cell(state, tab, rows, 0, 0x23).lane == ("slide", "detune")
    D._stage(state, tab, rows, _op("flo"), 0x11, (None, 0))
    assert D._cell(state, tab, rows, 0, 0x11) == G.Cell("select", ("pitch", "lo"), 0, 0x11, 0)


# ---- 5. the mapped graph passes the same law -------------------------------------
def test_the_mapped_lanes_build_a_graph_that_passes_the_law():
    """One vocabulary, one builder: DefMON's cells go through `gtoracle` unchanged."""
    rows = [Row(WGh=0x40, WGl=0x01, AD=0x18, SR=0xF9)]
    tab = _tables(rows)
    cells = {
        5: G.Cell("select", ("ins", "ad"), 0, 0x18, 0),
        6: G.Cell("select", ("ins", "sr"), 0, 0xF9, 0),
        4: G.Cell("ctrl", ("sidtab", "wave"), 0, 0x41, 0),
    }
    native = G.Native("defmon", tab, [cells] * 3, [(5, 6, 4)] * 3, [], [], [], {})
    _g, rep = G.strict(native)
    assert rep.divergence is None and rep.coverage.interp == 9
    assert rep.coverage.classes["ctrl"]["lane"] == 3 and rep.coverage.classes["ad"]["lane"] == 3


# ---- 6. the editor front-end (an optional extra) ---------------------------------
DM = pytest.mark.skipif(not D.dm_available(), reason="pydefmon not installed")


@DM
def test_a_replay_that_is_not_defmons_is_refused_rather_than_mis_read():
    """Every site is verified against the opcode that must be there."""
    assert D.dm_sites(bytearray(0x10000), 0x1000) is None


@DM
def test_a_song_written_here_decomposes_into_the_same_generic_lanes():
    """The editor's own row parser, mapped: a bitmap-packed row becomes lanes."""
    # pylint: disable=import-error,import-outside-toplevel
    from pydefmon import NOTE_PITCH_HI, NOTE_PITCH_LO, DefmonSong, SidtabRow

    song = DefmonSong()
    packed = SidtabRow.pack({"WGh": 0x40, "AD": 0x18, "SR": 0xF9, "PW": 0x84, "FV": 0x10})
    song.sidtab_region[3 * 15 : 4 * 15] = packed
    song.set_dl(3, 0x05)
    song.set_jp(4, target=3)
    song.arranger_v1[0] = 7
    mem = bytearray(0x10000)
    mem[0x2000 : 0x2000 + 128] = NOTE_PITCH_LO
    mem[0x2100 : 0x2100 + 128] = NOTE_PITCH_HI
    sites = D.Sites(0, 0, (), {}, 0, (0x2000, 0x2100), 0x2200, 0x2300)
    tab, rows = D.dm_tables(song, sites, mem)
    assert rows[3].WGh == 0x40 and tab[("sidtab", "wave")][3] == 0x41
    assert (tab[("ins", "ad")][3], tab[("pw", "lo")][3], tab[("filt", "vol")][3]) == (
        0x18,
        0x80,
        0x1F,
    )
    assert tab[("clock", "dl")][3] == 0x05 and tab[("clock", "jp")][4] == 3
    assert tab[("arr", "v1")][0] == 7 and tab[("pitch", "lo")] == tuple(NOTE_PITCH_LO)


@DM
@pytest.mark.parametrize("rel", ["MUSICIANS/G/Goto80/Automatas.sid"])
def test_a_cached_defmon_tune_reproduces_its_own_sid_writes_from_the_song_alone(rel):
    """The strict oracle against the tracker's law: the composer's data, verbatim."""
    path = HVSC / rel
    if not path.is_file():
        pytest.skip("tune not cached")
    got = D.dm_decompile(path, 60)
    assert got is not None
    native, records, fetched = got
    assert native.editor == "defmon"
    assert {("pitch", "lo"), ("ins", "ad"), ("sidtab", "wave"), ("clock", "dl")} <= set(
        native.tables
    )
    _g, rep = G.strict(native, records, 0)
    assert rep.divergence is None and rep.matched == rep.frames
    assert rep.coverage.classes["freq"]["lane"] > 0
    assert rep.coverage.classes["ad"]["imm"] == 0
    assert any(fetched) and native.structure["patterns"] > 0


# ---- 7. the arrangement: the pattern's own column names the row --------------------
def _walk(at=None):
    """The per-voice pattern position the address bus names."""
    return {"at": at or {}, "refused": {}, "steps": set(), "rows": set(), "of": {}}


def _patt(name=None, at=None):
    """A flat 4096-entry pattern column table, with one column's entries set."""
    return {
        key: tuple((at or {}).get(i, 0) if key[1] == name else 0 for i in range(4096))
        for key in (("patt", "note"), ("patt", "slot_a"), ("patt", "slot_b"))
    }


def test_a_pattern_events_note_column_names_the_pitch_tables_row():
    """One index link: the pattern names the note, the note table names the byte."""
    tab = _tables([Row(WGh=0x40)])
    tab.update(_patt("note", {32: 2}))
    got = D._dm_src({("row", 0): 0}, tab, _walk({0: (1, 0)}), 0, ("pitch", "lo"), 2)
    assert got == (("patt", "note"), 32, (0, 1), 0)


def test_a_relative_tr_shifts_the_note_index_by_its_own_amount():
    """`TR` with bit 7 clear is added to the note the pattern named: `SELECT[rel]`'s shift."""
    tab = _tables([Row(TR=0x05)])
    tab.update(_patt("note", {0: 2}))
    walk = _walk({0: (0, 0)})
    got = D._dm_src({("row", 0): 0}, tab, walk, 0, ("pitch", "lo"), 7)
    assert got == (("patt", "note"), 0, (0, 0), 5)  # note 2 + TR 5 = index 7
    assert D._dm_src({("row", 0): 0}, tab, walk, 0, ("pitch", "lo"), 8) is None
    assert walk["refused"] == {"arpeggio": 1}  # a row the shifted column does not name


def test_a_slot_column_names_the_sidtab_row_an_instrument_lane_reads():
    """GATE_A/GATE_B arm a sidTAB row, the index link an instrument lane needs."""
    tab = _tables([Row(AD=0x18), Row(AD=0x22)])
    tab.update(_patt("slot_a", {0: 1}))
    got = D._dm_src({}, tab, _walk({0: (0, 0)}), 0, ("ins", "ad"), 1)
    assert got == (("patt", "slot_a"), 0, (0, 0), 0)


def test_a_pattern_position_the_bus_never_named_is_refused_not_guessed():
    """No arranger read, no pattern read, no arrangement — and the refusal is priced."""
    tab = _tables([Row(AD=0x18)])
    tab.update(_patt())
    walk = _walk()
    assert D._dm_src({}, tab, walk, 0, ("ins", "ad"), 0) is None
    assert walk["refused"] == {"no_pattern_row": 1}


@DM
def test_the_packed_pattern_stream_is_rebuilt_from_the_flag_and_its_gated_columns():
    """The packer stores an event as its flag plus only the columns its gates arm."""
    # pylint: disable=import-error,import-outside-toplevel
    from pydefmon import DefmonSong, PatternEvent

    song = DefmonSong()
    events = [PatternEvent.note_on(0x40, slot_a=3, duration=2), PatternEvent.alt_end()]
    song.set_pattern_events(0, events + [PatternEvent.delay(1) for _e in range(30)])
    mem = bytearray(0x10000)
    mem[0x3000:0x3004] = bytes([events[0].flag, 3, 0x40, events[1].flag])
    got = D.dm_pattern_map(song, mem, 0x3000)
    assert {got[0x3000], got[0x3002]} == {(0, 0)} and got[0x3003] == (0, 1)
    assert 0x3004 not in got  # the stream ends where the next pattern has no end event


@DM
@pytest.mark.parametrize("rel", ["MUSICIANS/G/Goto80/Automatas.sid"])
def test_a_cached_defmon_tune_reads_its_tables_at_rows_its_patterns_name(rel):
    """The arrangement off the address bus: an arranger read and a pattern read."""
    path = HVSC / rel
    if not path.is_file():
        pytest.skip("tune not cached")
    got = D.dm_decompile(path, 60)
    assert got is not None
    native, records, _fetched = got
    graph, rep = G.strict(native, records, 0)
    assert rep.divergence is None
    assert rep.arrangement["patterns"] > 0 and rep.arrangement["orderlist_entries"] > 0
    assert G.index_nodes(graph)[0] > 0
    assert rep.arrangement["loop_at_end"] + rep.arrangement["loop_elsewhere"] > 0
