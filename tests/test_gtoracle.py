"""gtoracle: the native-editor mapping, its law, and what it refuses.

The builder is editor-independent, so most of this drives it from a hand-built
`Native`; the two editor front-ends are optional extras and skip without them."""

from pathlib import Path

import pytest

from deity_informant import framelog as F
from deity_informant import gtoracle as G
from deity_informant import tracker as T

SWM = Path(__file__).resolve().parent.parent / ".oracle-cache" / "swm"
HVSC = Path(__file__).resolve().parent.parent / ".oracle-cache" / "hvsc"


def _native(tables, writes, shape=None, **kw):
    """A `Native` over hand-built cells: the builder never sees an editor."""
    n = len(writes)
    return G.Native(
        kw.get("editor", "test"),
        tables,
        writes,
        shape or [tuple(sorted(w)) for w in writes],
        kw.get("notes", [(0, 0, 0)] * n),
        kw.get("instrs", [(0, 0, 0)] * n),
        kw.get("onsets", [(0, 0, 0)] * n),
        kw.get("structure", {}),
    )


def _sel(lane, row, value):
    return G.Cell("select", lane, row, value, 0)


# ---- 1. the generic vocabulary ---------------------------------------------------
def test_a_lane_read_becomes_a_select_over_the_editors_own_table():
    """A byte of the composer's table at the row its player reached is a SELECT."""
    tables = {("ins", "ad"): (0x11, 0x22, 0x33)}
    writes = [{5: _sel(("ins", "ad"), 1, 0x22)}, {5: _sel(("ins", "ad"), 2, 0x33)}]
    nat = _native(tables, writes)
    graph, rep = G.strict(nat)
    assert rep.coverage.interp == 2 and rep.coverage.residual == 0
    node = [g for g in graph.nodes if g.route == ("plane", 5)][0]
    assert node.transfer == ("SELECT", (0x11, 0x22, 0x33), (1, 2))
    assert rep.coverage.classes["ad"]["lane"] == 2


def test_the_adsr_preamble_is_a_lookup_on_the_note_on_edge_not_a_hard_restart():
    """Both editors' hard restart maps to one generic preamble lane."""
    tables = {("hr", "ad"): (0x0F,), ("ins", "ad"): (0x11,)}
    writes = [{5: _sel(("hr", "ad"), 0, 0x0F)}, {5: _sel(("ins", "ad"), 0, 0x11)}]
    graph, rep = G.strict(_native(tables, writes))
    lanes = {g.transfer[1] for g in graph.nodes if g.route == ("plane", 5)}
    assert lanes == {(0x0F,), (0x11,)} and rep.coverage.interp == 2


def test_a_ctrl_byte_is_the_waveform_lane_read_through_its_gate_image():
    """Waveform and gate are one table plus its images, so every byte is declared."""
    lane = (0x41, 0x11)
    tables = {("wtbl", "left"): lane}
    writes = [
        {4: G.Cell("ctrl", ("wtbl", "left"), 0, 0x41, 0)},
        {4: G.Cell("ctrl", ("wtbl", "left"), 2, 0x40, 0)},
    ]
    graph, rep = G.strict(_native(tables, writes))
    node = [g for g in graph.nodes if g.route == ("plane", 4)][0]
    assert node.transfer[1] == G._ctrl_table(lane)
    assert rep.coverage.classes["ctrl"] == dict.fromkeys(G._CLASSES, 0) | {
        "lane": 1,
        "gate": 1,
    }


# ---- 2. what the mapping refuses -------------------------------------------------
def test_a_sweep_whose_step_the_table_holds_is_a_ramp():
    step = 3
    writes = [{2: G.Cell("ramp", ("ptbl", "right"), 0, 0x10 + step * i, step)} for i in range(4)]
    graph, rep = G.strict(_native({("ptbl", "right"): (step,)}, writes))
    node = [g for g in graph.nodes if g.route == ("plane", 2)][0]
    assert node.transfer == ("RAMP", 0x10, step, 0x100, ())
    assert rep.coverage.classes["pw"] == dict.fromkeys(G._CLASSES, 0) | {
        "ramp": 3,
        "seed": 1,
    }


def _sweep16(words, step, mask_hi=0xFF):
    """Writes for a 16-bit sweep over the pw pair, cells as an editor emits them."""
    return [
        {
            2: G.Cell("ramp16", ("t", "r"), 0, w & 0xFF, step, base=(3, mask_hi)),
            3: G.Cell("raw", ("ghost", "pw"), 0, (w >> 8) & mask_hi, 0),
        }
        for w in words
    ]


