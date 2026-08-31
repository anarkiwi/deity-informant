"""JCH V20 as a hand-written trackerprog: two builds of one player.

``tools/trackerprog_jch.py`` states each tune's data in prototype-trackerprog.md's
vocabulary; the claim is section 2's certificate.  *Guldkornekspressen Intro*
certifies over its whole 2,401-tick horizon and re-verifies its loop; *I Could
Eat a Knob at Night* certifies over its whole 8,577 under the tool itself
(docs/prototype-jch-trackerprog.md section 3), and this suite renders the prefix
named in ``CLAIMS`` so the hvsc budget stays where it is.
"""

import sys
from functools import lru_cache
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tuneprog"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "tools"))

from deity_informant.trackerprog import printer  # noqa: E402

import trackerprog_jch as TJ  # noqa: E402
from _hvsc import EASY, GULDKORN, KNOB, tune_file  # noqa: E402

pytestmark = pytest.mark.hvsc

CERTS = Path(__file__).resolve().parent.parent.parent / "docs" / "certificates"
# tune -> its certificate, the prefix this suite renders, and
#         (patterns, events, tuning, pulse rows, filter rows, wave rows, commands, instruments)
CLAIMS = {
    GULDKORN: ("jch-guldkorn-intro", None, (27, 723, 96, 20, 23, 64, 13, 19)),
    KNOB: ("jch-knob-at-night", 2000, (18, 839, 80, 3, 2, 19, 0, 5)),
}
STREAMS = [
    "channel",
    "filter",
    "notestage",
    "pitch",
    "prelude",
    "pulse",
    "voicebits",
    "wave",
    "wavetab",
    "writeout",
]
ACCS = [
    "filter.step",
    "pulse.step",
    "slide",
    "vibrato",
    "vibrato.ramp",
    "vibrato.turn",
    "wave.step",
]


def claim(name):
    """What the committed tuneprog certificate says: the cadence and the horizon."""
    return TJ.claim(str(CERTS / (CLAIMS[name][0] + ".json")), 0)


@lru_cache(maxsize=None)
def tune(name):
    """One reading per tune per worker: the object is a pure function of the band."""
    loop, ticks, cycles, _, _ = claim(name)
    x = TJ.Tune(str(tune_file(name)), 0, cycles, None if loop else ticks)
    x.built = x.build()  # rendering copies the state it moves, so one object serves all
    return x


def built(name):
    return tune(name).built


@pytest.mark.parametrize("name", sorted(CLAIMS))
def test_each_build_certifies_on_the_universal_player(name):
    loop, ticks, cycles, _, end = claim(name)
    obj = built(name)
    prefix = CLAIMS[name][1] or ticks
    doc, done = TJ.certify(str(tune_file(name)), 0, obj, prefix, cycles)
    assert done and doc["divergence"] is None and doc["diverged"] == 0
    assert doc["ticks"] == prefix
    assert doc["identical_ticks"] + doc["permuted_ticks"] == prefix  # the same writes
    assert (
        len(obj["score"]["patterns"]),
        sum(len(p["events"]) for p in obj["score"]["patterns"].values()),
        len(obj["pitch"]["freq"]),
        len(obj["streams"]["pulse"]["rows"]),
        len(obj["streams"]["filter"]["rows"]),
        len(obj["streams"]["wavetab"]["rows"]),
        len(obj["score"]["commands"]),
        len(obj["instruments"]),
    ) == CLAIMS[name][2]
    if loop:
        assert TJ.loop_holds(obj, loop)
    if end == "fixed_point" and prefix == ticks:
        assert TJ.fixed_point(obj, ticks)


def test_the_sample_build_refuses_by_name_with_its_cell():
    """A nibble stream is not a score: the third V20 tune refuses, and emits nothing."""
    with pytest.raises(TJ.Refused) as e:
        TJ.build(str(tune_file(EASY)))
    r = e.value.refusal
    assert (r.why, r.cell) == ("sample stream", "mode_vol") and r.site and r.detail
    assert TJ.main([str(tune_file(EASY)), "--ticks", "10"]) == 3


def test_the_song_that_ends_is_a_fixed_point_and_not_a_loop():
    """Period one is no loop: the state stops, and the score runs to the first repeat."""
    loop, ticks, _, _, end = claim(KNOB)
    assert loop is None and end == "fixed_point" and ticks == 8577
    obj = built(KNOB)
    assert obj["score"]["orders"][0]["end"] == "horizon"
    assert [o["end"] for o in built(GULDKORN)["score"]["orders"]] == [{"jump": 0}] * 3


@pytest.mark.parametrize("name", sorted(CLAIMS))
def test_the_two_builds_disagree_about_having_a_shadow(name):
    """One build banks the chip out and flushes its own copy; the other writes as it goes."""
    obj, x = built(name), tune(name)
    assert x.L["wrapper"] == (name == KNOB)
    assert ("shadow" in obj["meta"]) == (name == KNOB)
    assert sorted(obj["streams"]) == sorted(STREAMS + (["wrapdata"] if name == KNOB else []))
    assert sorted(obj["accs"]) == ACCS
    assert obj["meta"]["commit_order"] == ["ad", "sr", "ctrl"]
    if name == KNOB:  # the same 25 registers, in the direction the frame's own byte picks
        regs = obj["meta"]["shadow"]["registers"]
        assert [r for r, _ in regs] == list(range(25)) + list(range(24, -1, -1))
        assert all(len(w) == 2 for _, w in regs)


