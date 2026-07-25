"""Sequencer-state slicing (M4.1): synthetic players assert exact
classification for CI; real-tune tests run full Songlengths length and
auto-skip without the HVSC cache (Commando is the strict ground-truth gate)."""

import json
from pathlib import Path

import pytest

from deity_informant import streams as ST
from deity_informant import structured as S
from deity_informant.c64 import load_psid

import _fuzzgen as G

from _corpus import corpus_params

HVSC = Path(__file__).resolve().parent.parent / ".oracle-cache" / "hvsc"

SID = G.SID
ORG = 0x1000
TBL = 0x1400
CNT = 0x1440
PTR = 0x60
PTLO, PTHI, PATA, PATB = 0x1500, 0x1508, 0x1520, 0x1530
CMDS, SEQ, TBL2 = 0x1540, 0x1550, 0x1560


def _model(asm, data, frames):
    mem = bytearray(0x10000)
    mem[0x0F00] = 0x60  # init: RTS
    for k, b in enumerate(asm.assemble()):
        mem[ORG + k] = b
    for a, v in data.items():
        mem[a] = v
    model, _ev = S.decompile(mem, 0x0F00, ORG, frames)
    return model


def _pointer_player():
    a = G.Asm(ORG)
    a.i("LDX", "abs", CNT)  # order position
    a.i("LDA", "absx", PTLO).i("STA", "zp", PTR)
    a.i("LDA", "absx", PTHI).i("STA", "zp", PTR + 1)
    a.i("LDY", "abs", CNT + 1)  # row position
    a.i("LDA", "indy", PTR).i("STA", "abs", SID + 4)
    a.i("INC", "abs", CNT + 1).i("LDA", "abs", CNT + 1).i("CMP", "imm", 3)
    a.i("BNE", "rel", ("L", "out"))
    a.i("LDA", "imm", 0).i("STA", "abs", CNT + 1)
    a.i("INC", "abs", CNT).i("LDA", "abs", CNT).i("CMP", "imm", 2)
    a.i("BNE", "rel", ("L", "out"))
    a.i("LDA", "imm", 0).i("STA", "abs", CNT)
    a.label("out").i("RTS")
    data = {PTLO: PATA & 0xFF, PTLO + 1: PATB & 0xFF, PTHI: PATA >> 8, PTHI + 1: PATB >> 8}
    data.update({PATA + k: 0x41 + k for k in range(3)})
    data.update({PATB + k: 0x11 + k for k in range(3)})
    return _model(a, data, 14)


def test_counter_classification():
    a = G.Asm(ORG)
    a.i("INC", "abs", CNT).i("LDA", "abs", CNT).i("CMP", "imm", 6)
    a.i("BNE", "rel", ("L", "out"))
    a.i("LDA", "imm", 0).i("STA", "abs", CNT)
    a.label("out").i("LDX", "abs", CNT).i("LDA", "absx", TBL)
    a.i("STA", "abs", SID).i("RTS")
    model = _model(a, {TBL + k: 0x10 + k for k in range(8)}, 8)
    rec = ST.classify(model)[CNT]
    assert rec["class"] == "counter"
    assert rec["deltas"] == [1] and rec["resets"] == [0] and rec["compares"] == [6]
    (s,) = [s for s in ST.streams(model) if s["base"] == TBL]
    assert s["kind"] == "table" and s["position_cells"] == [CNT]
    assert s["consumers"]["sid"] == [SID] and not s["command"]
    (sl,) = [v for k, v in ST.slice_sid(model).items() if k[1] == SID]
    assert sl["cells"] == [CNT] and ("table", TBL) in sl["sites"]


def test_pointer_pair_classification():
    model = _pointer_player()
    cls = ST.classify(model)
    lo, hi = cls[PTR], cls[PTR + 1]
    assert lo["class"] == hi["class"] == "pointer"
    assert lo["pair"] == hi["pair"] == (PTR, PTR + 1)
    assert lo["role"] == "lo" and hi["role"] == "hi"
    assert lo["reload_tables"] == [PTLO] and hi["reload_tables"] == [PTHI]
    assert lo["position_cells"] == [CNT + 1]
    assert cls[CNT]["class"] == "counter" and cls[CNT]["resets"] == [0]
    assert cls[CNT]["index_bases"] == [PTLO, PTHI]  # order cell indexes the reloads
    assert cls[CNT + 1]["class"] == "counter" and cls[CNT + 1]["compares"] == [3]


