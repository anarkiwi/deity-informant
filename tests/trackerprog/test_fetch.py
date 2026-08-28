"""T3: the fetch as producers over row bytes and cells, and the bytes-only row."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tuneprog"))

from deity_informant.trackerprog import certify, emit, fetch, lift, region  # noqa: E402
from deity_informant.trackerprog.namer import Namer  # noqa: E402
from deity_informant.trackerprog.resolve import Sel  # noqa: E402
from deity_informant.trackerprog.sound import evaldata  # noqa: E402
from deity_informant.tuneprog import pipeline, provenance  # noqa: E402
from deity_informant.tuneprog.history import history  # noqa: E402
from deity_informant.tuneprog.ir import Bin, Const, Load, Store  # noqa: E402
from deity_informant.tuneprog.verify import certify as certified  # noqa: E402

from _prog import tuneprog  # noqa: E402
from test_player import INS_TUNE, ins_blocks, t3  # noqa: E402
from test_score import TUNE, TUNE_CALL, blocks  # noqa: E402


def derive(code=TUNE, calls=64, data=None):
    """``(fetches, chans, prog, view, names, t2)`` of a hermetic tune, the region's own."""
    trace, prog = tuneprog(code, calls=calls, s4=True, blocks=data or blocks())
    view, _st, names = pipeline.present(prog)
    hist, ver = history(prog, trace, names.to_dict(), calls=calls, obs=True)
    t2 = lift.document(view, names, hist, certified(prog, ver))
    F, bad = region.fetch(prog, emit.tables_of(t2, view, names))
    assert bad == []
    chans = fetch.channels_of(t2, view, names)
    return fetch.Fetches(prog, names, F, chans, Namer(view, names)), chans, prog, view, names, t2


def test_the_fetch_is_producers_over_row_bytes_and_named_cells():
    tp, refusals, _rec, _ver, _prog, _snd = t3()
    assert refusals == []
    (rgn,) = tp["score"]["fetch"]
    assert not rgn["refusals"]
    prints = [p["print"] for p in rgn["producers"]]
    assert "sid.reg[0] = FREQ_LO[byte[0]] if byte[0] != $FF" in prints
    assert "sid.reg[4] = $41 if byte[0] != $FF" in prints
    assert any(p["bytes"] == ["byte[0]"] for p in rgn["producers"])
    assert {p["cell"] for p in rgn["producers"]} >= {"sid.reg[1]", "ptr", "ptr[1]"}
    assert certify.schema_check({**tp, "producers": [], "accs": {}}) == []


def test_the_order_wrap_reads_the_order_table_at_a_named_position():
    tp, _refusals, _rec, _ver, _prog, _snd = t3()
    (rgn,) = tp["score"]["fetch"]
    reads = {b for p in rgn["producers"] for b in p["bytes"]}
    assert "T2000[0]" in reads and "byte[0]" in reads
    chan_o, chan_p = tp["score"]["channels"]
    assert (chan_o["role"], chan_p["role"]) == ("order", "pattern")


def test_a_row_is_its_duration_and_bytes_and_patterns_are_reused():
    tp, _refusals, _rec, _ver, _prog, _snd = t3()
    (voice,) = tp["score"]["voices"]
    assert all(set(r) == {"dur", "bytes", "at"} for r in voice["rows"])
    assert sum(r["dur"] for r in voice["rows"]) + voice["start"] == tp["meta"]["horizon"]
    # the orderlist 0, 1, $FF plays two patterns round and round, named by their pointers
    assert {"p0", "p1"} <= set(voice["patterns"]) and len(voice["order"]) > len(voice["patterns"])
    assert [o["pattern"] for o in voice["order"]][:2] == ["p0", "p1"]
    assert voice["patterns"]["p0"][0]["bytes"] == {"T2000": [0], "T2100": [12]}
    md = emit.render(tp)
    assert "voice 0: order p0 p1 " in md and "fetch " in md and "    4 00 | 0C" in md


def test_a_callee_inside_the_region_is_fetched_with_it():
    tp, refusals, _rec, ver, _prog, snd = t3(TUNE_CALL)
    assert refusals == []
    (rgn,) = tp["score"]["fetch"]
    assert any(p["print"].startswith("ptr = ") for p in rgn["producers"])
    got, trap = emit.replay(tp, snd)
    assert trap is None and certify.divergence(ver.obs, got) is None


