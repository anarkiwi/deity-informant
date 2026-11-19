"""LLVM's scalar and memory passes over the optimized MLIR of a lift, and back.

:func:`lower` makes each memory access a call of an inlinable accessor loading or storing its
global, scoped by its address set (:func:`scopes`), numbers every location and states the
intervals of loads as LLVM ``range`` attributes; LLVM's tools lower, optimize and import the
module, and :func:`recover` rebuilds the dialects :func:`mlir.parse` reads with the original
locations and attributes.
"""

from __future__ import annotations

import re
import shlex
from collections import deque
from dataclasses import dataclass, field, replace

import numpy as np

from . import mlir

#: Generic printing with locations, as :func:`mlir.parse` reads.
PRINT = ("--mlir-print-op-generic", "--mlir-print-debuginfo", "--mlir-print-local-scope")
#: LLVM's passes: InstCombine (its fixpoint unverified, as in the standard pipelines), then the
#: facts of dominating conditions and value ranges (EarlyCSE, CVP, constraint elimination
#: after sinking each value to its one use) and InstCombine over what they leave, each access
#: an opaque call; then the accesses inlined, loads and stores redundant on dominating paths
#: (EarlyCSE over MemorySSA) and dead stores removed. None of them duplicates, merges or
#: reorders blocks, nor moves an access to another block.
PASSES = (
    "function(instcombine<no-verify-fixpoint>,early-cse,correlated-propagation,sink,"
    "constraint-elimination,instcombine<no-verify-fixpoint>),always-inline,"
    "function(early-cse<memssa>,dse)"
)
#: To LLVM IR, :data:`PASSES`, back.
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
    ("opt", f"-passes={PASSES}", "-S"),
    ("mlir-translate", "--import-llvm", *PRINT),
)
TIDY = ("mlir-opt", "--canonicalize", "--cse", *PRINT)
#: The lowering of ``index``: the lift has no 64-bit integers of its own.
INDEX = "i64"
#: A pointer, recovered as its offset from the module's one memory global, an address.
PTR = "!llvm.ptr"
ADDR, MASK = "i16", 0xFFFF
#: The operations reduction modulo a width commutes with.
NARROW = {"add", "sub", "mul", "and", "or", "xor"}
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
_GLOBAL = re.compile(r'"memref\.global"\(\) <\{sym_name = "(\w+)".*type = (memref<(\d+)x(\w+)>)')
_SYM = re.compile(r'sym_name = "([\w.$-]+)"')
_FTYPE = re.compile(r"function_type = \((.*?)\) -> (\(.*?\)|[^,}]+)")


def _range(t, r):
    """The ``llvm.range`` of interval ``r`` of type ``t`` (None: every value)."""
    top = (1 << int(t[1:])) - 1
    if r is None or r == (0, top):
        return None
    return f"llvm.range = #llvm.constant_range<{t}, {r[0]}, {(r[1] + 1) & top}>"


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


def _effects(other, inaccessible):
    """An ``llvm.memory_effects``: ``other`` for the program's memory, ``inaccessible`` for
    memory it does not address."""
    return (
        f"#llvm.memory_effects<other = {other}, argMem = {other}, inaccessibleMem = "
        f"{inaccessible}, errnoMem = none, targetMem0 = none, targetMem1 = none>"
    )


#: Touching no byte of the program's memory.
ELSEWHERE = f"memory_effects = {_effects('none', 'readwrite')}, no_unwind, will_return"
#: The externals' LLVM attributes: the checks leave the program's memory, traps end.
EXTERNALS = {
    "codechk": ELSEWHERE,
    "objchk": ELSEWHERE,
    "trap": 'no_unwind, passthrough = ["noreturn"]',
}
#: The I/O calls, each also declared ``.dev`` (:data:`ELSEWHERE`) for a call reaching only
#: volatile bytes.
DEVICES = ("io_read", "io_write")
#: A dynamic index in ``llvm.getelementptr``'s ``rawConstantIndices``.
DYNAMIC = -(1 << 31)


