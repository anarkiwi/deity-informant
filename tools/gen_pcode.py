"""Generate ``crates/di-pcode/src/templates_gen.rs`` from ``deity_informant.lifter.lift``.

Each opcode is lifted under two sentinel sets (all operand and pointer bytes differ); a
constant that differs must bind via ``prov`` to an operand slot reproducing both lifts,
and everything else must be identical. See docs/native.md.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from deity_informant.lifter import OPS, lift
from deity_informant.vm import PcodeVM

OUT = Path(__file__).resolve().parent.parent / "crates/di-pcode/src/templates_gen.rs"
PC = 0x1234
# (b1, b2, mem[b1], mem[(b1 + 1) & $FF]) per lift; every byte differs between sets.
SENTINELS = ((0x5A, 0xC3, 0x71, 0x9E), (0xA7, 0x3C, 0x18, 0x2B))
IY_INDEX = 0x35
SLOTS = {
    ((1,), "id"): ("B1", lambda b1, b2: b1),
    ((1, 2), "word"): ("Word", lambda b1, b2: b1 | b2 << 8),
    ((1,), "hi1"): ("B1Inc", lambda b1, b2: (b1 + 1) & 0xFF),
    ((2,), "hi1"): ("B2Inc", lambda b1, b2: (b2 + 1) & 0xFF),
}
OPC = {
    "COPY": "Copy",
    "INT_ZEXT": "IntZext",
    "INT_ADD": "IntAdd",
    "INT_SUB": "IntSub",
    "INT_AND": "IntAnd",
    "INT_OR": "IntOr",
    "INT_XOR": "IntXor",
    "INT_LEFT": "IntLeft",
    "INT_RIGHT": "IntRight",
    "INT_EQUAL": "IntEqual",
    "INT_NOTEQUAL": "IntNotEqual",
    "INT_LESS": "IntLess",
    "INT_LESSEQUAL": "IntLessEqual",
    "INT_CARRY": "IntCarry",
    "LOAD": "Load",
    "STORE": "Store",
}
CTRL = {
    "next": "Next",
    "jmp": "Jmp",
    "jmpind": "JmpInd",
    "jsr": "Jsr",
    "rts": "Rts",
    "rti": "Rti",
    "brk": "Brk",
    "jam": "Jam",
}
PEN = {None: "None", "branch": "Branch", "ax": "Ax", "ay": "Ay", "iy": "Iy"}
STK = ("jsr", "brk", "rts", "rti")


def _image(op, b1, b2, p1, p2):
    mem = bytearray(0x10000)
    mem[PC], mem[PC + 1], mem[PC + 2] = op, b1, b2
    mem[b1], mem[(b1 + 1) & 0xFF] = p1, p2
    return mem


def _mask(sz):
    return (1 << (8 * sz)) - 1


def _kind(rec, key):
    return (rec[key] or (None,))[0]


def _vn(vn, slot=None):
    if vn is None:
        return "Vn::NONE"
    sp, off, sz = vn
    if sp == "c":
        return "cs(Slot::%s, %d)" % (slot, sz) if slot else "c(0x%x, %d)" % (off, sz)
    if sp == "r":
        assert sz == 1, vn
        return "r(%d)" % off
    assert sp == "u", vn
    return "u(%d, %d)" % (off, sz)


class Opcode:
    """One opcode's lifts, checked against each other and reduced to a template."""

    def __init__(self, op):
        self.op = op
        self.mn, self.mode = OPS[op]
        self.mems = [_image(op, *s) for s in SENTINELS]
        self.lifts = [lift(m, PC) for m in self.mems]
        self.rec = rec = self.lifts[0]
        for other in self.lifts[1:]:
            for k in ("len", "cyc", "stk"):
                assert other[k] == rec[k], (hex(op), k)
            assert len(other["ops"]) == len(rec["ops"]), "%02X op shape varies" % op
            for k in ("ctrl", "pen"):
                assert _kind(other, k) == _kind(rec, k), "%02X %s kind varies" % (op, k)
        assert rec["stk"] == (rec["ctrl"][0] if rec["ctrl"][0] in STK else None)
        self.pen = _kind(rec, "pen")
        self.slots = self._bind()
        self._check_ctrl_pen()
        self.iy_base = self._iy_base() if self.pen == "iy" else 0

    def _bind(self):
        """``{(op index, input index): slot name}`` for every operand-derived constant."""
        prov = self.rec["prov"]["ops"]
        slots = {}
        for i, (mn, out, ins) in enumerate(self.rec["ops"]):
            for other in self.lifts[1:]:
                omn, oout, oins = other["ops"][i]
                assert omn == mn and oout == out and len(oins) == len(ins), (hex(self.op), i)
                for a, b in zip(ins, oins):
                    assert a[0] == b[0] and a[2] == b[2] and (a[0] == "c" or a[1] == b[1])
            for j, vn in enumerate(ins):
                if vn[0] != "c":
                    continue
                vals = [rec["ops"][i][2][j][1] for rec in self.lifts]
                key = prov.get((i, j))
                if key is None:
                    if len(set(vals)) > 1:
                        raise NotImplementedError(
                            "operand-dependent constant without provenance: %02X op %d input %d"
                            % (self.op, i, j)
                        )
                    continue
                name, fn = SLOTS[(tuple(key[0]), key[1])]
                got = [fn(b1, b2) & _mask(vn[2]) for b1, b2, _p1, _p2 in SENTINELS]
                assert got == vals, (hex(self.op), i, j, got, vals)
                slots[(i, j)] = name
        return slots

    def _check_ctrl_pen(self):
        length = self.rec["len"]
        for (b1, b2, _p1, _p2), r in zip(SENTINELS, self.lifts):
            w = b1 | b2 << 8
            ctrl, pen = r["ctrl"], r["pen"]
            if ctrl[0] == "br":
                fall = (PC + length) & 0xFFFF
                assert ctrl[1][0] == "r" and ctrl[2] in (0, 1)
                assert ctrl[3:] == ((fall + (b1 - 256 if b1 & 0x80 else b1)) & 0xFFFF, fall)
                assert ctrl[1:3] == self.rec["ctrl"][1:3]
            elif ctrl[0] in ("jmp", "jsr", "jmpind"):
                assert ctrl[1] == w and length == 3
            else:
                assert len(ctrl) == 1, ctrl
            if pen is not None:
                want = {"branch": (), "ax": (w,), "ay": (w,), "iy": (b1,)}[pen[0]]
                assert tuple(pen[1:]) == want, (hex(self.op), pen)

    def _iy_base(self):
        """The unique holding the ``(zp)`` pointer the ``,Y`` penalty is computed from."""
        common = None
        for mem, rec, (_b1, _b2, p1, p2) in zip(self.mems, self.lifts, SENTINELS):
            vm = PcodeVM(mem)
            vm.volatile = False
            vm.reg[2] = IY_INDEX
            vm._exec(rec, PC)
            sizes = {out[1]: out[2] for _mn, out, _ins in rec["ops"] if out and out[0] == "u"}
            hits = {off for off, v in vm.uniq.items() if v == p1 | p2 << 8 and sizes[off] == 2}
            common = hits if common is None else common & hits
        assert len(common) == 1, (hex(self.op), common)
        return common.pop()

    def ops(self):
        return [
            "    Op::new(O::%s, %s, %s, %s),"
            % (
                OPC[mn],
                _vn(out),
                _vn(ins[0], self.slots.get((i, 0))),
                _vn(ins[1] if len(ins) > 1 else None, self.slots.get((i, 1))),
            )
            for i, (mn, out, ins) in enumerate(self.rec["ops"])
        ]

    def binds(self):
        """Operand-bound input positions as ``op index << 1 | input index``."""
        return sorted(i << 1 | j for i, j in self.slots)

    def template(self, start, bind):
        rec = self.rec
        ctrl = rec["ctrl"]
        if ctrl[0] == "br":
            kind = "CtrlKind::Br { flag: %d, pol: %d }" % (ctrl[1][1], ctrl[2])
        else:
            kind = "CtrlKind::" + CTRL[ctrl[0]]
        return (
            "    Template { start: %d, n: %d, bind: %d, nbind: %d, len: %d, cyc: %d, "
            "pen: PenKind::%s, ctrl: %s, iy_base: %d }, // %02X %s %s"
            % (
                start,
                len(rec["ops"]),
                bind,
                len(self.slots),
                rec["len"],
                rec["cyc"],
                PEN[self.pen],
                kind,
                self.iy_base,
                self.op,
                self.mn,
                self.mode,
            )
        )


