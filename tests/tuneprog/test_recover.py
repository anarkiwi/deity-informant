"""S6 recovery: roles, struct views, tables and names from the IR alone."""

from deity_informant.tuneprog import recover as R
from deity_informant.tuneprog import structure as S

from _asm import asm
from _prog import PLAY, counter, tuneprog


def _snip(play, cells=(), calls=6):
    """A tune whose init writes ``cells`` (so they are storage, not folded constants)."""
    init = ["init: LDA #$00", "STA cnt"] + ["STA %s" % c for c in cells] + ["RTS"]
    data = ["cnt: BRK"] + ["%s: BRK" % c for c in cells]
    return _names(asm(PLAY, *init, "play:", *play, "RTS", *data), calls=calls)


def _names(code, calls=6, **kw):
    _T, prog = tuneprog(code, calls=calls, s4=True, **kw)
    view = S.view(prog)
    return view, R.recover(view, S.structure(view))


def _role(names, role):
    return {names.region[r] for r, k in names.role.items() if k == role}


def test_a_cell_whose_value_reaches_a_voice_register_is_that_register_s_image():
    _view, names = _snip(
        ["LDA img", "STA $D404", "LDX img2", "STX $D405", "LDA img3", "EOR img4", "STA $D40B"],
        cells=("img", "img2", "img3", "img4"),
    )
    assert _role(names, "sid_image") >= {"ctrl", "ad", "ctrl_2", "ctrl_eor"}


def test_the_global_registers_keep_their_own_names():
    _view, names = _snip(["LDA img", "STA $D418", "LDA img2", "STA $D416"], cells=("img", "img2"))
    assert _role(names, "sid_image") == {"mode_vol", "cutoff_hi"}


def test_a_decrement_with_a_reload_is_a_timer_and_an_increment_is_a_counter():
    _view, names = _snip(
        [
            "LDA flag",
            "BNE out2",
            "DEC tmr",
            "BPL out",
            "LDA #$04",
            "STA tmr",
            "out: INC cnt",
            "out2: LDA #$07",
            "STA $D400",
        ],
        cells=("tmr", "flag"),
    )
    assert _role(names, "timer") == {"timer"} and _role(names, "counter") == {"counter"}


def test_a_zero_page_pair_read_through_is_a_pointer_and_the_stream_stays_apart():
    code = counter(
        "LDA #<tab",
        "STA $FB",
        "LDA #>tab",
        "STA $FC",
        "LDY cnt",
        "LDA ($FB),Y",
        "STA $D400",
        "INC cnt",
        "RTS",
        "tab: BRK",
        "BRK",
        "BRK",
        "BRK",
    )
    view, names = _names(code)
    ptrs = [r for r, k in names.role.items() if k == "ptr"]
    assert ptrs and all(next(x for x in view.storage if x.id == r).base == 0xFB for r in ptrs)


def test_a_value_that_indexes_a_table_is_a_cursor_named_after_it():
    code = counter(
        "LDY idx",
        "LDA tab,Y",
        "STA $D400",
        "INC idx",
        "LDA idx",
        "AND #$03",
        "STA idx",
        "RTS",
        "idx: BRK",
        "tab: BRK",
        "BRK",
        "BRK",
        "BRK",
    )
    _view, names = _names(code)
    assert names.region
    cursors = _role(names, "cursor")
    assert cursors and all(n.endswith("_idx") for n in cursors)


def test_regions_of_one_stride_and_three_elements_are_a_voice_view():
    code = asm(
        PLAY,
        "init: LDX #$02",
        "il: LDA #$00",
        "STA lo,X",
        "STA hi,X",
        "DEX",
        "BPL il",
        "RTS",
        "play: LDX #$02",
        "lp: LDA lo,X",
        "STA $D400",
        "LDA hi,X",
        "STA $D401",
        "INC lo,X",
        "DEX",
        "BPL lp",
        "RTS",
        "lo: BRK",
        "BRK",
        "BRK",
        "hi: BRK",
        "BRK",
        "BRK",
    )
    _view, names = _names(code)
    assert names.groups["voice"]["n"] == 3 and names.groups["voice"]["stride"] == 1
    assert {names.view[r][1] for r in names.groups["voice"]["members"]} >= {"freq_lo", "freq_hi"}


def test_the_note_table_is_found_by_its_octave_ramp():
    tab = []
    f = 0x0117
    for _ in range(96):
        tab.append(min(int(f), 0xFFFF))
        f *= 2 ** (1 / 12)
    lo = bytes(v & 0xFF for v in tab)
    hi = bytes(v >> 8 for v in tab)
    assert R._freq_layout(lo + hi) == ("lo|hi", 96, 0)
    assert R._freq_layout(hi + lo) == ("hi|lo", 96, 0)
    inter = bytes(b for v in tab for b in (v & 0xFF, v >> 8))
    assert R._freq_layout(inter) == ("u16le", 96, 0)
    assert R._freq_layout(bytes(range(256))) is None


def test_names_are_unique_stable_and_serialisable():
    code = counter("LDA img", "STA $D404", "LDA img2", "STA $D40B", "RTS", "img: BRK", "img2: BRK")
    view, names = _names(code)
    again = R.recover(view, S.structure(view))
    assert names.region == again.region
    assert len(set(names.region.values())) == len(names.region)
    doc = names.to_dict()
    assert doc["regions"] and set(doc) == {
        "regions",
        "groups",
        "image",
        "procs",
        "phase",
        "u16",
        "copies",
    }
    assert names.of(-99) == "r-99"


