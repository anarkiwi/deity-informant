"""defMON as a hand-written trackerprog: two builds of one player.

``tools/trackerprog_defmon.py`` states each tune's data in
prototype-trackerprog.md's vocabulary; the claim is section 2's certificate.
*Automatas* certifies over its whole 149,025-tick horizon under the tool's own
``--budget``/``--resume`` (docs/prototype-defmon-trackerprog.md section 3); this
suite renders the prefix named in ``CLAIMS`` so the hvsc budget stays where it is.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tuneprog"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "tools"))

from deity_informant.trackerprog import printer  # noqa: E402

import trackerprog_defmon as TD  # noqa: E402
from _hvsc import AUTOMATAS, JAZZPJAZZ, tune_file  # noqa: E402

pytestmark = pytest.mark.hvsc

# tune -> cycles per tick, the certified horizon, the prefix this suite renders,
#         the rate the entry runs at, (patterns, events, sidTAB rows, commands)
CLAIMS = {
    AUTOMATAS: (2457, 149025, 9000, 8, (113, 1621, 358, 97)),
    JAZZPJAZZ: (16422, 1799, 1799, 1, (32, 305, 97, 11)),
}
STREAMS = ["casa", "casb", "filter", "pitch_out", "voice_bit"]
ACCS = ["pw_down", "pw_turn", "pw_up", "slide_down", "slide_up"]


def tune(name):
    cycles, horizon, _, _, _ = CLAIMS[name]
    return TD.Tune(str(tune_file(name)), 0, cycles, None if name == AUTOMATAS else horizon)


def built(name):
    return tune(name).build()


@pytest.mark.parametrize("name", sorted(CLAIMS))
def test_each_build_certifies_on_the_universal_player(name):
    cycles, _, prefix, _, shape = CLAIMS[name]
    obj = built(name)
    doc, done = TD.certify(str(tune_file(name)), 0, obj, prefix, cycles, 0.0, None)
    assert done and doc["divergence"] is None
    assert doc["ticks"] == prefix
    assert doc["identical_ticks"] == prefix  # not a permutation: the same list
    assert (
        len(obj["score"]["patterns"]),
        sum(len(p["events"]) for p in obj["score"]["patterns"].values()),
        len(obj["streams"]["casa"]["rows"]),
        len(obj["score"]["commands"]),
    ) == shape


@pytest.mark.parametrize("name", sorted(CLAIMS))
def test_the_two_builds_differ_in_their_multispeed_and_their_data(name):
    """The version difference is the entry's rate and the note column's mask."""
    _, _, _, rate, _ = CLAIMS[name]
    obj = built(name)
    t = obj["meta"]["tempo"]  # the entry's rate, as the one clock form's own divider
    assert (t["cell"], t["step"], t["rate"], t["phase"]) == ("rowsleft", -1, rate, 0)
    assert obj["meta"]["shadow"]["registers"] == TD.FLUSH  # the cutoff is not the image's
    assert 22 not in TD.FLUSH
    assert sorted(obj["streams"]) == STREAMS
    assert sorted(obj["accs"]) == ACCS
    ends = {o["end"] if isinstance(o["end"], str) else "jump" for o in obj["score"]["orders"]}
    assert ends == ({"jump"} if name == AUTOMATAS else {"horizon"})


@pytest.mark.parametrize("name", sorted(CLAIMS))
def test_the_instrument_is_a_stream_and_nothing_else(name):
    """defMON's sound definition is a sidTAB row, so its instrument is a re-point."""
    obj = built(name)
    assert list(obj["instruments"]) == ["0"]
    ins = obj["instruments"]["0"]
    assert "adsr" not in ins and "prelude" not in ins  # both are the sidTAB row's own
    assert [t for r in ins["on_note"] for t, _ in r["sets"]] == ["@freq_idx", "@acc", "@osc"]
    for cmd in obj["score"]["commands"].values():
        assert list(cmd) == ["rows"] and all("point" in r for r in cmd["rows"])


def _byte(e, path):
    for k in path:
        e = e[k]
    return e


def _record(row):  # noqa: C901 - one clause per column of the record
    """The sidTAB record an object row is, byte for byte, back out of its sets."""
    head, tail, m0, m1 = {}, {}, 0, 0
    for t, v in row.get("sets", ()):
        if t == "@ctrl":
            m0, head[0x40] = m0 | 0x40, v
        elif t == "@ctrl_eor":
            m0, head[0x80] = m0 | 0x80, v
        elif t == "ad":
            m0, head[0x20] = m0 | 0x20, v
        elif t == "sr":
            m0, head[0x10] = m0 | 0x10, v
        elif t == "@freq_idx":
            m0, head[0x08] = m0 | 0x08, _byte(v, ("field", 0, "add", 0))
        elif t == "@osc":
            m0, head[0x04] = m0 | 0x04, v
        elif t == "shadow.pw.hi":
            m0, head[0x02] = m0 | 0x02, v
        elif t == "@pwstep":
            m1, tail[0x80] = m1 | 0x80, v
        elif t == "#res_route":
            m1 = m1 | 0x40
            tail[0x40] = 0 if "and" in v else _byte(v, ("or", 0, "or", 1))
        elif t == "#mode_vol":
            m1, tail[0x20] = m1 | 0x20, v
        elif t == "#flt_base":
            m1, tail[0x10] = m1 | 0x10, v
        elif t == "#flt_acc":
            m1, tail[0x08] = m1 | 0x08, [v & 0xFF, v >> 8]
        elif t == "#flt_step" and 0x08 not in tail:
            m1, tail[0x08] = m1 | 0x08, [v & 0xFF, 0x80 | v >> 8]
        elif t == "#flt_dir" and v:
            tail[0x08][1] |= 0x40
    out = [m0] + [head[k] for k in (0x40, 0x80, 0x20, 0x10, 0x08, 0x04, 0x02) if k in head]
    out.append(m1)
    for k in (0x80, 0x40, 0x20, 0x10):
        if k in tail:
            out.append(tail[k])
    return out + tail.get(0x08, [])


