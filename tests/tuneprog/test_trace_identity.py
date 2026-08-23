"""S1 differential: the ``Trace`` the front end consumes is byte-for-byte pinned.

One fixture per recorded mechanism -- per-op access sets, SMC opcode and operand
cells, index domains, every control-kind edge, JSR/RTS pairing, inputs, IO logs,
the preemption schedule, both footprint hashes -- hashed after serialisation.
:func:`capture` regenerates the table; every digest below came from ``main`` at
f713814, was regenerated on ``nmi-prototype`` (PR #272), which added
:attr:`~.tracedata.Trace.nmilog` to the digest and moved nothing (no fixture here
has a second entry, so the log it hashes is empty -- a schedule appearing in a
one-entry tune's trace would move a digest, and
:func:`test_the_digest_follows_the_preemption_schedule` shows it does), and again
on ``p4-oracle-fixes``, which gave ``wlog``/``iolog`` their ``nmi`` column: it
moved exactly the twelve fixtures that write to a chip, and no other.
"""

import hashlib
import json

import numpy as np
import pytest

from deity_informant.tuneprog.machine import Entry
from deity_informant.tuneprog.trace import Tracer

from _asm import asm, banked_out, sid_image

PLAY = 0x1000
ARRAYS = (
    "image_pre",
    "image_post_init",
    "state_hash",
    "footprint_size",
    "state_hash_free",
    "footprint_free",
)


def digest(trace, tmp_path):
    """``trace.json`` plus every bulk array, hashed: the front end's whole input."""
    trace.save(tmp_path)
    h = hashlib.sha256((tmp_path / "trace.json").read_bytes())
    for name in ARRAYS:
        v = getattr(trace, name)
        h.update(np.asarray(bytearray(v) if isinstance(v, bytes) else v).tobytes())
    for log in (trace.wlog, trace.iolog, trace.nmilog):
        for k in sorted(log):
            h.update(log[k].tobytes())
    return h.hexdigest()


def f_pointer():
    """(zp),Y: pointer bytes and the stream load are three attributed ops."""
    init = asm(0x1100, "LDA #$10", "STA $FB", "LDA #$11", "STA $FC", "RTS")
    play = asm(PLAY, "LDA ($FB),Y", "STA $D400", "INY", "RTS")
    return {PLAY: play, 0x1100: init}, 0x1100, {0x1110: 0x5A, 0x1111: 0x6B}, 4


def f_indexed():
    """Indexed writes with a taken/not-taken branch: index domain and both edges."""
    play = asm(PLAY, "LDX #$02", "loop: STA $1100,X", "DEX", "BPL loop", "RTS")
    return {PLAY: play, 0x1200: asm(0x1200, "RTS")}, 0x1200, {0x1100: 0, 0x1103: 0}, 3


def f_calls():
    """JSR/RTS pairing, a tail jump into a JSR target, register summaries."""
    play = asm(PLAY, "JSR $1020", "JSR $1030", "RTS")
    sub = asm(0x1020, "LDA #$01", "JMP $1030")
    tail = asm(0x1030, "STA $D400", "RTS")
    init = asm(0x1040, "LDA #$0F", "STA $D418", "RTS")
    return {PLAY: play, 0x1020: sub, 0x1030: tail, 0x1040: init}, 0x1040, {}, 3


def f_rts_trick():
    """An unmatched RTS: a pushed target returned to, and its loose-target count."""
    play = asm(PLAY, "LDA #$10", "PHA", "LDA #$1F", "PHA", "RTS")
    return {PLAY: play, 0x1020: asm(0x1020, "RTS"), 0x1030: asm(0x1030, "RTS")}, 0x1030, {}, 2


def f_smc():
    """Self-modifying operand and opcode cells: variants, cell values, site merging."""
    play = asm(
        PLAY,
        "LDA cell+1",
        "CLC",
        "ADC #$01",
        "STA cell+1",
        "cell: LDA $1200",
        "STA $D401",
        "LDA #$AD",
        "STA op",
        "op: NOP",
        "RTS",
    )
    return {PLAY: play, 0x1300: asm(0x1300, "RTS")}, 0x1300, {0x1200 + i: i for i in range(8)}, 5


