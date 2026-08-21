"""``--songs all``: one tuneprog from the union of every subtune's trace."""

import json
from pathlib import Path

import pytest

from deity_informant.tuneprog import pipeline
from deity_informant.tuneprog.ir import Const, Let, Load, Tuneprog
from deity_informant.tuneprog.machine import Refusal
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


# song 1's tick (init A=0) is a no-op and repeats at once; song 2 steps a 16-bit counter.
MIXED = asm(
    PLAY,
    "init: STA sel",
    "RTS",
    "play: LDA sel",
    "BEQ quiet",
    "INC cnt",
    "BNE quiet",
    "INC cnt+1",
    "quiet: LDA #$0F",
    "STA $D418",
    "RTS",
    "sel: BRK",
    "cnt: BRK",
    "BRK",
)


def _mixed(tmp_path):
    p = tmp_path / "mixed.sid"
    p.write_bytes(
        psid({PLAY: MIXED}, init=MIXED.labels["init"], play=MIXED.labels["play"], songs=2)
    )
    return p


def _argv(sid, out, period, calls=12, chunk=4):
    a = [str(sid), "--out", str(out), "--songs", "all", "--calls", str(calls)]
    a += ["--chunk", str(chunk), "--budget", "0.0", "--no-text", "--prefix", "0", "--resume"]
    return a + ["--until-period"] if period else a


def _drive(sid, out, period=lambda st: True, chunk=4):
    """Run the pipeline to the end in ``--budget 0`` steps; ``period`` picks each argv."""
    rc, n = pipeline.MORE, 0
    while rc == pipeline.MORE and n < 40:
        f = out / "state.json"
        st = json.loads(f.read_text()) if f.exists() else {}
        rc = pipeline.main(_argv(sid, out, period(st), chunk=chunk))
        n += 1
    assert rc == 0
    return json.loads((out / "certificate.json").read_text())


def _ticks(cert):
    return [(s["song"], s["ticks"], s["complete"]) for s in cert["subtunes"]]


def _traced(out):
    return json.loads((out / "state.json").read_text())["traced"]


def test_songs_all_records_each_subtunes_own_stop_reason(tmp_path):
    """A period-stopping subtune certifies its witness, whatever chunk found it."""
    out = tmp_path / "out"
    cert = _drive(_mixed(tmp_path), out)
    assert _ticks(cert) == [(1, 2, True), (2, 12, False)]  # song 1 repeats at tick 1
    assert {k: (v["calls"], v["stop"]) for k, v in _traced(out).items()} == {
        "1": (2, "period"),
        "2": (12, "horizon"),
    }
    assert not (out / "verify.pkl").exists()


def test_a_subtunes_tick_count_does_not_depend_on_the_chunk_that_found_it(tmp_path):
    sid = _mixed(tmp_path)
    ticks = [_ticks(_drive(sid, tmp_path / ("c%d" % c), chunk=c)) for c in (3, 4, 8)]
    assert ticks[0] == ticks[1] == ticks[2] == [(1, 2, True), (2, 12, False)]


def test_the_union_certificate_certifies_each_subtunes_own_horizon():
    """`ghouls-songs-all` stops each subtune where that subtune's own certificate does."""
    certs = Path(__file__).resolve().parents[2] / "docs" / "certificates"
    alone = {}
    for p in sorted(certs.glob("ghouls-song[0-9]*.json")):
        s = json.loads(p.read_text())["subtunes"][0]
        alone[s["song"]] = s["ticks"]
    union = json.loads((certs / "ghouls-songs-all.json").read_text())["subtunes"]
    assert len(alone) == len(union) == 32
    assert {s["song"]: s["ticks"] for s in union} == alone


def test_a_subtune_traced_under_another_horizon_is_retraced_on_resume(tmp_path):
    sid = _mixed(tmp_path)
    fresh = _drive(sid, tmp_path / "fresh")
    out = tmp_path / "mixed"
    got = _drive(sid, out, period=lambda st: bool(st.get("traced")))
    assert _ticks(got) == _ticks(fresh)
    assert (_traced(out)["1"]["stop"], _traced(out)["1"]["calls"]) == ("period", 2)


