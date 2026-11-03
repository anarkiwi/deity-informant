"""deity_informant.cgen: programs translated to C, compiled with gcc and played against the
reference interpreter; relocation and data invariance of the ``.c``; llvm-mos builds it."""

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
from deity_informant import NATIVE, cgen, trace
from deity_informant.cgen import mlir

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import cgenplay  # noqa: E402  pylint: disable=wrong-import-position
import cmetrics  # noqa: E402  pylint: disable=wrong-import-position
from test_pcode2mlir import OPS, UNARY, VALUES, op_function, unary_function  # noqa: E402

pytestmark = pytest.mark.skipif(
    not NATIVE or not shutil.which("gcc") or not shutil.which("clang-format"),
    reason="needs the native extension, gcc and clang-format",
)
MOS = "ghcr.io/anarkiwi/docker-mos-llvm-sdk:v23.4.0"

TABLES = """
entry:  AND #$03
        TAX
        CLC
        LDA acc
        ADC lo,X
        STA acc
        LDA acc+1
        ADC hi,X
        STA acc+1
        STA $C000
        LDA acc
        STA $C001
        RTS
lo:     .byte $34,$FF,$80,$01
hi:     .byte $12,$00,$7F,$FF
acc:    .byte 0,0
end:
"""
SUBS = """
entry:  AND #$07
        TAX
        JSR emit
        INX
        JSR emit
        JSR twice
        RTS
twice:  INX
        JSR emit
        DEX
emit:   LDA tab,X
        STA $C002
        STX $C003
        RTS
tab:    .byte 1,2,3,4,5,6,7,8,9,10,11,12
end:
"""
PTRS = """
entry:  AND #$01
        ASL A
        TAX
        LDA ptrs,X
        STA $FB
        LDA ptrs+1,X
        STA $FC
        LDY #$00
loop:   LDA ($FB),Y
        BEQ done
        STA $C010
        INY
        BNE loop
done:   RTS
ptrs:   .byte lo(s0),hi(s0),lo(s1),hi(s1)
s0:     .byte 1,2,3,0
s1:     .byte 9,8,0
end:
"""
OOB = """
tab:    .byte 1,2,3
entry:  LDX idx
        LDA tab,X
        STA $C020
        INC idx
        RTS
idx:    .byte 0
end:
"""
SMCOP = """
entry:  AND #$01
        BEQ add
        LDA #$E9
        STA site
        BNE go
add:    LDA #$69
        STA site
go:     LDA #$10
        SEC
site:   ADC #$03
        STA $C001
        RTS
end:
"""
ILLEGAL = """
entry:  STA $FB
        .byte $A7,$FB
        STX $C030
        .byte $8F,$31,$C0
        .byte $CF,lo(cnt),hi(cnt)
        PHP
        PLA
        STA $C032
        .byte $EF,lo(cnt),hi(cnt)
        STA $C033
        .byte $0F,lo(cnt),hi(cnt)
        STA $C034
        .byte $6F,lo(cnt),hi(cnt)
        STA $C035
        LDA cnt
        STA $C036
        RTS
cnt:    .byte $5A
end:
"""
UNTAKEN = """
entry:  LDX #$00
loop:   LDA tab,X
        BMI neg
        STA $C040
        INX
        CPX #$04
        BNE loop
        RTS
neg:    EOR #$FF
        STA $C041
        INX
        CPX #$04
        BNE loop
        RTS
tab:    .byte 1,2,3,4
end:
"""
SENS = """
entry:  TAX
        LDA $C0F0,X
        STA $C010
        LDA $C000
        STA $C011
        RTS
end:
"""
LOHI = """
entry:  AND #$03
        TAY
        LDA tlo,Y
        STA ptr
        LDA thi,Y
        STA ptr+1
        JMP (ptr)
j0:     LDA msg
        STA $C050
        RTS
j1:     LDA msg+1
        STA $C051
        RTS
j2:     LDX msg+2
        STX $C052
        RTS
j3:     STY $C053
        RTS
tlo:    .byte lo(j0),lo(j1),lo(j2),lo(j3)
thi:    .byte hi(j0),hi(j1),hi(j2),hi(j3)
msg:    .byte $11,$22,$33
ptr:    .byte 0,0
end:
"""
CLEAR = """
entry:  CMP #$01
        BEQ clear
        INC ops+1
ops:    LDA #$11
        STA $C060
        LDA #$22
        STA $C061
        LDA #$33
        STA $C062
        RTS
clear:  LDX #$0A
loop:   LDA #$00
        STA ops+1,X
        TXA
        SEC
        SBC #$05
        TAX
        BPL loop
        RTS
end:
"""
FLIP = """
entry:  CMP #$02
        BCS run
        LSR A
        LDA #$69
        BCC fill
        LDA #$E9
fill:   LDX #$05
set:    STA ops,X
        DEX
        DEX
        DEX
        DEX
        DEX
        BPL set
        RTS
run:    LDA #$10
        SEC
ops:    ADC #$03
        STA $C060
        ADC #$04
        STA $C061
        RTS
end:
"""
RELOC = """
entry:  AND #$03
        TAX
        LDA #lo(tab)
        STA $FB
        LDA #hi(tab)
        STA $FC
        LDA #lo(tab)
        STA op+1
        LDA #hi(tab)
        STA op+2
op:     LDA $FFFF,X
        STA $C050
        TXA
        TAY
        LDA ($FB),Y
        JSR emit
        LDA ptrs,X
        STA $FD
        LDA ptrs+1,X
        STA $FE
        LDA ($FD),Y
        JSR emit
        RTS
emit:   CLC
        ADC tab,X
        STA $C051
        RTS
tab:    .byte 3,1,4,1,5,9,2,6
ptrs:   .byte lo(tab),hi(tab),lo(tab+2),hi(tab+2),lo(tab+4),hi(tab+4),lo(tab+5),hi(tab+5)
end:
"""


