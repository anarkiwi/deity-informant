"""Structured decompiler: full-length real-tune acceptance (cycle-stamped
bit-exact, text round-trip, size), fuzz-corpus development checks, loud faults."""

import hashlib
import re
from pathlib import Path

import pytest

from deity_informant import stext
from deity_informant import structured as S
from deity_informant.c64 import load_psid
from deity_informant.cli import format_insn

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
    text = stext.emit(model)
    tm = stext.parse(text)
    assert stext.emit(tm) == text  # canonical text is a parse/emit fixpoint
    tw = S.Walker(tm)
    assert tw.run(frames) == ev.wlog  # standalone text replay, cycle-stamped
    assert bytes(tw.m) == ev.end_mem
    return model, text


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


def _disasm_size(mem):
    nz = [a for a in range(0x10000) if mem[a]]
    lo, hi = min(nz), max(nz)
    total = 0
    pc = lo
    while pc <= hi:
        try:
            length, text = format_insn(mem, pc)
        except Exception:  # pylint: disable=broad-except
            length, text = 1, "$%04X: .byte" % pc
        total += len(text) + 1
        pc += length
    return total


_OPEN_P4 = {  # open P4 bugs (docs/decompiler-plan.md): diagnostics asserted so drift surfaces
    "Krakout": "unproven control targets",
    "Trap": "unproven control targets",
    "Bionic_Commando": "unproven control targets",
    "Comic_Bakery": "unproven control targets",
    "Wizball": "unproven control targets",
}


@pytest.mark.parametrize("sid,secs", _tunes())
def test_real_tune_full_length_cycle_exact(sid, secs):
    """Acceptance: full song length, cycle-stamped (cycle, reg, value) log
    bit-exact from model and from parsed text; text is smaller than the
    disassembly listing (docs/decompiler-plan.md G3/G6)."""
    mem, _load, init, play = load_psid(sid.read_bytes())
    mem[0xD418] = 0x0F
    frames = int(secs * 50)
    try:
        model, text = _verify(mem, init, play, frames)
    except S.DecompileError as exc:
        expected = _OPEN_P4.get(sid.stem)
        if expected and expected in str(exc):
            pytest.xfail("open P4 bug: %s" % exc)
        raise
    assert sid.stem not in _OPEN_P4, "open P4 bug fixed: remove from _OPEN_P4"
    assert model.dispatch_sets is not None
    assert len(text) < _disasm_size(model.mem0)
