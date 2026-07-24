"""§4 soundness: per-site proof records, the evidence-only tracked lemma, strict
(sound) mode, the proof report, and relational vector closure. Corpus-absent."""

import types

import pytest

from deity_informant import expr as E
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
    Cartesian {lo}x{hi} overflows the budget so static closure must give up."""
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
    mem[STUB] = 0x60  # RTS: every traced target returns cleanly
    mem[CTR] = 0x00
    for i in range(256):  # traced indices 1..FRAMES hit the stub; rest diverge
        lo, hi = (STUB & 0xFF, STUB >> 8) if 1 <= i <= FRAMES else (i, i)
        mem[TLO + i], mem[THI + i] = lo, hi
    return mem


@pytest.mark.parametrize(
    "name,kind",
    [("jump_table", "vector"), ("jmp_indirect", "vector"), ("smc_opcode", "opcode")],
)
def test_dispatch_sites_carry_proven_proofs(name, kind):
    p = next(q for q in G.players(1) if q.name == name)
    model, _ev = S.decompile(_img_from_player(p), _player_init(p), p.org, p.frames)
    assert model.proofs, "expected a dispatch/opcode proof site"
    assert all(pr.status == "proven" for pr in model.proofs.values())
    assert any(pr.kind == kind for pr in model.proofs.values())
    assert not model.evidence_sites


def test_evidence_site_recorded_with_lemma_and_replays():
    mem = _smc_vector_image()
    model, ev = S.decompile(mem, INIT, ORG, FRAMES)
    assert len(model.evidence_sites) == 1
    site = next(iter(model.evidence_sites))
    pr = model.proofs[site]
    assert pr.status == "evidence" and pr.kind == "jump"
    assert pr.lemma and ("too large" in pr.lemma or "cell" in pr.lemma)
    assert model.evidence_sites[site] == {STUB}
    w = S.Walker(model)
    assert w.run(FRAMES) == ev.wlog and bytes(w.m) == ev.end_mem


def test_sound_mode_fails_loudly_on_evidence_site():
    with pytest.raises(S.DecompileError) as exc:
        S.decompile(_smc_vector_image(), INIT, ORG, FRAMES, sound=True)
    assert "$1017" in str(exc.value) and "sound mode" in str(exc.value)


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


def test_proof_report_shape_and_sound_tag():
    p = next(q for q in G.players(1) if q.name == "jump_table")
    proven, _ev = S.decompile(_img_from_player(p), _player_init(p), p.org, p.frames)
    rep = S.proof_report(proven)
    assert rep["tally"].get("proven") and "evidence" not in rep["tally"]
    assert "[SOUND]" in S.format_report(proven)
    ev_model, _ = S.decompile(_smc_vector_image(), INIT, ORG, FRAMES)
    text = S.format_report(ev_model)
    assert "[SOUND]" not in text and "evidence" in text and "$1017" in text
