"""The C64's devices as a :class:`~deity_informant.devices.Model` (``docs/cgen.md``, *Devices*).

The VIC-II, the SID at ``$D400``, colour RAM, CIA 1 and 2 and the 6510 port, from the chips'
register maps; reads answer sets over both SID models and PAL or NTSC timing.
"""

from __future__ import annotations

from .devices import ALL, Model, counter, derived, latch, status, submasks, write_only

__all__ = ["MODEL", "VIC", "SID", "CIA1", "CIA2", "COLOUR", "osc3"]

VIC, SID, COLOUR, CIA1, CIA2 = 0xD000, 0xD400, 0xD800, 0xDC00, 0xDD00

#: Voice 3's frequency, control register and its bits.
FREQ3, CTRL3 = SID + 0x0E, SID + 0x12
SYNC, RING, TEST = 0x02, 0x04, 0x08
TRI, SAW, PULSE = 0x10, 0x20, 0x40

#: Fewest raster lines of a frame (NTSC 6567R56A): a compare line below it recurs.
LINES = 262

_ICR = frozenset(f | b for f in range(32) for b in (0, 0x80))
_IRQ = frozenset(0x70 | f | b for f in range(16) for b in (0, 0x80))


def _span(n):
    """Machine cycles of ``n`` CPU cycles between two accesses: an access's cycle within its
    instruction varies by two, and the VIC-II's DMA (bad lines, sprites) at most doubles a
    span, plus two raster lines."""
    return max(n - 2, 0), 2 * n + 126


def osc3(state, at):
    """``$D41B``: voice 3's waveform output, from its control register; a sawtooth or triangle
    released from the test bit counts its frequency per cycle (the 8580 a cycle later)."""
    c = state.held(CTRL3)
    if c is None:
        return ALL
    wave = c & 0xF0
    if wave == PULSE:
        return {0, 0xFF}
    if wave not in (SAW, TRI):
        return ALL
    if c & TEST:
        return {0, 0xFF} if wave == TRI and c & RING else {0}
    _, prev, when = state.last(CTRL3)
    since = state.since(CTRL3, at)
    lo, hi = state.last(FREQ3), state.last(FREQ3 + 1)
    late = [w[2] for w in (lo, hi) if w is not None and w[2] is not None]
    if c & SYNC or prev is None or not prev & TEST or since is None or None in (lo, hi):
        return ALL
    if any(t > when for t in late) or since[1] - since[0] > 4096:
        return ALL
    f = hi[0] << 8 | lo[0]
    out = set()
    for e in range(max(since[0] - 1, 0), since[1] + 1):
        acc = f * e & 0xFFFFFF
        if wave == SAW:
            out.add(acc >> 16)
            continue
        t, msb = acc >> 15 & 0xFF, acc >> 23
        out.add(t ^ 0xFF if msb else t)
        if c & RING:
            out.add(t if msb else t ^ 0xFF)
    return out


def _icr_recur(state, addr):
    """The flags of a timer running continuously on the clock whose interrupt is masked off:
    one read eventually holds each."""
    base = addr & 0xFF00
    mask = state.held(base + 0x0D)
    out = []
    for flag, ctrl, bits in ((1, 0x0E, 0x29), (2, 0x0F, 0x69)):
        c = state.held(base + ctrl)
        if c is not None and c & bits == 1 and mask is not None and not mask & flag:
            out.append(frozenset(v for v in _ICR if v & flag))
    return out


def _icr_mask(prev, value):
    """The interrupt mask a write to the ICR leaves: bit 7 sets the bits given, else clears."""
    if value & 0x80:
        return None if prev is None else prev | value & 0x1F
    if value & 0x1F == 0x1F:
        return 0
    return None if prev is None else prev & ~value & 0x1F


def _irq_recur(state, _addr):
    """The raster flag, its compare line one every frame reaches and its interrupt off."""
    ctl, line, on = state.held(VIC + 0x11), state.held(VIC + 0x12), state.held(VIC + 0x1A)
    if None in (ctl, line, on) or on & 1 or (ctl & 0x80) << 1 | line >= LINES:
        return []
    return [frozenset(v for v in _IRQ if v & 1)]


def _port(state, _at):
    """``$01``: the output bits as written, the inputs any."""
    d, v = state.held(0), state.held(1)
    if d is None or v is None:
        return ALL
    out = v & d & 0x3F
    return {out | x for x in submasks(~d & 0x3F | 0xC0)}


def _vic():
    regs = {VIC + r: latch() for r in range(0x11)}
    regs.update(
        {
            VIC + 0x11: counter(live=0x80, mask=0x7F),
            VIC + 0x12: counter(),
            VIC + 0x13: write_only(),
            VIC + 0x14: write_only(),
            VIC + 0x15: latch(),
            VIC + 0x16: latch(0x3F, live=0xC0),
            VIC + 0x17: latch(),
            VIC + 0x18: latch(0xFE, live=0x01),
            VIC + 0x19: status(lambda s, t: _IRQ, _irq_recur),
            VIC + 0x1A: latch(0x0F, ones=0xF0),
            VIC + 0x1B: latch(),
            VIC + 0x1C: latch(),
            VIC + 0x1D: latch(),
            VIC + 0x1E: status(lambda s, t: ALL, ack=True),
            VIC + 0x1F: status(lambda s, t: ALL, ack=True),
        }
    )
    regs.update({VIC + r: latch(0x0F, ones=0xF0) for r in range(0x20, 0x2F)})
    regs.update({VIC + r: write_only(live=0, ones=0xFF) for r in range(0x2F, 0x40)})
    return regs


def _sid():
    regs = {SID + r: write_only() for r in range(0x20)}
    regs[SID + 0x1B] = derived(osc3, CTRL3, 0xFE)
    return regs


def _cia(base):
    regs = {base + r: write_only() for r in range(0x10)}
    regs.update(
        {
            base + 0x02: latch(),
            base + 0x03: latch(),
            base + 0x0D: status(lambda s, t: _ICR, _icr_recur, ack=True, fold=_icr_mask),
            base + 0x0E: latch(0xEF),
            base + 0x0F: latch(0xEF),
        }
    )
    for r in (0x08, 0x0B):
        regs[base + r] = status(lambda s, t: ALL, ack=True)
    if base == CIA2:
        regs[base + 0x01] = status(lambda s, t: ALL, ack=True)
    return regs


#: The C64.
MODEL = Model(
    {
        0x00: latch(),
        0x01: derived(_port),
        **_vic(),
        **_sid(),
        **{COLOUR + r: latch(0x0F, live=0xF0) for r in range(0x400)},
        **_cia(CIA1),
        **_cia(CIA2),
    },
    mirrors=((VIC, SID, 0x40), (SID, COLOUR, 0x20), (CIA1, CIA2, 0x10), (CIA2, 0xDE00, 0x10)),
    elapsed=_span,
)
