"""S8: per-call differential verification, divergence reports, periodicity, chunking."""

import pickle

import pytest

from deity_informant.tuneprog.ir import Const, Store
from deity_informant.tuneprog.verify import Reference, Verifier, certify, prefix_check, verify

from _asm import asm
from _prog import PLAY, counter, tuneprog

PERIODIC = ("INC cnt", "LDA cnt", "AND #$03", "STA cnt", "STA $D400")


def _io_stores(prog, proc="tick"):
    return [
        s
        for b in prog.procs[proc].blocks.values()
        for s in b.stmts
        if type(s) is Store and s.cls == "io"
    ]


def test_clean_run_has_no_divergence_and_matches_the_state_hashes():
    T, prog = tuneprog(counter("INC cnt", "LDA cnt", "STA $D400"), calls=8, s4=True)
    v = verify(prog, T, calls=8, prefix=4)
    assert v.div is None and v.call == 8
    n, h = v.M.hash()
    assert n == T.footprint_size[7] and h == T.state_hash[7]
    cert = certify(prog, v, prefix=4)
    assert cert["divergence"] is None
    assert cert["subtunes"][0]["divergences"] == 0
    assert cert["subtunes"][0]["envelope_traps"] == 0
    assert cert["cost"]["ir_statements"] > 0


def test_periodicity_witness_agrees_with_the_tracer():
    T, prog = tuneprog(counter(*PERIODIC), calls=12, s4=True)
    assert T.meta["period"] == 4
    v = verify(prog, T, calls=12)
    assert v.div is None
    assert (v.period, v.first_repeat) == (T.meta["period"], T.meta["first_repeat"])
    sub = v.subtune()
    assert sub["complete"] and sub["period"] == 4 and sub["closure"] == "trace"


def test_a_wrong_write_value_is_reported_with_its_site():
    T, prog = tuneprog(counter("LDA #$07", "STA $D400"), calls=4, s4=True)
    st = _io_stores(prog)[0]
    site = st.src
    st.v = Const(0x42)
    v = verify(prog, T, calls=4)
    assert v.div["tick"] == 0 and v.div["index"] == 0
    assert v.div["compared"] == "sid"
    assert v.div["expected"] == ["$D400", 7] and v.div["got"] == ["$D400", 0x42]
    assert v.div["site"] == "$%04X" % site
    assert certify(prog, v)["subtunes"][0]["divergences"] == 1


def test_a_missing_write_is_reported_at_the_right_index():
    T, prog = tuneprog(counter("LDA #$07", "STA $D400", "STA $D401"), calls=3, s4=True)
    b = [b for b in prog.procs["tick"].blocks.values() if _io_stores(prog)]
    for blk in prog.procs["tick"].blocks.values():
        blk.stmts = [s for s in blk.stmts if not (type(s) is Store and s.a.v == 0xD401)]
    del b
    v = verify(prog, T, calls=3)
    assert v.div["tick"] == 0 and v.div["index"] == 1 and v.div["got"] is None
    assert v.div["expected"] == ["$D401", 7]


def test_schedule_effects_are_compared_too():
    T, prog = tuneprog(counter("LDA cnt", "STA $DC04", "STA $D400", "INC cnt"), calls=3, s4=True)
    assert verify(prog, T, calls=3).div is None
    for blk in prog.procs["tick"].blocks.values():
        for s in blk.stmts:
            if type(s) is Store and s.cls == "io" and s.a.v == 0xDC04:
                s.v = Const(0x99)
    v = verify(prog, T, calls=3)
    assert v.div["compared"] == "io"


