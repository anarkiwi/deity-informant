"""Strided intervals, effective-address facts and layout recovery on programs of known layout."""

import json
import struct
import sys
from pathlib import Path

import numpy as np
import pytest
from jennings.devices.mpu6502 import NativeMPU
from jennings.isa import is_memory
from jennings.memory import NativeMemory
from jennings.opcodes import OPCODES

from wholeprog_progs import assemble

from deity_informant import NATIVE, layout, trace, wholeprog
from deity_informant.strided import CAP, Strided, fold

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import layout as layout_tool  # noqa: E402 pylint: disable=wrong-import-position,wrong-import-order

native = pytest.mark.skipif(not NATIVE, reason="needs the native extension")
IO = [(0xC000, 0xC100)]

# Counters, a voice record and its device registers, channel arrays, split tables, a pointer.
PLAYER = """
play:
  INC $40
  BNE nc
  INC $41
nc:
  CLC
  LDA $42
  ADC #$30
  STA $42
  LDA $43
  ADC #$00
  STA $43
  LDX #14
voice:
  LDA $1800,X
  CLC
  ADC #1
  STA $1800,X
  LDA $1801,X
  ADC #0
  STA $1801,X
  LDA $1802,X
  STA $C000,X
  LDA $1803,X
  STA $C001,X
  LDA $1804,X
  STA $C002,X
  LDA $1805,X
  STA $C003,X
  INC $1806,X
  TXA
  SEC
  SBC #7
  TAX
  BPL voice
  LDX #2
chan:
  LDY $1900,X
  LDA flo,Y
  CLC
  ADC $1910,X
  STA $1920,X
  LDA fhi,Y
  ADC #0
  STA $1930,X
  LDA $1940,X
  ASL
  TAY
  LDA ptab,Y
  STA $FB
  LDA ptab+1,Y
  STA $FC
  LDY #2
rd:
  LDA ($FB),Y
  STA $C020,Y
  DEY
  BPL rd
  INC $1900,X
  LDA $1900,X
  AND #3
  STA $1900,X
  DEX
  BPL chan
  RTS
flo: .byte 1,2,3,4
fhi: .byte 5,6,7,8
ptab: .byte lo(d0),hi(d0),lo(d1),hi(d1)
d0: .byte 1,2,3
d1: .byte 4,5,6
"""

CHANNELS = {0x1940: 0, 0x1941: 1, 0x1942: 1, 0x1901: 1, 0x1902: 2}

# A split address table indexed by X feeding the pointer at $FB; parallel arrays $1A00/$1A80.
SPLIT = """
play:
  LDX $30
  INX
  CPX #3
  BNE ok
  LDX #0
ok:
  STX $30
  LDA tlo,X
  STA $FB
  LDA thi,X
  STA $FC
  LDY #0
  LDA ($FB),Y
  STA $1A00,X
  STA $1A80,X
  RTS
tlo: .byte lo(d0),lo(d1),lo(d2)
thi: .byte hi(d0),hi(d1),hi(d2)
d0: .byte 1
d1: .byte 2
d2: .byte 3
"""

# A word at $50 and a pointer at $51, $1B00 at strides 2 and 3 conflict; strides 7 and 14 merge.
CONFLICT = """
play:
  CLC
  LDA $50
  ADC #1
  STA $50
  LDA $51
  ADC #0
  STA $51
  LDY #0
  LDA ($51),Y
  STA $C000
  LDX #4
a:
  LDA $1B00,X
  DEX
  DEX
  BPL a
  LDX #6
b:
  LDA $1B00,X
  DEX
  DEX
  DEX
  BPL b
  LDX #14
c:
  LDA $1C00,X
  CPX #7
  BEQ d
  LDA $1C01,X
d:
  TXA
  SEC
  SBC #7
  TAX
  BPL c
  RTS
"""


def _run(src, calls=40, init=None):
    img, lab = assemble(src)
    img = bytearray(img)
    for a, v in (init or {}).items():
        img[a] = v
    play = lab["play"]
    facts = trace.collect(img, lambda s: [s.call(play) for _ in range(calls)], ea=True)
    prog = wholeprog.lift(img, [play], IO, facts)
    return img, lab, facts, layout.build(prog, facts, img, IO)


