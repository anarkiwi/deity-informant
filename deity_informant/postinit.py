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
from jennings.opcodes import OPCODES

from . import devices, masks, polling, wholeprog

__all__ = [
    "Init",
    "reference",
    "run",
    "reproduces",
    "bounded",
    "cost",
    "ENTRY",
    "Run",
    "traced",
    "forks",
    "FORKS",
    "pointers",
    "groups",
]

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
    #: The bytes outside the stack page and the volatile ranges.
    kept: np.ndarray | None = None
    #: Per run, the addresses it stored to (:func:`run`), where known.
    stored: list | None = None

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

    def varying(self):
        """The free bytes whose value differs between songs, ascending."""
        held = self.states[:, self.free]
        return self.free[(held != held[0]).any(axis=0)]

    def select(self, songs):
        """This init's runs of the values ``songs`` (of :attr:`songs`, its runs' ``stored``
        known)."""
        rows = [self.songs.index(a) for a in songs]
        stored = [self.stored[i] for i in rows]
        free = np.zeros(0x10000, dtype=bool)
        for s in stored:
            free[s] = True
        writes = [self.writes[i] for i in rows]
        free &= self.kept
        return Init(
            self.image,
            list(songs),
            self.states[rows],
            writes,
            np.flatnonzero(free),
            self.kept,
            stored,
        )

    def extended(self, runs):
        """This init with the distinct states the :class:`Run` s ``runs`` leave that it lacks
        (their volatile writes alongside): its free bytes and the bytes those store."""
        states, writes = list(self.states), list(self.writes)
        free = self.mask
        for r in runs:
            st = np.frombuffer(bytes(r.mem[0:0x10000]), np.uint8)
            stored = [x for (_, w), xs in r.touched.items() if w for x in xs]
            free[np.asarray(stored, dtype=np.int64)] = True
            if not any((st == o)[self.kept].all() for o in states):
                states.append(st)
                writes.append(list(r.writes))
        free &= self.kept
        return Init(
            self.image, self.songs, np.stack(states), writes, np.flatnonzero(free), self.kept
        )

    def copies(self, source, base):
        """Per song, the copies ``[(dst, src, n)]`` making ``base`` its state on the free bytes.

        ``src`` is the address of a run of bytes no song changes that ``source`` allows, the
        longest the song's values continue as when longer than :data:`ENTRY` (``bytes.find``
        over doubling then halving lengths), else a ``bytes`` literal."""
        free = self.mask
        ok = np.asarray(source, dtype=bool) & ~free
        segs = [(a, base[a : a + n].tobytes()) for a, n in masks.runs(ok) if n > ENTRY]
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
            for a, n in masks.runs(free):
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


def _find(segs, t, memo):
    """The address of the first occurrence of ``t`` in the segments ``[(addr, bytes)]``."""
    if t not in memo:
        memo[t] = next((a + i for a, b in segs if (i := b.find(t)) >= 0), None)
    return memo[t]


def run(image, pc, songs, volatile, machine=None, skip=False):
    """The :class:`Init` of init at ``pc`` on ``image`` for each value of ``A`` in ``songs``,
    on ``machine`` (default :func:`reference` over ``volatile``); with ``skip``, of each
    value it returns with (the others left out of ``songs``)."""
    machine = machine or reference(volatile)
    vol = masks.mask([(0x100, 0x200), *volatile])
    states, writes, kept, each = [], [], [], []
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
        writes.append([(int(x), int(v)) for x, v in w])
        each.append(np.asarray(sorted(s), dtype=np.int64))
        stored[each[-1]] = True
    img = np.frombuffer(bytes(image), dtype=np.uint8)
    held = np.stack(states) if states else np.zeros((0, 0x10000), np.uint8)
    return Init(img, kept, held, writes, np.flatnonzero(stored & ~vol), ~vol, each)


