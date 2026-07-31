"""trackertext: the tracker graph rendered in the musical domain, for review."""

import numpy as np

from deity_informant import frameprog
from deity_informant import tracker as T
from deity_informant import trackertext as X

CTRL_LANE = (0x41, 0x11, 0x21)
AD_LANE = (0x0A, 0x0B, 0x0C)
BASE, ROWS, STRIDE, NF = 0x2000, 3, 4, 8


def _prog():
    """A frame program declaring one 3-row bank whose two lanes the graph reads."""
    mem = bytearray(0x10000)
    for off, vals in ((2, CTRL_LANE), (3, AD_LANE)):
        for i, v in enumerate(vals):
            mem[BASE + STRIDE * i + off] = v
    decl = {
        "kind": "table",
        "base": BASE,
        "size": ROWS * STRIDE,
        "stride": STRIDE,
        "mut": (0,),
        "cobases": [],
    }
    return frameprog.FrameProgram(0x1000, 0x0F00, decls=[decl], mem0=mem)


def _pitch(n=24):
    """A 2-octave equal-tempered pitch table, as ``tracker._pitch`` would recover it."""
    words = np.array([round(0x0116 * 2 ** (i / 12)) for i in range(n)], dtype=np.int64)
    return T.Pitch(0x2100, words, n // 12, int(words[0]), "<", False, None)


def _graph(pitch=None, with_pitch=True):
    """A hand-built graph carrying every transfer kind, over 8 frames."""
    p = pitch if pitch is not None else _pitch()
    img = tuple((b & a) | o for a, o in T._SECT for b in CTRL_LANE)
    seq = [3, 5] * 4
    lo = [int(p.words[i]) & 0xFF for i in seq]
    hi = [int(p.words[i]) >> 8 for i in seq]
    nodes = [
        T.div(2),
        T.edge((1, 0, 1, 1, 1, 1, 1, 1)),
        T.lookup((0x80,), ("event", 1), 4),
        T.select(CTRL_LANE + img, (0, 4, 7, 10), ("event", 1), 4),
        T.select(AD_LANE, (0, 1, 2, 0, 1, 2, 0), ("event", 1), 5),
        T.ramp(0x80, 0x10, 0x100, ("event", 0), 2),
        T.raw([[(0x15, 0x10)], [], [(0x15, 0x11), (0x16, 0x22)], [], [], [], [], []]),
        T.lookup(lo, T.FRAME, 0),
        T.lookup(hi, T.FRAME, 1),
    ]
    classes = {
        "ctrl": {"lane": 2, "gate": 5, "imm": 7, "ramp": 0, "seed": 0},
        "ad": {"lane": 7, "gate": 0, "imm": 0, "ramp": 0, "seed": 0},
        "pw": {"lane": 0, "gate": 0, "imm": 0, "ramp": 3, "seed": 1},
    }
    return T.Graph(nodes, freq_table=p if with_pitch else None, classes=classes)


def _text(**kw):
    return X.emit(_graph(), NF, _prog(), **kw)


def _line(text, prefix):
    return next(ln for ln in text.splitlines() if ln.strip().startswith(prefix))


def test_every_transfer_kind_renders():
    """One entry per node, in graph order, naming its transfer, trigger and route."""
    text = _text()
    kinds = [ln.split()[1] for ln in text.splitlines() if ln.startswith("n0")]
    assert kinds == ["DIV", "EDGE", "LOOKUP", "SELECT", "SELECT", "RAMP", "RAW", "LOOKUP", "LOOKUP"]
    assert "one tick per 2" in _line(text, "n00")
    assert "n02  LOOKUP -> voice 1 waveform          <- n01 x7" in text
    assert "9 nodes: 1 DIV, 1 EDGE, 3 LOOKUP, 1 RAMP, 1 RAW, 2 SELECT" in text


def test_nothing_of_the_machine_survives():
    """No address, no register number: the artifact is in the musical domain."""
    text = _text()
    assert "$" not in text and "0x" not in text
    for musical in ("voice 1 waveform", "voice 1 attack/decay", "voice 1 pulse lo", "inst 00"):
        assert musical in text


def test_a_select_names_the_table_and_the_row_it_reads():
    """A lane is named by its table and its part; a ctrl row says how the gate rode it."""
    text = _text()
    assert "reads  table 0 waveform lane, 3 rows + 3 images" in text
    assert _line(text, "rows   inst 00  inst 01 hold").endswith("inst 01 gate-  inst 01 gate+")
    assert "reads  table 0 attack/decay lane, 3 rows" in text
    assert "table 0  instrument    3 rows   lanes: attack/decay, waveform" in text


def test_instruments_are_numbered_by_first_appearance_and_read_musically():
    """The instrument list is the table's rows, decoded as an editor shows them."""
    text = _text()
    assert "  entry    attack/decay  waveform" in text
    assert "  inst 00  A0 DA         pulse+gate" in text
    assert "  inst 01  A0 DB         tri+gate" in text
    assert "  inst 02  A0 DC         saw+gate" in text


def test_a_repeated_block_is_surfaced_with_its_repeat_count():
    """Repetition is the arrangement showing through, so it is factored out, not trimmed."""
    assert "rows   [inst 00  inst 01  inst 02] x2  inst 00" in _text()
    assert X._stream([1, 2, 1, 2, 1, 2], str) == "[1  2] x3"
    assert X._cycles([(1, 1), (2, 1), (1, 1), (2, 1)]) == [(0, 4, 2)]


def test_nothing_is_collapsed_silently():
    """A stream cut for width reports the blocks and rows it did not print."""
    got = X._stream(range(60), lambda v: "row%d" % v, width=20)
    assert got.startswith("row0  row1") and got.endswith("rows)")
    assert "...(+57 blocks, 57 rows)" in got
    assert X._block(["a", "b", "c"], width=1) == "a  ...(+2)"


def test_a_kind_with_many_nodes_is_capped_and_the_rest_counted():
    """A whole tune's sweep is thousands of one-run RAMPs; the listing says what it dropped."""
    nodes = [T.edge((1,) * NF)] + [T.ramp(16 * i, 1, 0x100, ("event", 0), 2) for i in range(1, 12)]
    g = T.Graph(nodes)
    keys, scan = X._scanned(g, NF, None)
    lines = X._generators(g, scan, keys, cap=3)
    assert sum(1 for ln in lines if ln.startswith("n")) == 4  # the EDGE, plus three RAMPs
    assert "...(+8 more RAMP nodes, 64 emits between them, not listed)" in lines
    assert "12 nodes: 1 EDGE, 11 RAMP" in X.emit(g, NF)


def test_the_edge_is_the_observed_trigger_floor():
    """A trigger stream reports when it fires, its consumers, and that it is observed."""
    line = _line(_text(), "when")
    assert "first f0, gaps: 1 apart x5, 2 apart x1" in line
    assert "every EDGE fire time is OBSERVED — the trigger floor" in _text()
    assert "7 fires over 8 frames -> n02  n03  n04" in _line(_text(), "n01")


def test_the_sweep_reports_its_seed_step_and_bound():
    """The seed is the one observed byte; the step and the wrap are the generator's."""
    text = _text()
    assert "sweep  starts at 128 (OBSERVED), steps +16 per fire, wraps at 256" in text
    assert "values 128 144 160 176  (4 emits)" in text


def test_the_note_lane_names_notes_and_factors_out_the_figure():
    """The generated pitch inverts through the graph's own pitch table into named runs."""
    text = _text()
    assert "voice 1  8 note frames of 8, 8 runs, 2 distinct notes" in text
    assert "  f00000-00007  [D#0  F0] x4" in text
    assert "voice 2  0 note frames of 8, 0 runs, 0 distinct notes" in text


def test_the_coverage_split_reports_the_evidence_classes_and_the_residual_share():
    """Strong (lane/gate/ramp), shallow (imm/seed) and the note class are never folded."""
    text = _text()
    row = ["waveform", "14", "14", "100.0%", "|", "2", "5", "0", "|", "7", "0", "|", "0", "|", "0"]
    assert _line(text, "waveform").split() == row
    assert _line(text, "pitch  ").split()[-1] == "16"  # the note lane: its own class
    assert _line(text, "all").split()[1:4] == ["41", "44", "93.2%"]
    assert "values   41/44 = 93.2% generated, 3 replayed as observed writes" in text


def test_the_residual_is_reported_per_plane_and_per_part():
    """Nothing lets a reader think the graph explains more than it does."""
    text = _text()
    assert "values   3 writes replayed verbatim over 8 frames = 6.8% of all writes" in text
    assert "plane  filter                 3 of       3 not explained (100.0%)" in text
    assert "filter cutoff lo               2 replayed,       0 generated" in text
    assert "timing   1 trigger streams, 7 fires" in text
    assert "shallow  7 program constants" in text and "1 observed bytes seeding a sweep" in text
    assert "f00000  filter cutoff lo = 16   (OBSERVED)" in text


def test_the_rendering_is_stable():
    """Same graph, same text — a rendering is diffable between runs and between tunes."""
    assert _text(title="t", law="PASS") == _text(title="t", law="PASS")
    assert X.emit(_graph(), NF, _prog()) == X.emit(_graph(), NF, _prog())


def test_the_law_verdict_and_title_are_carried_verbatim():
    assert "tracker  Tune  subtune 0  8 frames (0:00)" in _text(title="Tune  subtune 0")
    assert "law      PASS " in _text(law="PASS")
    assert "law      not checked " in _text()


def test_without_a_frame_program_the_lanes_are_still_tables():
    """The graph alone renders: a lane is a table of its own, just not grouped."""
    text = X.emit(_graph(), NF)
    assert "table 0 waveform lane, 3 rows" in text and "table 1 attack/decay lane" in text
    assert "tempo" not in text


def test_a_graph_without_a_pitch_table_has_no_note_lane():
    text = X.emit(_graph(with_pitch=False), NF, _prog())
    assert "pitch    (no pitch table recovered)" in text
    assert "(no pitch table recovered: no note lane)" in text


def test_an_octave_shift_table_names_its_rows_without_an_octave():
    """A one-octave table is transposed by octave, so its rows are bare note names."""
    p = _pitch()
    assert "24 notes, C0 .. B1, 2 octaves, equal-tempered" in X._pitch_lines(p)[0]
    shift = p._replace(shift=True, words=p.words[:12], octaves=1)
    assert "12 notes, C .. B, one octave, transposed down by octave" in X._pitch_lines(shift)[0]
    text = X.emit(_graph(pitch=shift), NF, _prog())
    assert "one octave, transposed down by octave" in text


def test_detune_is_reported_in_cents():
    """A detuned note is an interval from the table's note, not a raw difference."""
    note = T.Note(3, 0x014B + 6, "D#0", 6)
    assert X._cents(note) == 31
    assert X._run_str([0, 0, 3, "D#0", 4, -12, 31]) == "D#0 x4 -12..+31c"


# ---- the masked route: one register, several generators (docs/tracker.md §4e) ----
MODE_LANE = (0x10, 0x20, 0x40)  # low-pass, band-pass, high-pass, in the mode field's bits
RES_LANE = (0x80, 0xA0)
MF = 4


def _masked():
    """A graph whose $18 and $17 are each partitioned between generators by mask."""
    nodes = [
        T.edge((1,) * MF),
        T.select(MODE_LANE, (0, 1, 2, 0), ("event", 0), 0x18, 0x70),
        T.lookup((0x0F,), ("event", 0), 0x18, 0x0F),
        T.select(RES_LANE, (0, 1, 0, 1), ("event", 0), 0x17, 0xF0),
        T.lookup((0x02,), ("event", 0), 0x17, 0x02),
        T.raw([[] for _f in range(MF)]),
    ]
    return T.Graph(nodes, classes={"filter": {"mask": 8}})


def test_a_masked_route_renders_as_the_distinct_musical_objects_it_names():
    """A mask is a field, not an annotation: mode, volume, resonance and routing each route."""
    text = X.emit(_masked(), MF)
    for musical in ("-> filter mode", "-> master volume", "-> resonance", "-> voice 2 routing"):
        assert musical in text
    assert "$" not in text and "0x" not in text and "mask" in text
    assert "constant master volume 15, 4 times" in text
    assert "constant voice 2 routing on, 4 times" in text


def test_a_masked_group_is_named_with_the_generators_that_share_its_write():
    """The fields of one register are stated together: what assembles the byte, and from where."""
    text = X.emit(_masked(), MF)
    assert (
        "fields   resonance + voice 2 routing: 2 generators of disjoint bits,"
        " one write between them — n03 n04" in text
    )
    assert (
        "fields   filter mode + master volume: 2 generators of disjoint bits,"
        " one write between them — n01 n02" in text
    )
    assert "rows   setting 00  setting 02  setting 04  setting 00" in text
    assert "rows   [setting 01  setting 03] x2" in text


def test_a_table_lane_is_decoded_by_the_part_of_the_register_it_drives():
    """A filter lane is a filter setting, not a waveform: the role picks the decoder."""
    text = X.emit(_masked(), MF)
    assert "  entry    filter mode  resonance" in text
    assert "  filt 00  low-pass     -" in text
    assert "  filt 01  -            resonance 8" in text
    assert X._lane_byte("mode/volume", 0x1F) == "voice 3 mute off, low-pass, master volume 15"
    assert X._lane_byte("cutoff hi", 0x1F) == "31"
    assert X._lane_byte("waveform", 0x41) == "pulse+gate"


def test_a_masked_group_counts_as_one_emit_not_one_per_field():
    """The rendering's own coverage is `tracker.coverage`'s, masked registers included."""
    g = _masked()
    mine = X._scan(g, MF, X._keys(g, {}), X._Tables(g, X._keys(g, {}))).cov
    theirs = T.coverage(g, MF)
    assert (mine.interp, mine.total, mine.planes) == (theirs.interp, theirs.total, theirs.planes)
    assert mine.interp == 2 * MF  # two registers, one write each per frame, not four
    assert "filter                 8       8  100.0%" in X.emit(g, MF)


def test_the_fire_index_is_the_evaluator_s_own_propagation():
    """The linear consumer index must agree with `tracker._fired` frame for frame."""
    for g in (_masked(), _graph()):
        nodes = g.nodes
        cons = X._consumers(nodes)
        roots = [i for i, n in enumerate(nodes) if n.route[0] == "fire"]
        for f in range(NF):
            assert X._fires(nodes, roots, cons, f) == T._fired(nodes, f)


# ---- side by side: our recovery against a native editor's own song ----------------
def _sides():
    """Two `Side`s of one tune: ours, and a second graph standing in for the oracle's."""
    return [
        X.Side("A", "our recovery", _graph(), "PASS", _prog()),
        X.Side("B", "the composer's own song", _graph(), "PASS", None),
    ]


def test_the_two_sides_are_rendered_by_one_emitter_and_compared_first():
    """Both graphs render through `emit`'s own sections, behind a difference table."""
    text = X.compare(_sides(), NF, title="Tune", facts=(("patterns", 12), ("orderlist rows", 40)))
    assert "compare  Tune  8 frames (0:00)" in text
    assert text.count("; ---- generators:") == 2 and text.count("; ---- residual:") == 2
    assert "; ===== A: our recovery =====" in text
    assert "; ===== B: the composer's own song =====" in text
    assert _line(text, "waveform").split()[-1] == "same"


def test_the_comparison_reads_off_pitch_instruments_and_the_arrangement():
    """The three axes, in the musical domain: notes, instrument numbering, arrangement."""
    text = X.compare(_sides(), NF, facts=(("patterns", 12),))
    assert "8 frames name a note on both sides, 8 agree (100.0%) — the same pitch" in text
    assert "a bijection" in _line(text, "instr")
    assert "ours 00 = theirs 01, ours 01 = theirs 03, ours 02 = theirs 04" in text
    assert "arrange  A 0, B 0 generators addressing another generator's index" in text
    assert "the song itself holds 12 patterns" in text
    assert "a row stream is a recovered index on both sides, never a generated one" in text


def test_a_side_that_starts_later_is_compared_at_its_own_offset():
    """A packed driver's frame 0 is not the tune's, so a side carries the offset it starts at."""
    sides = _sides()
    sides[1] = sides[1]._replace(offset=2)
    text = X.compare(sides, NF)
    assert "6 frames name a note on both sides" in text
    assert "ours names a note on 8 frames in all" in text


def test_an_index_node_is_named_by_what_it_does_not_by_a_missing_register():
    """An index source writes no register, so it must not read as unexplained."""
    g = T.Graph(
        [
            T.edge((1, 1)),
            T.indexer(("LOOKUP", (3,)), ("event", 0)),
            T.select(tuple(range(10)), ("node", 1), ("event", 0), 0),
        ]
    )
    assert X._route(g.nodes[1]) == "-> the row another generator reads"
    assert X._lookup_str(g.nodes[1], 2).endswith("(see the note lane)")


def test_a_relative_route_says_it_offsets_its_register():
    """A relative emit is a delta, not the byte the register takes."""
    g = T.Generator(("LOOKUP", (1,)), T.FRAME, T.relative(0x01, "ADD", ("prev",)))
    assert X._route(g) == "-> voice 1 pitch hi, as an offset"


def test_the_arrangement_axis_counts_a_generated_row_where_one_exists():
    """A pattern read at a row an index generator supplies is what the axis is looking for."""
    nodes = list(_graph().nodes) + [
        T.indexer(("RAMP", 0, 1, 4), T.FRAME),
        T.select((0x11, 0x22, 0x33, 0x44), ("node", len(_graph().nodes)), T.FRAME, 0x15),
    ]
    side = X.Side("A", "our recovery", T.Graph(nodes), "PASS", _prog())
    text = X.compare([side, _sides()[1]], NF)
    assert "arrange  A 1, B 0 generators addressing another generator's index" in text
    assert "A 1, B 0 tables read at a generated row" in text
    assert "a row stream is a recovered index on both sides" not in text
