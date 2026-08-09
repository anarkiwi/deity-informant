"""Hermetic tests for tools/source_anchor.py: label-to-address binding by opcode alignment.

A synthetic source and the image assembled from it stand in for the cached players, so
the recovery is checked against known bindings under relocation and under an edit that
puts the two streams out of step."""

import sys
from pathlib import Path

import numpy as np
import pytest

import _fuzzgen as G
from deity_informant.lifter import MODE_LEN

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import source_anchor as SA

POOL = (
    ("LDA", "imm"),
    ("STA", "abs"),
    ("LDX", "imm"),
    ("STX", "abs"),
    ("INX", "impl"),
    ("DEY", "impl"),
    ("AND", "imm"),
    ("ORA", "imm"),
    ("ADC", "imm"),
    ("EOR", "imm"),
    ("ASL", "acc"),
    ("TAX", "impl"),
    ("CLC", "impl"),
    ("LDY", "absx"),
    ("CMP", "imm"),
    ("PHA", "impl"),
    ("PLA", "impl"),
    ("SBC", "indy"),
    ("STY", "zp"),
    ("ROR", "acc"),
)
TEXT = {
    "imm": "#$%02X",
    "abs": "$%04X",
    "absx": "$%04X,X",
    "indy": "($%02X),Y",
    "zp": "$%02X",
    "acc": "A",
    "impl": "",
}
ORG = 0x1000


def spec(n, seed=7):
    """``[(label or None, mnemonic, mode, operand)]``: a stream whose n-grams are unique."""
    rng = np.random.default_rng(seed)
    pick = rng.integers(0, len(POOL), n)
    out = []
    for k, p in enumerate(pick.tolist()):
        mn, mode = POOL[p]
        operand = 0xD400 + k % 251 if mode in ("abs", "absx") else (k % 251) & 0xFF
        out.append(("lbl%d" % k if k % 7 == 3 else None, mn, mode, operand))
    return out


def render(items, org=None):
    """The assembler text of ``items``; ``org`` also states each address as defMON's source does."""
    lines, pc = [], org
    for lab, mn, mode, operand in items:
        head = "%-10s" % (lab + ":") if lab else " " * 10
        arg = TEXT[mode] % operand if "%" in TEXT[mode] else TEXT[mode]
        text = ("%s %s %s" % (head, mn.lower(), arg)).rstrip()
        lines.append(text if org is None else "%s    // $%04X" % (text, pc))
        pc = None if org is None else pc + MODE_LEN[mode]
    return "\n".join(lines)


def image(items, org=ORG):
    """``(mem, seats)``: ``items`` assembled at ``org`` into a bare 64K image."""
    asm = G.Asm(org)
    for _lab, mn, mode, operand in items:
        asm.i(mn, mode, operand)
    mem = np.zeros(0x10000, np.uint8)
    code = asm.assemble()
    mem[org : org + len(code)] = np.frombuffer(code, np.uint8)
    pc, seats = org, []
    for _lab, _mn, mode, _operand in items:
        seats.append(pc)
        pc += MODE_LEN[mode]
    return mem, np.array(seats, np.int64)


def bind(text, mem, seats, ngram=8, min_run=8):
    """The tool's own path: parse, decode, align, anchor -- ``{label: address}``."""
    src = SA.parse_source(text)
    addrs, ids, modes = SA.image_stream(mem, seats)
    runs, pos = SA.align(src, ids, ngram)
    addr = np.where(pos >= 0, addrs[pos.clip(0)], -1)
    ok = (modes[pos.clip(0)] == src["modes"]) | (src["modes"] < 0)
    rows = SA.anchors(src, addr, runs, ok, min_run)
    return {r["label"]: r["addr"] for r in rows}, rows, runs, src


def expected(items, org=ORG):
    """``{label: address}`` from the assembler's own layout: the binding under test."""
    out, pc = {}, org
    for lab, _mn, mode, _operand in items:
        if lab:
            out[lab] = pc
        pc += MODE_LEN[mode]
    return out