@pytest.mark.parametrize("name", sorted(CLAIMS))
def test_every_byte_of_the_tune_s_data_is_in_the_object(name):
    """The sidTAB, the arranger and the patterns, byte for byte, out of the object."""
    x = tune(name)
    obj = x.build()
    rows = obj["streams"]["casa"]["rows"]
    waits = set(range(len(rows))) - set(x.act.values()) - set(x.jump.values()) - {0}
    for i, r in sorted(x.act.items()):
        at = x.addr(i)
        got = _record(rows[r])
        assert got == list(x.m[at : at + len(got)]), "sidTAB row %d" % i
        assert x.delay(i) == _delay(rows, r, waits), "sidTAB delay %d" % i
    for k, r in x.jump.items():
        assert rows[r] == {"jump": x.act[x.enter(k)]}
    cols = (x.L["col0"], x.L["col1"], x.L["col2"])
    for step, o in enumerate(obj["score"]["orders"]):
        for i in range(len(o["play"])):
            assert x.m[cols[step] + i] == _pattern_no(x, obj, step, i), (step, i)


def _delay(rows, r, waits):
    """A row's own delay: the hold it goes on to, and the bit 7 that ends the stream."""
    nxt = rows[r]["next"]
    if nxt in waits:
        return (0x80 if rows[nxt]["next"] == 0 else 0) | rows[nxt]["hold"]
    return 0x80 if nxt == 0 else 0


def _pattern_no(x, obj, voice, step):
    """Which source pattern an order step names: the one whose rows it materialised."""
    ev = obj["score"]["patterns"][str(obj["score"]["orders"][voice]["play"][step]["pattern"])]
    n = x.m[(x.L["col0"], x.L["col1"], x.L["col2"])[voice] + step]
    rows = x.patrows(x.pattern_at(n))
    assert len(ev["events"]) <= len(rows)
    for e, r in zip(ev["events"], rows):
        assert e["note"] == r["note"] and e["sounds"] == (r["note"] is not None)
        assert (e["arm"] or []) == [
            "cascade.%s:%02X" % (s, r[s]) for s in ("a", "b") if r[s] is not None
        ]
    return n


@pytest.mark.parametrize("name", sorted(CLAIMS))
def test_the_event_is_the_canonical_one(name):
    """One field says a row sounds; the note column holds a pitch or nothing."""
    obj = built(name)
    for pat in obj["score"]["patterns"].values():
        for e in pat["events"]:
            assert set(e) == {"dur", "sounds", "tie", "gate", "note", "ins", "arm"}
            assert isinstance(e["sounds"], bool) and e["dur"] >= 1
            assert e["tie"] is False and e["gate"] is None and e["ins"] is None
            assert (e["note"] is not None) == e["sounds"]


@pytest.mark.parametrize("name", sorted(CLAIMS))
def test_no_number_outside_the_tuning_exists_anywhere(name):
    """A pitch table is a pitch table: every note the score plays is a row of it."""
    obj = built(name)
    p = obj["pitch"]
    for pat in obj["score"]["patterns"].values():
        for e in pat["events"]:
            if e["note"] is not None:
                assert p["base"] <= e["note"] < p["base"] + len(p["freq"])
    assert p["base"] < 0 < len(p["freq"])  # the slide's window lies below the notes
    x = tune(name)
    assert p["note_count"] == x.L["notes"]  # where the stored table ends
    assert len(p["freq"]) > p["note_count"]  # and what is read past it
    assert p["freq"] == [
        x.m[x.L["pitch_lo"] + n] | x.m[x.L["pitch_hi"] + n] << 8
        for n in range(p["base"], p["base"] + len(p["freq"]))
    ]


@pytest.mark.parametrize("name", sorted(CLAIMS))
def test_no_command_is_named_by_the_index_its_player_dispatched_on(name):
    """A command is named by what it does: which cascade it starts, and where."""
    obj = built(name)
    for k, cmd in obj["score"]["commands"].items():
        what, byte = k.split(":")
        assert what in ("cascade.a", "cascade.b")
        assert cmd["rows"][0]["point"][0][0] == "cas" + what[-1]
        assert "id" not in cmd
        assert int(byte, 16) < 0x100
    assert obj["meta"]["row_command"] == "spent"


def test_the_print_carries_the_forms_and_measures_itself():
    text = printer.render(built(JAZZPJAZZ))
    for line in (
        "tick       16422 cycles; tempo rowsleft -1, row at rowsleft >= $80",
        "sequencer  the row shares the voice's tick",
        "tick       row ; machine",
        "shadow     23 registers, flushed in the image's own order at the head of every tick",
    ):
        assert line in text, line
    n = printer.numbers(text)
    assert n["blocks"] == 7 and n["data_rows"] == n["statements"] > 0
