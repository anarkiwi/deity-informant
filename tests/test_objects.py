"""Pointer objects (``docs/wholeprog.md``, *Pointer objects*): the bounds checks of the loads
through a zero-page pointer pair hoisted to the pair's definitions, the loads proven inside
the object plus their indices."""

import pytest

import wholeprog_progs as W
from test_wholeprog import differential
from deity_informant import NATIVE, devices, postinit, wholeprog

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


def byte_rows(prefix, data):
    """Assembly ``.byte`` rows of ``data``, the ``i``-th labelled ``{prefix}{i}``."""
    return "".join(f"{prefix}{i}:     .byte {', '.join(map(str, d))}\n" for i, d in enumerate(data))


def stream_src(advance="LDA #3", tail=""):
    """The pair ``$FB`` set by ``init`` to one of the streams by ``A`` and read by ``play``
    one record per call, its fields at constant offsets, then advanced past the record by
    ``CLC; ADC; STA; BCC; INC``. ``advance`` loads the record's length for a record of
    three bytes, the data follow init and precede play, and ``tail`` follows play."""
    data = streams()
    rows = byte_rows("s", data)
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


def replay(img, prog, labels, songs, n):
    """``init`` then ``n`` calls of ``play`` replayed for each of ``songs``."""
    for song in songs:
        differential(img, prog, [labels["init"], labels["play"]], plan(song, n))


def loads(img, labels):
    """The ``LDA ($FB),Y`` starts of ``play``."""
    return [a for a in range(labels["play"], labels["stop"]) if img[a] == 0xB1]


@pytest.mark.timeout(120)  # replays every song through each executor
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
    replay(img, prog, labels, range(4), 400)
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
    replay(img, prog, labels, range(4), 50)


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


def runs(marker, lens, slot=16, fill=0x11):
    """Per run of ``lens`` a ``slot`` of bytes: ``n`` data bytes (no marker), ``marker``, then
    ``fill``; None for a run with no marker in its slot."""
    out = []
    for i, n in enumerate(lens):
        body = [(3 * i + j) % 0x70 + 1 for j in range(slot if n is None else n)]
        out.append(body if n is None else body + [marker] + [fill] * (slot - n - 1))
    return out


def walk_src(data, test="BMI done", step="", tail="", setter=None):
    """Pair ``$FB`` set to one of the runs ``data`` by ``A`` (by ``init``, or by ``setter``
    run at ``play``'s start), read by ``play`` from its start until ``test`` finds a marker,
    ``step`` before each ``INY``; ``tail`` follows play."""
    rows = byte_rows("r", data)
    n = len(data)
    sets = """        AND #$03
        TAX
        LDA tlo,X
        STA $FB
        LDA thi,X
        STA $FC
"""
    return f"""
init:   {"STA $F0" if setter else sets.strip()}
        RTS
tlo:    .byte {', '.join(f'lo(r{i})' for i in range(n))}
thi:    .byte {', '.join(f'hi(r{i})' for i in range(n))}
{rows}play:   {sets.strip() if setter else "NOP"}
        LDY #0
loop:   LDA ($FB),Y
        {test}
        STA $C001
{step}        INY
        BNE loop
done:   RTS
{tail}end:
"""


SIGN = walk_src(runs(0x80, [5, 9, 3, 12]))

# The records are written by play.
WRITTEN = walk_src(runs(0x80, [5, 9, 3, 12]), step="        STA r1+2\n")

# A path increments `Y` past a byte it did not test.
UNTESTED = walk_src(runs(0x80, [6, 8, 4, 12]), step="        INY\n        LDA ($FB),Y\n")

# The last run has no marker before play's code.
OPEN = walk_src(runs(0x80, [5, 9, 3, None]))

# Play sets the pointer itself, from A, a byte past the run's start by I/O.
SET = walk_src(runs(0, [5, 9, 3, 12]), test="BEQ done", setter=True).replace(
    "        LDA tlo,X\n        STA $FB\n        LDA thi,X\n",
    "        LDA $C000\n        AND #$01\n        CLC\n        ADC tlo,X\n        STA $FB\n"
    "        LDA thi,X\n        ADC #0\n",
)


def walk_lift(src, entries=("init", "play")):
    """``(image, labels, program)``: the static lift of ``src``'s ``entries``, the pair holding
    the first run's start."""
    img, labels = W.assemble(src)
    img[0xFB], img[0xFC] = labels["r0"] & 0xFF, labels["r0"] >> 8
    loaded = (0x1000, labels["end"])
    prog = wholeprog.lift(
        img, [labels[e] for e in entries], [W.IO], entry_sp=0xFD, loaded=loaded, fold=True
    )
    return img, labels, prog


