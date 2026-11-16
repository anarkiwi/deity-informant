"""deity_informant.devices and the C64's model: every read the emulated machine makes lies
in the model's set for the state the program's writes leave."""

import random

import pytest

import wholeprog_progs as W
from deity_informant import NATIVE, wholeprog
from deity_informant import c64devices as C
from deity_informant import devices as D

vm = pytest.importorskip("vagrant_moses.machine")
c64 = pytest.importorskip("deity_informant.c64")

ORG, OUT = 0x1000, 0x3000
#: CPU cycles of the instructions the programs use, and the cycle of their access.
LDA_IMM, ABS, NOP = 2, 4, 2

SID_CTRL = [w | t for w in (0x10, 0x20, 0x40, 0x80, 0x30, 0x00) for t in (0, 0x08, 0x04, 0x0C)]
WRITES = (
    [(C.SID + r, None) for r in (0x0E, 0x0F, 0x10, 0x11)]
    + [(C.SID + 0x12, SID_CTRL), (C.SID + 0x32, SID_CTRL), (C.SID + 0x2E, None)]
    + [(C.VIC + r, None) for r in range(0x2F) if r not in (0x19, 0x1E, 0x1F)]
    + [(C.VIC + 0x15, [0, 0xFF]), (C.VIC + 0x11, [0x1B, 0x0B, 0x9B]), (C.VIC + 0x1A, [0, 1])]
    + [(C.CIA1 + r, None) for r in (2, 3, 4, 5, 6, 7)]
    + [(C.CIA1 + 0x0E, [0x01, 0x11, 0x09, 0x21, 0]), (C.CIA1 + 0x0F, [0x01, 0x11, 0x41, 0])]
    + [(C.CIA1 + 0x0D, [0x7F, 0x81, 0x01, 0x82]), (C.CIA2 + 0x0D, [0x7F, 0x01])]
    + [(C.CIA2 + r, None) for r in (2, 3, 0x0E, 0x0F)]
    + [(C.COLOUR + r, None) for r in (0, 1, 0x3E7)]
    + [(0x00, None), (0x01, [0x35, 0x36, 0x37, 0x07, 0x05])]
)
READS = sorted(
    set(C.MODEL.described([(0xD000, 0xD800), (0xDC00, 0xDE00)])) - {C.CIA2 + 0x01}
    | {C.SID + 0x1B, C.SID + 0x3B, 0x00, 0x01, C.COLOUR, C.COLOUR + 1}
)


def program(rng, n):
    """``(code, ops)``: ``n`` random writes, reads (each stored to ``OUT`` on) and waits after
    ``SEI``, then a jump to itself; ``ops`` ``[(write, addr, value, cpu cycle)]``."""
    code, ops, t = [0x78], [], 2
    for k in range(n):
        pick = rng.random()
        if pick < 0.45:
            addr, vals = rng.choice(WRITES)
            v = rng.choice(vals) if vals else rng.randrange(256)
            code += [0xA9, v, 0x8D, addr & 0xFF, addr >> 8]
            ops.append((True, addr, v, t + LDA_IMM + ABS - 1))
            t += LDA_IMM + ABS
        elif pick < 0.85:
            addr = rng.choice(READS)
            out = OUT + k
            code += [0xAD, addr & 0xFF, addr >> 8, 0x8D, out & 0xFF, out >> 8]
            ops.append((False, addr, out, t + ABS - 1))
            t += 2 * ABS
        else:
            pad = rng.randrange(1, 12)
            code += [0xEA] * pad
            t += NOP * pad
    end = ORG + len(code)
    return code + [0x4C, end & 0xFF, end >> 8], ops, t


def run(code, cycles, model, ntsc):
    """RAM after ``code`` ran for ``cycles`` on the emulated C64 with a SID of ``model``."""
    chip = c64._Chip(model)  # pylint: disable=protected-access
    m = vm.Machine(ntsc=ntsc, sids={0xD400: chip.sid})
    for i, b in enumerate(code):
        m[ORG + i] = b
    start = m.reset(ORG)
    m.run(start + 8 * cycles + 2000)
    return m.ram