def test_a_16_bit_sweep_is_one_pair_routed_ramp_carrying_into_its_high_byte():
    """The carry the byte-wide RAMP refused is the word's own; both writes are one emit."""
    words = [0x00F0, 0x0110, 0x0130, 0x0150]
    graph, rep = G.strict(_native({("t", "r"): (0x20,)}, _sweep16(words, 0x20)))
    node = [g for g in graph.nodes if g.route[0] == "pair"][0]
    assert node.transfer == ("RAMP", 0x00F0, 0x20, 0x10000, ())
    assert node.route == T.pair(2, 3, 0xFF)
    assert rep.divergence is None and rep.coverage.interp == 8
    assert rep.coverage.classes["pw"] == dict.fromkeys(G._CLASSES, 0) | {"ramp": 6, "seed": 2}


def test_a_high_byte_outside_the_mask_refuses_the_pair_and_reseeds_the_byte_run():
    """A kbtrack-adjusted high byte is not this emit's; the byte run restarts after it."""
    writes = _sweep16([0x00F0, 0x0110, 0x0130], 0x20, mask_hi=0x7F)
    writes[0][3] = G.Cell("raw", ("ghost", "pw"), 0, 0xFF, 0)  # out of mask: no pair seed
    graph, rep = G.strict(_native({("t", "r"): (0x20,)}, writes))
    assert rep.divergence is None
    pairs = [g for g in graph.nodes if g.route[0] == "pair"]
    assert [g.transfer for g in pairs] == [("RAMP", 0x0110, 0x20, 0x10000, ())]
    lows = [g for g in graph.nodes if g.route == ("plane", 2) and g.transfer[0] == "RAMP"]
    assert not lows  # frame 0's write stays residual, not a stale-seeded byte ramp


def test_a_step_of_zero_and_a_run_of_one_are_both_refused():
    """A generator that predicts nothing is not one (docs/tracker.md §4c)."""
    flat = [{2: G.Cell("ramp", ("t", "r"), 0, 0x10, 0)} for _f in range(4)]
    one = [{2: G.Cell("ramp", ("t", "r"), 0, 0x10, 5)}]
    for writes in (flat, one):
        _g, rep = G.strict(_native({("t", "r"): (5,)}, writes))
        assert rep.coverage.interp == 0


def test_an_arithmetic_break_ends_the_run_at_a_new_observed_seed():
    """A re-staged step starts a new run, and each run pays one observed byte."""
    writes = [{2: G.Cell("ramp", ("t", "r"), 0, v, 3)} for v in (0x10, 0x13, 0x16, 0x40, 0x43)]
    _g, rep = G.strict(_native({("t", "r"): (3,)}, writes))
    assert rep.coverage.classes["pw"]["seed"] == 2 and rep.coverage.classes["pw"]["ramp"] == 3


def test_one_emit_the_projection_contradicts_refuses_its_whole_run():
    """A run is claimed whole or not at all, so a fitted sweep cannot slip through."""
    writes = [{2: G.Cell("ramp", ("t", "r"), 0, 0x10 + 3 * i, 3)} for i in range(4)]
    nat = _native({("t", "r"): (3,)}, writes)
    records = F.canonical([[(2, 0x10)], [(2, 0x13)], [(2, 0x99)], [(2, 0x19)]])
    _g, rep = G.graph(records, nat, offset=0)
    assert rep.divergence is None and rep.coverage.interp == 0 and rep.coverage.residual == 4


def test_a_byte_the_table_no_longer_holds_stays_raw():
    """The declared byte must agree, as `tracker._lane_key` requires of `mem0`."""
    writes = [{5: _sel(("ins", "ad"), 0, 0x99)}]
    _g, rep = G.strict(_native({("ins", "ad"): (0x11,)}, writes))
    assert rep.coverage.interp == 0 and rep.raw_kinds == {"select:('ins', 'ad')": 1}


def test_a_row_outside_the_table_stays_raw():
    writes = [{5: _sel(("ins", "ad"), 7, 0x11)}]
    _g, rep = G.strict(_native({("ins", "ad"): (0x11,)}, writes))
    assert rep.coverage.interp == 0


