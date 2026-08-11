"""asm6502: the two-pass label assembler every 6502 emitter in the tree writes through.

The encoding table is ``lifter.OPS``, so what this assembles is exactly what
``lifter.lift`` decodes and ``PcodeVM`` runs. The witness, the canonical example
and the differential fuzzer's generator are all this class: ``ENC`` spells the
legal 6510, ``ENC_ILLEGAL`` adds every undocumented opcode the lifter models for
the pair no legal one spells (``AsmIllegal``, which the fuzz corpus needs).

The duplicate policy is measured, not chosen: ``OPS`` maps no ``(mn, mode)`` pair
to two legal bytes, so lowest-wins and highest-wins agree byte for byte and the
merge moves no fixture; among the illegal-only pairs the lowest byte wins.
"""

from __future__ import annotations

from .lifter import ILLEGAL_OPCODES, MODE_LEN, OPS

ENC = {}
for _op in sorted(OPS):
    if _op not in ILLEGAL_OPCODES:
        ENC.setdefault(OPS[_op], _op)

ENC_ILLEGAL = dict(ENC)
for _op in sorted(OPS):
    if _op in ILLEGAL_OPCODES:
        ENC_ILLEGAL.setdefault(OPS[_op], _op)

ONE_BYTE = frozenset(("imm", "zp", "zpx", "zpy", "indx", "indy", "rel"))
NO_OPERAND = frozenset(("impl", "acc"))


class Asm:
    """Two-pass label assembler emitting legal 6510 machine code at ``org``.

    An operand is an int or ``(kind, label[, offset])`` with kind ``L`` (word),
    ``LOL`` (low byte) or ``HIL`` (high byte); the offset form is how the witness
    patches an instruction's own operand bytes for a computed address.
    """

    ENC = ENC

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
            out.append(self.ENC[(mn, mode)])
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


class AsmIllegal(Asm):
    """The same assembler over ``ENC_ILLEGAL``: the fuzz corpus stresses undocumented
    opcodes, which the witness and the example never emit."""

    ENC = ENC_ILLEGAL