@pytest.mark.parametrize("org", (ORG, 0x4000, 0xC123))
def test_recovers_bindings_under_relocation(org):
    items = spec(140)
    got, rows, runs, _src = bind(render(items), *image(items, org))
    assert got == expected(items, org)
    assert len(runs) == 1 and runs[0][2] == len(items)
    assert all(r["run_mode"] == 1.0 for r in rows)


def test_insertion_in_the_image_splits_the_chain():
    items = spec(140)
    cut = 70
    mem, seats = image(items[:cut] + [(None, "NOP", "impl", 0)] + items[cut:])
    got, _rows, runs, _src = bind(render(items), mem, seats)
    want = expected(items)
    assert len(runs) == 2
    assert all(got[k] == want[k] for k in got if int(k[3:]) < cut)
    assert all(got[k] == want[k] + 1 for k in got if int(k[3:]) >= cut)
    assert len(got) > 0.9 * len(want)


def test_deletion_from_the_image_still_anchors_both_sides():
    items = spec(140)
    cut = 70
    kept = items[:cut] + items[cut + 1 :]
    got, _rows, runs, _src = bind(render(items), *image(kept))
    want = expected(kept)
    assert len(runs) == 2
    assert {k: v for k, v in got.items() if k in want} == {k: want[k] for k in got if k in want}
    assert len(got) > 0.9 * len(want)


def test_linear_walk_resyncs_at_the_next_seat_over_data():
    items = spec(40)
    mem, seats = image(items)
    gap = int(seats[20])
    mem[gap - 1] = 0x0C  # a 3-byte opcode: the decode now steps over the seat at gap
    addrs, _ids, _modes = SA.image_stream(mem, seats)
    assert gap in addrs.tolist()
    assert np.array_equal(addrs[addrs >= gap], seats[seats >= gap])


def test_control_reads_the_declared_addresses_as_shift_classes():
    items = spec(120)
    mem, seats = image(items, 0x2000)
    src = SA.parse_source(render(items, org=0x2000))
    addrs, ids, _modes = SA.image_stream(mem, seats)
    _runs, pos = SA.align(src, ids, 8)
    same = SA.control(src, np.where(pos >= 0, addrs[pos.clip(0)], -1))
    assert same["checked"] == len(items) and same["agree"] == len(items)
    assert same["n_classes"] == 1
    moved = SA.control(src, np.where(pos >= 0, addrs[pos.clip(0)] + 0x800, -1))
    assert moved["agree"] == 0 and moved["classes"] == [[-0x800, len(items)]]


def test_dialects_parse_to_one_instruction_stream():
    body = "\n".join(
        (
            "here:      lda #$01",
            "there      sta $d400",
            "gone =*",
            "           ldx $10,y",
            "wide       EQU .",
            "loop:      dex:bne loop     ; two statements, one line",
            "//         lda #$02        <- a Kick comment, not code",
            "  every one of these lines is prose and none of it is an instruction",
        )
    )
    src = SA.parse_source(body)
    assert [SA.op_table()[0][m] for m in ("LDA", "STA", "LDX", "DEX", "BNE")] == src["ids"].tolist()
    assert [n for n, _l, _i in src["labels"]] == ["here", "there", "gone", "wide", "loop"]
    assert [i for _n, _l, i in src["labels"]] == [0, 1, 2, 3, 3]
    assert src["modes"].tolist() == [1, 2, 4, 0, 2]


@pytest.mark.parametrize(
    "operand,want",
    (
        (None, 0),
        ("", 0),
        ("A", 0),
        ("#$0f", 1),
        ("$d400", 2),
        ("lbl + $23", 2),
        ("tbl,x", 3),
        ("tbl,Y", 4),
        ("($fb,x)", 5),
        ("($fb),y", 6),
        ("(vector)", 7),
    ),
)
def test_mode_class_of_operand_syntax(operand, want):
    assert SA.src_mode(operand) == want


def test_short_streams_and_empty_alignments_are_not_errors():
    assert SA.grams(np.arange(3), 8).size == 0
    assert SA.seeds(np.zeros(0, np.uint64), np.zeros(0, np.uint64))[0].size == 0
    assert SA.chain([]) == []
    assert SA.control({"declared": np.array([-1])}, np.array([-1])) is None
