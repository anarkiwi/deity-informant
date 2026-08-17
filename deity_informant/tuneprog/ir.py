"""The tuneprog IR (design section 4): types, JSON, machine state, reference interpreter.

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
JSR/RTS frames, which the tracer's write log and footprint do not see either.

:class:`Interp` is the semantics: everything else (SSA, idioms, generated
Python) is verified against it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from hashlib import blake2b

SID_LO, SID_HI = 0xD400, 0xD7FF  # the SID decode band
SID_REG_LO, SID_REG_HI = 0xD400, 0xD418  # the register file inside it
IO_LO, IO_HI = 0xD000, 0xDFFF
STACK_LO, STACK_HI = 0x0100, 0x01FF
MASK = (0, 0xFF, 0xFFFF)
REG_NAMES = {0: "A", 1: "X", 2: "Y", 3: "SP", 8: "C", 9: "Z", 10: "I", 11: "D", 13: "V", 14: "N"}
REGVAR = {i: REG_NAMES.get(i, "r%d" % i) for i in range(16)}
REGIDX = {v: k for k, v in REGVAR.items()}


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
    label: str
    stmts: list = field(default_factory=list)
    term: object = field(default_factory=Return)
    src: int = 0
    count: int = 0


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


# ---- machine state -----------------------------------------------------------
class Machine:
    """The tuneprog's machine: flat image, register file, marks, logs, input stream.

    ``k`` marks bytes whose value the program knows (load image, stack page, and
    everything written); ``W`` is the set of addresses written in the current
    phase -- the footprint the periodicity hash covers, exactly as
    :class:`~deity_informant.tuneprog.trace.Tracer` computes it.
    """

    __slots__ = (
        "m",
        "k",
        "W",
        "regs",
        "bank",
        "sid",
        "io",
        "src",
        "inp",
        "icur",
        "override",
        "_fp",
    )

    def __init__(self, image, load=(0, 0), inputs=(), override=None):
        self.m = bytearray(image)
        self.regs = [0] * 16
        self.regs[3] = 0xFF
        self.k = bytearray(0x10000)
        self.k[load[0] : load[1]] = b"\1" * (load[1] - load[0])
        self.k[0x100:0x200] = b"\1" * 0x100
        self.W = set()
        self.bank = 2
        self.setbank()
        self.sid = []
        self.io = []
        self.src = []
        self.inp = list(inputs)
        self.icur = 0
        self.override = dict(override or {})
        self._fp = ()

    # ---- the 6510 port: I/O mapped only when both port bits allow it ---------
    def setbank(self):
        p = (self.m[1] | ~self.m[0]) & 7
        self.bank = 0 if not p & 3 else (1 if not p & 4 else 2)

    def play_phase(self):
        """Switch the footprint set to the play-written one (init keeps its own)."""
        self.W = set()
        self._fp = ()
        return self

    def take_input(self, a):
        """Next pinned input for ``a``; falls back to ``override`` past the stream."""
        if self.icur < len(self.inp):
            rec = self.inp[self.icur]
            if rec[3] != a:
                raise TrapError("input mismatch", "want $%04X got $%04X" % (a, rec[3]))
            self.icur += 1
            return rec[4]
        v = self.override.get(a)
        if v is None:
            raise TrapError("input exhausted", "$%04X" % a)
        return v

    def rdk(self, a):
        """A byte whose value may be unknown (uninitialised RAM is an input)."""
        return self.m[a] if self.k[a] else self.take_input(a)

    def ioload(self, a):
        """A byte from $D000-$DFFF: a pinned input when I/O is mapped, else RAM."""
        if self.bank != 2:
            return self.m[a]
        return self.override[a] if a in self.override else self.take_input(a)

    def iostore(self, a, v, src=0):
        """A store into $D000-$DFFF: a SID write, a schedule effect, or RAM."""
        if self.bank == 2:
            if SID_LO <= a <= SID_HI:
                self.sid.append((a, v))
                self.src.append(src)
            else:
                self.io.append((a, v))
        else:
            self.k[a] = 1
            self.W.add(a)
        self.m[a] = v

    def trap(self, why, detail=""):
        raise TrapError(why, detail)

    def env(self, a, lo, hi, src=0):
        raise TrapError("envelope", "$%04X outside [$%04X,$%04X] at $%04X" % (a, lo, hi, src))

    # ---- multi-byte access (little endian, wrapping like the VM) ------------
    def rd(self, a, w, cls):
        if cls == "ram":
            return self.m[a] if w == 1 else self.m[a] | (self.m[(a + 1) & 0xFFFF] << 8)
        f = self.ioload if cls == "io" else self.rdk
        v = 0
        for i in range(w):
            v |= f((a + i) & 0xFFFF) << (8 * i)
        return v

    def push(self, v):
        """Push a byte the way the CPU's own frames do: memory only, no marks."""
        self.m[0x100 + self.regs[3]] = v & 0xFF
        self.regs[3] = (self.regs[3] - 1) & 0xFF

    def wr(self, a, v, w):
        for i in range(w):
            b = (a + i) & 0xFFFF
            self.m[b] = (v >> (8 * i)) & 0xFF
            self.k[b] = 1
            self.W.add(b)
        if a <= 1:
            self.setbank()

    def hash(self):
        """``(footprint size, blake2b digest)`` -- the tracer's periodicity witness."""
        if len(self._fp) != len(self.W):
            self._fp = tuple(sorted(self.W))
        n = len(self._fp)
        h = blake2b(
            bytes(map(self.m.__getitem__, self._fp)), digest_size=8, key=n.to_bytes(4, "little")
        ).digest()
        return n, int.from_bytes(h, "little")


