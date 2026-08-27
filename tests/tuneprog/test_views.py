"""S6 group views: the role a split record's own field earns from its cells."""

from deity_informant.tuneprog import pipeline

from _asm import asm
from _prog import PLAY, printed, tuneprog


def _block(target="tab", entries=16):
    """A 12-byte block one init loop clears, its third field indexing ``target``."""
    code = asm(
        PLAY,
        "init: LDX #$0B",
        "clr: LDA #$00",
        "STA blk,X",
        "DEX",
        "BPL clr",
        "LDX #$02",
        "c2: STA v1,X",
        "STA v2,X",
        "DEX",
        "BPL c2",
        "STA cnt",
        "RTS",
        "play: LDX #$02",
        "loop: INC v1,X",
        "INC v2,X",
        "LDA v1,X",
        "STA blk,X",
        "LDA v2,X",
        "STA blk+3,X",
        "LDY blk+6,X",
        "LDA %s,Y" % target,
        "STA $D400",
        "DEX",
        "BPL loop",
        "INC cnt",
        "RTS",
        "cnt: BRK",
        "v1: BRK",
        "BRK",
        "BRK",
        "v2: BRK",
        "BRK",
        "BRK",
        "blk: BRK",
        "tab: BRK",
    )
    data = {code.labels["blk"] + i: 0 for i in range(12)}
    data.update({code.labels["tab"] + i: i for i in range(entries)})
    return printed(code, calls=6, data=data)


def test_a_record_field_that_indexes_a_block_is_that_block_s_cursor():
    doc = _block()
    assert "  .cursor " in doc and "voice_2[v].cursor" in doc, doc


def test_a_field_that_only_selects_one_of_a_few_elements_keeps_its_offset_name():
    """The mirror of the scalar guard: an index into a variable is no cursor."""
    doc = _block("v1")
    assert "  .cursor " not in doc and "  .f06 " in doc, doc
    assert "voice[voice_2[v].f06]." in doc, doc  # still an index, of three elements


def _pair_index(size=16):
    """A tune walking a table through a zero-page pointer pair and a cursor cell."""
    code = asm(
        PLAY,
        "init: LDA #$00",
        "STA cur",
        "STA cnt",
        "RTS",
        "play: LDA #<tab",
        "STA $FB",
        "LDA #>tab",
        "STA $FC",
        "LDY cur",
        "LDA ($FB),Y",
        "STA $D400",
        "INC cur",
        "LDA cur",
        "AND #$03",
        "STA cur",
        "INC cnt",
        "RTS",
        "cur: BRK",
        "cnt: BRK",
        "tab: BRK",
    )
    data = {code.labels["tab"] + i: i for i in range(size)}
    _T, prog = tuneprog(code, calls=6, s4=True, data=data)
    return pipeline.present(prog)


def test_a_table_reached_through_a_pointer_pair_records_the_pair_by_name():
    _view, _st, names = _pair_index()
    ptr = [x for x in names.index if x["base"] == "ptr"]
    assert ptr and all(x["pair"] == "ptr" for x in ptr), names.index
    assert all(isinstance(x["tables"], list) for x in ptr)
    assert {x["base"] for x in names.index} <= {"const", "ptr", "other"}
