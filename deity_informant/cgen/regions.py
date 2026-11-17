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
from .. import masks

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
                return [_chain(image, hit[0])[-2], q]
            below = [s for s in ss if s <= q]
            return [p, max(below) if below else ss[0]]
        if ss[0] == q:
            return [p, q]
        p = ss[0]
    return [p, q]


def _chain(image, pc):
    """``[pc, ...]``: the chain of ``JMP abs`` from ``pc`` and where it ends (in a cycle, the
    ``JMP`` it reaches again)."""
    out, seen = [pc], set()
    while OPS.get(image[pc]) == ("JMP", "abs") and pc not in seen:
        seen.add(pc)
        pc = successors(image, pc)[0]
        out.append(pc)
    return out


def through(image, pc):
    """Where a chain of ``JMP abs`` from ``pc`` ends."""
    return _chain(image, pc)[-1]


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


class Memory:  # pylint: disable=too-many-instance-attributes
    """Regions of ``image`` for the accesses of a program (see the module docstring):
    ``code_span`` names the constant bytes (``CODE_ORG``); ``held`` are the spans of those a
    bounded access may reach and of the tables (widened over the regions they overlap, which
    are their slices), each held in memory as ``Cj`` from ``Cj_ORG``."""

    def __init__(
        self, image, code, volatile, loaded, sensitive=(), pairs=None, tables=None, written=()
    ):  # pylint: disable=too-many-arguments,too-many-positional-arguments
        self.image, self.code, self.pairs = bytes(image), code, pairs or {}
        #: The initial bytes of the regions: ``image`` unless an init run on them differs.
        self.init = self.image
        self.written = list(written)
        self.tables = np.zeros(0x10000, dtype=bool) if tables is None else tables
        self.pairs_to = {}
        for lo, hi in self.pairs.items():
            self.pairs_to.setdefault(hi, []).append(lo)
        self.vol = masks.mask(volatile)
        self.volatile = [tuple(r) for r in volatile]
        self.loaded, self.sensitive = loaded, sorted(sensitive)
        self.regions, self.owner, self.unknown = [], np.full(0x10000, -1, dtype=np.int32), False
        self.constant = self.code | self.tables
        self.code_span = self._span(self.constant)
        self.held, self.blocks = [], []
        #: The block arrays the ``.c`` indexes.
        self.blocks_used = set()

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

    def pointer_run(self, hull, whole=False):
        """The span ``(a, b)`` (inclusive) a pointer load with address hull ``(lo, hi)`` reads
        inside the loaded program and the bytes past it, below the volatile ranges, a block copy
        writes (``written``) the hull reaches: from its first non-code byte to its last, held as
        one array (code bytes and the bytes between the program and those inside it held too);
        the hull's other addresses read ``oob_rd``. None when the hull meets a volatile range
        there, or holds no data byte of the program, or (``whole``) leaves the program and
        those bytes; with ``whole`` a hull in those bytes alone is its own span."""
        lo, hi = hull[:2]
        a0, a1 = self.loaded
        vol = np.flatnonzero(self.vol[a1:])
        below = a1 + int(vol[0]) if vol.size else 0x10000
        past = [b for a, b in self.written if a1 <= a <= hi and b <= below]
        if whole and not a0 <= lo <= hi < max([a1, *past]):
            return None
        lo, hi = max(lo, a0), min(hi, max([a1, *past]) - 1)
        data = np.flatnonzero(~self.code[lo : hi + 1]) if lo <= hi else np.zeros(0, int)
        if not data.size or (lo + data[0] >= a1 and not whole) or self.vol[lo : hi + 1].any():
            return None
        return lo + int(data[0]), lo + int(data[-1])

    def partition(  # pylint: disable=too-many-arguments,too-many-positional-arguments
        self,
        accesses,
        stores,
        unknown_store=True,
        joined=(),
        wide=(),
        writes=None,
        state=None,
        sources=(),
        blocks=(),
        hulls=(),
    ):
        """Regions for ``accesses`` (``(lo, hi)`` inclusive, None when unknown), given the
        ``stores`` at constant addresses (code bytes they hit are data). Constant bytes are
        held where a bounded access may reach them, or everywhere when a store of unknown
        address (``unknown_store``) may need them to check it, and tables (data read as
        constant) always; a load of unknown address reaching other code reads ``oob_rd``.
        Each of the ``joined`` intervals (the addresses of an instance's copies) is held in one
        span with the regions it touches, so the copies index one array; each of the ``wide``
        runs (pointer loads', :meth:`pointer_run`) is touched whatever its width. With
        ``writes`` (the bounded stores' ``(lo, hi)``) a byte past the loaded image that no
        store writes is in no region: a read of it is out of bounds (``oob_rd``). The bytes
        of ``state`` (a mask: cells held as C variables) are in a region only where one of
        the ``accesses`` touches them. The regions only stores with address sets (``written``:
        init's among them) or ``sources`` (init's copies, a run each) touch are numbered after
        the others, so the ``.c`` names the same regions whatever songs init runs for. The
        ``blocks`` runs (a data block's, ``hi`` exclusive) are regions, tables or not. Each of
        the ``hulls`` (a data block of several runs', ``hi`` exclusive) is one array (``Bj``,
        ``blocks``) the regions and held spans in it are slices of, indexed by address: its
        other bytes are padding (:meth:`padding`), zero, which no valid run reads (the block's
        check traps first)."""
        for a in stores:
            self.code[a] = False
        bounded = [r for r in accesses if r is not None and r[1] - r[0] < BOUNDED]
        self.unknown |= len(bounded) < len(accesses)
        touched = masks.mask(bounded, True)
        if writes is not None:
            kept = masks.mask(writes, True, out=masks.mask([(0, self.loaded[1]), *self.written]))
            # The copies of an instance index one array: the bytes between theirs are no read.
            for lo, hi in joined:
                kept[lo : hi + 1] |= kept[lo] and kept[hi]
            touched &= kept
        self.constant = constant = self.code | self.tables
        need = constant.copy() if unknown_store else touched & self.code | self.tables
        if self.unknown:
            touched[self.loaded[0] : self.loaded[1]] = True
        masks.mask(wide, True, out=touched)
        played = touched & ~constant & ~self.vol
        for lo, hi in self.written:
            touched[lo:hi] |= True if state is None else ~state[lo:hi]
        touched &= ~constant & ~self.vol
        for lo, hi in joined:
            need[lo : hi + 1] |= constant[lo : hi + 1] | touched[lo : hi + 1]
        masks.mask(sources, True, out=touched)
        touched &= ~constant & ~self.vol
        touched |= masks.mask(blocks) & ~self.code & ~self.vol
        found = masks.runs(touched)
        self.regions = sorted(found, key=lambda r: not played[r[0] : r[0] + r[1]].any())
        for k, (org, n) in enumerate(self.regions):
            self.owner[org : org + n] = k
        self.code_span = self._span(constant)
        self.held = self._spans(need)
        self.blocks = self._blocks(hulls)

    def _blocks(self, hulls):
        """The arrays holding the ``hulls``: each widened over the held spans and regions it
        overlaps, and merged with the others it meets."""
        out = []
        for a, b in sorted(hulls):
            grown = True
            while grown:
                grown = False
                for lo, hi in [*self.held, *((o, o + n) for o, n in self.regions)]:
                    if lo < b and hi > a and (lo < a or hi > b):
                        a, b, grown = min(a, lo), max(b, hi), True
            if out and out[-1][1] >= a:
                out[-1] = (out[-1][0], max(out[-1][1], b))
            else:
                out.append((a, b))
        return out

    def block_of(self, lo, hi):
        """The index of the block array holding ``[lo, hi]``, else None."""
        return next((j for j, (a, b) in enumerate(self.blocks) if a <= lo and hi < b), None)

    def padding(self, j, held=True):
        """The bytes of block array ``j`` neither a region's nor (``held``) a held span's: its
        padding, zero."""
        lo, hi = self.blocks[j]
        pad = self.owner[lo:hi] < 0
        for a, b in self.held if held else ():
            pad[max(a, lo) - lo : max(min(b, hi) - lo, 0)] = False
        return pad

    def block_init(self, j, held=True):
        """The initial bytes of block array ``j``: its padding zero."""
        lo, hi = self.blocks[j]
        b = np.frombuffer(self.init, np.uint8)[lo:hi].copy()
        b[self.padding(j, held)] = 0
        return bytes(b)

    def moves(self, addr, origin=False):
        """Whether ``addr`` moves with the loaded image when it is relocated: a byte of it, or
        (``origin``, a region's origin) its end too, where a region run on past it starts."""
        return self.loaded[0] <= addr < self.loaded[1] + origin

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
        for org, n in masks.runs(need):
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
        return self.holding(lo, hi)

    def holding(self, lo, hi):
        """The index of the held span ``[lo, hi]`` lies in, else None."""
        return next((j for j, (a, b) in enumerate(self.held) if a <= lo and hi < b), None)

    def spans_at(self, idx):
        """Per address of ``idx``, the index of the held span holding it, else -1."""
        if not self.held:
            return np.full(len(idx), -1)
        a, b = np.array(self.held).T
        j = np.searchsorted(a, idx, "right") - 1
        return np.where((j >= 0) & (idx < b[j]), j, -1)

    def region(self, lo, hi, wide=False):
        """The region holding all of ``[lo, hi]`` (narrower than ``BOUNDED`` unless ``wide``),
        or None."""
        k = int(self.owner[lo])
        return k if self.owner[hi] == k >= 0 and (wide or hi - lo < BOUNDED) else None

    def bounded(self, lo, hi):
        """``(regions, held spans)``: the one region or held span holding the bytes of
        ``[lo, hi]`` (narrower than ``BOUNDED``) inside the loaded image (all of them when none
        is), when every byte there is held, else None; every other byte reads ``oob_rd``."""
        if hi - lo >= BOUNDED:
            return None
        idx = np.arange(lo, hi + 1)
        own = self.owner[lo : hi + 1]
        span = np.where(self.constant[lo : hi + 1] | (own >= 0), self.spans_at(idx), -1)
        inside = (idx >= self.loaded[0]) & (idx < self.loaded[1])
        sel = inside | ~inside.any()
        ks, js = np.unique(own[sel & (own >= 0) & (span < 0)]), np.unique(span[sel & (span >= 0)])
        if len(ks) + len(js) != 1 or not ((own >= 0) | (span >= 0))[inside].all():
            return None
        return tuple(int(k) for k in ks), tuple(int(j) for j in js)

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
        """``(symbol, offset)`` of a constant address: relative to the region holding it when
        the two move alike with the loaded image, or to the code span or loaded image when the
        program holds it, else ``(None, addr)`` (a device or fixed machine address, as a vector
        the program writes, or a fixed address past the image in a region starting in it)."""
        k = int(self.owner[addr])
        if k >= 0 and self.moves(self.regions[k][0], True) == self.moves(addr):
            return f"R{k}_ORG", addr - self.regions[k][0]
        if k >= 0:
            return None, addr
        if not self.loaded[0] <= addr < self.loaded[1]:
            return None, addr
        if self.code_span[0] <= addr < self.code_span[1]:
            return "CODE_ORG", addr - self.code_span[0]
        return "IMG_ORG", addr - self.loaded[0]


