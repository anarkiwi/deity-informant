"""Structured-decompiler prototype: full-length real-tune acceptance (cycle-
stamped bit-exact), fuzz-corpus development checks, and loud-fault cases."""

import hashlib
import re
from pathlib import Path

import pytest

from deity_informant import structured as S
from deity_informant.c64 import load_psid

import _fuzzgen as G

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


def _verify(mem, init, play, frames):
    model, ev = S.decompile(mem, init, play, frames)
    w = S.Walker(model)
    assert w.run(frames) == ev.wlog
    assert bytes(w.m) == ev.end_mem
    assert list(w.r) == list(ev.end_reg)
    return model


@pytest.mark.parametrize("p", _PLAYERS, ids=_IDS)
def test_fuzz_walker_bit_exact(p):
    """Development aid (acceptance is the real-tune gate): every idiom class
    replays with identical cycle-stamped log, end memory, and end registers."""
    _verify(_image(p), _init(p), p.org, p.frames)


def test_opcode_dispatch_outside_evidence_faults():
    p = next(q for q in _PLAYERS if q.name == "smc_opcode")
    model, _ev = S.decompile(_image(p), _init(p), p.org, p.frames)
    (site,) = {pc for pc in model.dispatch_pcs if len(model.pcs[pc]) >= 1 if pc >= p.org}
    with pytest.raises(S.WalkError):
        model.block(site, 0x02)


def test_side_effect_volatile_read_is_decompile_error():
    mem = bytearray(0x10000)
    mem[0x0F00] = 0x60  # init: RTS
    mem[0x1000:0x1004] = bytes((0xAD, 0x0D, 0xDC, 0x60))  # LDA $DC0D ; RTS
    model, _ev = S.decompile(mem, 0x0F00, 0x1000, 1)
    with pytest.raises(S.DecompileError):
        model.build_all()


def _tunes():
    if not SONGLENGTHS.is_file():
        return []
    lengths = {}
    txt = SONGLENGTHS.read_text(encoding="latin-1")
    for m in re.finditer(r"^([0-9a-f]{32})=(\d+):(\d+)", txt, re.M):
        lengths[m.group(1)] = int(m.group(2)) * 60 + int(m.group(3))
    out = []
    for sid in sorted(HVSC.rglob("*.sid")):
        data = sid.read_bytes()
        _mem, _load, _init_, play = load_psid(data)
        secs = lengths.get(hashlib.md5(data).hexdigest())
        if play and secs:
            out.append(pytest.param(sid, secs, id=sid.stem))
    return out


@pytest.mark.parametrize("sid,secs", _tunes())
def test_real_tune_full_length_cycle_exact(sid, secs):
    """Acceptance: full song length, cycle-stamped (cycle, reg, value) log
    bit-exact, end state identical (docs/decompiler-plan.md G3)."""
    mem, _load, init, play = load_psid(sid.read_bytes())
    mem[0xD418] = 0x0F
    frames = int(secs * 50)
    model = _verify(mem, init, play, frames)
    assert model.dispatch_pcs is not None  # model built; SMC sites enumerated
