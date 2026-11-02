"""Whole-program lifting: lifted programs against the reference interpreter and jennings."""

import dataclasses
import threading

import pytest

from jennings.devices.mpu6502 import NativeMPU
from jennings.memory import NativeMemory

import wholeprog_progs as W
from deity_informant import NATIVE, c64, trace, wholeprog
from deity_informant.smc import machine
from wholeprog_progs import CODEHIT, GEN, INVALIDATE, IO, JMPIND, PATCH, TOGGLE, WRAP, assemble

pytestmark = pytest.mark.skipif(not NATIVE, reason="needs the native extension")


@pytest.fixture(name="backend", autouse=True, params=["jit", "interp"])
def _backend(request, monkeypatch):
    """Every test on both engines (``wholeprog.BACKEND``)."""
    monkeypatch.setattr(wholeprog, "BACKEND", request.param)
    return request.param


PROGRAMS = {
    "patch": ("PATCH", ["entry"], [(0, 0)] * 20),
    "toggle": ("TOGGLE", ["entry"], [(0, 0)] * 6),
    "jumptab": ("JUMPTAB", ["entry"], [(0, a) for a in range(8)]),
    "pushpull": ("PUSHPULL", ["entry"], [(0, a) for a in range(4)]),
    "tsx": ("TSXPROG", ["entry"], [(0, 0)] * 3),
    "jmpind": ("JMPIND", ["entry"], [(0, a) for a in range(8)]),
    "nested": ("NESTED", ["entry"], [(0, 0)] * 5),
    "timing": ("TIMING", ["entry"], [(0, 0)] * 30),
    "brkrti": ("BRKRTI", ["entry"], [(0, 0)] * 3),
    "gen": ("GEN", ["gen", "run"], [(0, 3), (1, 0), (0, 4), (1, 0), (0, 3), (1, 0)]),
    "guarded": ("GUARDED", ["entry"], [(0, 0x18)] * 4),
    "recurse": ("RECURSE", ["entry"], [(0, 0)] * 3),
    "deadflags": ("DEADFLAGS", ["entry"], [(0, 0)] * 3),
    "correlated": ("CORRELATED", ["entry"], [(0, a) for a in range(16)]),
    "brkzero": ("BRKZERO", ["entry"], [(0, a) for a in range(3)]),
    "sprestore": ("SPRESTORE", ["entry"], [(0, a) for a in range(3)]),
    "dataincode": ("DATAINCODE", ["entry"], [(0, 0)] * 4),
    "dropret": ("DROPRET", ["entry"], [(0, a) for a in range(3)]),
    "pushrts": ("PUSHRTS", ["entry"], [(0, a) for a in range(4)]),
    "sptable": ("SPTABLE", ["entry"], [(0, a) for a in range(4)]),
    "unexec": ("UNEXEC", ["entry"], [(0, a) for a in (0, 1, 0x40)]),
    "frame": ("FRAME", ["entry"], [(0, a) for a in range(8)]),
    "ptable": ("PTABLE", ["entry"], [(0, 0)] * 12),
    "pxtable": ("PXTABLE", ["entry"], [(0, i & 1) for i in range(12)]),
}

# Facts inputs and inputs reaching a stack height the facts never saw, per program.
UNSEEN = {
    "correlated": ([0], [5]),
    "brkzero": (range(3), [0x40, 0x80]),
    "sprestore": (range(3), [0x80]),
    "dropret": (range(3), [0x80]),
    "pushrts": (range(4), [0x81]),
    "sptable": ([0], [1]),
}
SP_DISPATCH = {"sprestore": 1, "sptable": 3}


def source(name):
    return getattr(W, name)


def facts_for(img, pcs, calls):
    return trace.collect(img, lambda s: [s.call(pcs[e], a) for e, a in calls])


