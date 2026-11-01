"""Whole-program lifting: lifted programs against the reference interpreter and jennings."""

import pytest

from jennings.devices.mpu6502 import NativeMPU
from jennings.memory import NativeMemory

import wholeprog_progs as W
from deity_informant import NATIVE, trace, wholeprog
from wholeprog_progs import GEN, INVALIDATE, IO, JMPIND, PATCH, TOGGLE, WRAP, assemble

pytestmark = pytest.mark.skipif(not NATIVE, reason="needs the native extension")

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
}


def source(name):
    return getattr(W, name)


def facts_for(img, pcs, calls):
    return trace.collect(img, lambda s: [s.call(pcs[e], a) for e, a in calls])


def differential(img, prog, pcs, calls, synth=True, max_cycles=10_000_000, mem=None):
    """Run ``calls`` on the reference and the lifted program; everything observable must
    match (RAM except the dead stack below SP). The final memory."""
    m1 = bytearray(img if mem is None else mem)
    m2 = bytearray(m1)
    s1 = s2 = {"reg": None, "cycles": 0}
    for n, (e, a) in enumerate(calls):
        kw = {"log": True, "synth": synth, "max_cycles": max_cycles}
        r = wholeprog.reference_call(m1, pcs[e], a=a, volatile=[IO], **kw, **s1)
        o = prog.call(m2, e, a=a, **kw, **s2)
        for k in ("log", "reg", "pc", "cycles", "halt"):
            assert r[k] == o[k], (n, k, r[k], o[k])
        sp = r["reg"][3]
        diff = [i for i in range(0x10000) if m1[i] != m2[i] and not 0x100 <= i <= 0x100 + sp]
        assert not diff, (n, [hex(i) for i in diff[:8]])
        s1 = {"reg": list(r["reg"]), "cycles": r["cycles"]}
        s2 = {"reg": list(o["reg"]), "cycles": o["cycles"]}
    return m1


def lifted(name, optimize=True, fact_calls=None):
    src, entries, calls = PROGRAMS[name]
    img, labels = assemble(source(src))
    pcs = [labels[e] for e in entries]
    facts = facts_for(img, pcs, calls if fact_calls is None else fact_calls)
    return img, pcs, calls, wholeprog.lift(img, pcs, [IO], facts, optimize=optimize)


@pytest.mark.parametrize("optimize", [True, False])
@pytest.mark.parametrize("name", sorted(PROGRAMS))
def test_exact_without_deopts(name, optimize):
    img, pcs, calls, prog = lifted(name, optimize)
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


def test_stack_traffic():
    prog = lifted("pushpull")[3]
    assert prog.stats["stack_private"]
    callee = prog.functions[1]
    assert not [op for op in ops_of(callee) if op[0] in ("STORE", "LOAD") and op[3] != "volatile"]
    prog = lifted("tsx")[3]
    assert not prog.stats["stack_private"]
    assert any(op[0] == "LOAD" for op in ops_of(prog.functions[1]))


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


def test_forced_deopt_is_exact():
    img, pcs, calls, prog = lifted("toggle", fact_calls=[])
    differential(img, prog, pcs, calls)
    assert sum(prog.deopts.values()) > 0


def test_code_write_invalidates():
    img, labels = assemble(INVALIDATE)
    pcs = [labels["entry"], labels["init"]]
    facts = facts_for(img, pcs, [(1, 0)] + [(0, 0)] * 3)
    prog = wholeprog.lift(img, pcs, [IO], facts)
    assert prog.stats["code_guards"] > 0
    differential(img, prog, pcs, [(1, 0)] + [(0, 0)] * 8)
    assert sum(prog.deopts.values()) >= 4


def test_stack_wrap_and_cycle_budget():
    img, labels = assemble(WRAP)
    pc = [labels["entry"]]
    prog = wholeprog.lift(img, pc, [IO], facts_for(img, pc, [(0, 0)] * 4))
    differential(img, prog, pc, [(0, 0)] * 6)
    img, pcs, calls, prog = lifted("timing")
    differential(img, prog, pcs, calls, max_cycles=150)


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


def test_lift_many_matches_lift():
    jobs = []
    for name in ("patch", "jumptab", "nested"):
        src, entries, calls = PROGRAMS[name]
        img, labels = assemble(source(src))
        pcs = [labels[e] for e in entries]
        jobs.append((img, pcs, [IO], facts_for(img, pcs, calls)))
    many = wholeprog.lift_many(jobs, progress=False)
    assert [p.stats for p in many] == [wholeprog.lift(*j).stats for j in jobs]


def jennings_stream(img, pc, calls):
    mem = NativeMemory(bytes(img))
    mpu = NativeMPU(memory=mem)
    mpu.sp, mpu.p, mpu.processorCycles = 0xFF, 0x30, 0
    log = []
    mem.observe_data_access(
        on_read=lambda a: log.append((a, mem[a], mpu.processorCycles, False)),
        on_write=lambda a, v: log.append((a, v, mpu.processorCycles, True)),
        reads=range(*IO),
        writes=range(*IO),
    )
    for a in calls:
        mpu.run_to_rts(pc, a, 10_000_000)
    return log, mpu


@pytest.mark.parametrize("name", ["patch", "toggle", "nested", "jumptab"])
def test_matches_jennings(name):
    img, pcs, calls, prog = lifted(name)
    ref, mpu = jennings_stream(img, pcs[0], [a for _, a in calls])
    mem, log, st = bytearray(img), [], {"reg": None, "cycles": 0}
    for _, a in calls:
        o = prog.call(mem, 0, a=a, log=True, **st)
        log += o["log"]
        st = {"reg": list(o["reg"]), "cycles": o["cycles"]}
    assert log == ref
    assert (mpu.a, mpu.x, mpu.y, mpu.sp, mpu.processorCycles) == (*st["reg"][:4], st["cycles"])
