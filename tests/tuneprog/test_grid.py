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
