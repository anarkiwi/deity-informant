"""S3: regions from the op-level access relation -- strides, fields, kinds, pointers."""

from deity_informant.tuneprog.lift import lift_trace
from deity_informant.tuneprog.regions import build_regions, index_regions

from _asm import asm, trace_prog

PLAY = 0x1000


def _build(code, init_label="init", play_label="play", calls=2, data=None):
    T, _ = trace_prog(
        {PLAY: code},
        init=code.labels[init_label],
        play=code.labels[play_label],
        calls=calls,
        data=data,
    )
    L = lift_trace(T)
    return T, build_regions(T, L)


def _at(regions, addr):
    return index_regions(regions).get(addr)


def test_stride_one_struct_of_arrays():
    code = asm(
        PLAY,
        "init: RTS",
        "play: LDX #$02",
        "loop: LDA $1100,X",
        "STA $1110,X",
        "DEX",
        "BPL loop",
        "RTS",
    )
    _T, R = _build(code, data={a: 1 for a in range(0x1100, 0x1113)})
    src, dst = _at(R, 0x1100), _at(R, 0x1110)
    assert src.addrs == (0x1100, 0x1101, 0x1102) and src.stride == 1 and src.fields == (0,)
    assert dst.addrs == (0x1110, 0x1111, 0x1112) and dst.stride == 1
    assert src.id != dst.id
    assert src.kind == "init_constant" or src.kind == "const"
    assert dst.kind == "state"


def test_stride_seven_voice_blocks():
    code = asm(
        PLAY,
        "init: RTS",
        "play: LDX #$0E",
        "loop: LDA $1100,X",
        "STA $1200,X",
        "TXA",
        "SBX #$07",
        "BPL loop",
        "RTS",
    )
    _T, R = _build(code, data={a: 3 for a in range(0x1100, 0x1210)})
    r = _at(R, 0x1200)
    assert r.addrs == (0x1200, 0x1207, 0x120E)
    assert r.stride == 7 and r.fields == (0,)
    assert [a["idx"] for a in r.accessors] == [[0, 7, 14]]


def test_stride_forty_nine_struct_of_code_and_fields():
    code = asm(
        PLAY,
        "init: RTS",
        "play: LDX #$62",
        "loop: LDA $1200,X",
        "STA $1240,X",
        "TXA",
        "SBX #$31",
        "BPL loop",
        "RTS",
    )
    _T, R = _build(code, data={a: 5 for a in range(0x1200, 0x12A5)})
    r = _at(R, 0x1240)
    assert r.addrs == (0x1240, 0x1271, 0x12A2) and r.stride == 49
    assert _at(R, 0x1200).stride == 49


def test_pointer_bytes_do_not_merge_with_the_stream():
    code = asm(
        PLAY,
        "init: LDA #$00",
        "STA $FB",
        "LDA #$20",
        "STA $FC",
        "RTS",
        "play: LDY #$02",
        "row: LDA ($FB),Y",
        "STA $D400",
        "DEY",
        "BPL row",
        "RTS",
    )
    _T, R = _build(code, data={0x2000: 1, 0x2001: 2, 0x2002: 3})
    ptr = _at(R, 0x00FB)
    assert ptr.addrs == (0x00FB, 0x00FC) and ptr.size == 2  # one 16-bit pointer, two fetch ops
    assert ptr.kind == "init_constant"  # only init writes it here
    stream = _at(R, 0x2000)
    assert stream is not ptr and stream.addrs == (0x2000, 0x2001, 0x2002)
    assert stream.kind == "const"  # read-only, inside the load band


def test_region_kinds():
    code = asm(
        PLAY,
        "init: LDA #$07",
        "STA $1300",
        "RTS",
        "play: LDA $1300",
        "STA $1310",
        "LDA $1320",
        "STA $D400",
        "STA $D020",
        "LDA $9000",
        "RTS",
    )
    _T, R = _build(code, data={0x1300: 0, 0x1310: 0, 0x1320: 0x11})
    assert _at(R, 0x1300).kind == "init_constant"
    assert _at(R, 0x1310).kind == "state"
    assert _at(R, 0x1320).kind == "const"
    assert _at(R, 0x9000).kind == "image"  # read-only power-on RAM outside the band
    assert _at(R, 0xD400).kind == "io" and _at(R, 0xD020).kind == "io"
    assert _at(R, 0x1300).init_bytes == b"\x00"  # pre-init contents


def test_smc_cell_region_lies_inside_executed_code():
    code = asm(
        PLAY,
        "init: RTS",
        "play: cell: LDA #$00",
        "STA $D400",
        "INC cell+1",
        "RTS",
    )
    _T, R = _build(code, calls=3)
    operand = _at(R, code.labels["cell"] + 1)
    assert operand is not None and operand.kind == "state"
    assert operand.addrs == (code.labels["cell"] + 1,)
    assert _at(R, code.labels["cell"]) is None  # the opcode byte is not accessed as data


def test_opcode_cell_and_its_operand_stay_separate():
    code = asm(
        PLAY,
        "init: RTS",
        "play: LDA gate",
        "EOR #$C9",
        "STA gate",
        "INC gate+1",
        "JSR gate",
        "RTS",
        "gate: LDA #$00",
        "RTS",
    )
    _T, R = _build(code, calls=4)
    g = code.labels["gate"]
    opcode, operand = _at(R, g), _at(R, g + 1)
    assert opcode is not None and operand is not None and opcode.id != operand.id
    assert opcode.addrs == (g,) and operand.addrs == (g + 1,)


def test_accessor_envelope_and_index_regions():
    code = asm(PLAY, "init: RTS", "play: LDY #$03", "loop: LDA $1400,Y", "DEY", "BPL loop", "RTS")
    _T, R = _build(code, data={a: 0 for a in range(0x1400, 0x1404)})
    r = _at(R, 0x1400)
    assert r.accessors[0]["extent"] == [0x1400, 0x1403]
    assert r.accessors[0]["idx"] == [0, 1, 2, 3]
    assert index_regions(R)[0x1403] is r
    d = r.to_dict()
    assert d["stride"] == 1 and d["base"] == 0x1400 and d["kind"] in ("const", "image")
