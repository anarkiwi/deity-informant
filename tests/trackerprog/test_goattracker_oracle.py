"""GoatTracker 2 as a hand-written trackerprog: two builds, one object shape.

``tools/trackerprog_goattracker.py`` states each tune's data in
prototype-trackerprog.md's vocabulary; the claim is section 2's certificate plus
the loop the source tuneprog carries, re-verified here on the render.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tuneprog"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "tools"))

from deity_informant.trackerprog import printer  # noqa: E402
from deity_informant.trackerprog.attest import attest  # noqa: E402

import trackerprog_goattracker as TG  # noqa: E402
from _hvsc import DIA, LINUS, tune_file  # noqa: E402

pytestmark = pytest.mark.hvsc

# tune -> horizon, loop period, first repeat, (instruments, patterns, events)
CLAIMS = {
    LINUS: (8236, 6720, 8235, (30, 33, 2289)),
    DIA: (8659, 8640, 8658, (20, 25, 1315)),
}
NIBBLE = {n: i for i, n in enumerate(TG.COMMANDS)}
FX = {
    "porta_up": 1,
    "porta_down": 2,
    "toneporta": 3,
    "toneporta_snap": 3,
    "vib_phase": 4,
    "vib_delay": 0,
}
STREAMS = ["exit", "filter", "funktempo", "hard_restart", "note_on", "pulse", "speed", "wave"]


def built(tune):
    return TG.build(str(tune_file(tune)))


@pytest.mark.parametrize("tune", sorted(CLAIMS))
def test_each_build_certifies_on_the_universal_player(tune):
    ticks, period, repeat, shape = CLAIMS[tune]
    obj = built(tune)
    doc = attest(obj, TG.reference(str(tune_file(tune)), 0, ticks))
    assert doc["divergence"] is None
    assert doc["ticks"] == ticks
    assert doc["identical_ticks"] == ticks  # a shadow flush leaves no interleave to drop
    assert doc["same_per_register_order"]
    assert TG.loop_holds(obj, {"period": period, "first_repeat": repeat})
    assert (
        len(obj["instruments"]),
        len(obj["score"]["patterns"]),
        sum(len(p["events"]) for p in obj["score"]["patterns"].values()),
    ) == shape


@pytest.mark.parametrize("tune", sorted(CLAIMS))
def test_no_number_outside_the_tuning_exists_anywhere(tune):
    """A pitch table is a pitch table: every note the score plays is a row of it."""
    obj = built(tune)
    top = obj["pitch"]["base"] + len(obj["pitch"]["freq"])
    for step, pat in _plays(obj):
        for e in pat["events"]:
            if e["note"] is not None:
                assert obj["pitch"]["base"] <= e["note"] + step["transpose"] < top


@pytest.mark.parametrize("tune", sorted(CLAIMS))
def test_the_two_builds_differ_only_in_their_data(tune):
    """No address, no register offset and no family branch survives in the object."""
    obj = built(tune)
    assert obj["meta"]["commit_order"] == ["sr", "ad", "ctrl"]
    assert obj["meta"]["shadow"] == {"registers": list(range(24, -1, -1))}
    assert set(obj["accs"]) == set(TG.accs())
    assert sorted(obj["streams"]) == STREAMS


def test_the_speed_table_carries_no_value_for_the_row_that_is_not_one():
    """Index 0 is the 1-based table's null; asking it for a step is an error."""
    row = built(LINUS)["streams"]["speed"]["rows"][0]
    assert row["zero"] == 1 and "trap" in row["delta"] and "trap" in row["depth"]


def test_the_flattened_print_carries_the_streams_and_measures_itself():
    text = printer.render(built(LINUS))
    for line in (
        "shadow     25 registers, flushed descending at the head of every tick",
        "tempo rowclock -1, row at rowclock == 0, early where rowclock == 2",
        "tick       row ; commit ; machine ; fetch ; prelude ; stream exit",
        "a new pitch resets vib_phase",
    ):
        assert line in text
    n = printer.numbers(text)
    assert n["blocks"] == 7 and n["data_rows"] == n["statements"] > 0


def _plays(obj):
    for order in obj["score"]["orders"]:
        for step in order["play"]:
            yield step, obj["score"]["patterns"][str(step["pattern"])]


def _semitone_byte(n):
    return (n if n >= 0 else n + 0x80) | 0x80


def _wave_bytes(row):
    """The wavetable row's own two bytes, back from the step.

    A row that produces leaves the flag its arms stand down on, which is the
    player's precedence said in the object and no byte of the table.
    """
    if "jump" in row:
        return 0xFF, row["jump"] or 0  # a jump to no row is the byte that names none
    op, left, right = row.get("op"), 0, 0
    sets = [x for x in row.get("sets", ()) if x[0] != "!produced"]
    if "hold" in row:
        left = row["hold"] - 1
    elif sets:
        left = (sets[0][1] + 0x10) & 0xFF
    if op is None:
        return left, right
    assert row["sets"][-1] == ["!produced", 1]  # every producing row leaves it
    if "pitch" in op:
        n = op["pitch"]
        return left, _semitone_byte(n) if op.get("relative") else n
    if "cmd" in op:
        name, _, param = op["cmd"].partition(":")
        return (left if sets else 0xF0 | NIBBLE[name]), int(param, 16)
    cmd = FX[op["acc"]]
    return (left if sets else 0xF0 | cmd), op.get("row", 0)


def _pulse_bytes(row):
    if "jump" in row:
        return 0xFF, row["jump"] or 0
    if "sets" in row:
        return row["sets"][0][1], row["sets"][1][1]
    return row["hold"], row["run"][0]["delta"] & 0xFF


