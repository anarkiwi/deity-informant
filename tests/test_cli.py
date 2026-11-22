"""CLI smoke tests: disasm / pcode / run over a hand-assembled illegal snippet."""

import pytest

from deity_informant import cli


def _prg(tmp_path):
    # LDA #$0F ; STA $D418 ; SRE $4F ; LAX $2000,Y ; RTS
    prog = bytes([0xA9, 0x0F, 0x8D, 0x18, 0xD4, 0x47, 0x4F, 0xBF, 0x00, 0x20, 0x60])
    p = tmp_path / "demo.prg"
    p.write_bytes(prog)
    return str(p)


def test_disasm_flags_illegals(tmp_path, capsys):
    rc = cli.main(["disasm", _prg(tmp_path), "--org", "0x1000"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "SRE $4f" in out and "; illegal" in out
    assert "LAX $2000,Y" in out
    assert "LDA #$0f" in out  # legal ones are not flagged
    assert out.count("; illegal") == 2


def test_pcode_dump(tmp_path, capsys):
    rc = cli.main(["pcode", _prg(tmp_path), "--org", "0x1000", "--at", "0x1005"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "SRE" in out and "STORE" in out and "ctrl=('next',)" in out


@pytest.mark.parametrize(
    "extra,check",
    [
        ([], lambda out: "D418" not in out and "0F" in out),
        (["--play", "0x1000", "--frames", "3"], lambda out: out.count("frame") == 3),
    ],
    ids=["grid", "frames"],
)
def test_run(tmp_path, capsys, extra, check):
    """``run`` prints the SID grid after init as hex values ($D418's volume nibble ``0F``, no
    register names), or one block per frame of play with ``--frames``."""
    rc = cli.main(["run", _prg(tmp_path), "--org", "0x1000", "--init", "0x1000", *extra])
    assert rc == 0 and check(capsys.readouterr().out)


def test_emit_sleigh_installs_the_module(tmp_path):
    pytest.importorskip("pypcode")
    dst = tmp_path / "6510" / "data" / "languages"
    assert cli.main(["emit-sleigh", "-o", str(dst)]) == 0
    assert (dst / "6510.sla").stat().st_size > 0
    assert {"6510.ldefs", "6510_smc.sinc", "6502.slaspec"} <= {p.name for p in dst.iterdir()}
    assert (tmp_path / "6510" / "Module.manifest").is_file()
