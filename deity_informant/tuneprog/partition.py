"""S6 -- region typing by accessor-shape partition, and its mirror, the merge.

An access starting at its own operand names a k-element array, a constant address
a scalar, a reach starting inside the region nothing; the narrow claim wins and the
overrunner keeps the fused region and its asserted bound.
"""

from __future__ import annotations

from .facts import image_copy, scales
from .ir import Load, Rgn, Store
from .irwalk import addr_split, apply_stmt, apply_term, node_loads, reachable

SPLITTABLE = ("state", "init_constant", "const", "image")
RANK = {"array": 0, "scalar": 1}  # an array claim is tried before a scalar one


def repartition(prog, facts):
    """Merge the extents of one array, then split what one reach fused.

    Presentation-only: it runs over :func:`~.pipeline.present`'s copy, so no
    certified S4 region id moves and a part is a fresh id above every existing one.
    """
    named = _named(facts)
    merged = _merge(prog, named)
    _recell(prog, [(k, 0, 0xFFFF, v) for k, v in merged.items()])
    parts, moved = _split(prog, named, _fields(prog))
    _recell(prog, moved)
    return bool(merged or parts)


def _named(facts):
    """Regions a record already partitions: the register image, and a record stride.

    An index carrying a scale reaches a record wider than a byte, which is what
    :func:`~.views.field_split` names; an extent claimed inside it is one of its
    fields, not a fusion.
    """
    sc = scales(facts)
    out = set(image_copy(facts))
    return out | {r for n, rids in facts.idxvar.items() if sc.get(n) for r in rids}


def _recell(prog, moves):
    """Point the copy folds' per-copy cells at the region that now owns each address."""

    def at(cell):
        rid, addr = cell
        return next(((n, addr) for o, lo, hi, n in moves if o == rid and lo <= addr <= hi), cell)

    for f in prog.meta.get("copyviews") or () if moves else ():
        cells = [[at(c) for c in v] for v in (f.get("slots") or {}).values()]
        keys = [tuple(v[0]) for v in cells]
        if len(set(keys)) != len(keys):
            raise ValueError("repartition collapsed two slots onto one cell: %r" % (keys,))
        f["slots"] = dict(zip(keys, cells))
        f["columns"] = {k: at(c) for k, c in (f.get("columns") or {}).items()}


def _fields(prog):
    """``{region: [{the addresses one fold names as one field}]}`` -- a view not to cut.

    The fold proved those cells one field of one record; a claim that keeps some of
    them and not others contradicts a partition the program already carries.
    """
    out = {}
    for f in prog.meta.get("copyviews") or ():
        for cells in (f.get("slots") or {}).values():
            by = {}
            for rid, addr in cells:
                by.setdefault(rid, set()).add(addr)
            for rid, addrs in by.items():
                out.setdefault(rid, []).append(addrs)
    return out


def _uncut(r, claims, groups):
    """Drop the claims that keep some cells of a fold's field and not the others.

    The rest of the partition stands: a claim contradicting a record the program
    already carries loses, it does not veto the region.
    """
    bad = set()
    for addrs in groups:
        hit = {_which(claims, a - r.base) for a in addrs}
        bad |= hit if len(hit) > 1 else set()
    return [c for i, c in enumerate(claims) if i not in bad]


def _which(claims, off):
    """The claim an offset falls in, or ``-1`` for the residue the parent keeps."""
    return next((i for i, (lo, hi) in enumerate(claims) if lo <= off <= hi), -1)


# ---- accesses ----------------------------------------------------------------
def _refs(prog):
    """``(procedure, region, envelope low, envelope high, address, is a store)``."""
    for n, p in prog.procs.items():
        for b in p.blocks.values():
            for s in list(b.stmts) + [b.term]:
                for x in node_loads(s):
                    yield n, x.r, x.lo, x.hi, x.a, False
                if type(s) is Store and s.r >= 0:
                    yield n, s.r, s.lo, s.hi, s.a, True


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


def _refused(r, named):
    """A region no partition applies to: a copymap, a record stride, or a named view."""
    return r.id < 0 or r.id in named or r.stride != 1 or r.kind not in SPLITTABLE


# ---- the mirror: three extents of one array ----------------------------------
def _merge(prog, named):
    """Stride-1 regions of one kind, one origin, whose extents overlap are one array.

    Overlapping bytes cannot be two arrays, and one origin is what says the
    accessors agree on shape: they index the same table from the same literal.
    Overlap alone was measured and refused -- it fuses per-copy columns.
    """
    runs, remap = {}, {}
    for r in sorted(prog.storage, key=lambda r: (r.base, r.id)):
        # a byte merged into its neighbour prints as an index, not a name: +210 tokens
        if _refused(r, named) or r.size < 2:
            continue
        run = runs.setdefault((r.kind, r.zero), [[]])
        if run[-1] and r.base >= max(q.base + q.size for q in run[-1]):
            run.append([])
        run[-1].append(r)
    for rs in [rs for run in runs.values() for rs in run if len(rs) > 1]:
        keep, lo = min(rs, key=lambda r: r.id), rs[0].base
        n = max(r.base + r.size for r in rs) - lo
        image, seen = bytearray(n), bytearray(n)
        for r in rs:
            o, k = r.base - lo, len(r.init)
            was = zip(seen[o : o + k], image[o : o + k], r.init)
            assert all(not s or a == b for s, a, b in was), "one image, two initial values"
            image[o : o + k], seen[o : o + k] = r.init, b"\1" * k
        keep.name = "%s_%04X" % (keep.kind, lo)
        keep.base, keep.size, keep.init = lo, len(image), bytes(image)
        remap.update({r.id: keep.id for r in rs if r is not keep})
    if remap:
        _repoint(prog, lambda rid, _lo, _hi: remap.get(rid))
        prog.storage = [r for r in prog.storage if r.id not in remap]
    return remap


