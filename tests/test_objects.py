"""Pointer objects (``docs/wholeprog.md``, *Pointer objects*): the bounds checks of the loads
through a zero-page pointer pair hoisted to the pair's definitions, the loads proven inside
the object plus their indices."""

import pytest

import wholeprog_progs as W
from test_wholeprog import differential
from deity_informant import NATIVE, wholeprog

ProcessPool = pytest.importorskip("pebble").ProcessPool

pytestmark = pytest.mark.skipif(not NATIVE, reason="needs the native extension")

LIMIT = 60


@pytest.fixture(name="backend", autouse=True, params=["jit", "interp"])
def _backend(request, monkeypatch):
    """Every test on both engines (``wholeprog.BACKEND``)."""
    monkeypatch.setattr(wholeprog, "BACKEND", request.param)
    return request.param


def streams(n=4, length=24):
    """``n`` record streams: records ``[length, field...]`` of 2 or 3 bytes, then ``$FF``."""
    out = []
    for s in range(n):
        body = []
        for i in range(length):
            body += [3, (s + i) & 0x7F, i & 0x7F] if (s + i) % 3 else [2, (s * i) & 0x7F]
        out.append(body + [0xFF])
    return out


def stream_src(advance="LDA #3", tail=""):
    """The pair ``$FB`` set by ``init`` to one of the streams by ``A`` and read by ``play``
    one record per call, its fields at constant offsets, then advanced past the record by
    ``CLC; ADC; STA; BCC; INC``. ``advance`` loads the record's length for a record of
    three bytes, the data follow init and precede play, and ``tail`` follows play."""
    data = streams()
    rows = "".join(f"s{i}:     .byte {', '.join(map(str, d))}\n" for i, d in enumerate(data))
    return f"""
init:   AND #$03
        TAX
        LDA slo,X
        STA $FB
        LDA shi,X
        STA $FC
        RTS
slo:    .byte lo(s0), lo(s1), lo(s2), lo(s3)
shi:    .byte hi(s0), hi(s1), hi(s2), hi(s3)
{rows}play:   LDY #0
        LDA ($FB),Y
        BMI stop
        CMP #3
        BEQ three
        LDY #1
        LDA ($FB),Y
        STA $C001
        LDA #2
        BNE adv
three:  LDY #1
        LDA ($FB),Y
        STA $C001
        LDY #2
        LDA ($FB),Y
        STA $C002
        {advance}
adv:    CLC
        ADC $FB
        STA $FB
        BCC stop
        INC $FC
stop:   RTS
{tail}end:
"""


STREAM = stream_src()

# A three-byte record advances the pointer by a byte read from I/O.
FAR = stream_src(advance="LDA $C003")

# A store through a pointer read from I/O may write the pair.
ALIAS = stream_src(
    tail="poke:   LDA $C004\n        STA $F0\n        LDA $C005\n        STA $F1\n"
    "        LDY #0\n        LDA $C006\n        STA ($F0),Y\n        RTS\n"
)

# The streams' table also names a word in play's own code.
CODE = STREAM.replace("slo:    .byte lo(s0)", "slo:    .byte lo(init)").replace(
    "shi:    .byte hi(s0)", "shi:    .byte hi(init)"
)


def lift(src, entries=("init", "play")):
    """``(image, labels, program)``: the static lift of ``src``'s ``entries``, the pair holding
    the first stream's start."""
    img, labels = W.assemble(src)
    img[0xFB], img[0xFC] = labels["s0"] & 0xFF, labels["s0"] >> 8
    loaded = (0x1000, labels["end"])
    prog = wholeprog.lift(
        img, [labels[e] for e in entries], [W.IO], entry_sp=0xFD, loaded=loaded, fold=True
    )
    return img, labels, prog


def plan(song, n):
    """``init`` with ``A = song``, then ``n`` calls of ``play``."""
    return [(0, song)] + [(1, 0)] * n


def loads(img, labels):
    """The ``LDA ($FB),Y`` starts of ``play``."""
    return [a for a in range(labels["play"], labels["stop"]) if img[a] == 0xB1]


def test_records_read_inside_their_object():
    """One check, before the first load of each call, keeps the pointer in the streams: every
    load of a field reads proven inside them, and the lift replays every stream."""
    img, labels, prog = lift(STREAM)
    [(lo, hi, pcs)] = prog.objects
    assert (lo, hi) == (labels["s0"], labels["play"])
    assert pcs == loads(img, labels)
    assert prog.stats["obj_guards"] == 1
    proven = dict(prog.proven)
    for pc in pcs:
        assert proven[pc][0] >= lo and proven[pc][1] <= hi + 2
    for song in range(4):
        differential(img, prog, [labels["init"], labels["play"]], plan(song, 400))
    assert not dict(prog.deopts)


def test_pointer_run_off_its_object_deopts():
    """A pointer the data advance past its streams fails the check before the next load: the
    run leaves the lifted code there and the reference reads on."""
    img, labels, prog = lift(FAR)
    assert prog.objects and prog.stats["obj_guards"] == 1
    mem = bytearray(img)
    mem[0xC003] = 3
    differential(img, prog, [labels["init"], labels["play"]], plan(1, 40), synth=False, mem=mem)
    assert not dict(prog.deopts)
    mem[0xC003] = 0xF0
    differential(img, prog, [labels["init"], labels["play"]], plan(1, 40), synth=False, mem=mem)
    assert dict(prog.deopts)


def test_store_that_may_write_the_pair_keeps_its_checks():
    """A store through a pointer read from I/O may write the pair: the pointer holds no
    bounds every run keeps, so its loads keep their hulls, and a run whose store retargets
    the pair replays exactly."""
    img, labels, prog = lift(ALIAS, ("init", "play", "poke"))
    assert not prog.objects and not prog.stats["obj_guards"]
    pcs = [labels["init"], labels["play"], labels["poke"]]
    mem = bytearray(img)
    calls = [(0, 2)] + [(1, 0), (2, 0)] * 20
    for poke in ([0x00, 0x30, 0x55], [0xFC, 0x00, 0x20]):
        mem[0xC004:0xC007] = bytes(poke)
        differential(img, prog, pcs, calls, synth=False, mem=mem)


@pytest.mark.parametrize("src", [CODE], ids=["code"])
def test_pointer_into_code_keeps_its_checks(src):
    """A pointer whose words may name code holds no object: its loads keep their hulls."""
    img, labels, prog = lift(src)
    assert not prog.objects and not prog.stats["obj_guards"]
    for song in range(4):
        differential(img, prog, [labels["init"], labels["play"]], plan(song, 50))


def lift_and_replay(src, calls):
    """The objects and deopts of the static lift of ``src`` replayed over ``calls``."""
    img, labels, prog = lift(src)
    differential(img, prog, [labels["init"], labels["play"]], calls)
    return prog.objects, dict(prog.deopts)


def test_objects_end():
    """The reaching definitions of every pair of many end within the limit."""
    pairs = "".join(
        f"        LDY #{k}\n        LDA ($FB),Y\n        STA ${0x10 + 2 * k:02X}\n"
        f"        LDA #0\n        STA ${0x11 + 2 * k:02X}\n        LDA (${0x10 + 2 * k:02X}),Y\n"
        for k in range(24)
    )
    src = STREAM.replace("play:   LDY #0\n", "play:   LDY #0\n" + pairs + "        LDY #0\n")
    with ProcessPool(max_workers=1) as pool:
        objs, deopts = pool.schedule(lift_and_replay, (src, plan(0, 30)), timeout=LIMIT).result()
    assert objs and not deopts
