"""asm6502: the two-pass label assembler the 6502 witness emits through.

The encoding table is ``lifter.OPS``, so what this assembles is exactly what
``lifter.lift`` decodes and ``PcodeVM`` runs. ``examples/state_machine_lift.Asm``
and ``tests/_fuzzgen.Asm`` are the same class by copy; stage 4 collapses them here.
"""

from __future__ import annotations

from .lifter import ILLEGAL_OPCODES, MODE_LEN, OPS

_ENC = {}
for _op in sorted(OPS):
    if _op not in ILLEGAL_OPCODES:
        _ENC.setdefault(OPS[_op], _op)

ONE_BYTE = frozenset(("imm", "zp", "zpx", "zpy", "indx", "indy", "rel"))
NO_OPERAND = frozenset(("impl", "acc"))


class Asm:
    """Two-pass label assembler emitting legal 6510 machine code at ``org``.

    An operand is an int or ``(kind, label[, offset])`` with kind ``L`` (word),
    ``LOL`` (low byte) or ``HIL`` (high byte); the offset form is how the witness
    patches an instruction's own operand bytes for a computed address.
    """

    def __init__(self, org):
        self.org, self.items, self.labels = org, [], {}
        self.end = org

    def i(self, mn, mode="impl", operand=None):
        self.items.append(("i", mn, mode, operand))
        return self

    def label(self, name):
        if name in self.labels:
            raise ValueError("duplicate label %r" % (name,))
        self.items.append(("label", name))
        self.labels[name] = None
        return self

    def byte(self, *vals):
        for v in vals:
            self.items.append(("byte", v))
        return self

    def _resolve(self, operand):
        if isinstance(operand, int):
            return operand
        kind, name = operand[0], operand[1]
        base = self.labels[name] + (operand[2] if len(operand) > 2 else 0)
        return {"L": base & 0xFFFF, "LOL": base & 0xFF, "HIL": (base >> 8) & 0xFF}[kind]

    def _addrs(self):
        pc = self.org
        for it in self.items:
            if it[0] == "label":
                self.labels[it[1]] = pc
            else:
                pc += 1 if it[0] == "byte" else MODE_LEN[it[2]]
        self.end = pc

    def assemble(self):
        """Two passes: bind every label to its pc, then encode."""
        self._addrs()
        out, pc = bytearray(), self.org
        for it in self.items:
            if it[0] == "label":
                continue
            if it[0] == "byte":
                out.append(self._resolve(it[1]) & 0xFF)
                pc += 1
                continue
            _, mn, mode, operand = it
            out.append(_ENC[(mn, mode)])
            pc += MODE_LEN[mode]
            if mode in NO_OPERAND:
                continue
            val = self._resolve(operand)
            if mode == "rel":
                delta = val - pc
                if not -128 <= delta <= 127:
                    raise ValueError("branch out of range: %r" % (operand,))
                out.append(delta & 0xFF)
            elif mode in ONE_BYTE:
                out.append(val & 0xFF)
            else:
                out.append(val & 0xFF)
                out.append((val >> 8) & 0xFF)
        return bytes(out)
