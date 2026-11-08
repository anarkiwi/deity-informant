"""Code cells as C state: scalars and field arrays, by the address sets of their accesses.

A cell all of whose accesses have one constant address is a scalar; those a family reaches at
one address per copy (an instance's, a loop index's), alike over the copies, are a struct's
fields, each an array over the copies; any other stays a byte (``docs/cgen.md``, *State*).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

#: Why a cell stays a byte, by the kind of access (or its absence) that keeps it there.
WHY = ("", "indexed", "pointer", "unknown", "init", "family", "unaccessed")
INDEXED, POINTER, UNKNOWN, INIT, FAMILY, UNACCESSED = range(1, 7)


@dataclass
class State:
    """The promoted cells: ``scalars`` (sorted addresses), ``structs`` (``(bases, fields)``:
    the copies' origins, ascending, and the fields' offsets in each, the structs by first
    base), ``why`` (per address, the :data:`WHY` code of a cell left a byte) and ``unread``
    (the cells promoted but never read, their stores dropped)."""

    scalars: list = field(default_factory=list)
    structs: list = field(default_factory=list)
    why: np.ndarray = field(default_factory=lambda: np.zeros(0x10000, dtype=np.int8))
    unread: list = field(default_factory=list)
    at: dict = field(default_factory=dict)

    def __post_init__(self):
        self.at = {int(a): None for a in self.scalars}
        for s, (bases, fields) in enumerate(self.structs):
            for e, b in enumerate(bases):
                self.at.update({int(b + d) & 0xFFFF: (s, e, int(d)) for d in fields})

    def mask(self):
        """The promoted and dropped cells, as a boolean array."""
        out = np.zeros(0x10000, dtype=bool)
        out[list(self.at) + list(self.unread)] = True
        return out

    def field_of(self, addrs):
        """``(struct, field, elements)`` of a family's per-copy addresses ``addrs`` when they
        are one field of one struct, else None."""
        places = [self.at.get(int(a)) for a in addrs]
        if None in places or len({(p[0], p[2]) for p in places}) != 1:
            return None
        return places[0][0], places[0][2], np.array([p[1] for p in places], dtype=np.int64)

    def counts(self, cells):
        """``{"scalars", "fields", "unread", "bytes": {why: n}}``: the ``cells`` (a mask)
        promoted to scalars and fields, dropped as never read, and left bytes by why."""
        field_at = [a for a, p in self.at.items() if p is not None]
        left = cells.copy()
        left[list(self.at) + list(self.unread)] = False
        why = np.where(left & (self.why == 0), UNACCESSED, self.why)[left]
        kinds, n = np.unique(why, return_counts=True)
        return {
            "scalars": int(cells[self.scalars].sum()),
            "fields": int(cells[field_at].sum()),
            "unread": int(cells[list(self.unread)].sum()),
            "bytes": {WHY[k]: int(c) for k, c in zip(kinds, n)},
        }


def _norm(addrs):
    return tuple(int(a - addrs[0]) for a in addrs)


def _structs(fams):
    """``(structs, at, bad)`` of the families ``fams`` (sorted address tuples), largest first:
    one whose bytes are some of one field's is that field; one alike up to a translation with a
    struct's first family, less than its least gap between copies past its origin, adds a
    field; any other starts a struct; one meeting another struct's bytes is ``bad``."""
    structs, at, bad = [], {}, []
    for fam in sorted(fams, key=lambda a: (-len(a), _norm(a), a[0])):
        places = {at.get(a) for a in fam}
        if len(places) == 1 and None not in places:
            continue
        if places != {None}:
            bad.append(fam)
            continue
        n = _norm(fam)
        s = next(
            (
                s
                for s, (m, base, _) in enumerate(structs)
                if m == n and 0 <= fam[0] - base < min(np.diff(n))
            ),
            None,
        )
        if s is None:
            s = len(structs)
            structs.append((n, fam[0], set()))
        d = fam[0] - structs[s][1]
        structs[s][2].add(d)
        at.update({a: (s, d) for a in fam})
    return structs, at, bad


def classify(cells, sets, why, stepped=()):
    """The :class:`State` of the ``cells`` (a mask) given ``sets`` (the addresses of each
    access with one address per copy or one constant address; those in ``stepped``, in the
    order of a loop's element numbers, must reach consecutive elements) and ``why`` (per
    address the :data:`WHY` code of the other accesses touching it, 0 where none does)."""
    why = why.copy()
    fams = sorted({tuple(sorted({int(a) for a in s})) for s in sets if len(set(s)) > 1})
    steps = [(tuple(sorted({int(a) for a in s})), np.array(s, dtype=np.int64)) for s in stepped]
    while True:
        ok = cells & (why == 0)
        bad = [a for a in fams if not ok[list(a)].all()]
        fams = [a for a in fams if ok[list(a)].all()]
        structs, at, clash = _structs(fams)
        bad += clash
        for a, order in steps:
            if a in fams and a not in clash:
                s, d = at[a[0]]
                n, base = np.array(structs[s][0]), structs[s][1]
                els = np.searchsorted(n, order - d - base)
                bad += [a] if (np.diff(els) != 1).any() else []
        if not bad:
            break
        for a in bad:
            sel = list(a)
            why[sel] = np.where(why[sel] == 0, FAMILY, why[sel])
    owner = np.zeros(0x10000, dtype=bool)
    owner[list(at)] = True
    const = np.zeros(0x10000, dtype=bool)
    const[[int(s[0]) for s in sets if len(set(s)) == 1]] = True
    scalars = np.flatnonzero(cells & (why == 0) & const & ~owner).tolist()
    out = [(base + np.array(n), sorted(f)) for n, base, f in structs]
    out.sort(key=lambda x: x[0][0])
    return State(scalars, out, why)
