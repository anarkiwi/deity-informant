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


def pointer_pairs(image, starts):
    """``{lo: hi}`` of the byte pairs instructions use as addresses: the zero-page pointers of
    ``(zp),Y``/``(zp,X)``, ``JMP (ind)`` vectors, three-byte operands and page-1 pairs."""
    pairs = {p: p + 1 for p in range(0x100, 0x1FF)}
    for pc in starts:
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
    """Regions of ``image`` for the accesses of a program (see the module docstring)."""

    def __init__(
        self, image, code, volatile, loaded, sensitive=(), pairs=None, tables=None
    ):  # pylint: disable=too-many-arguments,too-many-positional-arguments
        self.image, self.code, self.pairs = bytes(image), code, pairs or {}
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
        self.code_span = self._span()

    def _span(self):
        """The span of the bytes taken as constant: code and the tables read for jumps."""
        span = np.flatnonzero(self.code | self.tables)
        return (int(span[0]), int(span[-1]) + 1) if len(span) else (0, 0)

    def partition(self, accesses, stores):
        """Regions for ``accesses`` (``(lo, hi)`` inclusive, None when unknown), given the
        ``stores`` at constant addresses (code bytes they hit are data)."""
        for a in stores:
            self.code[a] = False
        touched = np.zeros(0x10000, dtype=bool)
        for r in accesses:
            if r is None or r[1] - r[0] >= BOUNDED:
                self.unknown = True
            else:
                touched[r[0] : r[1] + 1] = True
        if self.unknown:
            touched[self.loaded[0] : self.loaded[1]] = True
        touched &= ~self.code & ~self.vol
        self.regions = runs(touched)
        for k, (org, n) in enumerate(self.regions):
            self.owner[org : org + n] = k
        lo, hi = self._span()
        for org, n in self.regions if hi > lo else ():
            if org < hi and org + n > lo:
                lo, hi = min(lo, org), max(hi, org + n)
        self.code_span = (lo, hi)

    def region(self, lo, hi):
        """The region holding all of ``[lo, hi]``, or None."""
        k = int(self.owner[lo])
        return k if self.owner[hi] == k >= 0 and hi - lo < BOUNDED else None

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
        """``(symbol, offset)`` of a constant address: relative to the region, code span or
        loaded image holding it, else ``(None, addr)``."""
        k = int(self.owner[addr])
        if k >= 0:
            return f"R{k}_ORG", addr - self.regions[k][0]
        if self.code_span[0] <= addr < self.code_span[1]:
            return "CODE_ORG", addr - self.code_span[0]
        if self.loaded[0] <= addr < self.loaded[1]:
            return "IMG_ORG", addr - self.loaded[0]
        return None, addr
