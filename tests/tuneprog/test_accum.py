"""T1: the section 5 accumulators of a recurrence, its bound, its policy, its replay."""

import json
from pathlib import Path
from tempfile import mkdtemp

import numpy as np
import pytest

from deity_informant.tuneprog import accreg, accum, accshape, graph, pipeline
from deity_informant.tuneprog import provenance
from deity_informant.tuneprog.accshape import canon, sext_split, step, terms
from deity_informant.tuneprog.facts import Facts
from deity_informant.tuneprog.history import history
from deity_informant.tuneprog.ir import Bin, Const, Load, Tuneprog, Var, succs
from deity_informant.tuneprog.recover import Names
from deity_informant.tuneprog.tracedata import Trace

from _asm import asm
from _hvsc import COMMANDO, EMOMYST, GULDKORN, LINUS, tune_file
from _prog import PLAY, tuneprog

CERT = {"subtunes": [{"complete": True, "period": 4}]}


def t1(code, calls=64, cert=CERT, **kw):
    """The T1 document of a snippet, over its own certified history and observable."""
    trace, prog = tuneprog(code, calls=calls, s4=True, **kw)
    view, st, names = pipeline.present(prog)
    t0 = provenance.document(view, st, names)
    hist, ver = history(prog, trace, names.to_dict(), calls=calls, obs=True)
    return accum.document(view, names, t0, hist, cert, obs=ver.obs)


def one(doc, **want):
    """The one Acc of a document matching ``want``; asserts there is exactly one."""
    got = [a for a in doc["accs"] if all(_get(a, k) == v for k, v in want.items())]
    assert len(got) == 1, json.dumps({"want": want, "got": doc["accs"]}, indent=1)
    return got[0]


def _get(a, key):
    for part in key.split("."):
        a = a.get(part) if isinstance(a, dict) else None
    return a


def clean(doc):
    """Every Acc replayed and inside its interval, with no refusal left over."""
    assert doc["refusals"] == [], json.dumps(doc["refusals"], indent=1)
    for a in doc["accs"]:
        assert a["verify"]["divergences"] == 0 and a["verify"]["escapes"] == 0, a
    return doc


# ---- the shapes, without a program -------------------------------------------
def test_a_self_referential_add_reads_as_sign_delta_and_carry():
    x = Load("ram", Const(0x10), 1, 0x10, 0x10, 1)
    me = lambda e: e == x  # noqa: E731
    assert step(Bin("+", Bin("+", x, Const(2)), Var("C")), me) == (1, Const(2), Var("C"), False)
    assert step(Bin("+", x, Const(1)), me) == (1, Const(1), None, False)
    borrow = Bin("+", Const(7), Bin("-", Const(1), Var("C")))
    assert step(Bin("-", x, borrow), me) == (-1, Const(7), Var("C"), True)
    assert step(Bin("&", x, Const(3)), me) is None


def test_an_envelope_is_not_part_of_the_value_two_sites_read():
    a = Load("ram", Const(9), 1, 9, 9, 3)
    b = Load("ram", Const(9), 1, 9, 20, 3)
    assert a != b and canon(a) == canon(b)


def test_a_signed_byte_split_three_and_eight_is_one_table_read():
    t = Load("ram", Const(0x2000), 1, 0x2000, 0x2000, 7)
    up = sext_split(Bin("&", t, Const(7)), Bin(">>", t, Const(3)), 3)
    neg = sext_split(
        Bin("|", t, Const(0xF8)),
        Bin("^", Bin(">>", Bin("^", t, Const(0xFF)), Const(3)), Const(0xFF)),
        3,
    )
    assert up == t and neg == t
    assert sext_split(Bin("&", t, Const(7)), Bin(">>", t, Const(4)), 3) is None


def test_the_additive_spine_carries_the_sign_of_every_term():
    x, y = Var("x"), Var("y")
    assert terms(Bin("-", x, Bin("+", y, Const(1)))) == [(1, x), (-1, y), (-1, Const(1))]


# ---- one policy per snippet ---------------------------------------------------
WRAP = asm(
    PLAY,
    "init: LDA #$00",
    "STA acc",
    "RTS",
    "play: LDA acc",
    "CLC",
    "ADC #$03",
    "STA acc",
    "STA $D400",
    "RTS",
    "acc: BRK",
)


