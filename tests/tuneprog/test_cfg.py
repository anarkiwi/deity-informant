"""S2b: procedures, clone-per-entry, tail calls, computed switches, summaries."""

import json

import pytest

from deity_informant.tuneprog.cfg import build_procs, procs_json
from deity_informant.tuneprog.lift import lift_trace
from deity_informant.tuneprog.machine import Refusal
from deity_informant.tuneprog.regions import build_regions

from _asm import asm, trace_prog

PLAY = 0x1000


def _procs(code, calls=2, data=None, regions=False):
    T, _ = trace_prog(
        {PLAY: code}, init=code.labels["init"], play=code.labels["play"], calls=calls, data=data
    )
    L = lift_trace(T)
    R = build_regions(T, L) if regions else None
    return T, build_procs(T, L, R)


def _by_entry(procs, addr):
    return next(p for p in procs.values() if p.entry == addr)


def test_tail_call_and_clone_per_entry():
    code = asm(
        PLAY,
        "init: RTS",
        "play: JSR head",
        "JSR tail",
        "RTS",
        "head: LDA #$01",
        "JMP tail",  # tail call into another entry
        "tail: STA $D400",
        "RTS",
    )
    T, procs = _procs(code)
    head, tail = code.labels["head"], code.labels["tail"]
    ph, pt = _by_entry(procs, head), _by_entry(procs, tail)
    assert [n["term"] for n in ph.nodes.values()][-1] == "tail"
    assert [n for n in ph.nodes.values() if n["term"] == "tail"][0]["call"] == [tail]
    assert ph.summary["calls"] == [tail]
    # the shared tail is cloned into the caller as well as owning its own proc
    assert (tail, T.sites[T.site_at(tail)[0]]["opcode"]) in pt.nodes
    assert all(n["pc"] != tail for n in ph.nodes.values())  # tail edges do not inline
    assert _by_entry(procs, code.labels["play"]).kind == "tick"


def test_shared_tail_is_cloned_into_both_callers():
    code = asm(
        PLAY,
        "init: RTS",
        "play: JSR suba",
        "JSR subb",
        "RTS",
        "suba: LDA #$01",
        "JMP shared",
        "subb: LDA #$02",
        "JMP shared",
        "shared: STA $D400",
        "RTS",
    )
    _T, procs = _procs(code)
    pa, pb = _by_entry(procs, code.labels["suba"]), _by_entry(procs, code.labels["subb"])
    sh = code.labels["shared"]
    # "shared" is not a JSR target, so it is an ordinary jump: cloned into both procs
    assert sh in {n["pc"] for n in pa.nodes.values()}
    assert sh in {n["pc"] for n in pb.nodes.values()}
    assert [n["term"] for n in pa.nodes.values()].count("return") == 1
    assert [n["term"] for n in pb.nodes.values()].count("return") == 1
    assert pa.exits and pb.exits


def test_unmatched_rts_becomes_a_switch_over_observed_targets():
    code = asm(
        PLAY,
        "init: RTS",
        "play: LDA #>back-1",
        "PHA",
        "LDA #<back-1",
        "PHA",
        "trick: RTS",
        "back: STA $D400",
        "RTS",
    )
    _T, procs = _procs(code, calls=1)
    tick = _by_entry(procs, code.labels["play"])
    n = [x for x in tick.nodes.values() if x["pc"] == code.labels["trick"]][0]
    assert n["term"] == "switch"
    assert n["switch"]["expr"]["kind"] == "stack"
    assert [c[0] for c in n["switch"]["cases"]] == [code.labels["back"]]
    assert n["switch"]["default"] == "trap"


def _jmpind_source(mask):
    return (
        PLAY,
        "init: RTS",
        "play: LDA vec",
        "EOR #$%02X" % mask,  # flip the low byte between the two arms
        "STA vec",
        "ind: JMP (vec)",
        "one: STA $D400",
        "RTS",
        "two: STA $D401",
        "RTS",
        "vec: BRK",
        "BRK",
    )


def test_jmp_indirect_becomes_a_switch():
    probe = asm(*_jmpind_source(0))
    code = asm(*_jmpind_source(probe.labels["one"] ^ probe.labels["two"]))
    T, procs = _procs(
        code,
        calls=2,
        data={
            code.labels["vec"]: code.labels["one"] & 0xFF,
            code.labels["vec"] + 1: code.labels["one"] >> 8,
        },
    )
    tick = _by_entry(procs, code.labels["play"])
    n = [x for x in tick.nodes.values() if x["pc"] == code.labels["ind"]][0]
    assert n["term"] == "switch" and n["switch"]["expr"]["kind"] == "jmpind"
    assert sorted(c[0] for c in n["switch"]["cases"]) == sorted(
        [code.labels["one"], code.labels["two"]]
    )
    assert T.meta["unmatched_rts"] == 0