def _speed_bytes(row):
    if isinstance(row["delta"], dict):
        return 0x80 | row["cmp"], row["delta"]["shr"][1]
    return row["cmp"], row["depth"]


def _filt_bytes(rows, n):
    """The filter table row by row; a mode row gives back the cutoff row it took."""
    out = [None] * (n + 2)
    for i in range(1, n + 1):
        r = rows[i]
        if "trap" in r:
            continue
        if "jump" in r:
            out[i] = (0xFF, r["jump"] or 0)
        elif "run" in r:
            out[i] = (r["hold"], r["run"][0]["delta"])
        else:
            s = dict(map(tuple, r["sets"]))
            if set(s) == {"#cutoff"}:
                out[i] = (0x00, s["#cutoff"])
            else:
                out[i] = ((s["#filttype"] >> 1) | 0x80, s["#filtctrl"])
                if "#cutoff" in s:
                    out[i + 1] = (0x00, s["#cutoff"])
    return out


def _param(c):
    for f in ("sets", "point", "all"):
        for _, v in c.get(f, ()):
            return v
    return 0


def _pattern_bytes(pat, notecode):
    """The pattern's own byte stream, back from the events."""
    out = []
    for e in pat["events"]:
        if e["ins"] is not None:
            out.append(e["ins"])
        if e["arm"] is not None:
            fx = NIBBLE[e["arm"].split(":")[0]]
            bare = not e["sounds"] and e["gate"] is None and e["dur"] == 1
            out.append((0x50 if bare else 0x40) | fx)
            if fx:
                out.append(int(e["arm"].split(":")[1], 16))
            if bare:
                continue
        if e["dur"] > 1:
            out.append(0x100 - e["dur"])
        elif e["gate"] is not None:
            out.append(0xBE if e["gate"] == "off" else 0xBF)
        elif e["sounds"]:
            out.append(e["note"] + notecode)
        else:
            out.append(0xBD)
    return out + [0]


@pytest.mark.parametrize("tune", sorted(CLAIMS))
def test_every_byte_of_the_tune_s_data_is_in_the_object(tune):
    """The object is the tune's own tables, materialised: reconstruct and diff.

    Nothing here is invented and nothing is lost -- the wave, pulse, filter and
    speed tables, the nine instrument columns and the patterns come back byte
    for byte, and the orderlist comes back as the steps it decodes to.
    """
    x = TG.Tune(str(tune_file(tune)))
    obj, m, lay = x.build(), x.m, x.L
    for name, fn in (("wave", _wave_bytes), ("pulse", _pulse_bytes), ("speed", _speed_bytes)):
        rows = obj["streams"][name]["rows"]
        n = lay[name + "rows"]
        assert [fn(rows[i]) for i in range(1, n + 1)] == [
            (x.t(name, i), x.t(name, i, True)) for i in range(1, n + 1)
        ], name
    got = _filt_bytes(obj["streams"]["filter"]["rows"], lay["filtrows"])
    for i in range(1, lay["filtrows"] + 1):
        if got[i] is not None:
            assert got[i] == (x.t("filt", i), x.t("filt", i, True)), "filter row %d" % i
    for k, ins in obj["instruments"].items():
        i = int(k)
        assert ins["adsr"] == [x.col("ad", i), x.col("sr", i)]
        assert (ins["wave"], ins["vibparam"], ins["vibdelay"]) == (
            x.col("firstwave", i),
            x.col("vibparam", i),
            x.col("vibdelay", i),
        )
    for k, pat in obj["score"]["patterns"].items():
        base = m[lay["patttbl"] + int(k)] | m[lay["patttbl_hi"] + int(k)] << 8
        b = _pattern_bytes(pat, lay["notecode"])
        assert b == list(m[base : base + len(b)]), "pattern %s" % k
    for v, order in enumerate(obj["score"]["orders"]):
        s = m[lay["songnum"] + 7 * v]
        p = m[lay["songtbl"] + s] | m[lay["songtbl_hi"] + s] << 8
        want, trans, y = [], 0, 0
        while m[p + y] != 0xFF:
            if m[p + y] >= 0xE0:
                trans = m[p + y] - 0xF0
                y += 1
            want.append((m[p + y], trans))
            y += 1
        assert [(t["pattern"], t["transpose"]) for t in order["play"]] == want, "order %d" % v


def _canonical(events):
    """Section 3.6's event, as the layer states it after the note column is spent."""
    for e in events:
        assert set(e) == {"dur", "sounds", "tie", "gate", "note", "ins", "arm"}
        assert isinstance(e["sounds"], bool)
        if e["note"] is not None:  # a pitch is a pitch: a row with one sounds
            assert e["sounds"]
        if e["gate"] is not None:  # a gate statement is its own row, never a note's
            assert not e["sounds"] and e["gate"] in ("on", "off")


@pytest.mark.parametrize("tune", sorted(CLAIMS))
def test_the_event_is_the_canonical_one(tune):
    """One field says a row sounds; the note column holds a pitch or nothing."""
    obj = built(tune)
    _canonical([e for p in obj["score"]["patterns"].values() for e in p["events"]])


@pytest.mark.parametrize("tune", sorted(CLAIMS))
def test_no_command_is_named_by_the_index_its_player_dispatched_on(tune):
    """A command is named by what it does; the jump table's nibble is spent."""
    obj = built(tune)
    # `init` is the tune's own init call, which the entry state names (§3.6)
    for name, cmd in obj["score"]["commands"].items():
        head = name.split(":")[0]
        assert head in TG.COMMANDS or head == obj["state0"]["prologue"], name
        assert "id" not in cmd
    assert obj["meta"]["row_command"] == "held"  # the holding is a datum, not the clock
