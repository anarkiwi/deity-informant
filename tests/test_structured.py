"""Structured decompiler: full-length real-tune acceptance (cycle-stamped
bit-exact model replay), fuzz-corpus development checks, loud faults.
Text-layer laws live in test_sidprog."""

from pathlib import Path

import pytest

from deity_informant import c64
from deity_informant import sidprog
from deity_informant import structured as S
from deity_informant.c64 import load_psid

import _fuzzgen as G

from _corpus import corpus_params

HVSC = Path(__file__).resolve().parent.parent / ".oracle-cache" / "hvsc"
SONGLENGTHS = HVSC / "Songlengths.md5"

_PLAYERS = G.players(3)
_IDS = [f"{p.name}-{p.seed[1]}" for p in _PLAYERS]


def _image(p):
    m = bytearray(0x10000)
    for a, v in p.image_data().items():
        m[a] = v
    if p.init is None:
        m[0x0F00] = 0x60  # RTS: empty init
    return m


def _init(p):
    return p.init_org if p.init is not None else 0x0F00


def _verify(mem, init, play, frames, subtune=0, img=None):
    model, ev = S.decompile(mem, init, play, frames, subtune, img=img)
    assert model.prologue == ev.prologue  # init's SID writes, order-preserved
    for site, pr in model.proofs.items():
        obs = model.pcs.get(site) if pr.kind == "opcode" else model.ev_targets.get(site)
        assert set(pr.targets) == set(obs or ()), "committed set != observed at $%04X" % site
    w = S.Walker(model)
    assert w.run(frames) == ev.wlog  # play-phase log from the post-init image
    assert bytes(w.m) == ev.end_mem
    return model


@pytest.mark.parametrize("p", _PLAYERS, ids=_IDS)
def test_fuzz_walker_bit_exact(p):
    """Development aid (acceptance is the real-tune gate): every idiom class
    replays with identical cycle-stamped log, end memory, and end registers."""
    _verify(_image(p), _init(p), p.org, p.frames)


def test_opcode_byte_outside_observed_set_faults():
    """Observed-primary: the committed dispatch set IS the observed opcode set;
    any other byte at the cell faults in the walker."""
    p = next(q for q in _PLAYERS if q.name == "smc_opcode")
    model, _ev = S.decompile(_image(p), _init(p), p.org, p.frames)
    (site,) = {pc for pc in model.dispatch_pcs if pc >= p.org}
    assert model.dispatch_sets[site] == model.pcs[site]
    m = bytearray(model.mem0)
    m[site] = 0x02  # JAM: never observed at the cell
    with pytest.raises(S.WalkError):
        model.lookup(site, m)


def test_collect_unreachable_drops_residue_blocks_and_site_records():
    """Blocks outside the final-closure reachable set are dropped along with
    their stale dyn_targets/proofs entries; replay is untouched."""
    p = next(q for q in _PLAYERS if q.name == "jump_table")
    model, ev = S.decompile(_image(p), _init(p), p.org, p.frames)
    baseline = set(model.blocks)
    model.build(0x0F00, 0x60)  # init RTS: never reachable from play
    model.dyn_targets[0x0F00] = []
    model.proofs[0x0F00] = S.Proof(0x0F00, "jump", "observed", (), "residue")
    S.collect_unreachable(model)
    assert (0x0F00, 0x60) not in model.blocks and not model.variants(0x0F00)
    assert 0x0F00 not in model.dyn_targets and 0x0F00 not in model.proofs
    assert set(model.blocks) == baseline
    w = S.Walker(model)
    assert w.run(p.frames) == ev.wlog