# ---- 2b. one register, two fields: the masked route ------------------------------
_FIELDS = {("ftbl", "type"): (0x10, 0x20), ("vol", "master"): (0x0F,)}


def _modevol(row=0, mode=0x10, vol=0x0F):
    """$18 as the editor spells it: a mode nibble beside a master volume."""
    return (
        G.Cell("select", ("ftbl", "type"), row, mode, 0, 0x70),
        G.Cell("select", ("vol", "master"), 0, vol, 0, 0x0F),
    )


def test_two_fields_of_one_register_become_two_masked_generators():
    """$18 is a mode ORed with a volume: two SELECTs, one write, one emit."""
    writes = [{0x18: _modevol(0)}, {0x18: _modevol(1, 0x20)}]
    graph, rep = G.strict(_native(_FIELDS, writes))
    got = sorted((g.route[2], g.transfer) for g in graph.nodes if g.route[:2] == ("plane", 0x18))
    assert [m for m, _t in got] == [0x0F, 0x70]
    assert dict(got)[0x70] == ("SELECT", (0x10, 0x20), (0, 1))
    assert rep.divergence is None and rep.coverage.interp == 2  # two frames, one write each
    assert rep.coverage.classes["filter"] == dict.fromkeys(G._CLASSES, 0) | {
        "mask": 2,
    }


def test_a_field_the_composers_table_no_longer_holds_refuses_the_whole_write():
    """Every field is checked against the table byte, so one wrong field costs the register."""
    writes = [{0x18: _modevol(0, mode=0x30)}]  # the table holds $10 at row 0
    _g, rep = G.strict(_native(_FIELDS, writes))
    assert rep.coverage.interp == 0 and rep.raw_kinds == {"mask:('ftbl', 'type')": 1}
    over = [{0x18: (_modevol(0)[0], G.Cell("select", ("vol", "master"), 0, 0x0F, 0, 0x1F))}]
    _g, rep = G.strict(_native(_FIELDS, over))  # $70 and $1F overlap: two owners of one bit
    assert rep.coverage.interp == 0  # refused by the mapping, before `tracker._check` sees it
    nodes = [T.select((0x10,), (0,), T.FRAME, 0x18, mask=m) for m in (0x70, 0x1F)]
    with pytest.raises(T.TrackerError):
        T.eval_graph(T.Graph(nodes), 1)


def test_a_bit_no_field_owns_must_be_zero_in_the_write():
    """The masks need not cover the byte, but what they leave over is emitted as zero."""
    writes = [{0x18: _modevol(0)}]
    records = F.canonical([[(0x18, 0x9F)]])  # bit 7 set, and no generator owns it
    _g, rep = G.graph(records, _native(_FIELDS, writes), offset=0)
    assert rep.divergence is None and rep.coverage.interp == 0 and rep.coverage.residual == 1


# ---- 3. the order-preserved section ----------------------------------------------
def _ord_writes(vals):
    return {
        6: _sel(("ins", "sr"), 0, vals[0]),
        5: _sel(("ins", "ad"), 0, vals[1]),
        4: G.Cell("ctrl", ("wtbl", "left"), 0, vals[2], 0),
    }


_ORD_TABLES = {("ins", "sr"): (0x66,), ("ins", "ad"): (0x55,), ("wtbl", "left"): (0x41,)}


def test_the_order_preserved_section_renders_in_the_drivers_own_order():
    writes = [_ord_writes((0x66, 0x55, 0x41)) for _f in range(3)]
    nat = _native(_ORD_TABLES, writes, shape=[(6, 5, 4)] * 3)
    graph, rep = G.strict(nat)
    recs = T.eval_graph(graph, 3)
    assert [r[1] for r in recs] == [((6, 0x66), (5, 0x55), (4, 0x41))] * 3
    assert rep.coverage.interp == 9


def test_a_frame_writing_a_subset_of_the_section_still_renders():
    """A ctrl-only frame is a subsequence of the layout: the other streams idle."""
    writes = [_ord_writes((0x66, 0x55, 0x41)), {4: G.Cell("ctrl", ("wtbl", "left"), 0, 0x41, 0)}]
    nat = _native(_ORD_TABLES, writes, shape=[(6, 5, 4), (4,)])
    graph, rep = G.strict(nat)
    assert [r[1] for r in T.eval_graph(graph, 2)] == [
        ((6, 0x66), (5, 0x55), (4, 0x41)),
        ((4, 0x41),),
    ]
    assert rep.coverage.interp == 4


