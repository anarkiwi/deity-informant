"""The state an init routine leaves per song (``docs/cgen.md``, *Init*), and its provenance.

Tags (:class:`Run`): ``None`` a constant of init's code, ``("L", key)`` a byte loaded at ``key``,
``("W", keys, 0, n)``/``("W", key, 1, n)`` the low/high byte of chain ``n`` on a word, ``"X"``
anything else; a key is ``(base, off)``, ``off`` past the word at key ``base``.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from tqdm import tqdm

from jennings.devices.mpu6502 import NativeMPU
from jennings.memory import NativeMemory
from jennings.isa import length, pointer_bytes
from jennings.opcodes import MEM_MODES as _MEM
from jennings.opcodes import OPCODES

from . import masks, wholeprog

__all__ = ["Init", "reference", "run", "Run"]


def reference(volatile):
    """A machine running init on the reference interpreter over ``volatile``.

    ``machine(mem, pc, a)`` runs init at ``pc`` with ``A = a`` on ``mem`` (a ``bytearray`` it
    leaves as init does) and gives its volatile accesses ``[(addr, value, write)]`` in order
    and the addresses it stored to; a machine giving ``(addr, value)`` gives writes only."""

    def machine(mem, pc, a):
        r = wholeprog.reference_call(mem, pc, a=a, volatile=volatile, log=True, written=True)
        if r["halt"] != "return":
            raise RuntimeError(f"init at ${pc:04X} with A=${a:02X}: {r['halt']}")
        return [(x, v, bool(w)) for x, v, _, w in r["log"]], r["written"]

    return machine


@dataclass
class Init:
    """What init leaves on ``image`` for each of ``songs``: ``states[i]`` the memory,
    ``writes[i]`` the volatile writes in order; ``free`` the addresses it stored to outside the
    stack page and the volatile ranges, ascending; ``io[i]`` the volatile accesses
    ``(addr, value, write)`` in order, where the machine gives reads."""

    image: np.ndarray
    songs: list
    states: np.ndarray
    writes: list
    free: np.ndarray
    #: The bytes outside the stack page and the volatile ranges.
    kept: np.ndarray | None = None
    #: Per run, the addresses it stored to (:func:`run`), where known.
    stored: list | None = None
    io: list | None = None

    @property
    def mask(self):
        """The free bytes, as a boolean array."""
        return masks.mask(at=self.free)

    def values(self):
        """``{addr: [values]}``: the value each free byte holds on each song, in order."""
        held = self.states[:, self.free]
        return {int(a): held[:, i].tolist() for i, a in enumerate(self.free)}

    def same(self):
        """The free bytes holding one value on every run, ascending."""
        held = self.states[:, self.free]
        return self.free[(held == held[:1]).all(axis=0)]

    def with_states(self, states):
        """This init with the 64 KiB images ``states`` besides its runs' (no volatile writes):
        its free bytes and those where one of them differs from its first run's state."""
        if len(states) == 0:
            return self
        extra = np.stack([np.frombuffer(bytes(s), np.uint8)[:0x10000] for s in states])
        free = self.mask
        free[(extra != self.states[0]).any(axis=0)] = True
        free &= self.kept
        return Init(
            self.image,
            self.songs,
            np.concatenate([self.states, extra]),
            [*self.writes, *([] for _ in extra)],
            np.flatnonzero(free),
            self.kept,
            io=None if self.io is None else [*self.io, *([] for _ in extra)],
        )


