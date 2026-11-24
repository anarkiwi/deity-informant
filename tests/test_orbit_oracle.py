"""The native orbit (``deity_informant.orbit``) against a per-access Python oracle: the same
frames, addresses and successors on programs that index past tables, store through and read
via page-zero pointers, modify in place and read devices, under random overrun sites."""

import hashlib
import random

import numpy as np
import pytest
from jennings.devices.mpu6502 import NativeMPU
from jennings.memory import NATIVE, NativeMemory
from jennings.opcodes import OPCODES

import wholeprog_progs as W
from deity_informant import masks, orbit

pytestmark = pytest.mark.skipif(not NATIVE, reason="needs jennings' native core")

IO = (0xD000, 0xE000)

#: Counters stepping modulo 7 and 13 index tables, a pointer pair and in-place updates.
PROG = """
play:   LDX cnt
        INX
        CPX #8
        BCC k1
        LDX #0
k1:     STX cnt
rd:     LDA tab,X
        CMP #$F0
        BCC k2
        LDA $D000
k2:     STA out,X
        TAY
ind:    LDA ($FB),Y
        STA ($FD),Y
rmw:    INC tab2,X
        LDA tab2,X
        AND #3
        STA tab2,X
        LDA cnt2
        CLC
        ADC tab,X
        AND #15
        STA cnt2
        TAX
        CPX #13
        BCC k3
        LDX #0
k3:     LDA tab3,X
        STA $FD
        LDA tab3+1,X
        STA $FB
        RTS
spin:   JMP spin
cnt:    .byte 0
cnt2:   .byte 0
tab:    .byte 1, 2, 3, 5, 8, 13, 21, $F5
tab2:   .byte 0, 0, 0, 0, 0, 0, 0, 0
tab3:   .byte 0, 3, 6, 9, 12, 15, 18, 21, 24, 27, 30, 33, 36, 39, 42
out:    .byte 0, 0, 0, 0, 0, 0, 0, 0
rows:   .byte 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4
        .byte 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4
        .byte 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4
end:
"""


def reference(image, pc, volatile, limit, sites=(), max_cycles=10_000_000):
    """:func:`deity_informant.orbit.orbit` as a Python callback on every data access, a
    blake2b digest of memory per call and Python subscriptions for the sites' rule."""
    mem = NativeMemory(bytes(image))
    mpu = NativeMPU(memory=mem)
    core, buf, steps = mpu._core, [], []  # pylint: disable=protected-access
    vol, hit, seen = masks.mask(volatile), [], set()

    def read(a):
        if vol[a]:
            hit.append(a)
        buf.append(core.pc << 17 | a)

    _rule(mem, core, sites, vol)
    mem.observe_data_access(
        on_read=read, on_write=lambda a, _: buf.append(core.pc << 17 | 1 << 16 | a)
    )
    for k in range(limit + 1):
        state = np.frombuffer(bytes(mem[0:0x10000]), np.uint8)[~vol]
        state = hashlib.blake2b(state.tobytes(), digest_size=16).digest()
        if state in seen:
            break
        seen.add(state)
        if k == limit:
            return None
        pcs = np.frombuffer(core.run_to_rts_traced(pc, 0, max_cycles, False)[0], np.uint16)
        pcs = pcs.astype(np.int64)
        steps.extend((pcs[:-1] << 16 | pcs[1:]).tolist())
        if hit or core.jammed:
            return None
    out, succ = {}, {}
    for key in sorted(set(buf)):
        out.setdefault((key >> 17, bool(key >> 16 & 1)), []).append(key & 0xFFFF)
    for e in sorted(set(steps)):
        succ.setdefault(e >> 16, []).append(e & 0xFFFF)
    return len(seen), out, succ


def _rule(mem, core, sites, vol):
    """The sites' rule as per-address read/write subscriptions on ``mem``."""
    at = {s["pc"]: orbit.sources(s) for s in sites}
    watched = set().union(*(np.flatnonzero(v != -1).tolist() for v in at.values()))
    watched = sorted(watched - set(np.flatnonzero(vol).tolist()))
    if not watched:
        return
    busy = []

    def outside(a):
        src = at.get(core.pc)
        if busy or src is None or src[a] == -1:
            return None
        busy.append(a)
        zp = mem[(core.pc + 1) & 0xFFFF]
        indirect = OPCODES[mem[core.pc]][1] in ("indx", "indy")
        busy.pop()
        if indirect and a in (zp, (zp + 1) & 0xFF):
            return None
        return int(src[a])

    def raw(a):
        busy.append(a)
        try:
            return mem[a]
        finally:
            busy.pop()

    def read(a):
        src = outside(a)
        return None if src is None else 0 if src == -2 else raw(src)

    mem.subscribe_to_read(watched, read)
    mem.subscribe_to_write(watched, lambda a, v: None if outside(a) is None else raw(a))


