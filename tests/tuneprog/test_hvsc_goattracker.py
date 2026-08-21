"""GoatTracker 2 on two Linus tunes (marked ``hvsc``; short horizons).

What anatomy 3.3 and ``docs/prototype-goattracker.md`` say the generic pipeline
must recover: the ghost image and its flush loop, the patched low-byte dispatch,
the SMC immediates, the voice loop, 1-based tables, a goto-free ``execchn``.
"""

import re

import pytest

from deity_informant.tuneprog import pipeline, printer

from _hvsc import DIA, LINUS, body, decompiled, load_addrs, switches, traced

pytestmark = pytest.mark.hvsc


def _dispatch(prog):
    """``{patched jump cell: the targets its switch names}``."""
    out = {}
    for addr, _w, term in switches(prog, width=2):
        out.setdefault(addr, set()).update(v for v, _l in term.cases)
    return out


def test_je_suis_linus_is_certified_and_flushes_a_ghost_image():
    run = decompiled(LINUS, seconds=30)
    text, names, prog, v, calls = run.text, run.names, run.prog, run.v, run.calls
    assert v.div is None and v.call == calls

    # the 25-byte ghost block is the SID image; the flush loop is a copy loop
    ghost = [r for r, k in names.role.items() if k == "sid_image"]
    assert len(ghost) == 1 and names.region[ghost[0]] == "ghost"
    img = next(r for r in prog.storage if r.id == ghost[0])
    assert (img.base, img.size, names.image[ghost[0]]) == (0x14CA, 25, 0xD400 - 0x14CA)
    assert "for v in 24..0:" in text and "sid.reg[v] = ghost.reg[v]" in text
    assert "ghost[x/7].ctrl = " in text and "ghost[x/7].freq_lo" in text
    assert "ghost.mode_vol = " in text and "ghost.res_route = " in text


def test_je_suis_linus_dispatches_through_the_patched_low_bytes():
    run = decompiled(LINUS, seconds=30)
    text, prog, trace = run.text, run.prog, run.trace

    # the JSR/JMP operand cells are one-byte writes read as a 16-bit target
    cells = _dispatch(prog)
    assert set(cells) >= {0x1289, 0x1295, 0x131E}
    tick0 = cells[0x1289] | cells[0x1295]
    assert len(tick0) >= 8, sorted(map(hex, tick0))
    assert all(0x1000 <= t < 0x1100 for t in tick0 | cells[0x131E])
    assert {0x1289, 0x1295, 0x131E} <= trace.cells  # the low byte is the variable
    assert not {0x128A, 0x1296, 0x131F} & trace.cells  # the high byte is a constant

    # the table's own entries are arms too: what the trace never dispatched traps
    sw = [t for _a, _w, t in switches(prog)]
    assert sw and all(t.default == "" for t in sw)
    assert len(cells[0x1289]) >= 12 and text.count("trap 'unverified'") >= 7
    assert "switch b1295:" in text and "case $1006:" in text


def test_je_suis_linus_keeps_its_smc_immediates_as_named_scalars():
    run = decompiled(LINUS, seconds=30)
    text, names, prog, trace = run.text, run.names, run.prog, run.trace

    # the immediate cells of anatomy 3.3.1, each read by a load at its instruction
    imm = {0x110D, 0x1141, 0x1145, 0x118A, 0x118F, 0x1194, 0x10AC, 0x1096, 0x1310, 0x131A}
    assert imm <= trace.cells and imm <= load_addrs(prog)
    scalars = {r.base for r in prog.storage if r.kind == "state" and r.size == 1}
    assert len(imm & scalars) >= 8
    named = {names.region[r.id] for r in prog.storage if r.id in names.region and r.base in imm}
    assert len(named) >= 8 and all(named)
    assert "cursor_1141" in text  # the filter cursor, named by what it indexes


