"""The pseudocode printer and the pipeline's print stage (hermetic snippets)."""

import json
import re

from deity_informant import cli
from deity_informant.tuneprog import live, pipeline, printer, pseudocode, recover, structure
from deity_informant.tuneprog.ir import Tuneprog

from _asm import asm, psid
from _prog import PLAY, counter, printed, proc_body, tuneprog


def _text(code, calls=6, pcs=True):
    _T, prog = tuneprog(code, calls=calls, s4=True)
    view = structure.view(prog, live.needed(prog)[0])
    st = structure.structure(view)
    return printer.render(view, st, recover.recover(view, st), pcs=pcs)


def test_the_document_has_the_five_sections_and_one_block_per_procedure():
    doc = _text(counter("LDA #$07", "STA $D400", "INC cnt"))
    for head in ("# tuneprog:", "## meta", "## state", "## const", "## inputs", "## program"):
        assert head in doc
    assert "entry     sub $" in doc and "calls/frame" in doc
    assert "tick():" in doc and "init():" in doc
    assert doc.count("```") % 2 == 0


def test_sid_stores_print_as_voice_registers_and_state_by_role():
    doc = _text(
        asm(
            PLAY,
            "init: LDA #$00",
            "STA img",
            "STA cnt",
            "RTS",
            "play: LDA img",
            "STA $D40B",
            "INC cnt",
            "RTS",
            "img: BRK",
            "cnt: BRK",
        )
    )
    assert "sid[1].ctrl = ctrl" in doc
    assert "counter += 1" in doc or "call_counter += 1" in doc


def test_a_counted_loop_prints_as_a_for_over_its_domain():
    doc = _text(counter("LDX #$02", "lp: TXA", "STA $D404", "DEX", "BPL lp"))
    assert "for v in 2, 1, 0:" in doc


def test_a_long_run_prints_as_a_range():
    doc = _text(counter("LDY #$17", "lp: LDA #$00", "STA $D400,Y", "DEY", "BPL lp"))
    assert "for v in 23..0:" in doc


def test_an_input_wait_collapses_to_a_while_over_the_input():
    code = asm(
        PLAY,
        "init: LDA #$00",
        "STA cnt",
        "wait: LDA $D012",
        "CMP #$0A",
        "BNE wait",
        "RTS",
        "play: INC cnt",
        "RTS",
        "cnt: BRK",
    )
    doc = _text(code, calls=2)
    assert "while input($D012)" in doc
    assert "$D012 raster" in doc


def test_a_delay_loop_keeps_its_body_instead_of_folding_into_a_wait():
    """A value the body defines from itself is a recurrence: it cannot be substituted."""
    code = asm(
        PLAY,
        "init: LDA #$05",
        "STA dly",
        "RTS",
        "play: LDA #$07",
        "STA $D400",
        "LDY dly",
        "lp: DEY",
        "BPL lp",
        "RTS",
        "dly: BRK",
    )
    doc = _text(code, calls=3)
    assert "while True:" in doc and "pass" not in doc, doc


def test_a_ghost_image_prints_as_the_registers_it_mirrors():
    from test_recover import ghost_tune  # pylint: disable=import-outside-toplevel

    doc = _text(ghost_tune("LDX #$07", "LDA cnt", "AND #$FE", "STA $1204,X"), calls=3)
    assert "sid.reg[v] = ghost.reg[v]" in doc  # the flush loop, register by register
    assert "ghost[1].ctrl = " in doc  # a write at a constant address, by its register
    assert "ghost " in doc and "sid_image" in doc and "flushed to $D400" in doc


def test_a_one_based_table_prints_the_index_the_tune_uses():
    code = asm(
        PLAY,
        "init: LDA #$01",
        "STA idx",
        "LDA #$00",
        "STA cell",
        "STA cell2",
        "RTS",
        "play: LDY idx",
        "LDA tab-1,Y",
        "STA cell",
        "LDA tab,Y",
        "STA cell2",
        "INC idx",
        "LDA cell",
        "STA $D400",
        "RTS",
        "idx: BRK",
        "cell: BRK",
        "cell2: BRK",
        "tab: BRK",
        "BRK",
        "BRK",
        "BRK",
        "BRK",
        "BRK",
    )
    doc, tab = _text(code, calls=3), "T%04X" % code.labels["tab"]
    idx = re.search(r"%s\[(\w+)\]" % tab, doc)
    assert idx, doc  # the table's own index, not the operand plus it
    assert "%s[1 + %s]" % (tab, idx.group(1)) in doc  # its look-ahead sibling
    assert "1-based, read at $%04X,i" % (code.labels["tab"] - 1) in doc


def test_the_pc_annotation_is_optional():
    code = counter("LDA #$07", "STA $D400", "INC cnt")
    assert _text(code).count("# $") > _text(code, pcs=False).count("# $")


