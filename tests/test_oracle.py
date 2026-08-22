"""Per-frame SID register-grid oracle test: deity's VM vs the sidtrace oracle.

Renders a tune with deity's own P-Code VM and compares ``$D400..$D418`` per frame
to the Dockerized ``sidplayfp``/``sidtrace`` oracle, both grids framed by
:mod:`deity_informant.tuneprog.grid`. Marked ``oracle``; tunes fetch to a cache.
"""

import os
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("pysidtracker")

from pysidtracker import registers as reg  # noqa: E402
from pysidtracker.image import SidImage  # noqa: E402
from pysidtracker.trace import trace_init  # noqa: E402
from pysidtracker.oracle import aligned_match  # noqa: E402
from pysidtracker.testing import TuneFetchError, oracle_grid  # noqa: E402

from deity_informant import PcodeVM, lift, run_irq, run_sub  # noqa: E402
from deity_informant.tuneprog import grid, tunes  # noqa: E402
from deity_informant.tuneprog.machine import Entry, find_entries  # noqa: E402

_CACHE = Path(os.environ.get("DEITY_ORACLE_CACHE", ".oracle-cache"))
_PW = set(reg.PW_HI_REGS)

FRAMES = 3000

COMMANDO = "Commando.sid"
CASES = ["Monty_on_the_Run.sid", COMMANDO, "A_Mind_Is_Born.sid"]


def _tune(name):
    path = tunes.resolve(name)
    if path is None:
        raise TuneFetchError("%s unavailable (offline, not cached)" % name)
    return path