@contextlib.contextmanager
def locked(tmp_path_factory):
    """An exclusive lock shared by the xdist workers, for building or pulling images."""
    with open(tmp_path_factory.getbasetemp().parent / "docker.lock", "w", encoding="utf-8") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        yield


@pytest.fixture(name="prefix", scope="session")
def fixture_prefix(tmp_path_factory):
    """The command prefix running ``mlir-opt``; skips without it or Docker."""
    with locked(tmp_path_factory):
        prefix = mlir.runner(build=True)
    if prefix is None:
        pytest.skip("needs mlir-opt or Docker")
    return prefix


def assemble(src, org=0x1000, patch=None):
    """``(image, labels, loaded range)`` of ``src`` at ``org``, ``patch`` bytes at labels."""
    img, labels = W.assemble(src, org)
    for label, data in (patch or {}).items():
        img[labels[label] : labels[label] + len(data)] = bytes(data)
    return img, labels, (org, labels["end"])


def translate(prefix, img, labels, loaded, entries=("entry",), facts=None, sensitive=()):
    """``(c, h, info)`` of the program."""
    ents = [(labels[n], n) for n in entries]
    return cgen.translate(
        img, ents, [W.IO], sensitive, facts, loaded=loaded, header="t.h", prefix=prefix
    )


def play(tmp_path, c, h, img, labels, calls, sensitive=()):
    """Compile ``c``/``h`` and replay ``calls`` ``[(name, a)]`` against the reference."""
    (tmp_path / "t.c").write_text(c)
    (tmp_path / "t.h").write_text(h)
    lib = cgenplay.build(tmp_path / "t.c", tmp_path / "t.so")
    names = {n for n, _ in calls}
    return cgenplay.replay(lib, img, {n: labels[n] for n in names}, calls, [W.IO], sensitive)


def check(prefix, tmp_path, src, calls, **kw):
    """Translate, play and return ``(c, h, info)``; the ``.c`` has no ``goto``."""
    img, labels, loaded = assemble(src)
    c, h, info = translate(prefix, img, labels, loaded, **kw)
    assert cmetrics.metrics(c)["goto"] == 0
    play(tmp_path, c, h, img, labels, calls, kw.get("sensitive", ()))
    return c, h, info


def entry_calls(values, n=1):
    """``entry`` called with each ``A`` of ``values``, ``n`` times over."""
    return [("entry", a) for a in values] * n


