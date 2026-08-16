"""The pseudocode printer and the pipeline's print stage (hermetic snippets)."""

import json

from deity_informant import cli
from deity_informant.tuneprog import pipeline, printer, recover, structure
from deity_informant.tuneprog.ir import Tuneprog

from _asm import asm, psid
from _prog import PLAY, counter, tuneprog


def _text(code, calls=6, pcs=True):
    _T, prog = tuneprog(code, calls=calls, s4=True)
    view = structure.view(prog, printer.needed(prog)[0])
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
    view = structure.view(prog, printer.needed(prog)[0])
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
    p = printer.Printer(prog, recover.recover(prog))
    assert p.cell(-99, 0x1234) == "mem[$1234]"
