"""SID Wizard (Hermit, anatomy 3.4) end to end on two Hermit tunes (``hvsc``).

Emomyst (SW 1.6) at 30 s and End of the World (SW 1.9) at 20 s: the init-time
relocation of table operands, the runtime blob base, the patched immediates, the
three dispatchers, the voice loop, the slowdown gate and the zero-page save.
"""

import os
import re
from pathlib import Path

import pytest

pytest.importorskip("pysidtracker")

from pysidtracker.testing import resolve_tune  # noqa: E402

from deity_informant.tuneprog import pipeline, printer, ssa  # noqa: E402
from deity_informant.tuneprog.ir import Bin, Const, Let, Load, Store, Switch, Var  # noqa: E402
from deity_informant.tuneprog.machine import find_entries  # noqa: E402
from deity_informant.tuneprog.trace import Tracer  # noqa: E402
from deity_informant.tuneprog.verify import verify  # noqa: E402

pytestmark = pytest.mark.hvsc

_CACHE = Path(os.environ.get("DEITY_ORACLE_CACHE", ".oracle-cache")) / "hvsc"
PAL_CLOCK = 985248
EMOMYST = "MUSICIANS/H/Hermit/Emomyst.sid"
EOTW = "MUSICIANS/H/Hermit/End_of_the_World.sid"
INDEXJ1, INDEXJ2, INDEXJP = 0x1952, 0x1A14, 0x19D0  # the three patched operands
_DONE = {}


def _tune(relpath):
    path = resolve_tune(relpath, cache_dir=_CACHE)
    if path is None:
        pytest.skip("%s unavailable (no HVSC tree, no cache, offline)" % relpath)
    return Path(path).read_bytes()


def _run(relpath, seconds):
    """Trace, verify and print one tune: ``(text, names, prog, trace, v, calls)``."""
    if (relpath, seconds) in _DONE:
        return _DONE[(relpath, seconds)]
    img, schedule = find_entries(_tune(relpath))
    entry = schedule[0]
    calls = int(seconds * PAL_CLOCK / entry.cycles_per_tick)
    tracer = Tracer(img, entry)
    tracer.run_init()
    tracer.run_calls(calls)
    trace = tracer.trace()
    prog, _regions, _procs = pipeline.build(trace, Path(relpath).name)
    before = prog.to_json()
    v = verify(prog, trace, calls=calls, prefix=200)
    view, st, names = pipeline.present(prog)
    text = printer.render(view, st, names)
    assert prog.to_json() == before  # S5/S6 annotate; the certified program is untouched
    _DONE[(relpath, seconds)] = (text, names, prog, trace, v, calls)
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


def _exprs(s):
    parts = (getattr(s, "e", None), getattr(s, "a", None), getattr(s, "v", None))
    parts += (getattr(s, "c", None),) + tuple(getattr(s, "args", ()))
    return [x for x in parts if x is not None]


def _walk(e):
    yield e
    if type(e) is Bin:
        yield from _walk(e.a)
        yield from _walk(e.b)
    elif type(e) is Load:
        yield from _walk(e.a)


def _loads(prog, names):
    """Every constant address the given procedures load a byte from."""
    out = set()
    for n in names:
        for b in prog.procs[n].blocks.values():
            for s in list(b.stmts) + [b.term]:
                for e in _exprs(s):
                    for x in _walk(e):
                        if type(x) is Load and type(x.a) is Const:
                            out.update(range(x.a.v, x.a.v + x.w))
    return out


def _switches(prog):
    """``{cell address: (width, arms)}`` for every computed-target switch."""
    out = {}
    for p in prog.procs.values():
        defs = {s.n: s.e for b in p.blocks.values() for s in b.stmts if type(s) is Let}
        for b in p.blocks.values():
            if type(b.term) is not Switch:
                continue
            for x in _walk(b.term.e):
                x = defs.get(x.n, x) if type(x) is Var else x
                if type(x) is Load and type(x.a) is Const:
                    out[x.a.v] = (x.w, len(b.term.cases))
    return out


