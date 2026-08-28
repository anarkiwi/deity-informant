"""T3: the universal player renders a tune lifted from its data, and certifies it."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tuneprog"))

from deity_informant.trackerprog import certify, emit, lift, player, region  # noqa: E402
from deity_informant.trackerprog.refuse import Refusal  # noqa: E402
from deity_informant.tuneprog import pipeline, provenance  # noqa: E402
from deity_informant.tuneprog.history import history  # noqa: E402
from deity_informant.tuneprog.verify import certify as certified  # noqa: E402

from _asm import asm  # noqa: E402
from _prog import PLAY, tuneprog  # noqa: E402
from test_score import CERT, TUNE, blocks, freq_table  # noqa: E402

ORDER, PLO, PHI, PAT0, PAT1, FLO, FHI, AD, SR = (
    0x2000,
    0x2010,
    0x2020,
    0x2100,
    0x2140,
    0x2200,
    0x2240,
    0x2300,
    0x2310,
)
INSTRUMENTS = 3  # the table holds four; the score reaches three, which is the lift's reach

# one voice: an orderlist, pattern pointers, patterns of note bytes where ``$80 | k``
# selects instrument ``k`` of a two-column table, the sound written every tick
# from the note and instrument cells outside the fetch
INS_LINES = [
    "init: LDA #$00",
    "STA row",
    "STA ord",
    "STA hold",
    "STA note",
    "STA ins",
    "LDX #$3B",
    "sum: LDA $2200,X",
    "CLC",
    "ADC $2240,X",
    "STA hold",
    "DEX",
    "BPL sum",
    "LDA #$00",
    "STA hold",
    "RTS",
    "play: DEC hold",
    "BPL sound",
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
    "BNE byte",
    "LDA #$00",
    "STA row",
    "STA hold",
    "INC ord",
    "RTS",
    "byte: CMP #$80",
    "BCC isnote",
    "AND #$7F",
    "STA ins",
    "INC row",
    "LDY row",
    "LDA ($FB),Y",
    "isnote: STA note",
    "INC row",
    "sound: LDX note",
    "LDA $2200,X",
    "STA $D400",
    "LDA $2240,X",
    "STA $D401",
    "LDX ins",
    "LDA $2300,X",
    "STA $D405",
    "LDA $2310,X",
    "STA $D406",
    "LDA #$41",
    "STA $D404",
    "RTS",
    "row: BRK",
    "ord: BRK",
    "hold: BRK",
    "note: BRK",
    "ins: BRK",
]
INS_TUNE = asm(PLAY, *INS_LINES)


def ins_blocks():
    lo, hi, _vals = freq_table()
    return {
        ORDER: bytes([0, 1, 0xFF]),
        PLO: bytes([PAT0 & 0xFF, PAT1 & 0xFF]),
        PHI: bytes([PAT0 >> 8, PAT1 >> 8]),
        PAT0: bytes([0x81, 12, 14, 0x83, 16, 0xFF]),
        PAT1: bytes([0x82, 24, 12, 0xFF]),
        FLO: lo,
        FHI: hi,
        AD: bytes([0x0F, 0x48, 0x22, 0x09]),
        SR: bytes([0xF0, 0xA8, 0x59, 0x00]),
    }


def t3(code=TUNE, calls=64, cert=None, data=None):
    trace, prog = tuneprog(code, calls=calls, s4=True, blocks=data or blocks())
    view, st, names = pipeline.present(prog)
    hist, ver = history(prog, trace, names.to_dict(), calls=calls, obs=True)
    cert = cert or certified(prog, ver)
    t0 = provenance.document(view, st, names)
    t2 = lift.document(view, names, hist, cert)
    tp, refusals, rec = emit.lift(prog, view, names, t0, None, t2, cert, trace.inputs)
    return tp, refusals, rec, ver, prog


def test_the_snippet_lifts_its_fetch_as_data_and_renders_its_observable_exactly():
    tp, refusals, rec, ver, _prog = t3()
    assert refusals == []
    assert certify.divergence(ver.obs, rec) is None
    got, trap = emit.replay(tp)
    assert trap is None and certify.divergence(ver.obs, got) is None
    (voice,) = tp["score"]["voices"]
    assert voice["order"] and voice["patterns"]
    assert all(r["dur"] >= 1 for p in voice["patterns"].values() for r in p)
    # the pattern bytes are the rows the fetch read: notes 12, 14, 16 and 24, 12
    notes = [b for r in voice["rows"] for a, b in r["bytes"] if a >= PAT0 and b != 0xFF]
    assert set(notes) == {12, 14, 16, 24}
    assert tp["score"]["regions"] and tp["score"]["fetches"]


def test_an_instrument_table_is_the_selector_the_envelope_writes_index():
    tp, refusals, _rec, ver, _prog = t3(INS_TUNE, data=ins_blocks())
    assert refusals == []
    ins = tp["instruments"]
    assert ins["entries"] == INSTRUMENTS == ins["used"] and sorted(ins["rows"]) == [1, 2, 3]
    assert [r["ad"] for _i, r in sorted(ins["rows"].items())] == [0x48, 0x22, 0x09]
    regs = {p["register"] for p in tp["producers"]}
    assert {"ad", "sr", "ctrl", "freq_lo", "freq_hi"} <= regs
    (voice,) = tp["score"]["voices"]
    sets = [c for p in voice["patterns"].values() for r in p for c in r["sets"]]
    # the instrument and note cells under S6's names, and the row clock a pattern end resets
    assert {c[0] for c in sets} == {"ad_idx", "freq_lo_idx", "phase"}
    assert {c[1] for c in sets if c[0] == "ad_idx"} == {1, 2, 3}
    got, _trap = emit.replay(tp)
    assert certify.divergence(ver.obs, got) is None


def test_the_certificate_binds_the_source_and_states_both_halves():
    tp, refusals, _rec, ver, _prog = t3()
    got, _trap = emit.replay(tp)
    doc = certify.certificate("snippet", CERT, ver.obs, got, refusals, tp["score"]["end"])
    assert doc["divergence"] is None and doc["emitted"] and doc["ticks"] == 64
    assert doc["compared"] and doc["dropped"] and doc["loop"]["period"] == 40
    assert doc["end"]["kind"] == "loop"
    assert json.loads(json.dumps(doc)) == doc


def test_a_refusal_or_a_divergence_means_no_emit_but_a_stated_render():
    tp, _refusals, _rec, ver, _prog = t3()
    got, _trap = emit.replay(tp)
    bad = [Refusal("command residue", "sid[0].ctrl = x", "$1000")]
    doc = certify.certificate("snippet", CERT, ver.obs, got, bad, tp["score"]["end"])
    assert not doc["emitted"] and doc["divergence"] is None and doc["refusals"][0]["cell"]
    assert doc["rendered"]["ticks_equal"] == 64
    wrong = list(got)
    wrong[5] = got[5]._replace(values=tuple(v if v is None else v + 1 for v in got[5].values))
    doc = certify.certificate("snippet", CERT, ver.obs, wrong, [], tp["score"]["end"])
    assert not doc["emitted"] and doc["divergence"]["tick"] == 5
    assert doc["divergence"]["register"].startswith("value")
    short = certify.divergence(ver.obs, got[:10])
    assert short["register"] == "horizon" and short["tick"] == 10


def test_a_changed_fetch_is_a_named_divergence():
    tp, _refusals, _rec, ver, _prog = t3()
    key = next(iter(tp["score"]["fetches"]))
    f = next(f for f in tp["score"]["fetches"][key] if f["cmds"])
    f["cmds"][0][2] ^= 1
    got, _trap = emit.replay(tp)
    assert certify.divergence(ver.obs, got) is not None


def test_the_print_measures_and_the_document_round_trips():
    tp, _refusals, _rec, ver, _prog = t3()
    md = emit.render(tp)
    n = emit.numbers(tp, md)
    assert set(n) >= {"tokens", "lines", "statements", "blocks", "header_rows", "data_rows", "xz"}
    assert n["statements"] > 0 and n["tokens"] > n["lines"] > 0 and n["xz"] > 0
    back = emit.from_json(json.loads(json.dumps(emit.to_json(tp))))
    got, trap = emit.replay(back)
    assert trap is None and certify.divergence(ver.obs, got) is None
    assert back["score"]["fetches"].keys() == tp["score"]["fetches"].keys()


def test_a_second_entry_is_a_sample_stream_and_a_varying_input_is_external():
    _tp, _refusals, _rec, _ver, prog = t3()
    prog.meta["schedule"] = [prog.meta["entry"], {"kind": "nmi", "addr": 0x1234}]
    view, st, names = pipeline.present(prog)
    t0 = provenance.document(view, st, names)
    t2 = {"pitch": None, "score": [], "refusals": [], "selectors": [], "streams": []}
    t2["horizon"] = {"ticks": 4}
    inputs = [(0, 0x1000, 0, 0xD012, 1), (1, 0x1000, 0, 0xD012, 2)]
    _tp2, refusals, _rec = emit.lift(prog, view, names, t0, None, t2, CERT, inputs)
    got = {(r.why, r.cell, r.site) for r in refusals}
    assert ("sample stream", "mode_vol", "$1234") in got
    assert ("external input", "$D012", "") in got


def test_a_fetch_region_is_single_entry_and_its_cursors_are_its_own():
    _tp, _refusals, _rec, _ver, prog = t3()
    tables = {(PAT0, PAT0 + 3), (PAT1, PAT1 + 2), (ORDER, ORDER + 2)}
    F, bad = region.fetch(prog, tables)
    assert bad == [] and len(F.regions) >= 1
    for r in F.regions.values():
        assert r.entry in r.blocks and r.exit not in r.blocks and r.exits
        assert F.pcs


def test_the_player_refuses_a_score_it_has_run_out_of():
    tp, _refusals, _rec, _ver, _prog = t3()
    for key in tp["score"]["fetches"]:
        tp["score"]["fetches"][key] = tp["score"]["fetches"][key][:1]
    _got, trap = emit.replay(tp)
    assert trap and trap["trap"] == "score exhausted"
    assert player.DEFAULT_ORDER == ("ad", "sr", "ctrl")