def test_one_unexplained_write_costs_its_whole_section_and_the_law_still_holds():
    """The section is typed whole or replayed whole; the law never rides on luck."""
    bad = _ord_writes((0x66, 0x99, 0x41))
    writes = [_ord_writes((0x66, 0x55, 0x41)), bad]
    nat = _native(_ORD_TABLES, writes, shape=[(6, 5, 4)] * 2)
    graph, rep = G.strict(nat)
    assert rep.coverage.interp == 3 and rep.coverage.residual == 3
    assert F.diff(T.eval_graph(graph, 2), F.canonical(G._predicted(nat))) is None


# ---- 4. the two graphs, and the law ----------------------------------------------
def _records(nat):
    return F.canonical(G._predicted(nat))


def test_the_admitted_graph_passes_the_law_and_keeps_the_disagreement_in_raw():
    """A native byte that disagrees with the projection is residual, never emitted."""
    nat = _native({("ins", "ad"): (0x11,)}, [{5: _sel(("ins", "ad"), 0, 0x11)}] * 2)
    records = F.canonical([[(5, 0x11)], [(5, 0x77)]])
    _g, rep = G.graph(records, nat, offset=0)
    assert rep.divergence is None
    assert rep.coverage.interp == 1 and rep.coverage.residual == 1


def test_the_strict_graph_fails_the_law_when_the_song_says_something_else():
    """Strict takes every byte from the song, so a divergence is a real finding."""
    nat = _native({("ins", "ad"): (0x11,)}, [{5: _sel(("ins", "ad"), 0, 0x11)}] * 2)
    records = F.canonical([[(5, 0x11)], [(5, 0x77)]])
    _g, rep = G.strict(nat, records, 0)
    assert rep.divergence is not None and rep.matched == 1


def test_align_finds_the_drivers_frame_offset():
    """A packed driver's first call is its init tail, so the clocks start apart."""
    nat = _native({("ins", "ad"): (0x11, 0x22)}, [{5: _sel(("ins", "ad"), 1, 0x22)}] * 3)
    records = F.canonical([[(5, 0x00)], [(5, 0x22)], [(5, 0x22)], [(5, 0x22)]])
    assert G.align(records, nat) == 1


def test_the_raw_floor_is_named_by_what_could_not_be_expressed():
    writes = [{0: G.Cell("raw", ("vibrato", None), 0, 0x40, 0)} for _f in range(3)]
    _g, rep = G.strict(_native({}, writes))
    assert rep.raw_kinds == {"raw:('vibrato', None)": 3}


# ---- 5. the editor front-ends (optional extras) ----------------------------------
GT = pytest.mark.skipif(not G.gt_available(), reason="pygoattracker not installed")
SW = pytest.mark.skipif(not G.sw_available(), reason="pysidwizard not installed")


def _swm(name="demo-example.swm"):
    """A cached module where one is fetched, else a module written here.

    The written one keeps the SID-Wizard path hermetic: no module is committed
    and none is needed to exercise the mapping."""
    import pysidwizard as P  # pylint: disable=import-error

    if (SWM / name).is_file():
        return P.read_swm(str(SWM / name))
    ins = P.Instrument(
        name=b"lead",
        control=0x08,
        hr_decay=15,
        hr_release=15,
        attack=1,
        decay=8,
        sustain=10,
        release=9,
        first_waveform=0x41,
        wf_table=bytes([0x41, 0x80, 0x00, 0x41, 0x80, 0x00]),
        pw_table=bytes([0x88, 0x40, 0x00, 0x04, 0x02, 0x00]),
    )
    swm = P.SWMFile(
        instruments=[ins],
        patterns=[P.Pattern(rows=[P.Row(note=0x20, instrument=1)] + [P.Row()] * 3, length=4)],
        sequences=[[P.PlayPattern(1), P.Loop(0)] for _v in range(3)],
        subtune_tempos=[(6, 6)],
    )
    return P.parse_swm(P.build_swm(swm))


@SW
def test_sidwizard_maps_to_the_same_lanes_and_passes_the_law():
    """One vocabulary, two editors: the same lane names, the same builder."""
    native, records = G.sw_native(_swm(), 60)
    assert native.editor == "sidwizard"
    assert {("pitch", "lo"), ("ins", "ad"), ("hr", "ad"), ("wf", "left")} <= set(native.tables)
    _g, rep = G.graph(records, native, offset=0)
    assert rep.divergence is None and rep.coverage.interp > 0