def test_a_free_running_add_is_one_wrapping_accumulator():
    doc = clean(t1(WRAP))
    a = one(doc, **{"target.register": "freq_lo"})
    assert a["delta"] == {"kind": "const", "value": 3}
    assert a["policy"] == "wrap" and a["width"] == 8 and a["scope"] == "global"
    assert a["target"]["register"] == "freq_lo" and a["target"]["voices"] == [0]
    assert a["verify"]["ticks"] and a["verify"]["copies"] == 1


REFLECT = asm(
    PLAY,
    "init: LDA #$00",
    "STA ph",
    "STA freq",
    "LDA #$08",
    "STA depth",
    "RTS",
    "play: LDA ph",
    "BMI dn",
    "CMP depth",
    "BCC up",
    "BEQ dn",
    "EOR #$FF",
    "dn: CLC",
    "up: ADC #$02",
    "STA ph",
    "LDA ph",
    "AND #$01",
    "BEQ pos",
    "LDA freq",
    "SEC",
    "SBC #$04",
    "STA freq",
    "JMP out",
    "pos: LDA freq",
    "CLC",
    "ADC #$04",
    "STA freq",
    "out: LDA freq",
    "STA $D400",
    "RTS",
    "ph: BRK",
    "depth: BRK",
    "freq: BRK",
)


def test_a_ones_complement_arm_is_the_triangle_s_reflect():
    doc = clean(t1(REFLECT))
    ph = one(doc, policy="reflect-complement")
    assert ph["delta"] == {"kind": "const", "value": 2} and ph["width"] == 8
    got = one(doc, policy="reflect")
    assert got["delta"] == {"kind": "const", "value": 4}
    assert got["phase"]["kind"] == "acc" and got["phase"]["acc"] == ph["id"]
    assert got["phase"]["bit"] == 0 and got["target"]["register"] == "freq_lo"


BOUNCE = asm(
    PLAY,
    "init: LDA #$00",
    "STA dir",
    "STA pwl",
    "LDA #$08",
    "STA pwh",
    "RTS",
    "play: LDA dir",
    "BNE dn",
    "LDA pwl",
    "CLC",
    "ADC #$40",
    "STA pwl",
    "STA $D402",
    "LDA pwh",
    "ADC #$00",
    "AND #$0F",
    "STA pwh",
    "STA $D403",
    "CMP #$0E",
    "BNE out",
    "INC dir",
    "RTS",
    "dn: LDA pwl",
    "SEC",
    "SBC #$40",
    "STA pwl",
    "STA $D402",
    "LDA pwh",
    "SBC #$00",
    "AND #$0F",
    "STA pwh",
    "STA $D403",
    "CMP #$08",
    "BNE out",
    "DEC dir",
    "out: RTS",
    "pwl: BRK",
    "dir: BRK",
    "pwh: BRK",
)


def test_a_direction_cell_bounces_the_pulse_between_the_ends_it_tests():
    doc = clean(t1(BOUNCE, calls=80))
    a = one(doc, policy="reflect")
    assert a["target"]["register"].startswith("pw") and a["width"] in (12, 16)
    assert a["delta"] == {"kind": "const", "value": 0x40}
    assert a["phase"]["kind"] in ("cell", "counter", "bit") and a["phase"]["cell"]
    lo, hi = a["bound"]["interval"]
    assert 0x800 <= lo <= hi <= 0xEFF and a["verify"]["copies"] == 1


HALT = asm(
    PLAY,
    "init: LDA #$00",
    "STA acc",
    "RTS",
    "play: LDA acc",
    "CMP #$40",
    "BCS out",
    "CLC",
    "ADC #$05",
    "STA acc",
    "STA $D400",
    "out: RTS",
    "acc: BRK",
)

CLAMP = asm(
    PLAY,
    "init: LDA #$00",
    "STA ph",
    "STA freq",
    "LDA #$70",
    "STA tgt",
    "RTS",
    "play: LDA ph",
    "CLC",
    "ADC #$02",
    "STA ph",
    "LDA freq",
    "CLC",
    "ADC #$10",
    "STA freq",
    "CMP tgt",
    "BCC out",
    "LDA tgt",
    "STA freq",
    "LDA #$00",
    "STA ph",
    "out: LDA freq",
    "STA $D400",
    "RTS",
    "ph: BRK",
    "tgt: BRK",
    "freq: BRK",
)

