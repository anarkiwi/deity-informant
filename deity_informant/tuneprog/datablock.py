"""S7 -- the data section: the bytes the program reads and no store's envelope reaches.

A region's cells are its extent, or the columns its stride marks off; its reach is
the union of its accessors' envelopes over them. Regions whose extents overlap, or
that one frequency layout names, are one block, printed in the layout S6 knows.
"""

from __future__ import annotations

from .partition import SPLITTABLE, refs

ROW = 32  # bytes one data row carries


def cells(r):
    """The addresses a region owns: its extent, or the columns its stride marks off."""
    if r.stride < 2:
        return set(range(r.base, r.base + r.size))
    fields = r.fields or (0,)
    return {r.base + o for o in range(r.size) if o % r.stride in fields}


def _runs(spans_):
    """``spans_`` merged into sorted disjoint ``[(lo, hi)]``, adjacent ones joined."""
    out = []
    for lo, hi in sorted(spans_):
        if out and lo <= out[-1][1] + 1:
            out[-1][1] = max(out[-1][1], hi)
        else:
            out.append([lo, hi])
    return [tuple(x) for x in out]


def spans(prog):
    """``({region: the cells its accessors reach}, the bytes a store can write)``.

    A store reaches its own region's cells inside its envelope -- a column's store
    does not write the columns beside it -- and every byte of it where it has none.
    """
    own = {r.id: cells(r) for r in prog.storage if r.id >= 0 and r.kind in SPLITTABLE}
    hits, wrote = {}, bytearray(0x10000)
    for _p, rid, lo, hi, _a, w in refs(prog):
        hits.setdefault(rid, []).append((lo, hi))
        if not w:
            continue
        if rid not in own:
            wrote[lo : hi + 1] = b"\1" * (hi - lo + 1)
        for a in own.get(rid, ()):
            if lo <= a <= hi:
                wrote[a] = 1
    reached = {}
    for rid, c in own.items():
        got = set()
        for lo, hi in _runs(hits.get(rid, ())):
            got |= {a for a in c if lo <= a <= hi}
        if got:
            reached[rid] = got
    return reached, wrote


def reach_bytes(prog):
    """Every byte of storage the program's accessors reach: the tune's own data."""
    reached, _w = spans(prog)
    return set().union(*reached.values()) if reached else set()


class Block:
    """One run of storage: the regions over it, what they reach, what of it is data."""

    def __init__(self, members, reached, wrote):
        self.members = sorted(members, key=lambda r: (r.base, r.id))
        self.reach = set().union(*(reached[r.id] for r in members))
        self.data = {a for a in self.reach if not wrote[a]}
        self.base = min(r.base for r in self.members)
        self.written = len(self.reach) - len(self.data)


def blocks(prog, names, reached, wrote):
    """Regions whose extents overlap, or that one frequency layout names, as blocks.

    A region no byte of which is data is state, which the state section already
    lists: it names no block and joins none.
    """
    reached = {i: c for i, c in reached.items() if any(not wrote[a] for a in c)}
    rs = sorted((r for r in prog.storage if r.id in reached), key=lambda r: (r.base, r.id))
    groups, end = [], -1
    for r in rs:
        if not groups or r.base > end:
            groups.append([])
        groups[-1].append(r)
        end = max(end, r.base + r.size - 1)
    at = {r.id: i for i, g in enumerate(groups) for r in g}
    join = {}
    for key in names.freq:  # a lo|hi table is two adjacent regions, so two runs
        own = sorted({at[i] for i in key if i in at})
        join.update({i: own[0] for i in own[1:]})
    out = {}
    for i, g in enumerate(groups):
        out.setdefault(join.get(i, i), []).extend(g)
    return [Block(m, reached, wrote) for _i, m in sorted(out.items())]


def _layout(names, blk):
    """``(kind, argument)``: the shape the recovered view already knows for a block."""
    ids = {r.id for r in blk.members}
    for key, lay in sorted(names.freq.items()):
        if set(key) <= ids:
            return "u16", (key, lay)
    strides = {r.stride for r in blk.members}
    if len(strides) == 1 and strides != {1}:
        return "record", strides.pop()
    split = names.split.get(blk.members[0].id) if len(blk.members) == 1 else None
    if split is not None and not split[3]:
        return "record", split[1]
    return "hex", None


def _fields(names, blk, k):
    """``{column offset: its field name}`` over the record a block's stride marks off."""
    out = {}
    for m in blk.members:
        view, split = names.view.get(m.id), names.split.get(m.id)
        for a in sorted(cells(m)):
            o = (a - blk.base) % k
            want = split[2].get(o) if split else (view[1] if view else names.region.get(m.id))
            out.setdefault(o, want or "+%d" % o)
    return out