@SW
def test_the_sidwizard_preamble_lane_is_the_four_hr_fields_packed_as_adsr():
    """`hr_attack`/`hr_decay`/... are one preamble lane, not four editor fields."""
    swm = _swm()
    tables, _base, _fbase, _pbase = G._sw_tables(swm, (0,) * 96, (0,) * 96)
    ins = swm.instruments[0]
    assert tables[("hr", "ad")][0] == (ins.hr_attack << 4) | ins.hr_decay
    assert tables[("hr", "sr")][0] == (ins.hr_sustain << 4) | ins.hr_release


@GT
def test_goattracker_maps_a_hand_built_song_and_passes_the_law():
    """A song written here, played by the editor's own routine, mapped and gated."""
    from pygoattracker import constants as gtc  # pylint: disable=import-error
    from pygoattracker.model import Instrument, Pattern, Row, Song  # pylint: disable=import-error
    from pygoattracker.sid import PackedInfo  # pylint: disable=import-error

    song = Song(
        instruments=[
            Instrument(
                attack_decay=0x18,
                sustain_release=0xF9,
                first_wave=0x41,
                wave_ptr=1,
                pulse_ptr=1,
                filter_ptr=1,
                vibrato_param=1,
            )
        ],
        patterns=[
            Pattern(
                rows=[
                    Row(note=gtc.note_value("C-4"), instrument=1),
                    Row(command=gtc.CMD_PORTAUP, data=1),
                    Row(command=gtc.CMD_SETAD, data=0x22),
                    Row(note=gtc.note_value("E-4"), command=gtc.CMD_VIBRATO, data=1),
                ]
                + [Row() for _r in range(4)]
            )
        ],
    )
    song.wavetable.left[:] = [0x41, 0x11, 0xE1, 0xF7, 0xFF]
    song.wavetable.right[:] = [0x80, 0x80, 0x80, 0x21, 0x01]
    song.pulsetable.left[:] = [0x88, 0x40, 0xFF]
    song.pulsetable.right[:] = [0x00, 0x02, 0x01]
    song.filtertable.left[:] = [0x90, 0x00, 0x08, 0xFF]
    song.filtertable.right[:] = [0xF1, 0x40, 0x02, 0x01]
    song.speedtable.left[:] = [0x01]
    song.speedtable.right[:] = [0x40]
    native = G.gt_native(song, PackedInfo(), 0, 40)
    assert native.structure["patterns"] == 1 and native.structure["instruments"] == 1
    records = F.canonical(G._predicted(native))
    g, rep = G.graph(records, native, offset=0)
    assert rep.divergence is None
    assert rep.coverage.classes["ad"]["lane"] > 0 and rep.coverage.classes["ctrl"]["lane"] > 0
    assert rep.coverage.classes["filter"]["lane"] > 0
    # the CMD_VIBRATO row: freq_lo takes the speedtable's own step over the value it holds
    assert rep.coverage.classes["freq"]["rel"] > 0
    rel = [n for n in g.nodes if n.route[0] == "rel"]
    assert rel and {n.route[3:] for n in rel} == {("ADD", ("prev",)), ("SUB", ("prev",))}
    assert {n.route[1] % 7 for n in rel} == {0}  # freq_lo only: the high byte moves on carry
    assert all(b in song.speedtable.right for n in rel for b in n.transfer[1] if b)


@GT
def test_the_goattracker_pitch_lane_is_the_editors_own_note_table():
    from pygoattracker import constants as gtc  # pylint: disable=import-error
    from pygoattracker.model import Song  # pylint: disable=import-error

    tables, _base = G._gt_tables(Song(), gtc.DEFAULT_ADPARAM, gtc.FREQ_TABLE)
    assert tables[("pitch", "lo")][: len(gtc.FREQ_LO)] == gtc.FREQ_LO
    assert tables[("pitch", "hi")][: len(gtc.FREQ_HI)] == gtc.FREQ_HI