@pytest.mark.parametrize("name", sorted(CLAIMS))
def test_the_event_is_the_canonical_one(name):
    """One field says a row sounds; the note column holds a pitch or nothing."""
    obj = built(name)
    for pat in obj["score"]["patterns"].values():
        for e in pat["events"]:
            assert set(e) == {"dur", "sounds", "tie", "gate", "note", "ins", "arm"}
            assert isinstance(e["sounds"], bool) and e["dur"] >= 1
            assert (e["note"] is not None) == e["sounds"]
            assert e["gate"] in (None, "on", "off")
            if e["gate"] is None:  # the row steps a held event spends, and does nothing
                assert not e["sounds"] and e["ins"] is None and e["arm"] is None


@pytest.mark.parametrize("name", sorted(CLAIMS))
def test_no_number_outside_the_tuning_exists_anywhere(name):
    """A pitch table is a pitch table: every note the score plays is a row of it."""
    obj, x = built(name), tune(name)
    p = obj["pitch"]
    for pat in obj["score"]["patterns"].values():
        for e in pat["events"]:
            if e["note"] is not None:
                assert p["base"] <= e["note"] < p["base"] + len(p["freq"])
    assert p["base"] == 0 and len(p["freq"]) <= p["note_count"] == x.L["notes"]
    assert p["freq"] == [
        TJ.word(x.m, x.L["freq"] + 2 * n) for n in range(p["base"], p["base"] + len(p["freq"]))
    ]


@pytest.mark.parametrize("name", sorted(CLAIMS))
def test_no_command_is_named_by_the_index_its_player_dispatched_on(name):
    """A command is named by what it does: a slide's step, a vibrato's speed and shift."""
    obj = built(name)
    for k, cmd in obj["score"]["commands"].items():
        what, _, param = k.partition(":")
        assert what in ("slide.up", "slide.down", "vibrato", "volume") and param
        assert list(cmd) == ["rows"]
    assert obj["meta"]["row_command"] == "spent"


def test_the_comparison_chunks_and_resumes_where_it_left_off(tmp_path):
    """The certificate is the whole horizon however many invocations reach it."""
    loop, ticks, cycles, _, _ = claim(GULDKORN)
    obj, sid = built(GULDKORN), str(tune_file(GULDKORN))
    state = str(tmp_path / "resume.pkl")
    doc, done = TJ.certify(sid, 0, obj, ticks, cycles, budget=0.05, state=state)
    assert not done and 0 < doc["ticks"] < ticks
    while not done:
        doc, done = TJ.certify(sid, 0, obj, ticks, cycles, budget=0.5, state=state)
    assert doc["ticks"] == ticks and doc["divergence"] is None and doc["diverged"] == 0


def test_the_print_carries_the_forms_and_measures_itself():
    text = printer.render(built(GULDKORN))
    for line in (
        "tick       19656 cycles; tempo rowclock -1, row at rowclock == 0, early where rowclock == 2",
        "sequencer  the row consumes the voice's tick when <keys> != 0",
        "tick       fetch ; prelude ; row ; machine",
    ):
        assert line in text, line
    n = printer.numbers(text)
    assert n["blocks"] == 7 and n["data_rows"] == n["statements"] > 0
    source = (
        Path(__file__).resolve().parent.parent.parent
        / "out"
        / "recert-main"
        / "jch-guldkorn-intro"
        / "tuneprog.md"
    )
    if source.is_file():  # the score compresses better than the program that played it
        assert n["xz"] < printer.numbers(source.read_text())["xz"]


def _sets(rows):
    return {t: v for r in rows for t, v in r.get("sets", ())}


def _cmd_bytes(cmd):
    """The two bytes of one command's own record, back out of what its rows do."""
    s = _sets(cmd["rows"])
    if "@sdir" in s:
        return s["@sdir"] | s["@sstep"] >> 8, s["@sstep"] & 0xFF
    if "@vinc" in s:
        return 0x60 | s["@vinc"], s["@vreload"] << 4 | s["@vshift"]
    return 0xF0, s["#vol_or"]


def _wave_note(row):
    """A decoded wave row's own note byte, back out of its columns.

    The three kinds are lossless: a trap row is the `$7E` the tune never takes, a
    relative row is its index and an absolute one that index with bit 7 set.  A
    jump row is the relative one whose index is `$7F`, and its target is `next`.
    """
    if "trap" in row:
        return 0x7E
    return row["pitch"] if row["relative"] else row["pitch"] | 0x80


def _rest(ev, i):
    """The row steps the event at ``i`` holds: the empty event that follows it, or none."""
    return ev[i + 1]["dur"] if i + 1 < len(ev) and ev[i + 1]["gate"] is None else 0