def _tick_procs(prog):
    return set(prog.procs) - ssa.init_reachable(prog)


def test_emomyst_is_certified_over_thirty_seconds():
    _text, _names, _prog, trace, v, calls = _run(EMOMYST, seconds=30)
    assert v.div is None and v.call == calls
    # the slowdown gate: the first play call returns before playing (anatomy 3.4.2)
    assert int((trace.wlog["call"] == 0).sum()) == 0
    assert int((trace.wlog["call"] == 1).sum()) > 0


def test_emomyst_folds_every_relocated_table_operand_outside_init():
    _text, _names, prog, trace, _v, _calls = _run(EMOMYST, seconds=30)
    init_only = trace.cells - trace.written_play
    assert len(init_only) >= 30  # 30 operands, most of them two bytes

    # every instruction whose operand init relocated, and which the tick runs
    reloc = [k for k in trace.sites if {k[0] + 1, k[0] + 2} & init_only]
    assert len([k for k in reloc if trace.sites[k]["phases"] & 2]) >= 25

    # constants in the tick; inside init the loads its own stores define (design S2)
    tick = _tick_procs(prog)
    assert not _loads(prog, tick) & init_only
    assert len(_loads(prog, ssa.init_reachable(prog)) & init_only) >= 8


def test_emomyst_prints_its_relocation_as_a_loop_over_the_pointer_tables():
    text, _names, prog, _trace, _v, _calls = _run(EMOMYST, seconds=30)
    body = "\n".join(_proc(text, "init"))
    assert "while True:" in body  # the DataPtr/PtrValu loop, not 30 unrolled stores
    assert re.search(r"ptr_\d\[\(\(ptr\[1\] << 8\) \| ptr\) \+ [12]\] = ", body), body
    stores = [
        s
        for b in prog.procs["init"].blocks.values()
        for s in b.stmts
        if type(s) is Store and type(s.a) is not Const and s.cls != "raw"
    ]
    assert 2 <= len(stores) <= 8  # the loop body's stores, once each


def test_emomyst_adds_the_blob_base_to_every_pointer_it_sets():
    text, names, _prog, _trace, _v, _calls = _run(EMOMYST, seconds=30)
    # LDA lo,Y; CLC; ADC SWP_OFFSET; STA zp; LDA hi,Y; ADC SWP_OFFSET+1; STA zp+1
    assert len(re.findall(r"ptr = \(T[0-9A-F]{4}\[.+\] \+ base\)", text)) >= 4, text
    assert "base" in names.u16.values() and "ptr" in names.u16.values()
    assert len([n for n in names.u16.values() if n.startswith("T")]) >= 2


def test_emomyst_keeps_its_patched_immediates_as_named_globals():
    text, names, prog, trace, _v, _calls = _run(EMOMYST, seconds=30)
    play = trace.cells & trace.written_play
    assert len(play) >= 20  # anatomy 3.4.1: 27 play-time immediates
    assert len(play & _loads(prog, _tick_procs(prog))) >= 20

    # the ones a role reaches are named by it (MAINVOL, FLTBAND, RESONIB, FSWITCH)
    named = {names.region.get(r.id) for r in prog.storage if r.base in play}
    assert {"res_route", "mode_vol", "cutoff_hi", "cutoff_lo"} <= named
    assert "sid.mode_vol = ($F | mode_vol)" in text
    assert re.search(r"\btimer \+= 1", text), text  # CWEPCNT, an `INC $15DD` in place


def test_emomyst_dispatches_through_two_branch_tables_and_one_jump_table():
    text, _names, prog, trace, _v, _calls = _run(EMOMYST, seconds=30)
    sw = _switches(prog)
    assert {INDEXJ1, INDEXJ2, INDEXJP} <= set(sw)
    assert sw[INDEXJ1] == (1, 7)  # NOTEFXTBL: 8 entries, 7 distinct targets
    assert sw[INDEXJ2] == (1, 14)  # SMALLFXTBL: 14 branch offsets
    assert sw[INDEXJP] == (2, 25)  # BIGFXTABLE: 31 words, 25 distinct targets
    assert {INDEXJ1, INDEXJ2} <= trace.cells and INDEXJP in trace.cells

    # a patched offset on an always-taken branch is a switch over site + 2 + offset
    assert re.search(r"switch \(\(\$1\w{3} \+ [^)]+\) - \(\(", text), text
    assert "switch b19D0:" in text and text.count("trap 'unverified'") >= 25


