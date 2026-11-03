"""tools/pcode2c.py, pcode2mlir.py, structure.py and cplay.py: lifted programs printed as C and
as structured C, compiled and played against the reference interpreter."""

import contextlib
import ctypes
import fcntl
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import wholeprog_progs as W
from deity_informant import NATIVE, trace, wholeprog

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import cmetrics  # noqa: E402  pylint: disable=wrong-import-position
import cplay  # noqa: E402  pylint: disable=wrong-import-position
import pcode2c  # noqa: E402  pylint: disable=wrong-import-position
import pcode2mlir  # noqa: E402  pylint: disable=wrong-import-position
import cslice as slicer  # noqa: E402  pylint: disable=wrong-import-position
import structure  # noqa: E402  pylint: disable=wrong-import-position

pytestmark = pytest.mark.skipif(
    not NATIVE or not shutil.which("gcc"), reason="needs the native extension and gcc"
)

PROGRAMS = {
    "patch": ("PATCH", [0] * 20),
    "toggle": ("TOGGLE", [0] * 6),
    "jumptab": ("JUMPTAB", list(range(8))),
    "pushpull": ("PUSHPULL", list(range(4))),
    "jmpind": ("JMPIND", list(range(8))),
    "nested": ("NESTED", [0] * 5),
    "guarded": ("GUARDED", [0x18] * 4),
    "recurse": ("RECURSE", [0] * 3),
    "correlated": ("CORRELATED", list(range(16))),
    "frame": ("FRAME", list(range(8))),
    "throwaway": ("THROWAWAY", list(range(4))),
    "countdown": ("COUNTDOWN", [0, 3, 7, 1, 5, 2, 6, 4] * 2),
    "pushrts": ("PUSHRTS", list(range(4))),
}
COUNTDOWN = """
entry:  AND #$07
        TAX
        INX
loop:   STX $C010
        TXA
        EOR $C011
        STA $C011
        DEX
        BNE loop
        LDA $C011
        BMI neg
        STA $C012
        RTS
neg:    STA $C013
        RTS
"""
LOOPS = {"countdown"}


@contextlib.contextmanager
def locked(tmp_path_factory):
    """An exclusive lock shared by the xdist workers, for building or pulling images."""
    with open(tmp_path_factory.getbasetemp().parent / "docker.lock", "w", encoding="utf-8") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        yield


@pytest.fixture(name="mlir", scope="session")
def fixture_mlir(tmp_path_factory):
    """The command prefix running the MLIR tools: ``PATH`` or else the ``di-mlir`` image,
    built here when Docker lacks it; skips without either."""
    with locked(tmp_path_factory):
        prefix = structure.runner(build=True)
    if prefix is None:
        pytest.skip("needs mlir-opt or Docker")
    return prefix


def lifted(name):
    """``(image, entry pc, A inputs, funcs)`` of a test program."""
    src, inputs = PROGRAMS[name]
    img, labels = W.assemble(COUNTDOWN if src == "COUNTDOWN" else getattr(W, src))
    pc = labels["entry"]
    facts = trace.collect(img, lambda s: [s.call(pc, a) for a in inputs])
    prog = wholeprog.lift(img, [pc], [W.IO], facts)
    return img, pc, inputs, prog.functions


def play(tmp_path, text, img, pc, inputs):
    """Compile C ``text`` and replay it, RAM included."""
    (tmp_path / "p.c").write_text(text)
    lib = cplay.build(tmp_path / "p.c", tmp_path / "p.so")
    cplay.replay(lib, img, pc, inputs, [W.IO])


@pytest.mark.parametrize("name", sorted(PROGRAMS))
def test_c_matches_reference(name, tmp_path):
    img, pc, inputs, funcs = lifted(name)
    play(tmp_path, pcode2c.emit(funcs, pc), img, pc, inputs)


@pytest.mark.parametrize("name", sorted(PROGRAMS))
def test_structured_c_matches_reference(name, mlir, tmp_path):
    img, pc, inputs, funcs = lifted(name)
    text = structure.structure(pcode2mlir.emit(funcs, pc), mlir)
    assert "goto" not in text and "cf." not in text
    if name in LOOPS:
        assert re.search(r"\b(while|for)\b", text)
    play(tmp_path, text, img, pc, inputs)