def _name(names, blk, kind, arg):
    """A block's name: the record view's group, or the region the block starts at."""
    r = blk.members[0]
    view = names.view.get(r.id) or names.split.get(r.id)
    same = view and all(names.view.get(m.id, view)[0] == view[0] for m in blk.members)
    if kind == "record" and same:
        return "%s[%d]" % (view[0], len({(a - blk.base) // arg for a in blk.data}))
    return names.region.get(r.id, r.name)


def _head(names, blk, kind, arg):
    """A block's one header row: name, extent, role, note, and the regions over it."""
    notes = [names.notes[m.id] for m in blk.members if names.notes.get(m.id)]
    seen = blk.members[1:] if kind != "record" else []  # a record's columns are its header
    other = ["%s $%04X" % (names.region.get(m.id, m.name), m.base) for m in seen]
    return "%-16s $%04X %-18s %-10s %s" % (
        _name(names, blk, kind, arg),
        blk.base,
        "%d bytes%s" % (len(blk.reach), " stride %d" % arg if kind == "record" else ""),
        names.role.get(blk.members[0].id, ""),
        "; ".join(notes + (["also " + ", ".join(other)] if other else [])),
    )


def _accessors(sites, blk):
    """One line per distinct printed accessor of the block, with the procedures."""
    seen, ids = {}, {r.id for r in blk.members}
    for (rid, text), (kinds, procs) in sites.items():
        if rid in ids:
            k, p = seen.setdefault(text, (set(), set()))
            k |= kinds
            p |= procs
    return [
        "  %-30s %s in %s" % (t, "/".join(sorted(k)), ", ".join(sorted(p)))
        for t, (k, p) in sorted(seen.items())
    ]


def _hexrows(img, addrs):
    """Maximal runs of ``addrs``, as rows of at most :data:`ROW` bytes."""
    out = []
    for lo, hi in _runs([(a, a) for a in addrs]):
        for s in range(lo, hi + 1, ROW):
            e = min(s + ROW, hi + 1)
            out.append("  $%04X  %s" % (s, " ".join("%02X" % img[a] for a in range(s, e))))
    return out


def _entries(key, rgn, lay):
    """``[(low address, high address)]`` of a note table's entries, in entry order."""
    kind, n, _cut = lay
    addrs = [a for i in key for a in range(rgn[i].base, rgn[i].base + rgn[i].size)]
    if kind == "u16le":
        return [(addrs[2 * j], addrs[2 * j + 1]) for j in range(n)]
    lo, hi = (addrs[:n], addrs[n : 2 * n]) if kind == "lo|hi" else (addrs[n : 2 * n], addrs[:n])
    return list(zip(lo, hi))


def _u16rows(rgn, img, blk, key, lay):
    """A note table as 16-bit entries, :data:`ROW` bytes to the row; the rest as hex."""
    out, left, row, at = [], set(blk.data), [], None
    for lo, hi in _entries(key, rgn, lay):
        left -= {lo, hi}
        at = lo if at is None else at
        both = lo in blk.data and hi in blk.data
        row.append("%04X" % (img[lo] | img[hi] << 8) if both else "----")
        if len(row) == ROW // 2:
            out.append("  $%04X  %s" % (at, " ".join(row)))
            row, at = [], None
    if row:
        out.append("  $%04X  %s" % (at, " ".join(row)))
    return out + _hexrows(img, sorted(left))


def _recordrows(names, img, blk, k):
    """One row per record, the fields as the column header and ``--`` for a written cell."""
    cols = _fields(names, blk, k)
    offs = sorted(cols)
    wide = [max(2, len(cols[o])) for o in offs]
    out, hit = [], set()
    js = sorted({(a - blk.base) // k for a in blk.data})
    pad = len(str(js[-1])) if js else 1
    for j in js:
        addrs = [blk.base + j * k + o for o in offs]
        hit |= {a for a in addrs if a in blk.data}
        cs = ("%02X" % img[a] if a in blk.data else "--" for a in addrs)
        out.append("  [%*d] %s" % (pad, j, " ".join(c.rjust(w) for c, w in zip(cs, wide))))
    head = "  %*s %s" % (pad + 2, "entry", " ".join(cols[o].rjust(w) for o, w in zip(offs, wide)))
    return ([head] + out if out else []) + _hexrows(img, sorted(blk.data - hit))


def rows(prog, names, img, blk, kind, arg):
    """A block's own bytes, in the layout its view knows."""
    if kind == "u16":
        return _u16rows(prog.by_id(), img, blk, *arg)
    if kind == "record":
        return _recordrows(names, img, blk, arg)
    return _hexrows(img, sorted(blk.data))


def section(prog, names, sites):
    """The ``## data`` section: every run of storage the program reads, with its bytes."""
    reached, wrote = spans(prog)
    img, out = prog.image(), []
    for blk in blocks(prog, names, reached, wrote):
        kind, arg = _layout(names, blk)
        out.append(_head(names, blk, kind, arg))
        out += _accessors(sites, blk) + rows(prog, names, img, blk, kind, arg)
        if blk.written:
            out.append("  %d bytes of it the program writes, not data" % blk.written)
    return out
