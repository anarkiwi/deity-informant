"""Ghouls'n'Ghosts (Follin, anatomy 3.6) end to end, plus the SID Wizard regression.

One music subtune over 30 s of music, one sound-effect subtune to its state
fixpoint, and Emomyst (whose init patches ~30 operands through a relocation loop)
at 10 s. The full 32-subtune certificates are ``docs/certificates/ghouls-*.json``.
"""

import pytest

from deity_informant.tuneprog import ssa
from deity_informant.tuneprog.ir import Const, Let, Load, Store, Switch, Var, retval

from _hvsc import EMOMYST, GNG, body as proc_body, decompiled, folded, switches
from deity_informant.tuneprog.verify import verify

pytestmark = pytest.mark.hvsc

DISPATCH = (0x6375, 0x6562, 0x6751)  # the three voices' patched JMP operands
VOICE = (0x6234, 0x6421, 0x6610)  # the three copies of the 493-byte template
SMC = (0x62EE, 0x64DB, 0x66CA, 0x6269, 0x640F, 0x65FE, 0x67ED, 0x6800)


def _stmts(prog):
    return [(n, b, s) for n, p in prog.procs.items() for b in p.blocks.values() for s in b.stmts]


def _defs(proc):
    return {s.n: s.e for b in proc.blocks.values() for s in b.stmts if type(s) is Let}


def _switches(prog, cell):
    """Switches whose value is the 16-bit load of ``cell`` (a patched JMP operand)."""
    return [t for a, _w, t in switches(prog, width=2) if a == cell]


def test_ghouls_song_one_over_thirty_seconds():
    run = decompiled(GNG, seconds=30, song=0, prefix=500, text=False)
    trace, prog, v, cert = run.trace, run.prog, run.v, run.cert
    sub = cert["subtunes"][0]
    assert cert["divergence"] is None and v.div is None
    assert sub["divergences"] == 0 and sub["envelope_traps"] == 0 and sub["seconds"] > 29
    assert trace.meta["songs"] == 32 and cert["entry"]["cycles_per_tick"] == 19656

    # 1: the patched JMP of each voice is a switch over its own handler table,
    # under the copy index, and the arms the voices share are one body
    arms = 0
    for cell in DISPATCH:
        sw = _switches(prog, cell)
        assert len(sw) == 1, "%04X" % cell
        arms += len(sw[0].cases)
    assert arms >= 21, arms

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

    # 6: each copy ran a different subset of the shared template, so the copies
    # differ in size before any operand does -- which is what the fold's coverage
    # vector records (below)
    bounds = VOICE + (0x67FF,)
    reached = [len([k for k in trace.sites if bounds[i] <= k[0] < bounds[i + 1]]) for i in range(3)]
    assert min(reached) > 100 and len(set(reached)) > 1, reached

    doc = decompiled(GNG, seconds=30, song=0, prefix=500).text
    assert "sid.reg[" in doc  # 2: the data-dependent register prints as one
    assert "return (" in doc  # 5
    assert "for v in" in doc  # the init clear's own loop, over its index


def test_ghouls_voice_copies_are_one_body_over_the_copy_index():
    text, names, view, prog = folded(GNG, seconds=30, song=0, prefix=500)
    doc = prog.meta["copies"]
    c = names.copies
    assert len(doc["families"]) == 1 and doc["families"][0]["copies"] == 3
    assert doc["families"][0]["rows"] > 300 and doc["families"][0]["columns"] > 40
    assert not doc["refused"] and 0 < c["unverified"] < c["statements"]
    assert {k.count("1") for k in c["coverage"]} == {1, 2, 3}  # every copy ran rows alone

    tick = proc_body(text, "tick")
    lines = "\n".join(tick)
    assert sum(1 for l in tick if l.strip().startswith("while True")) >= 1, tick[:4]
    # the per-copy columns are read once, at the top of the loop over the index
    assert lines.count("copies_%04X[" % VOICE[0]) > 40
    # the command switch is inside the loop, one arm body for the three voices
    arms = [i for i, l in enumerate(tick) if l.strip().startswith("switch b%04X[" % DISPATCH[0])]
    assert len(arms) == 3 and lines.count("case $") >= 21
    assert lines.count("goto L") >= 14  # the arms two of the three voices share
    assert "unverified (ran for v = " in lines
    assert len(view.procs["tick"].blocks) < 250


def test_the_folded_ghouls_program_is_the_certified_one():
    _text, names, _view, prog = folded(GNG, seconds=30, song=0, prefix=500)
    run = decompiled(GNG, seconds=30, song=0, prefix=500, text=False)
    v = verify(prog, run.trace, calls=run.calls, prefix=0)
    assert v.div is None and v.call == run.calls and prog is run.prog
    tick = prog.procs["tick"].blocks
    zero = [b for b in tick.values() if b.cover and 0 in tuple(b.cover)]
    assert len(zero) > 20 and all(b.count == 0 for b in zero if not any(b.cover))
    assert names.copies["unverified"] == sum(len(b.stmts) for b in zero)


def test_ghouls_sound_effect_subtune_is_complete():
    run = decompiled(GNG, seconds=25, song=15, until_period=True, prefix=100, text=False)
    trace, prog, v, cert = run.trace, run.prog, run.v, run.cert
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
    run = decompiled(EMOMYST, seconds=10, prefix=500, text=False)
    trace, prog, v, cert = run.trace, run.prog, run.v, run.cert
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
