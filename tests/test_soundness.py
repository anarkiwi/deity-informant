"""Observed-primary soundness: every committed site set is the trace-observed
set (runtime guards cover the rest); static analysis only certifies a guard
dead when its set EQUALS the observed set. Strict (--sound) mode, the
certification report, and relational vector closure. Corpus-absent."""

import types

import pytest

from deity_informant import expr as E
from deity_informant import frameprog
from deity_informant import structured as S

import _fuzzgen as G

SID = 0xD400
ORG = 0x1000
CTR = 0x1440
TLO, THI = 0x2000, 0x2100
STUB = 0x1300
INIT = 0x0F00
FRAMES = 6
REG = 4


def _rebuild(model):
    """The block model the frame program rebuilds from its own text alone."""
    text = frameprog.dumps(frameprog.program(model))
    return frameprog.block_model(frameprog.loads(text))


def _img_from_player(p):
    m = bytearray(0x10000)
    for a, v in p.image_data().items():
        m[a] = v
    if p.init is None:
        m[0x0F00] = 0x60
    return m


def _player_init(p):
    return p.init_org if p.init is not None else 0x0F00


def _smc_vector_image():
    """Self-modified JMP vector indexed by a counter that widens to TOP; the
    Cartesian {lo}x{hi} overflows the budget and the stub mutates a table byte
    (paired-index immutability refuses), so static closure must give up."""
    a = G.Asm(ORG)
    a.i("LDA", "imm", 0x2A).i("STA", "abs", SID + REG)
    a.i("INC", "abs", CTR)
    a.i("LDX", "abs", CTR)
    a.i("LDA", "absx", TLO).i("STA", "abs", ("L", "jmp", 1))
    a.i("LDA", "absx", THI).i("STA", "abs", ("L", "jmp", 2))
    a.label("jmp").i("JMP", "abs", 0x0000)
    prog = a.assemble()
    mem = bytearray(0x10000)
    for k, b in enumerate(prog):
        mem[ORG + k] = b
    mem[INIT] = 0x60
    mem[STUB : STUB + 4] = bytes((0xEE, 0x80, 0x20, 0x60))  # INC TLO+$80; RTS
    mem[CTR] = 0x00
    for i in range(256):  # traced indices 1..FRAMES hit the stub; rest diverge
        lo, hi = (STUB & 0xFF, STUB >> 8) if 1 <= i <= FRAMES else (i, i)
        mem[TLO + i], mem[THI + i] = lo, hi
    return mem


@pytest.mark.parametrize(
    "name,kind",
    [("jump_table", "vector"), ("jmp_indirect", "vector"), ("smc_opcode", "opcode")],
)
def test_dispatch_sites_commit_observed_sets(name, kind):
    """Observed-primary doctrine: the committed set at every site is exactly the
    trace-observed set; a static set wider than observed keeps the guard live
    (status ``observed``) and is recorded in the lemma, never serialized."""
    p = next(q for q in G.players(1) if q.name == name)
    model, _ev = S.decompile(_img_from_player(p), _player_init(p), p.org, p.frames)
    assert model.proofs, "expected a dispatch/opcode proof site"
    assert any(pr.kind == kind for pr in model.proofs.values())
    for site, pr in model.proofs.items():
        obs = model.pcs[site] if pr.kind == "opcode" else model.ev_targets[site]
        assert set(pr.targets) == set(obs)
        assert pr.status == "certified" or pr.lemma


def test_uncertified_site_keeps_live_guard_and_replays():
    """Static closure gives up: the site commits its observed set with a live
    guard and a lemma naming the static refusal; replay stays bit-exact."""
    mem = _smc_vector_image()
    model, ev = S.decompile(mem, INIT, ORG, FRAMES)
    live = {s: p for s, p in model.proofs.items() if p.status == "observed"}
    assert len(live) == 1
    site, pr = next(iter(live.items()))
    assert pr.kind == "jump"
    assert pr.lemma and ("too large" in pr.lemma or "cell" in pr.lemma or "observed" in pr.lemma)
    assert set(pr.targets) == {STUB} == set(model.dyn_targets[site])
    w = S.Walker(model)
    assert w.run(FRAMES) == ev.wlog and bytes(w.m) == ev.end_mem


def test_sound_mode_fails_loudly_on_guard_live_site():
    with pytest.raises(S.DecompileError) as exc:
        S.decompile(_smc_vector_image(), INIT, ORG, FRAMES, sound=True)
    assert "$1017" in str(exc.value) and "sound mode" in str(exc.value)