@GT
@pytest.mark.parametrize("rel", ["MUSICIANS/C/Chiummo_Aldo/25_Years_tune_1.sid"])
def test_a_cached_gt_tune_reproduces_its_own_sid_writes_from_the_song_alone(rel):
    """The strict oracle against the tracker's law: the composer's data, verbatim."""
    from deity_informant import frameprog
    from deity_informant import structured as S
    from deity_informant.c64 import load_psid

    path = HVSC / rel
    if not path.is_file():
        pytest.skip("tune not cached")
    mem, _load, init, play = load_psid(path.read_bytes())
    mem[0xD418] = 0x0F
    model, _ev = S.decompile(mem, init, play, 60, 0)
    prog = frameprog.program(model)
    trace, _walker = frameprog.iota(model, 60)
    records = T.oracle(prog, trace, 60)
    song, info, sub = G.gt_decompile(path, 0)
    native = G.gt_native(song, info, sub, 60)
    offset = G.align(records, native)
    _g, rep = G.strict(native, records, offset)
    assert rep.divergence is None and rep.matched == rep.frames


# ---- 6. the arrangement: a row a generator supplies, not one the player was watched at --
def _arr(lane, row, value, src):
    """A cell whose own row the pattern column ``src`` names."""
    return G.Cell("select", lane, row, value, 0, src=src)


def _arranged(rows=(0, 1, 2), notes=(1, 2, 0), shift=0):
    """A `Native` whose freq lane is the pitch table at the note its pattern holds."""
    pitch = (0x11, 0x22, 0x33)
    tables = {("pitch", "lo"): pitch, ("patt", "note"): tuple(notes)}
    writes = [
        {
            0: _arr(
                ("pitch", "lo"),
                notes[r] + shift,
                pitch[notes[r] + shift],
                (("patt", "note"), r, (0, 7), shift),
            )
        }
        for r in rows
    ]
    return _native(tables, writes)


def test_a_pattern_column_supplies_the_pitch_tables_row_and_the_law_holds():
    """The note is the composer's pattern byte, and the pitch table reads it at that row."""
    graph, rep = G.strict(_arranged())
    src = [g for g in graph.nodes if g.route == T.INDEX]
    reader = [g for g in graph.nodes if g.route == ("plane", 0)][0]
    assert rep.divergence is None and rep.coverage.interp == 3
    assert len(src) == 2 and reader.transfer[2] == ("node", graph.nodes.index(src[1]))
    assert src[1].transfer[:2] == ("SELECT", (1, 2, 0))  # the pattern's own note column
    assert rep.arrangement["patterns"] == 1 and rep.arrangement["pattern_rows"] == 3


def test_an_evenly_stepped_walk_is_a_row_counter_and_a_broken_one_is_unrolled():
    """`RAMP` is the row counter; a walk that restarts is carried unrolled instead."""
    ramp = G.strict(_arranged(rows=(0, 1, 2)))[0]
    walk = G.strict(_arranged(rows=(0, 1, 0)))[0]
    assert [g.transfer for g in ramp.nodes if g.route == T.INDEX][0] == ("RAMP", 0, 1, 0, ())
    assert [g.transfer for g in walk.nodes if g.route == T.INDEX][0] == ("SELECT", (0, 1, 0), ())


def test_a_row_counter_seeded_at_another_pattern_breaks_the_law():
    """The orderlist picks which rows the counter walks, so a wrong seed writes wrong bytes."""
    graph = G.strict(_arranged())[0]
    records = T.eval_graph(graph, 3)
    at = [i for i, g in enumerate(graph.nodes) if g.route == T.INDEX][0]
    graph.nodes[at] = graph.nodes[at]._replace(transfer=("RAMP", 1, 1, 0, ()))
    assert F.diff(T.eval_graph(graph, 3), records) is not None


def test_a_pattern_row_moved_by_one_moves_the_record_it_writes():
    """The pattern's own column is what is read, so shifting it shifts the emit."""
    base = T.eval_graph(G.strict(_arranged())[0], 3)
    moved = T.eval_graph(G.strict(_arranged(notes=(2, 1, 0)))[0], 3)
    assert F.diff(moved, base) is not None


def test_a_row_the_pattern_column_does_not_name_refuses_the_whole_stream():
    """A generated row past its table drops the write, so a stream that fails is refused."""
    tables = {("pitch", "lo"): (0x11, 0x22), ("patt", "note"): (0, 1)}
    writes = [
        {0: _arr(("pitch", "lo"), 0, 0x11, (("patt", "note"), 0, (0, 7), 0))},
        {0: _arr(("pitch", "lo"), 1, 0x22, (("patt", "note"), 0, (0, 7), 0))},  # row 0 names 0
    ]
    graph, rep = G.strict(_native(tables, writes))
    assert rep.divergence is None and rep.coverage.interp == 2
    assert not [g for g in graph.nodes if g.route == T.INDEX]
    assert rep.arrangement["refused_row_not_declared"] == 1