@pytest.mark.parametrize(
    "src,values",
    [
        (TABLES, range(4)),
        (SUBS, range(8)),
        (PTRS, range(2)),
        (ILLEGAL, range(0, 256, 7)),
        (W.RECURSE + "end:\n", range(2)),
    ],
    ids=["tables", "subs", "ptrs", "illegal", "recurse"],
)
def test_matches_reference(prefix, tmp_path, src, values):
    c, _, _ = check(prefix, tmp_path, src, entry_calls(values, 8))
    assert "trap(" not in c.split("void entry")[1]


def test_pointer_reads_go_through_regions(prefix, tmp_path):
    c, h, info = check(prefix, tmp_path, PTRS, entry_calls([0, 1, 1, 0]))
    assert info["unknown_access"] and "rd(" in c
    assert all(f"R{k}_ORG" in h for k in range(len(info["regions"])))


def test_read_off_a_table_into_code(prefix, tmp_path):
    c, _, info = check(prefix, tmp_path, OOB, entry_calls([0], 64))
    assert "oob_rd" in c and "CODE[" in c
    assert info["code_span"][0] == W.assemble(OOB)[1]["entry"]


def test_smc_operand_cells(prefix, tmp_path):
    img, labels = W.assemble(W.PATCH)
    c, h, _ = cgen.translate(
        img,
        [(labels["entry"], "entry")],
        [W.IO],
        loaded=(0x1000, 0x1921),
        header="t.h",
        prefix=prefix,
    )
    play(tmp_path, c, h, img, labels, entry_calls([0] * 20))


def test_smc_opcode_cell(prefix, tmp_path):
    c, _, _ = check(prefix, tmp_path, SMCOP, entry_calls([0, 1, 1, 0, 1], 4))
    assert "0xE9" in c and "0x69" in c


@pytest.mark.parametrize("name", ["JUMPTAB", "JMPIND", "PUSHRTS"])
def test_computed_jumps_from_facts(prefix, tmp_path, name):
    img, labels = W.assemble(getattr(W, name))
    pc = labels["entry"]
    facts = trace.collect(img, lambda s: [s.call(pc, a) for a in range(4)])
    c, h, info = cgen.translate(
        img,
        [(pc, "entry")],
        [W.IO],
        facts=facts,
        loaded=(0x1000, 0x1900),
        header="t.h",
        prefix=prefix,
    )
    assert info["facts_edges"]
    assert cmetrics.metrics(c)["goto"] == 0
    play(tmp_path, c, h, img, labels, entry_calls(range(4), 3))


@pytest.mark.parametrize("name", ["LOHI", "JMPIND", "JUMPTAB", "PUSHRTS"])
def test_computed_jumps_from_the_code(prefix, tmp_path, name):
    src = LOHI if name == "LOHI" else getattr(W, name) + "end:\n"
    c, _, info = check(prefix, tmp_path, src, entry_calls(range(4), 3))
    [targets] = info["jumps"].values()
    assert len(targets) == (2 if name == "PUSHRTS" else 4)
    assert all(key[0] != "ind" for key in info["traps"].values())
    assert name == "PUSHRTS" or not info["traps"]
    org = info["code_span"][0]
    assert all(f"CODE_ORG + 0x{t - org:X}" in c for t in targets[:-1])


@pytest.mark.parametrize(
    "edit",
    [
        ("AND #$03\n", ""),
        ("entry:  AND #$03", "entry:  LDX tlo\n        STX tlo\n        AND #$03"),
    ],
    ids=["unbounded", "written"],
)
def test_unresolved_jump_traps(prefix, tmp_path, edit):
    img, labels, loaded = assemble(LOHI.replace(*edit))
    c, h, info = translate(prefix, img, labels, loaded)
    assert not info["jumps"]
    k = next(k for k, key in info["traps"].items() if key[0] == "ind")
    assert f"trap({k})" in c
    with pytest.raises(AssertionError, match="trap"):
        play(tmp_path, c, h, img, labels, [("entry", 1)])
    facts = trace.collect(img, lambda s: [s.call(labels["entry"], a) for a in range(4)])
    c, h, info = translate(prefix, img, labels, loaded, facts=facts)
    assert info["facts_edges"] and not info["jumps"]
    (tmp_path / "facts").mkdir()
    play(tmp_path / "facts", c, h, img, labels, entry_calls(range(4), 2))