def test_replay_detects_a_wrong_constant(tmp_path):
    img, pc, inputs, funcs = lifted("jumptab")
    text = pcode2c.emit(funcs, pc)
    wrong = re.sub(r"^(\s+io_write\(\w+, )", r"\1 1 ^ ", text, count=1, flags=re.M)
    assert wrong != text
    with pytest.raises(AssertionError):
        play(tmp_path, wrong, img, pc, inputs)


def vn(kind, off, size):
    return [kind, off, size]


def function(ops):
    """A one-block function over ``ops`` ending in RETURN."""
    return {
        "pc": 0x1000,
        "entry": True,
        "recursive": False,
        "tables": [],
        "blocks": [{"pc": 0x1000, "ops": [*ops, ["RETURN", None, [None], None]]}],
    }


def op_function(op, sizes, out):
    """Operands of ``sizes`` bytes from $10 and $14; the result of ``op`` (``out`` bytes) to $20."""
    ops = [
        ["LOAD", vn("u", 1, sizes[0]), [vn("c", 0x10, 2)], None],
        ["LOAD", vn("u", 2, sizes[1]), [vn("c", 0x14, 2)], None],
        [op, vn("u", 3, out), [vn("u", 1, sizes[0]), vn("u", 2, sizes[1])], None],
        ["STORE", None, [vn("c", 0x20, 2), vn("u", 3, out)], None],
    ]
    return function(ops)


def shift(left):
    return lambda a, b, s: (a << b if left else a >> b) if b < 8 * s else 0


# op: (operand sizes, result size, python meaning given the operands and the first's width)
OPS = {
    "INT_ADD": ((2, 2), 2, lambda a, b, s: a + b),
    "INT_SUB": ((2, 2), 2, lambda a, b, s: a - b),
    "INT_MULT": ((1, 1), 1, lambda a, b, s: a * b),
    "INT_AND": ((1, 1), 1, lambda a, b, s: a & b),
    "INT_OR": ((2, 2), 2, lambda a, b, s: a | b),
    "INT_XOR": ((1, 1), 1, lambda a, b, s: a ^ b),
    "INT_LEFT": ((1, 1), 1, shift(True)),
    "INT_RIGHT": ((1, 1), 1, shift(False)),
    "INT_LEFT16": ((2, 1), 2, shift(True)),
    "INT_RIGHT16": ((2, 1), 2, shift(False)),
    "INT_EQUAL": ((1, 1), 1, lambda a, b, s: int(a == b)),
    "INT_NOTEQUAL": ((2, 2), 1, lambda a, b, s: int(a != b)),
    "INT_LESS": ((1, 1), 1, lambda a, b, s: int(a < b)),
    "INT_LESSEQUAL": ((2, 2), 1, lambda a, b, s: int(a <= b)),
    "INT_CARRY": ((1, 1), 1, lambda a, b, s: int(a + b >= 1 << 8 * s)),
    "INT_CARRY16": ((2, 2), 1, lambda a, b, s: int(a + b >= 1 << 8 * s)),
    "BOOL_AND": ((1, 1), 1, lambda a, b, s: int(bool(a) and bool(b))),
    "BOOL_OR": ((1, 1), 1, lambda a, b, s: int(bool(a) or bool(b))),
}
VALUES = {1: [0, 1, 7, 8, 0x7F, 0x80, 0xFF], 2: [0, 1, 8, 0x80, 0x7FFF, 0x8000, 0x1234, 0xFFFF]}


def compiled(tmp_path, kind, text):
    """The library of C ``text`` and its memory."""
    (tmp_path / f"{kind}.c").write_text(text)
    lib = cplay.build(tmp_path / f"{kind}.c", tmp_path / f"{kind}.so")
    return lib, (ctypes.c_uint8 * 65536).in_dll(lib, "M")


UNARY = {
    "INT_NEGATE": (1, 1, [], lambda a: ~a),
    "INT_2COMP": (2, 2, [], lambda a: -a),
    "BOOL_NEGATE": (1, 1, [], lambda a: int(a == 0)),
    "INT_ZEXT": (1, 2, [], lambda a: a),
    "SUBPIECE": (2, 1, [vn("c", 1, 4)], lambda a: a >> 8),
    "COPY": (2, 1, [], lambda a: a),
}