def f_branch_zero():
    """A zero-displacement branch: taken and fall-through are the same target."""
    play = asm(PLAY, "LDX #$01", "BEQ over", "over: STX $D40D", "RTS")
    return {PLAY: play, 0x1300: asm(0x1300, "RTS")}, 0x1300, {}, 3


def f_branch_zero_taken():
    """The same branch, taken: one edge cell counts both directions."""
    play = asm(PLAY, "LDX #$00", "BEQ over", "over: STX $D40D", "RTS")
    return {PLAY: play, 0x1300: asm(0x1300, "RTS")}, 0x1300, {}, 3


def f_smc_revert():
    """An operand cell that alternates between two values: a site's bytes come back."""
    play = asm(
        PLAY,
        "LDA cell+1",
        "EOR #$01",
        "STA cell+1",
        "cell: LDA $1200",
        "STA $D402",
        "RTS",
    )
    return {PLAY: play, 0x1300: asm(0x1300, "RTS")}, 0x1300, {0x1200: 7, 0x1201: 9}, 6


def f_smc_under_io():
    """A chip write that lands on an executed byte of the RAM under I/O."""
    init = asm(0x1100, "LDA #$60", "STA $D500", "RTS")
    play = asm(PLAY, "LDA #$EA", "STA $D500", "JSR $D500", "RTS")
    return {PLAY: play, 0x1100: init}, 0x1100, {}, 3


def f_jmpind():
    """A computed JMP(ind) through a pointer play increments: varying jmpind edges."""
    init = asm(0x1100, "LDA #$40", "STA $FD", "LDA #$10", "STA $FE", "RTS")
    play = asm(PLAY, "JMP ($00FD)", "RTS")
    return {PLAY: play, 0x1040: asm(0x1040, "INC $00FD", "RTS"), 0x1100: init}, 0x1100, {}, 3


def f_io():
    """Volatile IO reads of several input kinds, plus SID and non-SID chip writes."""
    play = asm(
        PLAY,
        "LDA $D012",
        "STA $D400",
        "LDA $DC04",
        "STA $D401",
        "LDA $D41B",
        "STA $D019",
        "LDA $1FFF",
        "STA $DD00",
        "RTS",
    )
    return {PLAY: play, 0x1100: asm(0x1100, "RTS")}, 0x1100, {}, 3


def f_period():
    """A play with no state of its own: the footprint repeats and both witnesses fire."""
    play = asm(PLAY, "LDA #$21", "JMP over", "over: STA $D404", "RTS")
    return {PLAY: play, 0x1100: asm(0x1100, "RTS")}, 0x1100, {}, 6


FIXTURES = {
    "branch_zero": (
        f_branch_zero,
        "20e5425ba6c73eca83742b49c49f5789733f85dcdfb2c512f6e77258e10578b0",
    ),
    "branch_zero_taken": (
        f_branch_zero_taken,
        "e19e075447bab7e584adc51b4076eade77ba43ed362b7f70f21977d65fe27e85",
    ),
    "calls": (f_calls, "0958316d2f67ac987f6cb4ce6703e9ed7c2a8158004fb22e7be4695867fa9b05"),
    "indexed": (f_indexed, "33f2ed505af0eabdc15cf2c561214d8597862e9f548a0cd583d3a2b8ce926279"),
    "io": (f_io, "7acb17b2d616221cc4e01d9fc6be1d1d0ed72a94fb041470d495fd25ed0b8e54"),
    "jmpind": (f_jmpind, "b7c3e7c90bda340724de5e3b369e460321ab58e49b5547f3149cc7f9063ce410"),
    "period": (f_period, "2bfa63efcd37518e7798dd251051cb5ecd876c5bc882ed12569a36299dcc047d"),
    "pointer": (f_pointer, "b5a669cf6ed5b66f959b258d41e83dc3fa418f308c782f5d6e5cf91329de8385"),
    "rts_trick": (f_rts_trick, "2fbe563c697f80e431684ee716d6725b8fc20d8a2c9b6148c14fb64693d7c345"),
    "smc": (f_smc, "c7caa67e6fb1ba97b3a02d5687bff2cc2fdff6f1b0ae2d9068873398c3dedd7f"),
    "smc_revert": (
        f_smc_revert,
        "563ee460620b4f0c9d2ab4c0a8c95ef4a065d28c95f147db9cf69cf7ba6f4f76",
    ),
    "smc_under_io": (
        f_smc_under_io,
        "394ed3441aa172c640707ae653a18a803e142f19e3896f763e3a8cc2ac414b06",
    ),
}


