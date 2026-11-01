"""A tiny two-pass assembler over jennings: ``label:``, ``.byte``, ``.org`` and ``{expr}``.

Operands may hold ``{expr}`` over labels and the ``lo``/``hi`` helpers; a bare symbolic operand
is wrapped automatically. Returns the 64 KiB image and the label table.
"""

from __future__ import annotations

import re

from jennings.assembler import Assembler
from jennings.devices.mpu6502 import MPU

__all__ = ["assemble", "lo", "hi"]

_ASM = Assembler(MPU())


def lo(v):
    return v & 0xFF


def hi(v):
    return v >> 8 & 0xFF


def assemble(src, org=0x1000):
    """Two-pass assembly of ``src``; the image and the label table."""
    lines = [ln.split(";")[0].strip() for ln in src.strip().splitlines()]
    labels = {}
    img = bytearray(0x10000)
    for final in (False, True):
        img = bytearray(0x10000)
        pc = org
        for ln in lines:
            m = re.match(r"(\w+):\s*(.*)", ln)
            if m:
                labels[m.group(1)] = pc
                ln = m.group(2)
            if not ln:
                continue

            def sub(mt, final=final):
                ns = {"lo": lo, "hi": hi}
                v = eval(mt.group(2), ns, labels) if final else 0x1234  # pylint: disable=eval-used
                return mt.group(1) + ("$%02X" % (v & 0xFF) if mt.group(1) == "#" else "$%04X" % v)

            if ln.startswith(".org"):
                pc = int(ln.split()[1].lstrip("$"), 16)
                continue
            if ln.startswith(".byte"):
                ns = {"lo": lo, "hi": hi}
                vals = [
                    (
                        eval(x.replace("$", "0x"), ns, labels)  # pylint: disable=eval-used
                        if final
                        else 0
                    )
                    for x in ln[5:].split(",")
                ]
                img[pc : pc + len(vals)] = bytes(v & 0xFF for v in vals)
                pc += len(vals)
                continue
            parts = ln.split(None, 1)
            if len(parts) == 2:
                m2 = re.match(r"^(#)(.+)()$|^(\(?)([^,)]+)(.*)$", parts[1])
                g = [x for x in m2.groups() if x is not None] if m2 else []
                if g and g[1] != "A" and g[1][0] not in "${":
                    ln = "%s %s{%s}%s" % (parts[0], g[0], g[1], g[2])
            text = re.sub(r"(#?)\{([^}]*)\}", sub, ln)
            b = _ASM.assemble(text, pc)
            img[pc : pc + len(b)] = bytes(b)
            pc += len(b)
    return img, labels
