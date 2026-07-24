"""CLI smoke tests: disasm / pcode / run over a hand-assembled illegal snippet."""

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


def test_decompile_verify_and_sidc_run(tmp_path, capsys):
    prg = _player_prg(tmp_path)
    out_file = tmp_path / "player.sidc"
    rc = cli.main(
        ["decompile", prg, "--org", "0x1000", "--init", "0x1009", "--play", "0x1000"]
        + ["--frames", "4", "--verify", "-o", str(out_file)]
    )
    err = capsys.readouterr().err
    assert rc == 0
    assert "verify ok" in err
    text = out_file.read_text()
    assert text.startswith("sidc 0\n") and "proc $1000 {" in text and "image {" in text

    rc = cli.main(["sidc-run", str(out_file), "--frames", "3"])
    out = capsys.readouterr().out
    assert rc == 0
    rows = [line.split(":")[1].split() for line in out.splitlines()]
    assert [r[0] for r in rows] == ["01", "02", "03"]  # $D400 counts up per frame
    assert all(r[24] == "0F" for r in rows)  # driver volume visible in the grid


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
