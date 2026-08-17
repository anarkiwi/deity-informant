"""The Ghidra bridge: facts export and the differential complexity oracle."""

import json

from deity_informant.tuneprog import ghidra_compare as GC, ghidra_facts as GF, pipeline
from deity_informant.tuneprog.cfg import procs_json
from deity_informant.tuneprog.lift import lift_trace

from _asm import asm, trace_prog

PLAY = 0x1000


def _smc_trace(calls=3):
    play = asm(
        PLAY,
        "LDA #$00",  # $1000: immediate cell, patched below
        "STA $D400",
        "INC $1001",  # $1005: patches the LDA operand
        "JMP $100B",  # $1008: control cell, patched by init
        "RTS",
    )
    return trace_prog({PLAY: play, 0x1020: asm(0x1020, "RTS")}, init=0x1020, play=PLAY, calls=calls)


def test_smc_cells_classify_kind_and_context():
    trace, _ = _smc_trace()
    rows = {r["pc"]: r for r in GF.smc_cells(trace, lift_trace(trace))}
    assert rows[PLAY]["kinds"] == ["imm"]
    assert rows[PLAY]["context"] == ["smc_imm"]
    assert rows[PLAY]["cells"] == [PLAY + 1]
    assert rows[PLAY]["mode"] == "imm"


def test_opcode_cell_with_an_rts_variant_uses_smc_var():
    play = asm(PLAY, "LDA #$01", "STA $1000", "RTS")  # $1000 opcode byte gets $01 stored
    trace, _ = trace_prog({PLAY: play, 0x1020: asm(0x1020, "RTS")}, init=0x1020, play=PLAY, calls=2)
    rows = {r["pc"]: r for r in GF.smc_cells(trace, lift_trace(trace))}
    assert PLAY in rows and "opcode" in rows[PLAY]["kinds"]
    assert rows[PLAY]["variants"]


def test_entries_are_unique_and_sorted():
    doc = {
        "procs": [
            {"name": "a", "entry": 0x20, "kind": "sub", "roles": ["sub"], "summary": {}},
            {"name": "a", "entry": 0x10, "kind": "tick", "roles": ["tick"], "summary": {}},
        ]
    }
    rows = GF.entries(doc)
    assert [r["addr"] for r in rows] == [0x10, 0x20]
    assert len({r["name"] for r in rows}) == 2


def test_computed_jumps_and_tail_calls():
    node = {
        "pc": 0x30,
        "mnemonic": "JMP",
        "computed": True,
        "tail_call": False,
        "call": [],
        "switch": {"expr": {"kind": "cell"}, "cases": [[0x40, {}], [0x50, {}]]},
    }
    tail = {"pc": 0x60, "mnemonic": "JMP", "computed": False, "tail_call": True, "call": [0x70]}
    doc = {"procs": [{"name": "p", "nodes": [node, tail]}]}
    assert GF.computed_jumps(doc)[0]["targets"] == [0x40, 0x50]
    assert GF.tail_calls(doc) == [{"pc": 0x60, "target": 0x70}]


def test_regions_take_the_recovered_name():
    rgn = [{"id": 3, "name": "state_1000", "base": 0x1000, "size": 4, "kind": "state", "addrs": []}]
    names = {"regions": [{"id": 3, "name": "voice"}]}
    assert GF.regions(rgn, names)[0]["name"] == "voice"


def test_emulate_facts_record_each_call_s_sid_writes():
    trace, _ = _smc_trace(calls=4)
    e = GF.emulate_facts(trace, calls=3)
    assert e["calls"] == 3 and e["sid_base"] == 0xD400
    assert all(w == [[0, n]] for n, w in enumerate(e["writes"]))  # the cell increments
    assert e["unpinned_inputs"] == []


def _front_end(out):
    """A finished-looking output directory for the synthetic SMC tune."""
    trace, _ = _smc_trace()
    trace.save(out)
    prog, regions, procs = pipeline.build(trace)
    (out / "regions.json").write_text(json.dumps([r.to_dict() for r in regions]))
    (out / "procs.json").write_text(json.dumps(procs_json(procs)))
    prog.save(out / "tuneprog.S4.json")
    return prog


def test_export_writes_a_complete_facts_directory(tmp_path):
    _front_end(tmp_path)
    dst = GF.export(tmp_path)
    doc = json.loads((dst / "ghidra_facts.json").read_text())
    assert doc["language"] == GF.LANGUAGE
    assert len((dst / "image_post_init.bin").read_bytes()) == 0x10000
    assert doc["entries"] and doc["smc_cells"] and doc["insn_addrs"]
    assert doc["meta"]["play"] == PLAY


MD = """# t

## program

```
tick(sp):                                # $1000, 3 calls
    a = 1
    goto L1
```

```
sub(sp):                                 # $1020, 1 calls
    return
```
"""


def test_md_procs_reads_lines_and_gotos():
    assert GC.md_procs(MD) == {0x1000: (3, 1), 0x1020: (2, 0)}


def _pair(stmts, ops, sites=10, gotos=0, unresolved=0, unreachable=0):
    mine = {"name": "p", "sites": sites, "stmts": stmts, "gotos": gotos, "raw_pcode_ops": 0}
    theirs = {
        "sites": sites,
        "pcode_ops": ops,
        "gotos": 0,
        "unresolved": unresolved,
        "unreachable": unreachable,
    }
    return mine, theirs


def test_flag_verdicts():
    assert GC._flag(*_pair(10, 100), GC.TOL)[0] == "ok"
    assert GC._flag(*_pair(200, 100), GC.TOL)[0] == "ours_bigger"
    assert GC._flag(*_pair(5, 100, unresolved=2), GC.TOL)[0] == "ghidra_incomplete"
    assert GC._flag(*_pair(5, 100, unreachable=1), GC.TOL)[0] == "ghidra_incomplete"
    mine, theirs = _pair(5, 100)
    theirs["sites"] = 2
    assert GC._flag(mine, theirs, GC.TOL)[0] == "ghidra_partial"
    assert GC._flag(*_pair(5, 4), GC.TOL)[0] == "ghidra_lead"
    assert GC._flag(*_pair(1, 100, gotos=5), GC.TOL)[0] == "ours_bigger"


def test_compare_joins_both_sides(tmp_path):
    out = tmp_path
    prog = _front_end(out)
    entry = min(b.src for p in prog.procs.values() for b in p.blocks.values() if b.src)
    stats = {
        "sites": 4,
        "raw_pcode_ops": 12,
        "pcode_ops": 40,
        "c_lines": 20,
        "gotos": 0,
        "unresolved": 0,
        "unreachable": 0,
        "warnings": 0,
        "uniques": 3,
        "ms": 1,
        "per_function": [
            {
                "name": "tick",
                "entry": "%04x" % entry,
                "sites": 4,
                "raw_pcode_ops": 12,
                "pcode_ops": 40,
                "c_lines": 20,
                "gotos": 0,
                "unresolved": 0,
                "unreachable": 0,
                "warnings": 0,
                "uniques": 3,
                "ms": 1,
            }
        ],
    }
    g = out / "g"
    g.mkdir()
    (g / "stats.json").write_text(json.dumps(stats))
    doc = GC.compare(out, g)
    assert doc["totals"]["ours"]["sites"] > 0
    assert any(r["entry"] == "%04X" % entry for r in doc["procs"])
    assert "| proc |" in GC.markdown(doc)