def _certified_image():
    """SMC'd JMP fully driven over its 4-entry table: static set == observed."""
    m = bytearray(0x10000)
    m[0x0F00] = 0x60
    for k in range(5):
        m[0x1030 + 8 * k : 0x1036 + 8 * k] = bytes((0xA9, 0x11 * (k + 1), 0x8D, k, 0xD4, 0x60))
    m[0x1020:0x1023] = bytes((0x4C, 0x30, 0x10))
    m[0x1100:0x1108] = bytes((0x30, 0x38, 0x40, 0x48) * 2)
    m[0x1108:0x1110] = bytes((0x10,) * 8)
    code = [0xE6, 0xF0, 0xA5, 0xF0, 0x29, 0x03, 0xAA, 0xBD, 0x00, 0x11, 0x8D, 0x21, 0x10]
    code += [0xBD, 0x08, 0x11, 0x8D, 0x22, 0x10, 0x4C, 0x20, 0x10]
    m[0x1000 : 0x1000 + len(code)] = bytes(code)
    return m


def test_certified_guard_dead_passes_sound_mode():
    """Certification: the static (paired-index) set equals the observed set, so
    the guard is provably dead and --sound succeeds."""
    model, ev = S.decompile(_certified_image(), INIT, ORG, 12, sound=True)
    pr = model.proofs[0x1020]
    assert pr.status == "certified" and pr.lemma.startswith("paired-index")
    assert set(pr.targets) == set(model.ev_targets[0x1020])
    w = S.Walker(model)
    assert w.run(12) == ev.wlog


def test_walker_guard_faults_outside_observed_target_set():
    """The model walker's dynamic-transfer guard fires for an out-of-set target,
    carrying the site pc and the target."""
    model, _ev = S.decompile(_certified_image(), INIT, ORG, 12)
    site = 0x1020
    blk = model.blocks[next(k for k in model.blocks if model.blocks[k].pcs[-1] == site)]
    w = S.Walker(model)
    with pytest.raises(S.WalkError, match=r"\$2345 at \$1020 outside observed set"):
        w._guard(blk, 0x2345)  # pylint: disable=protected-access


PBUF = 0x1500


def _pending_vector_image():
    """Self-patched indirect JMP whose pointer value set spans 12 play-written
    pointer pairs: cell resolution outruns the closure rounds (Army_Moves shape).
    Returns ``(mem, vector site pc)``."""
    a = G.Asm(ORG)
    a.i("LDA", "imm", 0x2A).i("STA", "abs", SID + REG)
    a.i("INC", "abs", CTR)
    a.i("LDX", "abs", CTR)
    a.i("LDA", "absx", TLO).i("STA", "abs", ("L", "vec", 1))
    a.i("LDA", "absx", THI).i("STA", "abs", ("L", "vec", 2))
    a.i("LDA", "imm", STUB & 0xFF)
    for k in range(12):
        a.i("STA", "abs", PBUF + 2 * k)
    a.i("JSR", "abs", STUB + 8)  # block split: lo/hi pointer stores never pair
    a.i("LDA", "imm", STUB >> 8)
    for k in range(12):
        a.i("STA", "abs", PBUF + 2 * k + 1)
    a.label("vec").i("JMP", "ind", PBUF)
    prog = a.assemble()
    mem = bytearray(0x10000)
    for k, b in enumerate(prog):
        mem[ORG + k] = b
    mem[INIT] = 0x60
    mem[STUB] = 0x60
    mem[STUB + 8] = 0x60
    for i in range(256):
        mem[TLO + i] = (PBUF + 2 * (i % 12)) & 0xFF
        mem[THI + i] = PBUF >> 8
    for k in range(12):
        mem[PBUF + 2 * k], mem[PBUF + 2 * k + 1] = STUB & 0xFF, STUB >> 8
    return mem, a.labels["vec"]


def test_pending_vector_resolution_commits_observed_with_live_guard():
    """A resolution still pending when closure rounds exhaust commits the
    observed set with a live guard and a tracked lemma; the guard survives the
    artifact round trip, so the rebuilt model is guarded to the same targets."""
    mem, site = _pending_vector_image()
    model, ev = S.decompile(mem, INIT, ORG, FRAMES)
    pr = model.proofs[site]
    assert pr.status == "observed" and pr.kind == "vector"
    assert set(pr.targets) == {STUB} == set(model.dyn_targets[site])
    assert "unclosed cell" in pr.lemma or "unresolved" in pr.lemma or "observed" in pr.lemma
    w = S.Walker(model)
    assert w.run(FRAMES) == ev.wlog and bytes(w.m) == ev.end_mem
    rebuilt = _rebuild(model)
    assert set(rebuilt.dyn_targets[site]) == {STUB}
    assert rebuilt.proofs[site].status == "observed"
    assert S.Walker(rebuilt).run(FRAMES) == ev.wlog


