"""S1 differential: the ``Trace`` the front end consumes is byte-for-byte pinned.

One fixture per recorded mechanism -- per-op access sets, SMC opcode and operand
cells, index domains, every control-kind edge, JSR/RTS pairing, register
summaries, inputs, IO logs, both footprint hashes -- hashed after serialisation.
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
    for log in (trace.wlog, trace.iolog):
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
    "calls": (f_calls, "617337b91f65ddd7e4e4801910c75971bb5a3addcdbefbe1b1857352e3d261a0"),
    "indexed": (f_indexed, "33f2ed505af0eabdc15cf2c561214d8597862e9f548a0cd583d3a2b8ce926279"),
    "io": (f_io, "4177514f959dd56714bc053fdf7d3094caa910362fa7e72dc4dd7b7ace2c91dc"),
    "jmpind": (f_jmpind, "b7c3e7c90bda340724de5e3b369e460321ab58e49b5547f3149cc7f9063ce410"),
    "period": (f_period, "d13801078c2c53256cc510cad3a5ccc6cca93c10d7df22aee4de48cea5ab4658"),
    "pointer": (f_pointer, "8b6b93e10131c7275e908298ddee67fae1f764cd48baa0d5039b929f8eeae613"),
    "rts_trick": (f_rts_trick, "2fbe563c697f80e431684ee716d6725b8fc20d8a2c9b6148c14fb64693d7c345"),
    "smc": (f_smc, "3953b32b75827f466ea6eea4059460a4288930c7d5cb7364312486ac7af0f1a3"),
    "smc_revert": (
        f_smc_revert,
        "1c59a6d8601fa78c751521e0d1a398f628f05c30a27e5fc79e4096f71d950e7b",
    ),
    "smc_under_io": (
        f_smc_under_io,
        "4e5bcac6e1884087730e42d463dd63c749c2c0623a61cc32f9b85a6e1d73095a",
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
    "irq": (build_irq, "4058e2e77484fb6fa373c5afa23594074fe4c38584beb8c5b91a2599c7f1d293"),
    "song0": (
        lambda: build_song(0),
        "76e0e40123f3f3d2228211b3725e1a95e80dce20ef56bf1ee4b797946a752e9c",
    ),
    "song1": (
        lambda: build_song(1),
        "bf3787cb9b71bae8e969354e04892443f11e999697b7e1324af12d3408b63639",
    ),
}


@pytest.mark.parametrize("name", sorted(FIXTURES))
def test_trace_bytes_are_pinned(name, tmp_path):
    assert digest(build(name), tmp_path) == FIXTURES[name][1]


@pytest.mark.parametrize("name", sorted(EXTRA))
def test_entry_shapes_are_pinned(name, tmp_path):
    assert digest(EXTRA[name][0](), tmp_path) == EXTRA[name][1]


def capture(tmp_path):
    """Every digest as JSON: how the tables above are filled after a deliberate change."""
    got = {n: digest(build(n), tmp_path) for n in sorted(FIXTURES)}
    got.update({n: digest(f(), tmp_path) for n, (f, _h) in sorted(EXTRA.items())})
    return json.dumps(got, indent=1)
