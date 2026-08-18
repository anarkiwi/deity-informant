"""The tuneprog machine and the reference interpreter that defines the semantics.

The machine is one flat 64 KiB image plus the marks, logs and pinned input stream
:mod:`~deity_informant.tuneprog.trace` recorded; :class:`Interp` executes a
:class:`~.ir.Tuneprog` over it, and every other executor is verified against it.
"""

from __future__ import annotations

from hashlib import blake2b

from .ir import (
    Bin,
    Call,
    Const,
    Goto,
    If,
    Let,
    Phi,
    REGVAR,
    SID_HI,
    SID_LO,
    STACK_HI,
    STACK_LO,
    Store,
    Switch,
    Trap,
    TrapError,
    Var,
    evalbin,
)


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
        "_nfp",
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
        self._nfp = -1

    # ---- the 6510 port: I/O mapped only when both port bits allow it ---------
    def setbank(self):
        p = (self.m[1] | ~self.m[0]) & 7
        self.bank = 0 if not p & 3 else (1 if not p & 4 else 2)

    def play_phase(self):
        """Switch the footprint set to the play-written one (init keeps its own)."""
        self.W = set()
        self._fp = ()
        self._nfp = -1
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
        """``(footprint size, blake2b digest)`` -- the tracer's periodicity witness.

        The stack page is machine texture, not tune state, so it is outside the
        footprint on both sides (:meth:`~.trace.Tracer._hash`).
        """
        if self._nfp != len(self.W):
            self._nfp = len(self.W)
            self._fp = tuple(sorted(a for a in self.W if not STACK_LO <= a <= STACK_HI))
        n = len(self._fp)
        h = blake2b(
            bytes(map(self.m.__getitem__, self._fp)), digest_size=8, key=n.to_bytes(4, "little")
        ).digest()
        return n, int.from_bytes(h, "little")


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
