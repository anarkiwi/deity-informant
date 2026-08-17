"""``--songs all``: one tuneprog from the union of every subtune's trace."""

import json

from deity_informant.tuneprog import pipeline
from deity_informant.tuneprog.ir import Const, Let, Load, Tuneprog
from deity_informant.tuneprog.tracedata import Trace, merge

from _asm import asm, psid
from _prog import PLAY

# init stores the subtune number into a play-time immediate, so the two subtunes
# differ in an SMC cell and in the path the tick takes.
TUNE = asm(
    PLAY,
    "init: STA vol+1",
    "STA sel",
    "RTS",
    "play: LDA sel",
    "BEQ quiet",
    "LDA #$41",
    "STA $D404",
    "quiet: vol: LDA #$00",
    "STA $D418",
    "INC cnt",
    "RTS",
    "sel: BRK",
    "cnt: BRK",
)


def _tune(tmp_path):
    p = tmp_path / "two.sid"
    p.write_bytes(psid({PLAY: TUNE}, init=TUNE.labels["init"], play=TUNE.labels["play"], songs=2))
    return p


def _run(tmp_path, *extra):
    out = tmp_path / "out"
    rc = pipeline.main([str(_tune(tmp_path)), "--out", str(out), "--calls", "6", *extra])
    assert rc == 0
    return out, json.loads((out / "certificate.json").read_text())


def test_every_subtune_verifies_against_the_union_program(tmp_path):
    out, cert = _run(tmp_path, "--songs", "all", "--prefix", "3")
    assert cert["divergence"] is None
    assert [s["song"] for s in cert["subtunes"]] == [1, 2]
    assert all(s["divergences"] == 0 and s["ticks"] == 6 for s in cert["subtunes"])
    assert (out / "s01" / "trace.json").exists() and (out / "s02" / "trace.json").exists()

    # what init writes is per-subtune state, so no cell folds to a constant and
    # the tick still loads the immediate init patched
    prog = Tuneprog.load(out / "tuneprog.S4.json")
    cell = TUNE.labels["vol"] + 1
    assert not [r for r in prog.storage if r.kind == "init_constant"]
    assert any(
        type(s) is Let and type(s.e) is Load and type(s.e.a) is Const and s.e.a.v == cell
        for b in prog.procs["tick"].blocks.values()
        for s in b.stmts
    )


def test_the_union_trace_keys_sites_by_every_subtunes_cells(tmp_path):
    out, _cert = _run(tmp_path, "--songs", "all")
    parts = [Trace.load(out / n) for n in ("s01", "s02")]
    whole = Trace.load(out)
    assert whole.cells == set().union(*(t.cells for t in parts))
    assert whole.written_play == set().union(*(t.written_play for t in parts))
    # the union program is at least as big as either subtune's
    assert len(whole.sites) >= max(len(t.sites) for t in parts)
    assert merge(parts).sites.keys() == whole.sites.keys()


def test_one_subtune_alone_still_folds_its_init_cell(tmp_path):
    out, cert = _run(tmp_path, "--song", "2")
    assert cert["subtunes"][0]["song"] == 2 and cert["subtunes"][0]["divergences"] == 0
    prog = Tuneprog.load(out / "tuneprog.S4.json")
    cell = TUNE.labels["vol"] + 1
    assert not [
        s
        for b in prog.procs["tick"].blocks.values()
        for s in b.stmts
        if type(s) is Let and type(s.e) is Load and type(s.e.a) is Const and s.e.a.v == cell
    ]
