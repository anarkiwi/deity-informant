"""§4 soundness accounting: per-site proof records, the evidence-only tracked
lemma, strict (sound) mode, and the proof report. Corpus-absent (synthetic)."""

import pytest

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


def test_proof_report_shape_and_sound_tag():
    p = next(q for q in G.players(1) if q.name == "jump_table")
    proven, _ev = S.decompile(_img_from_player(p), _player_init(p), p.org, p.frames)
    rep = S.proof_report(proven)
    assert rep["tally"].get("proven") and "evidence" not in rep["tally"]
    assert "[SOUND]" in S.format_report(proven)
    ev_model, _ = S.decompile(_smc_vector_image(), INIT, ORG, FRAMES)
    text = S.format_report(ev_model)
    assert "[SOUND]" not in text and "evidence" in text and "$1017" in text