def test_pointer_stream_and_slice():
    model = _pointer_player()
    (s,) = [s for s in ST.streams(model) if s["kind"] == "pointer"]
    assert s["pair_cells"] == [PTR, PTR + 1]
    assert s["position_cells"] == [CNT + 1]
    assert s["consumers"]["sid"] == [SID + 4]
    (sl,) = [v for k, v in ST.slice_sid(model).items() if k[1] == SID + 4]
    assert sl["cells"] == sorted([PTR, PTR + 1, CNT, CNT + 1])
    assert ("table", PTLO) in sl["sites"] and ("table", PTHI) in sl["sites"]
    assert ("pair", ((PTR, False), (PTR + 1, False))) in sl["sites"]


def test_command_stream_compare_dispatch():
    a = G.Asm(ORG)
    a.i("LDX", "abs", CNT).i("LDA", "absx", CMDS).i("INC", "abs", CNT)
    a.i("CMP", "imm", 1).i("BNE", "rel", ("L", "c2"))
    a.i("LDA", "imm", 0x41).i("STA", "abs", SID + 4).i("RTS")
    a.label("c2").i("CMP", "imm", 2).i("BNE", "rel", ("L", "out"))
    a.i("LDA", "imm", 0x11).i("STA", "abs", SID + 4)
    a.label("out").i("RTS")
    model = _model(a, {CMDS + k: v for k, v in enumerate((1, 2, 1, 2))}, 4)
    (s,) = [s for s in ST.streams(model) if s["base"] == CMDS]
    assert s["command"] and s["consumers"]["compare"] == [1, 2]
    assert s["position_cells"] == [CNT]


def test_index_cell_classification():
    a = G.Asm(ORG)
    a.i("LDX", "abs", CNT).i("LDA", "absx", SEQ).i("STA", "abs", CNT + 1)
    a.i("INC", "abs", CNT)
    a.i("LDX", "abs", CNT + 1).i("LDA", "absx", TBL2).i("STA", "abs", SID + 1).i("RTS")
    data = {SEQ + k: k & 3 for k in range(8)}
    data.update({TBL2 + k: 0x20 + k for k in range(4)})
    model = _model(a, data, 6)
    cls = ST.classify(model)
    assert cls[CNT + 1]["class"] == "index"
    assert cls[CNT + 1]["index_bases"] == [TBL2] and cls[CNT + 1]["reset_tables"] == [SEQ]
    assert cls[CNT]["class"] == "counter" and cls[CNT]["resets"] == []


def test_masked_counter_and_indexed_sid_store():
    a = G.Asm(ORG)
    a.i("LDA", "abs", CNT).i("CLC").i("ADC", "imm", 1).i("AND", "imm", 3)
    a.i("STA", "abs", CNT)
    a.i("LDX", "abs", CNT).i("LDA", "absx", TBL).i("STA", "absx", SID).i("RTS")
    model = _model(a, {TBL + k: 0x30 + k for k in range(4)}, 6)
    rec = ST.classify(model)[CNT]
    assert rec["class"] == "counter" and rec["deltas"] == [1] and rec["masks"] == [3]
    (s,) = [s for s in ST.streams(model) if s["base"] == TBL]
    assert s["consumers"]["sid"] == [SID]  # indexed SID store, base register
    (sl,) = [v for k, v in ST.slice_sid(model).items() if k[1] == SID]
    assert "indexed SID store (base register)" in sl["notes"]


def test_computed_store_recorded_as_top():
    a = G.Asm(ORG)
    a.i("LDA", "imm", PATA & 0xFF).i("STA", "zp", PTR)
    a.i("LDA", "imm", PATA >> 8).i("STA", "zp", PTR + 1)
    a.i("LDY", "abs", CNT).i("LDA", "abs", CNT)
    a.i("STA", "indy", PTR)  # write through pointer: a computed (top) store
    a.i("INC", "abs", CNT).i("LDA", "abs", CNT).i("STA", "abs", SID).i("RTS")
    model = _model(a, {}, 4)
    (sl,) = [v for k, v in ST.slice_sid(model).items() if k[1] == SID]
    assert sl["cells"] == [CNT]
    assert any(n.startswith("computed store at ") for n in sl["notes"])