CARRY = asm(
    PLAY,
    "init: LDA #$00",
    "STA one",
    "STA acc",
    "RTS",
    "play: LDA one",
    "CLC",
    "ADC #$80",
    "STA one",
    "STA $D401",
    "LDA acc",
    "ADC #$10",
    "STA acc",
    "STA $D400",
    "RTS",
    "acc: BRK",
    "one: BRK",
)

OPAQUE = asm(
    PLAY,
    "init: LDA #$01",
    "STA acc",
    "RTS",
    "play: LDA acc",
    "ASL A",
    "ORA #$01",
    "STA acc",
    "STA $D400",
    "RTS",
    "acc: BRK",
)


def test_a_guard_the_update_never_passes_is_the_halting_bound():
    doc = clean(t1(HALT))
    a = one(doc, policy="halt")
    assert a["delta"] == {"kind": "const", "value": 5}
    assert a["bound"]["interval"][1] <= 0x40 + 5  # the guard is read before the step


def test_a_snap_to_the_cell_its_guard_compares_against_clamps_and_resets():
    doc = clean(t1(CLAMP))
    a = one(doc, policy="clamp")
    assert a["delta"] == {"kind": "const", "value": 0x10}
    other = one(doc, policy="wrap")
    assert a["links"] == [{"reset": other["id"]}] and other["delta"]["value"] == 2


def test_two_byte_adds_a_carry_joins_are_one_sixteen_bit_accumulator():
    doc = clean(t1(CARRY))
    a = one(doc, policy="wrap")
    assert a["delta"] == {"kind": "const", "value": 0x1080} and a["width"] == 16
    assert a["verify"]["divergences"] == 0


def test_an_update_the_grammar_has_no_term_for_refuses_by_name():
    doc = t1(OPAQUE)
    assert doc["accs"] == []
    assert [(r["why"], r["clause"]) for r in doc["refusals"]] == [("unclassified update", "delta")]
    assert doc["refusals"][0]["cell"] and doc["refusals"][0]["site"]


STEP = asm(
    PLAY,
    "init: LDA #$02",
    "STA sh",
    "LDA #$00",
    "STA idx",
    "STA acc",
    "RTS",
    "play: LDX idx",
    "LDA tab+1,X",
    "SEC",
    "SBC tab,X",
    "STA stp",
    "LDY sh",
    "shift: LSR stp",
    "DEY",
    "BNE shift",
    "LDA acc",
    "CLC",
    "ADC stp",
    "STA acc",
    "STA $D400",
    "INC idx",
    "LDA idx",
    "AND #$03",
    "STA idx",
    "RTS",
    "idx: BRK",
    "sh: BRK",
    "stp: BRK",
    "acc: BRK",
    "tab: BRK",
    "BRK",
    "BRK",
    "BRK",
    "BRK",
)
TAB = {"tab": (4, 20, 44, 92, 188)}

RELOAD = asm(
    PLAY,
    "init: LDA #$00",
    "STA cur",
    "STA pw",
    "LDA #$02",
    "STA tmr",
    "RTS",
    "play: DEC tmr",
    "BPL go",
    "LDX cur",
    "INX",
    "CPX #$03",
    "BNE ok",
    "LDX #$00",
    "ok: STX cur",
    "LDA len,X",
    "STA tmr",
    "LDA base,X",
    "CMP #$FF",
    "BEQ go",
    "STA pw",
    "go: LDX cur",
    "LDA pw",
    "CLC",
    "ADC step,X",
    "STA pw",
    "STA $D402",
    "RTS",
    "cur: BRK",
    "tmr: BRK",
    "pw: BRK",
    "len: BRK",
    "BRK",
    "BRK",
    "base: BRK",
    "BRK",
    "BRK",
    "step: BRK",
    "BRK",
    "BRK",
)
SEG = {"len": (3, 5, 2), "base": (0x10, 0xFF, 0x40), "step": (4, 2, 0xFC)}


def _data(code, tables):
    """``{address: byte}`` for the tables a snippet's labels name."""
    return {code.labels[n] + i: v for n, vs in tables.items() for i, v in enumerate(vs)}


def test_the_difference_of_two_entries_shifted_by_a_cell_is_a_tablestep():
    doc = clean(t1(STEP, calls=48, data=_data(STEP, TAB)))
    a = one(doc, **{"delta.kind": "tablestep"})
    assert a["delta"]["shift"] and a["delta"]["span"] == 1 and a["delta"]["table"]
    assert a["delta"]["index"]["role"] == "cursor"
    assert a["policy"] == "wrap" and a["verify"]["divergences"] == 0