def differential(
    img,
    prog,
    pcs,
    calls,
    synth=True,
    max_cycles=10_000_000,
    mem=None,
    reg=None,
    bank=None,
    volatile=(IO,),
):  # pylint: disable=too-many-arguments,too-many-positional-arguments
    """Run ``calls`` on the reference and the lifted program from registers ``reg``;
    everything observable must match (RAM except the dead stack below SP; cycles only when
    the program counts them). Without cycle counting a call the reference does not return
    from is compared by its halt and ends the run. The final memory."""
    m1 = bytearray(img if mem is None else mem)
    m2 = bytearray(m1)
    s1 = s2 = {"reg": reg, "cycles": 0}
    keys = ("log", "reg", "pc", "halt") + (("cycles",) if prog.cycles else ())
    for n, (e, a) in enumerate(calls):
        kw = {"log": True, "synth": synth, "max_cycles": max_cycles}
        r = wholeprog.reference_call(
            m1, pcs[e], a=a, volatile=list(volatile), clocked=prog.cycles, bank=bank, **kw, **s1
        )
        o = prog.call(m2, e, a=a, **kw, **s2)
        if not prog.cycles and r["halt"] == "cycles":
            assert o["halt"] == "cycles", n
            break
        for k in keys:
            assert r[k] == o[k], (n, k, r[k], o[k])
        sp = r["reg"][3]
        diff = [i for i in range(0x10000) if m1[i] != m2[i] and not 0x100 <= i <= 0x100 + sp]
        assert not diff, (n, [hex(i) for i in diff[:8]])
        s1 = {"reg": list(r["reg"]), "cycles": r["cycles"]}
        s2 = {"reg": list(o["reg"]), "cycles": o["cycles"] if prog.cycles else r["cycles"]}
    return m1


def lifted(name, optimize=True, fact_calls=None, cycles=False):
    src, entries, calls = PROGRAMS[name]
    img, labels = assemble(source(src))
    pcs = [labels[e] for e in entries]
    facts = facts_for(img, pcs, calls if fact_calls is None else fact_calls)
    prog = wholeprog.lift(img, pcs, [IO], facts, optimize=optimize, cycles=cycles)
    return img, pcs, calls, prog


@pytest.mark.parametrize("cycles", [False, True])
@pytest.mark.parametrize("optimize", [True, False])
@pytest.mark.parametrize("name", sorted(PROGRAMS))
def test_exact_without_deopts(name, optimize, cycles):
    img, pcs, calls, prog = lifted(name, optimize, cycles=cycles)
    assert prog.cycles == cycles
    differential(img, prog, pcs, calls)
    assert not prog.deopts
    st = prog.stats
    assert st["unreached"] == 0
    if optimize:
        assert st["ops"] < st["emitted_ops"]


def test_smc_shapes():
    st = lifted("patch")[3].stats
    assert st["cells"] == 3 and st["opcode_cells"] == 0
    st = lifted("toggle")[3].stats
    assert st["opcode_cells"] == 2 and st["calls"] == 1


def ops_of(fn):
    return [op for b in fn["blocks"] for op in b["ops"]]


@pytest.mark.parametrize("optimize", [False, True])
def test_rmw_writes_back_before_result(optimize):
    img = bytearray(0x10000)
    img[0x1000:0x1007] = bytes.fromhex("ee00c0ee04d460")  # INC $C000; INC $D404; RTS
    prog = wholeprog.lift(img, [0x1000], [c64.SID], optimize=optimize)
    stores = [op[3] for f in prog.functions for op in ops_of(f) if op[0] == "STORE"]
    assert stores == [None] * (1 if optimize else 2) + ["volatile"] * 2
    want = [(0xD404, 0, False), (0xD404, 0, True), (0xD404, 1, True)]
    for mem, out in (
        (m := bytearray(img), prog.call(m, 0, log=True)),
        (r := bytearray(img), wholeprog.reference_call(r, 0x1000, volatile=[c64.SID], log=True)),
    ):
        assert [(a, v, w) for a, v, _, w in out["log"]] == want and mem[0xC000] == 1


def test_representation():
    prog = lifted("jumptab")[3]
    fns = prog.functions
    assert fns[0]["entry"] and not fns[1]["entry"]
    names = {op[0] for f in fns for op in ops_of(f)}
    assert {"CALL", "RETURN", "BRANCHIND", "STORE"} <= names
    for f in fns:
        for op in ops_of(f):
            mn, out, ins, _aux = op
            assert isinstance(mn, str) and isinstance(ins, list)
            for v in [out] + ins:
                assert v is None or (v[0] in "cru" and len(v) == 3)
    assert "CALL" in str(prog) and prog.entries == [fns[0]["pc"]]