def _vector_analysis(idx_addr):
    """Analysis over one block that patches a JMP vector from split tables at
    $2000/$2100 indexed by ``idx_addr`` (an address expr in register A)."""
    tlo, thi, ptr = 0x2000, 0x2100, 0x1010
    mem = bytearray(0x10000)
    for i in range(256):
        mem[tlo + i], mem[thi + i] = i, (0x30 + (i >> 1)) & 0xFF
    lo_addr = E.op("INT_ADD", (idx_addr, E.konst(tlo, 2)), 2)
    hi_addr = E.op("INT_ADD", (idx_addr, E.konst(thi, 2)), 2)
    events = [
        ("ld", 0, lo_addr),
        ("st", E.konst(ptr, 2), E.uni(0, 1)),
        ("ld", 1, hi_addr),
        ("st", E.konst(ptr + 1, 2), E.uni(1, 1)),
    ]
    lo_byte = E.mem(E.konst(ptr, 2), 1)
    hi_byte = E.mem(E.konst(ptr + 1, 2), 1)
    vec = E.op(
        "INT_OR",
        (
            E.op("INT_ZEXT", (lo_byte,), 2),
            E.op("INT_LEFT", (E.op("INT_ZEXT", (hi_byte,), 2), E.konst(8, 1)), 2),
        ),
        2,
    )
    blk = S.Block(0x1000, 0x4C, [0x1000], events, ("jmpd", vec), [E.reg(i) for i in range(16)])
    model = types.SimpleNamespace(
        blocks={(0x1000, 0x4C): blk}, written={ptr, ptr + 1}, mem0=bytes(mem)
    )
    ana = S.Analysis(model)
    return ana, blk, vec, mem, tlo, thi


def test_relational_closure_enumerates_correlated_pairs():
    idx = E.op("INT_ZEXT", (E.reg(0),), 2)  # tables indexed by A
    ana, blk, vec, mem, tlo, thi = _vector_analysis(idx)
    aset = {0, 5, 17, 200, 255}
    ana.R[((0x1000, 0x4C), 0)] = set(aset)
    got = ana._relational_targets(blk, vec)  # pylint: disable=protected-access
    assert got == {mem[tlo + a] | (mem[thi + a] << 8) for a in aset}


def test_relational_closure_refuses_top_index():
    idx = E.op("INT_ZEXT", (E.reg(0),), 2)
    ana, blk, vec, _m, _l, _h = _vector_analysis(idx)
    ana.R[((0x1000, 0x4C), 0)] = S.TOP
    with pytest.raises(S.DecompileError):
        ana._relational_targets(blk, vec)  # pylint: disable=protected-access


def test_relational_closure_refuses_volatile_index():
    ana, blk, vec, _m, _l, _h = _vector_analysis(E.mem(E.konst(0xD41B, 2), 2))  # osc3: volatile
    ana.R[((0x1000, 0x4C), 0)] = {0, 1, 2}
    with pytest.raises(S.DecompileError):
        ana._relational_targets(blk, vec)  # pylint: disable=protected-access


def _counted_loop_image():
    """A DEY/BPL counted loop that increments a cell $2000 each pass."""
    a = G.Asm(ORG)
    a.i("LDY", "imm", 0x05)
    a.i("LDA", "imm", 0x78).i("STA", "abs", 0x2000)
    a.label("loop")
    a.i("INC", "abs", 0x2000)
    a.i("DEY")
    a.i("BPL", "rel", ("L", "loop"))
    a.i("LDA", "abs", 0x2000).i("STA", "abs", SID + 4).i("RTS")
    mem = bytearray(0x10000)
    for k, b in enumerate(a.assemble()):
        mem[ORG + k] = b
    mem[INIT] = 0x60
    return mem


def test_natural_loops_finds_counted_loop():
    model, _ev = S.decompile(_counted_loop_image(), INIT, ORG, 2)
    loops = model.analysis.natural_loops()
    assert loops, "expected a natural loop"
    dec = ("op", "INT_ADD", (E.reg(2), E.konst(0xFF, 1)), 1)  # DEY: Y = Y - 1
    counters = [
        i
        for _hdr, body in loops.items()
        for bkey in body
        for i in range(16)
        if model.blocks[bkey].regs[i] == ("op", "INT_ADD", (E.reg(i), E.konst(0xFF, 1)), 1)
    ]
    assert dec in [model.blocks[b].regs[2] for body in loops.values() for b in body]
    assert counters, "expected a decremented loop counter"
    for hdr, body in loops.items():
        assert hdr in body and model.blocks[hdr].term[0] == "br"


