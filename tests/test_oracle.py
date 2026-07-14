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