def test_compound_assignment_and_the_signed_test_read_as_source():
    code = asm(
        PLAY,
        "init: LDA #$05",
        "STA spd",
        "STA ctr",
        "LDA #$00",
        "STA flag",
        "RTS",
        "play: LDA flag",
        "BNE out2",
        "DEC ctr",
        "BPL out",
        "LDA spd",
        "STA ctr",
        "out: LDA #$07",
        "out2: LDA #$07",
        "STA $D400",
        "RTS",
        "ctr: BRK",
        "spd: BRK",
        "flag: BRK",
    )
    doc = _text(code)
    assert "timer -= 1" in doc and "if timer < 0:" in doc


def test_a_folded_word_write_forgets_the_cells_it_overwrote():
    """The S6 fold leaves one ``W16`` where the two half stores were, which must
    invalidate the value memo of both halves: the low cell no longer holds what
    the store before the fold site put there."""
    code = asm(
        PLAY,
        "init: LDA #$00",
        "STA plo",
        "STA phi",
        "STA out",
        "LDA #$03",
        "STA tmp",
        "RTS",
        "play: LDA tmp",
        "ASL A",
        "STA plo",
        "LDA plo",
        "CLC",
        "ADC #$05",
        "STA plo",
        "LDA phi",
        "ADC #$01",
        "STA phi",
        "LDA tmp",
        "ASL A",
        "STA out",
        "RTS",
        "plo: BRK",
        "phi: BRK",
        "out: BRK",
        "tmp: BRK",
    )
    body = "\n".join(proc_body(printed(code), "tick"))
    assert "acc += $105" in body  # the pair folded: no store of its own remains
    assert body.count("(b1034 << 1)") == 2 and "= acc_lo" not in body


def test_a_call_forgets_the_cells_the_callee_may_overwrite():
    """The same memo, the other way a cell stops holding what was stored into it."""
    code = asm(
        PLAY,
        "init: LDA #$00",
        "STA cell",
        "STA out",
        "LDA #$03",
        "STA tmp",
        "RTS",
        "play: LDA tmp",
        "ASL A",
        "STA cell",
        "JSR sub",
        "LDA tmp",
        "ASL A",
        "STA out",
        "RTS",
        "sub: LDA #$09",
        "STA cell",
        "STA $D404",
        "RTS",
        "cell: BRK",
        "out: BRK",
        "tmp: BRK",
    )
    body = "\n".join(proc_body(printed(code), "tick"))
    assert "p_1020()" in body and body.count("(b102B << 1)") == 2


def _tune(tmp_path):
    code = asm(
        PLAY,
        "init: LDA #$00",
        "STA cnt",
        "STA img",
        "RTS",
        "play: LDX #$02",
        "lp: LDA img",
        "STA $D404",
        "DEX",
        "BPL lp",
        "INC cnt",
        "RTS",
        "img: BRK",
        "cnt: BRK",
    )
    p = tmp_path / "snippet.sid"
    p.write_bytes(psid({PLAY: code}, init=code.labels["init"], play=code.labels["play"]))
    return p


def test_the_pipeline_writes_every_artefact(tmp_path):
    out = tmp_path / "out"
    rc = pipeline.main([str(_tune(tmp_path)), "--out", str(out), "--calls", "8", "--prefix", "4"])
    assert rc == 0
    for name in ("tuneprog.S4.json", "tuneprog.py", "certificate.json", "tuneprog.md"):
        assert (out / name).exists(), name
    cert = json.loads((out / "certificate.json").read_text())
    assert cert["divergence"] is None and cert["stage"] == "S6"
    assert "annotate" in cert["presentation"]
    assert json.loads((out / "state.json").read_text())["stage"] == "done"
    s5 = json.loads((out / "tuneprog.S5.json").read_text())
    assert any(n["kind"] == "for" for n in _flat(s5["procs"]["tick"]))
    s6 = json.loads((out / "tuneprog.S6.json").read_text())
    assert any(r["role"] == "sid_image" for r in s6["regions"])
    assert "for v in 2, 1, 0:" in (out / "tuneprog.md").read_text()


def _flat(nodes):
    for n in nodes:
        yield n
        for f in ("then", "els", "body"):
            yield from _flat(n.get(f, []))


def test_the_cli_subcommand_runs_the_same_pipeline(tmp_path, capsys):
    out = tmp_path / "cli"
    rc = cli.main(["tuneprog", str(_tune(tmp_path)), "--out", str(out), "--calls", "6"])
    assert rc == 0
    assert "tick" in capsys.readouterr().out or (out / "tuneprog.md").exists()
    assert (out / "tuneprog.md").read_text().startswith("# tuneprog: snippet.sid")