def test_affine_bound_narrows_monotone_loop_cell():
    model, _ev = S.decompile(_counted_loop_image(), INIT, ORG, 2)
    ana = model.analysis
    ana.close({0x2000})
    v = ana.S.get(0x2000)
    assert v is not S.TOP  # was widened to TOP without the trip bound
    assert v <= set(range(0x78, 0x7F))  # H in [$78, $7E] via H + Y = K, Y0 = 5
    assert 0x2000 in ana._pinned


def _nested_loop_image():
    """An outer DEY/BPL loop incrementing $2000, with an inner DEX/BNE loop that
    touches neither (Bionic's copy-loop shape, constant outer counter)."""
    a = G.Asm(ORG)
    a.i("LDY", "imm", 0x03).i("LDA", "imm", 0x78).i("STA", "abs", 0x2000)
    a.label("outer").i("LDX", "imm", 0x04)
    a.label("inner").i("DEX").i("BNE", "rel", ("L", "inner"))
    a.i("INC", "abs", 0x2000).i("DEY").i("BPL", "rel", ("L", "outer"))
    a.i("LDA", "abs", 0x2000).i("STA", "abs", SID + 4).i("RTS")
    mem = bytearray(0x10000)
    for k, b in enumerate(a.assemble()):
        mem[ORG + k] = b
    mem[INIT] = 0x60
    return mem


def test_affine_bound_handles_nested_loop():
    """Leader-split loop detection recovers the outer loop past the overlapping
    entry and inner loop, so the trip bound applies to the nested case."""
    model, _ev = S.decompile(_nested_loop_image(), INIT, ORG, 2)
    ana = model.analysis
    loops = {tuple(sorted(hex(b[0]) for b in body)) for body in ana.natural_loops().values()}
    assert any(len(body) > 1 for body in loops)  # a nested (multi-block) loop found
    ana.close({0x2000})
    v = ana.S.get(0x2000)
    assert v is not S.TOP and v <= set(range(0x78, 0x7D))  # H in [$78, $7C], Y0 = 3


def test_proof_report_shape_and_sound_tag():
    certified, _ev = S.decompile(_certified_image(), INIT, ORG, 12)
    rep = S.proof_report(certified)
    assert rep["tally"].get("certified") and "observed" not in rep["tally"]
    assert "[SOUND]" in S.format_report(certified)
    live_model, _ = S.decompile(_smc_vector_image(), INIT, ORG, FRAMES)
    text = S.format_report(live_model)
    assert "[SOUND]" not in text and "observed" in text and "$1017" in text


def _opcode_top_image():
    """Opcode cell aliased by a computed store through a TOP pointer whose value
    also cannot resolve (the Athena shape): closure must give TOP for the cell,
    so the committed observed set keeps its guard live."""
    cell = 0x1030
    m = bytearray(0x10000)
    m[INIT] = 0x60
    a = G.Asm(ORG)
    a.i("INC", "abs", CTR)  # widens to TOP across frames
    a.i("LDA", "abs", CTR).i("STA", "zp", 0xFB).i("STA", "zp", 0xFC)
    a.i("LDY", "imm", 0x00)
    a.i("LDA", "indy", 0xFB).i("STA", "indy", 0xFB)  # unresolvable value, TOP range
    a.i("LDA", "imm", 0xEA).i("STA", "abs", cell)
    a.i("JMP", "abs", cell)
    code = a.assemble()
    m[ORG : ORG + len(code)] = code
    m[cell] = 0xEA
    m[cell + 1] = 0x60
    return m, cell


# ---- run-to-recurrence closure certifier ---------------------------------------
CT16 = 0x1600
TLO2, THI2 = 0x2000, 0x2008