def scopes(sets, fn, first=0):
    """``(props, aliases)``: each access's ``alias_scopes``/``noalias_scopes`` for address
    sets ``sets`` (runs ``[(lo, hi)]``, ``hi`` exclusive; None: any) of function ``fn``, one
    scope per class of the coarsest partition making each set a union of classes, and the
    scopes' attribute aliases (``distinct`` ids from ``first``)."""
    known = [i for i, runs in enumerate(sets) if runs]
    out = [""] * len(sets)
    if not known:
        return out, []
    cuts = np.unique([x for i in known for r in sets[i] for x in r])
    member = np.zeros((len(known), len(cuts) - 1), dtype=bool)
    for row, i in enumerate(known):
        for lo, hi in sets[i]:
            member[row, np.searchsorted(cuts, lo) : np.searchsorted(cuts, hi)] = True
    member = member[:, member.any(axis=0)]
    cls = np.unique(member.T, axis=0, return_inverse=True)[1].ravel()
    every = np.arange(cls.max() + 1)
    for row, i in enumerate(known):
        mine = np.unique(cls[member[row]])
        other = np.setdiff1d(every, mine, assume_unique=True)
        if other.size:
            names = [", ".join(f"#s{fn}_{c}" for c in x) for x in (mine, other)]
            out[i] = f"alias_scopes = [{names[0]}], noalias_scopes = [{names[1]}]"
    defs = [f"#d{fn} = #llvm.alias_scope_domain<id = distinct[{first}]<>>"]
    defs += [
        f"#s{fn}_{c} = #llvm.alias_scope<id = distinct[{first + 1 + c}]<>, domain = #d{fn}>"
        for c in every
    ]
    return out, defs


def _accessor(name, kind, t, g, elem, vol, r=None):  # pylint: disable=too-many-arguments
    """The ``always_inline`` ``llvm.func`` ``name``: ``memref`` access ``kind`` (volatile when
    ``vol``) of a ``t`` of global ``g`` of ``elem`` at its ``i64`` argument as an ``llvm`` load
    (its result of interval ``r``) or store."""
    ftype = f"{t} (i64)" if kind == "load" else f"void ({t}, i64)"
    res = f"res_attrs = [{{{_range(t, r)}}}], " if r is not None and _range(t, r) else ""
    props = "volatile_, ordering = 0 : i64" if vol else "ordering = 0 : i64"
    access = (
        f'%v = "llvm.load"(%p) <{{{props}}}> : (!llvm.ptr) -> {t}\n"llvm.return"(%v) : '
        f"({t}) -> ()"
        if kind == "load"
        else f'"llvm.store"(%x, %p) <{{{props}}}> : ({t}, !llvm.ptr) -> ()\n"llvm.return"() : '
        "() -> ()"
    )
    return (
        f'"llvm.func"() <{{CConv = #llvm.cconv<ccc>, always_inline, function_type = '
        f'!llvm.func<{ftype}>, linkage = #llvm.linkage<internal>, {res}sym_name = "{name}", '
        f"visibility_ = 0 : i64}}> ({{\n"
        f"^bb0({'%i: i64' if kind == 'load' else f'%x: {t}, %i: i64'}):\n"
        f'%m = "llvm.mlir.addressof"() <{{global_name = @{g}}}> : () -> !llvm.ptr\n'
        f'%p = "llvm.getelementptr"(%m, %i) <{{elem_type = {elem}, noWrapFlags = 3 : i32, '
        f"rawConstantIndices = array<i32: {DYNAMIC}>}}> : (!llvm.ptr, i64) -> !llvm.ptr\n"
        f"{access}\n}}) : () -> ()"
    )


#: The properties of an accessor's ``llvm.call`` but its callee and scopes.
CALL = (
    "CConv = #llvm.cconv<ccc>, TailCallKind = #llvm.tailcallkind<none>, fastmathFlags = "
    "#llvm.fastmath<none>, op_bundle_sizes = array<i32>"
)


def _global(name, size, elem):
    """The ``llvm.mlir.global`` of memory global ``name``: external, its contents unknown."""
    return (
        f'"llvm.mlir.global"() <{{addr_space = 0 : i32, global_type = !llvm.array<{size} x '
        f'{elem}>, linkage = #llvm.linkage<external>, sym_name = "{name}", visibility_ = 0 : '
        "i64}> ({\n}) : () -> ()"
    )


