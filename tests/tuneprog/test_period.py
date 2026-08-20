"""The periodicity classifier: smallest periods, counter blockers, aperiodic tunes."""

from deity_informant.tuneprog.machine import Entry
from deity_informant.tuneprog.period import Samples, classify, min_period
from deity_informant.tuneprog.trace import Tracer

from _asm import asm, sid_image

PLAY = 0x1000
INIT = 0x1200


def _sample(blocks, calls, data=None):
    img = sid_image(blocks, INIT, PLAY, data, 0x1000)
    t = Tracer(img, Entry("sub", PLAY, 19656, "test"))
    t.run_init()
    s = Samples()
    for _ in range(calls):
        t.run_calls(1)
        s.add(t.vm)
    return classify(s)


def test_min_period_is_the_smallest_one_including_a_partial_last_block():
    assert min_period(b"aaaa") == 1
    assert min_period(b"abcabcabc") == 3
    assert min_period(b"abcab") == 3
    assert min_period(b"abcd") is None
    assert min_period([]) is None


def test_a_counter_read_only_masked_leaves_the_observable_periodic():
    # the cell's period is 256, the tune's is 8: the counter alone blocks a repeat
    play = asm(PLAY, "INC $1400", "LDA $1400", "AND #$07", "STA $D400", "RTS")
    doc = _sample({PLAY: play, INIT: asm(INIT, "RTS")}, 100, data={0x1400: 0})
    assert doc["observable_period"] == 8
    assert doc["loop"] == 8
    assert [b["addr"] for b in doc["blockers"]] == ["$1400"]
    assert doc["blockers"][0]["period"] is None
    assert doc["verdict"] == "state only"


def test_a_counter_whose_full_value_reaches_the_sid_refuses():
    # the SID stream carries the counter's full width, so nothing repeats
    play = asm(PLAY, "INC $1400", "LDA $1400", "STA $D400", "RTS")
    doc = _sample({PLAY: play, INIT: asm(INIT, "RTS")}, 100, data={0x1400: 0})
    assert doc["observable_period"] is None
    assert doc["verdict"] == "aperiodic"


def test_a_state_that_repeats_is_periodic_with_no_blockers():
    play = asm(PLAY, "LDA $1400", "EOR #$FF", "STA $1400", "STA $D400", "RTS")
    doc = _sample({PLAY: play, INIT: asm(INIT, "RTS")}, 40, data={0x1400: 0})
    assert doc["observable_period"] == 2
    assert doc["blockers"] == []
    assert doc["verdict"] == "periodic"


def test_an_accumulator_drifting_by_a_constant_per_loop_is_a_blocker():
    # the loop is 2 ticks and the accumulator adds 3 a tick: a constant drift of 6
    play = asm(
        PLAY,
        "LDA $1400",
        "EOR #$FF",
        "STA $1400",
        "STA $D400",
        "LDA $1401",
        "CLC",
        "ADC #$03",
        "STA $1401",
        "RTS",
    )
    doc = _sample({PLAY: play, INIT: asm(INIT, "RTS")}, 60, data={0x1400: 0, 0x1401: 0})
    assert doc["observable_period"] == 2
    assert [b["addr"] for b in doc["blockers"]] == ["$1401"]
    assert doc["blockers"][0]["drift"] == [6]
    assert doc["verdict"] == "state only"