def unary_function(op, size, out, extra):
    """The first operand of ``size`` bytes from $10 through ``op`` to $20 (``out`` bytes)."""
    ops = [
        ["LOAD", vn("u", 1, size), [vn("c", 0x10, 2)], None],
        [op, vn("u", 3, out), [vn("u", 1, size), *extra], None],
        ["STORE", None, [vn("c", 0x20, 2), vn("u", 3, out)], None],
    ]
    return function(ops)


def check_ops(tmp_path, mlir, fn, cases):
    """Both printers' ``play`` on ``fn`` for each ``(memory patch, wanted result at $20)``."""
    texts = {
        "c": pcode2c.emit([fn], 0x1000),
        "mlir": structure.structure(pcode2mlir.emit([fn], 0x1000), mlir),
    }
    libs = {kind: compiled(tmp_path, kind, text) for kind, text in texts.items()}
    for patch, skip, want in cases:
        for kind, (lib, m) in libs.items():
            if kind in skip:
                continue
            m[:] = bytes(65536)
            for at, data in patch.items():
                m[at : at + len(data)] = data
            lib.play()
            assert bytes(m[0x20 : 0x20 + len(want)]) == want, (kind, patch)


@pytest.mark.parametrize("name", sorted(UNARY))
def test_unary_op_semantics(name, mlir, tmp_path):
    size, out, extra, meaning = UNARY[name]
    fn = unary_function(name, size, out, extra)
    cases = [
        (
            {0x10: a.to_bytes(size, "little")},
            (),
            (meaning(a) & (1 << 8 * out) - 1).to_bytes(out, "little"),
        )
        for a in VALUES[size]
    ]
    check_ops(tmp_path, mlir, fn, cases)


@pytest.mark.parametrize("name", sorted(OPS))
def test_op_semantics(name, mlir, tmp_path):
    sizes, out, meaning = OPS[name]
    fn = op_function(name.removesuffix("16"), sizes, out)
    cases = []
    for a in VALUES[sizes[0]]:
        for b in VALUES[sizes[1]]:
            patch = {0x10: a.to_bytes(sizes[0], "little"), 0x14: b.to_bytes(sizes[1], "little")}
            skip = (
                ("c",) if name.startswith(("INT_LEFT", "INT_RIGHT")) and b >= 8 * sizes[0] else ()
            )
            want = (meaning(a, b, sizes[0]) & (1 << 8 * out) - 1).to_bytes(out, "little")
            cases.append((patch, skip, want))
    check_ops(tmp_path, mlir, fn, cases)


@pytest.fixture(name="framac", scope="session")
def fixture_framac(tmp_path_factory):
    """Pulls the Frama-C image once; skips without Docker."""
    if not shutil.which("docker"):
        pytest.skip("needs Docker")
    with locked(tmp_path_factory):
        subprocess.run(["docker", "pull", "-q", slicer.IMAGE], check=True, capture_output=True)


@pytest.mark.parametrize("structured", [False, True])
def test_slice_matches_reference_and_shrinks(structured, mlir, framac, tmp_path):
    img, pc, inputs, funcs = lifted("countdown")
    text = pcode2c.emit(funcs, pc)
    if structured:
        text = structure.structure(pcode2mlir.emit(funcs, pc), mlir)
    sliced = slicer.slice_c(text)
    assert "_slice_" not in sliced and "sid_bus" not in sliced
    before, after = cmetrics.metrics(text), cmetrics.metrics(sliced)
    assert after["statements"] < before["statements"]
    play(tmp_path, sliced, img, pc, inputs)


def test_metrics_count_control_flow():
    text = (
        "void f() {\n  do {\n    if (a) goto b;\n  } while (x);\n  while (y) {}\n  int32_t v1;\n}\n"
    )
    m = cmetrics.metrics(text)
    assert (m["if"], m["goto"], m["loops"], m["int32_t variables"]) == (1, 1, 2, 1)
    assert "| statements |" in cmetrics.table([])
