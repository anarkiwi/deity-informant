"""A whole-program lift as MLIR over ``func``/``cf``/``arith``/``memref``.

Each lifted function is a private ``func.func`` taking and returning the register file; its
registers, stack slots and block-crossing temporaries are ``alloca`` s for ``mem2reg``. Memory
is the global ``@M``; volatile accesses, traps and code checks call external functions. A call
of a function called from more than one site is ``no_inline``.
"""

from __future__ import annotations

from collections import Counter

BIN = {
    "INT_ADD": "addi",
    "INT_SUB": "subi",
    "INT_AND": "andi",
    "INT_OR": "ori",
    "INT_XOR": "xori",
    "INT_MULT": "muli",
}
SHIFT = {"INT_LEFT": "shli", "INT_RIGHT": "shrui"}
CMP = {"INT_EQUAL": "eq", "INT_NOTEQUAL": "ne", "INT_LESS": "ult", "INT_LESSEQUAL": "ule"}
BOOL = {"BOOL_AND": "andi", "BOOL_OR": "ori"}
EXTERNS = {
    "io_read": "(i16) -> i8",
    "io_write": "(i16, i8) -> ()",
    "trap": "(i16) -> ()",
    "codechk": "(i16, i8) -> i8",
}
#: Entry register values besides ``A`` (index: value; others 0): ``SP`` below the return address.
ENTRY_REGS = {3: 0xFD, 16: 0xFF}
NSLOT = 18


def ty(size):
    """The MLIR integer type of ``size`` bytes."""
    return f"i{8 * size}"


def _key(v):
    return ("r", v[1]) if v[0] == "r" else ("u", v[1], v[2])