@pytest.mark.parametrize("ntsc", [False, True], ids=["pal", "ntsc"])
@pytest.mark.parametrize("model", ["6581", "8580"])
def test_emulated_reads_lie_in_the_models_sets(model, ntsc):
    """Randomized: each read of a random program of writes, reads and waits lies in the set
    the C64 model gives for the state the writes before it leave, at its CPU cycle."""
    rng = random.Random(f"{model}{ntsc}")
    sizes = []
    for _ in range(60):
        code, ops, cycles = program(rng, 40)
        ram = run(code, cycles, model, ntsc)
        st = D.State(C.MODEL)
        for write, addr, v, at in ops:
            if write:
                st.write(addr, v, at)
                continue
            want = st.read(addr, at)
            assert ram[v] in want, (hex(addr), ram[v], sorted(want)[:8])
            sizes.append(len(want))
    assert min(sizes) == 1 and max(sizes) == 256


def voice3(rng):
    """``(code, ops, cycles)``: the VIC's display and sprites set at random, voice 3's
    frequency written, its oscillator held by the test bit and released to a waveform, then
    reads of its output between waits and writes."""
    head = [(0xD011, rng.choice([0x1B, 0x0B, 0x13])), (0xD015, rng.choice([0, 0xFF]))]
    head += [(0xD001 + 2 * i, rng.randrange(256)) for i in range(8)]
    head += [(0xD40E, rng.randrange(256)), (0xD40F, rng.choice([0xFF, 0x80, 0x10, 1, 0]))]
    wave = rng.choice([0x10, 0x20, 0x40, 0x14, 0x12, 0x30])
    head += [(0xD412, wave | 0x08 | rng.choice([0, 1])), (0xD412, wave | rng.choice([0, 1]))]
    code, ops, t = [0x78], [], 2
    for addr, v in head:
        code += [0xA9, v, 0x8D, addr & 0xFF, addr >> 8]
        ops.append((True, addr, v, t + LDA_IMM + ABS - 1))
        t += LDA_IMM + ABS
    for k in range(rng.randrange(1, 6)):
        pad = rng.choice([0, 0, 1, 3, 10, 40])
        code += [0xEA] * pad
        t += NOP * pad
        if rng.random() < 0.1:
            addr, v = rng.choice([(0xD40E, rng.randrange(256)), (0xD412, wave)])
            code += [0xA9, v, 0x8D, addr & 0xFF, addr >> 8]
            ops.append((True, addr, v, t + LDA_IMM + ABS - 1))
            t += LDA_IMM + ABS
        out = OUT + k
        code += [0xAD, 0x1B, 0xD4, 0x8D, out & 0xFF, out >> 8]
        ops.append((False, 0xD41B, out, t + ABS - 1))
        t += 2 * ABS
    end = ORG + len(code)
    return code + [0x4C, end & 0xFF, end >> 8], ops, t


@pytest.mark.parametrize("model", ["6581", "8580"])
def test_released_oscillator_reads_lie_in_the_models_sets(model):
    """Randomized: voice 3 released from the test bit under the VIC's DMA reads values in
    the model's sets, most of them a few values."""
    rng = random.Random(model)
    few = 0
    for _ in range(300):
        code, ops, cycles = voice3(rng)
        ram = run(code, cycles, model, rng.random() < 0.5)
        st = D.State(C.MODEL)
        for write, addr, v, at in ops:
            if write:
                st.write(addr, v, at)
                continue
            want = st.read(addr, at)
            assert ram[v] in want, (hex(addr), ram[v], sorted(want)[:8])
            few += len(want) < 256
    assert few > 300


def test_released_sawtooth_reads_a_few_values_either_model():
    """Voice 3's sawtooth released from the test bit at full frequency reads a few values,
    the 6581's and the 8580's both among them; with time unknown it reads any."""
    for model, want in (("6581", 3), ("8580", 2)):
        st = D.State(C.MODEL)
        code = [0x78]
        for addr, v in ((0xD40E, 0xFF), (0xD40F, 0xFF), (0xD412, 0xFF), (0xD412, 0x20)):
            code += [0xA9, v, 0x8D, addr & 0xFF, addr >> 8]
        code += [0xAD, 0x1B, 0xD4, 0x8D, 0x00, 0x30]
        ram = run(code + [0x4C, 0x1F, 0x10], 40, model, False)
        assert ram[OUT] == want
        for i, (addr, v) in enumerate(((0xD40E, 0xFF), (0xD40F, 0xFF), (0xD412, 0xFF))):
            st.write(addr, v, 2 + 6 * i + 5)
        st.write(0xD412, 0x20, 2 + 6 * 3 + 5)
        got = st.read(0xD41B, 2 + 6 * 4 + 3)
        assert want in got and 2 < len(got) < 256
        st.wait()
        assert st.read(0xD41B, 40) == D.ALL


