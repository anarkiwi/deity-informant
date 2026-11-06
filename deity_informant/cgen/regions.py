"""The memory partition of a translated program: constant code and data regions.

Constant code is the bytes of reached instructions no constant-address store writes; its span
also covers the tables the lift read jump targets and stored values from, which a store of
unknown address must not change either, and the data regions it overlaps. Data regions are
the maximal runs of other bytes some access may touch (an access of unknown address may touch
every byte of the loaded image), outside volatile ranges.
"""

from __future__ import annotations

import numpy as np

from ..lifter import MODE_LEN, OPS

#: Widest address interval treated as bounded; wider accesses are of unknown address.
BOUNDED = 0x200


def code_bytes(image, starts):
    """The instruction bytes of the instructions at ``starts``, as a boolean array."""
    mask = np.zeros(0x10000, dtype=bool)
    for pc in starts:
        n = MODE_LEN[OPS[image[pc]][1]] if image[pc] in OPS else 1
        mask[(pc + np.arange(n)) & 0xFFFF] = True
    return mask


def successors(image, pc):
    """Where the instruction at ``pc`` may go: a branch's next instruction and target, a
    ``JMP abs``'s target, else the next instruction."""
    op = OPS.get(image[pc], ("", "impl"))
    nxt = (pc + MODE_LEN[op[1]]) & 0xFFFF
    if op[1] == "rel":
        return [nxt, (nxt + (image[(pc + 1) & 0xFFFF] ^ 0x80) - 0x80) & 0xFFFF]
    if op == ("JMP", "abs"):
        return [image[(pc + 1) & 0xFFFF] | image[(pc + 2) & 0xFFFF] << 8]
    return [nxt]


def edge(image, p, q):
    """``[from, to]``: the instruction edge from the instruction at ``p`` toward ``q`` (an
    instruction where the target block's code starts): past the instructions with one
    successor (``JMP``s the lift threads included), a branch's edge to ``q``, the last
    ``JMP`` of a chain from one of its successors to ``q``, else its successor at or
    before ``q``."""
    seen = set()
    while p not in seen:
        seen.add(p)
        ss = successors(image, p)
        if len(ss) > 1:
            if q in ss:
                return [p, q]
            hit = [s for s in ss if through(image, s) == q]
            if hit:
                return [_last_jump(image, hit[0]), q]
            below = [s for s in ss if s <= q]
            return [p, max(below) if below else ss[0]]
        if ss[0] == q:
            return [p, q]
        p = ss[0]
    return [p, q]


def _last_jump(image, pc):
    """The last ``JMP abs`` of the chain from ``pc``."""
    seen = set()
    while (n := successors(image, pc)[0]) not in seen and OPS.get(image[n]) == ("JMP", "abs"):
        seen.add(pc)
        pc = n
    return pc


def through(image, pc):
    """Where a chain of ``JMP abs`` from ``pc`` ends."""
    seen = set()
    while OPS.get(image[pc]) == ("JMP", "abs") and pc not in seen:
        seen.add(pc)
        pc = successors(image, pc)[0]
    return pc


def pointer_pairs(image, starts):
    """``{lo: hi}`` of the byte pairs instructions use as addresses: the zero-page pointers of
    ``(zp),Y``/``(zp,X)``, ``JMP (ind)`` vectors, the ``BRK`` vector, three-byte operands and
    page-1 pairs."""
    pairs = {p: p + 1 for p in range(0x100, 0x1FF)}
    for pc in starts:
        if image[pc] == 0x00:
            pairs[0xFFFE] = 0xFFFF
        if image[pc] not in OPS:
            continue
        mode = OPS[image[pc]][1]
        if mode in ("indx", "indy"):
            p = image[(pc + 1) & 0xFFFF]
            pairs[p] = (p + 1) & 0xFF
        elif MODE_LEN[mode] == 3:
            pairs[(pc + 1) & 0xFFFF] = (pc + 2) & 0xFFFF
            if mode == "ind":
                p = image[(pc + 1) & 0xFFFF] | image[(pc + 2) & 0xFFFF] << 8
                pairs[p] = (p & 0xFF00) | ((p + 1) & 0xFF)
    return pairs