def served(prog, pc):
    """The objects serving the load at ``pc``."""
    return [o for o in prog.objects if pc in o[2]]


def test_walk_to_a_marker_reads_its_runs():
    """The walk from the pointer to its run's sign-bit marker reads proven inside the runs:
    one check after ``LDY`` of the pointer plus ``Y``; every run replays."""
    img, labels, prog = walk_lift(SIGN)
    [(lo, hi, pcs)] = served(prog, labels["loop"])
    assert lo == labels["r0"] and hi < labels["play"] and pcs == [labels["loop"]]
    a, b = dict(prog.proven)[labels["loop"]][:2]
    assert labels["r0"] <= a and b < labels["play"]
    replay(img, prog, labels, range(4), 30)
    assert not dict(prog.deopts)


@pytest.mark.parametrize("src", [WRITTEN, UNTESTED], ids=["written", "untested"])
def test_walk_unproven_keeps_its_check(src):
    """A walk over bytes a store may write, or past a byte it did not test, holds no bound of
    its own: the memory map alone checks its load, against the run of data the tables its
    pointer is copied from lie in, and every run replays."""
    img, labels, prog = walk_lift(src)
    assert [o[:2] for o in served(prog, labels["loop"])] == [(labels["tlo"], labels["play"] - 1)]
    replay(img, prog, labels, range(4), 30)


def test_walk_with_no_marker_deopts_when_run_off():
    """A run with no marker before the held bytes end fails the check after ``LDY``: its
    walk leaves the lifted code there, the others read unchecked."""
    img, labels, prog = walk_lift(OPEN)
    [(lo, hi, _)] = served(prog, labels["loop"])
    assert lo == labels["r0"] and hi < labels["r3"]
    replay(img, prog, labels, range(3), 30)
    assert not dict(prog.deopts)
    replay(img, prog, labels, [3], 30)
    assert dict(prog.deopts)


def test_seeded_pairs_need_no_check():
    """Init's per-argument pointers, which play never stores, seed the pair: the walk from
    each to its zero marker reads proven inside its run with no check."""
    src = walk_src(runs(0, [5, 9, 3, 12]), test="BEQ done")
    img, labels = W.assemble(src)
    init = postinit.run(img, labels["init"], range(4), [W.IO])
    image = init.states[0].tobytes()
    prog = wholeprog.lift(
        image,
        [labels["play"]],
        [W.IO],
        entry_sp=0xFD,
        loaded=(0x1000, labels["end"]),
        fold=True,
        free=init.values(),
    )
    assert not prog.stats["obj_guards"]
    a, b = dict(prog.proven)[labels["loop"]][:2]
    assert labels["r0"] <= a and b < labels["play"]
    for song in range(4):
        mem = bytearray(init.states[song].tobytes())
        differential(image, prog, [labels["play"]], [(0, 0)] * 5, mem=mem)


def chain_lift(n):
    """The object guards of the static lift of a chain of ``n`` pairs loaded with the program,
    each stored through the one before, replayed."""
    chain = "".join(
        f"        LDA #${0x12 + 2 * k:02X}\n        STA (${0x10 + 2 * k:02X}),Y\n" for k in range(n)
    )
    src = f"play:   LDY #0\n{chain}        LDA ($FB),Y\n        RTS\nend:\n"
    img, labels = W.assemble(src)
    for k in range(n + 1):
        img[0x10 + 2 * k], img[0x11 + 2 * k] = 0x12 + 2 * k, 0
    prog = wholeprog.lift(img, [labels["play"]], [W.IO], entry_sp=0xFD, loaded=(0, labels["end"]))
    differential(img, prog, [labels["play"]], [(0, 0)] * 3)
    return prog.stats["obj_guards"]


def test_seeds_end():
    """The seeds of a chain of pairs, each stored through the one before, end within the
    limit: a store through a pair no longer a seed may write any."""
    with ProcessPool(max_workers=1) as pool:
        assert pool.schedule(chain_lift, (40,), timeout=LIMIT).result() == 0


