"""deity_informant.polling: device polling loops, and the init they leave translatable."""

import pytest
from jennings.devices.mpu6502 import NativeMPU
from jennings.memory import NativeMemory

import wholeprog_progs as W
from deity_informant import NATIVE, cgen, polling, postinit, wholeprog

pytestmark = pytest.mark.skipif(not NATIVE, reason="needs the native extension")

#: The I/O ranges: the test device and a VIC whose raster line the machine's beam gives.
VOLATILE = [W.IO, (0xD000, 0xD400)]


def machine(count=3, flag=0xC1):
    """A ``postinit`` machine on the jennings core: ``$D011``/``$D012`` the beam, ``$C00D``
    answering 0 to its first ``count`` reads, then ``flag``."""

    def run(mem, pc, a):
        m = NativeMemory(bytes(mem))
        m.raster_model(False)
        m.io = True
        seen = [0]

        def status(_):
            seen[0] += 1
            return flag if seen[0] > count else 0

        m.subscribe_to_read([0xC00D], status)
        io, stored = [], set()

        def wrote(x, v):
            stored.add(x)
            if any(lo <= x < hi for lo, hi in VOLATILE):
                io.append((x, v))

        m.observe_data_access(on_read=None, on_write=wrote, stack=True)
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


def loops_of(src):
    """``(image, labels, loops)``: the polling loops of ``src``'s ``init`` lifted once."""
    img, lab = W.assemble(src, 0x1000)
    prog = wholeprog.lift(img, [lab["init"]], VOLATILE, once=True)
    return img, lab, polling.loops(img, prog.starts, prog.code, VOLATILE)


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


@pytest.mark.parametrize(
    "src,translated",
    [
        (RASTER, True),
        (TIMER, True),
        (KEPT, True),
        (STATUS, False),
        (STORING, False),
        (COUNTING, False),
    ],
    ids=["raster", "timer", "kept", "status", "storing", "counting"],
)
def test_init_polling_is_translated_where_the_wait_settles(src, translated):
    """Init waiting on the device is translated where the wait leaves one state on every byte
    it leaves on and keeps it on every other: a raster line, a flag whose other bits are not
    read, the compared byte left in ``A``; a varying byte stored, a store or a count is not."""
    p, _ = lifted(src)
    assert (p.lifted is not None) == translated


def test_loops_are_the_storeless_cycles_reading_the_device():
    """Only a cycle reading the device without writing is a polling loop: its instructions,
    its one reading instruction and the registers live into it (all where it is left)."""
    _, lab, loops = loops_of(KEPT)
    w = lab["wait"]
    assert set(loops) == {w, w + 3, w + 5}
    loop = loops[w]
    assert (loop.at, loop.addr) == (w, 0xD012)
    assert loop.live[w] == frozenset(polling.REGS) - {0, 8, 9, 14}
    assert not loops_of(STORING)[2]


@pytest.mark.parametrize(
    "src,byte,varying",
    [(KEPT, 0x30, set()), (TIMER, 0x80, {13}), (STATUS, 0x80, {0})],
    ids=["kept", "timer", "status"],
)
def test_settle_gives_the_least_leaving_byte_and_what_varies(src, byte, varying):
    """The least byte the loop leaves on, and the registers differing between such bytes."""
    img, lab, loops = loops_of(src)
    mpu = NativeMPU(memory=NativeMemory(bytes(img)))
    got = polling.settle(loops[lab["wait"]], mpu.memory, polling.registers(mpu), lab["wait"])
    assert got == (byte, frozenset(varying))


@pytest.mark.parametrize(
    "src",
    [
        "init:   LDA $C00D\n        BMI one\n        BEQ two\n        JMP init\n"
        "one:    RTS\ntwo:    RTS\n",
        "init:   LDA #0\nwait:   ADC #1\n        BIT $C00D\n        BPL wait\n        RTS\n",
    ],
    ids=["two-exits", "accumulating"],
)
def test_settle_needs_one_exit_and_a_kept_state(src):
    """A loop leaving to one place or another by the byte it reads, or changing a register
    it reads on a pass it stays on, does not settle."""
    img, lab, loops = loops_of(src)
    head = lab.get("wait", lab["init"])
    mpu = NativeMPU(memory=NativeMemory(bytes(img)))
    assert polling.settle(loops[head], mpu.memory, polling.registers(mpu), head) is None


def test_run_answers_the_poll_and_records_it():
    """The traced run answers the loop's device read with the byte it leaves on, recorded in
    order, where the reference's device (the image byte) never satisfies it."""
    img, lab, loops = loops_of(RASTER)
    assert postinit.traced(img, lab["init"], 0, VOLATILE) is None
    r = postinit.traced(img, lab["init"], 0, VOLATILE, loops)
    assert r.exact and r.reads == [(0xD012, 0x30)]


def test_run_reading_a_varying_register_is_not_exact():
    """A register differing between the bytes the loop leaves on, read after it, ends the
    run's exactness; a run returning only by a loop that does not settle is none."""
    img, lab, loops = loops_of(STATUS)
    img[0xC00D] = 0x80
    r = postinit.traced(img, lab["init"], 0, VOLATILE, loops)
    assert r is not None and not r.exact
    img, lab, loops = loops_of(COUNTING)
    assert postinit.traced(img, lab["init"], 0, VOLATILE, loops) is None


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
    reading the device each pass; the init beside it is translated."""
    p, labels = lifted(PLAYWAIT)
    fn = next(fn for fn in p.funcs if fn["pc"] == labels["play"])
    loads = [op[2][0] for b in fn["blocks"] for op in b["ops"] if op[0] == "LOAD"]
    assert p.lifted is not None and any(vn[0] == "c" and vn[1] == 0xC00D for vn in loads)
