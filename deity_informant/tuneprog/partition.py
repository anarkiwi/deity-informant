"""S6 -- region typing by accessor-shape partition, and its mirror, the merge.

An access starting at its own operand names a k-element array, a constant address a
scalar, a reach starting inside the region nothing; the narrow claim wins and the
overrunner keeps the fused region and its asserted bound.
"""

from __future__ import annotations

from .copyview import fold_fields, remap_cells
from .facts import image_copy, per_region, scales, unclaimed
from .ir import Load, Rgn, Store, overlaps, rgn_name
from .irwalk import accessors, apply_stmt, apply_term, reachable

SPLITTABLE = ("state", "init_constant", "const", "image")
RANK = {"array": 0, "scalar": 1}  # an array claim is tried before a scalar one


def repartition(prog, facts):
    """Merge the extents of one array, then split what one reach fused.

    Presentation-only: it runs over :func:`~.pipeline.present`'s copy, so no
    certified S4 region id moves and a part is a fresh id above every existing one.
    """
    named = _record_regions(facts)
    merged = _merge_extents(prog, named)
    remap_cells(prog, [(k, 0, 0xFFFF, v) for k, v in merged.items()])
    parts, moved = _split_regions(prog, named, fold_fields(prog))
    remap_cells(prog, moved)
    return bool(merged or parts)


def _record_regions(facts):
    """Regions a record already partitions: the register image, and a record stride.

    An index carrying a scale reaches a record :func:`~.views.record_split` names
    off the same map, so an extent claimed inside it is a field, not a fusion.
    """
    return set(image_copy(facts)) | set(per_region(facts, scales(facts)))


def _uncut(r, claims, groups):
    """Drop the claims that keep some cells of a fold's field and not the others.

    The rest of the partition stands: a claim contradicting a record the program
    already carries loses, it does not veto the region.
    """
    bad = set()
    for addrs in groups:
        hit = {_which(claims, a - r.zero) for a in addrs}
        bad |= hit if len(hit) > 1 else set()
    return [c for i, c in enumerate(claims) if i not in bad]


def _which(claims, off):
    """The claim an offset falls in, or ``-1`` for the residue the parent keeps."""
    return next((i for i, (lo, hi) in enumerate(claims) if lo <= off <= hi), -1)


def _repoint(prog, pick):
    """Move every access ``pick(region, lo, hi)`` names onto the region it returns.

    An access is keyed by its region and envelope, never by identity: two accesses
    asserting one bound over one region are one claim and land in one part.
    """

    def fn(e):
        if type(e) is Load:
            r = pick(e.r, e.lo, e.hi)
            if r is not None:
                return Load(e.cls, e.a, e.w, e.lo, e.hi, r)
        return e

    for p in prog.procs.values():
        for b in p.blocks.values():
            for s in b.stmts:
                if type(s) is Store and s.r >= 0:
                    r = pick(s.r, s.lo, s.hi)
                    s.r = s.r if r is None else r
                apply_stmt(s, fn)
            apply_term(b.term, fn)


def _cuttable(r, named):
    """A stride-1 region of splittable storage that no record view already names."""
    return unclaimed(r, named, SPLITTABLE) and r.stride == 1


# ---- the mirror: three extents of one array ----------------------------------
def _merge_extents(prog, named):
    """Stride-1 regions of one kind, one origin, whose extents overlap are one array.

    Overlapping bytes cannot be two arrays, and one origin is what says the
    accessors agree on shape: they index the same table from the same literal.
    Overlap alone was measured and refused -- it fuses per-copy columns.
    """
    by, remap = {}, {}
    for r in prog.storage:
        # a byte merged into its neighbour prints as an index, not a name: +210 tokens
        if _cuttable(r, named) and r.size > 1:
            by.setdefault((r.kind, r.zero), []).append(r)
    for rs in [g for one in by.values() for g in overlaps(one) if len(g) > 1]:
        keep, lo = min(rs, key=lambda r: r.id), rs[0].base
        n = max(r.base + r.size for r in rs) - lo
        image, seen = bytearray(n), bytearray(n)
        for r in rs:
            o, k = r.base - lo, len(r.init)
            was = zip(seen[o : o + k], image[o : o + k], r.init)
            assert all(not s or a == b for s, a, b in was), "one image, two initial values"
            image[o : o + k], seen[o : o + k] = r.init, b"\1" * k
        keep.name = rgn_name(keep.kind, lo)
        keep.base, keep.size, keep.init = lo, len(image), bytes(image)
        remap.update({r.id: keep.id for r in rs if r is not keep})
    if remap:
        _repoint(prog, lambda rid, _lo, _hi: remap.get(rid))
        prog.storage = [r for r in prog.storage if r.id not in remap]
    return remap