# ---- reference interpreter ---------------------------------------------------
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


class Interp:
    """Executes a :class:`Tuneprog` over a :class:`Machine`. The semantics."""

    def __init__(self, prog, machine):
        self.prog = prog
        self.M = machine
        self.steps = 0

    def ev(self, e, F):
        t = type(e)
        if t is Var:
            return F[e.n]
        if t is Const:
            return e.v
        if t is Bin:
            return evalbin(e.op, self.ev(e.a, F), self.ev(e.b, F), e.w)
        a = self.ev(e.a, F)
        if not e.lo <= a <= e.hi or a + e.w - 1 > e.hi:
            self.M.env(a, e.lo, e.hi)
        return self.M.rd(a, e.w, e.cls)

    def run(self, name, args=()):
        """Run procedure ``name``; returns the values of its ``rets``."""
        proc = self.prog.procs[name]
        F = dict(zip((REGVAR[i] for i in proc.params), args))
        lbl, prev = proc.entry, None
        M = self.M
        while True:
            blk = proc.blocks[lbl]
            self.steps += 1
            for s in blk.stmts:
                t = type(s)
                if t is Let:
                    F[s.n] = self.ev(s.e, F)
                elif t is Store:
                    a = self.ev(s.a, F)
                    if not s.lo <= a <= s.hi or a + s.w - 1 > s.hi:
                        M.env(a, s.lo, s.hi, s.src)
                    if s.cls == "io":
                        M.iostore(a, self.ev(s.v, F) & 0xFF, s.src)
                    elif s.cls == "raw":
                        M.m[a] = self.ev(s.v, F) & 0xFF
                    else:
                        M.wr(a, self.ev(s.v, F), s.w)
                elif t is Call:
                    vals = self.run(s.proc, [self.ev(a, F) for a in s.args])
                    F.update(zip(s.rets, vals))
                elif t is Phi:
                    F[s.n] = F[s.args[prev]]
                elif not self.ev(s.e, F):
                    raise TrapError(s.why, blk.label)
            term = blk.term
            k = type(term)
            prev = lbl
            if k is Goto:
                lbl = term.to
            elif k is If:
                lbl = term.t if self.ev(term.c, F) else term.f
            elif k is Switch:
                v = self.ev(term.e, F)
                lbl = next((l for c, l in term.cases if c == v), None)
                if lbl is None:
                    raise TrapError("switch", "$%04X value %d" % (blk.src, v))
            elif k is Trap:
                raise TrapError(term.why, "$%04X %s" % (blk.src, blk.label))
            else:
                return tuple(self.ev(v, F) for v in term.vals)