def test_an_ff_sentinel_reloads_the_segment_a_countdown_paces():
    doc = clean(t1(RELOAD, calls=48, data=_data(RELOAD, SEG)))
    a = one(doc, policy="reload")
    assert a["delta"]["kind"] == "tabcell" and a["delta"]["index"]["role"] == "cursor"
    assert a["rate"]["kind"] == "countdown" and a["rate"]["counter"]


# ---- reload then step, and a carry another block supplies ----------------------
SEGSTEP = asm(
    PLAY,
    "init: LDA #$00",
    "STA cur",
    "STA cut",
    "LDA #$01",
    "STA tmr",
    "RTS",
    "play: DEC tmr",
    "BPL go",
    "LDX cur",
    "INX",
    "CPX #$03",
    "BNE ok",
    "LDX #$00",
    "ok: STX cur",
    "LDA len,X",
    "STA tmr",
    "LDA base,X",
    "CMP #$FF",
    "BEQ go",
    "STA cut",
    "go: LDX cur",
    "LDA cut",
    "CLC",
    "ADC step,X",
    "STA cut",
    "STA $D416",
    "RTS",
    "cur: BRK",
    "tmr: BRK",
    "cut: BRK",
    "len: BRK",
    "BRK",
    "BRK",
    "base: BRK",
    "BRK",
    "BRK",
    "step: BRK",
    "BRK",
    "BRK",
)
SEGS = {"len": (1, 2, 1), "base": (0x20, 0xFF, 0x60), "step": (7, 3, 0x100 - 5)}


def test_a_segment_reload_and_the_step_that_follows_it_are_one_tick():
    """The tick that re-points a stream reloads *and* steps: the order the play does."""
    doc = clean(t1(SEGSTEP, calls=48, data=_data(SEGSTEP, SEGS)))
    a = one(doc, policy="reload")
    assert a["delta"]["kind"] == "tabcell" and a["verify"]["divergences"] == 0
    view = pipeline.present(tuneprog(SEGSTEP, calls=48, s4=True, data=_data(SEGSTEP, SEGS))[1])[0]
    ss = _writes(view, SEGSTEP.labels["cut"])
    srcs = [x.stmt.src for x in ss]
    assert len(srcs) == 2 and srcs == sorted(srcs)  # the reload ranks before the step it precedes


def _writes(view, addr):
    """Every store into one cell, in the order :func:`~.accshape.rank` runs them."""
    got = accshape.sites(view, Facts(view), accshape.rank(view)[0])
    return [x for k, v in got.items() if k.cells[0][1] == addr for x in v]


def test_reverse_postorder_puts_an_arm_before_the_join_it_falls_into():
    p = pipeline.present(tuneprog(SEGSTEP, calls=8, s4=True)[1])[0].procs["tick"]
    order, back = graph.rpo(p), graph.latches(p)
    assert sorted(order) == sorted(p.blocks) and order[0] == p.entry
    for lbl, b in p.blocks.items():
        for nxt in succs(b.term):
            assert order.index(lbl) < order.index(nxt) or lbl in back


JOINCARRY = asm(
    PLAY,
    "init: LDA #$00",
    "STA sel",
    "STA acc",
    "LDA #$70",
    "STA hic",
    "LDA #$10",
    "STA loc",
    "RTS",
    "play: LDA sel",
    "BEQ lo",
    "LDA hic",
    "CMP #$40",
    "JMP j",
    "lo: LDA loc",
    "CMP #$40",
    "j: LDA acc",
    "ADC #$10",
    "STA acc",
    "STA $D400",
    "INC sel",
    "LDA sel",
    "AND #$01",
    "STA sel",
    "RTS",
    "sel: BRK",
    "acc: BRK",
    "hic: BRK",
    "loc: BRK",
)

PINNED = asm(
    PLAY,
    "init: LDA #$00",
    "STA acc",
    "RTS",
    "play: LDA acc",
    "ADC #$10",  # no CLC: the bit is whatever the caller entered with
    "STA acc",
    "STA $D400",
    "RTS",
    "acc: BRK",
)


def test_a_carry_another_block_of_the_tick_defines_is_carry_of_a_site():
    doc = clean(t1(JOINCARRY))
    a = one(doc, **{"target.register": "freq_lo"})
    assert a["delta"]["kind"] == "const" and a["delta"]["value"] == 0x10
    assert a["delta"]["carry"]["site"] and a["delta"]["carry"]["flag"]
    assert a["verify"]["divergences"] == 0


