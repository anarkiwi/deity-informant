"""End-to-end certificates for two HVSC exemplars (marked ``hvsc``; short horizons).

The full-length runs (Commando's HVSC length, Automatas to its first state
repeat) are the job of ``tools/tuneprog_certify.py`` and reproduced by
``tools/tuneprog_recert.py``; their certificates live in ``docs/certificates/``.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from deity_informant.tuneprog import emit, ssa
from deity_informant.tuneprog.build import build_ir
from deity_informant.tuneprog.idioms import rewrite
from deity_informant.tuneprog.irwalk import loads
from deity_informant.tuneprog.verify import certify, verify

from _hvsc import AUTOMATAS, COMMANDO, front_end, tune_file

pytestmark = pytest.mark.hvsc

ROOT = Path(__file__).resolve().parents[2]


def _certify(relpath, seconds, prefix, song=None, override=None):
    """Trace, decompile, run S4 and verify: ``(prog, trace, verifier, cert, calls, raw)``."""
    _e, calls, trace, lifted, regions, procs = front_end(relpath, seconds, song, override)
    prog = build_ir(trace, lifted, regions, procs, meta={"name": Path(relpath).name})
    raw = _counts(prog) + (_load_addrs(prog),)
    ssa.simplify(
        prog, rewrite, folds=ssa.Folds(trace.image_post_init, trace.cells, trace.written_play)
    )
    src = emit.emit_python(prog)
    v = verify(prog, trace, calls=calls, prefix=prefix, src=src)
    return prog, trace, v, certify(prog, v, prefix=prefix), calls, raw


def _counts(prog):
    """``(statements, flag definitions)`` of a program, for the S4 reduction check."""
    lets = [
        s
        for p in prog.procs.values()
        for b in p.blocks.values()
        for s in b.stmts
        if type(s).__name__ == "Let"
    ]
    n = sum(len(b.stmts) for p in prog.procs.values() for b in p.blocks.values())
    return n, sum(s.n.split("#")[0] in ("C", "Z", "N", "V") for s in lets)


def _load_addrs(prog, procs=None):
    """Every constant address the program loads from (E4: SMC cells are loads)."""
    out = set()
    for n, p in prog.procs.items():
        if procs is not None and n not in procs:
            continue
        for b in p.blocks.values():
            for s in b.stmts:
                for e in (getattr(s, "e", None), getattr(s, "a", None), getattr(s, "v", None)):
                    for x in loads(e) if e is not None else ():
                        if type(x.a).__name__ == "Const":
                            out.update(range(x.a.v, x.a.v + x.w))
    return out


def test_commando_certified_over_a_minute_of_music():
    prog, _T, v, cert, calls, raw = _certify(COMMANDO, seconds=60, prefix=2000)
    sub = cert["subtunes"][0]
    assert cert["divergence"] is None and v.div is None
    assert sub["divergences"] == 0 and sub["envelope_traps"] == 0
    assert sub["ticks"] == calls == v.call and sub["seconds"] > 59
    assert sub["interp_prefix"] == 2000 and sub["closure"] == "trace"
    assert cert["stage"] == "S4" and cert["entry"]["addr"] == 0x5012
    # S4 keeps only the flag values the tune uses as data (anatomy section 5.3)
    stmts, flags = _counts(prog)
    assert stmts < 0.5 * raw[0] and flags < 0.15 * raw[1]
    assert sub["inputs_pinned"] >= calls  # A is live-in at every call


def test_automatas_certified_over_thirty_seconds_of_music():
    prog, T, v, cert, calls, raw = _certify(AUTOMATAS, seconds=30, prefix=2000)
    sub = cert["subtunes"][0]
    assert cert["divergence"] is None and v.div is None
    assert sub["divergences"] == 0 and sub["envelope_traps"] == 0
    assert sub["ticks"] == calls == v.call
    assert sub["interp_prefix"] == 2000
    assert cert["entry"] == {
        "kind": "sub",
        "addr": 0x0FE3,
        "cycles_per_tick": 2457,
        "source": "cia_timer",
    }
    # E4: every play-written operand cell is read by a load at its instruction
    play_cells = T.cells & T.written_play
    assert play_cells and play_cells <= raw[2]
    assert 0x0FE4 in play_cells  # the wrapper's own call counter
    # cells init patches are cells too, and the tick folds them back to constants
    assert 0x10D4 in T.cells and 0x10D4 not in T.written_play  # the model's NOP/ASL
    tick = set(prog.procs) - ssa.init_reachable(prog)
    assert not _load_addrs(prog, tick) & (T.cells - T.written_play)
    # 24 SID writes a call, all of them from the tuneprog's own statements
    assert len(v.M.sid) == 24
    assert sub["inputs_pinned"] == 2228  # the init raster wait plus one $D41B read
    # the SMC opcode cells became variant switches with a trap default
    sw = [
        b.term
        for p in prog.procs.values()
        for b in p.blocks.values()
        if type(b.term).__name__ == "Switch"
    ]
    assert sw and all(t.default == "" for t in sw)
    assert T.meta["unmatched_rts"] == 0
    stmts, flags = _counts(prog)
    assert stmts < 0.6 * raw[0] and flags < 0.35 * raw[1]


def test_automatas_certifies_under_the_other_sid_model():
    # $D41B bit 0 picks the 6581/8580 cutoff scaling at init; pin the bit the
    # default trace did not see and the same pipeline must still certify.
    _prog, _T, v, cert, calls, _raw = _certify(
        AUTOMATAS, seconds=10, prefix=500, override={0xD41B: 0x01}
    )
    assert cert["divergence"] is None and v.call == calls
    assert cert["subtunes"][0]["envelope_traps"] == 0


def test_certify_tool_smoke(tmp_path):
    sid = tune_file(COMMANDO)
    out = tmp_path / "cert"
    r = subprocess.run(
        [
            sys.executable,
            str(ROOT / "tools" / "tuneprog_certify.py"),
            str(sid),
            "--out",
            str(out),
            "--seconds",
            "5",
            "--prefix",
            "100",
        ],
        capture_output=True,
        text=True,
        check=False,
        cwd=ROOT,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    for name in (
        "trace.json",
        "regions.json",
        "procs.json",
        "tuneprog.S4.json",
        "tuneprog.py",
        "tuneprog.S5.json",
        "tuneprog.S6.json",
        "tuneprog.md",
    ):
        assert (out / name).exists(), name
    cert = json.loads((out / "certificate.json").read_text())
    assert cert["divergence"] is None and cert["subtunes"][0]["divergences"] == 0
    assert "def p_tick(S, m" in (out / "tuneprog.py").read_text()
