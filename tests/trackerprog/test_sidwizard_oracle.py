"""SID Wizard as a hand-written trackerprog: two builds of one player.

``tools/trackerprog_sidwizard.py`` states each tune's data in
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

import trackerprog_sidwizard as TS  # noqa: E402
from _hvsc import EMOMYST, EOTW, tune_file  # noqa: E402

pytestmark = pytest.mark.hvsc

# tune -> horizon, loop period, first repeat, (instruments, patterns, events)
CLAIMS = {
    EMOMYST: (8084, 6120, 8083, (11, 21, 843)),
    EOTW: (14465, 7688, 14464, (21, 31, 1314)),
}
STREAMS = [
    "chords",
    "chordstart",
    "exit",
    "exp",
    "filter",
    "gate_row",
    "hard_restart",
    "pitch_out",
    "pitch_row",
    "pulse",
    "pw_out",
    "tempo",
    "voice_bit",
    "wave",
]
ORDER = {EMOMYST: ["ad", "sr", "ctrl"], EOTW: ["sr", "ad", "ctrl"]}
NOTEFX = {"porta.note": 0x78, "sync.on": 0x79, "sync.off": 0x7A, "ring.on": 0x7B, "ring.off": 0x7C}


def built(tune):
    return TS.build(str(tune_file(tune)))


@pytest.mark.parametrize("tune", sorted(CLAIMS))
def test_each_build_certifies_on_the_universal_player(tune):
    ticks, period, repeat, shape = CLAIMS[tune]
    obj = built(tune)
    doc = attest(obj, TS.reference(str(tune_file(tune)), 0, ticks))
    assert doc["divergence"] is None
    assert doc["ticks"] == ticks
    assert doc["identical_ticks"] == ticks  # not a permutation: the same list
    assert doc["same_per_register_order"]
    assert TS.loop_holds(obj, {"period": period, "first_repeat": repeat})
    assert (
        len(obj["instruments"]),
        len(obj["score"]["patterns"]),
        sum(len(p["events"]) for p in obj["score"]["patterns"].values()),
    ) == shape


@pytest.mark.parametrize("tune", sorted(CLAIMS))
def test_the_two_builds_differ_in_one_datum_and_their_data(tune):
    """The version difference is ``commit_order``; the rest is the same object."""
    obj = built(tune)
    assert obj["meta"]["commit_order"] == ORDER[tune]
    assert "shadow" not in obj["meta"]  # nothing is deferred, so nothing is flushed
    assert sorted(obj["streams"]) == STREAMS
    assert set(obj["accs"]) == set(TS.accs())


@pytest.mark.parametrize("tune", sorted(CLAIMS))
def test_no_number_outside_the_tuning_exists_anywhere(tune):
    """A pitch table is a pitch table: every note the score plays is a row of it."""
    obj = built(tune)
    top = obj["pitch"]["base"] + len(obj["pitch"]["freq"])
    for step, pat in _plays(obj):
        for e in pat["events"]:
            if e["note"] is not None:
                n = e["note"] + step["transpose"] + _shift(obj, e, pat)
                assert obj["pitch"]["base"] <= n < top


def _shift(obj, e, pat):
    """The instrument's own transposition, whichever instrument the row plays."""
    if e["ins"] is not None:
        return obj["instruments"][str(e["ins"])]["transpose"]
    return max(
        (obj["instruments"][str(x["ins"])]["transpose"] for x in pat["events"] if x["ins"]),
        default=0,
    )


@pytest.mark.parametrize("tune", sorted(CLAIMS))
def test_no_command_is_named_by_the_index_its_player_dispatched_on(tune):
    """A command is named by what it does; the three jump tables are spent."""
    obj = built(tune)
    known = set(TS.NOTEFX) | set(TS.SMALLFX) | set(TS.BIGFX) | {"legato", "nop"}
    known |= {obj["state0"].get("prologue")}  # the init call, named by the entry state
    for name, cmd in obj["score"]["commands"].items():
        assert name.split(":")[0] in known, name
        assert "id" not in cmd
    assert obj["meta"]["row_command"] == "spent"


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


def test_the_print_carries_the_forms_and_measures_itself():
    text = printer.render(built(EMOMYST))
    for line in (
        "tempo spdcnt +1, row at phase == 2, fetched where phase == 0, early where phase < 2",
        "tick       fetch ; prelude ; commit ; row ; commit ; machine ; stream exit",
        "prologue   init",  # the init call the entry state names, and its empty command
        "    init   --",
    ):
        assert line in text, line
    n = printer.numbers(text)
    assert n["blocks"] == 7 and n["data_rows"] == n["statements"] > 0