def test_no_verify_and_no_text_skip_their_stages(tmp_path):
    out = tmp_path / "quiet"
    rc = cli.main(
        ["tuneprog", str(_tune(tmp_path)), "--out", str(out), "--calls", "4", "--no-verify"]
    )
    assert rc == 0 and not (out / "certificate.json").exists()
    assert (out / "tuneprog.md").exists()
    out2 = tmp_path / "quiet2"
    rc = cli.main(
        ["tuneprog", str(_tune(tmp_path)), "--out", str(out2), "--calls", "4", "--no-text"]
    )
    assert rc == 0 and not (out2 / "tuneprog.md").exists()


def test_a_chunked_run_resumes_where_it_stopped(tmp_path):
    out = tmp_path / "chunk"
    sid = str(_tune(tmp_path))
    rc = pipeline.main([sid, "--out", str(out), "--calls", "64", "--budget", "0", "--chunk", "8"])
    assert rc == pipeline.MORE
    for _ in range(40):
        rc = pipeline.main([sid, "--out", str(out), "--calls", "64", "--resume", "--budget", "0"])
        if rc != pipeline.MORE:
            break
    assert rc == 0
    assert json.loads((out / "certificate.json").read_text())["subtunes"][0]["ticks"] == 64


def test_the_printed_program_names_every_procedure_it_prints():
    _T, prog = tuneprog(
        asm(
            PLAY,
            "init: LDA #$00",
            "STA cnt",
            "RTS",
            "play: JSR work",
            "INC cnt",
            "RTS",
            "work: LDA #$07",
            "STA $D400",
            "RTS",
            "cnt: BRK",
        ),
        calls=3,
        s4=True,
    )
    view = structure.view(prog, live.needed(prog)[0])
    st = structure.structure(view)
    names = recover.recover(view, st)
    doc = printer.render(view, st, names)
    for name in view.procs:
        assert "%s(" % names.procs[name] in doc


def test_rendering_needs_no_certificate():
    doc = _text(counter("LDA #$07", "STA $D400"))
    assert "certified" not in doc


def test_an_unknown_region_prints_as_raw_memory():
    _T, prog = tuneprog(counter("LDA #$07", "STA $D400"), calls=2, s4=True)
    p = pseudocode.Printer(prog, recover.recover(prog))
    assert p.cell(-99, 0x1234) == "mem[$1234]"


def _transposed(extra=(), pre=()):
    """A block one init loop made, walked ``base + n*3 + v`` by the voice index."""
    code = asm(
        PLAY,
        "init: LDX #$0B",
        "clr: LDA #$00",
        "STA blk,X",
        "DEX",
        "BPL clr",
        *pre,
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
        "LDA blk+6,X",
        "STA $D400",
        "DEX",
        "BPL loop",
        *extra,
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
    )
    data = {code.labels["blk"] + i: 0 for i in range(12)}
    return printed(code, calls=6 + 3 * bool(extra), data=data)


def test_a_block_walked_field_outside_element_inside_prints_as_a_record():
    doc = _transposed()
    assert re.search(r"^voice_2\[3\]  \$\w+ 12 bytes, stride 1, 3 fields$", doc, re.M), doc
    for f in ("f00", "f03", "f06"):
        assert "voice_2[v].%s" % f in doc, doc
    tick = "\n".join(proc_body(doc, "tick"))
    assert not re.search(r"b\w{4}\[", tick), tick  # no flat address left in the tick
    init = "\n".join(proc_body(doc, "init"))
    assert re.search(r"b\w{4}\[v\] = 0", init), init  # the clear loop reaches every field


def test_a_field_only_one_constant_access_names_is_listed_with_the_others():
    """The play phase decides the layout; any access inside a field still prints as one."""
    doc = _transposed(pre=("LDA #$05", "STA blk+9"))
    assert re.search(r"^voice_2\[3\]  \$\w+ 12 bytes, stride 1, 4 fields$", doc, re.M), doc
    assert "  .f09 " in doc and "voice_2[0].f09 = 5" in doc, doc


def test_a_block_something_reads_as_a_table_is_not_that_record():
    """One index crossing the field boundary refuses the layout the others suggest."""
    doc = _transposed(("LDY cnt", "LDA blk,Y", "STA $D401"))
    assert "voice_2[" not in doc and "stride 1, 3 fields" not in doc
    assert re.search(r"\w+\[v \+ 3\] = ", doc), doc  # the block keeps its flat address


def _offset_table(vals):
    """A tune whose SID register offset comes from a per-track table."""
    code = asm(
        PLAY,
        "init: LDA #$00",
        "STA cnt",
        "RTS",
        "play: LDX cnt",
        "LDY vmap,X",
        "LDA #$0A",
        "STA $D405,Y",
        "INC cnt",
        "LDA cnt",
        "CMP #$03",
        "BNE out",
        "LDA #$00",
        "STA cnt",
        "out: RTS",
        "cnt: BRK",
        "vmap: BRK",
    )
    return printed(code, calls=6, data={code.labels["vmap"] + i: v for i, v in enumerate(vals)})