def _pattern_bytes(x, obj, at, ev):  # noqa: C901 - one clause per token of the column
    """One pattern's bytes: its shape off the tune, and every value out of the object."""
    out, y, i, c = [], 0, 0, 0
    while True:
        b = x.m[at + y]
        y += 1
        if b == 0x7F:
            assert i == len(ev), "the pattern's own rows and the object's differ"
            return out + [0x7F]
        if b >= 0x80:
            if b & 0xE0 == 0x80:
                out.append(0x80 | (0x10 if ev[i]["tie"] else 0) | _rest(ev, i))
            elif b & 0xE0 == 0xA0:
                out.append(0xA0 | ev[i]["ins"])
            else:  # the command's own index is the shape; its two bytes are the object's
                k = b & 0x3F
                assert _cmd_bytes(obj["score"]["commands"][ev[i]["arm"][c]]) == (
                    x.m[x.L["cmdtab"] + 2 * k],
                    x.m[x.L["cmdtab_b"] + 2 * k],
                )
                c += 1
                out.append(0xC0 | k)
            continue
        e = ev[i]
        out.append(e["note"] if e["sounds"] else (0x7E if e["gate"] == "on" else 0))
        assert len(e["arm"] or ()) == c, "the row's commands and the object's differ"
        i += 1 + (1 if _rest(ev, i) else 0)
        c = 0


def _program_bytes(x, obj, base, name):
    """A four-column program's records, back out of the stream's act and wait rows."""
    rows, act = x.prog[base]
    out = {}
    for k, r in act.items():
        s = _sets(rows[r : r + 1])
        nxt = rows[r]["next"]
        wait = rows[nxt] if nxt not in act.values() and "hold" in rows[nxt] else None
        after = wait["next"] if wait else nxt
        link = next(j for j, a in act.items() if a == after)
        if base == x.L["pcol0"]:
            init = 0xFF if "@pw" not in s else (s["@pw"] & 0xF0) | s["@pw"] >> 8
            out[k] = [init, s["@pwstep"], s["@pwdir"] | (wait["hold"] if wait else 0), link]
        else:
            init = 0xFF if "#cutoff" not in s else s["#cutoff"]
            out[k] = [init, s["#fstep"], (wait["hold"] if wait else 0), link]
        assert obj["streams"][name]["rows"][r] is rows[r]
    return out


@pytest.mark.parametrize("name", sorted(CLAIMS))
def test_every_byte_of_the_tune_s_data_is_in_the_object(name):  # noqa: C901 - one per table
    """Every byte of the tune's own data, out of the object: the shape is the tune's."""
    x = tune(name)
    obj = x.built
    m, L = x.m, x.L
    for i, ins in obj["instruments"].items():
        col = ins["adsr"] + [ins[k] for k in ("flags", "vol", "filter", "pulse", "wave", "wave")]
        assert col == [m[L["ins0"] + 8 * int(i) + k] for k in range(8)], "instrument %s" % i
    for base, stream in ((L["pcol0"], "pulse"), (L["fcol0"], "filter")):
        for k, rec in _program_bytes(x, obj, base, stream).items():
            assert rec == list(m[base + k : base + k + 4]), "%s record %d" % (stream, k)
    rows = obj["streams"]["wavetab"]["rows"]
    assert [_wave_note(r) for r in rows] == list(m[L["wnote"] : L["wnote"] + L["wave_rows"]])
    ctrl = list(m[L["wctrl"] : L["wctrl"] + L["wave_rows"]])  # a trap row carries no columns
    assert [r.get("ctrl", c) for r, c in zip(rows, ctrl)] == ctrl
    assert obj["pitch"]["freq"] == [
        TJ.word(m, L["freq"] + 2 * n) for n in range(len(obj["pitch"]["freq"]))
    ]
    for v, o in enumerate(obj["score"]["orders"]):
        at = m[L["optr"] + v] | m[L["optr_hi"] + v] << 8
        for step in o["play"]:
            b = m[at]
            if b >= 0x80:  # the order's own transpose column, where the step carries one
                assert b & 0x7F == step["transpose"], "order %d transpose" % v
                at += 1
                b = m[at]
            ev = obj["score"]["patterns"][str(step["pattern"])]["events"]
            assert _pattern_bytes(x, obj, x.pattern_at(b), ev), "order %d pattern %d" % (v, b)
            at += 1
        assert o["end"] == "horizon" or m[at] == 0xFF, "order %d ends" % v
    if L["wrapper"]:  # the wrapper's own four-byte record, one a frame
        at = TJ.word(m, L["dptr"])
        for k, r in enumerate(obj["streams"]["wrapdata"]["rows"][1:]):
            got = [r["pw0_hi"] << 4 | r["pw0_lo"] >> 4, r["pw1_hi"] << 4 | r["pw1_lo"] >> 4]
            assert got + [r["cut"], r["delay"]] == list(m[at + 4 * k : at + 4 * k + 4])
