"""The NMI families (marked ``hvsc``; short horizons).

*Alien_3* installs an NMI vector and *Jazzpjazz* loads a CIA #2 Timer-A latch,
and neither ICR ever enables anything to dispatch either -- dead evidence. JCH's
*Easy Does It* is the live one: a sample mixer on CIA #2 Timer A at ~5 kHz,
preempting a raster-driven play routine ([playroutine-anatomy.md] 3.5).
"""

import pytest

from deity_informant import c64
from deity_informant.tuneprog.machine import find_entries, frame_slots
from deity_informant.tuneprog.trace import Tracer

from _hvsc import ALIEN3, EASY, JAZZPJAZZ, decompiled, tune

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


EASY_TICKS = 40


def _easy():
    return decompiled(EASY, seconds=EASY_TICKS * 19656 / 985248, text=False)


def test_easy_does_it_is_one_program_over_two_entries():
    """The NMI mixer is the schedule's second entry, on the vector the port dispatches."""
    run = _easy()
    sched = run.prog.meta["schedule"]
    assert [e["kind"] for e in sched] == ["irq", "nmi"]
    assert sched[1] == {
        "kind": "nmi",
        "addr": 0x40E9,
        "cycles_per_tick": 193,
        "source": "cia2_timer_a",
    }
    assert run.prog.meta["nmi_procs"] == ["nmi"]


def test_easy_does_it_verifies_under_the_traced_interleaving():
    run = _easy()
    sub = run.cert["subtunes"][0]
    assert run.v.div is None and sub["divergences"] == 0 and sub["envelope_traps"] == 0
    assert sub["nmis"] > 100 * sub["ticks"] * 0.9  # ~19656/193 preemptions a tick
    assert "nmi preemption schedule" in run.cert["compared"]


def test_the_mixer_owns_d418_and_the_play_routine_owns_the_rest():
    """Anatomy 3.5.5: ``$D418`` is written only by the NMI, and thousands of times a frame."""
    import numpy as np

    log = _easy().trace.wlog
    addr = np.asarray(log["addr"])
    assert int((addr == 0xD418).sum()) > 100 * EASY_TICKS * 0.9
    assert int(((addr >= 0xD400) & (addr <= 0xD417)).sum()) > 0
