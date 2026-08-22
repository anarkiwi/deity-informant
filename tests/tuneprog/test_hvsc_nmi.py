"""The dead-NMI family (marked ``hvsc``; short horizons).

Two tunes refused as a second schedule until the gate asked whether a CIA #2
source can fire: *Alien_3* installs an NMI vector, *Jazzpjazz* loads a CIA #2
Timer-A latch, and neither ICR ever enables anything to dispatch either.
"""

import pytest

from deity_informant import c64
from deity_informant.tuneprog.machine import find_entries, frame_slots
from deity_informant.tuneprog.trace import Tracer

from _hvsc import ALIEN3, JAZZPJAZZ, decompiled, tune

pytestmark = pytest.mark.hvsc

BOTH = (ALIEN3, JAZZPJAZZ)
SECONDS = 10


def _post_init(rel):
    """``(entry, tracer)`` after this tune's init has had the machine."""
    img, sched = find_entries(tune(rel))
    return sched[0], Tracer(img, sched[0]).run_init()


def test_neither_tune_can_dispatch_an_nmi():
    """Each carries the evidence the old gate refused on, and no source to fire it."""
    _entry, tr = _post_init(ALIEN3)
    assert c64.read_vector(tr.vm.mem, c64.NMI_VEC[0])  # a vector is installed
    _entry, tr2 = _post_init(JAZZPJAZZ)
    assert tr2.vm.cia[1].latch != 0xFFFF  # a Timer-A latch is loaded
    for t in (tr, tr2):
        assert t.vm.cia[1].sources() == 0 and t.vm.cia[1].fired(t.vm.cycles) == 0


def test_both_certify_over_their_horizon():
    for rel in BOTH:
        run = decompiled(rel, seconds=SECONDS, text=False)
        assert run.v.div is None and run.v.call == run.calls
        sub = run.cert["subtunes"][0]
        assert sub["divergences"] == 0 and sub["envelope_traps"] == 0


def test_alien3_enters_through_the_hardware_vector_on_the_bare_rti_frame():
    """No KERNAL prologue runs, so the machine leaves only the status byte."""
    entry = decompiled(ALIEN3, seconds=SECONDS, text=False).prog.meta["entry"]
    assert entry["kind"] == "irq" and entry["kernal"] is False
    assert frame_slots(entry) == {1: "P"}


def test_jazzpjazz_ticks_on_the_host_not_the_cia2_latch_it_loaded():
    """CIA #2's line is the NMI, so its period is not a tick whatever it holds."""
    entry, tr = _post_init(JAZZPJAZZ)
    assert entry.source.endswith("_host_cia")
    assert entry.cycles_per_tick != tr.vm.cia[1].latch + 1
