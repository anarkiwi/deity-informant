"""S7 data section: a table's reach, the layout its view knows, and its accessors."""

from deity_informant.tuneprog import datablock as D
from deity_informant.tuneprog.ir import Bin, Block, Const, Let, Load, Proc, Rgn, Store, Tuneprog
from deity_informant.tuneprog.ir import Var
from deity_informant.tuneprog.recover import Names

from _asm import asm
from _prog import PLAY, printed

BASE = 0x1000


def _rgn(rid, base, size, init, kind="const", stride=1):
    return Rgn(
        id=rid,
        name="%s_%04X" % (kind, base),
        base=base,
        size=size,
        kind=kind,
        stride=stride,
        init=init,
        fields=(0,),
        origin=base,
    )


def _at(base):
    return Bin("+", Const(base, 2), Var("i"), 2)


def _read(rid, base, lo, hi):
    return Let("t%d" % rid, Load("ram", _at(base), 1, lo, hi, rid))


def _write(rid, base, lo, hi):
    return Store("ram", _at(base), Var("a"), 1, lo, hi, rid)


def _prog(regions, stmts):
    proc = Proc("tick", (), (), {"b0": Block("b0", list(stmts))}, "b0", "tick")
    return Tuneprog({"name": "s", "load": (BASE, BASE + 0xFFF)}, list(regions), [], {"tick": proc})


def _section(regions, stmts, names, sites=None):
    return D.section(_prog(regions, stmts), names, sites or {})


# ---- the reach ---------------------------------------------------------------
def test_a_regions_cells_are_its_extent_or_the_columns_its_stride_marks_off():
    assert D.cells(_rgn(1, BASE, 4, b"")) == {BASE, BASE + 1, BASE + 2, BASE + 3}
    assert D.cells(_rgn(1, BASE, 9, b"", stride=3)) == {BASE, BASE + 3, BASE + 6}


def test_a_table_prints_the_bytes_its_accessors_envelopes_reach():
    lines = _section(
        [_rgn(1, BASE, 8, bytes(range(8)))],
        [_read(1, BASE, BASE, BASE + 7)],
        Names(region={1: "T1000"}),
    )
    assert lines[0].startswith("T1000            $1000 8 bytes")
    assert lines[1:] == ["  $1000  00 01 02 03 04 05 06 07"]


def test_a_byte_no_envelope_reaches_is_not_printed_and_the_rows_restart():
    lines = _section(
        [_rgn(1, BASE, 8, bytes(range(8)))],
        [_read(1, BASE, BASE, BASE + 1), _read(1, BASE, BASE + 5, BASE + 6)],
        Names(region={1: "T1000"}),
    )
    assert lines[0].startswith("T1000            $1000 4 bytes")
    assert lines[1:] == ["  $1000  00 01", "  $1005  05 06"]


def test_a_cell_a_stores_envelope_reaches_is_state_and_the_block_counts_it():
    lines = _section(
        [_rgn(1, BASE, 4, bytes(range(4)), kind="state")],
        [_read(1, BASE, BASE, BASE + 3), _write(1, BASE, BASE + 2, BASE + 3)],
        Names(region={1: "b1000"}),
    )
    assert lines[1:] == ["  $1000  00 01", "  2 bytes of it the program writes, not data"]


def test_a_store_reaches_its_own_columns_not_the_ones_between_them():
    a, b = _rgn(1, BASE, 4, bytes(range(4)), stride=2), _rgn(2, BASE + 1, 3, b"", stride=2)
    reached, wrote = D.spans(
        _prog([a, b], [_read(1, BASE, BASE, BASE + 3), _write(2, BASE + 1, BASE + 1, BASE + 3)])
    )
    assert reached[1] == {BASE, BASE + 2} and not any(wrote[BASE : BASE + 4 : 2])
    assert all(wrote[BASE + 1 : BASE + 4 : 2])


