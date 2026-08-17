"""Ghouls'n'Ghosts (Follin, anatomy 3.6) end to end, plus the SID Wizard regression.

One music subtune over 30 s of music, one sound-effect subtune to its state
fixpoint, and Emomyst (whose init patches ~30 operands through a relocation loop)
at 10 s. The full 32-subtune certificates are ``docs/certificates/ghouls-*.json``.
"""

import os
from pathlib import Path

import pytest

pytest.importorskip("pysidtracker")

from pysidtracker.testing import resolve_tune  # noqa: E402

from deity_informant.tuneprog import pipeline, printer, ssa  # noqa: E402
from deity_informant.tuneprog.ir import (  # noqa: E402
    Const,
    Let,
    Load,
    Store,
    Switch,
    Var,
    retval,
)
from deity_informant.tuneprog.machine import find_entries  # noqa: E402
from deity_informant.tuneprog.trace import Tracer  # noqa: E402
from deity_informant.tuneprog.verify import certify, verify  # noqa: E402

pytestmark = pytest.mark.hvsc

_CACHE = Path(os.environ.get("DEITY_ORACLE_CACHE", ".oracle-cache")) / "hvsc"
PAL_CLOCK = 985248
GNG = "MUSICIANS/F/Follin_Tim/Ghouls_n_Ghosts.sid"
EMOMYST = "MUSICIANS/H/Hermit/Emomyst.sid"
DISPATCH = (0x6375, 0x6562, 0x6751)  # the three voices' patched JMP operands
VOICE = (0x6234, 0x6421, 0x6610)  # the three copies of the 493-byte template
SMC = (0x62EE, 0x64DB, 0x66CA, 0x6269, 0x640F, 0x65FE, 0x67ED, 0x6800)


def _tune(relpath):
    path = resolve_tune(relpath, cache_dir=_CACHE)
    if path is None:
        pytest.skip("%s unavailable (no HVSC tree, no cache, offline)" % relpath)
    return Path(path).read_bytes()


def _run(relpath, seconds, song=None, until_period=False, prefix=500):
    """Trace, decompile, verify: ``(trace, prog, verifier, certificate)``."""
    img, schedule = find_entries(_tune(relpath))
    entry = schedule[0]
    calls = int(seconds * PAL_CLOCK / entry.cycles_per_tick)
    tr = Tracer(img, entry, song=song)
    tr.run_init()
    while tr.calls_done < calls and not (until_period and tr.period is not None):
        tr.run_calls(min(256, calls - tr.calls_done))
    trace = tr.trace()
    prog, _regions, _procs = pipeline.build(trace, Path(relpath).name)
    v = verify(prog, trace, calls=trace.meta["calls"], prefix=prefix)
    return trace, prog, v, certify(prog, v, prefix=prefix)


def _stmts(prog):
    return [(n, b, s) for n, p in prog.procs.items() for b in p.blocks.values() for s in b.stmts]


def _defs(proc):
    return {s.n: s.e for b in proc.blocks.values() for s in b.stmts if type(s) is Let}


def _switches(prog, cell):
    """Switches whose value is the 16-bit load of ``cell`` (a patched JMP operand)."""
    out = []
    for p in prog.procs.values():
        defs = _defs(p)
        for b in p.blocks.values():
            t = b.term
            if type(t) is not Switch:
                continue
            e = defs.get(t.e.n, t.e) if type(t.e) is Var else t.e
            if type(e) is Load and e.w == 2 and type(e.a) is Const and e.a.v == cell:
                out.append(t)
    return out


def _text(prog):
    view, st, names = pipeline.present(prog)
    return printer.render(view, st, names, None)


