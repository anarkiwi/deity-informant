"""T2: cursors, streams, the pitch table and the materialised score."""

import json
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tuneprog"))

from deity_informant.trackerprog import cursors, lift, score  # noqa: E402
from deity_informant.trackerprog.refuse import Refusal  # noqa: E402
from deity_informant.trackerprog.resolve import Sel, free, walkx  # noqa: E402
from deity_informant.tuneprog import pipeline  # noqa: E402
from deity_informant.tuneprog.history import history  # noqa: E402
from deity_informant.tuneprog.ir import Bin, Const, Load, Rgn, Var  # noqa: E402

from _asm import asm  # noqa: E402
from _prog import PLAY, tuneprog  # noqa: E402

ORDER, PLO, PHI, PAT0, PAT1, FLO, FHI = 0x2000, 0x2010, 0x2020, 0x2100, 0x2140, 0x2200, 0x2240
NOTES = 60
CERT = {"subtunes": [{"complete": True, "period": 40}]}

# one voice: an orderlist of pattern numbers ($FF loops), pattern pointers lo/hi,
# patterns of note bytes ($FF ends one), a 12-TET table split lo|hi
TUNE = asm(
    PLAY,
    "init: LDA #$00",
    "STA row",
    "STA ord",
    "STA hold",
    "LDX #$3B",
    "sum: LDA $2200,X",  # reach the whole pitch table, so its region spans it
    "CLC",
    "ADC $2240,X",
    "STA hold",
    "DEX",
    "BPL sum",
    "LDA #$00",
    "STA hold",
    "RTS",
    "play: DEC hold",
    "BPL done",
    "LDA #$03",
    "STA hold",
    "LDX ord",
    "LDA $2000,X",
    "CMP #$FF",
    "BNE ok",
    "LDX #$00",
    "STX ord",
    "LDA $2000,X",
    "ok: TAY",
    "LDA $2010,Y",
    "STA $FB",
    "LDA $2020,Y",
    "STA $FC",
    "LDY row",
    "LDA ($FB),Y",
    "CMP #$FF",
    "BNE note",
    "LDA #$00",
    "STA row",
    "STA hold",
    "INC ord",
    "RTS",
    "note: TAX",
    "LDA $2200,X",
    "STA $D400",
    "LDA $2240,X",
    "STA $D401",
    "LDA #$41",
    "STA $D404",
    "INC row",
    "done: RTS",
    "row: BRK",
    "ord: BRK",
    "hold: BRK",
)


def freq_table(n=NOTES, base=0x0200):
    """A 12-TET table of ``n`` u16 entries, as ``(lo bytes, hi bytes)``."""
    vals = [int(round(base * 2 ** (i / 12))) for i in range(n)]
    return bytes(v & 0xFF for v in vals), bytes(v >> 8 for v in vals), vals


def blocks():
    lo, hi, _vals = freq_table()
    pats = {PAT0: bytes([12, 14, 16, 0xFF]), PAT1: bytes([24, 12, 0xFF])}
    return {
        ORDER: bytes([0, 1, 0xFF]),
        PLO: bytes([PAT0 & 0xFF, PAT1 & 0xFF]),
        PHI: bytes([PAT0 >> 8, PAT1 >> 8]),
        **pats,
        FLO: lo,
        FHI: hi,
    }


def t2(code=TUNE, calls=64, cert=CERT, **kw):
    trace, prog = tuneprog(code, calls=calls, s4=True, blocks=blocks(), **kw)
    view, _st, names = pipeline.present(prog)
    hist, ver = history(prog, trace, names.to_dict(), calls=calls)
    assert ver.div is None
    return lift.document(view, names, hist, cert)


@pytest.fixture(scope="module")
def doc():
    return t2()


def test_the_pitch_table_is_materialised_as_the_values_read(doc):
    _lo, _hi, vals = freq_table()
    assert doc["pitch"]["layout"] == "lo|hi" and doc["pitch"]["entries"] == vals
    assert doc["pitch"]["accessors"] and {a["shift"] for a in doc["pitch"]["accessors"]} == {0}


