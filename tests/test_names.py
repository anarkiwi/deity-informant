"""Atoms of the memory regions and names from the data flow (docs/cgen.md, *Names*)."""

import numpy as np
import pytest

import wholeprog_progs as W
from deity_informant import cgen
from deity_informant.cgen import names
from test_cgen import VARPTR, assemble, body_of, cgenplay
from test_cgen import fixture_prefix  # noqa: F401  pylint: disable=unused-import

SID = (0xD400, 0xD420)

VOICES = """
entry:  LDX #2
loop:   LDY regs,X
        LDA freq,X
        CLC
        ADC #1
        STA freq,X
        STA $D400,Y
        LDA ctrl,X
        STA $D404,Y
        DEX
        BPL loop
        LDA vol
        STA $D418
        INC vol
        RTS
regs:   .byte 0,7,14
freq:   .byte 1,2,3
ctrl:   .byte $11,$21,$41
vol:    .byte 5
end:
"""

RECORDS = """
entry:  TAY
        LDA $3000,Y
        STA $D418
        LDX #14
loop:   LDA $3000,X
        CLC
        ADC $3001,X
        STA $3001,X
        STA $D401,X
        TXA
        SEC
        SBC #7
        TAX
        BPL loop
        RTS
end:
"""

POINTER = """
entry:  LSR
        BCC read
        AND #$01
        ASL
        TAX
        LDA ptrs,X
        STA $FB
        LDA ptrs+1,X
        STA $FC
        RTS
read:   LDY #1
        LDA ($FB),Y
        STA $D405
        RTS
ptrs:   .byte lo(tab),hi(tab),lo(tab+2),hi(tab+2)
tab:    .byte 9,8,7,6
end:
"""

STRADDLE = """
entry:  AND #$01
        BEQ play
        LDX #5
clear:  LDA #0
        STA lo,X
        DEX
        BPL clear
        RTS
play:   LDX #2
each:   LDA lo,X
        STA $D400
        LDA hi,X
        STA $D401
        INC lo,X
        DEX
        BPL each
        RTS
lo:     .byte 1,2,3
hi:     .byte 4,5,6
end:
"""


def translate(prefix, src, org=0x1000):
    """``(c, h, info, image, labels)`` of ``src`` at ``org``, the SID volatile."""
    img, labels, loaded = assemble(src, org)
    c, h, info = cgen.translate(
        img, [(labels["entry"], "entry")], [W.IO, SID], loaded=loaded, header="t.h", prefix=prefix
    )
    return c, h, info, img, labels


def replay(tmp_path, c, h, img, labels, values):
    """Compile ``c``/``h`` and replay ``entry`` for each ``A`` of ``values`` against the
    reference, the SID's writes compared."""
    (tmp_path / "t.c").write_text(c)
    (tmp_path / "t.h").write_text(h)
    lib = cgenplay.build(tmp_path / "t.c", tmp_path / "t.so")
    calls = [("entry", a) for a in values]
    cgenplay.replay(lib, img, {"entry": labels["entry"]}, calls, [W.IO, SID])


def test_sid_registers_by_name():
    """SID addresses name their register and voice, as the llvm-mos SDK's ``struct __sid``."""
    assert names.sid_register(0xD40B) == ("ctrl", 2)
    assert names.sid_register(0xD418) == ("amp", None)
    assert names.sid_register(0xD41D) is None and names.sid_register(0xD3FF) is None
    assert (
        names.sid_symbol(0xD412) == "SID_V3_CTRL" and names.sid_symbol(0xD415) == "SID_FLT_FREQ_LO"
    )
    assert names.sid_symbol(0xC000) is None and names.sid_label(0xC000, False) is None
    assert (
        names.sid_label(0xD407, False) == "freq_lo2" and names.sid_label(0xD407, True) == "freq_lo"
    )


def test_labels_rank_registers_then_indices():
    """As near to both bytes of a 16-bit register a node takes its name; else the first
    register by address and voice, then the voice index before the register offset."""
    assert names.label_of({"freq_hi", "freq_lo", "ctrl"}) == "freq"
    assert names.label_of({"pw_hi1", "pw_lo1"}) == "pw1"
    assert names.label_of({"ctrl", "ad"}) == "ctrl" and names.label_of({"amp", "sr3"}) == "sr3"
    assert names.label_of({names.VOICE_REG, names.VOICE_INDEX}) == names.VOICE_INDEX
    assert names.label_of({"x", names.VOICE_REG}) == names.VOICE_REG
    assert names.label_of(()) is None


