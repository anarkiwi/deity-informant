"""tools/pcodeopt.py and pcodec.py: compact structured C against the reference interpreter."""

import re
import shutil
import sys
from pathlib import Path

import pytest

import wholeprog_progs as W
from deity_informant import NATIVE, trace, wholeprog
from test_pcode2mlir import PROGRAMS, lifted, play

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import cmetrics  # noqa: E402  pylint: disable=wrong-import-position
import cplay  # noqa: E402  pylint: disable=wrong-import-position
import pcode2c  # noqa: E402  pylint: disable=wrong-import-position
import pcodec  # noqa: E402  pylint: disable=wrong-import-position

pytestmark = pytest.mark.skipif(
    not NATIVE or not shutil.which("gcc"), reason="needs the native extension and gcc"
)

SAVE_RESTORE = """
entry:  LDA $FB
        PHA
        TXA
        STA $FB
        INC $FB
        LDA $FB
        STA $C010
        PLA
        STA $FB
        RTS
"""
NESTED_SLOTS = """
entry:  LDA $1800
        PHA
        JSR sub
        PLA
        STA $1801
        RTS
sub:    LDA #$09
        PHA
        PLA
        STA $C010
        RTS
"""
READS_X = """
entry:  STX $C010
        INX
        RTS
"""
FLAGS = """
entry:  AND #$0F
        CMP #$08
        BCC low
        ADC #$01
        STA $C011
        RTS
low:    CLC
        ADC $1800
        STA $C012
        RTS
"""
EXTRA = {
    "save_restore": SAVE_RESTORE,
    "nested_slots": NESTED_SLOTS,
    "reads_x": READS_X,
    "flags": FLAGS,
}


def lift_source(src, inputs):
    """``(image, entry, inputs, funcs)`` of assembly ``src`` lifted with facts from ``inputs``."""
    img, labels = W.assemble(src)
    pc = labels["entry"]
    facts = trace.collect(img, lambda s: [s.call(pc, a) for a in inputs])
    return img, pc, inputs, wholeprog.lift(img, [pc], [W.IO], facts).functions


def program(name):
    """The lifted test program ``name`` (the shared set or one of ``EXTRA``)."""
    if name in EXTRA:
        return lift_source(EXTRA[name], [0, 1, 7, 8, 9, 15, 0x1F, 0x80])
    return lifted(name)


def statements(text):
    return cmetrics.metrics(text)["statements"]


@pytest.mark.parametrize("name", sorted(PROGRAMS) + sorted(EXTRA))
def test_compact_matches_reference_with_ram(name, tmp_path):
    img, pc, inputs, funcs = program(name)
    text, info = pcodec.compact_c(funcs, pc)
    (tmp_path / "p.c").write_text(text)
    lib = cplay.build(tmp_path / "p.c", tmp_path / "p.so")
    cplay.replay(lib, img, pc, inputs, [W.IO], skip=info["unobserved"])


@pytest.mark.parametrize("name", sorted(PROGRAMS) + sorted(EXTRA))
def test_compact_never_adds_statements(name):
    _, pc, _, funcs = program(name)
    assert statements(pcodec.compact_c(funcs, pc)[0]) <= statements(pcode2c.emit(funcs, pc))


@pytest.mark.parametrize("name", ["jumptab", "countdown", "flags", "reads_x"])
def test_reducible_programs_have_no_goto(name):
    _, pc, _, funcs = program(name)
    assert "goto" not in pcodec.compact_c(funcs, pc)[0]


def test_unread_scratch_byte_is_not_observable():
    _, pc, _, funcs = program("save_restore")
    text, info = pcodec.compact_c(funcs, pc)
    assert info["unobserved"] == [0xFB] and "FB" not in text.split("*/")[1]


def test_entry_reads_are_reported():
    assert pcodec.compact_c(program("reads_x")[3], program("reads_x")[1])[1]["reads"] == ["X"]
    assert pcodec.compact_c(program("flags")[3], program("flags")[1])[1]["reads"] == []


def test_dead_flags_are_removed():
    _, pc, _, funcs = program("flags")
    text = pcodec.compact_c(funcs, pc)[0]
    assert not re.search(r"\b[NVZ] = ", text)


def test_callee_stack_slots_do_not_clobber_the_caller(tmp_path):
    img, pc, inputs, funcs = program("nested_slots")
    for text in (pcode2c.emit(funcs, pc), pcodec.compact_c(funcs, pc)[0]):
        play(tmp_path, text, img, pc, inputs)


def test_compact_replay_detects_a_mutation(tmp_path):
    img, pc, inputs, funcs = program("jumptab")
    text = pcodec.compact_c(funcs, pc)[0]
    wrong = re.sub(r"^(\s+io_write\(\w+, )", r"\1 1 ^ ", text, count=1, flags=re.M)
    assert wrong != text
    with pytest.raises(AssertionError):
        play(tmp_path, wrong, img, pc, inputs)
