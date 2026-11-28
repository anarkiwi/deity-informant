"""Device polling loops: play's lifted as loops, and the inits a machine runs that poll."""

import pytest
from jennings.devices.mpu6502 import NativeMPU
from jennings.memory import NativeMemory

import wholeprog_progs as W
from deity_informant import NATIVE, cgen

pytestmark = pytest.mark.skipif(not NATIVE, reason="needs the native extension")

#: The I/O ranges: the test device and a VIC whose raster line the machine's beam gives.
VOLATILE = [W.IO, (0xD000, 0xD400)]


def machine(count=3, flag=0xC1, addr=0xC00D, volatile=VOLATILE):
    """A ``postinit`` machine on the jennings core: ``$D011``/``$D012`` the beam, ``addr``
    answering 0 to its first ``count`` reads, then ``flag``; ``volatile`` accesses logged
    ``(addr, value, write)``."""

    def run(mem, pc, a):
        m = NativeMemory(bytes(mem))
        m.raster_model(False)
        m.io = True
        seen = [0]

        def status(_):
            seen[0] += 1
            return flag if seen[0] > count else 0

        m.subscribe_to_read([addr], status)
        io, stored = [], set()

        def vol(x):
            return any(lo <= x < hi for lo, hi in volatile)

        def wrote(x, v):
            stored.add(x)
            if vol(x):
                io.append((x, v, True))

        def read(x):
            if vol(x):
                io.append((x, flag if x == addr and seen[0] > count else m[x], False))

        m.observe_data_access(on_read=read, on_write=wrote, stack=True)
        NativeMPU(memory=m).run_to_rts(pc, a, 10_000_000)
        m.observe_data_access(None, None)
        mem[:] = bytes(m[0:0x10000])
        return io, stored

    return run


def lifted(src, **kw):
    """``(program, labels)`` of ``src``'s ``init`` and ``play`` lifted on :func:`machine`."""
    img, labels = W.assemble(src, 0x1000)
    ents = [(labels["init"], "init"), (labels["play"], "play")]
    p, _ = cgen.lift(
        img,
        ents,
        VOLATILE,
        loaded=(0x1000, labels["end"]),
        init=0,
        songs=range(2),
        machine=machine(**kw),
    )
    return p, labels


PLAY = """
play:   LDA song
        STA $C000
        RTS
song:   .byte 0
cnt:    .byte 0
end:
"""

RASTER = """
init:   LDX #$30
wait:   CPX $D012
        BNE wait
        STA song
        LDA #$11
        STA $C020
        RTS
""" + PLAY

#: Waits on bit 7 of ``$C00D``; ``V`` differs between the bytes it leaves on, unread after.
TIMER = """
init:   STA song
wait:   BIT $C00D
        BPL wait
        LDA #$11
        STA $C020
        RTS
""" + PLAY

#: The polled byte left in ``A`` and stored: the raster line it waits for.
KEPT = """
init:   STA song
wait:   LDA $D012
        CMP #$30
        BNE wait
        STA cnt
        RTS
""" + PLAY

#: The polled byte left in ``A`` and stored: any with bit 7 set.
STATUS = """
init:   STA song
wait:   LDA $C00D
        BPL wait
        STA cnt
        RTS
""" + PLAY

#: A loop that stores while it polls: no polling loop.
STORING = """
init:   STA song
wait:   INC cnt
        LDA $C00D
        BPL wait
        RTS
""" + PLAY

#: A loop counting its passes in a register init stores after it.
COUNTING = """
init:   STA song
        LDX #0
wait:   INX
        LDA $C00D
        BPL wait
        STX cnt
        RTS
""" + PLAY


#: Play waiting on bit 7 of ``$C00D``.
PLAYWAIT = """
init:   STA song
        RTS
play:   LDA song
wait:   BIT $C00D
        BPL wait
        STA $C000
        RTS
song:   .byte 0
end:
"""


def test_play_polling_is_a_loop_reading_the_device():
    """A polling loop in an entry called any number of times is lifted as the loop it is,
    reading the device each pass; the init beside it is replayed and lifts no function."""
    p, labels = lifted(PLAYWAIT)
    fn = next(fn for fn in p.funcs if fn["pc"] == labels["play"])
    loads = [op[2][0] for b in fn["blocks"] for op in b["ops"] if op[0] == "LOAD"]
    assert any(vn[0] == "c" and vn[1] == 0xC00D for vn in loads)
    assert labels["init"] not in {f["pc"] for f in p.funcs}