def test_an_instrument_byte_is_a_guarded_producer_and_the_player_applies_it():
    tp, refusals, _rec, ver, _prog, snd = t3(INS_TUNE, data=ins_blocks())
    assert refusals == []
    (rgn,) = tp["score"]["fetch"]
    assert any(p["cell"] == "ad_idx" and "byte[0]" in p["bytes"] for p in rgn["producers"])
    got, trap = emit.replay(tp, snd)
    assert trap is None and certify.divergence(ver.obs, got) is None


def test_what_does_not_open_is_a_named_refusal_and_the_player_traps_on_it():
    fs, chans, prog, _view, _names, _t2 = derive()
    ((_key, r),) = fs.F.regions.items()
    assert all(not D["refusals"] for D in fs.out.values())
    blk = next(
        prog.procs[r.proc].blocks[l]
        for l in r.blocks
        if any(type(s) is Store for s in prog.procs[r.proc].blocks[l].stmts)
    )
    st = next(s for s in blk.stmts if type(s) is Store)
    st.v = Bin("+", st.v, Load("io", Const(0xD012, 2), 1, 0xD012, 0xD012, -1), 1)
    again = fetch.Fetches(prog, fs.ctx.names, fs.F, chans, fs.namer)
    (bad,) = [x for D in again.out.values() for x in D["refusals"]]
    assert bad.why == "fetch not in IR" and "input" in bad.detail and bad.site.startswith(r.proc)
    doc = fetch.document(again, chans)
    assert doc[0]["refusals"][0]["cell"] == bad.cell


def test_evaluate_and_the_data_form_agree_on_selections_and_bytes():
    cur = Load("ram", Const(0x10, 2), 1, 0x10, 0x10, 3)
    e = Sel(
        (
            ((), Const(1)),
            (((Bin("==", cur, Const(0)), True, frozenset()),), fetch.Byte("T", cur, 2)),
        )
    )
    mem = bytearray(0x100)
    rows = {"T": [7, 8, 9]}

    def rd(a, w, _cls):
        return sum(mem[a + i] << (8 * i) for i in range(w))

    def byte(t, pos):
        return rows[t][pos]

    env = {"byte": byte}
    data = fetch.todata(e)
    for c, want in ((0, 9), (1, 1)):
        mem[0x10] = c
        assert fetch.evaluate(e, {}, rd, byte) == want == evaldata(data, env, mem, {})
    both = fetch.todata(Bin("and", Bin("==", cur, Const(1)), Bin("<", cur, Const(2))))
    assert evaldata(both, env, mem, {}) == 1
    assert fetch.evaluate(Bin("or", Const(0), Bin("==", cur, Const(1))), {}, rd, byte) == 1


def test_the_print_names_cells_bytes_and_selections():
    fs, chans, _prog, _view, _names, _t2 = derive()
    pr = fetch.Printer(fs.namer, chans, fs.copyvars)
    at = chans["T2100"]["addr"]
    cur = Load("ram", Const(at, 2), 1, at, at, 1)
    assert pr.expr(fetch.Byte("T2100", cur, 1)) == "byte[1]"
    assert pr.expr(fetch.Byte("T2000", Const(0), 3)) == "T2000[3]"
    assert pr.expr(Bin("or", Bin("and", Const(1), Const(0)), Const(2))) == "((1 and 0) or 2)"
    assert pr.expr(Bin("==", Bin("<", Const(1), Const(2)), Const(0))) == "not (1 < 2)"
    (D,) = fs.out.values()
    assert D["order"] and all(t in chans for t in D["order"])


def test_provenance_and_the_oracle_stay_the_reference():
    trace, prog = tuneprog(TUNE, calls=32, s4=True, blocks=blocks())
    view, st, names = pipeline.present(prog)
    hist, ver = history(prog, trace, names.to_dict(), calls=32, obs=True)
    t0 = provenance.document(view, st, names)
    t2 = lift.document(view, names, hist, certified(prog, ver))
    tp, refusals, rec, _snd = emit.lift(prog, view, names, t0, None, t2, None, trace.inputs)
    assert refusals == [] and certify.divergence(ver.obs, rec) is None
    want, trap = emit.oracle(prog, tp)
    assert trap is None and certify.divergence(ver.obs, want) is None