def test_jump_table_data_and_relocation_invariance(prefix, tmp_path):
    outs = []
    runs = ((0x1000, [0x11, 0x22, 0x33]), (0x1000, [0x44, 0x55, 0x66]), (0x2345, [1, 2, 3]))
    for i, (org, msg) in enumerate(runs):
        img, labels, loaded = assemble(LOHI, org, patch={"msg": msg})
        c, h, _ = translate(prefix, img, labels, loaded)
        (tmp_path / str(i)).mkdir()
        play(tmp_path / str(i), c, h, img, labels, entry_calls(range(4), 2))
        outs.append((c, h))
    assert outs[0][0] == outs[1][0] == outs[2][0] and len({h for _, h in outs}) == 3
    assert not re.search(r"0x1[0-9A-F]{3}\b|0x23[0-9A-F]{2}\b", outs[0][0])


def test_indexed_stores_clear_operand_cells(prefix, tmp_path):
    c, _, info = check(prefix, tmp_path, CLEAR, entry_calls([0, 0, 1, 0, 2, 0, 1, 0]))
    assert "codechk" not in c and not info["traps"]
    assert (info["stats"]["cells"], info["stats"]["code_guards"]) == (3, 0)


def test_indexed_store_of_known_opcodes(prefix, tmp_path):
    c, _, info = check(prefix, tmp_path, FLIP, entry_calls([2, 1, 2, 0, 2, 3, 1, 2]))
    assert "codechk" not in c and not info["traps"]
    assert (info["stats"]["opcode_cells"], info["stats"]["code_guards"]) == (2, 0)


def test_untaken_path_and_data_invariance(prefix, tmp_path):
    img_a, labels, loaded = assemble(UNTAKEN)
    img_b, _, _ = assemble(UNTAKEN, patch={"tab": [1, 0x80, 3, 0xFF]})
    c_a, h_a, _ = translate(prefix, img_a, labels, loaded)
    c_b, h_b, _ = translate(prefix, img_b, labels, loaded)
    assert c_a == c_b and h_a != h_b
    assert "0xFF" in c_a
    play(tmp_path, c_a, h_b, img_b, labels, entry_calls([0], 3))


def test_relocation_invariance(prefix, tmp_path):
    outs = []
    for org in (0x1000, 0x2345):
        img, labels, loaded = assemble(RELOC, org)
        c, h, _ = translate(prefix, img, labels, loaded)
        play(tmp_path, c, h, img, labels, entry_calls(range(4), 2))
        outs.append((c, h))
    assert outs[0][0] == outs[1][0] and outs[0][1] != outs[1][1]
    assert not re.search(r"0x1[0-9A-F]{3}\b|0x23[0-9A-F]{2}\b", outs[0][0])


def test_sensitive_reads_are_kept(prefix, tmp_path):
    img, labels, loaded = assemble(SENS)
    calls = entry_calls([0x15, 0x02, 0x20, 0x15])
    plain, _, _ = translate(prefix, img, labels, loaded)
    c, h, _ = translate(prefix, img, labels, loaded, sensitive=[0xC005, 0xC000])
    assert plain.count("io_read") < c.count("io_read")
    play(tmp_path, c, h, img, labels, calls, [0xC005, 0xC000])


def test_mos_build(prefix, tmp_path_factory):
    if not shutil.which("docker"):
        pytest.skip("needs Docker")
    with locked(tmp_path_factory):
        if subprocess.run(
            ["docker", "pull", "-q", MOS], capture_output=True, check=False
        ).returncode:
            pytest.skip("llvm-mos image unavailable")
    img, labels, loaded = assemble(RELOC)
    c, h, _ = translate(prefix, img, labels, loaded)
    out = Path(__file__).resolve().parents[1] / "out" / "cgen-mos"
    out.mkdir(parents=True, exist_ok=True)
    (out / "t.c").write_text(c)
    (out / "t.h").write_text(h)
    cmd = [
        "docker",
        "run",
        "--rm",
        "-v",
        f"{out}:/w",
        "-w",
        "/w",
        MOS,
        "mos-c64-clang",
        "-std=c99",
        "-Os",
        "-Wall",
        "-Wextra",
        "-Werror",
        "-c",
        "t.c",
        "-o",
        "t.o",
    ]
    done = subprocess.run(cmd, capture_output=True, text=True, check=False)
    assert done.returncode == 0, done.stderr