def test_a_region_the_program_only_writes_names_no_block():
    lines = _section(
        [_rgn(1, BASE, 2, b"\x00\x00", kind="state")],
        [_write(1, BASE, BASE, BASE + 1)],
        Names(region={1: "b1000"}),
    )
    assert lines == []


# ---- the layouts -------------------------------------------------------------
def test_a_note_table_prints_as_16_bit_entries():
    lines = _section(
        [_rgn(1, BASE, 4, bytes([0x17, 0x01, 0x25, 0x02]))],
        [_read(1, BASE, BASE, BASE + 3)],
        Names(region={1: "FREQ"}, freq={(1,): ("u16le", 2, 0)}),
    )
    assert lines[1:] == ["  $1000  0117 0225"]


def test_a_note_tables_two_columns_are_one_block_and_one_entry_list():
    regions = [_rgn(1, BASE, 2, bytes([0x17, 0x25])), _rgn(2, BASE + 2, 2, bytes([0x01, 0x02]))]
    stmts = [_read(1, BASE, BASE, BASE + 1), _read(2, BASE + 2, BASE + 2, BASE + 3)]
    names = Names(region={1: "FREQ_LO", 2: "FREQ_HI"}, freq={(1, 2): ("lo|hi", 2, 0)})
    lines = _section(regions, stmts, names)
    assert len([l for l in lines if not l.startswith(" ")]) == 1
    assert lines[1:] == ["  $1000  0117 0225"]


def test_the_columns_of_one_record_print_one_row_per_record_under_their_fields():
    img = bytes(range(9))
    regions = [_rgn(i + 1, BASE + i, 9 - i, img[i:], stride=3) for i in range(3)]
    stmts = [_read(i + 1, BASE + i, BASE + i, BASE + i + 6) for i in range(3)]
    names = Names(
        region={1: "a", 2: "b", 3: "c"}, view={i: ("ins", "abc"[i - 1]) for i in (1, 2, 3)}
    )
    lines = _section(regions, stmts, names)
    assert lines[0].startswith("ins[3]           $1000 9 bytes stride 3")
    assert lines[1:] == [
        "  entry  a  b  c",
        "  [  0] 00 01 02",
        "  [  1] 03 04 05",
        "  [  2] 06 07 08",
    ]
    assert lines[1].index(" a") == lines[2].index("00")  # the header sits over its column


# ---- the accessors -----------------------------------------------------------
def test_each_distinct_printed_accessor_is_one_line_with_its_procedures():
    sites = {
        (1, "T1000[i]"): ({"read"}, {"tick"}),
        (1, "T1000[i + 1]"): ({"read", "written"}, {"tick", "init"}),
    }
    lines = _section(
        [_rgn(1, BASE, 2, b"\x01\x02")],
        [_read(1, BASE, BASE, BASE + 1)],
        Names(region={1: "T1000"}),
        sites,
    )
    assert lines[1:3] == [
        "  T1000[i + 1]                   read/written in init, tick",
        "  T1000[i]                       read in tick",
    ]


TABLE = asm(
    PLAY,
    "init: LDA #$00",
    "STA cnt",
    "RTS",
    "play: LDX cnt",
    "LDA tab,X",
    "STA $D400",
    "INC cnt",
    "RTS",
    "cnt: BRK",
    "tab: BRK",
    *["BRK"] * 5,
)


def test_the_accessor_line_is_the_form_the_program_prints_and_the_bytes_are_its_own():
    data = {TABLE.labels["tab"] + i: 0x40 + i for i in range(6)}
    doc = printed(TABLE, calls=6, data=data).split("## data")[1].split("```")[1]
    head = next(l for l in doc.splitlines() if l and not l.startswith(" "))
    assert head.startswith("%-16s $%04X 6 bytes" % (head.split()[0], TABLE.labels["tab"]))
    acc = [l for l in doc.splitlines() if l.startswith("  ") and l.endswith("read in tick")]
    assert len(acc) == 1 and acc[0].strip().startswith("%s[" % head.split()[0])
    assert "  $%04X  40 41 42 43 44 45" % TABLE.labels["tab"] in doc
