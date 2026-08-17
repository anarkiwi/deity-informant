"""GoatTracker 2 on two Linus tunes (marked ``hvsc``; short horizons).

What anatomy 3.3 and ``docs/prototype-goattracker.md`` say the generic pipeline
must recover: the ghost image and its flush loop, the patched low-byte dispatch,
the SMC immediates, the voice loop, 1-based tables, a goto-free ``execchn``.
"""

import os
import re
from pathlib import Path

import pytest

pytest.importorskip("pysidtracker")

from pysidtracker.testing import resolve_tune  # noqa: E402

from deity_informant.tuneprog import pipeline, printer  # noqa: E402
from deity_informant.tuneprog.ir import Bin, Const, Let, Load, Switch, Var  # noqa: E402
from deity_informant.tuneprog.machine import find_entries  # noqa: E402
from deity_informant.tuneprog.trace import Tracer  # noqa: E402
from deity_informant.tuneprog.verify import verify  # noqa: E402

pytestmark = pytest.mark.hvsc

_CACHE = Path(os.environ.get("DEITY_ORACLE_CACHE", ".oracle-cache")) / "hvsc"
PAL_CLOCK = 985248
LINUS = "MUSICIANS/L/Linus/Je_suis_Linus_le_salaud.sid"
DIA = "MUSICIANS/L/Linus/Do_It_Again.sid"
_DONE = {}


def _tune(relpath):
    path = resolve_tune(relpath, cache_dir=_CACHE)
    if path is None:
        pytest.skip("%s unavailable (no HVSC tree, no cache, offline)" % relpath)
    return Path(path).read_bytes()


def _run(relpath, seconds):
    """Trace, verify and print one tune: ``(text, names, prog, trace, regions, v, calls)``."""
    if (relpath, seconds) in _DONE:
        return _DONE[(relpath, seconds)]
    img, schedule = find_entries(_tune(relpath))
    entry = schedule[0]
    calls = int(seconds * PAL_CLOCK / entry.cycles_per_tick)
    tracer = Tracer(img, entry)
    tracer.run_init()
    tracer.run_calls(calls)
    trace = tracer.trace()
    prog, regions, _procs = pipeline.build(trace, Path(relpath).name)
    before = prog.to_json()
    v = verify(prog, trace, calls=calls, prefix=200)
    view, st, names = pipeline.present(prog)
    text = printer.render(view, st, names)
    assert prog.to_json() == before  # S5/S6 annotate; the certified program is untouched
    _DONE[(relpath, seconds)] = (text, names, prog, trace, regions, v, calls)
    return _DONE[(relpath, seconds)]


def _proc(doc, name):
    """The lines of one printed procedure."""
    out, on = [], False
    for line in doc.splitlines():
        if line.startswith("%s(" % name):
            on = True
            continue
        if on and (line.startswith("```") or (line and not line.startswith(" "))):
            break
        if on:
            out.append(line)
    return out


def _dispatch(prog):
    """Every ``switch`` over a patched jump cell: ``{cell address: {targets}}``."""
    out = {}
    for p in prog.procs.values():
        for b in p.blocks.values():
            if type(b.term) is not Switch:
                continue
            defs = {s.n: s.e for s in b.stmts if type(s) is Let}
            e = b.term.e
            e = defs.get(e.n, e) if type(e) is Var else e
            if type(e) is Load and type(e.a) is Const and e.w == 2:
                out.setdefault(e.a.v, set()).update(v for v, _l in b.term.cases)
    return out


def _loads(prog):
    """Every constant address the program loads a byte from."""
    out = set()

    def walk(e):
        if type(e) is Load:
            if type(e.a) is Const:
                out.update(range(e.a.v, e.a.v + e.w))
            walk(e.a)
        elif type(e) is Bin:
            walk(e.a)
            walk(e.b)

    for p in prog.procs.values():
        for b in p.blocks.values():
            for s in b.stmts:
                for e in (getattr(s, "e", None), getattr(s, "a", None), getattr(s, "v", None)):
                    if e is not None:
                        walk(e)
            if type(b.term) is Switch:
                walk(b.term.e)
    return out


def test_je_suis_linus_is_certified_and_flushes_a_ghost_image():
    text, names, prog, _T, _R, v, calls = _run(LINUS, seconds=30)
    assert v.div is None and v.call == calls

    # the 25-byte ghost block is the SID image; the flush loop is a copy loop
    ghost = [r for r, k in names.role.items() if k == "sid_image"]
    assert len(ghost) == 1 and names.region[ghost[0]] == "ghost"
    img = next(r for r in prog.storage if r.id == ghost[0])
    assert (img.base, img.size, names.image[ghost[0]]) == (0x14CA, 25, 0xD400 - 0x14CA)
    assert "for v in 24..0:" in text and "sid[v] = ghost[v]" in text
    assert "ghost[x/7].ctrl = " in text and "ghost[x/7].freq_lo" in text
    assert "ghost.mode_vol = " in text and "ghost.res_route = " in text