# ---- the partition -----------------------------------------------------------
def _cover(r, acc):
    """``(shape, low, high)`` of one accessor inside ``r``, in bytes from its zero.

    ``array`` when the envelope starts at the access's own operand and spans more
    than one byte, so the span is the element count; ``scalar`` for a constant
    address; ``''`` for a reach starting inside the region or a one-byte index read.
    """
    e = r.extent(acc.lo, acc.hi)
    if e is None:
        return None
    lo, hi = e
    if acc.idx is None:
        return "scalar", lo, hi
    return ("array" if hi > lo and acc.base == r.zero + lo else "", lo, hi)


def _claims(covers):
    """The disjoint extents the play-time shapes agree on, arrays before scalars.

    A scalar inside an array is one of its elements, not a competing extent; a
    claim partly overlapping a narrower one disagrees about a byte and loses, and
    is the overrunning accessor. Two claims are the least a partition can be.
    """
    out = []
    for _s, lo, hi in sorted(covers, key=lambda c: (RANK.get(c[0], len(RANK)), c[2] - c[1], c[1])):
        if (lo, hi) not in out and not any(lo <= b and a <= hi for a, b in out):
            out.append((lo, hi))
    return sorted(out) if len(out) > 1 else []


def _uniform(claims):
    """True when every claim is the same width at one spacing: a record, not a fusion.

    The mechanism's premise is that the accessors are *not* all one shape; where
    they are, the layout is a record and :func:`~.views.record_split` is what names it.
    """
    w = {hi - lo for lo, hi in claims}
    d = {b[0] - a[0] for a, b in zip(claims, claims[1:])}
    return len(w) == 1 and len(d) == 1


def _disagree(covers, claims):
    """True when some access is contained in no claim: the partition is a real boundary.

    An access contained in no claim crosses a boundary the claims drew or lies in
    the residue the parent keeps; either way they are not the whole region. It is
    the access :func:`_split_regions` cannot move, so the parent is never orphaned.
    """
    return any(not any(a <= c[1] and c[2] <= b for a, b in claims) for c, _w, _t in covers if c)


def _part_kind(r, lo, hi, stores, band):
    """A part no store's envelope reaches is read-only, whatever its neighbours are."""
    if r.kind not in ("state", "init_constant") or any(a <= hi and lo <= b for a, b in stores):
        return r.kind
    return "const" if band[0] <= r.zero + lo and r.zero + hi < band[1] else "image"


def _part(r, lo, hi, kind, rid):
    """One carved extent as a region of its own, indexed from its own base."""
    lo, hi = r.zero + lo, r.zero + hi
    return Rgn(
        id=rid,
        name=rgn_name(kind, lo),
        base=lo,
        size=hi - lo + 1,
        kind=kind,
        init=r.init[lo - r.base : hi - r.base + 1],
        fields=(0,),
        origin=lo,
    )


def _split_regions(prog, named, fields):
    """Carve every region its accessors' shapes disagree about.

    Returns ``([part ids], [(region, low, high, part)])`` -- the parts, and the
    address ranges that moved into them.
    """
    band = tuple(prog.meta.get("load") or (0, 0))
    tick = reachable(prog, prog.meta.get("tick_proc")) or set(prog.procs)
    byr = {}
    for acc in accessors(prog):
        byr.setdefault(acc.rid, []).append(acc)
    nid = max((r.id for r in prog.storage), default=0) + 1
    new, moved, span = [], {}, []
    for r in prog.storage:
        if not _cuttable(r, named):
            continue
        covers = [(_cover(r, acc), acc.store, acc.proc in tick) for acc in byr.get(r.id, ())]
        claims = _claims([c for c, _w, t in covers if c and c[0] and t])
        if not claims:
            continue
        claims = _uncut(r, claims, fields.get(r.id, ()))
        if len(claims) < 2 or _uniform(claims) or not _disagree(covers, claims):
            continue
        stores = [(c[1], c[2]) for c, w, _t in covers if c and w]
        ids = range(nid, nid + len(claims))
        for (lo, hi), pid in zip(claims, ids):
            new.append(_part(r, lo, hi, _part_kind(r, lo, hi, stores, band), pid))
            span.append((r.id, r.zero + lo, r.zero + hi, pid))
        for acc in byr[r.id]:
            k = _which(claims, acc.lo - r.zero)
            if k >= 0 and acc.hi - r.zero <= claims[k][1]:
                moved[(r.id, acc.lo, acc.hi)] = ids[k]
        nid += len(claims)
    if not new:
        return [], []
    _repoint(prog, lambda rid, lo, hi: moved.get((rid, lo, hi)))
    prog.storage = sorted(prog.storage + new, key=lambda r: (r.id < 0, r.base, r.id))
    return [r.id for r in new], span
