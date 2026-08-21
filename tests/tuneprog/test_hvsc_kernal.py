"""The installed-handler family the KERNAL dispatches (marked ``hvsc``; short horizons).

The two evidence tunes of the CINV entry convention: a CINV handler is entered
with A/X/Y on the frame below the status byte, so the ``$EA31``/``$EA81``
epilogue pops the machine's own three bytes and the tick reaches its ``RTI``.
"""

import pytest

from deity_informant.tuneprog import frames
from deity_informant.tuneprog.machine import frame_slots

from _hvsc import JODLER, PROFESSOR, decompiled

pytestmark = pytest.mark.hvsc

BOTH = ((JODLER, 14), (PROFESSOR, 15))


def test_both_are_cinv_entries_certified_over_their_horizon():
    for rel, secs in BOTH:
        run = decompiled(rel, seconds=secs, text=False)
        assert run.entry.kind == "irq" and run.entry.kernal
        assert run.v.div is None and run.v.call == run.calls
        sub = run.cert["subtunes"][0]
        assert sub["divergences"] == 0 and sub["envelope_traps"] == 0
        assert sub["inputs_pinned"] == 0  # nothing outside the program is pinned
        assert run.prog.meta["stack"] == "eliminated"


def test_the_entry_frame_of_each_is_the_four_bytes_the_kernal_left():
    """Y/X/A at ``SP+1..3`` and the status at ``SP+4``, all four consumed as values."""
    for rel, secs in BOTH:
        run = decompiled(rel, seconds=secs, text=False)
        entry = run.prog.meta["entry"]
        assert frame_slots(entry) == {4: "P", 3: 0, 2: 1, 1: 2}
        assert sorted(frames.contract(run.prog)[run.prog.meta["tick_proc"]]) == [1, 2, 3, 4]


def test_jodler_closes_on_its_own_period():
    """A KERNAL-dispatched tick is periodic like any other, and on its own witness."""
    run = decompiled(JODLER, seconds=16, until_period=True, text=False)
    sub = run.cert["subtunes"][0]
    assert sub["complete"] and sub["period"] == sub["trace_period"] > 0
    assert sub["first_repeat"] == sub["trace_first_repeat"] == run.trace.meta["first_repeat"]