@pytest.mark.parametrize("cycles", [False, True])
def test_stack_traffic(cycles):
    prog = lifted("pushpull", cycles=cycles)[3]
    st = prog.stats
    # Only the entry's return reads its caller's return address from memory.
    assert st["stack_private"] and st["slot_ops"] == 8 and st["stack_ops"] == 2
    callee = prog.functions[1]
    assert not [op for op in ops_of(callee) if op[0] in ("STORE", "LOAD") and op[3] != "volatile"]
    prog = lifted("tsx", cycles=cycles)[3]
    st = prog.stats
    assert not st["stack_private"] and st["page1_sites"] == 2 and st["stack_guards"] == 0
    assert st["stack_ops"] > 0
    assert any(op[0] == "LOAD" for op in ops_of(prog.functions[1]))


@pytest.mark.parametrize("cycles", [False, True])
def test_guarded_stack(cycles):
    img, pcs, calls, prog = lifted("guarded", cycles=cycles)
    st = prog.stats
    assert st["stack_private"] and st["page1_sites"] == 0
    assert st["stack_guards"] == 2 and st["stack_ops"] == 2 and st["slot_ops"] == 2
    assert not prog.deopts
    # The pointer moved into the live stack: the guard deopts before the load, with the
    # pushed byte and the elided return address in memory for the reference to read.
    differential(img, prog, pcs, calls + [(0, 0x01)] * 3 + calls)
    loop = labels_of("GUARDED")["loop"]
    assert prog.deopts == {loop: 3}
    log = prog.call(bytearray(img), 0, a=0x01, log=True)["log"]
    ra = labels_of("GUARDED")["sub"] - 5
    stack = [v for a, v, _, w in log if w and 0xC0B1 <= a <= 0xC0B5]
    assert stack == [0xFB, ra & 0xFF, ra >> 8, 0x01, 0x00]


def labels_of(src):
    return assemble(source(src))[1]


@pytest.mark.parametrize("cycles", [False, True])
def test_recursion(cycles):
    img, pcs, calls, prog = lifted("recurse", cycles=cycles)
    rec = [f["recursive"] for f in prog.functions]
    assert rec == [False, True] and prog.stats["stack_private"]
    assert prog.stats["stack_ops"] > 2
    differential(img, prog, pcs, calls)
    assert not prog.deopts


def writes_carry_before_call(fn):
    ops = ops_of(fn)
    call = next(i for i, op in enumerate(ops) if op[0] == "CALL")
    return any(op[1] == ["r", 8, 1] for op in ops[:call])


def test_callee_summaries_remove_dead_flags():
    assert not writes_carry_before_call(lifted("deadflags")[3].functions[0])
    assert writes_carry_before_call(lifted("deadflags", cycles=True)[3].functions[0])


def test_generated_code_entry_state():
    img, labels = assemble(GEN)
    for k in (3, 4):
        mem = bytearray(img)
        wholeprog.reference_call(mem, labels["gen"], a=k)
        facts = facts_for(mem, [labels["run"]], [(0, 0)] * 3)
        prog = wholeprog.lift(mem, [labels["run"]], [IO], facts)
        assert prog.stats["cells"] == 0
        differential(img, prog, [labels["run"]], [(0, 0)] * 3, mem=mem)
        assert not prog.deopts


@pytest.mark.parametrize("cycles", [False, True])
def test_forced_deopt_is_exact(cycles):
    img, pcs, calls, prog = lifted("toggle", fact_calls=[], cycles=cycles)
    differential(img, prog, pcs, calls)
    assert sum(prog.deopts.values()) > 0


@pytest.mark.parametrize("cycles", [False, True])
def test_code_write_invalidates(cycles):
    img, labels = assemble(INVALIDATE)
    pcs = [labels["entry"], labels["init"]]
    facts = facts_for(img, pcs, [(1, 0)] + [(0, 0)] * 3)
    prog = wholeprog.lift(img, pcs, [IO], facts, cycles=cycles)
    assert prog.stats["code_guards"] > 0
    differential(img, prog, pcs, [(1, 0)] + [(0, 0)] * 8)
    assert sum(prog.deopts.values()) >= 4


@pytest.mark.parametrize("cycles", [False, True])
def test_stack_wrap(cycles):
    img, labels = assemble(WRAP)
    pc = [labels["entry"]]
    prog = wholeprog.lift(img, pc, [IO], facts_for(img, pc, [(0, 0)] * 4), cycles=cycles)
    differential(img, prog, pc, [(0, 0)] * 6)
    assert sum(prog.deopts.values()) > 0


def test_cycle_budget():
    img, pcs, calls, prog = lifted("timing", cycles=True)
    differential(img, prog, pcs, calls, max_cycles=150)
    assert sum(prog.deopts.values()) > 0