def test_the_score_is_an_order_of_patterns_through_a_pointer_table(doc):
    assert doc["refusals"] == []
    (voice,) = doc["score"]
    (order,) = voice["order"]
    (pattern,) = voice["pattern"]
    assert order["depth"] == 0 and pattern["depth"] == 1
    assert pattern["pointers"]["entries"] == 2 and pattern["terminator"] == 0xFF
    assert order["terminator"] == 0xFF
    bases = [e["base"] for e in pattern["events"]]
    assert sorted(set(bases)) == [PAT0, PAT1]
    rows = [tuple(e["bytes"]) for e in pattern["events"] if e["bytes"]]
    assert rows[:5] == [(12,), (14,), (16,), (24,), (12,)]
    assert {e["ticks"] for e in pattern["events"][:-1] if e["bytes"]} == {
        4
    }  # the horizon cuts the last
    assert sum(e["ticks"] for e in pattern["events"]) == doc["horizon"]["ticks"]


def test_the_order_loops_at_its_terminator(doc):
    (order,) = doc["score"][0]["order"]
    first = [e["bytes"] for e in order["events"] if e["bytes"]][:4]
    assert first == [[0], [1], [0], [1]] or first[:2] == [[0], [1]]


# ---- the shapes, without a tune ------------------------------------------------
def _rgn(rid, kind, base, size):
    return Rgn(rid, kind, base, size, kind)


def test_a_nest_deeper_than_two_pointer_bases_is_not_cursor_shaped():
    rgn = {
        1: _rgn(1, "const", 0x100, 16),
        2: _rgn(2, "const", 0x200, 16),
        3: _rgn(3, "state", 0x10, 1),
    }
    cur = Load("ram", Const(0x10, 2), 1, 0x10, 0x10, 3)

    def ptrtab(sel):
        lo = Load("ram", Bin("+", Const(0x100, 2), sel, 2), 1, 0x100, 0x10F, 1)
        hi = Load("ram", Bin("+", Const(0x200, 2), sel, 2), 1, 0x200, 0x20F, 2)
        return Bin("|", lo, Bin("<<", hi, Const(8, 1), 2), 2)

    one = ptrtab(cur)
    assert score.depth(one, rgn) == 1
    two = ptrtab(Load("ram", Bin("+", one, cur, 2), 1, 0, 0xFFFF, 1))
    assert score.depth(two, rgn) == 2
    three = ptrtab(Load("ram", Bin("+", two, cur, 2), 1, 0, 0xFFFF, 1))
    assert score.depth(three, rgn) == 3
    assert score.depth(Bin("+", cur, cur, 2), rgn) is None


def test_decompose_reads_origin_cursor_shift_and_base():
    rgn = {3: _rgn(3, "state", 0x10, 1)}
    cur = Load("ram", Const(0x10, 2), 1, 0x10, 0x10, 3)
    base, origin, cursor, shift = cursors.decompose(
        Bin("+", Bin("+", Const(0x1000, 2), Bin("<<", cur, Const(3, 1), 1), 2), Const(1, 1), 2), rgn
    )
    assert (base, origin, shift) == (None, 0x1001, 3) and cursor.region == 3
    assert cursors.basekind(None, rgn) == "const"


def test_successors_split_steps_from_jumps_and_count_holds():
    got = cursors.successors(np.array([0, 0, 1, 1, 1, 2, 0, 0, 1]))
    assert got.step == 1 and dict(got.jumps) == {(2, 0): 1}
    assert got.holds == [2, 3, 1, 2, 1] and got.visited == [0, 1, 2]


def test_free_names_skip_a_selection_s_guards_when_asked():
    e = Sel((((), Var("a")), (((Var("g"), True, frozenset()),), Var("b"))))
    assert free(e) == {"a", "b", "g"} and free(e, False) == {"a", "b"}
    assert {x.n for x in walkx(e, False) if type(x) is Var} == {"a", "b"}


def test_a_refusal_names_a_known_reason():
    assert Refusal("score not cursor-shaped", "c").to_dict()["cell"] == "c"
    with pytest.raises(ValueError):
        Refusal("nope", "c")


def test_the_document_round_trips_through_json(doc):
    again = json.loads(json.dumps(doc))
    assert again["horizon"]["ticks"] == 64 and again["stats"]["accesses"] > 0