def lower(  # pylint: disable=too-many-locals,too-many-statements,too-many-branches
    text, ranges=None, sets=None, devices=None
):
    """``(text, table)``: ``text`` with its accesses calls of accessors (:func:`_accessor`),
    locations ``loc("o":N:1)`` (the :class:`Table` keeps the rest); per function ``ranges``
    its loads' intervals, ``sets`` its accesses' ``(runs, volatile)`` (:func:`scopes`; without
    one volatile), ``devices`` whether each I/O call reaches only volatile bytes."""
    table, out, stack, gets = Table(), [], [], {}
    label, terms, conds, made = "", {}, [], {}
    ranges, decls, defs, mine, nload = ranges or {}, {}, [], (), 0
    sets, aliases, props, access, nmem = sets or {}, [], [], (), 0
    devices, dev = devices or {}, iter(())
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
                assert nmem == len(access) or not access, (head, nmem, len(access))
                table.branches.update((n, tuple(terms.get(x) for x in xs)) for n, xs in conds)
                label, terms, conds, made = "", {}, [], {}
                mine = ()
                if head in EXTERNALS and out[-1].endswith("({"):
                    s = f"}}) {{{EXTERNALS[head]}}}{s[2:]}"
                if head in DEVICES and out[-1].endswith("({"):
                    defs += [
                        out[-1].replace(f'"{head}"', f'"{head}.dev"'),
                        f"}}) {{{ELSEWHERE}}}{s[2:]}",
                    ]
            out.append(f'{s} loc("o":{table.number(where)}:1)')
            continue
        if m := _GLOBAL.search(s):
            table.globals[m.group(1)] = (raw.strip(), m.group(2), m.group(4))
            out.append(_global(m.group(1), m.group(3), m.group(4)))
            continue
        if m := _GET.match(s):
            gets[m.group(1)] = m.group(2)
            table.gets.setdefault(stack[-1], {}).setdefault(m.group(2), where or "unknown")
            continue
        m = mlir._HEAD.match(s)
        assert m, s
        name, prop, rest = m.group(3), (m.group(6) or "<{}>")[2:-2], m.group(7)
        attrs = ""
        if a := mlir._ATTRS.match(rest):
            attrs, s = a.group(0).strip(), s[: m.start(7)] + rest[a.end() :].lstrip()
        callee = prop.split(",")[0][len("callee = @") :] if name == "func.call" else ""
        if callee in DEVICES and next(dev, False):
            s = s.replace(f"callee = @{callee}", f"callee = @{callee}.dev", 1)
        lines = [s]
        if mm := _MEM.search(s):
            vol = access[nmem][1] if nmem < len(access) else True
            args = [x.strip() for x in mm.group(2).split(",")]
            ins = mlir._types(mm.group(3))
            g, t = gets[args[-2]], mm.group(4) if mm.group(1) == "load" else ins[0]
            r = None
            if mm.group(1) == "load":
                r = mine[nload] if nload < len(mine) else None
                nload += 1
            key = (mm.group(1), t, gets[args[-2]], vol, r if r and _range(t, r) else None)
            if key not in decls:
                decls[key] = f"__a{len(decls)}"
                defs.append(_accessor(decls[key], *key[:2], g, table.globals[g][2], *key[3:]))
            k = f"%__{len(out)}"
            args[-2:] = [k]
            p = ", ".join(x for x in (props[nmem] if nmem < len(props) else "", CALL) if x)
            lines = [
                f'{k} = "arith.index_cast"({mm.group(2).split(",")[-1].strip()}) : (index) -> i64',
                f'{s[: mm.start()]}"llvm.call"({", ".join(args)}) <{{{p}, callee = @{decls[key]}, '
                f"operandSegmentSizes = array<i32: {len(args)}, 0>}}> : "
                f"({', '.join(ins[:-2] + ['i64'])}) -> {mm.group(4)}",
            ]
            nmem += 1
        if s.endswith("({"):
            sym = _SYM.search(s) if name == "func.func" else None
            stack.append(sym and sym.group(1))
            if sym:
                table.funcs[sym.group(1)] = [raw.strip(), None]
                mine, nload = ranges.get(sym.group(1), ()), 0
                access, nmem = sets.get(sym.group(1), ()), 0
                dev = iter(devices.get(sym.group(1), ()))
                props, more = scopes([x[0] for x in access], len(table.funcs), len(aliases))
                aliases += more
            out.append(s)
            continue
        n = table.number(where, name, prop, attrs)
        made.update(dict.fromkeys(mlir._results(m.group(1), m.group(2)), n))
        table.operands[n] = tuple(made.get(a.strip(), 0) for a in m.group(4).split(","))
        if name in TERMINATORS:
            terms[label] = n
        if name == "cf.cond_br":
            conds.append((n, [x.strip()[1:] for x in m.group(5).split(",")]))
        out += [f'{x} loc("o":{n}:1)' for x in lines]
    return "\n".join(aliases + out[:1] + defs + out[1:]) + "\n", table