def test_entry_register_guard():
    img, labels = assemble(PATCH)
    pc = labels["entry"]
    facts = facts_for(img, [pc], [(0, 0)] * 4)
    prog = wholeprog.lift(img, [(pc, {"a": [0]})], [IO], facts)
    differential(img, prog, [pc], [(0, 0)] * 2)
    assert not prog.deopts
    differential(img, prog, [pc], [(0, 1)])
    assert prog.deopts == {pc: 1}


def test_no_facts_is_exact():
    img, labels = assemble(JMPIND)
    pc = [labels["entry"]]
    prog = wholeprog.lift(img, pc, [IO])
    differential(img, prog, pc, [(0, a) for a in range(4)])
    assert sum(prog.deopts.values()) == 4


def test_matches_constant_code():
    img, _pcs, _calls, prog = lifted("toggle")
    mem = bytearray(img)
    assert prog.matches(mem)
    mem[_pcs[0]] ^= 0xFF
    assert not prog.matches(mem)


def test_facts_roundtrip():
    img, labels = assemble(TOGGLE)
    f = facts_for(img, [labels["entry"]], [(0, 0)] * 2)
    g = trace.Facts.loads(f.dumps())
    assert g == f and f.writes and f.edges and f.starts
    p1 = wholeprog.lift(img, [labels["entry"]], [IO], f)
    p2 = wholeprog.lift(img, [labels["entry"]], [IO], g.to_dict())
    assert p1.stats == p2.stats


def test_bank_facts_need_a_bank_map():
    """A flat lift ignores the configurations the machine saw each start run in."""
    img, pcs, calls, prog = lifted("toggle")
    f = facts_for(img, pcs, calls)
    seen = dataclasses.replace(f, banks={pc: [7] for pc in f.starts})
    flat = wholeprog.lift(img, pcs, [IO], seen)
    assert flat.stats == prog.stats
    differential(img, flat, pcs, calls)
    assert not flat.deopts


@pytest.mark.parametrize("cycles", [False, True])
def test_lift_many_matches_lift(cycles):
    jobs = []
    for name in ("patch", "jumptab", "nested"):
        src, entries, calls = PROGRAMS[name]
        img, labels = assemble(source(src))
        pcs = [labels[e] for e in entries]
        jobs.append((img, pcs, [IO], facts_for(img, pcs, calls)))
    many = wholeprog.lift_many(jobs, progress=False, cycles=cycles)
    assert all(p.cycles == cycles for p in many)
    assert [p.stats for p in many] == [wholeprog.lift(*j, cycles=cycles).stats for j in jobs]


def jennings_stream(img, pc, calls, cycles):
    mem = NativeMemory(bytes(img))
    mpu = NativeMPU(memory=mem)
    mpu.sp, mpu.p, mpu.processorCycles = 0xFF, 0x30, 0
    log = []
    mem.observe_data_access(
        on_read=lambda a: log.append((a, mem[a], mpu.processorCycles * cycles, False)),
        on_write=lambda a, v: log.append((a, v, mpu.processorCycles * cycles, True)),
        reads=range(*IO),
        writes=range(*IO),
    )
    for a in calls:
        mpu.run_to_rts(pc, a, 10_000_000)
    return log, mpu


@pytest.mark.parametrize("cycles", [False, True])
@pytest.mark.parametrize("name", ["patch", "toggle", "nested", "jumptab", "guarded"])
def test_matches_jennings(name, cycles):
    img, pcs, calls, prog = lifted(name, cycles=cycles)
    ref, mpu = jennings_stream(img, pcs[0], [a for _, a in calls], cycles)
    mem, log, st = bytearray(img), [], {"reg": None, "cycles": 0}
    for _, a in calls:
        o = prog.call(mem, 0, a=a, log=True, **st)
        log += o["log"]
        st = {"reg": list(o["reg"]), "cycles": o["cycles"] if cycles else 0}
    assert log == ref
    assert (mpu.a, mpu.x, mpu.y, mpu.sp) == tuple(st["reg"][:4])
    assert not cycles or mpu.processorCycles == st["cycles"]


