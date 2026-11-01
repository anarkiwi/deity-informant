"""tools/wplift.py: lift an image file, save and reuse facts, dump, check."""

import json
import sys
from pathlib import Path

import pytest
from wholeprog_progs import JUMPTAB, assemble

from deity_informant import NATIVE

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import wplift  # noqa: E402 pylint: disable=wrong-import-position,wrong-import-order

pytestmark = pytest.mark.skipif(not NATIVE, reason="needs the native extension")


def test_wplift_roundtrip(tmp_path, capsys):
    img, labels = assemble(JUMPTAB)
    path = tmp_path / "prog.bin"
    path.write_bytes(bytes(img[0x1000:0x1100]))
    e = labels["entry"]
    common = [str(path), "--org", "0x1000", "--entry", hex(e), "--volatile", "0xC000-0xC100"]
    calls = ["--calls", ",".join("%#x:%d" % (e, a) for a in range(4))]
    facts = tmp_path / "f.json"
    assert wplift.main(common + calls + ["--save-facts", str(facts), "--check"]) == 0
    assert wplift.main(common + calls + ["--facts", str(facts), "--cycles", "--check"]) == 0
    dump = tmp_path / "p.txt"
    assert wplift.main(common + calls + ["--facts", str(facts), "--dump", str(dump), "--raw"]) == 0
    lines = [json.loads(ln) for ln in capsys.readouterr().out.splitlines()]
    assert lines[1]["exact"] and lines[0]["ops"] < lines[0]["emitted_ops"]
    assert lines[3]["exact"] and lines[2]["ops"] > lines[0]["ops"]
    assert lines[4]["ops"] == lines[4]["emitted_ops"]
    assert "BRANCHIND" in dump.read_text()
    init = ["--init", "%#x:1" % e, "--facts", str(facts), "--check"]
    assert wplift.main(common + calls + init) == 0
    assert json.loads(capsys.readouterr().out.splitlines()[-1])["exact"]
    loop = tmp_path / "loop.bin"
    loop.write_bytes(bytes([0x4C, 0x00, 0x10]))
    spin = [str(loop), "--org", "0x1000", "--entry", "0x1000", "--calls", "0x1000:0", "--check"]
    assert wplift.main(spin) == 1
    assert json.loads(capsys.readouterr().out.splitlines()[-1])["mismatch"] == [0, "no-return"]
    assert wplift.calls_spec("0x10:2*3,0x20") == [(0x10, 2)] * 3 + [(0x20, 0)]
