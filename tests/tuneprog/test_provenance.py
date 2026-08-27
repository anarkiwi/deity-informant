"""T0: per-register provenance of every SID write site (hermetic snippets).

One snippet per shape the lift must read: a direct register write, a voice-strided
indexed one, a register-file image and its flush, a self-update, and the two
addresses no name reaches. Every record's ``print`` is the line the printer
renders for that site, which is the acceptance the exemplars repeat.
"""

import pytest

from deity_informant.tuneprog import pipeline, printer, provenance
from deity_informant.tuneprog.ir import Const, Load, SID_REG_LO, Var

from _asm import asm
from _prog import PLAY, counter, tuneprog

GHOST = 0x1500
FLUSH = (
    "LDX #$18",
    "f1: LDA $1500,X",
    "STA $D400,X",
    "DEX",
    "BPL f1",
)


def t0(code, calls=6, **kw):
    """``(the T0 document, the printed tuneprog)`` of one snippet."""
    _T, prog = tuneprog(code, calls=calls, s4=True, **kw)
    view, st, names = pipeline.present(prog)
    doc = provenance.document(view, st, names)
    text = printer.render(view, st, names, pcs=False)
    lines = {l.strip() for l in text.splitlines()}
    for r in doc["writes"]:  # the self-check: every record re-renders to its line
        assert r["print"] in lines, r["print"]
    return doc, text


def only(doc, **want):
    """The one write record whose fields match ``want``."""
    hit = [r for r in doc["writes"] if all(r[k] == v for k, v in want.items())]
    assert len(hit) == 1, [r["print"] for r in doc["writes"]]
    return hit[0]


def test_a_direct_register_write_names_its_register_voice_and_cell():
    code = counter("INC cnt", "LDX cnt", "LDA $1600,X", "STA $D404")
    doc, _text = t0(code, data={0x1600 + i: i for i in range(8)})
    r = only(doc, register="ctrl")
    assert r["direct"] and r["kind"] == "register" and r["voices"] == [0]
    assert r["envelope"] == ["$D404", "$D404"] and r["site"]["width"] == 1
    assert not r["self_update"] and r["refusal"] is None
    assert [c["role"] for c in r["cells"]] == ["sid_image"]  # the table this register reads
    assert r["cells"][0]["name"] == r["print"].split(" = ")[1] == "ctrl[ctrl_idx]"
    assert r["cells"][0]["base"] == "$1601" and r["cells"][0]["size"] == 6
    assert r["site"]["proc"] and r["site"]["count"] > 0


def test_an_indexed_write_takes_its_voices_from_the_envelope():
    code = asm(
        PLAY,
        "init: RTS",
        "play: LDX #$00",
        "l1: LDA #$21",
        "STA $D400,X",
        "TXA",
        "CLC",
        "ADC #$07",
        "TAX",
        "CPX #$15",
        "BNE l1",
        "RTS",
    )
    doc, _text = t0(code)
    r = only(doc, register="freq_lo")
    assert r["voices"] == [0, 1, 2] and r["envelope"] == ["$D400", "$D40E"]
    assert r["kind"] == "register" and r["refusal"] is None
    assert provenance.regvoices(SID_REG_LO, SID_REG_LO, SID_REG_LO + 14) == ("freq_lo", [0, 1, 2])


@pytest.fixture(name="ghost", scope="module")
def _ghost():
    """A tune that assembles its registers in RAM and flushes the file from it."""
    code = asm(
        PLAY,
        "init: LDX #$18",
        "i1: LDA #$00",
        "STA $1500,X",
        "DEX",
        "BPL i1",
        "RTS",
        "play: INC $1504",
        "CLC",
        "LDA $1500",
        "ADC #$34",
        "STA $1500",
        "LDA $1501",
        "ADC #$12",
        "STA $1501",
        *FLUSH,
        "RTS",
    )
    return t0(code, data={GHOST + i: 0 for i in range(25)})[0]


def test_an_image_write_is_rekeyed_by_the_flush_delta(ghost):
    r = only(ghost, register="freq")
    assert not r["direct"] and r["voices"] == [0] and r["site"]["width"] == 2
    assert r["self_update"] and r["site"]["hifirst"] is False
    assert r["envelope"] == ["$D400", "$D400"] and r["image"]["delta"] == SID_REG_LO - GHOST
    assert r["image"]["flush_pc"] == only(ghost, kind="file", direct=True)["site"]["pc"]
    assert r["image"]["flush_proc"] == only(ghost, kind="file", direct=True)["site"]["proc"]
    assert ghost["image"] == [
        {
            "region": r["image"]["region"],
            "name": r["image"]["name"],
            "base": "$%04X" % GHOST,
            "size": 25,
            "delta": SID_REG_LO - GHOST,
        }
    ]