@pytest.mark.parametrize("cycles", [False, True])
@pytest.mark.parametrize("name", sorted(UNSEEN))
def test_heights_from_facts(name, cycles):
    _img, _pcs, _calls, prog = lifted(name, cycles=cycles)
    st = prog.stats
    assert st["stack_private"] and st["unknown_heights"] == 0
    assert (st["split_nodes"] > 0) == (name in ("correlated", "sptable"))
    assert st["sp_dispatch"] == SP_DISPATCH.get(name, 0)
    seen, unseen = UNSEEN[name]
    img, pcs, calls, prog = lifted(name, fact_calls=[(0, a) for a in seen], cycles=cycles)
    assert prog.stats["stack_private"]
    differential(img, prog, pcs, [(0, a) for a in unseen] + calls)
    assert sum(prog.deopts.values()) >= len(unseen)


@pytest.mark.parametrize("cycles", [False, True])
def test_soft_deopt_resumes_with_folded_register(cycles):
    img, labels = assemble(CODEHIT)
    pc = [labels["entry"]]
    prog = wholeprog.lift(img, pc, [IO], facts_for(img, pc, [(0, 0)]), cycles=cycles)
    assert prog.stats["code_guards"] > 0
    differential(img, prog, pc, [(0, 0), (0, 1), (0, 0)])
    assert sum(prog.deopts.values()) > 0


@pytest.mark.parametrize("cycles", [False, True])
def test_data_written_in_unexecuted_code(cycles):
    img, pcs, calls, prog = lifted("dataincode", cycles=cycles)
    assert prog.stats["cells"] > 0
    differential(img, prog, pcs, calls)
    assert not prog.deopts
    facts = facts_for(img, pcs, calls)
    executed = {a + k for a in facts.starts for k in range(3)}
    facts.writes = {a: v for a, v in facts.writes.items() if a in executed}
    prog = wholeprog.lift(img, pcs, [IO], facts, cycles=cycles)
    differential(img, prog, pcs, calls)
    assert sum(prog.deopts.values()) == len(calls)


def test_cell_fixpoint_terminates():
    """A store that makes its own cell decode a jump back to it gains and loses the value it
    stores with each round of discovery."""
    img = bytearray(0x10000)
    img[0x1000:0x100A] = bytes([0xA9, 0x4C, 0x8D, 0x05, 0x10, 0xA9, 0x02, 0x10, 0x00, 0x60])
    out = []
    t = threading.Thread(target=lambda: out.append(wholeprog.lift(img, [0x1000])), daemon=True)
    t.start()
    t.join(30)
    assert out and out[0].stats["opcode_cells"] == 1


@pytest.mark.parametrize("cycles", [False, True])
@pytest.mark.parametrize("name", ["unexec", "frame", "ptable"])
def test_page1_stack_private(name, cycles):
    img, pcs, calls, prog = lifted(name, cycles=cycles)
    st = prog.stats
    assert st["stack_private"] and st["page1_sites"] == 0 and st["slot_ops"] > 0
    assert st["sp_guards"] == (name != "unexec")
    if name == "unexec":
        differential(img, prog, pcs, [(0, 0x80)] + calls)
    else:
        differential(img, prog, pcs, calls, reg=[0, 0, 0, 0xF0] + [0] * 12)
    assert sum(prog.deopts.values()) > 0


@pytest.mark.parametrize("cycles", [False, True])
def test_page1_range_guard(cycles):
    img, pcs, calls, prog = lifted("pxtable", cycles=cycles)
    st = prog.stats
    assert st["stack_private"] and st["slot_ops"] > 0 and st["stack_guards"] > 0
    assert (st["kept_bytes"], st["sp_guards"]) == (2, 1)
    differential(img, prog, pcs, [(0, 2)] + calls)
    assert sum(prog.deopts.values()) == 1


@pytest.mark.parametrize("cycles", [False, True])
@pytest.mark.parametrize("name", ["SPIN", "INDSPIN", "POPREC", "COUNT"])
def test_budget_ends_calls(name, cycles):
    img, labels = assemble(source(name))
    pc = [labels["entry"]]
    facts = trace.collect(img, lambda s: [s.call(pc[0]) for _ in range(2)], 100_000)
    prog = wholeprog.lift(img, pc, [IO], facts, cycles=cycles)
    assert (prog.stats["budget_checks"] > 0) == (not cycles)
    for cap, halt in ((100_000, "return" if name == "COUNT" else "cycles"), (1_000, "cycles")):
        differential(img, prog, pc, [(0, 0)] * 2, max_cycles=cap)
        assert prog.call(bytearray(img), 0, max_cycles=cap)["halt"] == halt