def test_report_serializable():
    model = _pointer_player()
    rep = ST.report(model)
    json.dumps(rep)
    assert rep["tally"]["pointer"] == 2 and rep["tally"]["counter"] == 2
    assert "pointer  zp_60 pair=zp_60/zp_61" in rep["text"]
    assert "sid.v1.ctrl" in rep["text"]


def _real_model(rel):
    entries = [t for t in corpus_params(HVSC) if str(t[0]).endswith(rel)]
    if not entries:
        pytest.skip("corpus tune absent: %s" % rel)
    sid, sub, secs = entries[0]
    mem, _load, init, play = load_psid(sid.read_bytes())
    mem[0xD418] = 0x0F
    model, _ev = S.decompile(mem, init, play, secs * 50, sub)
    return model


def test_real_tune_streams_commando_ground_truth():
    """Strict gate: the study's Commando ground truth, exact (order counters,
    orderlist pointer pair + reload tables, freq-table streams into freq)."""
    model = _real_model("Hubbard_Rob/Commando.sid")
    cls = ST.classify(model)
    for a in (0x54EC, 0x54ED, 0x54EE):
        assert cls[a]["class"] == "counter"
        assert cls[a]["deltas"] == [1] and cls[a]["resets"] == [0]
    lo, hi = cls[0x5D], cls[0x5E]
    assert lo["class"] == hi["class"] == "pointer"
    assert lo["pair"] == hi["pair"] == (0x5D, 0x5E)
    assert lo["reload_tables"] == [0x56F9] and hi["reload_tables"] == [0x56FC]
    assert {0x54EC, 0x54ED, 0x54EE} <= set(lo["position_cells"])
    strs = ST.streams(model)
    freq_lo = {r for s in strs if s["base"] == 0x5428 for r in s["consumers"]["sid"]}
    freq_hi = {r for s in strs if s["base"] == 0x5429 for r in s["consumers"]["sid"]}
    assert 0xD400 in freq_lo and 0xD401 in freq_hi
    cells = set()
    for sl in ST.slice_sid(model).values():
        cells.update(sl["cells"])
    assert {0x54EC, 0x54ED, 0x54EE, 0x5D, 0x5E} <= cells


@pytest.mark.parametrize(
    "rel,want_ctrl",
    [("Cadaver/Aces_High.sid", True), ("Cadaver/Consultant.sid", False)],
    ids=["Aces_High", "Consultant"],
)
def test_real_tune_streams_goattracker(rel, want_ctrl):
    """GoatTracker: zp $FB/$FC pattern pointer, counters, a command stream;
    stream consumers include ADSR (and ctrl where stream-fed)."""
    model = _real_model(rel)
    cls = ST.classify(model)
    assert cls[0xFB]["class"] == "pointer" and cls[0xFC]["class"] == "pointer"
    assert any(r["class"] == "counter" for r in cls.values())
    strs = ST.streams(model)
    sids = {r for s in strs for r in s["consumers"]["sid"]}
    assert {0xD405, 0xD406} <= sids  # v1 attack_decay / sustain_release
    if want_ctrl:
        assert 0xD404 in sids
    assert any(s["command"] for s in strs)


def test_real_tune_streams_ghouls_pointers():
    """Follin: pointer-shaped sequencer state the staircase scan found none of;
    update-code classification finds reload/advance pointer pairs statically."""
    model = _real_model("Follin_Tim/Ghouls_n_Ghosts.sid")
    cls = ST.classify(model)
    names = {r["name"] for r in cls.values() if r["class"] == "pointer"}
    # the three per-voice script pairs confirmed by docs/follin-dispatch-study.md
    assert {"zp_21", "zp_22", "zp_23", "zp_24", "zp_25", "zp_26"} <= names
    assert any(r["reload_tables"] for r in ptrs)
    assert any(r["advance"] for r in ptrs)
