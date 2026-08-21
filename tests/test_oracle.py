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

_CACHE = Path(os.environ.get("DEITY_ORACLE_CACHE", ".oracle-cache"))
_PW = set(reg.PW_HI_REGS)

FRAMES = 3000

CASES = ["Monty_on_the_Run.sid", "Commando.sid", "A_Mind_Is_Born.sid"]


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
    from deity_informant.tuneprog.machine import find_entries  # pylint: disable=C0415
    from deity_informant.tuneprog.trace import Tracer  # pylint: disable=C0415

    img, schedule = find_entries(path.read_bytes())
    tr = Tracer(img, schedule[0])
    tr.run_init()
    tr.run_calls(nframes)
    return tr.trace()


def _bycall(trace, nframes):
    """A grid built the old way: every write in the frame of the tick that issued it."""
    log = trace.wlog
    addr = np.asarray(log["addr"], dtype=np.int64) - grid.SID_BASE
    keep = (addr >= 0) & (addr < grid.SID_REGS)
    call = np.asarray(log["call"], dtype=np.int64)[keep]
    return grid.grid(
        np.where(call < 0xFFFFFFF, call, -1), addr[keep], np.asarray(log["val"])[keep], nframes
    )


@pytest.mark.oracle
def test_a_player_run_with_io_banked_out_writes_no_register():
    """Puterman's V20 wrapper: only its flush reaches the chip, and the oracle agrees.

    With the 6510 port's direction byte wrong the player's own 25 writes a frame
    reach the SID as well and every frame differs. The wrapper spends 168 ->
    10,248 cycles inside one tick, so the frame a write lands in is its cycle's.
    """
    path = _tune(KNOB)
    expected = _oracle(path, FRAMES)
    trace = _trace(path, FRAMES)
    bad = grid.differing(expected, grid.trace_grid(trace, FRAMES))
    assert not bad.size, "frames %s differ" % bad[:3]
    rounded = oracle_grid(
        path, oracle_cache=_CACHE / "csv", seconds=FRAMES // 50 + 2, frames=FRAMES
    )
    assert len(grid.differing(rounded, _bycall(trace, FRAMES))) > 200  # measured: 297
