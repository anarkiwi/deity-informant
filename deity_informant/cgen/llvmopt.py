"""LLVM InstCombine over the optimized MLIR of a lift, and back.

:func:`lower` makes memory accesses opaque calls and numbers every location, LLVM's tools
lower, combine and import the module, and :func:`recover` rebuilds the dialects
:func:`mlir.parse` reads with the original locations and attributes.
"""

from __future__ import annotations

import re
import shlex
from collections import deque
from dataclasses import dataclass, field, replace

from . import mlir

#: Generic printing with locations, as :func:`mlir.parse` reads.
PRINT = ("--mlir-print-op-generic", "--mlir-print-debuginfo", "--mlir-print-local-scope")
#: To LLVM IR, ``instcombine`` (its fixpoint unverified, as in the standard pipelines), back.
STAGES = (
    (
        "mlir-opt",
        "--convert-arith-to-llvm",
        "--convert-cf-to-llvm",
        "--convert-func-to-llvm",
        "--convert-ub-to-llvm",
        "--reconcile-unrealized-casts",
        "--ensure-debug-info-scope-on-llvm-func",
        "--mlir-print-debuginfo",
    ),
    ("mlir-translate", "--mlir-to-llvmir"),
    ("opt", "-passes=instcombine<no-verify-fixpoint>", "-S"),
    ("mlir-translate", "--import-llvm", *PRINT),
)
TIDY = ("mlir-opt", "--canonicalize", "--cse", *PRINT)
#: The lowering of ``index``: the lift has no 64-bit integers of its own.
INDEX = "i64"
BINARY = {
    "add": "addi",
    "sub": "subi",
    "mul": "muli",
    "and": "andi",
    "or": "ori",
    "xor": "xori",
    "shl": "shli",
    "lshr": "shrui",
    "ashr": "shrsi",
    "udiv": "divui",
    "sdiv": "divsi",
    "urem": "remui",
    "srem": "remsi",
}
#: The operations an ``llvm`` operation with a flag computes alike: recovered as the one its
#: location's operation was.
ALIKE = {("or", "isDisjoint"): ("arith.addi", "arith.xori")}
TERMINATORS = {"cf.br", "cf.cond_br", "cf.switch", "func.return"}
COMMUTATIVE = {"add", "mul", "and", "or", "xor"}
CASTS = {"zext": "arith.extui", "sext": "arith.extsi", "trunc": "arith.trunci"}
MINMAX = {"smax": "maxsi", "smin": "minsi", "umax": "maxui", "umin": "minui"}
#: Operations recovered as the value they pass on, or as nothing.
TRANSPARENT = {"llvm.freeze", "llvm.intr.expect"}
DROPPED = {"llvm.insertvalue", "llvm.extractvalue", "llvm.intr.assume", *TRANSPARENT}
#: ``arith.cmpi`` predicates, which ``llvm.icmp`` numbers alike.
EQ, NE, SLT, SLE, SGT, SGE, ULT, ULE, UGT, UGE = range(10)

_LOC = re.compile(r'"o":(\d+):\d+')
_FUSED = re.compile(r"fused<#llvm\.di_subprogram<.*?>>\[(.*?)\]")
_MEM = re.compile(r'"memref\.(load|store)"\(([^)]*)\) : \(([^)]*)\) -> (\S+)')
_GET = re.compile(r'^(%[\w$.-]+) = "memref\.get_global"\(\) <\{name = @(\w+)\}>')
_GLOBAL = re.compile(r'"memref\.global"\(\) <\{sym_name = "(\w+)".*type = (memref<\d+x(\w+)>)')
_SYM = re.compile(r'sym_name = "([\w.$-]+)"')
_FTYPE = re.compile(r"function_type = \((.*?)\) -> (\(.*?\)|[^,}]+)")


