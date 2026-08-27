"""Per-frame register grids: a write belongs to the frame its cycle falls in."""

import numpy as np
import pytest

from deity_informant.tuneprog import grid

from _asm import asm, trace_prog

PLAY = 0x1000
CPF = 19656
BURN = ("LDX #$22", "l1: LDY #$00", "l2: DEY", "BNE l2", "DEX", "BNE l1")  # ~43,500 cycles


def test_a_tick_that_outlives_its_frame_writes_into_the_next_one():
    """A tick spanning 2.2 frames leaves its second write two frames on."""
    T, _ = trace_prog(
        {
            PLAY: asm(
                PLAY,
                "INC ctr",
                "LDA ctr",
                "STA $D404",
                *BURN,
                "INC ctr",
                "LDA ctr",
                "STA $D404",
                "RTS",
                "ctr: BRK",
            ),
            0x1200: asm(0x1200, "RTS"),
        },
        init=0x1200,
        play=PLAY,
        calls=3,
    )
    assert list(grid.trace_grid(T, 8)[:, 4]) == [1, 1, 3, 3, 5, 5, 6, 6]
    assert list(grid.tick_grid(T, 8)[:, 4]) == [2, 4, 6, 6, 6, 6, 6, 6]


def test_a_tick_inside_its_frame_frames_exactly_like_its_call_index():
    T, _ = trace_prog(
        {
            PLAY: asm(
                PLAY, "INC ctr", "LDA ctr", "STA $D400", "LDA #$F7", "STA $D403", "RTS", "ctr: BRK"
            ),
            0x1200: asm(0x1200, "RTS"),
        },
        init=0x1200,
        play=PLAY,
    )
    rows = grid.trace_grid(T)
    assert not grid.differing(grid.tick_grid(T, len(rows)), rows).size
    assert list(rows[0][:4]) == [1, 0, 0, 7]  # $D403 keeps its low nibble only
    assert rows[1][0] == 2


def test_a_write_before_frame_zero_is_the_baseline_the_first_row_fills_from():
    rows = grid.grid([-2, -1, 0, 2], [1, 3, 1, 1], [9, 0xF3, 7, 8], 4)
    assert [list(r[:4]) for r in rows] == [
        [0, 7, 0, 3],
        [0, 7, 0, 3],
        [0, 8, 0, 3],
        [0, 8, 0, 3],
    ]


def test_the_frame_of_a_cycle_counts_from_the_first_interrupt():
    f = grid.frames([100, CPF - 1, CPF + 100, 3 * CPF], 100, CPF)
    assert list(f) == [0, 0, 1, 2]
    assert list(grid.frames([0, 99], 100, CPF)) == [-1, -1]


def test_the_cycle_column_unwraps_past_two_to_the_thirty_second():
    c = np.array([grid.WRAP - 10, grid.WRAP - 5, 5, 20], dtype=np.uint32)
    assert list(np.diff(grid.unwrap(c))) == [5, 10, 15]
    assert grid.unwrap(np.array([], dtype=np.uint32)).size == 0


def test_a_sidtrace_row_frames_by_its_own_interrupt():
    pytest.importorskip("pysidtracker")
    from pysidtracker.oracle import SidtraceRow  # pylint: disable=import-outside-toplevel

    r0 = 1_000_000
    rows = [
        SidtraceRow(r0 - 500, None, None, None, 0, 4, 0x11),  # init writes: the baseline
        SidtraceRow(r0 + 10, None, 10, None, 0, 4, 0x21),
        SidtraceRow(r0 + CPF + 10, None, 10, None, 0, 4, 0x31),
        SidtraceRow(r0 + CPF + 12, None, 12, None, 1, 4, 0xFF),  # another chip
        SidtraceRow(r0 + 2 * CPF + 10, None, 10, None, 0, 4, 0x41),
        SidtraceRow(r0 + 3 * CPF + 10, None, 10, None, 0, 4, 0x51),
    ]
    got = grid.sidtrace_grid(rows)
    assert [int(r[4]) for r in got] == [0x21, 0x31, 0x41, 0x51]
    assert int(grid.sidtrace_grid(rows, 1)[0][4]) == 0x21
    assert grid.sidtrace_clock(rows) == (r0, CPF)