HEADER = """\
// Generated by tools/gen_pcode.py from deity_informant.lifter; do not edit.

use crate::ir::{c, cs, r, u, CtrlKind, Op, Opc as O, PenKind, Slot, Template, Vn};
"""


def generate() -> str:
    codes = [Opcode(op) for op in range(256)]
    pool, table, binds, start = [], [], [], 0
    for o in codes:
        pool.append("    // %02X %s %s" % (o.op, o.mn, o.mode))
        pool += o.ops()
        table.append(o.template(start, len(binds)))
        binds += o.binds()
        start += len(o.rec["ops"])
    uniq = max(
        vn[1] + vn[2]
        for o in codes
        for _mn, out, ins in o.rec["ops"]
        for vn in (out, *ins)
        if vn is not None and vn[0] == "u"
    )
    out = [
        HEADER,
        "/// Unique-space words an instruction needs.",
        "pub const UNIQUE_SIZE: usize = %d;" % uniq,
        "",
        "/// Every opcode's op templates, concatenated.",
        "pub static OPS: [Op; %d] = [" % start,
        *pool,
        "];",
        "",
        "/// Every opcode's operand-bound input positions (`op << 1 | input`), concatenated.",
        "pub static BINDS: [u8; %d] = [" % len(binds),
        *("    %s," % ", ".join(map(str, binds[i : i + 16])) for i in range(0, len(binds), 16)),
        "];",
        "",
        "/// Per-opcode templates.",
        "pub static TEMPLATES: [Template; 256] = [",
        *table,
        "];",
    ]
    return "\n".join(out) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true", help="fail if the checked-in file is stale")
    args = ap.parse_args(argv)
    src = generate()
    if args.check:
        if not OUT.exists() or OUT.read_text() != src:
            print("%s is stale; run tools/gen_pcode.py" % OUT, file=sys.stderr)
            return 1
        return 0
    OUT.write_text(src)
    return 0


if __name__ == "__main__":
    sys.exit(main())
