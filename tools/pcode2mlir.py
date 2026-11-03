"""Print a whole-program lift (``wholeprog.Program.functions``) as MLIR.

``python tools/pcode2mlir.py FUNCS.pkl --entry PC [-o OUT.mlir]``: a ``func.func`` per function
over ``cf``/``arith``; memory is the external ``memref.global @M``, registers are one-cell
globals and volatile accesses, ``deopt`` and ``codechk`` are calls to external functions.
"""

import argparse
import pickle
import sys
from collections import defaultdict
from pathlib import Path

from pcode2c import REG

BIN = {
    "INT_ADD": "addi",
    "INT_SUB": "subi",
    "INT_AND": "andi",
    "INT_OR": "ori",
    "INT_XOR": "xori",
    "INT_MULT": "muli",
}
SHIFT = {"INT_LEFT": "shli", "INT_RIGHT": "shrui"}
CMP = {
    "INT_EQUAL": "eq",
    "INT_NOTEQUAL": "ne",
    "INT_LESS": "ult",
    "INT_LESSEQUAL": "ule",
}
BOOL = {"BOOL_AND": "andi", "BOOL_OR": "ori"}
EXTERNS = (
    "func.func private @io_read(i16) -> i8",
    "func.func private @io_write(i16, i8)",
    "func.func private @deopt(i16)",
    "func.func private @codechk(i16, i8) -> i8",
)


def ty(size):
    """The MLIR integer type of a varnode of ``size`` bytes."""
    return f"i{8 * size}"


def reg_name(v):
    """The global symbol of register varnode ``v``."""
    return REG.get(v[1], f"s{v[1]}")