def test_an_envelope_violation_counts_as_a_divergence():
    code = asm(
        PLAY,
        "init: LDA #$00",
        "STA cnt",
        "RTS",
        "play: LDY cnt",
        "LDA tab,Y",
        "STA $D400",
        "INC cnt",
        "LDA cnt",
        "AND #$03",
        "STA cnt",
        "RTS",
        "cnt: BRK",
        "tab: BRK",
        "BRK",
        "BRK",
        "BRK",
    )
    T, prog = tuneprog(code, calls=8, s4=True)
    assert verify(prog, T, calls=8).div is None
    for blk in prog.procs["tick"].blocks.values():
        for s in blk.stmts:
            if type(s) is not Store and getattr(s, "e", None) is not None:
                e = s.e
                if type(e).__name__ == "Load" and e.r >= 0 and e.hi - e.lo == 3:
                    s.e = type(e)(e.cls, e.a, e.w, e.lo, e.lo, e.r)  # shrink the envelope
    v = verify(prog, T, calls=8)
    assert v.div is not None and v.div["trap"] == "envelope"
    assert certify(prog, v)["subtunes"][0]["envelope_traps"] == 1


def test_chunked_resume_reaches_the_same_state_as_one_run():
    T, prog = tuneprog(counter(*PERIODIC), calls=12, s4=True)
    ref = Reference(T, 12)
    one = Verifier(prog, ref)
    one.run(12)
    part = Verifier(prog, ref)
    part.run(5)
    st = pickle.loads(pickle.dumps(part.state(), protocol=pickle.HIGHEST_PROTOCOL))
    two = Verifier(prog, ref)
    two.restore(st)
    two.run(12)
    assert two.call == one.call == 12 and two.div is None
    assert bytes(two.M.m) == bytes(one.M.m)
    assert two.M.hash() == one.M.hash()
    assert (two.period, two.first_repeat) == (one.period, one.first_repeat)


def test_budget_stops_a_run_before_the_horizon():
    T, prog = tuneprog(counter(*PERIODIC), calls=40, s4=True)
    v = Verifier(prog, Reference(T, 40))
    done = v.run(40, budget=0.0, chunk=1)
    assert not done and 0 < v.call < 40
    assert v.run(40)
    assert v.call == 40 and v.div is None


def test_interpreter_prefix_agrees_with_the_generated_code():
    T, prog = tuneprog(counter("INC cnt", "LDA cnt", "STA $D400"), calls=6, s4=True)
    ref = Reference(T, 6)
    p = prefix_check(prog, ref, 6)
    assert p.div is None and p.call == 6
    assert (
        certify(prog, verify(prog, T, calls=6, prefix=6), prefix=6)["subtunes"][0]["interp_prefix"]
        == 6
    )


def test_entry_register_values_are_checked_against_the_trace():
    # the play routine reads A before writing it: the tracer pins it as an input
    T, prog = tuneprog(counter("STA $D400", "INC cnt"), calls=4, s4=True)
    assert any(a >= 0x10000 for _c, _s, _o, a, _v in T.inputs)
    v = verify(prog, T, calls=4)
    assert v.div is None and v.nreg > 0
    ref = Reference(T, 4)
    ref.regs[1] = [(0, 0x5A)]  # a value the tuneprog cannot have
    bad = Verifier(prog, ref)
    bad.run(4)
    assert bad.div["compared"] == "entry register"


def test_init_writes_are_compared():
    code = asm(
        PLAY,
        "init: LDA #$0F",
        "STA $D418",
        "RTS",
        "play: LDA #$07",
        "STA $D400",
        "RTS",
    )
    T, prog = tuneprog(code, calls=2, s4=True)
    assert [(a, v) for a, v, _c in T.init_writes] == [(0xD418, 0x0F)]
    assert verify(prog, T, calls=2).div is None
    for blk in prog.procs["init"].blocks.values():
        for s in blk.stmts:
            if type(s) is Store and s.cls == "io":
                s.v = Const(0x00)
    v = verify(prog, T, calls=2)
    assert v.div["tick"] == -1 and v.div["expected"] == ["$D418", 0x0F]


@pytest.mark.parametrize("backend", ["py", "interp"])
def test_both_executors_run_the_same_program(backend):
    T, prog = tuneprog(counter(*PERIODIC), calls=8, s4=True)
    v = Verifier(prog, Reference(T, 8), backend=backend)
    v.run(8)
    assert v.div is None and v.call == 8