class Body:  # pylint: disable=too-many-instance-attributes
    """One lifted function as the MLIR text of a private ``func.func``."""

    def __init__(self, fn, name, ctx):
        self.fn, self.name, self.ctx = fn, name, ctx
        self.lines, self.n, self.env, self.block_id = [], 0, {}, 0
        self.cells = {("r", r): s for r, s in ctx.regfile}
        for b in fn["blocks"]:
            defined = set()
            for op in b["ops"]:
                for v in filter(None, op[2]):
                    if v[0] == "r" or (v[0] == "u" and _key(v) not in defined):
                        self.cells[_key(v)] = v[2]
                if op[1]:
                    if op[1][0] == "r":
                        self.cells[_key(op[1])] = op[1][2]
                    defined.add(_key(op[1]))
        self.order = sorted(self.cells, key=lambda k: tuple(map(str, k)))

    def new(self):
        """A fresh SSA name."""
        self.n += 1
        return f"%v{self.n}"

    def put(self, text):
        """Append an operation line."""
        self.lines.append(f"    {text}")

    def op(self, text):
        """Append ``text`` as an operation with a result; its name."""
        name = self.new()
        self.put(f"{name} = {text}")
        return name

    def const(self, value, size):
        """An ``arith.constant`` of ``size`` bytes."""
        return self.op(f"arith.constant {value & (1 << 8 * size) - 1} : {ty(size)}")

    def fit(self, val, have, want):
        """``val`` of ``have`` bytes as ``want`` bytes, truncated or zero-extended."""
        if have == want:
            return val
        kind = "trunci" if have > want else "extui"
        return self.op(f"arith.{kind} {val} : {ty(have)} to {ty(want)}")

    @staticmethod
    def cell(key):
        """The SSA name of a cell's ``alloca``."""
        return "%c_" + "_".join(map(str, key))

    def read(self, v):
        """The value of varnode ``v``."""
        if v[0] == "c":
            return self.const(v[1], v[2])
        if _key(v) not in self.cells:
            return self.env[_key(v)]
        return self.op(f"memref.load {self.cell(_key(v))}[] : memref<{ty(v[2])}>")

    def write(self, v, val):
        """Assign ``val`` to output varnode ``v``."""
        if _key(v) not in self.cells:
            self.env[_key(v)] = val
            return
        self.put(f"memref.store {val}, {self.cell(_key(v))}[] : memref<{ty(v[2])}>")

    def regs(self):
        """The register file's current values."""
        return [self.read(["r", r, s]) for r, s in self.ctx.regfile]

    def ret(self):
        """Return the register file."""
        self.put(f"func.return {', '.join(self.regs())} : {self.ctx.types}")

    def index(self, addr, k):
        """The ``index`` of the byte ``k`` above the 16-bit address value ``addr``."""
        if k:
            addr = self.op(f"arith.addi {addr}, {self.const(k, 2)} : i16")
        return self.op(f"arith.index_castui {addr} : i16 to index")

    def load(self, op):
        """The value of a ``LOAD``: ``size`` bytes, least significant first."""
        out, addr = op[1], self.read(op[2][0])
        if op[3] == "volatile":
            assert out[2] == 1, op
            return self.op(f"func.call @io_read({addr}) : (i16) -> i8")
        val = None
        for k in range(out[2]):
            byte = self.op(f"memref.load %M[{self.index(addr, k)}] : memref<65536xi8>")
            part = self.fit(byte, 1, out[2])
            if k:
                sh = self.const(8 * k, out[2])
                part = self.op(f"arith.shli {part}, {sh} : {ty(out[2])}")
                part = self.op(f"arith.ori {val}, {part} : {ty(out[2])}")
            val = part
        return val

    def store(self, op):
        """A ``STORE``: bytes least significant first."""
        addr, val = op[2]
        a, value = self.read(addr), self.read(val)
        if op[3] == "volatile":
            assert val[2] == 1, op
            self.put(f"func.call @io_write({a}, {value}) : (i16, i8) -> ()")
            return
        for k in range(val[2]):
            part = value
            if k:
                sh = self.const(8 * k, val[2])
                part = self.op(f"arith.shrui {value}, {sh} : {ty(val[2])}")
            part = self.fit(part, val[2], 1)
            self.put(f"memref.store {part}, %M[{self.index(a, k)}] : memref<65536xi8>")

    def dummy(self, op):
        """A throw-away read: an ``io_read`` when kept, made when its predicate is nonzero."""
        if op[3] != "volatile" or not self.ctx.keep_dummy:
            return
        addr = self.read(op[2][0])
        pred = (*op[2], None)[1]
        if pred is None:
            self.op(f"func.call @io_read({addr}) : (i16) -> i8")
            return
        cond, label = self.truth(self.read(pred), pred[2]), f"^b{self.block_id}_r{self.n}"
        self.put(f"cf.cond_br {cond}, {label}, {label}j")
        self.lines.append(f"  {label}:")
        self.op(f"func.call @io_read({addr}) : (i16) -> i8")
        self.put(f"cf.br {label}j")
        self.lines.append(f"  {label}j:")

    def truth(self, val, size):
        """``val != 0`` as ``i1``."""
        return self.op(f"arith.cmpi ne, {val}, {self.const(0, size)} : {ty(size)}")

    def shift(self, name, vals, wide):
        """``a`` shifted by ``b`` at ``wide`` bytes; zero once ``b`` reaches the width."""
        (a, sa), (b, sb) = vals
        a, b = self.fit(a, sa, wide), self.fit(b, sb, wide)
        raw = self.op(f"arith.{SHIFT[name]} {a}, {b} : {ty(wide)}")
        ok = self.op(f"arith.cmpi ult, {b}, {self.const(8 * wide, wide)} : {ty(wide)}")
        return self.op(f"arith.select {ok}, {raw}, {self.const(0, wide)} : {ty(wide)}")

    def flag(self, cond, w):
        """The ``i1`` ``cond`` as a ``w``-byte 0 or 1."""
        return self.op(f"arith.extui {cond} : i1 to {ty(w)}")

    def value(self, op):  # pylint: disable=too-many-return-statements
        """The value of a pure op, ``out`` bytes wide, each operand at its own width."""
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
            sh = [(vals[0][0], sa), (self.const(8 * ins[1][1], 1), 1)]
            return self.fit(self.shift("INT_RIGHT", sh, sa), sa, w)
        raise ValueError(name)

    def trap(self, key):
        """Call ``trap`` with the canonical index of ``key`` and return poison: ``trap``
        does not return, so the registers it leaves are dead."""
        k = self.ctx.traps.setdefault(key, len(self.ctx.traps))
        self.put(f"func.call @trap({self.const(k, 2)}) : (i16) -> ()")
        vals = [self.op(f"ub.poison : {ty(s)}") for _, s in self.ctx.regfile]
        self.put(f"func.return {', '.join(vals)} : {self.ctx.types}")

    def block(self, i):  # pylint: disable=too-many-branches
        """Emit block ``i``; a mid-block ``CBRANCH`` continues in a new block."""
        self.env, part, self.block_id = {}, 0, i
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
                return self.ret()
            elif name == "BRANCHIND":
                return self.switch(op, i)
            elif name == "CALL":
                self.call(op[3])
            elif name == "CALLOTHER" and op[3] == "codechk":
                a, v = (self.read(x) for x in op[2] if x)
                self.write(op[1], self.op(f"func.call @codechk({a}, {v}) : (i16, i8) -> i8"))
            elif name == "CALLOTHER":
                pc = op[2][0]
                if pc[0] == "c":
                    return self.trap(("deopt", pc[1]))
                # A deopt of an instance function: the copy's start, one trap for all copies.
                return self.trap(("deopt", self.fn["blocks"][i]["pc"], self.fn["pc"]))
            elif name == "STORE":
                self.store(op)
            elif op[1] is None:
                self.dummy(op)
            elif name == "LOAD":
                self.write(op[1], self.load(op))
            else:
                self.write(op[1], self.value(op))
        if i + 1 < len(self.fn["blocks"]):
            return self.put(f"cf.br ^b{i + 1}")
        return self.ret()

    def call(self, callee):
        """A ``CALL``: the register file through the callee."""
        args, t = self.regs(), self.ctx.types
        res = self.new()
        name = self.ctx.names[callee]
        attr = " {no_inline}" if self.ctx.sites[callee] > 1 else ""
        self.put(f"{res}:{len(args)} = func.call @{name}({', '.join(args)}){attr} : ({t}) -> ({t})")
        for j, (r, s) in enumerate(self.ctx.regfile):
            self.write(["r", r, s], f"{res}#{j}")

    def switch(self, op, i):
        """A ``BRANCHIND`` dispatching on its table; a trap otherwise, unless the table is
        closed (holds every target), when its last case is the default."""
        target = self.read(op[2][0])
        table = self.fn["tables"][op[3]]
        if op[3] in self.fn.get("closed", ()) and table:
            cases = "".join(f", {pc}: ^b{blk}" for pc, blk in table[:-1])
            return self.put(f"cf.switch {target} : i16, [default: ^b{table[-1][1]}{cases}]")
        cases = "".join(f", {pc}: ^b{blk}" for pc, blk in table)
        self.put(f"cf.switch {target} : i16, [default: ^b{i}_d{cases}]")
        self.lines.append(f"  ^b{i}_d:")
        return self.trap(("ind", self.fn["blocks"][i]["pc"], self.fn["pc"], i))

    def emit(self):
        """The MLIR text of the function."""
        body = []
        for i in range(len(self.fn["blocks"])):
            self.block(i)
            body += self.lines
            self.lines = []
        params = ", ".join(f"%p{j}: {ty(s)}" for j, (_, s) in enumerate(self.ctx.regfile))
        head = [f"  func.func private @{self.name}({params}) -> ({self.ctx.types}) {{"]
        head.append("    %M = memref.get_global @M : memref<65536xi8>")
        head += [
            f"    {self.cell(k)} = memref.alloca() : memref<{ty(self.cells[k])}>"
            for k in self.order
        ]
        head.append("    %z = arith.constant 0 : i8")
        init = {r: f"%p{j}" for j, (r, _) in enumerate(self.ctx.regfile)}
        for k in self.order:
            if k[0] == "r":
                t = ty(self.cells[k])
                head.append(
                    f"    memref.store {init.get(k[1], '%z')}, {self.cell(k)}[] : memref<{t}>"
                )
        return head + ["    cf.br ^b0"] + body + ["  }"]