def test_dynamic_jump_onto_fallthrough_is_observed():
    """A patched JMP landing exactly on the next instruction is an observed
    edge (recorded despite equalling fallthrough): the committed set carries
    it and the standalone text replays without a guard fault."""
    mem = bytearray(0x10000)
    mem[0x0F00] = 0x60
    code = [0xA9, 0x0A, 0x8D, 0x08, 0x10, 0xA9, 0x41]  # patch JMP operand lo
    code += [0x4C, 0x0A, 0x10]  # $1007: JMP $100A == fallthrough
    code += [0x8D, 0x00, 0xD4, 0x60]  # $100A: STA $D400; RTS
    mem[0x1000 : 0x1000 + len(code)] = bytes(code)
    model, ev = S.decompile(mem, 0x0F00, 0x1000, 3)
    assert 0x100A in model.ev_targets[0x1007] and 0x100A in model.dyn_targets[0x1007]
    w = S.Walker(model)
    assert w.run(3) == ev.wlog
    tm = sidprog.parse(sidprog.emit(model))
    assert tm.run(3) == ev.wlog


def test_dynamic_branch_onto_fallthrough_replays():
    """A patched branch whose displacement resolves to zero lands on its own
    fallthrough — a static edge, so the dynamic-target guard admits it."""
    mem = bytearray(0x10000)
    mem[0x0F00] = 0x60
    code = [0xA9, 0x00, 0x8D, 0x08, 0x10, 0xA9, 0x01, 0xD0, 0x00]  # BNE +0 (patched)
    code += [0x8D, 0x00, 0xD4, 0x60]
    mem[0x1000 : 0x1000 + len(code)] = bytes(code)
    model, ev = S.decompile(mem, 0x0F00, 0x1000, 3)
    w = S.Walker(model)
    assert w.run(3) == ev.wlog
    tm = sidprog.parse(sidprog.emit(model))
    assert tm.run(3) == ev.wlog


def test_cia_icr_read_modeled_as_zero_source():
    """$DC0D reads are constant-0 under the per-frame driver, exactly as in
    PcodeVM; the decompiled model replays them rather than refusing."""
    mem = bytearray(0x10000)
    mem[0x0F00] = 0x60  # init: RTS
    mem[0x1000:0x1006] = bytes((0xAD, 0x0D, 0xDC, 0x8D, 0x00, 0xD4))  # LDA $DC0D; STA $D400
    mem[0x1006] = 0x60
    model, ev = S.decompile(mem, 0x0F00, 0x1000, 2)
    w = S.Walker(model)
    assert w.run(2) == ev.wlog


# ---- paired-index lemma (cross-block single-writer-pair) -----------------------
def _paired_base():
    """Dispatch skeleton: SMC'd ``JMP`` at $1020 (operands $1021/$1022), five
    handlers, split lo/hi tables at $1100/$1108."""
    m = bytearray(0x10000)
    m[0x0F00] = 0x60
    for k in range(5):
        m[0x1030 + 8 * k : 0x1036 + 8 * k] = bytes((0xA9, 0x11 * (k + 1), 0x8D, k, 0xD4, 0x60))
    m[0x1020:0x1023] = bytes((0x4C, 0x30, 0x10))
    m[0x1100:0x1108] = bytes((0x30, 0x38, 0x40, 0x48) * 2)
    m[0x1108:0x1110] = bytes((0x10,) * 8)
    return m


def _paired_verify(m, frames):
    model, ev = S.decompile(m, 0x0F00, 0x1000, frames)
    w = S.Walker(model)
    assert w.run(frames) == ev.wlog and bytes(w.m) == ev.end_mem
    return model


def test_paired_cross_block_writer_pair_proves():
    """Operand stores in a predecessor block of the dispatch block: the paired
    lemma zips the tables over the index domain (4 targets, not 16)."""
    m = _paired_base()
    code = [0xE6, 0xF0, 0xA5, 0xF0, 0x29, 0x03, 0xAA, 0xBD, 0x00, 0x11, 0x8D, 0x21, 0x10]
    code += [0xBD, 0x08, 0x11, 0x8D, 0x22, 0x10, 0xA5, 0xF0, 0x29, 0x01, 0xF0, 0x07]
    code += [0x4C, 0x20, 0x10]
    m[0x1000 : 0x1000 + len(code)] = bytes(code)
    model = _paired_verify(m, 12)
    pr = model.proofs[0x1020]
    assert pr.status == "certified" and pr.lemma.startswith("paired-index")
    assert set(pr.targets) == {0x1030, 0x1038, 0x1040, 0x1048}