def run(image, pc, songs, volatile, machine=None, skip=False):
    """The :class:`Init` of init at ``pc`` on ``image`` for each value of ``A`` in ``songs``,
    on ``machine`` (default :func:`reference` over ``volatile``); with ``skip``, of each
    value it returns with (the others left out of ``songs``)."""
    machine = machine or reference(volatile)
    vol = masks.mask([(0x100, 0x200), *volatile])
    states, writes, kept, each, io = [], [], [], [], []
    stored = np.zeros(0x10000, dtype=bool)
    for a in tqdm(songs, desc="init runs", unit="run", leave=False, disable=None):
        mem = bytearray(image)
        try:
            w, s = machine(mem, pc, a)
        except RuntimeError:
            if not skip:
                raise
            continue
        kept.append(a)
        states.append(np.frombuffer(bytes(mem), dtype=np.uint8))
        io.append([(int(e[0]), int(e[1]), bool(e[2]) if len(e) > 2 else True) for e in w])
        writes.append([(x, v) for x, v, wr in io[-1] if wr])
        each.append(np.asarray(sorted(s), dtype=np.int64))
        stored[each[-1]] = True
    img = np.frombuffer(bytes(image), dtype=np.uint8)
    held = np.stack(states) if states else np.zeros((0, 0x10000), np.uint8)
    return Init(img, kept, held, writes, np.flatnonzero(stored & ~vol), ~vol, each, io)


X = "X"
_COPY = {"TAX": ("a", "x"), "TAY": ("a", "y"), "TXA": ("x", "a"), "TYA": ("y", "a")}
_LOAD = {"LDA": "a", "LDX": "x", "LDY": "y", "LAX": "ax", "LAS": "ax"}
_STORE = {"STA": "a", "STX": "x", "STY": "y", "SAX": "ax", "SHA": "ax", "SHX": "x", "SHY": "y"}
_CARRY_CONST = {"CLC", "SEC", "CMP", "CPX", "CPY", "PLP", "DCP", "SBX", "ANC", "RTI"}
_REG_ALU = {"INX": "x", "DEX": "x", "INY": "y", "DEY": "y", "SBX": "x"}
_ACC_ALU = {"AND", "ORA", "EOR", "ANE", "LXA", "ALR", "ARR", "ANC"}
_RMW = {"ASL", "LSR", "ROL", "ROR", "INC", "DEC", "SLO", "RLA", "SRE", "RRA", "ISC", "DCP"}
_SHIFT = {"ASL", "LSR", "ROL", "ROR", "SLO", "RLA", "SRE", "RRA", "ALR", "ARR"}


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


class Run:  # pylint: disable=too-many-instance-attributes
    """One run of an entry: ``mem`` the memory it leaves, ``tags`` the tag of every byte it
    wrote, ``vals`` the byte each key was loaded with and ``writes`` its volatile writes."""

    def __init__(self, image, volatile):
        self.mem = NativeMemory(bytes(image))
        self.mpu = NativeMPU(memory=self.mem)
        self.volatile = [tuple(v) for v in volatile]
        self.tags, self.vals, self.acc = {}, {}, []
        self.reg = dict.fromkeys("axyc")
        self.chain = 0
        self.writes = []

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
        if key is None or self._vol(a):
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

    def _vol(self, x):
        return any(lo <= x < hi for lo, hi in self.volatile)

    def _wrote(self, x, v):
        self.acc.append((x, True))
        if self._vol(x):
            self.writes.append((x, v))

    def _base(self, mode, ops):
        """The tags of the bytes holding the base of the access of the instruction at the PC in
        ``mode`` with operand bytes at ``ops``, before it runs: its pointer's or operand's."""
        ptr = pointer_bytes(self.mem, self.mpu.pc, self.mpu.x)
        if ptr is not None:
            return [self._tag(a, (None, a)) for a in ptr]
        return [self.tags.get(a) for a in ops] if mode[:3] == "abs" else []

    def step(self):  # pylint: disable=too-many-branches
        """Execute one instruction, carrying the tags."""
        mpu, mem, reg = self.mpu, self.mem, self.reg
        pc, sp = mpu.pc, mpu.sp
        name, mode = OPCODES[mem[pc]]
        ops = [(pc + 1 + i) & 0xFFFF for i in range(length(mem[pc]) - 1)]
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
            on_write=self._wrote,
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