def test_flow_labels_by_nearest_seed():
    """Breadth-first backwards from every seed: a node takes the labels of the nearest."""
    flow = names.Flow()
    for a, b in (("a", "b"), ("b", "s1"), ("a", "c"), ("c", "d"), ("d", "s2"), ("e", "s2")):
        flow.edge(a, b)
    flow.edge("a", "a")
    flow.seeds["s1"].add("ctrl")
    flow.seeds["s2"].add("ad")
    got = flow.labels()
    assert got["a"] == (2, "ctrl") and got["c"] == (2, "ad") and got["e"] == (1, "ad")
    assert "f" not in got and got["s1"] == (0, "ctrl")


def test_atoms_of_arrays_fields_scalars_and_pairs():
    """Address sets alike up to a translation are fields of one record, a run its own array;
    a constant access inside one is its element; the other bytes are scalars or pairs; a set
    meeting another's atoms straddles them."""
    acc = [
        (0x10, (0x10, 0x11, 0x12)),
        (0x13, (0x13, 0x14, 0x15)),
        (0x12, (0x12,)),
        (0x20, (0x20, 0x27, 0x2E)),
        (0x21, (0x21, 0x28, 0x2F)),
        (0x10, tuple(range(0x10, 0x16))),
        (0x40, (0x40,)),
        (0x41, (0x41,)),
        (0x50, (0x50,)),
    ]
    written = np.zeros(0x10000, dtype=bool)
    written[0x10:0x30] = True
    atoms, bad = names.atomize(3, acc, words={0x40}, written=written)
    kinds = {(x.org, x.kind, x.offsets, x.const) for x in atoms}
    assert kinds == {
        (0x10, "array", (0, 1, 2), False),
        (0x13, "array", (0, 1, 2), False),
        (0x20, "field", (0, 7, 14), False),
        (0x21, "field", (0, 7, 14), False),
        (0x40, "pair", (0, 1), True),
        (0x50, "scalar", (0,), True),
    }
    assert bad == [tuple(range(0x10, 0x16))] and all(x.region == 3 for x in atoms)
    assert names.atomize(0, [(1, (1,))])[0][0].const is False


def test_unique_and_scoped_names():
    """A base taken, or a C keyword, gets the next number; scopes take names apart."""
    taken = {"ctrl"}
    assert names.unique([(1, "ctrl"), (2, "ctrl"), (3, "if"), (4, "a")], taken) == {
        1: "ctrl_2",
        2: "ctrl_3",
        3: "if_2",
        4: "a_2",
    }
    one, two = {"voice"}, set()
    assert names.scoped("voice", [one, two]) == "voice_2" and "voice_2" in two
    assert names.scoped("voice", [two]) == "voice"


def test_voice_loop_names_registers_cells_and_its_index(prefix, tmp_path):
    """A voice loop's per-voice bytes, promoted to a field, take the SID register they reach;
    the loop's counter choosing the voice register offset ``voice_reg`` from its table is
    ``voice``; an atom of a region is named alike; the C plays as the machine code and is
    the same at another origin."""
    c, h, info, img, labels = translate(prefix, VOICES)
    replay(tmp_path, c, h, img, labels, range(4))
    body = body_of(c, "entry")
    assert "uint8_t g_freq_lo[3] = {g_freq_lo_INIT};" in c and "#define g_freq_lo_INIT 0x01," in h
    assert info["state"]["vars"] == {f"g_freq_lo[{k}]": labels["freq"] + k for k in range(3)}
    assert "io_write(SID_V1_FREQ_LO + voice_reg, " in body and "uint8_t voice = 2;" in body
    assert "voice_reg = C0[C0_AT2 + voice];" in body and "io_write(SID_AMP, g_amp);" in body
    assert info["names"]["atoms"]["g_amp"] == {
        "region": 0,
        "org": labels["vol"],
        "offsets": [0],
        "kind": "scalar",
        "const": False,
        "label": "amp",
    }
    assert info["names"]["registers"] == {
        "SID_V1_FREQ_LO": 0xD400,
        "SID_V1_CTRL": 0xD404,
        "SID_AMP": 0xD418,
    }
    assert "#define SID_V1_CTRL 0xD404" in h and "#define g_amp (R0[0x0])" in h
    assert info["names"]["accesses"] == {"atom": 2, "cell": 2, "held": 2, "straddling": 0}
    assert info["names"]["variables"] == {"labelled": 3, "role": 1}
    assert translate(prefix, VOICES, 0x2345)[0] == c