def test_an_offset_table_of_the_voice_strides_prints_the_register_by_voice():
    assert re.search(r"sid\[\w+\]\.ad = \$A", _offset_table((0, 7, 14)))


def test_an_offset_table_that_is_not_the_voice_strides_keeps_its_read():
    doc = _offset_table((0, 7, 13))
    assert "sid.reg[5 + " in doc and ".ad = " not in doc


def test_the_v_flag_of_a_subtract_prints_as_one_overflow_test():
    doc = printed(
        asm(
            PLAY,
            "init: LDA #$00",
            "STA cnt",
            "STA lim",
            "RTS",
            "play: LDA cnt",
            "SEC",
            "SBC lim",
            "BVC out",
            "LDA #$07",
            "STA $D404",
            "out: INC cnt",
            "LDA cnt",
            "EOR #$FF",
            "STA lim",
            "RTS",
            "cnt: BRK",
            "lim: BRK",
        ),
        calls=6,
    )
    assert re.search(r"overflow\(\w+ - \w+\)", doc), doc  # the branch, taken or marked
    assert " & (" not in doc  # none of the flag plumbing is left


def test_a_byte_exclusive_ored_with_its_own_mask_prints_as_a_complement():
    _T, prog = tuneprog(counter("LDA #$07", "STA $D400"), calls=2, s4=True)
    p = pseudocode.Printer(prog, recover.recover(prog))
    byte = pseudocode.Load("ram", pseudocode.Const(0x1934, 2), 1, r=-99)
    word = pseudocode.Load("ram", pseudocode.Const(0x1934, 2), 2, r=-99)
    assert p.expr(pseudocode.Bin("^", byte, pseudocode.Const(0xFF), 1)) == "~mem[$1934]"
    assert p.expr(pseudocode.Bin("^", byte, pseudocode.Const(0x7F), 1)) == "(mem[$1934] ^ $7F)"
    assert p.expr(pseudocode.Bin("^", word, pseudocode.Const(0xFF), 1)) == "(mem[$1934] ^ $FF)"


def test_a_sign_extended_byte_prints_as_sext():
    _T, prog = tuneprog(counter("LDA #$07", "STA $D400"), calls=2, s4=True)
    p = pseudocode.Printer(prog, recover.recover(prog))
    t = pseudocode.Load("ram", pseudocode.Const(0x1934, 2), 1, r=-99)
    e = pseudocode.Bin(
        "-",
        pseudocode.Bin("+", pseudocode.Const(0x1953, 2), t, 2),
        pseudocode.Bin("<<", pseudocode.Bin("&", t, pseudocode.Const(0x80)), pseudocode.Const(1)),
        2,
    )
    assert p.expr(e) == "($1953 + sext(mem[$1934]))"  # the shape a patched branch adds


# ---- the coverage marks and the counted loop's own header --------------------
UNTAKEN = asm(
    PLAY,
    "init: LDA #$00",
    "STA cnt",
    "RTS",
    "play: INC cnt",
    "LDA cnt",
    "CMP #$F0",
    "BCC over",
    "LDA #$07",
    "STA $D404",
    "over: LDA #$0F",
    "STA $D418",
    "RTS",
    "cnt: BRK",
)


def test_a_branch_direction_the_trace_never_took_is_a_mark_not_an_arm():
    doc = printed(UNTAKEN)
    assert "trap 'untaken'" not in doc, doc
    assert re.search(r"# untaken: \(?\w+ >= \$F0", doc), doc
    assert re.search(r"^untaken   1 branch direction", doc, re.M), doc


def test_a_branch_the_trace_took_both_ways_keeps_its_arm():
    code = asm(
        PLAY,
        "init: LDA #$00",
        "STA cnt",
        "RTS",
        "play: INC cnt",
        "LDA cnt",
        "AND #$01",
        "BEQ over",
        "LDA #$07",
        "STA $D404",
        "over: LDA #$0F",
        "STA $D418",
        "RTS",
        "cnt: BRK",
    )
    doc = printed(code)
    assert "# untaken" not in doc, doc
    assert re.search(r"^\s+if .*:", doc, re.M), doc


def test_a_counted_loop_does_not_also_print_where_its_index_starts():
    doc = printed(counter("LDX #$03", "lp: STX $D400", "DEX", "BNE lp"))
    assert "for v in 3, 2, 1:" in doc, doc
    assert not re.search(r"^\s+x\d* = 3$", doc, re.M), doc