class Context:  # pylint: disable=too-few-public-methods
    """What the functions of one program share: the register file, names and traps."""

    def __init__(self, funcs, keep_dummy):
        sizes = {r: {2 if r == 16 else 1} for r in (0, *ENTRY_REGS)}
        for fn in funcs:
            for b in fn["blocks"]:
                for op in b["ops"]:
                    for v in filter(None, [op[1], *op[2]]):
                        if v[0] == "r" and v[1] < NSLOT:
                            sizes.setdefault(v[1], set()).add(v[2])
        assert all(len(s) == 1 for s in sizes.values()), sizes
        self.regfile = [(r, next(iter(s))) for r, s in sorted(sizes.items())]
        self.types = ", ".join(ty(s) for _, s in self.regfile)
        rank = sorted(range(len(funcs)), key=lambda i: funcs[i]["pc"])
        self.names = [""] * len(funcs)
        for j, i in enumerate(rank):
            self.names[i] = f"f{j}"
        self.traps, self.keep_dummy = {}, keep_dummy
        self.sites = Counter(
            op[3] for fn in funcs for b in fn["blocks"] for op in b["ops"] if op[0] == "CALL"
        )


def emit(funcs, entries, keep_dummy=False):
    """The MLIR module of lifted ``funcs`` (sorted by entry PC) and ``{trap key: index}``.

    ``entries`` are ``(pc, name)``; ``keep_dummy`` keeps throw-away reads as ``io_read`` s."""
    ctx = Context(funcs, keep_dummy)
    out = ["module {", '  memref.global "private" @M : memref<65536xi8>']
    out += [f"  func.func private @{n}{t}" for n, t in EXTERNS.items()]
    for fn, name in zip(funcs, ctx.names):
        out += Body(fn, name, ctx).emit()
    by_pc = {fn["pc"]: i for i, fn in enumerate(funcs) if fn["entry"]}
    t = ctx.types
    for pc, label in entries:
        out.append(f"  func.func @{label}(%a: i8) {{")
        args = []
        for r, s in ctx.regfile:
            args.append("%a" if r == 0 else f"%e{r}")
            if r:
                out.append(f"    %e{r} = arith.constant {ENTRY_REGS.get(r, 0)} : {ty(s)}")
        callee = f"@{ctx.names[by_pc[pc]]}({', '.join(args)})"
        out.append(f"    %r:{len(args)} = func.call {callee} : ({t}) -> ({t})")
        out += ["    func.return", "  }"]
    return "\n".join(out + ["}"]) + "\n", ctx.traps