class Fn:
    """One lifted function as MLIR text."""

    def __init__(self, fn, label, labels):
        self.fn, self.label, self.labels = fn, label, labels
        self.lines, self.n, self.env = [], 0, {}
        self.regs, cross = {}, set()
        for b in fn["blocks"]:
            defined = set()
            for op in b["ops"]:
                for v in filter(None, op[2]):
                    if v[0] == "u" and tuple(v[1:]) not in defined:
                        cross.add(tuple(v[1:]))
                    if v[0] == "r":
                        self.regs[reg_name(v)] = v[2]
                out = op[1]
                if out and out[0] == "u":
                    defined.add(tuple(out[1:]))
                elif out:
                    self.regs[reg_name(out)] = out[2]
        self.cross = {k: f"%a{k[0]}_{k[1]}" for k in cross}
        self.globals = {r: f"%g_{r}" for r in [*self.regs, "M"]}
        self.zero = "%zero"

    def new(self):
        """A fresh SSA name."""
        self.n += 1
        return f"%t{self.n}"

    def put(self, text):
        """Append an operation line."""
        self.lines.append(f"    {text}")

    def op(self, text, res=True):
        """Append ``text`` as an operation; its result name."""
        name = self.new() if res else ""
        self.put(f"{name} = {text}" if res else text)
        return name

    def const(self, value, size):
        """An ``arith.constant`` of ``size`` bytes."""
        return self.op(f"arith.constant {value & (1 << 8 * size) - 1} : {ty(size)}")

    def fit(self, val, have, want):
        """``val`` of ``have`` bytes as ``want`` bytes: truncated or zero-extended."""
        if have == want:
            return val
        kind = "trunci" if have > want else "extui"
        return self.op(f"arith.{kind} {val} : {ty(have)} to {ty(want)}")

    def cell(self, v):
        """The address and memref type of the storage of ``v``: a register's one-cell global or
        a crossing temporary's rank-0 ``alloca``."""
        if v[0] == "r":
            return f"{self.globals[reg_name(v)]}[{self.zero}]", f"memref<1x{ty(v[2])}>"
        return f"{self.cross[tuple(v[1:])]}[]", f"memref<{ty(v[2])}>"

    def read(self, v):
        """The value of varnode ``v``."""
        if v[0] == "c":
            return self.const(v[1], v[2])
        if v[0] == "u" and tuple(v[1:]) not in self.cross:
            return self.env[tuple(v[1:])]
        at, t = self.cell(v)
        return self.op(f"memref.load {at} : {t}")

    def write(self, v, val):
        """Assign ``val`` to the output varnode ``v``."""
        if v[0] == "u" and tuple(v[1:]) not in self.cross:
            self.env[tuple(v[1:])] = val
            return
        at, t = self.cell(v)
        self.put(f"memref.store {val}, {at} : {t}")

    def byte(self, addr, k):
        """The memory byte ``k`` above address varnode ``addr`` as ``(M, index)``."""
        a = self.read(addr)
        if k:
            a = self.op(f"arith.addi {a}, {self.const(k, 2)} : i16")
        return self.globals["M"], self.op(f"arith.index_castui {a} : i16 to index")

    def load(self, op):
        """The value loaded by ``op``: ``size`` bytes, least significant first."""
        out, addr = op[1], op[2][0]
        if op[3] == "volatile":
            assert out[2] == 1, op
            return self.op(f"func.call @io_read({self.read(addr)}) : (i16) -> i8")
        val = None
        for k in range(out[2]):
            m, i = self.byte(addr, k)
            part = self.fit(self.op(f"memref.load {m}[{i}] : memref<65536xi8>"), 1, out[2])
            if k:
                part = self.shift("INT_LEFT", [(part, out[2]), (self.const(8 * k, 1), 1)], out[2])
                part = self.op(f"arith.ori {val}, {part} : {ty(out[2])}")
            val = part
        return val

    def store(self, op):
        """Emit the ``STORE`` ``op``: bytes least significant first."""
        addr, val = op[2]
        value = self.read(val)
        if op[3] == "volatile":
            assert val[2] == 1, op
            self.op(f"func.call @io_write({self.read(addr)}, {value}) : (i16, i8) -> ()", False)
            return
        for k in range(val[2]):
            part = value
            if k:
                part = self.shift("INT_RIGHT", [(value, val[2]), (self.const(8 * k, 1), 1)], val[2])
            m, i = self.byte(addr, k)
            self.put(f"memref.store {self.fit(part, val[2], 1)}, {m}[{i}] : memref<65536xi8>")

    def dummy(self, op):
        """A throw-away bus read: kept only when volatile, guarded by its second input."""
        if op[3] != "volatile":
            return
        addr = self.read(op[2][0])
        pred = (*op[2], None)[1]
        if pred is None:
            self.op(f"func.call @io_read({addr}) : (i16) -> i8")
            return
        self.put(f"scf.if {self.truth(self.read(pred), pred[2])} {{")
        self.op(f"func.call @io_read({addr}) : (i16) -> i8")
        self.put("}")

    def truth(self, val, size):
        """``val != 0`` as ``i1``."""
        return self.op(f"arith.cmpi ne, {val}, {self.const(0, size)} : {ty(size)}")

    def shift(self, name, vals, wide):
        """``a`` shifted by ``b`` at ``wide`` bytes; zero when ``b`` reaches the width."""
        (a, sa), (b, sb) = vals
        a, b = self.fit(a, sa, wide), self.fit(b, sb, wide)
        raw = self.op(f"arith.{SHIFT[name]} {a}, {b} : {ty(wide)}")
        ok = self.op(f"arith.cmpi ult, {b}, {self.const(8 * wide, wide)} : {ty(wide)}")
        return self.op(f"arith.select {ok}, {raw}, {self.const(0, wide)} : {ty(wide)}")

    def flag(self, cond, w):
        """The ``i1`` ``cond`` as a ``w``-byte 0 or 1."""
        return self.op(f"arith.extui {cond} : i1 to {ty(w)}")

    def value(self, op):
        """The value of a pure op, ``out`` bytes wide, each operand taken at its own width."""
        name, w, ins = op[0], op[1][2], op[2]
        vals = [(self.read(v), v[2]) for v in ins if v]
        a, sa = vals[0]
        if name in ("COPY", "INT_ZEXT"):
            return self.fit(a, sa, w)
        if name in BIN or name in CMP:
            wide = max(s for _, s in vals) if name in CMP else max(w, *(s for _, s in vals))
            x, y = (self.fit(v, s, wide) for v, s in vals)
            if name in CMP:
                return self.flag(self.op(f"arith.cmpi {CMP[name]}, {x}, {y} : {ty(wide)}"), w)
            return self.fit(self.op(f"arith.{BIN[name]} {x}, {y} : {ty(wide)}"), wide, w)
        if name in SHIFT:
            wide = max(w, sa)
            return self.fit(self.shift(name, vals, wide), wide, w)
        if name in BOOL:
            x, y = (self.truth(v, s) for v, s in vals)
            return self.flag(self.op(f"arith.{BOOL[name]} {x}, {y} : i1"), w)
        if name == "BOOL_NEGATE":
            return self.flag(self.op(f"arith.cmpi eq, {a}, {self.const(0, sa)} : {ty(sa)}"), w)
        if name == "INT_CARRY":
            t = self.op(f"arith.addi {a}, {vals[1][0]} : {ty(sa)}")
            return self.flag(self.op(f"arith.cmpi ult, {t}, {a} : {ty(sa)}"), w)
        a = self.fit(a, sa, w)
        if name == "INT_NEGATE":
            return self.op(f"arith.xori {a}, {self.const(-1, w)} : {ty(w)}")
        if name == "INT_2COMP":
            return self.op(f"arith.subi {self.const(0, w)}, {a} : {ty(w)}")
        if name == "SUBPIECE":
            moved = self.shift(
                "INT_RIGHT", [(vals[0][0], sa), (self.const(8 * ins[1][1], 1), 1)], sa
            )
            return self.fit(moved, sa, w)
        raise ValueError(name)

    def block(self, i):
        """Emit block ``i``; a mid-block ``CBRANCH`` continues in a new block."""
        self.env, part = {}, 0
        self.lines.append(f"  ^b{i}:")
        for op in self.fn["blocks"][i]["ops"]:
            name = op[0]
            if name == "CBRANCH":
                cond = self.truth(self.read(op[2][0]), op[2][0][2])
                part += 1
                self.put(f"cf.cond_br {cond}, ^b{op[3]}, ^b{i}_{part}")
                self.lines.append(f"  ^b{i}_{part}:")
            elif name == "BRANCH":
                return self.put(f"cf.br ^b{op[3]}")
            elif name == "RETURN":
                return self.put("func.return")
            elif name == "BRANCHIND":
                return self.switch(op, i)
            elif name == "CALL":
                self.put(f"func.call @{self.labels[op[3]]}() : () -> ()")
            elif name == "CALLOTHER" and op[3] == "codechk":
                a, v = (self.read(x) for x in op[2] if x)
                self.write(op[1], self.op(f"func.call @codechk({a}, {v}) : (i16, i8) -> i8"))
            elif name == "CALLOTHER":
                return self.deopt(self.const(op[2][0][1], 2))
            elif name == "STORE":
                self.store(op)
            elif op[1] is None:
                self.dummy(op)
            elif name == "LOAD":
                self.write(op[1], self.load(op))
            else:
                self.write(op[1], self.value(op))
        return self.put(f"cf.br ^b{i + 1}" if i + 1 < len(self.fn["blocks"]) else "func.return")

    def deopt(self, pc):
        """Report a deopt at ``pc`` and return."""
        self.op(f"func.call @deopt({pc}) : (i16) -> ()", False)
        self.put("func.return")

    def switch(self, op, i):
        """A ``BRANCHIND`` dispatching on its function's table; deopt otherwise."""
        target = self.read(op[2][0])
        cases = "".join(f",\n      {pc} : ^b{blk}" for pc, blk in self.fn["tables"][op[3]])
        self.put(f"cf.switch {target} : i16, [\n      default: ^b{i}_d{cases}\n    ]")
        self.lines.append(f"  ^b{i}_d:")
        self.deopt(target)

    def type_of(self, name):
        """The memref type of global ``name``."""
        return "memref<65536xi8>" if name == "M" else f"memref<1x{ty(self.regs[name])}>"

    def emit(self):
        """The MLIR text of the function."""
        body = []
        for i in range(len(self.fn["blocks"])):
            self.block(i)
            body += self.lines
            self.lines = []
        head = [f"  func.func @{self.label}() {{"]
        head += [
            f"    {name} = memref.get_global @{r} : {self.type_of(r)}"
            for r, name in self.globals.items()
        ]
        head += ["    %zero = arith.constant 0 : index"]
        head += [
            f"    {name} = memref.alloca() : memref<{ty(key[1])}>"
            for key, name in sorted(self.cross.items())
        ]
        return head + ["    cf.br ^b0"] + body + ["  }"]


def emit(funcs, entry):
    """The whole program as MLIR text."""
    labels = ["play" if f["entry"] and f["pc"] == entry else f"f_{f['pc']:04X}" for f in funcs]
    fns = [Fn(f, label, labels) for f, label in zip(funcs, labels)]
    regs = defaultdict(set, {n: {1} for n in REG.values()})
    for f in fns:
        for name, size in f.regs.items():
            regs[name].add(size)
    assert all(len(s) == 1 for s in regs.values()), regs
    out = ["module {", "  memref.global @M : memref<65536xi8>"]
    out += [
        f"  memref.global @{n} : memref<1x{ty(*s)}> = dense<0>" for n, s in sorted(regs.items())
    ]
    out += [f"  {e}" for e in EXTERNS]
    for f in fns:
        out += f.emit()
    return "\n".join(out + ["}"])


def main(argv=None):
    """Command line: print the MLIR of a pickled lift."""
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("funcs")
    ap.add_argument("--entry", type=lambda s: int(s, 0), required=True)
    ap.add_argument("-o", "--out")
    args = ap.parse_args(argv)
    text = emit(pickle.loads(Path(args.funcs).read_bytes()), args.entry) + "\n"
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