def reproduces(prog, image, state, runs):
    """Whether lift ``prog`` of init (its one entry; throw-away reads not made) reproduces from
    ``image`` each run of ``runs`` (per argument value of the :class:`Init` ``state``, its
    :func:`forks`): it returns without a deopt making the run's volatile reads (its polls and
    probes) and writes, leaves the run's memory outside the stack page and the volatile
    ranges, and one run of each value is the machine's (its writes and state)."""
    img = np.frombuffer(bytes(image), np.uint8)
    for a, want, writes, rs in zip(state.songs, state.states, state.writes, runs):
        machine, want = False, np.where(state.kept & ~state.mask, img, want)
        for ran in rs:
            mem = bytearray(image)
            r = prog.call(mem, 0, a=a, log=True, reads=bytes(v for _, v in ran.reads))
            log = [tuple(e[:2]) for e in r["log"] if e[3]]
            reads = [tuple(e[:2]) for e in r["log"] if not e[3]]
            if r["halt"] != "return" or r["deopt"] is not None or not ran.exact:
                return False
            got = np.frombuffer(bytes(mem), np.uint8)
            left = np.frombuffer(bytes(ran.mem[0:0x10000]), np.uint8)
            if reads != ran.reads or log != ran.writes or not (got == left)[state.kept].all():
                return False
            machine |= log == writes and (left == want)[state.kept].all()
        if not machine:
            return False
    return True


def bounded(prog, runs):
    """Whether every access of the runs ``runs`` (:class:`Run` s of init) lies in what lift
    ``prog`` bounds it to: a pointer load in its hulls (``reach``, ``proven``), a bounded
    store in its array (``bounded``), an access with an address set in the set
    (``addr_sets``)."""
    boxes = [(dict(prog.reach), 0, False), (dict(prog.proven), 0, False)]
    boxes.append((dict(prog.bounded), 1, True))
    sets = dict(prog.addr_sets)
    for r in runs:
        for (pc, w), xs in r.touched.items():
            got = np.fromiter(xs, dtype=np.int64)
            for box in (b.get(pc) for b, _, store in boxes if store == w):
                if box is not None and not ((got >= box[0]) & (got <= box[1] - w)).all():
                    return False
            spans = sets.get((pc, w))
            if spans is not None:
                inside = np.zeros(got.shape, dtype=bool)
                for lo, hi in spans:
                    inside |= (got >= lo) & (got < hi)
                if not inside.all():
                    return False
    return True


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


class Run:  # pylint: disable=too-many-instance-attributes
    """One run of init: ``mem`` the memory it leaves, ``tags`` the tag of every byte it wrote
    and ``vals`` the byte each key was loaded with. With ``polls``
    (:func:`deity_informant.polling.loops`) the device answers each the least byte it leaves
    on (``reads``), ``exact`` while it is read only there or where ``model`` describes it."""

    def __init__(self, image, volatile, polls=None, model=None, answers=()):
        self.mem = NativeMemory(bytes(image))
        self.mpu = NativeMPU(memory=self.mem)
        self.volatile = [tuple(v) for v in volatile]
        self.tags, self.vals, self.acc = {}, {}, []
        #: ``{(pc, write): {address}}``: the effective addresses each instruction read, wrote.
        self.touched = {}
        self.reg = dict.fromkeys("axyc")
        self.chain = 0
        self.polls, self.reads, self.exact = polls or {}, [], True
        self.varying, self._loop, self.strict = frozenset(), None, False
        #: The device state (:class:`deity_informant.devices.State`), the volatile writes and
        #: the probes ``[(addr, values, answer)]`` (reads the model answers), in order; the
        #: cycle of the running instruction's last access.
        self.dev = None if model is None else devices.State(model)
        self.writes, self.probes, self.answers = [], [], tuple(answers)
        self._at, self._answered = None, set()

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

    def _vol(self, x):
        return any(lo <= x < hi for lo, hi in self.volatile)

    def _answer(self, addr):
        """A volatile read outside a polling loop: the model's least value, or the one
        :attr:`answers` gives this probe; None (the memory's byte) where undescribed."""
        if self.dev is None or self._loop is not None or self._at is None:
            return None
        vals = self.dev.read(addr, self._at)
        if vals is None:
            return None
        vals = tuple(sorted(vals))
        k = len(self.probes)
        v = self.answers[k] if k < len(self.answers) else vals[0]
        self.probes.append((addr, vals, v))
        self.reads.append((addr, v))
        self._answered.add(addr)
        return v

    def _wrote(self, x, v):
        self.acc.append((x, True))
        if self._vol(x):
            self.writes.append((x, v))
            if self.dev is not None:
                self.dev.write(x, v, self._at)

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
        self.acc, self._answered = [], set()
        self._at = mpu.processorCycles + mpu.cycletime[mem[pc]] - 1
        mpu.step()
        self._at = None
        for x, w in self.acc if mode in _MEM else ():
            if x == self.acc[-1][0]:
                self.touched.setdefault((pc, w), set()).add(x)
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
            if self.writes and self.writes[-1][0] == ea and ea in self._answered:
                self.writes.insert(len(self.writes) - 1, (ea, self.reads[-1][1]))
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

    def _poll(self):
        """Settle a polling loop entered at the next instruction: the device answers it the
        least byte it leaves on, the registers that differ between such bytes ``varying`` once
        it is left. A device read outside it, a loop not settling, a read of ``varying`` ends
        ``exact``."""
        pc, inside = self.mpu.pc, self._loop
        if inside is not None and pc in inside[0].pcs:
            if pc == inside[0].at:
                self.reads.append((inside[0].addr, inside[1]))
            return
        if inside is not None:
            self.varying, self._loop = inside[2], None
            if self.dev is not None:
                self.dev.wait()
        elif any(not w and self._vol(x) and x not in self._answered for x, w in self.acc):
            self._inexact(pc)
        loop = self.polls.get(pc)
        if loop is None:
            if self.varying:
                use, defs, _ = polling.flow(self.mem, pc)
                if use & self.varying:
                    self._inexact(pc)
                self.varying -= defs
            return
        got = polling.settle(loop, self.mem, polling.registers(self.mpu), pc, self.dev)
        if got is None or self.varying & loop.live[pc]:
            self._inexact(pc)
            return
        self.mem[loop.addr], self._loop = got[0], (loop, got[0], got[1])
        if pc == loop.at:
            self.reads.append((loop.addr, got[0]))

    def _inexact(self, pc):
        self.exact = False
        if self.strict:
            raise RuntimeError(f"init at ${pc:04X} depends on the device")

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
        if self.dev is not None:
            vol = [range(lo, min(hi, 0x10000)) for lo, hi in self.volatile]
            self.mem.subscribe_to_read([a for r in vol for a in r], self._answer)
        try:
            for _ in range(max_steps):
                if mpu.sp > 0xFD:
                    return self
                if self.polls:
                    self._poll()
                self.step()
        finally:
            self.mem.observe_data_access(None, None)
        raise RuntimeError(f"init at ${pc:04X} did not return")