def test_a_carry_the_tick_is_given_is_an_external_input_and_refuses():
    doc = t1(PINNED)
    assert doc["accs"] == []
    assert [(r["why"], r["clause"]) for r in doc["refusals"]] == [("unclassified update", "delta")]


# ---- a copy loop's scratch, read off the register it lands in -------------------
def _voiceloop(sink):
    """A per-voice loop whose one scratch cell is reloaded from a table each pass."""
    return asm(
        PLAY,
        "init: LDX #$02",
        "iz: LDA #$00",
        "STA cur,X",
        "DEX",
        "BPL iz",
        "RTS",
        "play: LDX #$02",
        "loop: LDA cur,X",
        "CLC",
        "ADC #$01",
        "AND #$03",
        "STA cur,X",
        "TAY",
        "LDA tab,Y",
        "STA sc",
        "LDY dep,X",
        "inner: LDA sc",
        "CLC",
        "ADC #$05",
        "STA sc",
        "DEY",
        "BPL inner",
        "LDY vmap,X",
        "LDA sc",
        sink,
        "DEX",
        "BPL loop",
        "RTS",
        "sc: BRK",
        "cur: BRK",
        "BRK",
        "BRK",
        "dep: BRK",
        "BRK",
        "BRK",
        "vmap: BRK",
        "BRK",
        "BRK",
        "tab: BRK",
        "BRK",
        "BRK",
        "BRK",
    )


VOICED = _voiceloop("STA $D400,Y")
PARKED = _voiceloop("STA $D404,Y")
LOOPTAB = {"dep": (0, 1, 2), "vmap": (0, 7, 14), "tab": (0x10, 0x20, 0x30, 0x40)}


def test_a_register_name_and_a_voice_are_one_field_of_the_observable():
    assert accreg.column("freq", 1) == (1, 0, 16) and accreg.column("freq_hi", 1) == (1, 8, 16)
    assert accreg.column("cutoff_lo", 0) == (6, 0, 3) and accreg.column("cutoff_hi", 0) == (
        6,
        3,
        11,
    )
    assert accreg.column("mode_vol", 0) == (8, 0, 8) and accreg.column("ctrl", 0) is None
    assert accreg._overlaps(accreg.column("freq", 2), accreg.column("freq_hi", 2))
    assert not accreg._overlaps(accreg.column("freq_lo", 2), accreg.column("freq_hi", 2))
    assert not accreg._overlaps(accreg.column("freq_lo", 0), accreg.column("freq_lo", 1))


def test_the_register_bound_names_the_horizon_where_the_tune_does_not_repeat():
    per = [(0, {}, np.array([4, 9]), None), (1, {}, np.array([2, 7]), None)]
    assert accreg.bound(per, False, None, 48) == [
        {"interval": [2, 9], "from": "observed", "witness": "horizon 48 ticks"}
    ]
    assert accreg.bound(per, True, 4, 48)[0]["witness"] == "period 4"
    assert accreg.bound([], True, 4, 48) == []


def test_a_copy_loops_scratch_is_read_off_the_register_it_lands_in_per_voice():
    """One column, three voices: the claim is the register the value lands in."""
    doc = clean(t1(VOICED, calls=48, data=_data(VOICED, LOOPTAB)))
    a = one(doc, **{"delta.kind": "repeat"})
    assert a["target"]["register"] == "freq_lo" and a["target"]["voices"] == [0, 1, 2]
    assert a["policy"] == "reload" and a["scope"] == "voice" and a["cell"]["copies"] == 1
    assert a["verify"]["copies"] == 3 and a["verify"]["divergences"] == 0
    assert a["bound"]["from"] == "observed" and a["delta"]["n"]["name"]


def test_the_same_scratch_with_no_register_column_refuses():
    """``ctrl`` is an edge, not a level: no column, so no series per voice and no claim."""
    doc = t1(PARKED, calls=48, data=_data(PARKED, LOOPTAB))
    assert doc["accs"] == []
    assert [(r["clause"], r["scratch"], r["why"]) for r in doc["refusals"]] == [
        ("replay", True, accum.DIVERGES)
    ]


# ---- the exemplars (marked ``hvsc``; short horizons) ---------------------------
_T1 = {}