BANKED = {
    "bankrd": ("BANKRD", [0, 0], []),
    "romram": ("ROMRAM", [0, 1, 2], []),
    "romcallk": ("ROMCALLK", [0, 0], []),
    "bankseen": ("BANKSEEN", [0, 0], [1]),
}


def banked_image(name):
    """A banked program over the machine's power-on RAM: its code page and its data bytes."""
    from vagrant_moses.c64 import poweron_ram  # pylint: disable=import-outside-toplevel

    img, labels = assemble(source(BANKED[name][0]))
    base = bytearray(poweron_ram())
    for a in range(0x10000):
        if img[a] or 0x1000 <= a < 0x1100:
            base[a] = img[a]
    return base, labels["entry"]


@pytest.mark.skipif(not machine.core(), reason="needs vagrant-moses' native machine")
@pytest.mark.parametrize("cycles", [False, True])
@pytest.mark.parametrize("name", sorted(BANKED))
def test_banked_lift_matches_machine(name, cycles):
    img, pc = banked_image(name)
    _, seen, forced = BANKED[name]
    runs = [(pc, a) for a in forced + seen]
    res = c64.lift_check(img, pc, [(pc, a) for a in seen], runs, cycles=cycles)
    prog = res.prog
    assert res.bad is None, res.bad
    assert (sum(prog.deopts.values()) > 0) == bool(forced) and prog.stats["bank_dispatch"] > 0
    post = bytearray(img)
    post[c64.IDLE] = 0x60
    calls = [(0, a) for a in forced + seen]
    differential(post, prog, [pc], calls, bank=c64.bank_map(), volatile=[c64.SID])


def test_backend_selection(backend):
    img, lab = assemble(PATCH)
    pcs = [lab["entry"]]
    facts = facts_for(img, pcs, [(0, 0)] * 4)
    prog = wholeprog.lift(img, pcs, [IO], facts)
    assert prog.backend == backend
    assert prog.compile() > 0
    other = "interp" if backend == "jit" else "jit"
    runs = [prog.call(bytearray(img), 0, backend=b) for b in (backend, other)]
    assert runs[0] == runs[1]
    prog.backend = other
    assert prog.backend == other
    with pytest.raises(ValueError):
        prog.backend = "nope"
    with pytest.raises(ValueError):
        prog.call(bytearray(img), 0, backend="nope")
    assert wholeprog.lift(img, pcs, [IO], facts, cycles=True).compile() is None
    assert wholeprog.lift_many([(img, pcs, [IO], facts)], progress=False)[0].backend == backend


def test_session_matches_call():
    img, lab = assemble(GEN)
    pcs = [lab["gen"], lab["run"]]
    calls = [(0, 3), (1, 0), (0, 4), (1, 0)]
    prog = wholeprog.lift(img, pcs, [IO], facts_for(img, pcs, calls))
    m1, m2 = bytearray(img), bytearray(img)
    s = prog.session(m2)
    reg = None
    for e, a in calls:
        r = prog.call(m1, e, a=a, reg=reg)
        reg = r["reg"]
        assert s.call(e, a) == r["halt"]
        assert s.reg == reg
    assert m1 == m2
    with pytest.raises(ValueError):
        s.call(len(pcs))
    with pytest.raises(ValueError):
        prog.session(bytearray(10))


def test_reads_replay_volatile_reads_in_order():
    img = bytearray(0x10000)
    code = bytes.fromhex("ad05d4ae12d0ac05d4ad00c060")  # LDA $D405; LDX $D012; LDY $D405; LDA $C000
    img[0x1000 : 0x1000 + len(code)] = code
    img[0xD405] = 0x77
    kw = {"volatile": [(0xD000, 0xE000)], "log": True}
    o = wholeprog.reference_call(bytearray(img), 0x1000, reads=b"\x12\x34", **kw)
    assert list(o["reg"][:3]) == [0, 0x34, 0] and [e[:2] for e in o["log"]] == [
        (0xD405, 0x12),
        (0xD012, 0x34),
        (0xD405, 0),
    ]
    prog = wholeprog.lift(img, [0x1000], [(0xD000, 0xE000)])
    lifted = prog.call(bytearray(img), 0, reads=b"\x12\x34", log=True)["log"]
    assert [e[:2] for e in lifted] == [e[:2] for e in o["log"]]
    assert wholeprog.reference_call(bytearray(img), 0x1000, **kw)["reg"][2] == 0x77