def _keys(m):
    return {
        (
            r["kind"],
            r["base"],
            r.get("stride"),
            tuple((f["offset"], f["width"]) for f in r["fields"]),
        )
        for r in m["regions"]
    }


def _pr(got, want):
    hit = len(got & want)
    return hit / len(got), hit / len(want)


def test_strided_basics():
    s = Strided.of([14, 0, 7])
    assert (s.lo, s.hi, s.stride, s.count, s.points) == (0, 14, 7, 3, 3)
    assert s.values() == [0, 7, 14] and 7 in s and 8 not in s and 21 not in s
    j = s.join(Strided.of([3]))
    assert (j.stride, j.values()) == (1, [0, 3, 7, 14]) and 4 not in j
    assert Strided.from_list(json.loads(json.dumps(j.to_list()))) == j
    one = Strided.of([5])
    assert (one.stride, one.count, one.values(), 5 in one) == (0, 1, [5], True)
    wide = Strided.of(range(0, 2 * CAP + 2, 2))
    assert (wide.stride, wide.bits, wide.count, wide.values()) == (2, None, None, None)
    assert 6 in wide and 7 not in wide and Strided.from_list(wide.to_list()) == wide
    assert wide.join(one).bits is None


def test_fold_matches_whole():
    rng = np.random.default_rng(1)
    keys = rng.integers(0, 6, 4000)
    vals = rng.integers(0, 40, 4000) * (keys + 1) + keys
    state = {}
    for part in np.array_split(np.arange(4000), 7):
        fold(state, keys[part], vals[part])
    assert fold(state, [], []) is state
    for k in range(6):
        assert state[k] == Strided.of(vals[keys == k].tolist())


def test_union_find_offsets():
    uf = layout.UnionFind()
    assert uf.union(10, 13, 3) and uf.union(13, 20, 7) and uf.union(5, 10, 5)
    assert uf.find(20) == (5, 15) and uf.union(10, 20, 10)
    assert not uf.union(10, 20, 9)
    assert uf.groups([5, 10, 13, 20, 99]) == {5: {5: 0, 10: 5, 13: 8, 20: 15}, 99: {99: 0}}


def test_accesses_per_instruction():
    """Each memory instruction makes the data accesses the EA fold expects, in order."""
    for op, (_, md) in OPCODES.items():
        if trace._MODE[op] < 0:  # pylint: disable=protected-access
            assert md not in trace.MODES or not is_memory(op)
            continue
        mem = NativeMemory(bytes(0x80) + b"\x00\x20" + bytes(0xF7E) + bytes([op, 0x80, 0x10, 0x60]))
        mpu = NativeMPU(memory=mem)
        seen = []
        mem.observe_data_access(on_read=seen.append, on_write=lambda a, v, s=seen: s.append(a))
        mpu.run_to_rts(0x1000, 0, 100)
        mem.observe_data_access()
        assert len(seen) == trace._K[op], (hex(op), md, seen)  # pylint: disable=protected-access
        if md in ("indx", "indy"):
            assert seen[:2] == [0x80, 0x81], hex(op)


def test_ea_facts():
    _, lab, facts, _ = _run(PLAYER, init=CHANNELS)
    ea = facts.ea
    (rec,) = ea[lab["voice"]].values()
    assert rec["mode"] == "absx" and rec["n"] == 40 * 3
    assert trace.EA.strided(rec, "index").values() == [0, 7, 14]
    assert trace.EA.strided(rec, "base").values() == [0x1800]
    assert trace.EA.strided(rec, "addr").values() == [0x1800, 0x1807, 0x180E]
    (ind,) = ea[lab["rd"]].values()
    assert ind["mode"] == "indy" and trace.EA.strided(ind, "base").values() == [0xFB]
    assert trace.EA.strided(ind, "ptr").values() == [lab["d0"], lab["d1"]]
    assert trace.EA.strided(ind, "index").values() == [0, 1, 2]
    assert trace.EA.strided(rec, "ptr") is None
    back = trace.Facts.loads(facts.dumps())
    assert back.ea == facts.ea and back.to_dict() == facts.to_dict()
    assert "ea" not in trace.Facts().to_dict()