def test_a_procedure_that_decodes_a_record_through_a_pointer_is_row_apply():
    code = counter(
        "LDA #<row",
        "STA $FB",
        "LDA #>row",
        "STA $FC",
        "JSR apply",
        "RTS",
        "apply: LDY #$00",
        "LDA ($FB),Y",
        "STA c1",
        "INY",
        "LDA ($FB),Y",
        "STA c2",
        "INY",
        "LDA ($FB),Y",
        "STA c3",
        "INY",
        "LDA ($FB),Y",
        "STA c4",
        "LDA c1",
        "STA $D400",
        "RTS",
        "c1: BRK",
        "c2: BRK",
        "c3: BRK",
        "c4: BRK",
        "row: BRK",
        "BRK",
        "BRK",
        "BRK",
    )
    _view, names = _names(code, calls=2)
    assert "row_apply" in names.procs.values()


GHOST = 0x1200


def ghost_tune(*play):
    """A tune with a 25-byte block a loop flushes into the SID, GoatTracker style."""
    return asm(
        PLAY,
        "init: LDA #$00",
        "STA cnt",
        "LDX #$18",
        "zl: STA $1200,X",
        "DEX",
        "BPL zl",
        "RTS",
        "play: LDX #$18",
        "fl: LDA $1200,X",
        "STA $D400,X",
        "DEX",
        "BPL fl",
        *play,
        "INC cnt",
        "RTS",
        "cnt: BRK",
    )


def test_a_loop_that_copies_a_block_into_the_registers_is_the_sid_image():
    view, names = _names(ghost_tune("LDA cnt", "AND #$FE", "STA $1204"), calls=3)
    rid = next(r for r, k in names.role.items() if k == "sid_image")
    assert next(x for x in view.storage if x.id == rid).base == GHOST
    assert names.region[rid] == "ghost" and names.image[rid] == 0xD400 - GHOST
    assert names.to_dict()["image"] == [{"region": rid, "delta": 0xD400 - GHOST}]


def test_a_block_the_sid_never_reads_wholesale_is_not_an_image():
    view, names = _names(
        asm(
            PLAY,
            "init: LDA #$00",
            "STA cnt",
            "LDX #$18",
            "zl: STA $1200,X",
            "DEX",
            "BPL zl",
            "RTS",
            "play: LDX #$18",
            "fl: LDA $1200,X",
            "STA $1300,X",  # a copy that is not the register file
            "DEX",
            "BPL fl",
            "INC cnt",
            "RTS",
            "cnt: BRK",
        ),
        calls=3,
    )
    assert not names.image and view.storage


def _vmap(vals, play=("LDA #$0A", "STA $D405,Y")):
    """A tune that reads its SID register offset from a three-entry table."""
    code = asm(
        PLAY,
        "init: LDA #$00",
        "STA cnt",
        "RTS",
        "play: LDX cnt",
        "LDY vmap,X",
        *play,
        "INC cnt",
        "LDA cnt",
        "CMP #$03",
        "BNE out",
        "LDA #$00",
        "STA cnt",
        "out: RTS",
        "cnt: BRK",
        "vmap: BRK",
        "BRK",
        "BRK",
        "tune: BRK",
    )
    data = {code.labels["vmap"] + i: v for i, v in enumerate(vals)}
    data.update({code.labels["tune"] + i: i for i in range(3)})
    return _names(code, calls=6, data=data)


def test_a_table_of_the_sid_voice_strides_is_the_voice_map():
    _view, names = _vmap((0, 7, 14))
    assert _role(names, "voice_map") == {"voice_map"} and names.voicemap


def test_a_table_that_is_not_the_voice_strides_is_not_the_voice_map():
    _view, names = _vmap((0, 7, 13))
    assert not names.voicemap and not _role(names, "voice_map")


def test_the_voice_strides_only_name_a_region_a_sid_access_is_indexed_by():
    """The bytes are one source; the role wants the SID access the printing wants."""
    _view, names = _vmap((0, 7, 14), play=("LDA #$0A", "STA $0400,Y"))
    assert names.voicemap and not _role(names, "voice_map")


def test_a_value_is_an_index_wherever_the_structuring_left_its_definition():
    """The load and the table read sit in different blocks; the role is the value's."""
    code = asm(
        PLAY,
        "init: LDA #$00",
        "STA cur",
        "STA cnt",
        "RTS",
        "play: LDX cur",
        "LDA cnt",
        "AND #$01",
        "BEQ out",  # the branch keeps the load and the table read in two blocks
        "LDA tab,X",
        "STA $D400",
        "out: INC cnt",
        "INC cur",
        "RTS",
        "cur: BRK",
        "cnt: BRK",
        "tab: BRK",
    )
    view, names = _names(code, calls=6)
    cur = next(r for r in view.storage if r.base == code.labels["cur"])
    assert names.role[cur.id] == "cursor"


def test_a_table_a_per_voice_store_reads_is_that_register_s_image():
    """A merged access indexes the register file; the store's own base still names it."""
    _view, names = _vmap((0, 7, 14), play=("LDA tune,X", "STA $D400,Y"))
    assert _role(names, "sid_image") == {"freq_lo"}