def _oracle(path, frames):
    """The oracle's grid, framed by the CSV's own interrupt clock."""
    rows = grid.sidtrace_grid(grid.oracle_rows(path, _CACHE / "csv", seconds=frames // 50 + 2))
    assert len(rows) >= frames, "oracle only %d frames (< %d) -- short/stale render" % (
        len(rows),
        frames,
    )
    return [r.tolist() for r in rows[:frames]]


def _snapshot(vm):
    m = vm.mem
    return [(m[0xD400 + i] & 0xF) if i in _PW else m[0xD400 + i] for i in range(reg.SID_REG_COUNT)]


def render(data, nframes):
    """Render a tune on deity's VM into a per-frame ``$D400..$D418`` grid.

    Header-play tunes drive one ``run_sub(play)`` per frame; handler-driven
    tunes (``play == 0``) drive ``run_irq`` on the init trace's IRQ vector.
    """
    img = SidImage.from_bytes(data)
    header = img.header
    vm = PcodeVM(bytes(img.mem))
    vm.mem[0xD418] = 0x0F  # PSID cold-start: maximum volume
    cache = {}
    run_sub(vm, header.init_address, cache, lift)
    rows = []
    if header.play_address:
        for _ in range(nframes):
            run_sub(vm, header.play_address, cache, lift)
            rows.append(_snapshot(vm))
    else:
        trace = trace_init(img, play_calls=0)
        handler = trace.irq_vector or trace.hw_irq_vector
        for _ in range(nframes):
            run_irq(vm, handler, cache, lift)
            rows.append(_snapshot(vm))
    return rows


@pytest.mark.oracle
@pytest.mark.parametrize("name", CASES)
def test_render_matches_oracle(name):
    """deity's grid matches the oracle byte-exact over ``FRAMES`` frames.

    Header-play (``monty``/``commando``) and handler-driven (``A_Mind_Is_Born``)
    RSID all reproduce full-length: one play call is one interrupt period, the
    frame the oracle's own writes are attributed to.
    """
    path = _tune(name)
    expected = _oracle(path, FRAMES)
    rendered = render(path.read_bytes(), len(expected))
    assert aligned_match(
        expected, rendered, max_lead=4
    ), "%s: deity render != sidtrace oracle over %d frames" % (name, len(expected))


KNOB = "I_Could_Eat_a_Knob_at_Night.sid"


def _trace(path, nframes):
    """``nframes`` ticks of the tuneprog tracer on ``path``."""
    from deity_informant.tuneprog.trace import Tracer  # pylint: disable=C0415

    img, schedule = find_entries(path.read_bytes())
    tr = Tracer(img, schedule[0])
    tr.run_init()
    tr.run_calls(nframes)
    return tr.trace()


@pytest.mark.oracle
def test_a_player_run_with_io_banked_out_writes_no_register():
    """Puterman's V20 wrapper: only its flush reaches the chip, and the oracle agrees.

    With the 6510 port's direction byte wrong the player's own 25 writes a frame
    reach the SID as well and every frame differs. Its ramp (168 -> 10,248 cycles
    a tick) stays inside the frame, so the 297 measure the anchor, not the rules.
    """
    path = _tune(KNOB)
    interrupt = _oracle(path, FRAMES)
    trace = _trace(path, FRAMES)
    cycle, tick = grid.trace_grid(trace, FRAMES), grid.tick_grid(trace, FRAMES)
    bad = grid.differing(interrupt, cycle)
    assert not bad.size, "frames %s differ" % bad[:3]
    rounded = oracle_grid(
        path, oracle_cache=_CACHE / "csv", seconds=FRAMES // 50 + 2, frames=FRAMES
    )
    # which side the delta is on: the trace's rule changes nothing, the oracle's
    # anchor changes 297 frames either way, so the anchor is the whole of it
    assert not grid.differing(interrupt, tick).size
    assert len(grid.differing(rounded, cycle)) > 200 and len(grid.differing(rounded, tick)) > 200


MIND = "A_Mind_Is_Born.sid"


@pytest.mark.oracle
def test_a_cinv_handler_matches_the_oracle_frame_for_frame():
    """The KERNAL entry frame is a machine-model claim, so the grid is what checks it.

    lft's handler is installed at CINV and chains to ``$EA31``, whose epilogue pops
    the three bytes the ``$FF48`` prologue saved: it reaches its ``RTI`` only
    because the tracer pushes them (:func:`~.machine.entry_frame`).
    """
    path = _tune(MIND)
    interrupt = _oracle(path, FRAMES)
    trace = _trace(path, FRAMES)
    assert trace.meta["entry"] == {
        "kind": "irq",
        "addr": 0x0031,
        "cycles_per_tick": 16422,
        "source": "pal_host_cia",
        "kernal": True,
    }
    bad = grid.differing(interrupt, grid.trace_grid(trace, FRAMES))
    assert not bad.size, "frames %s differ" % bad[:3]


JODLER = "Jodler.sid"
AUTOMATAS = "Automatas.sid"
JAZZPJAZZ = "Jazzpjazz.sid"
CADENCES = [  # the driver's raster; an armed latch of its own; the driver's CIA; the KERNAL's
    (COMMANDO, Entry("sub", 0x5012, 19656, "pal_video")),
    (AUTOMATAS, Entry("sub", 0x0FE3, 2457, "cia_timer")),
    (JODLER, Entry("irq", 0xC738, 16422, "pal_host_cia", True)),
    (MIND, Entry("irq", 0x0031, 16422, "pal_host_cia", True)),
]
CAD_FRAMES = 900


@pytest.mark.oracle
def test_a_cia2_latch_that_arms_nothing_is_not_the_cadence():
    """*Jazzpjazz* loads a `$DD04`/`$DD05` latch and enables no source to dispatch it.

    CIA #2's line is the NMI, so the tick is the driver's CIA, and the oracle's own
    raises say so: a wrong period does not divide the gaps between the interrupts
    it attributes its writes to. The grid is not compared -- this player polls the
    raster, which the VM does not model, and the certificate pins those reads.
    """
    path = _tune(JAZZPJAZZ)
    data = path.read_bytes()
    entry = find_entries(data)[1][0]
    latch = trace_init(SidImage.from_bytes(data)).cia2_timer_latch
    assert entry == Entry("sub", 0x1003, 16422, "pal_host_cia")
    assert latch is not None and entry.cycles_per_tick != latch + 1
    rows = grid.oracle_rows(path, _CACHE / "csv", seconds=CAD_FRAMES // 50 + 2)
    gaps = np.diff(_raises(rows))
    assert gaps.size and not (gaps % entry.cycles_per_tick).any()


def _raises(rows):
    """The instant of every interrupt the CSV attributes a write to."""
    src = "since_video_irq" if any(r.since_video_irq is not None for r in rows) else "since_cia_irq"
    return sorted({r.cycle - getattr(r, src) for r in rows if getattr(r, src) is not None})


@pytest.mark.oracle
@pytest.mark.parametrize("name,want", CADENCES, ids=[c[0][:6] for c in CADENCES])
def test_the_cadence_is_the_oracles_own_interrupt_period(name, want):
    """Each cadence class, decided against ``sidplayfp``'s raises and its grid.

    The raises pin the period (a wrong cadence does not divide their gaps, which
    are whole ticks of the real one) and framing both grids on the cadence under
    test pins the phase. The CSV carries only the raises a write fell in, so both
    grids are anchored at their own first written frame.
    """
    path = _tune(name)
    _img, schedule = find_entries(path.read_bytes())
    entry = schedule[0]
    assert entry == want
    rows = grid.oracle_rows(path, _CACHE / "csv", seconds=CAD_FRAMES // 50 + 2)
    at = _raises(rows)
    gaps = np.diff(at)
    assert gaps.size and not (gaps % entry.cycles_per_tick).any(), "%d does not tile %s" % (
        entry.cycles_per_tick,
        sorted(set(gaps.tolist()))[:3],
    )
    trace = _trace(path, CAD_FRAMES)
    cyc, _reg, _val, call = grid.sid_writes(trace)
    played = cyc[call != grid.INIT_CALL]
    lead = int(grid.frames(played[:1], trace.meta["cycles_init"], entry.cycles_per_tick)[0])
    want_grid = grid.sidtrace_grid(rows, first=at[0], cycles_per_frame=entry.cycles_per_tick)
    got = grid.trace_grid(trace, CAD_FRAMES)[lead:]
    n = min(len(want_grid), len(got))
    assert n > CAD_FRAMES // 2, "%s: only %d frames to compare" % (name, n)
    bad = grid.differing(want_grid[:n], got[:n])
    assert not bad.size, "%s: frames %s of %d differ" % (name, bad[:3], n)