#: Bytes of one entry of a :func:`background` table: its origin, pool offset and mask.
ENTRY = 6


def _cuts(values, care, p):
    """The run starts of a fewest-runs partition of the address space in which each run's
    ``care`` bytes at one phase (address modulo ``p``) hold one value: each pair of cared bytes
    of one phase, unlike and with none between, needs a start in ``(first, second]``, and the
    second of the pairs by ascending second that the last start misses is one."""
    idx = np.flatnonzero(care)
    order = np.lexsort((idx, idx & (p - 1)))
    a = idx[order]
    bad = ((a[1:] ^ a[:-1]) & (p - 1) == 0) & (values[a[1:]] != values[a[:-1]])
    firsts, seconds = a[:-1][bad], a[1:][bad]
    by = np.argsort(seconds, kind="stable")
    cuts = [0]
    for first, second in zip(firsts[by].tolist(), seconds[by].tolist()):
        if first >= cuts[-1]:
            cuts.append(second)
    return cuts


def _table(values, care, p):
    """``(entries, pool)`` of the runs of :func:`_cuts` at period ``p``: a run whose cared
    bytes span no more than ``p`` is literal (``mask`` 0xFFFF, the pool offset less its first
    cared byte, so ``pool[offset + a]`` reads it; neighbours joined across gaps no wider than
    an entry), any other periodic (``mask`` ``p - 1``, ``pool[offset + (a & mask)]``, units
    shared)."""
    cuts = _cuts(values, care, p)
    ends = cuts[1:] + [0x10000]
    pool, units, parts = bytearray(), {}, []
    for s, e in zip(cuts, ends):
        at = np.flatnonzero(care[s:e]) + s
        if not at.size:
            continue
        lo, hi = int(at[0]), int(at[-1])
        if hi - lo < p:
            if parts and parts[-1][1] == "lit" and lo - parts[-1][3] <= ENTRY:
                parts[-1][3] = hi
            else:
                parts.append([s, "lit", lo, hi])
            continue
        unit = np.zeros(p, np.uint8)
        unit[at & (p - 1)] = values[at]
        parts.append([s, "per", bytes(unit), None])
    entries = []
    for s, kind, x, y in parts:
        if kind == "lit":
            entries.append((s, (len(pool) - x) & 0xFFFF, 0xFFFF))
            pool += bytes(values[x : y + 1])
        else:
            if x not in units:
                units[x] = len(pool)
                pool += x
            entries.append((s, units[x], p - 1))
    if not entries:
        entries, pool = [(0, 0, 0)], bytearray(1)
    entries[0] = (0, *entries[0][1:])
    return entries, bytes(pool)


def _size(table):
    """The bytes of a :func:`_table`."""
    return len(table[0]) * ENTRY + len(table[1])


def background(values, care):
    """``(entries, pool)``: the bytes of ``values`` where ``care`` holds as a table of runs
    ``(origin, offset, mask)`` (ascending, the first at 0; a byte ``a`` reads ``pool[(offset +
    (a & mask)) & 0xFFFF]`` of the last run starting at or below it) over a pool of byte units
    repeating every power of two bytes, or literal, the period the one giving the smallest
    table of those below its size (:data:`ENTRY` bytes an entry): exact on the cared bytes,
    any value elsewhere."""
    values, care = np.frombuffer(bytes(values), np.uint8), np.asarray(care, bool)
    best, p = _table(values, care, 1), 2
    while p <= 0x8000 and p < _size(best):
        t = _table(values, care, p)
        best = t if _size(t) < _size(best) else best
        p *= 2
    return best
