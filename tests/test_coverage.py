"""The SLEIGH 6510 illegal set must equal the Python lifter's illegal set.

This is the single-source-of-truth guard: the executable truth (deity_informant.OPS,
oracle-validated) and the decompiler truth (6510_illegal.sinc) are independent
encodings, so they must not silently diverge. Where pypcode is installed, this
also compiles+loads the 6510 spec and asserts it decodes every illegal opcode
(stock 6502 throws BadDataError on all of them).
"""

import re
from pathlib import Path

import pytest

from deity_informant import ILLEGAL_OPCODES

ROOT = Path(__file__).resolve().parent.parent
SINC = ROOT / "ghidra" / "6510" / "data" / "languages" / "6510_illegal.sinc"


def _sinc_opcodes():
    """Expand every opcode byte the illegal .sinc defines."""
    text = SINC.read_text()
    # OP3 RMW subtable: collect the bbb values it maps (bbb=N).
    op3_rows = re.findall(r"^OP3:.*?is\s+bbb=(\d)", text, re.M)
    op3_bbb = sorted(int(b) for b in op3_rows)
    ops = set()
    for line in text.splitlines():
        if not line.startswith(":"):
            continue
        pat = line.split(" is ", 1)[1] if " is " in line else ""
        pat = pat.split("{", 1)[0]
        # explicit op=0xNN (possibly several OR'd)
        for m in re.findall(r"op=0x([0-9A-Fa-f]{2})", pat):
            ops.add(int(m, 16))
        # RMW pattern: (cc=C & aaa=A) ... & OP3 -> expand over OP3's bbb set
        cc = re.search(r"cc=(\d)", pat)
        aaa = re.search(r"aaa=(\d)", pat)
        if cc and aaa and "OP3" in pat:
            c, a = int(cc.group(1)), int(aaa.group(1))
            for b in op3_bbb:
                ops.add((a << 5) | (b << 2) | c)
    return ops


def test_sinc_opcode_set_equals_python_illegal_set():
    sinc = _sinc_opcodes()
    py = set(ILLEGAL_OPCODES)
    assert sinc == py, "sinc-only: %s ; python-only: %s" % (
        sorted("%02X" % o for o in sinc - py),
        sorted("%02X" % o for o in py - sinc),
    )
    assert len(sinc) == 105


# ---- pypcode-gated: the spec actually compiles and decodes the illegals ------
# The ctx6510 fixture (session-scoped: build once) lives in tests/conftest.py.
pypcode = pytest.importorskip("pypcode")


def test_6510_decodes_every_illegal(ctx6510):
    fails = []
    for op in sorted(ILLEGAL_OPCODES):
        try:
            d = ctx6510.disassemble(bytes([op, 0x10, 0x20]), 0x1000, 0)
            if not d.instructions:
                fails.append("%02X:empty" % op)
        except Exception as e:  # noqa: BLE001
            fails.append("%02X:%s" % (op, type(e).__name__))
    assert not fails, "6510 failed to decode: " + ", ".join(fails)


def test_stock_6502_rejects_illegals():
    ctx = pypcode.Context("6502:LE:16:default")
    # a spot-check: stock 6502 must NOT decode these (proves the extension matters)
    for op in (0x07, 0x47, 0xAB, 0x8B, 0x9F, 0x02):
        with pytest.raises(Exception):
            ctx.disassemble(bytes([op, 0x10, 0x20]), 0x1000, 0)