def test_ghouls_song_one_over_thirty_seconds():
    trace, prog, v, cert = _run(GNG, seconds=30, song=0)
    sub = cert["subtunes"][0]
    assert cert["divergence"] is None and v.div is None
    assert sub["divergences"] == 0 and sub["envelope_traps"] == 0 and sub["seconds"] > 29
    assert trace.meta["songs"] == 32 and cert["entry"]["cycles_per_tick"] == 19656

    # 1: the patched JMP of each voice is a switch over the handler table
    for cell in DISPATCH:
        sw = _switches(prog, cell)
        assert len(sw) == 1, "%04X" % cell
        assert len(sw[0].cases) >= 10, "%04X: %d arms" % (cell, len(sw[0].cases))

    # 2: `$85` writes SID registers the song names -- the address is data
    sidw = [s for _n, _b, s in _stmts(prog) if type(s) is Store and s.cls == "io"]
    computed = [s for s in sidw if type(s.a) is not Const]
    assert computed and all(0xD400 <= s.lo <= s.hi <= 0xD7FF for s in computed)

    # 5: the play routine returns A = $7B | $7C | $7D
    assert retval(prog.procs["tick"]) is not None

    # 7: SMC cells, play-written and init-patched
    assert len(trace.cells) >= 15
    assert set(SMC) | set(DISPATCH) <= trace.cells
    assert trace.cells & trace.written_play and trace.cells - trace.written_play
    tick = set(prog.procs) - ssa.init_reachable(prog)
    loads = {
        e.a.v
        for n, _b, s in _stmts(prog)
        if n in tick
        for e in (getattr(s, "e", None),)
        if type(e) is Load and type(e.a) is Const
    }
    assert not loads & (trace.cells - trace.written_play)  # init's cells folded

    # 8: init clears $D400-$D41C with $08 then $00 (four writes past $D418)
    clear = [(a, val) for a, val, _c in trace.init_writes if 0xD400 <= a <= 0xD41C]
    assert len(clear) == 2 * 0x1D
    assert {v for _a, v in clear} == {0x08, 0x00}
    assert clear[0][0] == 0xD41C and clear[-1][0] == 0xD400

    # 6: the three voice copies are not one `for v`, and this is why -- each copy
    # ran a different subset of the shared template, so their trace-closed
    # programs differ in size before any operand does.
    bounds = VOICE + (0x67FF,)
    reached = [len([k for k in trace.sites if bounds[i] <= k[0] < bounds[i + 1]]) for i in range(3)]
    assert min(reached) > 100 and len(set(reached)) > 1, reached

    doc = _text(prog)
    assert "sid.reg[" in doc  # 2: the data-dependent register prints as one
    assert "return (" in doc  # 5
    assert "for v in" in doc  # copy folding is on: the init clear and the $88 arm


def test_ghouls_sound_effect_subtune_is_complete():
    trace, prog, v, cert = _run(GNG, seconds=25, song=15, until_period=True, prefix=100)
    sub = cert["subtunes"][0]
    assert sub["divergences"] == 0 and sub["envelope_traps"] == 0
    assert sub["complete"] and sub["period"] == 1  # the effect ends and the state stops
    assert v.div is None

    # 3: SFX start patches `STA $6219`'s operand and stores through it into the
    # three voices' fixed-note-length cells
    through = []
    for _n, p in prog.procs.items():
        defs = _defs(p)
        for b in p.blocks.values():
            for s in b.stmts:
                if type(s) is Store and type(s.a) is Var:
                    e = defs.get(s.a.n)
                    if type(e) is Load and type(e.a) is Const and e.a.v == 0x6219:
                        through.append(s)
    assert through, "no store through load16($6219)"
    assert all(s.lo <= 0x640F and s.hi >= 0x67ED for s in through)
    assert 0x6219 in trace.cells


def test_sid_wizard_still_certifies_after_the_init_cell_rule():
    trace, prog, v, cert = _run(EMOMYST, seconds=10)
    assert cert["divergence"] is None and v.div is None
    assert cert["subtunes"][0]["divergences"] == 0
    # init relocates the player, so most of its cells are init-only; the tick code
    # folds every one of them back to a constant
    init_only = trace.cells - trace.written_play
    assert len(init_only) >= 20
    tick = set(prog.procs) - ssa.init_reachable(prog)
    loads = {
        e.a.v
        for n, _b, s in _stmts(prog)
        if n in tick
        for e in (getattr(s, "e", None),)
        if type(e) is Load and type(e.a) is Const
    }
    assert not loads & init_only