def _closure_image(counter16=False):
    """Masked-counter SMC JMP over cross-page handlers with a value-preserving
    table self-write: paired refuses, the per-cell Cartesian (16) exceeds the
    observed set, so only recurrence can certify. ``counter16`` adds a
    monotone 16-bit counter that prevents recurrence."""
    m = bytearray(0x10000)
    m[INIT] = 0x60
    handlers = (0x1130, 0x1238, 0x1340, 0x1548)
    for k, h in enumerate(handlers):
        m[h : h + 6] = bytes((0xA9, 0x11 * (k + 1), 0x8D, k, 0xD4, 0x60))
        m[TLO2 + k], m[THI2 + k] = h & 0xFF, h >> 8
    a = G.Asm(ORG)
    a.i("INC", "abs", CTR)
    if counter16:
        a.i("INC", "abs", CT16).i("BNE", "rel", ("L", "skip")).i("INC", "abs", CT16 + 1)
        a.label("skip")
    a.i("LDA", "abs", TLO2 + 3).i("STA", "abs", TLO2 + 3)
    a.i("LDA", "abs", CTR).i("AND", "imm", 0x03).i("TAX")
    a.i("LDA", "absx", TLO2).i("STA", "abs", ("L", "jmp", 1))
    a.i("LDA", "absx", THI2).i("STA", "abs", ("L", "jmp", 2))
    a.label("jmp").i("JMP", "abs", handlers[0])
    code = a.assemble()
    m[ORG : ORG + len(code)] = code
    return m, a.labels["jmp"]


def test_closure_recurrence_certifies_and_completes_observed_sets():
    """Recurrence closes the observed sets for the infinite run: the 2-frame
    window sees 2 of 4 arms, the extension completes them, and every guard
    certifies with the horizon in the lemma."""
    m, site = _closure_image()
    base, _ev = S.decompile(bytearray(m), INIT, ORG, 2)
    assert base.closure is None and base.proofs[site].status == "observed"
    assert len(base.dyn_targets[site]) == 2
    model, ev = S.decompile(bytearray(m), INIT, ORG, 2, close=True)
    clo = model.closure
    assert clo.recur is not None and clo.recur - clo.first == 256
    assert len(model.dyn_targets[site]) == 4
    assert all(p.status == "certified" for p in model.proofs.values())
    assert model.proofs[site].lemma.startswith("closure: frame %d" % clo.recur)
    w = S.Walker(model)
    assert w.run(2) == ev.wlog and bytes(w.m) == ev.end_mem
    assert "closure: recurrence at frame" in S.format_report(model)
    rebuilt = _rebuild(model)  # extension-grown sets ride evidence { closure }
    assert len(rebuilt.dyn_targets[site]) == 4
    assert S.Walker(rebuilt).run(2) == ev.wlog


def test_sound_mode_composes_with_closure():
    m, site = _closure_image()
    with pytest.raises(S.DecompileError, match="sound mode"):
        S.decompile(bytearray(m), INIT, ORG, 2, sound=True)
    model, _ev = S.decompile(bytearray(m), INIT, ORG, 2, sound=True, close=True)
    assert model.proofs[site].status == "certified"


def test_closure_cap_without_recurrence_keeps_guards_live():
    """A monotone 16-bit counter never recurs within the cap: certification
    never fires, the diagnosis names the still-changing cells, and sound mode
    still fails."""
    m, site = _closure_image(counter16=True)
    model, ev = S.decompile(bytearray(m), INIT, ORG, 8, close=True, close_cap=600)
    clo = model.closure
    assert clo.recur is None and clo.first is None
    assert "no recurrence within 600" in clo.note
    changed = {a for a, _w, _e in clo.diag}
    assert CT16 in changed and CT16 + 1 in changed
    assert model.proofs[site].status == "observed"
    w = S.Walker(model)
    assert w.run(8) == ev.wlog
    text = S.format_report(model)
    assert "no recurrence within 600 frames" in text and "still changing" in text
    with pytest.raises(S.DecompileError, match="sound mode"):
        S.decompile(bytearray(m), INIT, ORG, 8, sound=True, close=True, close_cap=600)


def test_opcode_cell_top_commits_observed_set_with_live_guard():
    m, cell = _opcode_top_image()
    model, ev = S.decompile(bytearray(m), INIT, ORG, FRAMES)
    pr = model.proofs[cell]
    assert pr.status == "observed" and pr.kind == "opcode"
    assert model.dispatch_sets[cell] == model.pcs[cell] == {0xEA}
    w = S.Walker(model)
    assert w.run(FRAMES) == ev.wlog and bytes(w.m) == ev.end_mem
    bad = bytearray(model.mem0)
    bad[cell] = 0x02  # JAM: outside the observed set
    with pytest.raises(S.WalkError):
        model.lookup(cell, bad)
    with pytest.raises(S.DecompileError, match="sound mode"):
        S.decompile(bytearray(m), INIT, ORG, FRAMES, sound=True)