def traced(
    image, pc, a=0, volatile=((0xD000, 0xE000),), polls=None, model=None, answers=(), ref=None
):  # pylint: disable=too-many-arguments,too-many-positional-arguments
    """The :class:`Run` of init at ``pc`` with ``A = a`` on ``image``, its ``polls`` and
    device ``model`` (its probes answered by ``answers``, then their least values); None when
    it does not return within the reference interpreter's budget (its run ``ref``,
    ``wholeprog.reference_call``; a run that only returns by its polls must stay ``exact``)."""
    if ref is None:
        ref = wholeprog.reference_call(bytearray(image), pc, a=a, volatile=list(volatile))
    if ref["halt"] != "return" and not polls:
        return None
    r = Run(image, volatile, polls, model, answers)
    r.strict = ref["halt"] != "return"
    try:
        return r.call(pc, a, ref["cycles"] // (1 if model else 2) + 1)
    except RuntimeError:
        return None


#: The most runs :func:`forks` makes of one argument value.
FORKS = 1024


def forks(image, pc, a, volatile, polls=None, model=None, cap=FORKS):
    """The :class:`Run` s of init at ``pc`` with ``A = a`` (:func:`traced`), one per sequence
    of values its probes may read (every value of each probe's set); None where one does
    not return or there are more than ``cap``."""
    out, work = [], [()]
    ref = wholeprog.reference_call(bytearray(image), pc, a=a, volatile=list(volatile))
    with tqdm(desc="init forks", unit="run", leave=False, disable=None) as progress:
        while work:
            r = traced(image, pc, a, volatile, polls, model, work.pop(), ref)
            if r is None:
                return None
            for j in range(len(r.answers), len(r.probes)):
                prefix = tuple(v for _, _, v in r.probes[:j])
                work.extend(prefix + (x,) for x in r.probes[j][1] if x != r.probes[j][2])
            out.append(r)
            progress.update()
            if len(out) + len(work) > cap:
                return None
    return out


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


def groups(runs, pairs=None):
    """``{lo: (g, delta)}``: each pair of ``pairs`` (default all) every run of ``runs`` left as
    a pointer on the same word as pair ``g``, holding ``g``'s value plus ``delta``; ``g`` the
    least pair of the group's most common value, so most of its pairs read it as it stands."""
    ps = [pointers(r) for r in runs]
    keys = {}
    for lo in sorted(set().union(*ps) if pairs is None else pairs):
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