# ---- the partition -----------------------------------------------------------
def _cover(r, lo, hi, a):
    """``(shape, low, high)`` of one access inside ``r``, in base-relative bytes.

    ``array`` when the envelope starts at the access's own operand and spans more
    than one byte, so the span is the element count; ``scalar`` for a constant
    address; ``''`` for a reach that starts inside the region, or a one-byte
    observation of an index, neither of which claims an extent.
    """
    lo, hi = lo - r.base, hi - r.base
    if lo < 0 or hi >= r.size or lo > hi:
        return None
    base, idx = addr_split(a)
    if idx is None:
        return "scalar", lo, hi
    return ("array" if hi > lo and base == r.base + lo else "", lo, hi)


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
    they are, the layout is a record and the stride views (:func:`~.views.field_split`,
    :func:`~.views.transpose_split`) are what name it.
    """
    w = {hi - lo for lo, hi in claims}
    d = {b[0] - a[0] for a, b in zip(claims, claims[1:])}
    return len(w) == 1 and len(d) == 1


def _disagree(covers, claims):
    """True when some access is contained in no claim: the partition is a real boundary.

    Every access inside one claim is that shape observed, however narrowly. An
    access contained in none of them either crosses a boundary the claims drew or
    lies wholly in the residue the parent keeps; both say the claims are not the
    whole region. It is also the access :func:`_split` cannot move, so a partition
    never orphans the parent and the parent's range overlaps its parts'.
    """
    return any(not any(a <= c[1] and c[2] <= b for a, b in claims) for c, _w, _t in covers if c)


def _kind(r, lo, hi, stores, band):
    """A part no store's envelope reaches is read-only, whatever its neighbours are."""
    if r.kind not in ("state", "init_constant") or any(a <= hi and lo <= b for a, b in stores):
        return r.kind
    return "const" if band[0] <= r.base + lo and r.base + hi < band[1] else "image"


def _part(r, lo, hi, kind, rid):
    """One carved extent as a region of its own, indexed from its own base."""
    return Rgn(
        id=rid,
        name="%s_%04X" % (kind, r.base + lo),
        base=r.base + lo,
        size=hi - lo + 1,
        kind=kind,
        init=r.init[lo : hi + 1],
        fields=(0,),
        origin=r.base + lo,
    )


def _split(prog, named, fields):
    """Carve every region its accessors' shapes disagree about.

    Returns ``([part ids], [(region, low, high, part)])`` -- the parts, and the
    address ranges that moved into them.
    """
    band = tuple(prog.meta.get("load") or (0, 0))
    tick = reachable(prog, prog.meta.get("tick_proc")) or set(prog.procs)
    byr = {}
    for n, rid, lo, hi, a, w in _refs(prog):
        byr.setdefault(rid, []).append((lo, hi, a, w, n in tick))
    nid = max((r.id for r in prog.storage), default=0) + 1
    new, moved, span = [], {}, []
    for r in prog.storage:
        if _refused(r, named):
            continue
        covers = [(_cover(r, lo, hi, a), w, t) for lo, hi, a, w, t in byr.get(r.id, ())]
        claims = _claims([c for c, _w, t in covers if c and c[0] and t])
        if not claims:
            continue
        claims = _uncut(r, claims, fields.get(r.id, ()))
        if len(claims) < 2 or _uniform(claims) or not _disagree(covers, claims):
            continue
        stores = [(c[1], c[2]) for c, w, _t in covers if c and w]
        ids = range(nid, nid + len(claims))
        for (lo, hi), pid in zip(claims, ids):
            new.append(_part(r, lo, hi, _kind(r, lo, hi, stores, band), pid))
            span.append((r.id, r.base + lo, r.base + hi, pid))
        for lo2, hi2, _a, _w, _t in byr[r.id]:
            k = _which(claims, lo2 - r.base)
            if k >= 0 and hi2 - r.base <= claims[k][1]:
                moved[(r.id, lo2, hi2)] = ids[k]
        nid += len(claims)
    if not new:
        return [], []
    _repoint(prog, lambda rid, lo, hi: moved.get((rid, lo, hi)))
    prog.storage = sorted(prog.storage + new, key=lambda r: (r.id < 0, r.base, r.id))
    return [r.id for r in new], span