@dataclass
class Table:  # pylint: disable=too-many-instance-attributes
    """What :func:`lower` leaves out: by location number ``N`` (from 1) the location's text,
    operation name, properties and discardable attributes; each function's first and last
    lines, the globals, each function's ``get_global`` locations, each ``cf.cond_br``'s
    successors by the numbers of their terminators, each operation's operands by the numbers
    of their definitions and the module's last line."""

    locs: list = field(default_factory=list)
    names: list = field(default_factory=list)
    props: list = field(default_factory=list)
    attrs: list = field(default_factory=list)
    funcs: dict = field(default_factory=dict)
    globals: dict = field(default_factory=dict)
    gets: dict = field(default_factory=dict)
    branches: dict = field(default_factory=dict)
    operands: dict = field(default_factory=dict)
    close: str = "}) : () -> ()"

    def number(self, where, name="", props="", attrs=""):
        """The number of a new location ``where``, of operation ``name``."""
        self.locs.append(where or "unknown")
        self.names.append(name)
        self.props.append(props)
        self.attrs.append(attrs)
        return len(self.locs)


def _mem_call(kind, args, types, res, gets):
    """The ``func.call`` text replacing a ``memref.load`` or ``memref.store``."""
    args, types = [a.strip() for a in args.split(",")], mlir._types(types)
    if kind == "load":
        return f'"func.call"({args[1]}) <{{callee = @__ld.{gets[args[0]]}}}> : (index) -> {res}'
    g = gets[args[1]]
    return f'"func.call"({args[0]}, {args[2]}) <{{callee = @__st.{g}}}> : ({types[0]}, index) -> ()'


def lower(text):
    """``(text, table)``: generic-form ``text`` with each ``memref`` access a call of
    ``@__ld.G`` or ``@__st.G``, each location ``loc("o":N:1)`` and the discardable attributes
    kept in the :class:`Table`."""
    table, out, stack, gets = Table(), [], [], {}
    label, terms, conds, made = "", {}, [], {}
    for raw in text.splitlines():
        s, _, _, where = mlir._strip_locs(raw.strip())
        if s.startswith("^"):
            label = mlir._BLOCK.match(s).group(1)
        if not s or s.startswith("//") or s.startswith("^"):
            out.append(s)
            continue
        if s.startswith("})"):
            head = stack.pop()
            if head is None:
                table.close = raw.strip()
            else:
                table.funcs[head][1] = raw.strip()
                table.branches.update((n, tuple(terms.get(x) for x in xs)) for n, xs in conds)
                label, terms, conds, made = "", {}, [], {}
            out.append(f'{s} loc("o":{table.number(where)}:1)')
            continue
        if m := _GLOBAL.search(s):
            table.globals[m.group(1)] = (raw.strip(), m.group(2), m.group(3))
            continue
        if m := _GET.match(s):
            gets[m.group(1)] = m.group(2)
            table.gets.setdefault(stack[-1], {}).setdefault(m.group(2), where or "unknown")
            continue
        m = mlir._HEAD.match(s)
        assert m, s
        name, props, rest = m.group(3), (m.group(6) or "<{}>")[2:-2], m.group(7)
        attrs = ""
        if a := mlir._ATTRS.match(rest):
            attrs, s = a.group(0).strip(), s[: m.start(7)] + rest[a.end() :].lstrip()
        if mm := _MEM.search(s):
            s = s[: mm.start()] + _mem_call(*mm.groups(), gets)
        if s.endswith("({"):
            sym = _SYM.search(s) if name == "func.func" else None
            stack.append(sym and sym.group(1))
            if sym:
                table.funcs[sym.group(1)] = [raw.strip(), None]
            out.append(s)
            continue
        n = table.number(where, name, props, attrs)
        made.update(dict.fromkeys(mlir._results(m.group(1), m.group(2)), n))
        table.operands[n] = tuple(made.get(a.strip(), 0) for a in m.group(4).split(","))
        if name in TERMINATORS:
            terms[label] = n
        if name == "cf.cond_br":
            conds.append((n, [x.strip()[1:] for x in m.group(5).split(",")]))
        out.append(f'{s} loc("o":{n}:1)')
    decls = [
        f'"func.func"() <{{function_type = {t}, sym_name = "__{k}.{g}", '
        'sym_visibility = "private"}> ({\n}) : () -> ()'
        for g, (_, _, e) in table.globals.items()
        for k, t in (("ld", f"(index) -> {e}"), ("st", f"({e}, index) -> ()"))
    ]
    return "\n".join(out[:1] + decls + out[1:]) + "\n", table