def test_a_transpose_is_a_relative_row_and_emits_select_rel():
    """The shifted read is `SELECT[rel]`: the column is the base, the shift the delta."""
    graph, rep = G.strict(_arranged(notes=(0, 1, 0), shift=1))
    reader = [g for g in graph.nodes if g.route == ("plane", 0)][0]
    src = [g for g in graph.nodes if g.route == T.INDEX]
    assert rep.divergence is None and rep.coverage.interp == 3
    assert reader.transfer[2] == ("rel", "ADD", ("const", 1), ("node", graph.nodes.index(src[1])))


def test_a_shift_the_column_does_not_reproduce_is_refused_and_priced():
    """An arpeggio is not a transpose: a row off the shifted column refuses, counted."""
    refused = {}
    tables = {("patt", "note"): (5, 6)}
    got = G._patt_src(tables, ("patt", "note"), 0, 8, (0, 0), refused, 3, "arpeggio")
    assert got == (("patt", "note"), 0, (0, 0), 3)  # 5 + 3 == 8: the shift names it
    assert G._patt_src(tables, ("patt", "note"), 0, 9, (0, 0), refused, 3, "arpeggio") is None
    assert refused == {"arpeggio": 1}
    assert G._patt_src(tables, ("patt", "note"), 0, 5, (0, 0), refused) == (
        ("patt", "note"),
        0,
        (0, 0),
        0,
    )


def test_mutation_a_wrong_transpose_delta_moves_the_record():
    """The transpose is generated, not replayed: perturbing the delta fails the law."""
    graph, _rep = G.strict(_arranged(notes=(0, 1, 0), shift=1))
    records = T.eval_graph(graph, 3)
    at = next(i for i, g in enumerate(graph.nodes) if g.route == ("plane", 0))
    g = graph.nodes[at]
    rows = ("rel", "ADD", ("const", 2), g.transfer[2][3])
    graph.nodes[at] = g._replace(transfer=g.transfer[:2] + (rows,))
    assert F.diff(T.eval_graph(graph, 3), records) is not None


@GT
def test_the_goattracker_orderlist_loop_is_the_lookups_own_wrap_only_sometimes():
    """A channel restarting at entry 0 wraps where `_emit` already wraps; others do not."""
    # pylint: disable=import-error,import-outside-toplevel
    from pygoattracker.model import Orderlist, PlayPattern, Song, Subtune

    chans = [Orderlist([PlayPattern(0)] * 3, restart=r) for r in (0, 0, 2)]
    song = Song(subtunes=[Subtune(channels=chans)])
    assert G._gt_loops(song, 0) == {"loop_at_end": 2, "loop_elsewhere": 1}


@GT
def test_goattracker_reads_its_pitch_table_at_the_row_its_pattern_names():
    """The editor's own player, mapped: the note lane's row is the pattern's note column."""
    # pylint: disable=import-error,import-outside-toplevel
    from pygoattracker import constants as gtc
    from pygoattracker.model import Instrument, Pattern, Row, Song
    from pygoattracker.sid import PackedInfo

    rows = [Row(note=gtc.note_value("C-4"), instrument=1)] + [Row() for _r in range(3)]
    rows += [Row(note=gtc.note_value("E-4"), instrument=1)] + [Row() for _r in range(3)]
    song = Song(
        instruments=[
            Instrument(attack_decay=0x18, sustain_release=0xF9, first_wave=0x41, wave_ptr=1)
        ],
        patterns=[Pattern(rows=rows)],
    )
    song.wavetable.left[:] = [0x41, 0xFF]
    song.wavetable.right[:] = [0x00, 0x01]
    native = G.gt_native(song, PackedInfo(), 0, 40)
    assert native.tables[("patt", "note")][0] == gtc.note_value("C-4") - gtc.FIRSTNOTE
    g, rep = G.strict(native, F.canonical(G._predicted(native)), 0)
    assert rep.divergence is None
    assert rep.arrangement["patterns"] == 1 and rep.arrangement["orderlist_entries"] > 0
    assert [n for n in g.nodes if n.route == T.INDEX]