def test_the_flush_loop_is_the_bridge_and_not_a_register(ghost):
    r = only(ghost, kind="file", direct=True)
    assert r["register"] is None and r["voices"] == [0, 1, 2]
    assert r["envelope"] == ["$D400", "$D418"] and r["refusal"] is None
    assert r["copies"] == r["cells"][0]["region"] and r["cells"][0]["role"] == "sid_image"


def test_a_store_that_reads_its_own_cell_is_a_recurrence(ghost):
    r = only(ghost, register="ctrl")
    assert r["self_update"] and not r["direct"] and r["voices"] == [0]
    assert r["print"].endswith("+= 1")


def test_an_index_the_envelope_cannot_stride_refuses():
    code = asm(
        PLAY,
        "init: LDA #$00",
        "STA idx",
        "STA cnt",
        "RTS",
        "play: INC cnt",
        "LDX idx",
        "LDA cnt",
        "STA $D400,X",
        "INC idx",
        "RTS",
        "idx: BRK",
        "cnt: BRK",
    )
    doc, _text = t0(code, calls=4)
    r = doc["writes"][0]
    assert len(doc["writes"]) == 1 and r["register"] is None
    assert r["refusal"]["why"] == "index not a voice" and r["refusal"]["why"] in provenance.REFUSALS
    assert r["envelope"] == ["$D400", "$D403"] and r["voices"] == [0]
    assert r["refusal"]["cell"] and r["refusal"]["site"] == r["site"]["pc"]


def test_a_patched_store_operand_refuses_as_an_smc_target():
    code = asm(PLAY, "init: RTS", "play: LDA #$11", "sta: STA $D400", "INC sta+1", "RTS")
    doc, _text = t0(code, calls=4)
    r = doc["writes"][0]
    assert len(doc["writes"]) == 1 and r["refusal"]["why"] == "smc target"
    assert r["register"] is None and r["envelope"] == ["$D400", "$D403"]


def test_a_constant_over_the_whole_file_is_provenance_for_each_register():
    code = asm(
        PLAY,
        "init: RTS",
        "play: LDA #$00",
        "LDX #$18",
        "c1: STA $D400,X",
        "DEX",
        "BPL c1",
        "RTS",
    )
    doc, _text = t0(code)
    r = doc["writes"][0]
    assert r["kind"] == "file" and r["copies"] is None and r["refusal"] is None
    assert r["voices"] == [0, 1, 2] and r["cells"] == []


def test_a_sixteen_bit_register_is_one_record_and_a_read_only_one_is_none():
    code = asm(
        PLAY,
        "init: RTS",
        "play: LDA #$34",
        "STA $D400",
        "LDA #$12",
        "STA $D401",
        "STA $D419",
        "RTS",
    )
    doc, _text = t0(code)
    r = only(doc, register="freq")  # the pair the fold made one statement
    assert len(doc["writes"]) == 1 and r["direct"] and r["site"]["width"] == 2
    assert r["voices"] == [0] and r["envelope"] == ["$D400", "$D400"]
    assert r["print"] == "sid[0].freq = $1234"


def test_the_envelope_rule_reads_the_register_file_and_nothing_else():
    lo = SID_REG_LO
    assert provenance.regvoices(lo + 4, lo + 4, lo + 18) == ("ctrl", [0, 1, 2])
    assert provenance.regvoices(0x1234, 0x1234, 0x1234) is None  # not the register file
    assert provenance.regvoices(lo, lo, lo + 3) is None  # a span no voice stride makes
    assert provenance.regvoices(lo, lo, lo + 21) is None  # past the third voice
    assert provenance.regvoices(0xD415, 0xD415, 0xD415) == ("cutoff_lo", [])
    assert provenance.regvoices(0xD415, 0xD415, 0xD416) is None  # a global takes no index
    assert provenance.regvoices(lo + 7, lo, lo) is None  # the envelope starts at the base


def test_the_slice_expands_an_address_and_stops_at_a_named_cell():
    named = Load("ram", Const(0x1500, 2), 1, 0, 0xFFFF, 3)
    anon = Load("ram", Var("t"), 1, 0, 0xFFFF, 9)
    assert provenance.expand(Var("v"), {"v": named}, frozenset({3})) is named
    assert provenance.expand(anon, {"t": Const(5, 2)}, frozenset({3})).a == Const(5, 2)
    assert provenance.expand(anon, {"t": Const(5, 2)}, frozenset({9})) is anon
    assert provenance.leaf(named, frozenset({3})) and not provenance.leaf(anon, frozenset({3}))