def build(name):
    """Trace one fixture to its recorded horizon."""
    blocks, init, data, calls = FIXTURES[name][0]()
    img = sid_image(blocks, init, PLAY, data)
    t = Tracer(img, Entry("sub", PLAY, 19656, "cia_timer"))
    t.run_init()
    t.run_calls(calls)
    return t.trace()


def build_irq():
    """An IRQ-entry tune with the KERNAL banked out: the pushed frame and RTI."""
    handler = asm(0x1200, "LDA $D019", "STA $D019", "LDA #$0F", "STA $D418", "INC $1FF0", "RTI")
    img = banked_out(sid_image({0x1100: asm(0x1100, "RTS"), 0x1200: handler}, 0x1100, 0))
    t = Tracer(img, Entry("irq", 0x1200, 19656, "pal_video"))
    t.run_init()
    t.run_calls(3)
    return t.trace()


def build_song(song):
    """One subtune of a two-subtune image: the song number in A reaches play's state."""
    play = asm(PLAY, "LDA $1FF0", "STA $D400", "RTS")
    init = asm(0x1100, "STA $1FF0", "RTS")
    img = sid_image({PLAY: play, 0x1100: init}, 0x1100, PLAY, {0x1FF0: 0})
    t = Tracer(img, Entry("sub", PLAY, 19656, "cia_timer"), song=song)
    t.run_init()
    t.run_calls(2)
    return t.trace()


EXTRA = {
    "irq": (build_irq, "a9509adaf502e0e43c96e49b08848f18244df529934c8445d4857c919c33b2f4"),
    "song0": (
        lambda: build_song(0),
        "2e7fc57fe1338efc1b7847e71c83c254530e40afcc24da0313d00844b4caf2dd",
    ),
    "song1": (
        lambda: build_song(1),
        "00f4ca615cebc57ac0a3a450ca3443ec0c7880a3e74246f77e34bee073e80aa6",
    ),
}


@pytest.mark.parametrize("name", sorted(FIXTURES))
def test_trace_bytes_are_pinned(name, tmp_path):
    assert digest(build(name), tmp_path) == FIXTURES[name][1]


@pytest.mark.parametrize("name", sorted(EXTRA))
def test_entry_shapes_are_pinned(name, tmp_path):
    assert digest(EXTRA[name][0](), tmp_path) == EXTRA[name][1]


def test_the_digest_follows_the_preemption_schedule(tmp_path):
    """A schedule in a one-entry tune's trace is a change of input, so it is a change of digest."""
    trace = build("io")
    before = digest(trace, tmp_path)
    trace.nmilog = {k: np.arange(2, dtype=np.uint32) for k in ("call", "insn", "cycle", "addr")}
    assert digest(trace, tmp_path) != before


def capture(tmp_path):
    """Every digest as JSON: how the table above is regenerated.

    Run it against a reference tree to re-baseline after a deliberate change::

        git archive f713814 | tar -x -C REF
        PYTHONPATH=REF:tests/tuneprog python -c "import test_trace_identity as M, \
            tempfile, pathlib; print(M.capture(pathlib.Path(tempfile.mkdtemp())))"
    """
    got = {n: digest(build(n), tmp_path) for n in sorted(FIXTURES)}
    got.update({n: digest(f(), tmp_path) for n, (f, _h) in sorted(EXTRA.items())})
    return json.dumps(got, indent=1)


def test_every_fixture_hashes_to_its_own_trace(tmp_path):
    """The table is regenerable and no two fixtures collide onto one digest."""
    got = json.loads(capture(tmp_path))
    assert set(got) == set(FIXTURES) | set(EXTRA)
    assert len(set(got.values())) == len(got)