def test_paired_unpaired_stores_refuse():
    """lo and hi stored in different blocks: the pairing premise refuses with
    the per-site diagnostic (the site falls back to per-cell closure)."""
    m = _paired_base()
    code = [0xE6, 0xF0, 0xA5, 0xF0, 0x29, 0x03, 0xAA, 0xBD, 0x00, 0x11, 0x8D, 0x21, 0x10]
    code += [0xA5, 0xF0, 0x29, 0x01, 0xF0, 0x00, 0xBD, 0x08, 0x11, 0x8D, 0x22, 0x10]
    code += [0x4C, 0x20, 0x10]
    m[0x1000 : 0x1000 + len(code)] = bytes(code)
    model = _paired_verify(m, 12)
    assert "stores $1021 but not $1022" in model.analysis.pair_refusals[0x1020]
    assert not model.proofs[0x1020].lemma.startswith("paired-index")
    assert set(model.proofs[0x1020].targets) == set(model.ev_targets[0x1020])


def test_paired_mutable_table_spill_refuses():
    """A value-preserving self-write makes a table cell mutable: the
    immutability gate refuses rather than reading through model.written."""
    m = _paired_base()
    code = [0xE6, 0xF0, 0xAD, 0x03, 0x11, 0x8D, 0x03, 0x11, 0xA5, 0xF0, 0x29, 0x03, 0xAA]
    code += [0xBD, 0x00, 0x11, 0x8D, 0x21, 0x10, 0xBD, 0x08, 0x11, 0x8D, 0x22, 0x10]
    code += [0xF0, 0x05, 0x4C, 0x20, 0x10]
    m[0x1000 : 0x1000 + len(code)] = bytes(code)
    model = _paired_verify(m, 12)
    assert "table read hits mutable/IO cell $1103" in model.analysis.pair_refusals[0x1020]
    assert not model.proofs[0x1020].lemma.startswith("paired-index")


def test_paired_constant_pair_and_indexed_mix_proves():
    """One writer stores a constant pair, another an indexed pair: both satisfy
    the premise and the target set is their union."""
    m = _paired_base()
    code = [0xE6, 0xF0, 0xA5, 0xF0, 0x29, 0x01, 0xF0, 0x58, 0xA5, 0xF0, 0x4A, 0x29, 0x03]
    code += [0xAA, 0xBD, 0x00, 0x11, 0x8D, 0x21, 0x10, 0xBD, 0x08, 0x11, 0x8D, 0x22, 0x10]
    code += [0x4C, 0x20, 0x10]
    m[0x1000 : 0x1000 + len(code)] = bytes(code)
    arm = [0xA9, 0x50, 0x8D, 0x21, 0x10, 0xA9, 0x10, 0x8D, 0x22, 0x10, 0x4C, 0x20, 0x10]
    m[0x1060 : 0x1060 + len(arm)] = bytes(arm)
    model = _paired_verify(m, 16)
    pr = model.proofs[0x1020]
    assert pr.status == "certified" and pr.lemma.startswith("paired-index")
    assert set(pr.targets) == {0x1030, 0x1038, 0x1040, 0x1048, 0x1050}