def compiled(tmp_path, c, h):
    """The library of ``c``/``h`` with the harness."""
    (tmp_path / "t.c").write_text(c)
    (tmp_path / "t.h").write_text(h)
    return cgenplay.build(tmp_path / "t.c", tmp_path / "t.so")


@pytest.mark.parametrize("name", sorted(OPS) + sorted(UNARY))
def test_op_semantics(prefix, tmp_path, name):
    """Every P-Code op through MLIR and the printer, on edge values, against its meaning."""
    if name in OPS:
        sizes, out, meaning = OPS[name]
        fn = op_function(name.removesuffix("16"), sizes, out)
    else:
        size, out, extra, unary = UNARY[name]
        sizes, fn = (size, 1), unary_function(name, size, out, extra)
        meaning = lambda a, b, s, f=unary: f(a)  # noqa: E731
    p = cgen.Program([fn], [(0x1000, "entry")], bytes(0x10000), set(), [], loaded=(0x10, 0x22))
    c, h, info = cgen.render(p, header="t.h", prefix=prefix)
    lib = compiled(tmp_path, c, h)
    cells = {}
    for k, (org, n) in enumerate(info["regions"]):
        arr = (ctypes.c_uint8 * n).in_dll(lib, f"R{k}")
        cells.update({org + i: (arr, i) for i in range(n)})
    for a in VALUES[sizes[0]]:
        for b in VALUES[sizes[1]] if name in OPS else [0]:
            for at, v, s in ((0x10, a, sizes[0]), (0x14, b, sizes[1])):
                for i, byte in enumerate(v.to_bytes(s, "little")):
                    if at + i in cells:
                        arr, j = cells[at + i]
                        arr[j] = byte
            lib.call(ctypes.cast(lib.entry, ctypes.c_void_p), 0)
            got = sum(cells[0x20 + i][0][cells[0x20 + i][1]] << 8 * i for i in range(out))
            assert got == meaning(a, b, sizes[0]) & (1 << 8 * out) - 1, (name, a, b)


def test_cli_writes_c_and_header(prefix, tmp_path):
    # pylint: disable=import-outside-toplevel
    from deity_informant.cgen.__main__ import main

    img, labels = W.assemble(W.JUMPTAB)
    pc = labels["entry"]
    facts = trace.collect(img, lambda s: [s.call(pc, a) for a in range(4)])
    (tmp_path / "facts.json").write_text(facts.dumps())
    (tmp_path / "prog.bin").write_bytes(bytes(img[0x1000:0x1100]))
    args = ["--org", "0x1000", "--entry", f"{pc}:entry", "--volatile", "0xC000-0xC100"]
    assert prefix is not None
    out = tmp_path / "prog.c"
    main(
        [str(tmp_path / "prog.bin"), *args, "--facts", str(tmp_path / "facts.json"), "-o", str(out)]
    )
    c, h = out.read_text(), (tmp_path / "prog.h").read_text()
    assert '#include "prog.h"' in c and "case CODE_ORG + " in c
    play(tmp_path, c, h, img, labels, entry_calls(range(4)))


def test_jump_facts_keep_computed_transfers_only():
    img, labels = W.assemble(W.JUMPTAB)
    pc = labels["entry"]
    facts = trace.collect(img, lambda s: [s.call(pc, a) for a in range(4)])
    kept = cgen.jump_facts(facts.to_dict(), bytes(img)).edges
    assert kept and all(img[src] in (0x60, 0x6C, 0x40) for src, _ in kept)
    assert len(kept) < len(facts.edges)


def test_parse_rejects_unknown_syntax():
    with pytest.raises(AssertionError):
        mlir.parse('"builtin.module"() ({\n  this is not mlir\n}) : () -> ()\n')
