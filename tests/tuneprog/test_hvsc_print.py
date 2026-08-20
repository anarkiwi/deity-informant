"""E11: the printed tuneprog of the two exemplars (marked ``hvsc``; short horizons).

Asserts the shape the plan's appendix A and anatomy 3.7.7 call for -- two rates,
the write-out folded over the voice index, one helper per role, 16-bit filter
views, no stack pointer, no goto -- and that S5/S6 left the S4 program identical.
"""

import json
import re

import pytest

from deity_informant import cli

from _hvsc import AUTOMATAS, COMMANDO, body, decompiled, folded, tune

pytestmark = pytest.mark.hvsc


def _temps(doc):
    """The distinct machine temporaries the program section still names."""
    return set(re.findall(r"\b[tnac]\d+\b", doc.split("## program")[1]))


def _fields(names, group="voice"):
    return {names.view[r][1] for r in names.groups.get(group, {}).get("members", ())}


def test_automatas_prints_the_shape_of_the_anatomy_player():
    run = decompiled(AUTOMATAS, seconds=30)
    text, names, v, calls = run.text, run.names, run.v, run.calls
    assert v.div is None and v.call == calls

    # two rates: the wrapper's call counter selects main or sub
    assert "call_counter" in text and "& 7" in text
    assert "main(" in text and "sub(" in text
    assert set(names.procs.values()) >= {"tick", "main", "sub", "row_apply", "init"}

    # the SID image write-out: >= 8 per-voice fields, all three voices
    image = {n for r, n in names.region.items() if names.role.get(r) == "sid_image"}
    assert image >= {"pw_lo", "pw_hi", "freq_lo", "freq_hi", "sr", "ad", "ctrl", "ctrl_eor"}
    assert len(_fields(names) & image) >= 7
    assert "sid.res_route = " in text and "sid.mode_vol = " in text

    # struct-of-code: the voice view is stride 49 and holds the cascade counters
    assert names.groups["voice"]["stride"] == 49 and names.groups["voice"]["n"] == 3
    assert "timer" in _fields(names) and any(f.startswith("ptr") for f in _fields(names))

    # the oscillator loop over X in {$62,$31,0} prints as a for over the voice index
    assert "for v in 2, 1, 0:" in text
    assert "voice[v].freq" in text and "FREQ" in text

    # the frequency table, the filter switch on the patched opcode, the raster wait
    assert "freq_table" in [names.role.get(r) for r in names.region]
    assert "switch " in text and "case " in text
    assert "while input($D012)" in text


def test_automatas_folds_the_write_out_and_names_one_helper_per_role():
    run = decompiled(AUTOMATAS, seconds=30)
    text = run.text
    helpers = ("writeout", "filter", "row_advance", "cascades", "oscillator")
    for name in helpers:
        assert text.count("\n%s():" % name) == 1, name

    # the write-out is one copy of the seven per-voice registers over the index
    out = body(text, "writeout")
    assert "    for v in 0, 1, 2:" in out
    assert len([l for l in out if "sid[v]." in l]) == 7
    assert not any("sid[0]." in l or "sid[1]." in l for l in out)

    # main and sub call the helpers instead of holding a copy each
    main, sub = body(text, "main"), body(text, "sub")
    assert [l.strip() for l in main if l.strip().endswith("()")] == [
        "writeout()",
        "filter()",
        "row_advance()",
        "cascades()",
        "oscillator()",
    ]
    assert "    cascades()" in sub and "    oscillator()" in sub
    assert len(main) < 20 and len(sub) < 12


def test_automatas_cascade_blocks_fold_over_the_voice_index():
    # the cascade is five copies of one block over per-copy cells; the fold makes
    # them one body under the copy index, in each of the two procedures holding it
    text, names, _view, prog = folded(AUTOMATAS, seconds=30)
    doc = prog.meta["copies"]
    fams = [f for f in doc["families"] if f["copies"] == 5]
    assert len(fams) == 2 and {f["rows"] for f in fams} == {18}
    assert names.copies["unverified"] < names.copies["statements"]
    hit = [
        b
        for b in (body(text, n) for n in names.procs.values())
        if b and any("copies_12BE[" in l for l in b)
    ]
    assert len(hit) == 1, text  # both procedures hold it; one helper carries it
    lines = "\n".join(hit[0])
    assert lines.count("while True") == 1 and "timer_4" in lines
    assert [f["why"] for f in doc["refused"]]  # and what the index cannot name


def test_automatas_has_no_machine_texture_left_in_the_hot_path():
    run = decompiled(AUTOMATAS, seconds=30)
    text, names = run.text, run.names

    # the stack pointer is not data here: the JSR frames and the PHA/PLA pair go
    assert not re.search(r"\bsp\d*\b", text)
    assert "saved = " in text and "= saved" in text

    # the filter accumulator is one 16-bit view stepped by one 16-bit operand
    acc = [n for n in names.u16.values() if n.endswith("acc")]
    assert acc and any(n.endswith("step") for n in names.u16.values())
    filt = "\n".join(body(text, "filter"))
    assert "%s += " % acc[0] in filt and "%s -= " % acc[0] in filt
    assert "carry(" not in filt and "carry(" not in "\n".join(body(text, "main"))
    assert "u16" in text.split("## program")[0]

    # the goto residue and the machine temporaries are gone (191 before S6's texture)
    assert "goto" not in text
    assert len(_temps(text)) <= 76, sorted(_temps(text))


def test_the_cli_subcommand_decompiles_commando_at_a_short_horizon(tmp_path):
    sid = tmp_path / "Commando.sid"
    sid.write_bytes(tune(COMMANDO))
    out = tmp_path / "out"
    assert cli.main(["tuneprog", str(sid), "--out", str(out), "--seconds", "5"]) == 0
    doc = (out / "tuneprog.md").read_text()
    assert doc.startswith("# tuneprog: Commando.sid")
    assert "## program" in doc and "tick(" in doc
    cert = json.loads((out / "certificate.json").read_text())
    assert cert["divergence"] is None and cert["stage"] == "S6"


def test_commando_prints_the_shape_of_the_design_illustration():
    run = decompiled(COMMANDO, seconds=20, song=0)
    text, names, v, calls = run.text, run.names, run.v, run.calls
    assert v.div is None and v.call == calls

    # the speed divider, exactly the design's S5 illustration
    assert "timer" in text and any(l.strip().endswith("-= 1") for l in text.splitlines())
    assert " < 0:" in text

    # the voice loop over X = 2..0 and the stride-1 per-voice fields
    assert "for v in 2, 1, 0:" in text
    assert names.groups["voice"]["stride"] == 1 and names.groups["voice"]["n"] == 3
    assert len(_fields(names)) >= 4
    assert "voice[v]." in text

    # the note table and the pointer-borne pattern stream
    assert "FREQ" in text and "ptr" in text
    assert "sid[" in text or "io[" in text

    # the machine texture goes here too: no stack pointer, and the speed divider
    assert not re.search(r"\bsp\d*\b", text)
    assert "timer_5 -= 1" in text or "timer -= 1" in text
