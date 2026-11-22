"""Print a whole-program lift (``wholeprog.Program.functions``) as MLIR.

``python tools/pcode2mlir.py FUNCS.pkl --entry PC [-o OUT.mlir]``: a ``func.func`` per function
over ``cf``/``arith``; memory is the external ``memref.global @M``, registers are one-cell
globals and volatile accesses, ``deopt`` and ``codechk`` are calls to external functions.
"""

import sys
from collections import defaultdict
from types import SimpleNamespace

from pcode2c import REG, entry_option, lift_of, slot_name, text_cli

from deity_informant.cgen.emit import Body, ty

EXTERNS = (
    "func.func private @io_read(i16) -> i8",
    "func.func private @io_write(i16, i8)",
    "func.func private @deopt(i16)",
    "func.func private @codechk(i16, i8) -> i8",
)


def reg_name(v, pc):
    """The global symbol of register varnode ``v`` in the function at ``pc``."""
    return slot_name(v, pc)


class Fn(Body):
    """One lifted function as MLIR text: :class:`Body`'s ops over global registers, a call
    taking and returning nothing and a ``deopt`` call in place of a trap."""

    def __init__(self, fn, label, labels):  # pylint: disable=super-init-not-called
        self.fn, self.label, self.labels = fn, label, labels
        self.lines, self.n, self.env, self.block_id, self.pc, self.node = [], 0, {}, 0, 0, 0
        self.ctx = SimpleNamespace(keep_dummy=True)
        self.reg_sizes, cross = {}, set()
        for b in fn["blocks"]:
            defined = set()
            for op in b["ops"]:
                for v in filter(None, op[2]):
                    if v[0] == "u" and tuple(v[1:]) not in defined:
                        cross.add(tuple(v[1:]))
                    if v[0] == "r":
                        self.reg_sizes[reg_name(v, self.fn["pc"])] = v[2]
                out = op[1]
                if out and out[0] == "u":
                    defined.add(tuple(out[1:]))
                elif out:
                    self.reg_sizes[reg_name(out, self.fn["pc"])] = out[2]
        self.cross = {k: f"%a{k[0]}_{k[1]}" for k in cross}
        self.globals = {r: f"%g_{r}" for r in self.reg_sizes} | {"M": "%M"}

    def put(self, text):
        """Append an operation line."""
        self.lines.append(f"    {text}")

    def slot(self, v):
        """The address and memref type of the storage of ``v``: a register's one-cell global or
        a crossing temporary's rank-0 ``alloca``."""
        if v[0] == "r":
            g = self.globals[reg_name(v, self.fn["pc"])]
            return f"{g}[%zero]", f"memref<1x{ty(v[2])}>"
        return f"{self.cross[tuple(v[1:])]}[]", f"memref<{ty(v[2])}>"

    def read(self, v):
        """The value of varnode ``v``."""
        if v[0] == "c":
            return self.const(v[1], v[2])
        if v[0] == "u" and tuple(v[1:]) not in self.cross:
            return self.env[tuple(v[1:])]
        at, t = self.slot(v)
        return self.op(f"memref.load {at} : {t}")

    def write(self, v, val):
        """Assign ``val`` to the output varnode ``v``."""
        if v[0] == "u" and tuple(v[1:]) not in self.cross:
            self.env[tuple(v[1:])] = val
            return
        at, t = self.slot(v)
        self.put(f"memref.store {val}, {at} : {t}")

    def ret(self):
        """Return."""
        self.put("func.return")

    def call(self, callee):
        """A ``CALL`` of function index ``callee``."""
        self.put(f"func.call @{self.labels[callee]}() : () -> ()")

    def trap(self, key, op):
        """A deopt at the PC of ``key``."""
        del op
        self.deopt(self.const(key[1], 2))

    def deopt(self, pc):
        """Report a deopt at ``pc`` and return."""
        self.put(f"func.call @deopt({pc}) : (i16) -> ()")
        self.put("func.return")

    def switch(self, op, i):
        """A ``BRANCHIND`` dispatching on its function's table; deopt otherwise."""
        target = self.read(op[2][0])
        cases = "".join(f",\n      {pc} : ^b{blk}" for pc, blk in self.fn["tables"][op[3]])
        t = ty(op[2][0][2])
        self.put(f"cf.switch {target} : {t}, [\n      default: ^b{i}_d{cases}\n    ]")
        self.lines.append(f"  ^b{i}_d:")
        # A switch on an index register (a closed table) has no target address to deopt at.
        self.deopt(target if op[2][0][2] == 2 else self.const(self.fn["blocks"][i]["pc"], 2))

    def type_of(self, name):
        """The memref type of global ``name``."""
        return "memref<65536xi8>" if name == "M" else f"memref<1x{ty(self.reg_sizes[name])}>"

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
        for name, size in f.reg_sizes.items():
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
    return text_cli(__doc__, "funcs", _render, argv, entry_option)


def _render(args):
    return emit(lift_of(args), args.entry) + "\n"


if __name__ == "__main__":
    sys.exit(main())
