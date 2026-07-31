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
    assert "reads  table 0 waveform lane, 3 rows + 3 gate images" in text
    assert _line(text, "rows   inst 00  inst 01 hold").endswith("inst 01 gate-  inst 01 gate+")
    assert "reads  table 0 attack/decay lane, 3 rows" in text
    assert "table 0  instrument    3 rows   lanes: attack/decay, waveform" in text


def test_instruments_are_numbered_by_first_appearance_and_read_musically():
    """The instrument list is the table's rows, decoded as an editor shows them."""
    text = _text()
    assert "  inst 00   A0 DA               pulse+gate" in text
    assert "  inst 01   A0 DB               tri+gate" in text
    assert "  inst 02   A0 DC               saw+gate" in text


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


def test_the_edge_is_the_observed_trigger_floor():
    """A trigger stream reports when it fires, its consumers, and that it is observed."""
    line = _line(_text(), "when")
    assert "first f0, gaps: 1 apart x5, 2 apart x1" in line
    assert "every EDGE fire time is OBSERVED — the trigger floor" in _text()
    assert "7 fires over 8 frames -> n02 n03 n04" in _line(_text(), "n01")


def test_the_sweep_reports_its_seed_step_and_bound():
    """The seed is the one observed byte; the step and the wrap are the generator's."""
    text = _text()
    assert "sweep  starts at 80 (OBSERVED), steps +16 per fire, wraps at 256" in text
    assert "values 80 90 A0 B0  (4 emits)" in text


def test_the_note_lane_names_notes_and_factors_out_the_figure():
    """The generated pitch inverts through the graph's own pitch table into named runs."""
    text = _text()
    assert "voice 1  8 note frames of 8, 8 runs, 2 distinct notes" in text
    assert "  f00000-00007  [D#0  F0] x4" in text
    assert "voice 2  0 note frames of 8, 0 runs, 0 distinct notes" in text


def test_the_coverage_split_reports_the_evidence_classes_and_the_residual_share():
    """Strong (lane/gate/ramp), shallow (imm/seed) and the note class are never folded."""
    text = _text()
    row = ["waveform", "14", "14", "100.0%", "|", "2", "5", "0", "|", "7", "0", "|", "0"]
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
    assert "f00000  filter cutoff lo = 10   (OBSERVED)" in text


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
