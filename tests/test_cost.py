"""Translation cost (docs/cgen.md, *Translation cost*): printed copies of each instruction,
the checks and the pointer definitions they check, and the ``#line`` markers owning the
compiled bytes."""

import re
import shutil
import subprocess

import pytest

import wholeprog_progs as W
from test_cgen import FXSHARE, assemble, cgenplay
from test_cgen import fixture_prefix  # noqa: F401  pylint: disable=unused-import
from deity_informant import NATIVE, cgen, wholeprog
from deity_informant.cgen import cost, marks

pytestmark = pytest.mark.skipif(
    not NATIVE or not shutil.which("gcc") or not shutil.which("clang-format"),
    reason="needs the native extension, gcc and clang-format",
)

#: A cursor init leaves and play advances, read at three offsets of ``Y``.
CURSOR = """
init:   AND #$01
        ASL A
        TAX
        LDA tab,X
        STA $FB
        LDA tab+1,X
        STA $FC
        RTS
play:   LDY #$00
        LDA ($FB),Y
        STA $C000
        INY
        LDA ($FB),Y
        STA $C001
        INY
        LDA ($FB),Y
        STA $C002
        LDA $FB
        CLC
        ADC #$03
        STA $FB
        BCC done
        INC $FC
done:   RTS
tab:    .byte lo(d0),hi(d0),lo(d1),hi(d1)
d0:     .byte 1,2,3,4,5,6
d1:     .byte 7,8,9
end:
"""


def lift(prefix, src, entries=("entry",), lines=False, **kw):
    """``(c, h, info, labels)`` of ``src`` translated with ``entries``."""
    img, labels, loaded = assemble(src)
    out = cgen.translate(
        img,
        [(labels[n], n) for n in entries],
        [W.IO],
        loaded=loaded,
        header="t.h",
        prefix=prefix,
        lines=lines,
        **kw,
    )
    return (*out, labels)


def kinds(emission):
    """``{kind: sites}`` of :func:`cost.sites`."""
    out = {}
    for kind, pc, _ in cost.sites(emission):
        out.setdefault(kind, []).append(pc)
    return out


def test_structuring_copies_count_each_printing(prefix):
    """Tails the structuring prints in each arm entering them, in each procedure holding
    them: every printing of their stores is a copy, the rest of the code printed once."""
    c, _, info, labels = lift(prefix, FXSHARE)
    copies = info["emission"]["copies"]
    t1, t2 = labels["t1"], labels["t2"]
    assert copies[t1] == len(re.findall(r"io_write\(0xC00[01], ", c)) > 1
    assert copies[t2] == len(re.findall(r"io_write\(0xC00[45], ", c)) > 1
    assert {pc for pc, n in copies.items() if n > 1} == {t1, t2}
    e1 = kinds(info["emission"])["E1"]
    assert len(e1) == copies[t1] + copies[t2] - 2


def test_checks_past_one_per_pointer_definition(prefix):
    """A pointer the entry starts with, read at three offsets, each read checked: every read
    has its bytes' entry definitions alone, so every check past the first is redundant, each
    with at most one trap path of its own."""
    c, _, info, _ = lift(prefix, CURSOR, ("init", "play"), init=0, songs=range(2))
    e = info["emission"]
    checks = [x for x in e["checks"] if x["kind"] == "objchk"]
    printed = len(re.findall(r"\bB\d+c\d+\((?!uint)|cgen_out\d+\(v|CGEN_OBJ\d+_ORG\) >", c))
    assert len(checks) == printed > 0
    defs = sorted({d for x in checks for d in x["defs"]})
    assert sorted(e["defs"][d][:4] for d in defs) == [["w", 0xFB, 0, None], ["w", 0xFB, 1, None]]
    assert e["unproven"]["play"] == defs and "init" not in e["unproven"]
    assert len(kinds(e).get("E2", [])) == printed - 1
    assert all(e["traps"].get(x["trap"], 0) <= 1 for x in checks)


def test_pointer_definitions_reach_through_calls():
    """A callee's store defines the pair's byte for the read past the call's return site, the
    caller's earlier store for the read before the call; the other byte keeps its store."""
    src = """
entry:  STA $FB
        LDA #$20
        STA $FC
        LDY #$00
        LDA ($FB),Y
        STA $C000
        JSR set
        LDA ($FB),Y
        STA $C001
        RTS
set:    LDA #$30
        STA $FC
        RTS
end:
"""
    img, labels = W.assemble(src)
    prog = wholeprog.lift(
        img, [labels["entry"]], [W.IO], entry_sp=0xFD, loaded=(0x1000, labels["end"])
    )
    defs, at, e = list(prog.pdefs), dict(prog.pdefs_at), labels["entry"]
    assert sorted(defs[d] for d in at[e + 8]) == [("w", 0xFB, 0, e), ("w", 0xFB, 1, e + 4)]
    assert sorted(defs[d] for d in at[e + 16]) == [
        ("w", 0xFB, 0, e),
        ("w", 0xFB, 1, labels["set"] + 2),
    ]


def test_lines_own_every_byte(prefix, tmp_path):
    """With ``lines`` every compiled byte of the ``.c`` is an original instruction's or an
    artifact's, named by its ``#line`` file; without them the ``.c`` has none."""
    c, h, info, labels = lift(prefix, CURSOR, ("init", "play"), True, init=0, songs=range(2))
    plain = lift(prefix, CURSOR, ("init", "play"), init=0, songs=range(2))[0]
    assert "#line" in c and "#line" not in plain
    (tmp_path / "t.c").write_text(c)
    (tmp_path / "t.h").write_text(h)
    so = tmp_path / "t.so"
    cmd = [
        "gcc",
        *cgenplay.CFLAGS,
        "-g",
        "-o",
        str(so),
        str(tmp_path / "t.c"),
        str(cgenplay.HARNESS),
    ]
    subprocess.run(cmd, check=True)
    own = pytest.importorskip("csize").owners(so, "t.c")
    assert own and {f for f, _ in own} <= set(marks.FILES)
    starts = set(info["starts"])
    assert {n for f, n in own if f == "6502"} <= starts
    e = info["emission"]
    assert {n for f, n in own if f == "check"} <= set(range(1, len(e["checks"]) + 1))
    assert {n - 1 for f, n in own if f == "trap"} <= set(e["traps"])
    assert sum(b for (f, _), b in own.items() if f == "check") > 0
    assert labels["play"] in starts