def test_a_legacy_songs_all_state_restarts_instead_of_resuming(tmp_path):
    sid = _mixed(tmp_path)
    out = tmp_path / "out"
    _drive(sid, out)
    st = json.loads((out / "state.json").read_text())
    st["traced"] = [1, 2]
    (out / "state.json").write_text(json.dumps(st))
    assert _ticks(_drive(sid, out)) == [(1, 2, True), (2, 12, False)]
    assert _traced(out)["2"]["stop"] == "horizon"


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


def test_changing_a_build_option_rebuilds_instead_of_resuming(tmp_path):
    """The S4 program on disk is not the one another `--closure` asks for."""
    out = tmp_path / "o"
    sid = _mixed(tmp_path)
    argv = [str(sid), "--out", str(out), "--song", "1", "--calls", "8"]
    assert pipeline.main(argv) == 0
    first = json.loads((out / "certificate.json").read_text())
    assert "closure" not in first
    assert pipeline.main(argv + ["--resume", "--closure", "static"]) == 0
    doc = json.loads((out / "certificate.json").read_text())
    assert doc["closure"]["arms"] >= 0 and doc["subtunes"][0]["ticks"] == 8
    assert json.loads((out / "state.json").read_text())["build"][0] == "static"


def test_a_single_song_resumed_under_another_horizon_is_retraced(tmp_path):
    """A tracer past the new target would certify ticks nobody asked for."""
    out = tmp_path / "o"
    sid = _mixed(tmp_path)
    argv = [str(sid), "--out", str(out), "--song", "2", "--no-text", "--prefix", "0"]
    assert pipeline.main(argv + ["--calls", "12"]) == 0
    assert pipeline.main(argv + ["--calls", "6", "--resume"]) == 0
    doc = json.loads((out / "certificate.json").read_text())
    assert doc["subtunes"][0]["ticks"] == 6 and doc["subtunes"][0]["divergences"] == 0
    assert json.loads((out / "state.json").read_text())["horizon"][0] == 6


# song 1 (init A=0) leaves its stack scratch alone; song 2 steps it, so page-free
SCRATCH = asm(
    PLAY,
    "init: STA sel",
    "RTS",
    "play: TSX",
    "LDA $0100,X",
    "CLC",
    "ADC sel",
    "STA $0100,X",
    "STA $D400",
    "RTS",
    "sel: BRK",
)


def test_until_period_retraces_only_the_subtunes_that_stopped_page_free(tmp_path):
    """S4 calls the program residual, so a page-free stop is no witness for it.

    Song 1 repeats at once on either footprint; song 2 repeats at 1 page-free and
    at 256 with the page, and is the one traced on.
    """
    out = tmp_path / "o"
    sid = tmp_path / "scratch.sid"
    sid.write_bytes(
        psid({PLAY: SCRATCH}, init=SCRATCH.labels["init"], play=SCRATCH.labels["play"], songs=2)
    )
    argv = [str(sid), "--out", str(out), "--songs", "all", "--until-period"]
    assert pipeline.main(argv + ["--max-calls", "1000", "--chunk", "8", "--no-text"]) == 0
    cert = json.loads((out / "certificate.json").read_text())
    assert cert["stack"]["procs"] == ["tick"]
    assert _ticks(cert) == [(1, 2, True), (2, 257, True)]
    st = json.loads((out / "state.json").read_text())
    assert st["stack"] == "residual"
    assert [st["traced"][k]["full"] for k in ("1", "2")] == [True, True]


def test_songs_all_refuses_subtunes_that_disagree_on_cadence(tmp_path):
    """One merged trace is one schedule, so a mixed ``speed`` word is a refusal."""
    p = tmp_path / "mixed.sid"
    p.write_bytes(
        psid(
            {PLAY: TUNE},
            init=TUNE.labels["init"],
            play=TUNE.labels["play"],
            songs=2,
            speed=0b10,
        )
    )
    with pytest.raises(Refusal) as e:
        pipeline.main([str(p), "--out", str(tmp_path / "out"), "--calls", "6", "--songs", "all"])
    assert e.value.reason == "subtunes disagree on cadence"
