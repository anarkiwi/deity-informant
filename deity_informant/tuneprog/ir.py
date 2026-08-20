"""The tuneprog IR (design section 4): node types, their JSON form, their algebra.

A :class:`Tuneprog` is ``{meta, storage, inputs, procs}``. A :class:`Proc` takes
its live-in 6510 registers as ``params`` and returns the ones it defines as
``rets`` (registers and flags are ordinary values, never machine state inside a
procedure); its body is a dict of :class:`Block` s, each a statement list and a
terminator. Statements are ``Let`` / ``Store`` / ``IoStore`` / ``Call`` /
``Assert`` / ``Phi`` (SSA only); terminators ``Goto`` / ``If`` / ``Switch`` /
``Return`` / ``Trap``.

Memory is one flat 64 KiB image; a region is a *view* of it, so ``Load``/``Store``
carry the region id and the observed extent ``[lo, hi]`` that the access must
stay inside -- outside it the run stops with an ``envelope`` trap. The three
access classes mirror the tracer exactly: ``ram`` (plain memory), ``chk``
(memory when the byte was ever written or is inside the load image, else a
pinned input) and ``io`` ($D000-$DFFF, a SID write / an ``iow`` / RAM under I/O
depending on the 6510 port). ``raw`` is memory without marks: the CPU's own
JSR/RTS frames, which the tracer's write log and footprint do not see either and
which :mod:`.stack` removes unless the program's stack is residual.

:class:`~.interp.Interp` over a :class:`~.interp.Machine` is the semantics:
everything else (SSA, idioms, generated Python) is verified against it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

SID_LO, SID_HI = 0xD400, 0xD7FF  # the SID decode band
SID_REG_LO, SID_REG_HI = 0xD400, 0xD418  # the register file inside it
IO_LO, IO_HI = 0xD000, 0xDFFF
STACK_LO, STACK_HI = 0x0100, 0x01FF
MASK = (0, 0xFF, 0xFFFF)
REG_NAMES = {0: "A", 1: "X", 2: "Y", 3: "SP", 8: "C", 9: "Z", 10: "I", 11: "D", 13: "V", 14: "N"}
REGVAR = {i: REG_NAMES.get(i, "r%d" % i) for i in range(16)}
REGIDX = {v: k for k, v in REGVAR.items()}
COPYVAR, COLVAR = "cv", "cx"  # the copy index and its columns (:mod:`.copymerge`)


def copyval(n):
    """True for a value the copy fold made: the index, or one of its columns.

    Both cross blocks, so liveness must see them; only the index is ever assigned
    twice, so only it takes a phi.
    """
    return n.startswith(COPYVAR) or n.startswith(COLVAR)


class TrapError(Exception):
    """A tuneprog trap: an unverified path, an envelope violation, a bad input."""

    def __init__(self, why, detail=""):
        super().__init__("%s: %s" % (why, detail) if detail else why)
        self.why = why
        self.detail = detail


# ---- expressions -------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class Const:
    v: int
    w: int = 1


@dataclass(frozen=True, slots=True)
class Var:
    n: str
    w: int = 1


@dataclass(frozen=True, slots=True)
class Load:
    """``mem[a]`` as ``w`` little-endian bytes through access class ``cls``."""

    cls: str
    a: object
    w: int = 1
    lo: int = 0
    hi: int = 0xFFFF
    r: int = -1


@dataclass(frozen=True, slots=True)
class Bin:
    """Binary op; ``w`` is the byte width the result is masked to (``carry``: its inputs')."""

    op: str
    a: object
    b: object
    w: int = 1


@dataclass(frozen=True, slots=True)
class R16:
    """S6 only -- a 16-bit read of the ``lo``/``hi`` region pair addressed by ``a``."""

    lo: int
    hi: int
    a: object


# ---- statements --------------------------------------------------------------
@dataclass(slots=True)
class Let:
    n: str
    e: object


@dataclass(slots=True)
class Store:
    cls: str
    a: object
    v: object
    w: int = 1
    lo: int = 0
    hi: int = 0xFFFF
    r: int = -1
    src: int = 0


@dataclass(slots=True)
class Call:
    proc: str
    args: tuple = ()
    rets: tuple = ()


@dataclass(slots=True)
class Assert:
    e: object
    why: str = "assert"


@dataclass(slots=True)
class Phi:
    n: str
    args: dict = field(default_factory=dict)


@dataclass(slots=True)
class W16:
    """S6 only -- a 16-bit assignment of ``e`` to the ``lo``/``hi`` pair at ``a``."""

    lo: int
    hi: int
    a: object
    e: object
    src: int = 0


# ---- terminators -------------------------------------------------------------
@dataclass(slots=True)
class Goto:
    to: str


@dataclass(slots=True)
class If:
    c: object
    t: str
    f: str


@dataclass(slots=True)
class Switch:
    e: object
    cases: tuple = ()
    default: str = ""


@dataclass(slots=True)
class Return:
    """Returns the values of the procedure's ``rets`` registers, in order."""

    vals: tuple = ()


@dataclass(slots=True)
class Trap:
    why: str = "trap"


@dataclass(slots=True)
class Block:
    """One basic block; ``cover`` counts its executions per copy (:mod:`.copymerge`).

    ``closed`` names the pass that put an unexecuted block here (:mod:`.closure`);
    it is orthogonal to ``cover``, which is about copies the trace did reach.
    """

    label: str
    stmts: list = field(default_factory=list)
    term: object = field(default_factory=Return)
    src: int = 0
    count: int = 0
    cover: tuple = ()
    closed: str = ""


@dataclass(slots=True)
class Proc:
    name: str
    params: tuple = ()
    rets: tuple = ()
    blocks: dict = field(default_factory=dict)
    entry: str = ""
    kind: str = "sub"

    def order(self):
        """Blocks in reverse postorder from the entry (layout and dataflow order)."""
        seen, out, stack = set(), [], [self.entry]
        while stack:
            lbl = stack.pop()
            if lbl in seen or lbl not in self.blocks:
                continue
            seen.add(lbl)
            out.append(lbl)
            stack.extend(reversed(succs(self.blocks[lbl].term)))
        return out


@dataclass(slots=True)
class Rgn:
    """One storage region: a view of the flat image (design section 4 ``Region``)."""

    id: int
    name: str
    base: int
    size: int
    kind: str
    stride: int = 1
    init: bytes = b""
    fields: tuple = ()
    origin: int = 0

    @property
    def zero(self):
        """The address index 0 has: the recovered origin, or the base."""
        return self.origin or self.base


@dataclass(slots=True)
class Tuneprog:
    meta: dict = field(default_factory=dict)
    storage: list = field(default_factory=list)
    inputs: list = field(default_factory=list)
    procs: dict = field(default_factory=dict)

    def by_id(self):
        """``{region id: region}`` over the program's storage."""
        return {r.id: r for r in self.storage}

    def image(self):
        """The pre-init 64 KiB image rebuilt from the regions' initial contents."""
        m = bytearray(0x10000)
        for r in self.storage:
            m[r.base : r.base + len(r.init)] = r.init
        return m

    def to_json(self):
        return enc(self)

    @classmethod
    def from_json(cls, doc):
        return dec(doc)

    def save(self, path):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(enc(self), f)
        return path

    @classmethod
    def load(cls, path):
        with open(path, encoding="utf-8") as f:
            return dec(json.load(f))


# ---- JSON: every IR node is a tagged list of its fields, in declaration order --
_NODES = (
    Const,
    Var,
    Load,
    Bin,
    Let,
    Store,
    Call,
    Assert,
    Phi,
    Goto,
    If,
    Switch,
    Return,
    Trap,
    Block,
    Proc,
    Rgn,
    Tuneprog,
)
_TAG = {t: "$" + t.__name__.lower() for t in _NODES}
_TYPE = {v: k for k, v in _TAG.items()}


def enc(x):
    """IR node (or container) -> JSON-safe data."""
    t = _TAG.get(type(x))
    if t is not None:
        return [t] + [enc(getattr(x, f)) for f in x.__slots__]
    if isinstance(x, bytes):
        return ["$hex", x.hex()]
    if isinstance(x, (list, tuple)):
        return [enc(i) for i in x]
    if isinstance(x, dict):
        return {"$dict": [[enc(k), enc(v)] for k, v in x.items()]}
    return x


def dec(j):
    """Inverse of :func:`enc`."""
    if isinstance(j, dict):
        return (
            {dec(k): dec(v) for k, v in j["$dict"]}
            if "$dict" in j
            else {k: dec(v) for k, v in j.items()}
        )
    if not isinstance(j, list):
        return j
    if j and j[0] == "$hex":
        return bytes.fromhex(j[1])
    t = _TYPE.get(j[0]) if j and isinstance(j[0], str) else None
    if t is None:
        return [dec(i) for i in j]
    return t(*[dec(a) for a in j[1:]])


def retexpr(proc, term, want):
    """The value one ``Return`` hands back, when a caller reads exactly one register."""
    regs = [i for i in want if i in proc.rets]
    if len(regs) != 1:
        return None
    v = term.vals[proc.rets.index(regs[0])] if len(term.vals) > proc.rets.index(regs[0]) else None
    return None if v is None or type(v) is Var else v


def retval(proc):
    """The value a play entry hands the host in ``A``, when it is a computed one.

    A register that merely survives to the ``RTS`` (a leftover accumulator, anatomy
    section 7's junk store in return position) is not a return value; one expression
    that every exit agrees on is (Follin's ``A = $7B | $7C | $7D``, anatomy 3.6.0).
    """
    if proc.kind != "tick" or 0 not in proc.rets:
        return None
    i = proc.rets.index(0)
    vals = {
        b.term.vals[i]
        for b in proc.blocks.values()
        if type(b.term) is Return and len(b.term.vals) > i
    }
    if len(vals) != 1:
        return None
    v = vals.pop()
    return None if type(v) is Var else v


def copymap_bands(storage):
    """``[(lo, hi)]`` of the per-copy column tables a copy fold laid down.

    The tables are read-only by construction, so a store that lands in one is a
    path the front end never proved: both executors trap on it.
    """
    return [(r.base, r.base + r.size - 1) for r in storage if r.kind == "copymap"]


def hits_band(bands, a, w):
    """True when a ``w``-byte access at ``a`` touches one of ``bands``."""
    return any(lo <= a + w - 1 and a <= hi for lo, hi in bands)


def succs(term):
    """Successor labels of a terminator, in control order."""
    k = type(term)
    if k is Goto:
        return [term.to]
    if k is If:
        return [term.t, term.f]
    if k is Switch:
        return [l for _v, l in term.cases] + ([term.default] if term.default else [])
    return []


def retarget(term, old, new):
    """``term`` with every successor ``old`` replaced by ``new``."""
    k = type(term)
    if k is Goto:
        return Goto(new if term.to == old else term.to)
    if k is If:
        return If(term.c, new if term.t == old else term.t, new if term.f == old else term.f)
    if k is Switch:
        return Switch(
            term.e,
            tuple((v, new if l == old else l) for v, l in term.cases),
            new if term.default == old else term.default,
        )
    return term


def evalbin(op, a, b, w):
    """One binary op, byte-exactly as ``vm._emit_line`` generates it."""
    if op == "+":
        return (a + b) & MASK[w]
    if op == "-":
        return (a - b) & MASK[w]
    if op == "&":
        return a & b
    if op == "|":
        return a | b
    if op == "^":
        return a ^ b
    if op == "<<":
        return (a << b) & MASK[w]
    if op == ">>":
        return a >> b
    if op == "==":
        return 1 if a == b else 0
    if op == "!=":
        return 1 if a != b else 0
    if op == "<":
        return 1 if a < b else 0
    if op == "<=":
        return 1 if a <= b else 0
    if op == "carry":
        return 1 if a + b > MASK[w] else 0
    raise TrapError("bad op", op)