def _ty(t):
    """The ``arith`` type of LLVM type ``t``."""
    return "index" if t == INDEX else t


def _width(t):
    return 64 if t == "index" else int(t[1:])


def _struct(t):
    """The field types of ``!llvm.struct<(...)>`` ``t``, or None."""
    if not t.startswith("!llvm.struct<"):
        return None
    return [_ty(x) for x in mlir._types(t[len("!llvm.struct<") : -1])]


def _results(head):
    """The result types of the ``func.func`` whose first line is ``head``."""
    m = _FTYPE.search(head)
    return mlir._types(m.group(2)) if m else []


def _number(o):
    """The location number of llvm operation ``o``, 0 where unknown or merged."""
    m = _LOC.search(o.where)
    return int(m.group(1)) if m else 0


def _line(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    res, name, args, ins, outs, props="", attrs="", succs="", where="unknown"
):
    """A generic-form operation."""
    head = f"{res} = " if res else ""
    p = f" <{{{props}}}>" if props else ""
    a = f" {attrs}" if attrs else ""
    t = (outs[0] if len(outs) == 1 else f"({', '.join(outs)})") if outs else "()"
    return (
        f'{head}"{name}"({", ".join(args)}){succs}{p}{a} : ({", ".join(ins)}) -> {t} loc({where})'
    )