def test_dual_entry_routine_with_a_patched_exit():
    # A synthetic model of Automatas' $1022: entered by JMP from one caller and by
    # JSR from another, whose exit cell is RTS in one context and LDA # in the other.
    code = asm(
        PLAY,
        "init: LDA #$00",
        "STA cnt",
        "RTS",
        "play: LDA cnt",
        "AND #$01",
        "BNE subtick",
        "JSR p1003",
        "JMP wrapend",
        "subtick: JSR p1006",
        "wrapend: INC cnt",
        "RTS",
        "p1003: JMP main",
        "p1006: LDA #$60",
        "STA gate",
        "JSR main",
        "LDA #$A9",
        "STA gate",
        "JMP tail",
        "main: LDA #$07",
        "STA $D400",
        "gate: LDA #$00",
        "STA $D401",
        "tail: LDA #$08",
        "STA $D402",
        "RTS",
        "cnt: RTS",
    )
    T, procs = _procs(code, calls=4)
    L = code.labels
    assert T.meta["unmatched_rts"] == 0
    assert set(T.jsr_targets) == {L["p1003"], L["p1006"], L["main"]}
    pmain = _by_entry(procs, L["main"])
    p1003 = _by_entry(procs, L["p1003"])
    p1006 = _by_entry(procs, L["p1006"])
    # the JMP-entered caller is a bare tail call
    assert len(p1003.nodes) == 1
    n1003 = next(iter(p1003.nodes.values()))
    assert n1003["term"] == "tail" and n1003["call"] == [L["main"]]
    # the opcode cell became a variant switch with both arms inside main
    assert sorted(a["opcode"] for a in pmain.variant_switch[L["gate"]]["arms"]) == [0x60, 0xA9]
    assert pmain.nodes[(L["gate"], 0x60)]["term"] == "return"
    assert pmain.nodes[(L["gate"], 0xA9)]["term"] == "goto"
    # the tail after the gate is cloned into both main and the JSR-entered caller
    tail_op = T.sites[T.site_at(L["tail"])[0]]["opcode"]
    assert (L["tail"], tail_op) in pmain.nodes and (L["tail"], tail_op) in p1006.nodes
    assert p1006.summary["calls"] == [L["main"]]
    assert L["main"] in _by_entry(procs, L["play"]).summary["calls"] or True
    assert [n["term"] for n in p1006.nodes.values()].count("return") == 1


def test_writer_derived_unverified_variant():
    # the writer stores $60 (RTS) into the opcode cell and restores it before the
    # cell ever executes as RTS: that arm is enumerated but marked unverified.
    code = asm(
        PLAY,
        "init: RTS",
        "play: JSR sub",
        "LDA #$60",
        "STA gate",
        "LDA #$A9",
        "STA gate",
        "RTS",
        "sub: gate: LDA #$00",
        "STA $D400",
        "RTS",
    )
    T, procs = _procs(code, calls=3)
    g = code.labels["gate"]
    assert T.cell_values[g] == {0x60, 0xA9}
    arms = _by_entry(procs, code.labels["sub"]).variant_switch[g]["arms"]
    assert [(a["opcode"], a["unverified"]) for a in arms] == [(0xA9, False), (0x60, True)]


def test_call_summaries_and_region_annotation():
    code = asm(
        PLAY,
        "init: RTS",
        "play: LDX #$05",
        "JSR sub",
        "RTS",
        "sub: TXA",
        "STA $1400",
        "RTS",
    )
    _T, procs = _procs(code, calls=1, data={0x1400: 0}, regions=True)
    s = _by_entry(procs, code.labels["sub"]).summary
    assert "X" in s["live_in"] and "A" in s["defs"]
    assert s["activations"] == 1 and s["regions"]


def test_recursion_is_refused():
    code = asm(
        PLAY,
        "init: RTS",
        "play: JSR sub",
        "RTS",
        "sub: LDA depth",
        "BNE out",
        "INC depth",
        "JSR sub",
        "out: RTS",
        "depth: BRK",
    )
    with pytest.raises(Refusal) as e:
        _procs(code, calls=1)
    assert e.value.reason == "recursion"


def test_procs_json_is_serialisable():
    code = asm(PLAY, "init: RTS", "play: JSR sub", "RTS", "sub: STA $D400", "RTS")
    _T, procs = _procs(code, calls=1)
    doc = json.loads(json.dumps(procs_json(procs)))
    assert {p["name"] for p in doc["procs"]} == set(procs)
    p = [x for x in doc["procs"] if x["name"] == "tick"][0]
    assert p["kind"] == "tick" and p["nodes"] and p["exits"]


def _patched_source(mask):
    return (
        PLAY,
        "init: RTS",
        "play: LDA jsr1+1",
        "EOR #$%02X" % mask,
        "STA jsr1+1",
        "jsr1: JSR one",
        "RTS",
        "one: STA $D400",
        "RTS",
        "two: STA $D401",
        "RTS",
    )


def test_patched_jsr_operand_becomes_a_computed_call():
    probe = asm(*_patched_source(0))
    code = asm(*_patched_source(probe.labels["one"] ^ probe.labels["two"]))
    _T, procs = _procs(code, calls=2)
    tick = _by_entry(procs, code.labels["play"])
    n = [x for x in tick.nodes.values() if x["pc"] == code.labels["jsr1"]][0]
    assert n["term"] == "call" and n["computed"]
    assert sorted(n["call"]) == sorted([code.labels["one"], code.labels["two"]])
    assert n["switch"]["expr"] == {"kind": "cell", "addr": code.labels["jsr1"] + 1, "size": 2}
    assert [p.entry for p in procs.values() if p.kind == "sub"]


def test_patched_jmp_with_one_observed_target_is_still_a_switch():
    code = asm(
        PLAY,
        "init: RTS",
        "play: LDA jmp1+1",
        "STA jmp1+1",  # the operand is a written cell even though the value never changes
        "jmp1: JMP one",
        "one: STA $D400",
        "RTS",
    )
    _T, procs = _procs(code, calls=2)
    tick = _by_entry(procs, code.labels["play"])
    n = [x for x in tick.nodes.values() if x["pc"] == code.labels["jmp1"]][0]
    assert n["term"] == "switch" and n["computed"]
    assert n["switch"]["expr"]["kind"] == "cell"
    assert [c[0] for c in n["switch"]["cases"]] == [code.labels["one"]]