def test_kinds():
    """A latch reads its last write through its mask and mirrors, a write-only register and an
    undescribed address any byte, a counter recurs at every value, a status register's
    acknowledge makes it sensitive, a derived register follows its control register."""
    st = D.State(C.MODEL)
    assert st.read(0xD020) == frozenset(range(0xF0, 0x100))
    st.write(0xD060, 0x05, 0)
    assert st.read(0xD020) == {0xF5} and st.read(0xD0E0) == {0xF5}
    assert st.read(0xD400) == D.ALL and st.read(0xDE00) is None and st.recur(0xDE00) == []
    assert st.recur(0xD012) == [frozenset({v}) for v in range(256)]
    assert 0xDC0D in C.MODEL.sensitive([(0xDC00, 0xDD00)])
    assert 0xD020 not in C.MODEL.sensitive([(0xD000, 0xD400)])
    st.write(0xD412, 0x41, 0)
    assert st.read(0xD41B) == {0, 0xFF}
    st.write(0xD412, 0x28, 0)
    assert st.read(0xD41B) == {0}


def test_timer_flag_recurs_while_its_timer_runs():
    """The ICR's timer A flag recurs once timer A runs continuously with its interrupt masked
    off; a one-shot timer or an unknown mask claims nothing."""
    st = D.State(C.MODEL)
    st.write(0xDC0E, 0x01, 0)
    assert st.recur(0xDC0D) == []
    st.write(0xDC0D, 0x7F, 0)
    (flag,) = st.recur(0xDC0D)
    assert all(v & 1 for v in flag) and 0x81 in flag
    st.write(0xDC0D, 0x81, 0)
    assert st.recur(0xDC0D) == []
    st.write(0xDC0D, 0x01, 0)
    st.write(0xDC0E, 0x09, 0)
    assert st.recur(0xDC0D) == []


def test_raster_flag_recurs_below_the_last_line():
    """``$D019``'s raster flag recurs for a compare line every frame reaches, its interrupt
    off."""
    st = D.State(C.MODEL)
    for addr, v in ((0xD011, 0x1B), (0xD012, 0x30), (0xD01A, 0)):
        st.write(addr, v, 0)
    assert len(st.recur(0xD019)) == 1
    st.write(0xD011, 0x9B, 0)
    assert st.recur(0xD019) == []


def test_lowered_registers():
    """The lowering gives each canonical register once, its mirrors as addresses, a derived
    register's sets per control value and a latch none."""
    regs, addrs = C.MODEL.lower([(0xD400, 0xD800)])
    assert len(addrs) == 0x400 and len(regs) == 0x20
    by = {r[0]: r for r in regs}
    osc = by[0xD41B]
    assert osc[4] == 0xD412 and osc[5] == 0xFE and len(osc[6]) == 128
    assert osc[6][0] == D.bitmap(D.ALL)
    assert osc[6][D.submasks(0xFE).index(0x28)] == D.bitmap({0})
    regs, _ = C.MODEL.lower([(0xD020, 0xD021)])
    assert regs == [(0xD020, 0x0F, 0xF0, 0, -1, 0, [])]


#: A latch written through a mirror and read, a probe of voice 3 under its test bit, and a
#: read of an address the model does not describe.
LIFTED = """
entry:  LDA #$05
        STA $D060
        LDA $D020
        TAX
        LDA #$28
        STA $D412
        LDA $D41B
        TAY
        LDA $DE00
        RTS
"""


def _held(prog, pc, r):
    return set(dict(prog.regs)[pc][r])


@pytest.mark.skipif(not NATIVE, reason="needs the native extension")
def test_lift_takes_device_reads_from_the_model():
    """The lift's register sets: a latch read gives the value written, the probe its set
    under the written control value; an undescribed address gives any byte either way."""
    img, lab = W.assemble(LIFTED, 0x1000)
    kw = {"entry_sp": 0xFD, "loaded": (0x1000, 0x1020)}
    vol = [(0xD000, 0xE000)]
    with_model = wholeprog.lift(img, [lab["entry"]], vol, devices=C.MODEL, **kw)
    without = wholeprog.lift(img, [lab["entry"]], vol, **kw)
    tax, tay, end = lab["entry"] + 8, lab["entry"] + 17, lab["entry"] + 21
    assert _held(with_model, tax + 1, 1) == {0xF5} and len(_held(without, tax + 1, 1)) == 256
    assert _held(with_model, tay + 1, 2) == {0}
    assert _held(with_model, end, 0) == _held(without, end, 0) == set(range(256))