def exemplar(rel, calls=1200):
    """The T1 document of one HVSC exemplar through ``pipeline.main``, once."""
    if rel not in _T1:
        out = Path(mkdtemp()) / "t1"
        assert pipeline.main([str(tune_file(rel)), "--out", str(out), "--calls", str(calls)]) == 0
        prog = Tuneprog.load(out / "tuneprog.S4.json")
        s6 = json.loads((out / "tuneprog.S6.json").read_text())
        t0 = json.loads((out / "tuneprog.T0.json").read_text())
        cert = json.loads((out / "certificate.json").read_text())
        regions = json.loads((out / "regions.json").read_text())
        hist, ver = history(prog, Trace.load(out), s6, calls=calls, regions_doc=regions, obs=True)
        view = pipeline.present(prog)[0]
        _T1[rel] = accum.document(view, Names.from_dict(s6), t0, hist, cert, obs=ver.obs)
    return _T1[rel]


@pytest.mark.hvsc
@pytest.mark.parametrize("rel", (LINUS, GULDKORN, EMOMYST, COMMANDO))
def test_every_exemplar_accumulator_is_an_exact_recurrence_or_a_named_refusal(rel):
    doc = exemplar(rel)
    assert doc["horizon"]["ticks"] == 1200 and (doc["accs"] or doc["refusals"])
    for a in doc["accs"]:
        assert a["verify"]["divergences"] == 0 and a["verify"]["escapes"] == 0
        assert a["step"]["clauses"] and a["step"]["value"] and a["step"]["width"] == a["width"]
        for c in a["step"]["clauses"]:
            assert c["site"] and c["kind"] in ("step", "action", "opaque", "half")
            assert all(t["truth"] in (True, False) and t["test"] for t in c["when"])
        assert a["delta"]["kind"] in ("const", "field", "tabcell", "tablestep", "repeat")
        assert a["policy"] in accum.POLICIES and a["scope"] in ("voice", "instrument", "global")
        assert a["target"]["register"] and a["cell"]["name"]
    for r in doc["refusals"]:
        assert r["why"] in accum.WHYS and r["cell"] and r["clause"] in accum.CLAUSES
        assert r["why"] == accum.WHY or r["detail"]
        assert r["why"] != accum.DIVERGES or r["tick"] > 0


@pytest.mark.hvsc
def test_goattracker_s_filter_steps_by_a_table_cell_and_its_vibrato_refuses_by_name():
    doc = exemplar(LINUS)
    flt = one(doc, **{"target.register": "cutoff_hi"})
    assert flt["delta"]["kind"] == "tabcell" and flt["verify"]["divergences"] == 0
    assert flt["step"]["inputs"]  # the cursor it reads mid-tick, as its own clauses
    assert {r["cell"] for r in doc["refusals"]} >= {"ghost", "voice[].b14A0"}


@pytest.mark.hvsc
def test_sid_wizard_s_cutoff_refuses_where_its_reader_cannot_place_a_write():
    doc = exemplar(EMOMYST)
    got = [r for r in doc["refusals"] if r["cell"] == "cutoff_lo"]
    assert got and all(r["why"] in (accum.INEXACT, accum.DIVERGES) for r in got)
    assert all(r["site"] for r in got)


@pytest.mark.hvsc
def test_hubbard_s_portamento_is_exact_and_his_scratch_producers_refuse_by_name():
    doc = exemplar(COMMANDO)
    porta = one(doc, **{"cell.name": "voice[].acc"})
    assert porta["verify"]["divergences"] == 0 and porta["width"] == 16
    assert {r["cell"] for r in doc["refusals"]} >= {"acc_2_lo", "rec2[].b5591"}
    for r in doc["refusals"]:
        assert r["why"] in accum.WHYS and r["cell"] and r["site"]


@pytest.mark.hvsc
def test_jch_s_pulse_and_filter_are_exact_reload_streams_of_segments():
    doc = exemplar(GULDKORN)
    got = [a for a in doc["accs"] if a["target"]["register"] in ("pw", "cutoff_hi")]
    assert len(got) == 3 and {a["target"]["register"] for a in got} == {"pw", "cutoff_hi"}
    for a in got:
        assert a["delta"]["kind"] == "tabcell" and a["delta"]["index"]["role"] == "cursor"
        assert a["rate"]["kind"] == "countdown" and a["verify"]["divergences"] == 0
        assert any(t["at"] for c in a["step"]["clauses"] for t in c["when"])
    assert "voice" in {a["scope"] for a in got}
    assert {r["cell"] for r in doc["refusals"]} == {"voice[].freq_lo"}