def test_je_suis_linus_dispatches_through_the_patched_low_bytes():
    text, _names, prog, trace, _R, _v, _calls = _run(LINUS, seconds=30)

    # the JSR/JMP operand cells are one-byte writes read as a 16-bit target
    cells = _dispatch(prog)
    assert set(cells) >= {0x1289, 0x1295, 0x131E}
    tick0 = cells[0x1289] | cells[0x1295]
    assert len(tick0) >= 8, sorted(map(hex, tick0))
    assert all(0x1000 <= t < 0x1100 for t in tick0 | cells[0x131E])
    assert {0x1289, 0x1295, 0x131E} <= trace.cells  # the low byte is the variable
    assert not {0x128A, 0x1296, 0x131F} & trace.cells  # the high byte is a constant

    # every arm is a target the trace took; the default traps
    sw = [b.term for p in prog.procs.values() for b in p.blocks.values() if type(b.term) is Switch]
    assert sw and all(t.default == "" for t in sw)
    assert "switch b1295:" in text and "case $1006:" in text


def test_je_suis_linus_keeps_its_smc_immediates_as_named_scalars():
    text, names, prog, trace, _R, _v, _calls = _run(LINUS, seconds=30)

    # the immediate cells of anatomy 3.3.1, each read by a load at its instruction
    imm = {0x110D, 0x1141, 0x1145, 0x118A, 0x118F, 0x1194, 0x10AC, 0x1096, 0x1310, 0x131A}
    assert imm <= trace.cells and imm <= _loads(prog)
    scalars = {r.base for r in prog.storage if r.kind == "state" and r.size == 1}
    assert len(imm & scalars) >= 8
    named = {names.region[r.id] for r in prog.storage if r.id in names.region and r.base in imm}
    assert len(named) >= 8 and all(named)
    assert "cursor_1141" in text  # the filter cursor, named by what it indexes


def test_je_suis_linus_prints_its_voice_loop_and_per_voice_records():
    text, names, _prog, _T, _R, _v, _calls = _run(LINUS, seconds=30)

    # JSR, JSR, then the third voice by falling into the routine: one loop
    assert re.search(r"for v in 0, 1, 2:\n\s+row_apply\(x=\(v \* 7\)\)", text), text
    assert text.count("row_apply(x=") == 1  # the three calls print once

    # X = voice*7 = record offset: the stride-7 blocks are one struct view
    assert names.groups["voice"]["stride"] == 7 and names.groups["voice"]["n"] == 3
    assert len(names.groups["voice"]["members"]) >= 10
    assert "voice[x/7]." in text


def test_je_suis_linus_recovers_the_base_of_its_one_based_tables():
    _text, _names, _prog, _T, regions, _v, _calls = _run(LINUS, seconds=30)
    by = {r.base: r for r in regions}

    # wavetbl is read at $16F8,Y with Y >= 1, so the table itself starts at $16F9
    assert 0x16F9 in by and by[0x16F9].origin < 0x16F9
    # the nine instrument columns: base-1+30k, every one of them 1-based
    cols = [by[b] for b in range(0x15EB, 0x15EB + 9 * 30, 30) if b in by]
    assert len(cols) >= 8 and all(r.origin == r.base - 1 for r in cols)
    assert all(r.kind == "const" for r in cols)


def test_je_suis_linus_structures_execchn_without_a_goto():
    text, _names, _prog, _T, _R, _v, _calls = _run(LINUS, seconds=30)
    body = _proc(text, "row_apply")
    assert body and "goto" not in text

    # the three-way DEC: tick 0, the continuing ticks, and the reload
    joined = "\n".join(body)
    assert "voice[x/7].timer_2 -= 1" in joined
    assert "if voice[x/7].timer_2 == 0:" in joined
    assert re.search(r"voice\[x/7\]\.timer_2 [<>]=? 0:", joined), joined
    assert re.search(r"voice\[x/7\]\.timer_2 = voice\[x/7\]\.\w+", joined), joined


def test_do_it_again_is_the_same_player_at_another_address():
    text, names, prog, trace, _R, v, calls = _run(DIA, seconds=20)
    assert v.div is None and v.call == calls

    # the same build at $AC00: ghost image, flush loop, voice loop, dispatch
    ghost = [r for r, k in names.role.items() if k == "sid_image"]
    assert len(ghost) == 1 and names.region[ghost[0]] == "ghost"
    img = next(r for r in prog.storage if r.id == ghost[0])
    assert img.size == 25 and names.image[ghost[0]] == 0xD400 - img.base
    assert "sid[v] = ghost[v]" in text and "for v in 24..0:" in text
    assert re.search(r"for v in 0, 1, 2:\n\s+\w+\(x=\(v \* 7\)\)", text), text
    assert names.groups["voice"]["stride"] == 7
    assert len(_dispatch(prog)) >= 3 and len(trace.cells) >= 10
    assert "goto" not in text
