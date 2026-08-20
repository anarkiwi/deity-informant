"""Per-frame SID register-grid oracle test: deity's VM vs the sidtrace oracle.

Renders a tune with deity's own P-Code VM and compares ``$D400..$D418`` per
frame to the Dockerized ``sidplayfp``/``sidtrace`` oracle. Marked ``oracle``
(excluded from the default suite); HVSC tunes fetch to a gitignored cache.
"""

import os
from pathlib import Path

import pytest

pytest.importorskip("pysidtracker")

from pysidtracker import registers as reg  # noqa: E402
from pysidtracker.image import SidImage  # noqa: E402
from pysidtracker.trace import trace_init  # noqa: E402
from pysidtracker.oracle import aligned_match  # noqa: E402
from pysidtracker.testing import TuneFetchError, oracle_grid, resolve_tune  # noqa: E402

from deity_informant import PcodeVM, lift, run_irq, run_sub  # noqa: E402

_CACHE = Path(os.environ.get("DEITY_ORACLE_CACHE", ".oracle-cache"))
_PW = set(reg.PW_HI_REGS)

FRAMES = 3000

CASES = [
    ("monty", "MUSICIANS/H/Hubbard_Rob/Monty_on_the_Run.sid"),
    ("commando", "MUSICIANS/H/Hubbard_Rob/Commando.sid"),
    ("A_Mind_Is_Born", "MUSICIANS/L/Lft/A_Mind_Is_Born.sid"),
]


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
@pytest.mark.parametrize("tune_id,relpath", CASES, ids=[c[0] for c in CASES])
def test_render_matches_oracle(tune_id, relpath):
    """deity's grid matches the oracle byte-exact over ``FRAMES`` frames.

    Header-play (``monty``/``commando``) and handler-driven (``A_Mind_Is_Born``)
    RSID all reproduce full-length. The length assert fails loud on a short/stale
    oracle render rather than silently under-validating.
    """
    path = resolve_tune(relpath, cache_dir=_CACHE / "hvsc")
    if path is None:
        raise TuneFetchError(f"tune {tune_id} unavailable (offline, not cached)")
    expected = oracle_grid(
        path, oracle_cache=_CACHE / "csv", seconds=FRAMES // 50 + 2, frames=FRAMES
    )
    assert (
        len(expected) >= FRAMES
    ), f"{tune_id}: oracle only {len(expected)} frames (< {FRAMES}) -- short/stale render"
    rendered = render(Path(path).read_bytes(), len(expected))
    assert aligned_match(
        expected, rendered, max_lead=4
    ), f"{tune_id}: deity render != sidtrace oracle over {len(expected)} frames"


KNOB = "MUSICIANS/P/Puterman/I_Could_Eat_a_Knob_at_Night.sid"
BANKED_FRAMES = 500


def _tick_grid(path, nframes):
    """Per-tick ``$D400..$D418`` from the tuneprog tracer's own SID write log.

    The log holds what reached the chip, so a player that writes the RAM under
    the SID (I/O banked out) contributes nothing to it -- which is the point.
    """
    from deity_informant.tuneprog.machine import find_entries
    from deity_informant.tuneprog.trace import Tracer

    img, schedule = find_entries(Path(path).read_bytes())
    tr = Tracer(img, schedule[0])
    tr.run_init()
    tr.run_calls(nframes)
    log = tr.trace().wlog
    rows, cur, i = [], [0] * reg.SID_REG_COUNT, 0
    calls = [int(c) for c in log["call"]]
    while i < len(calls) and calls[i] > nframes:
        i += 1  # the init phase logs call = 0xFFFFFFFF
    for frame in range(nframes):
        while i < len(calls) and calls[i] == frame:
            a = int(log["addr"][i]) - 0xD400
            if 0 <= a < reg.SID_REG_COUNT:
                cur[a] = int(log["val"][i])
            i += 1
        rows.append([(cur[k] & 0xF) if k in _PW else cur[k] for k in range(reg.SID_REG_COUNT)])
    return rows


@pytest.mark.oracle
def test_a_player_run_with_io_banked_out_writes_no_register():
    """Puterman's V20 wrapper: only its flush reaches the chip, and the oracle agrees.

    With the 6510 port's direction byte wrong, ``STA $01`` banks nothing, the
    player's own 25 writes a frame reach the SID as well, and every frame differs.
    """
    path = resolve_tune(KNOB, cache_dir=_CACHE / "hvsc")
    if path is None:
        raise TuneFetchError("Puterman/I_Could_Eat_a_Knob_at_Night unavailable")
    expected = oracle_grid(
        path, oracle_cache=_CACHE / "csv", seconds=BANKED_FRAMES // 50 + 2, frames=BANKED_FRAMES
    )
    assert len(expected) >= BANKED_FRAMES, f"oracle only {len(expected)} frames -- short render"
    rendered = _tick_grid(path, len(expected))
    bad = [i for i, (a, b) in enumerate(zip(expected, rendered)) if a != b]
    assert not bad, f"frames {bad[:3]} differ: {expected[bad[0]]} != {rendered[bad[0]]}"