def cursor_src(gate="", step="INC cur,X", tail=""):
    """Three objects, each with a record selector ``sel,X``, a cursor ``cur,X`` into the record
    the selector's pointer names and an offset ``pos,X`` into one array of segments, kept
    between calls of ``play``: each call reads the byte at each, advances past it with ``step``
    (the record cursor) or ``INC`` (the offset), and at a record's sign-bit marker resets the
    cursor and selects the next record by a table; at a segment's marker the offset restarts at
    its selector's segment. Init sets the selectors from ``A``; ``gate`` starts each object's
    turn and ``tail`` follows play."""
    recs = [[(5 * i + j) % 0x70 + 1 for j in range(3 + 2 * i)] + [0x80] for i in range(4)]
    rows = byte_rows("r", recs)
    segs = [[(7 * i + j) % 0x70 + 1 for j in range(2 + 3 * i)] + [0x80] for i in range(4)]
    starts = [sum(len(s) for s in segs[:i]) for i in range(4)]
    flat = ", ".join(str(b) for s in segs for b in s)
    return f"""
init:   AND #$03
        LDX #2
iloop:  STA sel,X
        TAY
        LDA start,Y
        STA pos,X
        LDA #0
        STA cur,X
        LDA #1
        STA cnt,X
        TYA
        CLC
        ADC #1
        AND #$03
        DEX
        BPL iloop
        RTS
play:   LDX #2
vloop:  {gate}
        LDY pos,X
seg:    LDA flat,Y
        BPL keep
        LDY sel,X
        LDA start,Y
        STA pos,X
        JMP rec
keep:   STA $C002
        INC pos,X
rec:    LDY sel,X
        LDA tlo,Y
        STA $FB
        LDA thi,Y
        STA $FC
        LDY cur,X
rd:     LDA ($FB),Y
        BMI next
        STA $C001
        {step}
        JMP done
next:   LDA #0
        STA cur,X
        LDY sel,X
        LDA nxt,Y
        STA sel,X
done:   DEX
        BPL vloop
        RTS
{tail}tlo:    .byte lo(r0), lo(r1), lo(r2), lo(r3)
thi:    .byte hi(r0), hi(r1), hi(r2), hi(r3)
nxt:    .byte 2, 0, 3, 1
start:  .byte {', '.join(map(str, starts))}
{rows}flat:   .byte {flat}
sel:    .byte 0, 0, 0
cur:    .byte 0, 0, 0
pos:    .byte 0, 0, 0
cnt:    .byte 0, 0, 0
end:
"""


CURSOR = cursor_src()

# Each object's turn waits on its counter.
COUNTED = cursor_src(gate="DEC cnt,X\n        BNE done\n        LDA #2\n        STA cnt,X")

# The record cursor advances by an I/O read, which the model answers with 0 or 1.
POLLED = cursor_src(step="LDA $C003\n        CLC\n        ADC cur,X\n        STA cur,X")

# Another entry stores through a pointer read from I/O: it may write a cursor.
POKED = cursor_src(
    tail="poke:   LDA $C004\n        STA $F0\n        LDA $C005\n        STA $F1\n"
    "        LDY #0\n        LDA $C006\n        STA ($F0),Y\n        RTS\n"
)

#: A model answering the record cursor's step.
STEP = devices.Model({0xC003: devices.derived(lambda st, at: [0, 1])})


def cursor_lift(src, entries=("play",), model=None):
    """``(image, labels, init runs, program)``: ``src``'s ``entries`` lifted from what its
    ``init`` leaves with ``A`` each of 0..3."""
    img, labels = W.assemble(src)
    img[0xC003] = 1
    init = postinit.run(img, labels["init"], range(4), [W.IO])
    image = init.states[0].tobytes()
    prog = wholeprog.lift(
        image,
        [labels[e] for e in entries],
        [W.IO],
        entry_sp=0xFD,
        loaded=(0x1000, labels["end"]),
        fold=True,
        free=init.values(),
        devices=model,
    )
    return image, labels, init, prog


@pytest.mark.parametrize(
    "src,model",
    [(CURSOR, None), (COUNTED, None), (POLLED, STEP)],
    ids=["plain", "counted", "polled"],
)
def test_cursors_read_inside_the_program(src, model):
    """Cursors kept between calls, reset at their data's markers, read proven inside the
    program: the bases lie in it, so the memory map holds every valid run's reads there,
    each checked before it (a counter deciding a turn taken both ways, an I/O step its
    modelled values); every run replays past many resets."""
    img, labels, init, prog = cursor_lift(src, model=model)
    proven = dict(prog.proven)
    for pc in (labels["rd"], labels["seg"]):
        lo, hi = proven[pc][:2]
        assert 0x1000 <= lo and hi < labels["end"]
    assert prog.stats["obj_guards"]
    for song in range(4):
        mem = bytearray(init.states[song].tobytes())
        differential(img, prog, [labels["play"]], [(0, 0)] * 200, synth=False, mem=mem)