def test_emomyst_prints_one_dotrack_over_the_three_voices():
    text, _names, _prog, _trace, _v, _calls = _run(EMOMYST, seconds=30)
    # LDX #14; JSR DOTRACK; LDX #7; JSR; LDX #0; JSR
    loop = r"for v in 0, 1, 2:\n(\s+# \$\w+\n)?\s+row_apply\(x=\(\$E - \(v \* 7\)\)\)"
    assert re.search(loop, text), text
    assert text.count("row_apply(x=") == 1
    body = "\n".join(_proc(text, "row_apply"))

    # the tempo test: SEC; SBC TEMPOTBL-1,Y; BEQ new row; BVC same row (the V flag)
    assert re.search(r"if \(\(b1024\[\$16 \+ x\] \^ \w+\) & \(", body), body
    assert "b1024[$16 + x] += 1" in body  # SPDCNT is post-incremented
    assert body.count("if t4 == 0:") == 1 and "if t4 == 2:" in body


def test_emomyst_passes_the_tick_number_to_the_hard_restart_as_a_bit_mask():
    text, _names, _prog, _trace, _v, _calls = _run(EMOMYST, seconds=30)
    # HARDRST: A = 2 at tick 0, 1 at tick 1, ANDed with the instrument control byte
    assert re.search(r"\bsaved\d* = a\n(.*\n)?\s+if \(saved\d* & T\w+\[", text), text
    assert len(re.findall(r"a\d+ = 2\n\s+# \$1310", text)) == 1, text
    assert len(re.findall(r"a\d+ = 1\n\s+# \$1310", text)) == 1, text


def test_end_of_the_world_is_the_same_player_two_versions_on():
    text, _names, _prog, _trace, v, calls = _run(EOTW, seconds=20)
    assert v.div is None and v.call == calls

    # ZEROPAGESAVE: the pushes around play and init, and no stack pointer left
    assert not re.search(r"\bsp\d*\b", text), text
    body = _proc(text, "tick")
    assert re.search(r"saved\d* = ptr", "\n".join(body[:3])), body[:3]
    assert re.search(r"ptr = saved\d*", "\n".join(body[-3:])), body[-3:]
    assert re.search(r"for v in 0, 1, 2:\n\s+row_apply\(x=\(\$E - \(v \* 7\)\)\)", text)


def test_end_of_the_world_carries_the_subtune_and_the_1_9_write_order():
    text, names, prog, trace, _v, _calls = _run(EOTW, seconds=20)
    # init saves the subtune in an immediate, SETSTUNE patches the orderlist reads
    init = "\n".join(_proc(text, "init"))
    assert re.search(r"b2A05 = (a\b|0)", init), init
    assert "cursor_302E = (a << 3)" in text  # SUBTUNES[sub * 8]
    assert len(re.findall(r"p_30(1B|24)\(", text)) >= 3
    assert {0x2A05, 0x302E, 0x3045} <= trace.cells  # SWPdone, SUBTPOS, XSTORE

    # 1.9 writes SR before AD (1.6 the other way) and reads FREQTBH by pitch
    assert re.search(r"sid\[x/7\]\.sr = .*\n\s+sid\[x/7\]\.ad = ", text), text
    assert re.search(r"sid\[x/7\]\.freq_hi = FREQ_HI\[", text), text

    # MULPLY is assembled in but never called, so no procedure starts there
    assert not any(p.blocks[p.entry].src == 0x2AA2 for p in prog.procs.values())
    assert len(names.u16) >= 4  # the pointer, the blob base, two word tables