def test_ea_bounded(monkeypatch):
    """The evidence is a fixed size per instruction however many calls ran."""
    monkeypatch.setattr(trace.EA, "LIMIT", 64)
    sizes = []
    for calls in (10, 10_000):
        _, _, facts, _ = _run(SPLIT, calls)
        sizes.append(
            {
                (pc, op): {k: v for k, v in r.items() if k != "n"}
                for pc, ops in facts.ea.items()
                for op, r in ops.items()
            }
        )
    assert sizes[0] == sizes[1]


@native
def test_player_layout():
    img, lab, _, m = _run(PLAYER, init=CHANNELS)
    flo, fhi, ptab = lab["flo"], lab["fhi"], lab["ptab"]
    want = {
        ("word", 0x40, None, ((0, 2),)),
        ("word", 0x42, None, ((0, 2),)),
        ("pointer", 0xFB, None, ((0, 2),)),
        ("address_table", ptab, 2, ((0, 2),)),
        ("record", 0x1800, 7, ((0, 2), (2, 1), (3, 1), (4, 1), (5, 1), (6, 1))),
        ("record", 0xC000, 7, ((0, 1), (1, 1), (2, 1), (3, 1))),
        ("parallel_array", 0x1900, 1, tuple((16 * k, 1) for k in range(5))),
        ("parallel_array", flo, 1, ((0, 1), (fhi - flo, 1))),
        ("array", flo, 1, ((0, 1),)),
        ("array", fhi, 1, ((0, 1),)),
        ("array", 0xC020, 1, ((0, 1),)),
    } | {("array", 0x1900 + 16 * k, 1, ((0, 1),)) for k in range(5)}
    assert _pr(_keys(m), want) == (1.0, 1.0)
    by = {(r["kind"], r["base"]): r for r in m["regions"]}
    assert by["address_table", ptab]["targets"] == [lab["d0"], lab["d1"]]
    assert by["address_table", ptab]["pointers"] == [0xFB]
    assert by["pointer", 0xFB]["targets"] == [lab["d0"], lab["d1"]]
    assert by["parallel_array", flo]["words"] == [[0, fhi - flo]]
    rec = by["record", 0x1800]
    assert (rec["count"], rec["extent"], rec["index"]) == (3, [0x1800, 0x1814], "x")
    assert layout.elements(rec) == [0x1800, 0x1807, 0x180E]
    assert layout.elements(rec | {"base": 0x1802, "extent": [0x1800, 0x1814]}) == [
        0x17FB,
        0x1802,
        0x1809,
    ]
    assert by["record", 0xC000]["io"] and "io" not in rec
    assert m["conflicts"] == []
    assert json.loads(json.dumps(m)) == m
    assert img[ptab] == lab["d0"] & 0xFF


@native
def test_split_table_layout():
    _, lab, _, m = _run(SPLIT)
    tlo, thi = lab["tlo"], lab["thi"]
    want = {
        ("pointer", 0xFB, None, ((0, 2),)),
        ("address_table", tlo, 1, ((0, 1), (3, 1))),
        ("parallel_array", tlo, 1, ((0, 1), (3, 1), (0x1A00 - tlo, 1), (0x1A80 - tlo, 1))),
        ("array", tlo, 1, ((0, 1),)),
        ("array", thi, 1, ((0, 1),)),
        ("array", 0x1A00, 1, ((0, 1),)),
        ("array", 0x1A80, 1, ((0, 1),)),
    }
    assert _pr(_keys(m), want) == (1.0, 1.0)
    t = next(r for r in m["regions"] if r["kind"] == "address_table")
    assert t["targets"] == [lab["d0"], lab["d1"], lab["d2"]] and t["words"] == [[0, 3]]
    assert (t["count"], t["extent"], t["pointers"]) == (3, [tlo, thi + 2], [0xFB])
    assert layout.table_entries(t) == [(tlo + k, thi + k) for k in range(3)]
    want = {tlo + k: "lo" for k in range(3)} | {thi + k: "hi" for k in range(3)}
    assert layout.address_bytes(m["regions"]) == want | {0xFB: "lo", 0xFC: "hi"}
    assert layout.table_entries({k: v for k, v in t.items() if k != "extent"}) == []