class _Func:  # pylint: disable=too-many-instance-attributes
    """The recovery of one ``llvm.func``."""

    def __init__(self, fn, table, name):
        self.fn, self.table, self.name = fn, table, name
        self.defs, self.users, self.types = {}, {}, {}
        for b in fn.regions[0]:
            for n, t in b.args:
                self.types[n] = _ty(t)
            for o in b.ops:
                for r, t in zip(o.res, o.types[1]):
                    self.defs[r], self.types[r] = o, _ty(t)
                for a in o.args:
                    self.users.setdefault(a, []).append(o)
        self.fields_of, self.prelude, self.lines, self.fresh = {}, {}, [], 0
        self.terms = {b.name: _number(b.ops[-1]) for b in fn.regions[0] if b.ops}

    def new(self):
        """A fresh value name."""
        self.fresh += 1
        return f"%r{self.fresh}"

    def val(self, v):
        """The scalar value ``v`` names: an extracted field as the field, a frozen value as
        itself."""
        d = self.defs.get(v)
        if d is not None and d.name == "llvm.extractvalue":
            return self.fields(d.args[0])[d.ints("position")[0]]
        if d is not None and d.name in TRANSPARENT:
            return self.val(d.args[0])
        return v

    def fields(self, v):
        """The scalar values of the fields of struct value ``v``."""
        if v in self.fields_of:
            return self.fields_of[v]
        d, kinds = self.defs.get(v), _struct(self.types[v])
        if d is not None and d.name == "llvm.insertvalue":
            out = list(self.fields(d.args[0]))
            out[d.ints("position")[0]] = self.val(d.args[1])
        elif d is not None and d.name in ("llvm.mlir.poison", "llvm.mlir.undef"):
            out = [self.prelude_op("ub.poison", "value = #ub.poison", t) for t in kinds]
        elif d is not None and d.name == "llvm.mlir.zero":
            out = [self.prelude_op("arith.constant", f"value = 0 : {t}", t) for t in kinds]
        elif d is not None and d.name == "llvm.call" and len(kinds) > 1:
            out = [f"{v}#{i}" for i in range(len(kinds))]
        else:
            out = [f"%x{v[1:]}_{i}" for i in range(len(kinds))]
        for x, t in zip(out, kinds):
            self.types.setdefault(x, t)
        self.fields_of[v] = out
        return out

    def flat(self, v):
        """``v`` as the scalar values it is: its fields when a struct."""
        return self.fields(v) if _struct(self.types.get(v, "")) else [self.val(v)]

    def prelude_op(self, name, props, t, where="unknown"):
        """The entry block's operation ``name`` of type ``t``, added once."""
        key = (name, props, t)
        if key not in self.prelude:
            res = f"%e{len(self.prelude)}"
            self.prelude[key] = (res, _line(res, name, [], [], [t], props, where=where))
            self.types[res] = t
        return self.prelude[key][0]

    def swapped(self, o):
        """Whether ``llvm.cond_br`` ``o`` has its original successors swapped (as
        ``instcombine`` does for a negated or non-canonical condition)."""
        orig = self.table.branches.get(_number(o))
        return (
            bool(orig)
            and None not in orig
            and orig[::-1] == tuple(self.terms.get(s) for s in o.succs)
        )

    def reordered(self, o):
        """Whether the operands of commutative ``o`` are its location's operation's, swapped."""
        orig = self.table.operands.get(_number(o))
        mine = tuple(_number(self.defs[a]) if a in self.defs else 0 for a in o.args)
        return 0 not in mine and mine[0] != mine[1] and orig == mine[::-1]

    def origin(self, o):
        """The location number ``o`` takes: its own, else that of the nearest operation it
        was combined with, users first; 0 (unknown) for a constant."""
        seen, todo = {id(o)}, deque([o])
        while todo:
            x = todo.popleft()
            if (n := _number(x)) and self.table.locs[n - 1] != "unknown":
                return n
            if not x.args and x.name != "llvm.call":
                continue
            for y in [u for r in x.res for u in self.users.get(r, [])] + [
                self.defs[a] for a in x.args if a in self.defs
            ]:
                if id(y) not in seen:
                    seen.add(id(y))
                    todo.append(y)
        return 0

    def emit(  # pylint: disable=too-many-arguments,too-many-positional-arguments
        self, res, name, args, outs, props="", succs="", src=None
    ):
        """Add ``res = name(args)`` typed ``outs`` at the location of llvm operation ``src``,
        with its original attributes when ``name`` is its original operation's."""
        n = self.origin(src) if src is not None else 0
        where, attrs = "unknown", ""
        if n:
            where = self.table.locs[n - 1]
            if self.table.names[n - 1] == name:
                attrs = self.table.attrs[n - 1]
                orig = self.table.props[n - 1]
                if name == "func.call" and orig.startswith(f"{props},"):
                    props = orig
        ins = [self.types[a] for a in args]
        self.lines.append(_line(res, name, args, ins, outs, props, attrs, succs, where))
        if res and len(outs) > 1:
            for i, t in enumerate(outs):
                self.types[f"{res.split(':')[0]}#{i}"] = t
        elif res:
            self.types[res] = outs[0]
        return res

    def op(self, name, args, t, props="", src=None):
        """A new single-result value."""
        return self.emit(self.new(), name, args, [t], props, src=src)

    def const(self, v, t, src):
        """A new constant ``v`` of type ``t``."""
        return self.op("arith.constant", [], t, f"value = {v} : {t}", src)

    def cmp(self, pred, a, b, src):
        """A new ``arith.cmpi``."""
        return self.op("arith.cmpi", [a, b], "i1", f"predicate = {pred} : i64", src)

    def select(self, c, a, b, src, res=None):
        """A new ``arith.select`` (named ``res``)."""
        return self.emit(res or self.new(), "arith.select", [c, a, b], [self.types[a]], src=src)

    def recover(self):
        """The ``func.func`` lines of the function."""
        head, close = self.table.funcs[self.name]
        body = []
        for b in self.fn.regions[0]:
            self.lines, args = [], []
            for n, t in b.args:
                kinds = _struct(t) or [self.types[n]]
                args += [f"{x}: {y}" for x, y in zip(self.flat(n), kinds)]
            for o in b.ops:
                self.convert(o)
            label = f"^{b.name or 'bb0'}"
            body.append((f"{label}({', '.join(args)}):" if args else f"{label}:", self.lines))
        out = [head]
        for k, (label, lines) in enumerate(body):
            out.append(label)
            out += [line for _, line in self.prelude.values()] if k == 0 else []
            out += lines
        out.append(close)
        return out

    def convert(self, o):  # pylint: disable=too-many-return-statements,too-many-branches
        """Emit the recovery of llvm operation ``o``."""
        kind, res = o.name.removeprefix("llvm."), o.res[0] if o.res else None
        args = [self.val(a) for a in o.args]
        struct = bool(o.res) and _struct(o.types[1][0])
        if o.name in DROPPED or struct and kind in ("mlir.poison", "mlir.undef", "mlir.zero"):
            return None
        t = self.types.get(res)
        if kind in ("mlir.poison", "mlir.undef"):
            return self.emit(res, "ub.poison", [], [t], "value = #ub.poison", src=o)
        if kind in ("mlir.constant", "mlir.zero"):
            v = o.prop("value").rsplit(":", 1)[0].strip() if kind == "mlir.constant" else "0"
            v = {"true": "1", "false": "0"}.get(v, v)
            return self.emit(res, "arith.constant", [], [t], f"value = {v} : {t}", src=o)
        if kind in BINARY:
            if kind in COMMUTATIVE and self.reordered(o):
                args = args[::-1]
            name, n = f"arith.{BINARY[kind]}", self.origin(o)
            alike = [
                x for (k, flag), xs in ALIKE.items() if k == kind and flag in o.props for x in xs
            ]
            name = self.table.names[n - 1] if n and self.table.names[n - 1] in alike else name
            return self.emit(res, name, args, [t], src=o)
        if kind == "icmp":
            pred = f"predicate = {o.int_prop('predicate')} : i64"
            return self.emit(res, "arith.cmpi", args, ["i1"], pred, src=o)
        if kind in CASTS:
            name = CASTS[kind]
            if "index" in (t, self.types[args[0]]):
                name = "arith.index_cast" if kind == "sext" else "arith.index_castui"
            return self.emit(res, name, args, [t], src=o)
        if kind == "select" and struct:
            for x, a, b in zip(self.fields(res), self.flat(o.args[1]), self.flat(o.args[2])):
                self.select(args[0], a, b, o, x)
            return None
        if kind == "select":
            return self.select(*args, o, res)
        if kind == "call":
            return self.call(o, args)
        if kind in ("br", "cond_br", "switch"):
            return self.branch(o)
        if kind in ("return", "unreachable"):
            outs = self.flat(o.args[0]) if o.args else []
            if kind == "unreachable":
                outs = [
                    self.prelude_op("ub.poison", "value = #ub.poison", x)
                    for x in _results(self.table.funcs[self.name][0])
                ]
            return self.emit(None, "func.return", outs, [], src=o)
        if kind.startswith("intr."):
            return self.intrinsic(kind[5:], o, args)
        raise ValueError(f"unrecoverable operation {o.name}")

    def call(self, o, args):
        """A call: of ``@__ld.G``/``@__st.G`` the memory access, else a ``func.call`` with
        the callee's results."""
        callee = o.prop("callee")[1:]
        mem, _, g = callee.partition(".")
        if mem in ("__ld", "__st") and g in self.table.globals:
            _, t, _ = self.table.globals[g]
            m = self.prelude_op(
                "memref.get_global",
                f"name = @{g}",
                t,
                self.table.gets.get(self.name, {}).get(g, "unknown"),
            )
            if mem == "__ld":
                return self.emit(
                    o.res[0], "memref.load", [m, args[0]], [self.types[o.res[0]]], src=o
                )
            return self.emit(None, "memref.store", [args[0], m, args[1]], [], src=o)
        outs = _results(self.table.funcs[callee][0])
        res = (o.res[0] if o.res else self.new()) if outs else None
        res = f"{res}:{len(outs)}" if len(outs) > 1 else res
        return self.emit(res, "func.call", args, outs, f"callee = @{callee}", src=o)

    def branch(self, o):
        """A ``cf`` terminator, struct operands passed as their fields."""
        edges = replace(o, name="cf." + o.name[5:]).edges()
        args, segs = [self.val(o.args[0])] if o.name != "llvm.br" else [], []
        if o.name == "llvm.cond_br" and self.swapped(o):
            edges = edges[::-1]
            args = [self.op("arith.xori", [args[0], self.const(1, "i1", o)], "i1", src=o)]
        for _, ix, _ in edges:
            flat = [x for i in ix for x in self.flat(o.args[i])]
            segs.append(len(flat))
            args += flat
        succs = "[" + ", ".join(f"^{s}" for s, _, _ in edges) + "]"
        props = ""
        if o.name == "llvm.cond_br":
            props = f"operandSegmentSizes = array<i32: 1, {segs[0]}, {segs[1]}>"
        elif o.name == "llvm.switch":
            t = self.types[args[0]]
            cases = [["false", "true"][v] if t == "i1" else str(v) for _, _, v in edges[1:]]
            seg = ": " * bool(cases) + ", ".join(map(str, segs[1:]))
            props = f"case_operand_segments = array<i32{seg}>, "
            if cases:
                props += f"case_values = dense<[{', '.join(cases)}]> : vector<{len(cases)}x{t}>, "
            props += f"operandSegmentSizes = array<i32: 1, {segs[0]}, {sum(segs[1:])}>"
        return self.emit(None, "cf." + o.name[5:], args, [], props, succs, src=o)

    def intrinsic(
        self, name, o, args
    ):  # pylint: disable=too-many-locals,too-many-return-statements
        """An LLVM intrinsic as ``arith`` operations."""
        res, a, t = o.res[0], args[0], self.types[args[0]]
        b, w = (args + [None])[1], _width(t)
        if name in MINMAX:
            return self.emit(res, f"arith.{MINMAX[name]}", args, [t], src=o)
        if name in ("ucmp", "scmp"):
            r = self.types[res]
            gt = self.cmp(UGT if name == "ucmp" else SGT, a, b, o)
            lt = self.cmp(ULT if name == "ucmp" else SLT, a, b, o)
            ext = [self.op("arith.extui", [x], r, src=o) for x in (gt, lt)]
            return self.emit(res, "arith.subi", ext, [r], src=o)
        if name.endswith(".with.overflow") and name[1:4] in ("add", "sub"):
            r = self.op(f"arith.{BINARY[name[1:4]]}", [a, b], t, src=o)
            self.fields_of[res] = [r, self.overflow(name[0], name[1:4], a, b, r, o)]
            return None
        if name in ("uadd.sat", "usub.sat"):
            r = self.op(f"arith.{BINARY[name[1:4]]}", [a, b], t, src=o)
            ov = self.overflow("u", name[1:4], a, b, r, o)
            return self.select(ov, self.const(-1 if name[1:4] == "add" else 0, t, o), r, o, res)
        if name in ("sadd.sat", "ssub.sat"):
            r = self.op(f"arith.{BINARY[name[1:4]]}", [a, b], t, src=o)
            neg = self.cmp(SLT, a, self.const(0, t, o), o)
            top = 1 << (w - 1)
            sat = self.select(neg, self.const(-top, t, o), self.const(top - 1, t, o), o)
            return self.select(self.overflow("s", name[1:4], a, b, r, o), sat, r, o, res)
        if name == "abs":
            neg = self.op("arith.subi", [self.const(0, t, o), a], t, src=o)
            return self.select(self.cmp(SLT, a, self.const(0, t, o), o), neg, a, o, res)
        if name in ("fshl", "fshr"):
            s = self.op("arith.remui", [args[2], self.const(w, t, o)], t, src=o)
            r = self.op("arith.subi", [self.const(w, t, o), s], t, src=o)
            hi, lo = (s, r) if name == "fshl" else (r, s)
            x = self.op("arith.shli", [a, hi], t, src=o)
            x = self.op("arith.ori", [x, self.op("arith.shrui", [b, lo], t, src=o)], t, src=o)
            zero = self.cmp(EQ, s, self.const(0, t, o), o)
            return self.select(zero, a if name == "fshl" else b, x, o, res)
        if name in ("ctpop", "bitreverse", "bswap"):
            return self.gather(name, a, t, w, o, res)
        if name in ("ctlz", "cttz"):
            acc = self.const(w, t, o)
            for i in range(w) if name == "ctlz" else range(w - 1, -1, -1):
                bit = self.op("arith.andi", [a, self.const(1 << i, t, o)], t, src=o)
                hit = self.cmp(NE, bit, self.const(0, t, o), o)
                acc = self.select(hit, self.const(w - 1 - i if name == "ctlz" else i, t, o), acc, o)
            return self.emit(res, "arith.addi", [acc, self.const(0, t, o)], [t], src=o)
        raise ValueError(f"unrecoverable intrinsic {name}")

    def gather(
        self, name, a, t, w, o, res
    ):  # pylint: disable=too-many-arguments,too-many-positional-arguments
        """``ctpop`` (the sum of the bits), ``bitreverse`` or ``bswap`` (the bits or bytes
        moved to their mirror positions) of ``a``."""
        step = 8 if name == "bswap" else 1
        mask, acc = self.const((1 << step) - 1, t, o), None
        for i in range(0, w, step):
            part = self.op("arith.shrui", [a, self.const(i, t, o)], t, src=o)
            part = self.op("arith.andi", [part, mask], t, src=o)
            if name != "ctpop":
                part = self.op("arith.shli", [part, self.const(w - step - i, t, o)], t, src=o)
            acc = (
                part
                if acc is None
                else self.op(
                    "arith.addi" if name == "ctpop" else "arith.ori", [acc, part], t, src=o
                )
            )
        return self.emit(res, "arith.addi", [acc, self.const(0, t, o)], [t], src=o)

    def overflow(
        self, sign, op, a, b, r, o
    ):  # pylint: disable=too-many-arguments,too-many-positional-arguments
        """Whether ``r = a op b`` (``add`` or ``sub``) overflowed, unsigned (``sign`` ``u``)
        or signed."""
        if sign == "u":
            return self.cmp(ULT, r, a, o) if op == "add" else self.cmp(ULT, a, b, o)
        t = self.types[a]
        x = self.op("arith.xori", [r, a] if op == "add" else [a, b], t, src=o)
        y = self.op("arith.xori", [r, b] if op == "add" else [a, r], t, src=o)
        both = self.op("arith.andi", [x, y], t, src=o)
        return self.cmp(SLT, both, self.const(0, t, o), o)


def recover(text, table):
    """The ``func``/``cf``/``arith``/``memref`` generic-form module of the imported ``llvm``
    dialect ``text`` of the module :func:`lower` gave ``table``."""
    top = mlir.parse(_FUSED.sub(r"\1", text))
    out = ['"builtin.module"() ({', *(g[0] for g in table.globals.values())]
    for fn in top.regions[0][0].ops:
        name = fn.prop("sym_name") if fn.name == "llvm.func" else None
        if name not in table.funcs:
            continue
        if any(b.ops for b in fn.regions[0]):
            out += _Func(fn, table, name).recover()
        else:
            out += table.funcs[name]
    out.append(table.close)
    return "\n".join(out) + "\n"


def combined(text, prefix):
    """Generic-form MLIR ``text`` after LLVM ``instcombine``, in the same dialects with the
    same locations and attributes; the tools run under ``prefix`` (:func:`mlir.runner`)."""
    low, table = lower(text)
    script = " | ".join(shlex.join(s) for s in STAGES)
    imported = mlir.run(prefix, ["bash", "-o", "pipefail", "-c", script], low)
    return mlir.run(prefix, list(TIDY), recover(imported, table))