def test_raises_that_do_not_agree_on_one_period_are_refused():
    """A gap is whole periods; anything else is loud. A late write is not: see below."""
    pytest.importorskip("pysidtracker")
    from pysidtracker.oracle import SidtraceRow  # pylint: disable=import-outside-toplevel

    def rows(*at):
        return [SidtraceRow(c + o, None, o, None, 0, 4, 1) for c, o in at]

    with pytest.raises(ValueError, match="no interrupt clock"):
        grid.sidtrace_clock(rows((0, 10)))
    with pytest.raises(ValueError, match="one period"):  # 1.5 periods between two raises
        grid.sidtrace_clock(rows((0, 10), (CPF, 10), (CPF * 5 // 2, 10)))
    mixed = [
        SidtraceRow(c + 10, None, 10 if k % 2 else None, None if k % 2 else 10, 0, 4, 1)
        for k, c in enumerate(range(0, 6 * CPF, CPF))
    ]
    with pytest.raises(ValueError, match="two interrupt sources"):  # else a doubled period
        grid.sidtrace_clock(mixed)


def test_a_write_a_whole_frame_after_its_raise_is_a_second_entry_writing():
    """An NMI writes while the tick's own interrupt is idle: the period still holds."""
    pytest.importorskip("pysidtracker")
    from pysidtracker.oracle import SidtraceRow  # pylint: disable=import-outside-toplevel

    rows = [SidtraceRow(c + o, None, o, None, 0, 4, 1) for c, o in ((0, CPF + 1), (CPF, 10))]
    assert grid.sidtrace_clock(rows) == (0, CPF)


def _obs(*writes, prev=None):
    return grid.reduce_tick(list(writes), prev)


def test_every_gate_edge_inside_one_tick_is_kept_in_order():
    """1 -> 0 -> 1 is two edges the envelope generator counts, not one level."""
    o = _obs((4, 0x11), (4, 0x10), (4, 0x11), (5, 0x0A), (0x18, 0x0F))
    assert o.edges == ((4, 0x11), (4, 0x10), (4, 0x11), (5, 0x0A))
    assert o.values[7:] == (None, 0x0F)  # res_route unwritten, mode_vol last-wins


def test_a_sixteen_bit_register_written_twice_in_a_tick_is_last_wins():
    """Hubbard's drum-then-arpeggio pair of ``$D401`` stores leaves one value."""
    o = _obs((1, 0x10), (0, 0x20), (1, 0x30))
    assert o.values[0] == 0x3020 and o.edges == ()


def test_a_tick_that_writes_one_half_carries_the_other_from_the_tick_before():
    prev = _obs((0, 0x20), (1, 0x10))
    assert prev.values[0] == 0x1020
    assert _obs((0, 0x30), prev=prev).values[0] == 0x1030
    assert _obs((1, 0x40), prev=prev).values[0] == 0x4020
    assert _obs((4, 0x11), prev=prev).values[0] == 0x1020  # neither half: carried whole
    assert _obs((4, 0x11)).values[0] is None  # and no prev leaves it unknown


def test_a_mirror_write_folds_onto_the_register_it_decodes_to():
    assert list(grid.regs([0xD400, 0xD420, 0xD7E0, 0xD412])) == [0, 0, 0, 0x12]
    assert list(grid.regs([0xD419, 0xD41F, 0xD3FF, 0xD800])) == [-1, -1, -1, -1]


def test_the_pulse_width_high_nibble_and_the_cutoff_high_bits_are_masked():
    assert _obs((2, 0xFF), (3, 0xF3)).values[3] == 0x3FF
    assert _obs((0x15, 0xFF), (0x16, 0xFF)).values[6] == 0x7FF  # 11 bits, 8 high 3 low


def test_folding_the_per_tick_reduction_over_a_run_equals_the_vectorised_one():
    rng = np.random.default_rng(7)
    n = 400
    frame = np.sort(rng.integers(0, 16, n))
    reg = rng.integers(0, grid.SID_REGS, n)
    val = rng.integers(0, 256, n)
    levels, edges = grid.reduce_run(frame, reg, val, 16)
    prev, rows = None, []
    for k in range(16):
        m = frame == k
        prev = grid.reduce_tick(list(zip(reg[m].tolist(), val[m].tolist())), prev)
        rows.append([0 if v is None else v for v in prev.values])
        assert prev.edges == edges[k]
    assert rows == levels.tolist()


def test_the_change_rule_keeps_exactly_the_writes_the_register_file_did_not_hold():
    rng = np.random.default_rng(3)
    reg = rng.integers(0, 4, 200)
    val = rng.integers(0, 3, 200)
    seed = bytes(rng.integers(0, 3, 4).tolist())
    sid, want = bytearray(seed), []
    for a, v in zip(reg.tolist(), val.tolist()):
        want.append(sid[a] != v)
        sid[a] = v
    assert list(grid.changes(reg, val, seed)) == want