def _plays(obj):
    for order in obj["score"]["orders"]:
        for step in order["play"]:
            yield step, obj["score"]["patterns"][str(step["pattern"])]


def _sets(row):
    return dict((t, v) for t, v, *_ in row.get("sets", ()))


def _wave_bytes(row, rows=()):
    """One waveform row's own three bytes, back from the step."""
    s = _sets(row)
    if "@wave" not in s and "@arpscnt" not in s:
        return (0xFE, None, row["detune"]) if "detune" in row else (0xFF,)
    b0 = s["@arpscnt"] if "@arpscnt" in s else s["@wave"]["and"][0]
    op = row.get("op")
    if "@chordval" in s:
        b1 = 0x7F
    elif op is None:
        b1 = 0x80
    elif op.get("relative"):
        b1 = op["pitch"] & 0xFF
    else:
        b1 = op["pitch"] | 0x80
    return b0, b1, row["detune"]


def _acting(row, rows):
    """A sweep row's own bytes: its delay, and the sets its landing row carries.

    A sweep of `n + 1` ticks that runs on `n` of them is two rows -- the acting
    one and the one its landing holds (section 3.3) -- so the delay is the first
    row's `hold` and the third byte is on the row it goes to.  A sweep of one
    tick runs on none and stays one row, delay 0.
    """
    s = _sets(row)
    if s:
        return row.get("hold", 1) - 1, s
    return row["hold"], _sets(rows[row["next"]])


def _pulse_bytes(row, rows):
    s = _sets(row)
    if "track" in row:
        return 0xFE, None, row["track"]
    if "run" in row:
        delay, s = _acting(row, rows)
        return delay, row["run"][0]["delta"] & 0xFF, s["@pkbdtrk"]
    if "@pw" not in s:
        return (0xFF,)
    return 0x80 | s["@pw"] >> 8, s["@pw"] & 0xFF, s["@pkbdtrk"]


def _third(s):
    return 0x80 | s["#fswitch"] if "#fswitch" in s else s.get("#ckbdtrk")


def _filter_bytes(row, rows):
    s = _sets(row)
    if "track" in row:
        return 0xFE, None, row["track"]
    if "run" in row:
        delay, s = _acting(row, rows)
        return delay, row["run"][0]["delta"] & 0xFF, _third(s)
    if "#fltband" not in s:
        return (0xFF,)
    return 0x80 | s["#fltband"] | s["#resonib"] >> 4, s["#cutoff"] >> 3, _third(s)


def _tables(x, obj, i):
    """Every byte of one instrument's three tables, back from the object's rows."""
    out = {}
    for slot, fn in (("wave", _wave_bytes), ("pulse", _pulse_bytes), ("filter", _filter_bytes)):
        rows = obj["streams"][slot]["rows"]
        for k, r in x.base[i][slot].items():
            for j, b in enumerate(fn(rows[r], rows)):
                if b is not None:
                    out[k + j] = b
    return out


def _header(ins):
    """The header bytes the object states, by position in the tune's own record.

    Nine are the record's own columns; three -- the vibrato delay, the arpeggio
    speed and the chord -- are the constants the note-on bakes, and are no column
    of the record because nothing reads them back (section 3.5).  Positions 10
    and 11 are the two table indices, spent into the rows they select, and 12-14
    the gate-off pointer, which the note-on refuses where it is not zero.
    """
    sets = {t: v for r in ins["on_note"] for t, v in r.get("sets", ())}
    cols = [ins["ctrl"]] + ins["hr"] + ins["adsr"] + [ins["vib"]]
    baked = [sets["@videlcnt"], sets["@arpsped"], sets["@curchord"], ins["transpose"] & 0xFF]
    return dict(enumerate(cols + baked)) | {15: ins["wave"]}


