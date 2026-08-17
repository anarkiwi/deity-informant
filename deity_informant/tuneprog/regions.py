"""S3 -- storage typing: regions from the exact op-level access relation.

Access units are P-Code ops, so a ``(zp),Y`` load's pointer bytes never merge
with the stream they address. :func:`build_regions` unions the addresses touched
by one op (plus the cell reads :mod:`.lift` residualised, plus the two halves of
a zero-page pointer, which are one 16-bit access in two ops), takes connected
components, and types each component:

``state`` (written at play time, or by init in a union build) / ``init_constant``
(written by init only) /
``const`` (read-only, inside the load band) / ``image`` (read-only, outside it) /
``io`` (``$D000-$DFFF``). Regions may lie inside executed instruction bytes --
that is what an SMC operand cell is.

``stride`` comes from the index-register domain of the accesses that are really
affine in X or Y (``LiftedSite.idx_ops``, so a ``(zp),Y`` pointer fetch carries
no domain), falling back to the address spacing; ``fields`` are the accessors'
base offsets modulo it, so a struct-of-arrays layout (stride 1) or a
struct-of-code layout (stride 49) falls out of the trace. Each accessor keeps
its observed extent (the envelope an indexed access must stay inside).

Public API: :func:`build_regions`, :func:`index_regions`, :class:`Region`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from math import gcd

from ..lifter import OPS
from .trace import IO_LO, IO_HI, IDX_REG


@dataclass
class Region:
    """One storage region: a connected component of the access relation.

    ``addrs`` is the exact address set; ``base``/``size`` are its extent and
    ``init_bytes`` the pre-init image over that extent, so an address ``a`` reads
    ``init_bytes[a - base]``. A sparse region (stride > 1) therefore carries the
    bytes between its cells too -- for an SMC region those are instruction bytes.
    """

    id: int
    name: str
    base: int
    size: int
    addrs: tuple
    kind: str
    stride: int = 1
    fields: tuple = ()
    accessors: list = field(default_factory=list)
    init_bytes: bytes = b""
    origin: int = 0

    @property
    def count(self):
        return len(self.addrs)

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "base": self.base,
            "size": self.size,
            "kind": self.kind,
            "stride": self.stride,
            "origin": self.origin,
            "fields": list(self.fields),
            "addrs": list(self.addrs),
            "init": self.init_bytes.hex(),
            "accessors": [dict(a, site=list(a["site"])) for a in self.accessors],
        }


class _DSU(dict):
    def find(self, a):
        r = self.setdefault(a, a)
        while r != self[r]:
            r = self[r]
        while self[a] != r:
            self[a], a = r, self[a]
        return r

    def union(self, it):
        it = iter(it)
        first = next(it, None)
        if first is None:
            return
        r = self.find(first)
        for x in it:
            s = self.find(x)
            if s != r:
                self[s] = r


def _accesses(trace, lifted):
    """``[(site key, op index, 'r'|'w'|'c', frozenset addrs)]`` -- one per access."""
    out = []
    for key, s in trace.sites.items():
        for i, a in s["reads"].items():
            if a:
                out.append((key, i, "r", frozenset(a)))
        for i, a in s["writes"].items():
            if a:
                out.append((key, i, "w", frozenset(a)))
        ls = (lifted or {}).get(key)
        if ls is None:
            continue
        for addr, sz in ls.cell_loads:
            out.append((key, -1, "c", frozenset((addr + k) & 0xFFFF for k in range(sz))))
    return out


def _pointer_unions(trace, lifted):
    """Address sets of ``(zp),Y`` pointer pairs: the two fetch ops are one access."""
    out = []
    for key, ls in (lifted or {}).items():
        s = trace.sites.get(key)
        if s is None:
            continue
        for lo, hi in ls.ptr_pairs:
            addrs = s["reads"].get(lo, set()) | s["reads"].get(hi, set())
            if addrs:
                out.append(frozenset(addrs))
    return out


def _domain_gcd(values):
    if len(values) < 2:
        return 0
    lo = min(values)
    g = 0
    for v in values:
        g = gcd(g, v - lo)
    return g


def _kind(addrs, trace, init_kind="init_constant"):
    if any(IO_LO <= a <= IO_HI for a in addrs):
        return "io"
    if addrs & trace.written_play:
        return "state"
    if addrs & trace.written_init:
        return init_kind
    lo, hi = trace.meta["load"]
    return "const" if all(lo <= a < hi for a in addrs) else "image"


def build_regions(trace, lifted=None, init_kind="init_constant"):
    """Regions of ``trace``; ``lifted`` adds the residualised cell reads.

    ``init_kind`` types what only ``init`` writes: ``init_constant`` for one
    subtune (the printer folds it), ``state`` for a union over subtunes, where the
    bytes are whatever that subtune's init put there.
    """
    accesses = _accesses(trace, lifted)
    dsu = _DSU()
    for _key, _i, _m, addrs in accesses:
        dsu.union(addrs)
    for addrs in _pointer_unions(trace, lifted):
        dsu.union(addrs)
    groups = {}
    for a in list(dsu):
        groups.setdefault(dsu.find(a), set()).add(a)
    by_root = {}
    regions = []
    for n, (root, addrs) in enumerate(sorted(groups.items(), key=lambda kv: min(kv[1]))):
        base = min(addrs)
        kind = _kind(addrs, trace, init_kind)
        r = Region(
            id=n,
            name="%s_%04X" % (kind, base),
            base=base,
            size=max(addrs) - base + 1,
            addrs=tuple(sorted(addrs)),
            kind=kind,
        )
        by_root[root] = r
        regions.append(r)
    for key, i, mode, addrs in accesses:
        r = by_root[dsu.find(next(iter(addrs)))]
        s = trace.sites[key]
        ls = (lifted or {}).get(key)
        indexed = i in ls.idx_ops if ls is not None else OPS[key[1]][1] in IDX_REG
        idx = s["idx"] if indexed else []
        r.accessors.append(
            {
                "site": key,
                "op": i,
                "mode": mode,
                "base": min(addrs),
                "extent": [min(addrs), max(addrs)],
                "idx": list(idx),
            }
        )
    pre = trace.image_pre
    for r in regions:
        g = 0
        for a in r.accessors:
            g = gcd(g, _domain_gcd(a["idx"]))
        if not g:
            for a in r.addrs:
                g = gcd(g, a - r.base)
        r.stride = g or 1
        r.fields = tuple(sorted({(a["base"] - r.base) % r.stride for a in r.accessors}))
        r.init_bytes = bytes(pre[r.base : r.base + r.size]) if r.kind != "io" else b""
        r.origin = _origin(r)
    return regions


def _origin(r):
    """The address index 0 has: ``operand + minimum observed index`` (design S6).

    A 1-based table is read at ``base-1,Y`` with ``Y >= 1``, so its lowest operand
    lies one byte below the region; indexing from that origin prints the table's
    own index (``T[y]``, and its look-ahead sibling as ``T[y + 1]``).
    """
    ops = [a["base"] - min(a["idx"]) for a in r.accessors if a["idx"]]
    return min(ops + [r.base])


def index_regions(regions):
    """``{address: Region}`` for every address any region covers."""
    return {a: r for r in regions for a in r.addrs}