def _site(rng, pc, lo):
    """A random overrun site of the instruction at ``pc`` over an object from ``lo``."""
    size = rng.randrange(1, 9)
    ok = [[lo, lo + size - 1]]
    if rng.random() < 0.5:
        ok.append([lo + size + 2, lo + size + 3])
    site = {"pc": pc, "addr": [rng.choice((0, lo)), lo + rng.randrange(8, 0x140)], "ok": ok}
    if rng.random() < 0.7:
        site["last"] = [[hi - rng.randrange(0, 2), rng.choice((1, 2))] for _, hi in ok]
    return site


def _state(rng, img, labels):
    """``img`` with random counters and page-zero pointers to the rows."""
    state = bytearray(img)
    state[labels["cnt"]] = rng.randrange(7)
    state[labels["cnt2"]] = rng.randrange(13)
    rows = labels["rows"] + rng.randrange(8)
    state[0xFB:0xFF] = bytes((rows & 0xFF, rows >> 8, rows & 0xFF, rows >> 8))
    return bytes(state)


def test_matches_reference():
    """Random states and sites give the reference's frames, addresses and successors, most
    closing within the limit."""
    img, labels = W.assemble(PROG)
    objs = {"rd": "tab", "ind": "rows", "rmw": "tab2", "k2": "out", "k3": "tab3"}
    closed = 0
    for seed in range(40):
        rng = random.Random(seed)
        state = _state(rng, img, labels)
        names = rng.sample(sorted(objs), rng.randrange(len(objs) + 1))
        sites = [_site(rng, labels[n], labels[objs[n]]) for n in names]
        if rng.random() < 0.3 and sites:
            sites[0] = {**sites[0], "addr": [0, 0xFFFF]}
        limit = rng.choice((3, 400, 400))
        got = orbit.orbit(state, labels["play"], [IO], limit, sites)
        assert got == reference(state, labels["play"], [IO], limit, sites), seed
        closed += got is not None
    assert closed >= 10


def test_none_cases():
    """A device read, too few calls, a jam and a call that never returns end without an orbit."""
    img, labels = W.assemble(PROG)
    state = bytearray(img)
    assert orbit.orbit(bytes(state), labels["play"], [IO], 400) is None
    state[labels["tab"] + 7] = 0
    assert orbit.orbit(bytes(state), labels["play"], [IO], 400) is not None
    assert orbit.orbit(bytes(state), labels["play"], [IO], 2) is None
    state[labels["play"]] = 0x02
    assert orbit.orbit(bytes(state), labels["play"], [IO], 400) is None
    for limit in (5, 50):
        args = (img, labels["spin"], [IO], limit, (), 1000)
        assert orbit.orbit(*args) == reference(*args)


def test_remap_skips_volatile_and_empty():
    """Sites wholly volatile, or with nothing outside their object, build no remap table."""
    vol = masks.mask([IO])
    site = {"pc": 0x1000, "addr": [0xD000, 0xD0FF], "ok": []}
    assert orbit._remap([site], vol) is None  # pylint: disable=protected-access
    whole = {"pc": 0x1000, "addr": [0x2000, 0x20FF], "ok": [[0x2000, 0x20FF]]}
    assert orbit._remap([whole], vol) is None  # pylint: disable=protected-access


def test_walk_says_how_it_ends():
    """The walk ends closed, open at the limit, on a device read or on a jam, with the
    accesses of the calls it ran."""
    img, labels = W.assemble(PROG)
    play, state = labels["play"], bytearray(img)
    how, calls, got, _ = orbit.walk(bytes(state), play, [IO], 400)
    assert how == "volatile" and (labels["k2"] - 3, False) in got and calls > 1
    state[labels["tab"] + 7] = 0
    closed = orbit.walk(bytes(state), play, [IO], 400)
    assert closed[0] == "closed" and tuple(closed[1:]) == orbit.orbit(bytes(state), play, [IO], 400)
    how, calls, got, succ = orbit.walk(bytes(state), play, [IO], 2)
    assert (how, calls) == ("open", 3) and got and succ
    state[play] = 0x02
    assert orbit.walk(bytes(state), play, [IO], 400)[0] == "jam"
