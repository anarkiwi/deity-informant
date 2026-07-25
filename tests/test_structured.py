"""Structured decompiler: full-length real-tune acceptance (cycle-stamped
bit-exact model replay), fuzz-corpus development checks, loud faults.
Text-layer laws live in test_sidprog."""

from pathlib import Path

import pytest

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


def _verify(mem, init, play, frames, subtune=0):
    model, ev = S.decompile(mem, init, play, frames, subtune)
    assert model.prologue == ev.prologue  # init's SID writes, order-preserved
    w = S.Walker(model)
    assert w.run(frames) == ev.wlog  # play-phase log from the post-init image
    assert bytes(w.m) == ev.end_mem
    return model


@pytest.mark.parametrize("p", _PLAYERS, ids=_IDS)
def test_fuzz_walker_bit_exact(p):
    """Development aid (acceptance is the real-tune gate): every idiom class
    replays with identical cycle-stamped log, end memory, and end registers."""
    _verify(_image(p), _init(p), p.org, p.frames)


def test_opcode_byte_outside_proven_set_faults():
    p = next(q for q in _PLAYERS if q.name == "smc_opcode")
    model, _ev = S.decompile(_image(p), _init(p), p.org, p.frames)
    (site,) = {pc for pc in model.dispatch_pcs if pc >= p.org}
    assert model.dispatch_sets[site] >= model.pcs[site]
    m = bytearray(model.mem0)
    m[site] = 0x02  # JAM: not in any proven store value set
    with pytest.raises(S.WalkError):
        model.lookup(site, m)


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


def test_evidence_bounded_dispatch_faults_on_unobserved_target():
    """A computed-dispatch site static analysis cannot bound is scoped to its
    observed targets; the standalone text walker faults on any other target
    (the guarded evidence envelope, identical to opcode-SMC dispatch)."""
    entry = next((t for t in corpus_params(HVSC) if t[0].stem == "Bionic_Commando"), None)
    if entry is None:
        pytest.skip("corpus tune absent")
    sid, sub, secs = entry
    mem, _load, init, play = load_psid(sid.read_bytes())
    mem[0xD418] = 0x0F
    model, _ev = S.decompile(mem, init, play, secs * 50, sub)
    assert model.evidence_sites, "expected at least one evidence-bounded site"
    tm = sidprog.parse(sidprog.emit(model)).link()
    _site, targets = next(iter(model.evidence_sites.items()))
    unobserved = next(a for a in range(0x0200, 0xCF00) if a not in tm.pcmap)
    assert unobserved not in targets
    with pytest.raises(S.WalkError):
        tm.node_at(unobserved)