def test_records_at_the_voice_stride_are_fields(prefix, tmp_path):
    """Bytes of per-voice records at the SID's stride, aliased by an access of any record
    byte, are a field of their region indexed by the register offset."""
    c, h, info, img, labels = translate(prefix, RECORDS)
    replay(tmp_path, c, h, img, labels, range(16))
    assert info["names"]["atoms"] == {
        "g_freq_hi": {
            "region": 0,
            "org": 0x3001,
            "offsets": [0, 7, 14],
            "kind": "field",
            "const": False,
            "label": "freq_hi",
        }
    }
    assert "g_freq_hi[voice_reg] = freq_hi;" in c and "#define g_freq_hi (R0 + 0x0)" in h
    assert "io_write(SID_V1_FREQ_HI + voice_reg, freq_hi);" in c and "CGEN_IX(R0, " in c


def test_pointer_pair_is_one_atom(prefix, tmp_path):
    """The two bytes of a zero-page pointer, read as one 16-bit value, are one pair."""
    c, h, info, img, labels = translate(prefix, POINTER)
    replay(tmp_path, c, h, img, labels, [1, 0, 3, 0, 0])
    assert info["names"]["atoms"]["g_ptr"]["kind"] == "pair"
    assert info["names"]["atoms"]["g_ptr"]["org"] == 0xFB
    assert "g_ptr[0] | ((uint16_t)g_ptr[1] << 8)" in c and "#define g_ptr (R0 + 0x0)" in h
    assert "io_write(SID_V1_AD, " in c


def test_an_access_straddling_atoms_stays_the_regions(prefix, tmp_path):
    """A loop over two arrays' bytes straddles them: it indexes the region, listed in
    ``straddling``, while the arrays' own accesses name them."""
    c, h, info, img, labels = translate(prefix, STRADDLE)
    replay(tmp_path, c, h, img, labels, [1, 0, 0, 1, 0])
    lo = labels["lo"]
    assert info["names"]["straddling"] == {0: [[lo, lo + 5]]}
    assert info["names"]["accesses"] == {"atom": 3, "straddling": 1}
    assert "R0[i_2] = 0;" in c and "io_write(SID_V1_FREQ_LO, freq_lo1);" in c
    assert "io_write(SID_V1_FREQ_HI, g_freq_hi1[i]);" in c and "#define g_freq_hi1 (R0 + 0x3)" in h


def test_a_named_cell_is_found_by_address(prefix, tmp_path):
    """A promoted cell the data flow names is the variable ``cgen_var_at`` finds by address
    for an access through a pointer the lift does not bound."""
    src = VARPTR.replace("STA $C001", "STA $D400")
    c, h, info, img, labels = translate(prefix, src)
    replay(tmp_path, c, h, img, labels, [labels["lv"] + 1 >> 8] * 4)
    assert info["state"]["vars"] == {"g_freq_lo1": labels["lv"] + 1}
    assert "return &g_freq_lo1;" in c and "uint8_t g_freq_lo1 = g_freq_lo1_INIT;" in c


@pytest.mark.parametrize("src", [VOICES, RECORDS, POINTER, STRADDLE])
def test_names_leave_the_compiled_program(prefix, src):
    """Every view the ``.c`` names is defined once in the ``.h`` and no local shares an atom's
    or a SID register's name."""
    c, h, info, _, _ = translate(prefix, src)
    defined = [
        w.split()[1]
        for w in h.splitlines()
        if w.startswith(("#define g_", "#define M_")) and not w.split()[1].endswith("_INIT")
    ]
    assert sorted(defined) == sorted(info["names"]["atoms"]) and len(set(defined)) == len(defined)
    body = body_of(c, "entry")
    assert not any(
        f"{t} {p}" in body for t in ("uint8_t", "uint16_t") for p in ("g_", "M_", "SID_")
    )
