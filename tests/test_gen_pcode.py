"""``tools/gen_pcode.py``: the checked-in templates are current, and binding is enforced."""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import gen_pcode  # noqa: E402  pylint: disable=wrong-import-position


def test_checked_in_templates_are_current():
    assert gen_pcode.main(["--check"]) == 0


def test_check_reports_stale_and_write_refreshes(tmp_path, monkeypatch):
    out = tmp_path / "templates_gen.rs"
    monkeypatch.setattr(gen_pcode, "OUT", out)
    assert gen_pcode.main(["--check"]) == 1
    out.write_text("stale")
    assert gen_pcode.main(["--check"]) == 1
    assert gen_pcode.main([]) == 0
    assert gen_pcode.main(["--check"]) == 0


def test_unbound_operand_constant_fails(monkeypatch):
    lift = gen_pcode.lift

    def no_prov(mem, pc):
        rec = lift(mem, pc)
        rec["prov"]["ops"] = {}
        return rec

    monkeypatch.setattr(gen_pcode, "lift", no_prov)
    with pytest.raises(NotImplementedError, match="A9 op 0 input 0"):
        gen_pcode.Opcode(0xA9)
    assert not gen_pcode.Opcode(0xEA).slots


def test_slots_and_iy_base():
    lda_absx = gen_pcode.Opcode(0xBD)
    assert set(lda_absx.slots.values()) == {"Word"}
    sha_indy = gen_pcode.Opcode(0x93)
    assert sha_indy.slots == {(0, 0): "B1", (1, 0): "B1Inc"}
    lda_indy = gen_pcode.Opcode(0xB1)
    defs = {out[1]: mn for mn, out, _ins in lda_indy.rec["ops"] if out and out[0] == "u"}
    assert defs[lda_indy.iy_base] == "INT_OR"