@pytest.mark.parametrize(
    "vec,kernal", [(0x0314, True), (0xFFFE, False), (0x0318, False)], ids=["cinv", "hw", "nmi"]
)
def test_handler_driven_entry_replays_bit_exact(vec, kernal):
    """``play == 0``: the installed vector's dispatch stub is the frame entry, and
    the handler's RTI is data flow plus a guarded goto (both executors agree)."""
    mem, init = G.irq_image(vec, kernal)
    model = _verify(mem, init, 0, 8, img=G.IRQ_IMAGE)
    assert model.play == c64.IRQ_DISPATCH
    assert [v for _c, _r, v in S.Walker(model).run(8)] == list(range(1, 9))
    rti = next(blk for blk in model.blocks.values() if blk.term[0] == "jmpd")
    assert set(model.dyn_targets[rti.pcs[-1]]) == {c64.IRQ_RETURN}


def test_play_zero_without_an_installed_vector_refuses():
    """No vector, no per-frame entry: the diagnostic names the vectors, not $0000."""
    mem = bytearray(0x10000)
    mem[0x0F00] = 0x60  # init: RTS, installing nothing
    with pytest.raises(S.DecompileError, match="no interrupt vector"):
        S.decompile(mem, 0x0F00, 0, 4)


def test_play_zero_init_that_never_returns_names_the_driver_cadence(monkeypatch):
    """The known deferred class: init idles until its own handler fires."""
    monkeypatch.setattr(S, "_GUARD", 1000)
    mem = bytearray(0x10000)
    mem[0x0F00:0x0F03] = bytes((0x4C, 0x00, 0x0F))  # init: JMP * (idle loop)
    with pytest.raises(RuntimeError, match="init never returned"):
        S.decompile(mem, 0x0F00, 0, 4)


def test_play_zero_with_a_zeroed_vector_refuses():
    mem, init = G.irq_image(handler=0x0000)
    with pytest.raises(S.DecompileError, match=r"vector \$0314 installed as \$0000"):
        S.decompile(mem, init, 0, 4, img=G.IRQ_IMAGE)


def test_load_image_over_the_dispatch_stub_refuses():
    """The tune's own bytes live under the stub: refuse rather than fake them."""
    mem, init = G.irq_image()
    with pytest.raises(S.DecompileError, match=r"dispatch stub at \$FF33"):
        S.decompile(mem, init, 0, 4, img=(0xFF00, 0x10000))


def _tunes():
    return [pytest.param(path, sub, secs, id=path.stem) for path, sub, secs in corpus_params(HVSC)]


@pytest.mark.parametrize("sid,subtune,secs", _tunes())
def test_real_tune_full_length_cycle_exact(sid, subtune, secs):
    """Model-level acceptance: bit-exact full-length log from the walker.
    Text round-trip and the size gate live on the canonical artifact (test_sidprog)."""
    mem, _load, init, play = load_psid(sid.read_bytes())
    mem[0xD418] = 0x0F
    frames = int(secs * 50)
    model = _verify(mem, init, play, frames, subtune)
    assert model.dispatch_sets is not None


def test_guard_live_dispatch_faults_on_unobserved_target():
    """A computed-dispatch site static analysis cannot certify serializes its
    observed set with a live guard; the standalone text walker faults on any
    other target (observed-primary doctrine)."""
    entry = next((t for t in corpus_params(HVSC) if t[0].stem == "Bionic_Commando"), None)
    if entry is None:
        pytest.skip("corpus tune absent")
    sid, sub, secs = entry
    mem, _load, init, play = load_psid(sid.read_bytes())
    mem[0xD418] = 0x0F
    model, _ev = S.decompile(mem, init, play, secs * 50, sub)
    live = {s: p for s, p in model.proofs.items() if p.status == "observed"}
    assert live, "expected at least one guard-live site"
    tm = sidprog.parse(sidprog.emit(model)).link()
    _site, pr = next(iter(live.items()))
    unobserved = next(a for a in range(0x0200, 0xCF00) if a not in tm.pcmap and a not in tm.contmap)
    assert unobserved not in pr.targets
    with pytest.raises(S.WalkError):
        tm.node_at(unobserved)
