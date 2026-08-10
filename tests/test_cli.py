"""CLI smoke tests: disasm / pcode / run over a hand-assembled illegal snippet."""

import re

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


def test_run_grid(tmp_path, capsys):
    rc = cli.main(["run", _prg(tmp_path), "--org", "0x1000", "--init", "0x1000"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "D418" not in out  # grid is $D400..$D418 bytes, printed as hex values
    assert "0F" in out  # $D418 volume nibble set by the routine


def _player_prg(tmp_path):
    # INC $1010 ; LDA $1010 ; STA $D400 ; RTS ; ... ; $1010: counter
    prog = bytes([0xEE, 0x10, 0x10, 0xAD, 0x10, 0x10, 0x8D, 0x00, 0xD4, 0x60]) + bytes(7)
    p = tmp_path / "player.prg"
    p.write_bytes(prog)
    return str(p)


def test_decompile_emits_the_frame_program_and_verifies_it(tmp_path, capsys):
    """``--verify`` is the artifact's own laws: fixpoint, rebuild, Gate FP."""
    prg = _player_prg(tmp_path)
    out_file = tmp_path / "player.frameprog"
    rc = cli.main(
        ["decompile", prg, "--org", "0x1000", "--init", "0x1009", "--play", "0x1000"]
        + ["--frames", "4", "--verify", "-o", str(out_file)]
    )
    err = capsys.readouterr().err
    assert rc == 0
    assert "verify ok" in err
    text = out_file.read_text()
    assert text.startswith("frameprog 1\n") and "sub_1000(" in text
    assert " ctr_1010: u8" in text and not re.search(r"@\d|@t\d|@x\(|code\[", text)
    assert "image {" in text and "evidence {" in text  # 3a: the artifact is total


def test_decompile_report_flag(tmp_path, capsys):
    prg = _player_prg(tmp_path)
    rc = cli.main(
        ["decompile", prg, "--org", "0x1000", "--init", "0x1009", "--play", "0x1000"]
        + ["--frames", "4", "--report", "--sound", "-o", str(tmp_path / "p.frameprog")]
    )
    err = capsys.readouterr().err
    assert rc == 0  # no dynamic dispatch: sound build succeeds
    assert "proof report" in err and "[SOUND]" in err


def test_decompile_close_flag(tmp_path, capsys):
    prg = _player_prg(tmp_path)
    rc = cli.main(
        ["decompile", prg, "--org", "0x1000", "--init", "0x1009", "--play", "0x1000"]
        + ["--frames", "4", "--report", "--close", "-o", str(tmp_path / "p.frameprog")]
    )
    err = capsys.readouterr().err
    assert rc == 0
    assert "closure: recurrence at frame" in err  # the 8-bit counter wraps at 256


def test_run_frames(tmp_path, capsys):
    rc = cli.main(
        [
            "run",
            _prg(tmp_path),
            "--org",
            "0x1000",
            "--init",
            "0x1000",
            "--play",
            "0x1000",
            "--frames",
            "3",
        ]
    )
    out = capsys.readouterr().out
    assert rc == 0
    assert out.count("frame") == 3