def _ty(t):
    """The ``arith`` type of LLVM type ``t``."""
    return {INDEX: "index", PTR: ADDR}.get(t, t)


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
        self.same, self.narrowed = {}, {}
        self.terms = {b.name: _number(b.ops[-1]) for b in fn.regions[0] if b.ops}
        self.indices, self.base = self.addresses(), self.bases()

    def new(self):
        """A fresh value name."""
        self.fresh += 1
        return f"%r{self.fresh}"

    def val(self, v):
        """The scalar value ``v`` names: an extracted field as the field, a frozen value as
        itself."""
        if v in self.same:
            return self.same[v]
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
            for v in (n for n, _ in b.args if n in self.indices):
                self.narrow(v)
            for o in b.ops:
                self.convert(o)
                for v in (r for r in o.res if r in self.indices):
                    self.narrow(v)
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
        if kind in ("mlir.addressof", "getelementptr"):
            return self.offset(o, args)
        if kind in ("load", "store"):
            return self.access(o, args)
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

    def bases(self):
        """The global each pointer of the function points into (a fixpoint over
        ``addressof``, ``getelementptr``, ``select`` and the edges' block arguments)."""
        out, blocks = {}, {b.name: b for b in self.fn.regions[0]}
        changed = True
        while changed:
            changed = False
            for o in (o for b in self.fn.regions[0] for o in b.ops):
                if o.name == "llvm.mlir.addressof":
                    new = [(o.res[0], o.prop("global_name")[1:])]
                elif o.name in ("llvm.getelementptr", "llvm.select"):
                    new = [(o.res[0], out.get(o.args[0]) or out.get(o.args[-1]))]
                elif o.succs:
                    edges = replace(o, name="cf." + o.name[5:]).edges()
                    new = [
                        (a, out.get(o.args[i]))
                        for s, ix, _ in edges
                        for (a, _), i in zip(blocks[s].args, ix)
                    ]
                else:
                    new = []
                for k, g in new:
                    if g and k not in out:
                        out[k], changed = g, True
        return out

    def memory(self, g):
        """The function's ``memref.get_global`` of global ``g``."""
        where = self.table.gets.get(self.name, {}).get(g, "unknown")
        return self.prelude_op("memref.get_global", f"name = @{g}", self.table.globals[g][1], where)

    def offset(self, o, args):
        """Pointer ``o`` as its :data:`ADDR` offset from the memory global: an ``addressof``
        0, a ``getelementptr`` its base's plus its indices scaled (bytes or byte arrays)."""
        if o.name == "llvm.mlir.addressof":
            self.same[o.res[0]] = self.prelude_op("arith.constant", f"value = 0 : {ADDR}", ADDR)
            return None
        elem, dyn = o.prop("elem_type"), iter(o.args[1:])
        scales = [int(elem[len("!llvm.array<") :].split()[0]), 1] if "array" in elem else [1]
        zero = self.prelude_op("arith.constant", f"value = 0 : {ADDR}", ADDR)
        acc = None if args[0] == zero else args[0]
        for k, scale in zip(o.ints("rawConstantIndices"), scales):
            if k == DYNAMIC:
                x = self.narrow(next(dyn))
                if scale & MASK != 1:
                    x = self.op("arith.muli", [x, self.const(scale & MASK, ADDR, o)], ADDR, src=o)
            elif k * scale & MASK:
                x = self.const(k * scale & MASK, ADDR, o)
            else:
                continue
            acc = x if acc is None else self.op("arith.addi", [acc, x], ADDR, src=o)
        acc = zero if acc is None else acc
        self.same[o.res[0]] = acc
        return None

    def const_of(self, v):
        """The value of ``llvm.mlir.constant`` ``v``, or -1."""
        d = self.defs.get(v)
        return int(d.prop("value").split(":")[0]) if d and d.name == "llvm.mlir.constant" else -1

    def addresses(self):
        """The values the indices of the function's ``getelementptr`` s are computed from,
        :meth:`narrow` at their definitions."""
        geps = [o for b in self.fn.regions[0] for o in b.ops if o.name == "llvm.getelementptr"]
        todo = [a for o in geps for a in o.args[1:]]
        seen = set()
        while todo:
            v = todo.pop()
            if v in seen:
                continue
            seen.add(v)
            d = self.defs.get(v)
            if d is not None and (d.name in TRANSPARENT or d.name[5:] in NARROW | {"shl"}):
                todo += d.args
        return seen

    def narrow(self, v):
        """Index ``v`` modulo the :data:`ADDR` width: its operations where reduction modulo
        the width commutes with them (an address lies below it), else ``v`` truncated."""
        if v in self.narrowed:
            return self.narrowed[v]
        d, kind = self.defs.get(v), ""
        if d is not None and d.name in TRANSPARENT:
            out = self.narrow(d.args[0])
        elif d is not None:
            kind = d.name.removeprefix("llvm.")
        if kind == "mlir.constant":
            k = int(d.prop("value").split(":")[0]) & MASK
            out = self.const(k - (k >> 15 << 16), ADDR, d)
        elif kind in ("zext", "sext") and _width(self.types[d.args[0]]) <= _width(ADDR):
            x = self.val(d.args[0])
            name = "arith.extui" if kind == "zext" else "arith.extsi"
            out = x if self.types[x] == ADDR else self.op(name, [x], ADDR, src=d)
        elif kind in NARROW:
            xs = [self.narrow(a) for a in d.args]
            out = self.op(f"arith.{BINARY[kind]}", xs, ADDR, src=d)
        elif kind == "shl" and 0 <= (k := self.const_of(d.args[1])) < _width(ADDR):
            out = self.op(
                "arith.shli", [self.narrow(d.args[0]), self.const(k, ADDR, d)], ADDR, src=d
            )
        else:
            x = self.val(v)
            name = "arith.index_cast" if self.types[x] == "index" else "arith.trunci"
            out = self.op(name, [x], ADDR, src=d)
        self.narrowed[v] = out
        return out

    def access(self, o, args):
        """``llvm.load``/``llvm.store`` ``o`` as the ``memref`` access of its offset."""
        p = self.op("arith.index_castui", [args[-1]], "index", src=o)
        m = self.memory(self.base[o.args[-1]])
        if o.name == "llvm.load":
            return self.emit(o.res[0], "memref.load", [m, p], [self.types[o.res[0]]], src=o)
        return self.emit(None, "memref.store", [args[0], m, p], [], src=o)

    def call(self, o, args):
        """A ``func.call`` with the callee's results."""
        callee = o.prop("callee")[1:].removesuffix(".dev")
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


def combined(text, prefix, ranges=None, sets=None, devices=None):
    """Generic-form MLIR ``text`` after LLVM's :data:`PASSES` given the loads' ``ranges``
    (:func:`lower`), in the same dialects with the same locations and attributes; the tools
    run under ``prefix`` (:func:`mlir.runner`)."""
    low, table = lower(text, ranges, sets, devices)
    script = " | ".join(shlex.join(s) for s in STAGES)
    imported = mlir.run(prefix, ["bash", "-o", "pipefail", "-c", script], low)
    return mlir.run(prefix, list(TIDY), recover(imported, table))