@pytest.mark.parametrize("tune", sorted(CLAIMS))
def test_every_byte_of_the_tune_s_data_is_in_the_object(tune):
    """The object is the tune's own tables, materialised: reconstruct and diff."""
    x = TS.Tune(str(tune_file(tune)))
    obj, m, L = x.build(), x.m, x.L
    assert obj["pitch"]["freq"] == [
        m[L["freqtbl"] + n] | m[L["freqtbh"] + n] << 8 for n in range(96)
    ]
    assert [r["value"] for r in obj["streams"]["exp"]["rows"]] == list(
        m[L["exptabh"] : L["exptabh"] + 107]
    )
    assert [r["value"] for r in obj["streams"]["chordstart"]["rows"]] == list(
        m[L["chdptrlo"] : L["insptlo"]]
    )
    assert [
        0x7F if isinstance(r["raw"], dict) else r["raw"] for r in obj["streams"]["chords"]["rows"]
    ] == list(m[L["chords"] : L["tempotbl"]])
    assert [obj["state0"]["globals"]["tempo%d" % i] for i in range(8)] == list(
        m[L["tempotbl"] : L["tempotbl"] + 8]
    )
    for k, ins in obj["instruments"].items():
        i, at = int(k), x.ins_at(int(k))
        want = list(m[at : at + 16])
        assert {j: want[j] for j in _header(ins)} == _header(ins), "instrument %s header" % k
        assert want[12:15] == [0, 0, 0], "instrument %s gate-off pointer" % k
        for off, b in _tables(x, obj, i).items():
            assert b == m[at + off], "instrument %s byte %02X" % (k, off)
    for k, pat in obj["score"]["patterns"].items():
        base = (m[L["pptrlo"] + int(k)] | m[L["pptrhi"] + int(k)] << 8) + (
            m[L["swp"]] | m[L["swp"] + 1] << 8
        )
        want = list(m[base : base + 4 * len(pat["events"]) + 1])
        b = _pattern_bytes(pat, want)
        assert b == want[: len(b)], "pattern %s" % k
    for v, order in enumerate(obj["score"]["orders"]):
        assert [s["pattern"] for s in order["play"]] == _order_patterns(m, L["orderlist"][v])


def _order_patterns(m, base):
    out, y = [], 0
    while m[base + y] != 0xFF:
        if m[base + y] < 0x80:
            out.append(m[base + y])
        y += 1
    return out


def _pattern_bytes(pat, want):  # noqa: C901 - one clause per column of the row
    """One pattern's own byte stream, back from the events.

    The row's *shape* -- how many of its four columns it has -- is the bit-7
    continuation the layer spends, so the reconstruction reads that off the tune
    and every value out of the object.  It has to: three of SID Wizard's effects
    have the same encoding in two columns, and a score that names a command by
    what it does says the same thing for both.
    """
    out = []
    for e in pat["events"]:
        arm = list(e["arm"] or ())
        cmds = [c for c in arm if c != "legato"]
        i = len(out)
        cols = 1 + bool(want[i] & 0x80)
        if cols == 2 and want[i + 1] & 0x80:
            cols = 3 + (want[i + 2] & 0xE0 == 0)
        legato = "legato" in arm or not any(c.startswith("portamento") for c in cmds)
        ins = 0x3F if e["tie"] and cols > 1 and legato else (e["ins"] or 0)
        slots = (cols >= 3) + (cols >= 2 and not ins)
        note = 0
        if e["dur"] > 1:
            note = e["dur"] + 0x6E
        elif e["gate"] is not None:
            note = 0x7D if e["gate"] == "on" else 0x7E
        elif e["sounds"]:
            note = e["note"]
        elif len(cmds) > slots:
            head = cmds.pop(0)
            note = NOTEFX.get(head, 0x60 | _val(head) if ":" in head else 0)
        if not ins and cols >= 2 and len(cmds) > (cols >= 3):
            head = cmds.pop(0)
            ins = _byte(head)
        out.append(note | (0x80 if cols > 1 else 0))
        if cols > 1:
            out.append(ins | (0x80 if cols > 2 else 0))
        if cols > 2:  # a four-column row is the big effect table's, not the small one's
            fx = cmds.pop(0)
            if cols == 4:
                out += [TS.BIGFX.index(fx.split(":")[0]) + 1, _val(fx)]
            else:
                out.append(_byte(fx))
        assert not cmds, "row commands left over: %s" % cmds
    return out + [0xFF]


def _byte(name):
    """One small effect's own byte; a build that compiles one out keeps the byte."""
    head = name.split(":")[0]
    return _val(name) if head == "nop" else _small(name) << 4 | _val(name)


def _small(name):
    head = name.split(":")[0]
    return TS.SMALLFX.index(head) + 2 if head in TS.SMALLFX else None


def _val(name):
    return int(name.split(":")[1], 16)