@native
def test_conflicts():
    _, _, _, m = _run(CONFLICT)
    kinds = sorted(c["kind"] for c in m["conflicts"])
    assert kinds == ["atoms", "strides"]
    atoms = next(c for c in m["conflicts"] if c["kind"] == "atoms")
    assert (atoms["base"], atoms["spans"]) == (0x50, [[0, 2], [1, 2]])
    (r,) = [r for r in m["regions"] if r["kind"] == "pointer"]
    assert (r["base"], r["size"], r["fields"]) == (0x50, 3, [{"offset": 0, "width": 3}])
    arr = {(r["base"], r["stride"]) for r in m["regions"] if r["kind"] == "array"}
    assert arr == {(0x1B00, 2), (0x1B00, 3)}
    (rec,) = [r for r in m["regions"] if r["kind"] == "record"]
    assert (rec["base"], rec["stride"], rec["count"]) == (0x1C00, 7, 3)
    assert rec["fields"] == [{"offset": 0, "width": 1}, {"offset": 1, "width": 1}]


@native
def test_unexecuted_table_and_static_pointers():
    """Tables and pointers from the ops alone carry no counts; a ``(zp,X)`` pointer at a
    constant index is a static pointer."""
    src = """
play:
  LDX #2
  LDA ($F0,X)
  STA $C000
  LDA $31
  BEQ skip
  LDY $32
  LDA tlo,Y
  STA $FD
  LDA thi,Y
  STA $FE
  LDA ($FD),Y
  STA $C001
skip:
  RTS
tlo: .byte 0
thi: .byte 0
"""
    _, lab, _, m = _run(src)
    by = {(r["kind"], r["base"]): r for r in m["regions"]}
    assert set(by) == {("pointer", 0xF2), ("pointer", 0xFD), ("address_table", lab["tlo"])}
    t = by["address_table", lab["tlo"]]
    assert "count" not in t and t["words"] == [[0, 1]] and t["pointers"] == [0xFD]


def _psid(code, play, load=0x1000, init=0x1000):
    hdr = b"PSID" + struct.pack(">HHHHHHHI", 2, 0x7C, load, init, play, 1, 1, 0)
    return hdr + b"\0" * 0x60 + struct.pack(">HBBBB", 0x14, 0, 0, 0, 0) + code


@native
def test_tool(tmp_path, capsys):
    img, lab = assemble("init:\n  RTS\n" + SPLIT)
    code = bytes(img[0x1000 : lab["d2"] + 1])
    path = tmp_path / "t.sid"
    path.write_bytes(_psid(code, lab["play"]))
    out = tmp_path / "m.json"
    assert layout_tool.main([str(path), "--calls", "20", "--json", str(out)]) == 0
    m = json.loads(out.read_text())
    assert m["tune"] == {"init": 0x1000, "play": lab["play"], "songs": 1, "song": 1, "calls": 20}
    assert layout_tool.summary(m) == {
        "address_table": 1,
        "array": 4,
        "parallel_array": 1,
        "pointer": 1,
        "conflicts": 0,
    }
    assert set(m["starts"]) >= {lab["play"], lab["ok"]} and lab["d0"] not in m["starts"]
    assert [lab["tlo"], lab["d2"]] in m["accessed"] and [0x30, 0x30] in m["accessed"]
    text = capsys.readouterr().out
    assert "address_table  $%04X-$%04X" % (lab["tlo"], lab["thi"] + 2) in text
    with pytest.raises(ValueError):
        layout_tool.song_layout(_psid(code, 0))


@native
def test_written_table_has_no_targets():
    _, lab, _, m = _run(SPLIT.replace("  RTS\ntlo:", "  INC thi+2\n  DEC thi+2\n  RTS\ntlo:"))
    (t,) = [r for r in m["regions"] if r["kind"] == "address_table"]
    assert t["written"] and "targets" not in t and t["base"] == lab["tlo"]