def test_je_suis_linus_prints_its_voice_loop_and_per_voice_records():
    run = decompiled(LINUS, seconds=30)
    text, names = run.text, run.names

    # JSR, JSR, then the third voice by falling into the routine: one loop
    assert re.search(r"for v in 0, 1, 2:\n\s+row_apply\(x=\(v \* 7\)\)", text), text
    assert text.count("row_apply(x=") == 1  # the three calls print once

    # X = voice*7 = record offset: the stride-7 blocks are one struct view
    assert names.groups["voice"]["stride"] == 7 and names.groups["voice"]["n"] == 3
    assert len(names.groups["voice"]["members"]) >= 10
    assert "voice[x/7]." in text


def test_blocks_a_and_b_print_as_the_records_their_play_time_stride_names():
    # init clears $1461..$148A with one loop, so the access relation joins blocks
    # A and B into one region; the tick walks it at stride 7
    run = decompiled(LINUS, seconds=30)
    text, names = run.text, run.names
    split = [(r, v) for r, v in names.split.items() if v[1] == 7]
    assert len(split) == 1, names.split
    g, _stride, fields, flip = split[0][1]
    assert not flip  # the element index is outside: a record, not its transpose
    assert names.groups[g]["n"] == 6 and len(fields) >= 5
    assert re.search(r"%s\[x/7( \+ 3)?\]\.\w+" % g, text), text
    # init still clears the whole block with one loop, at stride 1
    assert "b1461[v] = 0" in text and not re.search(r"b1461\[\$?\w+ \+", text), text


def test_je_suis_linus_recovers_the_base_of_its_one_based_tables():
    run = decompiled(LINUS, seconds=30)
    regions = run.regions
    by = {r.base: r for r in regions}

    # wavetbl is read at $16F8,Y with Y >= 1, so the table itself starts at $16F9
    assert 0x16F9 in by and by[0x16F9].origin < 0x16F9
    # the nine instrument columns: base-1+30k, every one of them 1-based
    cols = [by[b] for b in range(0x15EB, 0x15EB + 9 * 30, 30) if b in by]
    assert len(cols) >= 8 and all(r.origin == r.base - 1 for r in cols)
    assert all(r.kind == "const" for r in cols)


def test_je_suis_linus_structures_execchn_without_a_goto():
    run = decompiled(LINUS, seconds=30)
    text = run.text
    lines = body(text, "row_apply")
    assert lines and "goto" not in text

    # the three-way DEC: tick 0, the continuing ticks, and the reload
    joined = "\n".join(lines)
    assert "voice[x/7].timer_2 -= 1" in joined
    assert "if voice[x/7].timer_2 == 0:" in joined
    assert re.search(r"voice\[x/7\]\.timer_2 [<>]=? 0:", joined), joined
    assert re.search(r"voice\[x/7\]\.timer_2 = voice\[x/7\]\.\w+", joined), joined


def test_do_it_again_is_the_same_player_at_another_address():
    run = decompiled(DIA, seconds=20)
    text, names, prog, trace, v, calls = run.text, run.names, run.prog, run.trace, run.v, run.calls
    assert v.div is None and v.call == calls

    # the same build at $AC00: ghost image, flush loop, voice loop, dispatch
    ghost = [r for r, k in names.role.items() if k == "sid_image"]
    assert len(ghost) == 1 and names.region[ghost[0]] == "ghost"
    img = next(r for r in prog.storage if r.id == ghost[0])
    assert img.size == 25 and names.image[ghost[0]] == 0xD400 - img.base
    assert "sid.reg[v] = ghost.reg[v]" in text and "for v in 24..0:" in text
    assert re.search(r"for v in 0, 1, 2:\n\s+\w+\(x=\(v \* 7\)\)", text), text
    assert names.groups["voice"]["stride"] == 7
    assert len(_dispatch(prog)) >= 3 and len(trace.cells) >= 10
    assert "goto" not in text


def test_the_filter_cursor_keeps_its_role_under_the_static_closure():
    """The closure splits the block the load sat in; a role is the value's, not a block's."""
    _entry, trace = traced(LINUS, 30)
    prog = pipeline.build(trace, "Je_suis_Linus_le_salaud.sid", static=True)[0]
    view, st, names = pipeline.present(prog)
    assert "cursor_1141" in printer.render(view, st, names)
