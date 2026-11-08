"""The state an init routine leaves per song (``docs/cgen.md``, *Init*), and its provenance.

Tags (:class:`Run`): ``None`` a constant of init's code, ``("L", key)`` a byte loaded at ``key``,
``("W", keys, 0, n)``/``("W", key, 1, n)`` the low/high byte of chain ``n`` on a word, ``"X"``
anything else; a key is ``(base, off)``, ``off`` past the word at key ``base``.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from jennings.devices.mpu6502 import NativeMPU
from jennings.memory import NativeMemory
from jennings.opcodes import OPCODES

from . import wholeprog

__all__ = ["Init", "reference", "run", "cost", "ENTRY", "Run", "traced", "pointers", "groups"]

#: Table bytes of one copy: its destination, source and length, two bytes each.
ENTRY = 6


def reference(volatile):
    """A machine running init on the reference interpreter over ``volatile``.

    ``machine(mem, pc, a)`` runs init at ``pc`` with ``A = a`` on ``mem`` (a ``bytearray`` it
    leaves as init does) and gives its volatile writes ``[(addr, value)]`` in order and the
    addresses it stored to."""

    def machine(mem, pc, a):
        r = wholeprog.reference_call(mem, pc, a=a, volatile=volatile, log=True, written=True)
        if r["halt"] != "return":
            raise RuntimeError(f"init at ${pc:04X} with A=${a:02X}: {r['halt']}")
        return [(x, v) for x, v, _, w in r["log"] if w], r["written"]

    return machine


@dataclass
class Init:
    """What init leaves on ``image`` for each of ``songs``: ``states[i]`` the memory,
    ``writes[i]`` the volatile writes in order; ``free`` the addresses it stored to outside the
    stack page and the volatile ranges, ascending."""

    image: np.ndarray
    songs: list
    states: np.ndarray
    writes: list
    free: np.ndarray

    def values(self):
        """``{addr: [values]}``: the values each free byte holds over the songs."""
        held = self.states[:, self.free]
        return {int(a): sorted(set(held[:, i].tolist())) for i, a in enumerate(self.free)}

    def varying(self):
        """The free bytes whose value differs between songs, ascending."""
        held = self.states[:, self.free]
        return self.free[(held != held[0]).any(axis=0)]

    def copies(self, source, base):
        """Per song, the copies ``[(dst, src, n)]`` making ``base`` its state on the free bytes.

        ``src`` is the address of a run of bytes no song changes that ``source`` allows, the
        longest the song's values continue as when longer than :data:`ENTRY` (``bytes.find``
        over doubling then halving lengths), else a ``bytes`` literal."""
        free = np.zeros(0x10000, dtype=bool)
        free[self.free] = True
        ok = np.asarray(source, dtype=bool) & ~free
        segs = [(a, base[a : a + n].tobytes()) for a, n in _runs(ok) if n > ENTRY]
        found = {}

        def longest(t):
            """``(src, n)`` of the longest prefix of ``t`` some segment holds."""
            best, n = None, ENTRY + 1
            while n <= len(t) and (hit := _find(segs, t[:n], found)) is not None:
                best, n = (hit, n), n * 2
            lo, hi = (best[1] if best else ENTRY) + 1, min(n, len(t) + 1)
            while lo < hi:
                mid = (lo + hi) // 2
                hit = _find(segs, t[:mid], found)
                lo, hi, best = (mid + 1, hi, (hit, mid)) if hit is not None else (lo, mid, best)
            return best

        out = []
        for st in self.states:
            ops, same = [], st == base
            for a, n in _runs(free):
                t, i, lit = st[a : a + n].tobytes(), 0, None
                while i < n:
                    if same[a + i] and lit is None:
                        i += 1
                        continue
                    m = longest(t[i:])
                    if m is None and not same[a + i]:
                        lit = i if lit is None else lit
                        i += 1
                        continue
                    if lit is not None:
                        ops.append((a + lit, t[lit:i], i - lit))
                        lit = None
                    if m is not None:
                        ops.append((a + i, *m))
                        i += m[1]
                if lit is not None:
                    ops.append((a + lit, t[lit:], n - lit))
            out.append(ops)
        return out


def cost(plan):
    """The table bytes of a ``plan`` of :meth:`Init.copies`: an entry per copy, and its
    literal bytes."""
    return sum(ENTRY + (n if isinstance(s, bytes) else 0) for ops in plan for _, s, n in ops)


def _runs(mask):
    """``[(start, length)]`` of the runs of True in ``mask``."""
    edges = np.flatnonzero(np.diff(np.concatenate(([0], np.asarray(mask, np.int8), [0]))))
    return [(int(a), int(b - a)) for a, b in zip(edges[::2], edges[1::2])]


def _find(segs, t, memo):
    """The address of the first occurrence of ``t`` in the segments ``[(addr, bytes)]``."""
    if t not in memo:
        memo[t] = next((a + i for a, b in segs if (i := b.find(t)) >= 0), None)
    return memo[t]


def run(image, pc, songs, volatile, machine=None):
    """The :class:`Init` of init at ``pc`` on ``image`` for each value of ``A`` in ``songs``,
    on ``machine`` (default :func:`reference` over ``volatile``)."""
    machine = machine or reference(volatile)
    vol = np.zeros(0x10000, dtype=bool)
    vol[0x100:0x200] = True
    for lo, hi in volatile:
        vol[lo:hi] = True
    states, writes = [], []
    stored = np.zeros(0x10000, dtype=bool)
    for a in songs:
        mem = bytearray(image)
        w, s = machine(mem, pc, a)
        states.append(np.frombuffer(bytes(mem), dtype=np.uint8))
        writes.append([(int(x), int(v)) for x, v in w])
        stored[np.asarray(list(s), dtype=np.int64)] = True
    img = np.frombuffer(bytes(image), dtype=np.uint8)
    return Init(img, list(songs), np.stack(states), writes, np.flatnonzero(stored & ~vol))


X = "X"
_COPY = {"TAX": ("a", "x"), "TAY": ("a", "y"), "TXA": ("x", "a"), "TYA": ("y", "a")}
_LOAD = {"LDA": "a", "LDX": "x", "LDY": "y", "LAX": "ax", "LAS": "ax"}
_STORE = {"STA": "a", "STX": "x", "STY": "y", "SAX": "ax", "SHA": "ax", "SHX": "x", "SHY": "y"}
_CARRY_CONST = {"CLC", "SEC", "CMP", "CPX", "CPY", "PLP", "DCP", "SBX", "ANC", "RTI"}
_REG_ALU = {"INX": "x", "DEX": "x", "INY": "y", "DEY": "y", "SBX": "x"}
_ACC_ALU = {"AND", "ORA", "EOR", "ANE", "LXA", "ALR", "ARR", "ANC"}
_RMW = {"ASL", "LSR", "ROL", "ROR", "INC", "DEC", "SLO", "RLA", "SRE", "RRA", "ISC", "DCP"}
_SHIFT = {"ASL", "LSR", "ROL", "ROR", "SLO", "RLA", "SRE", "RRA", "ALR", "ARR"}
_MEM = {"abs", "absx", "absy", "zp", "zpx", "zpy", "indx", "indy"}


def _next(key):
    return (key[0], (key[1] + 1) & 0xFFFF)


def _word(lo, hi):
    """``(key, chain)`` of the word whose two bytes have tags ``lo``, ``hi``, or None."""
    if not (isinstance(lo, tuple) and isinstance(hi, tuple)):
        return None
    if lo[0] == "L":
        keys, n = (lo[1],), 0
    elif lo[0] == "W" and lo[2] == 0:
        keys, n = lo[1], lo[3]
    else:
        return None
    if hi[0] == "L":
        key = next((k for k in keys if _next(k) == hi[1]), None)
        return (key, n) if n == 0 and key is not None else None
    return (hi[1], n) if hi[0] == "W" and hi[2:] == (1, n) and hi[1] in keys else None


class Run:
    """One run of init: ``mem`` the memory it leaves, ``tags`` the tag of every byte it wrote
    and ``vals`` the byte each key was loaded with."""

    def __init__(self, image, volatile):
        self.mem = NativeMemory(bytes(image))
        self.mpu = NativeMPU(memory=self.mem)
        self.volatile = [tuple(v) for v in volatile]
        self.tags, self.vals, self.acc = {}, {}, []
        self.reg = dict.fromkeys("axyc")
        self.chain = 0

    def _key(self, tags, ea):
        """The key of the address ``ea`` reached from a base held in bytes tagged ``tags``."""
        if all(t is None for t in tags):
            return (None, ea)
        w = _word(*tags) if len(tags) == 2 else None
        if w is None:
            return None
        k = w[0]
        held = self.vals[k] | self.vals[_next(k)] << 8
        return (k, (ea - held) & 0xFFFF)

    def _tag(self, a, key):
        """The tag of the byte at ``a`` (key ``key``) as a load reads it."""
        if a in self.tags:
            return self.tags[a]
        if key is None or any(lo <= a < hi for lo, hi in self.volatile):
            return X
        self.vals.setdefault(key, self.mem[a])
        return ("L", key)

    def _adc(self, ta, tm):  # pylint: disable=too-many-return-statements
        """``(sum, carry)`` tags of an ``ADC``/``SBC`` of tags ``ta`` and ``tm``."""
        tc = self.reg["c"]
        if self.mpu.p & NativeMPU.DECIMAL or X in (ta, tm, tc):
            return X, X
        ws = [t for t in (ta, tm) if t is not None and t[0] == "W"]
        ls = [t for t in (ta, tm) if t is not None and t[0] == "L"]
        if not ws and not ls:
            return (None, None) if tc is None else (X, X)
        if len(ws) == 2:
            return X, X
        _, cks, cn = tc or (None, (), None)
        if ws and ws[0][2] == 1:
            key, n = ws[0][1], ws[0][3]
            if tc is None or key in cks:
                return ("W", key, 1, n if tc is None else cn), X
            return X, X
        if tc is not None:
            # A high byte: the carry names its low byte's words, the load the one it is.
            hit = [c for c in cks for t in ls if _next(c) == t[1]]
            return (("W", hit[0], 1, cn), X) if len(hit) == 1 and not ws else (X, X)
        keys = ws[0][1] if ws else tuple(t[1] for t in ls)
        self.chain += 1
        return ("W", keys, 0, self.chain), ("C", keys, self.chain)

    def _base(self, mode, ops):
        """The tags of the bytes holding the base of the access of an instruction in ``mode``
        with operand bytes at ``ops``, before it runs: its zero-page pointer's or operand's."""
        if mode in ("indx", "indy"):
            zp = (self.mem[ops[0]] + (self.mpu.x if mode == "indx" else 0)) & 0xFF
            return [self._tag(a, (None, a)) for a in (zp, (zp + 1) & 0xFF)]
        return [self.tags.get(a) for a in ops] if mode[:3] == "abs" else []

    def step(self):  # pylint: disable=too-many-branches
        """Execute one instruction, carrying the tags."""
        mpu, mem, reg = self.mpu, self.mem, self.reg
        pc, sp = mpu.pc, mpu.sp
        name, mode = OPCODES[mem[pc]]
        n = 2 if mode[:3] == "abs" else 0 if mode in ("impl", "acc") else 1
        ops = [(pc + 1 + i) & 0xFFFF for i in range(n)]
        imm = self.tags.get(ops[0]) if mode == "imm" else None
        mtag, tags = None, self._base(mode, ops) if mode in _MEM else []
        self.acc = []
        mpu.step()
        if mode in _MEM and self.acc:
            ea = self.acc[-1][0]
            mtag = self._tag(ea, self._key(tags, ea))
        elif mode == "imm":
            mtag = imm
        if name in _LOAD:
            for r in _LOAD[name]:
                reg[r] = mtag
        elif name in _STORE and mode in _MEM:
            rs = _STORE[name]
            ok = len(rs) == 1 or reg["a"] is None and reg["x"] is None
            self.tags[ea] = reg[rs[0]] if ok else X
        elif name in _COPY:
            reg[_COPY[name][1]] = reg[_COPY[name][0]]
        elif name in ("ADC", "SBC"):
            reg["a"], reg["c"] = self._adc(reg["a"], mtag)
        elif name in _REG_ALU:
            reg[_REG_ALU[name]] = None if reg[_REG_ALU[name]] is None else X
        elif name in _ACC_ALU or mode == "acc":
            reg["a"] = None if reg["a"] is None and mtag is None else X
        elif name in _RMW and mode in _MEM:
            self.tags[ea] = mtag = None if mtag is None else X
            if name in ("SLO", "RLA", "SRE", "RRA", "ISC"):
                reg["a"] = None if reg["a"] is None and mtag is None else X
        elif name in ("PHA", "PHP"):
            self.tags[0x100 | sp] = reg["a"] if name == "PHA" else None
        elif name == "PLA":
            reg["a"] = self.tags.get(0x100 | (sp + 1) & 0xFF)
        elif name in ("JSR", "BRK"):
            for d in range(3 if name == "BRK" else 2):
                self.tags[0x100 | (sp - d) & 0xFF] = None
        elif name == "TSX":
            reg["x"] = None
        if name in _SHIFT:
            reg["c"] = mtag if mode in _MEM else reg["a"]
        elif name in _CARRY_CONST:
            reg["c"] = None

    def call(self, pc, a=0, max_steps=10_000_000):
        """``run_to_rts`` from ``pc`` with ``A = a``, returning to the dummy ``$0001``."""
        mpu = self.mpu
        self.mem[0x1FE] = self.mem[0x1FF] = 0
        mpu.sp, mpu.pc, mpu.a = 0xFD, pc, a
        self.tags[0x1FF] = self.tags[0x1FE] = None
        self.mem.observe_data_access(
            on_read=lambda x: self.acc.append((x, False)),
            on_write=lambda x, _: self.acc.append((x, True)),
            stack=True,
        )
        try:
            for _ in range(max_steps):
                if mpu.sp > 0xFD:
                    return self
                self.step()
        finally:
            self.mem.observe_data_access(None, None)
        raise RuntimeError(f"init at ${pc:04X} did not return")


def traced(image, pc, a=0, volatile=((0xD000, 0xE000),)):
    """The :class:`Run` of init at ``pc`` with ``A = a`` on ``image``; None when it does not
    return on the reference interpreter (``wholeprog.reference_call``) within its budget."""
    ref = wholeprog.reference_call(bytearray(image), pc, a=a, volatile=list(volatile))
    if ref["halt"] != "return":
        return None
    return Run(image, volatile).call(pc, a, ref["cycles"] // 2 + 1)


def pointers(r):
    """``{lo: (key, value)}``: the pairs the run ``r`` wrote as the two bytes of one chain on a
    word, by that word's key, and the value each holds."""
    out = {}
    for lo in sorted(r.tags):
        hi = (lo + 1) & 0xFFFF
        w = _word(r.tags[lo], r.tags[hi]) if hi in r.tags else None
        if w is not None:
            out[lo] = (w[0], r.mem[lo] | r.mem[hi] << 8)
    return out


def groups(runs, pairs):
    """``{lo: (g, delta)}``: each pair of ``pairs`` every run of ``runs`` left as a pointer on
    the same word as pair ``g``, holding ``g``'s value plus ``delta``; ``g`` the least pair of
    the group's most common value, so most of its pairs read it as it stands."""
    ps = [pointers(r) for r in runs]
    keys = {}
    for lo in sorted(pairs):
        if all(lo in p for p in ps):
            keys.setdefault(tuple(p[lo][0] for p in ps), []).append(lo)
    out = {}
    for los in keys.values():
        vals = [tuple(p[lo][1] for p in ps) for lo in los]
        counts = [(vals.count(v), -i) for i, v in enumerate(vals)]
        g = los[counts.index(max(counts))]
        for lo in los:
            ds = {(p[lo][1] - p[g][1]) & 0xFFFF for p in ps}
            if lo != g and len(ds) == 1:
                out[lo] = (g, ds.pop())
    return out