def runs(mask):
    """``[(start, length)]`` of the runs of True in ``mask``."""
    edges = np.flatnonzero(np.diff(np.concatenate(([0], mask.view(np.int8), [0]))))
    return [(int(a), int(b - a)) for a, b in zip(edges[::2], edges[1::2])]


class Memory:  # pylint: disable=too-many-instance-attributes
    """Regions of ``image`` for the accesses of a program (see the module docstring):
    ``code_span`` names the constant bytes (``CODE_ORG``); ``held`` are the spans of those a
    bounded access may reach and of the tables (widened over the regions they overlap, which
    are their slices), each held in memory as ``Cj`` from ``Cj_ORG``."""

    def __init__(
        self, image, code, volatile, loaded, sensitive=(), pairs=None, tables=None, written=()
    ):  # pylint: disable=too-many-arguments,too-many-positional-arguments
        self.image, self.code, self.pairs = bytes(image), code, pairs or {}
        self.written = list(written)
        self.tables = np.zeros(0x10000, dtype=bool) if tables is None else tables
        self.pairs_to = {}
        for lo, hi in self.pairs.items():
            self.pairs_to.setdefault(hi, []).append(lo)
        self.vol = np.zeros(0x10000, dtype=bool)
        for lo, hi in volatile:
            self.vol[lo:hi] = True
        self.volatile = [tuple(r) for r in volatile]
        self.loaded, self.sensitive = loaded, sorted(sensitive)
        self.regions, self.owner, self.unknown = [], np.full(0x10000, -1, dtype=np.int32), False
        self.constant = self.code | self.tables
        self.code_span = self._span(self.constant)
        self.held = []

    def _span(self, mask):
        """The span of ``mask``, widened over the regions it overlaps."""
        span = np.flatnonzero(mask)
        return self._widen(*((int(span[0]), int(span[-1]) + 1) if len(span) else (0, 0)))

    def _widen(self, lo, hi, touch=False):
        """``[lo, hi)`` widened over the regions it overlaps (with ``touch``, or abuts)."""
        for org, n in self.regions if hi > lo else ():
            if org < hi + touch and org + n + touch > lo:
                lo, hi = min(lo, org), max(hi, org + n)
        return lo, hi

    def pointer_run(self, hull, touched):
        """The span ``(a, b)`` (inclusive) a pointer load with address hull ``(lo, hi)`` reads
        inside the loaded program: from its first non-code byte to its last, held as one array
        (code bytes inside it held too). None when the hull meets a volatile range there, or
        reaches below the program a byte ``touched`` (other accesses) or a volatile range holds,
        or holds no data byte of the program."""
        lo, hi = hull[:2]
        a0, a1 = self.loaded
        if lo < a0 and (touched[lo:a0].any() or self.vol[lo:a0].any()):
            return None
        lo, hi = max(lo, a0), min(hi, a1 - 1)
        data = np.flatnonzero(~self.code[lo : hi + 1]) if lo <= hi else np.zeros(0, int)
        if not data.size or self.vol[lo : hi + 1].any():
            return None
        return lo + int(data[0]), lo + int(data[-1])

    def partition(self, accesses, stores, unknown_store=True, joined=(), wide=()):
        """Regions for ``accesses`` (``(lo, hi)`` inclusive, None when unknown), given the
        ``stores`` at constant addresses (code bytes they hit are data). Constant bytes are
        held where a bounded access may reach them, or everywhere when a store of unknown
        address (``unknown_store``) may need them to check it, and tables (data read as
        constant) always; a load of unknown address reaching other code reads ``oob_rd``.
        Each of the ``joined`` intervals (the addresses of an instance's copies) is held in one
        span with the regions it touches, so the copies index one array; each of the ``wide``
        runs (pointer loads', :meth:`pointer_run`) is touched whatever its width."""
        for a in stores:
            self.code[a] = False
        touched = np.zeros(0x10000, dtype=bool)
        for r in accesses:
            if r is None or r[1] - r[0] >= BOUNDED:
                self.unknown = True
            else:
                touched[r[0] : r[1] + 1] = True
        self.constant = constant = self.code | self.tables
        need = constant.copy() if unknown_store else touched & self.code | self.tables
        if self.unknown:
            touched[self.loaded[0] : self.loaded[1]] = True
        for lo, hi in self.written:
            touched[lo:hi] = True
        for lo, hi in wide:
            touched[lo : hi + 1] = True
        touched &= ~constant & ~self.vol
        for lo, hi in joined:
            need[lo : hi + 1] |= constant[lo : hi + 1] | touched[lo : hi + 1]
        self.regions = runs(touched)
        for k, (org, n) in enumerate(self.regions):
            self.owner[org : org + n] = k
        self.code_span = self._span(constant)
        self.held = self._spans(need)

    def hull(self, lo, hi):
        """The hull of the bytes in ``[lo, hi]`` when every one is constant (code, or a table
        the lift read as constant), else the full byte range."""
        if hi - lo < 0x100 and self.constant[lo : hi + 1].all():
            b = np.frombuffer(self.image, np.uint8)[lo : hi + 1]
            return int(b.min()), int(b.max())
        return 0, 255

    def _spans(self, need):
        """The runs of ``need`` merged across gaps of data region bytes (slices, held once),
        each widened over the regions it overlaps: any other byte of a gap would be held as data
        the program never reads."""
        spans = []
        for org, n in runs(need):
            if spans and (self.owner[spans[-1][1] : org] >= 0).all():
                spans[-1][1] = org + n
            else:
                spans.append([org, org + n])
        out = []
        for a, b in (self._widen(a, b, True) for a, b in spans):
            if out and out[-1][1] >= a:
                out[-1] = (out[-1][0], max(out[-1][1], b))
            else:
                out.append((a, b))
        return out

    def held_span(self, lo, hi, store=False, wide=False):
        """The index of the held span holding all of ``[lo, hi]`` (narrower than ``BOUNDED``
        unless ``wide``) when each byte is held there: a constant (not for a ``store``) or a
        byte of a region inside it, else None."""
        data = self.owner[lo : hi + 1] >= 0
        held = data if store else data | self.constant[lo : hi + 1]
        if hi - lo >= BOUNDED and not wide or not held.all():
            return None
        return next((j for j, (a, b) in enumerate(self.held) if a <= lo and hi < b), None)

    def span_of(self, org, n):
        """The index of the held span a region ``(org, n)`` lies in, else None."""
        return next((j for j, (a, b) in enumerate(self.held) if a <= org and org + n <= b), None)

    def region(self, lo, hi, wide=False):
        """The region holding all of ``[lo, hi]`` (narrower than ``BOUNDED`` unless ``wide``),
        or None."""
        k = int(self.owner[lo])
        return k if self.owner[hi] == k >= 0 and (wide or hi - lo < BOUNDED) else None

    def is_volatile(self, lo, hi):
        """Whether all of ``[lo, hi]`` is volatile."""
        return hi - lo < BOUNDED and bool(self.vol[lo : hi + 1].all())

    def is_code(self, lo, hi):
        """Whether all of ``[lo, hi]`` is constant code."""
        return hi - lo < BOUNDED and bool(self.code[lo : hi + 1].all())

    def sensitive_in(self, lo, hi):
        """Whether a read-sensitive address lies in ``[lo, hi]``."""
        return any(lo <= a <= hi for a in self.sensitive)

    def base(self, addr):
        """``(symbol, offset)`` of a constant address: relative to the region holding it, or
        to the code span or loaded image when the program holds it, else ``(None, addr)``
        (a device or fixed machine address, as a vector the program writes)."""
        k = int(self.owner[addr])
        if k >= 0:
            return f"R{k}_ORG", addr - self.regions[k][0]
        if not self.loaded[0] <= addr < self.loaded[1]:
            return None, addr
        if self.code_span[0] <= addr < self.code_span[1]:
            return "CODE_ORG", addr - self.code_span[0]
        return "IMG_ORG", addr - self.loaded[0]
