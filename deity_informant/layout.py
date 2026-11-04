"""Memory layout recovery: words, pointers, address tables and arrays (``docs/layout.md``).

Carry chains, pointer arithmetic and effective-address evidence are merged by union-find over
regions with offsets, as in Aggregate Structure Identification (Ramalingam, Field and Tip,
POPL 1999) and DIVINE (Balakrishnan and Reps, VMCAI 2007).
"""

from __future__ import annotations

from collections import defaultdict
from math import gcd

from .trace import EA, Facts

__all__ = ["UnionFind", "address_bytes", "build", "elements", "pointer_uses", "table_entries"]

_REG = {"zpx": "x", "absx": "x", "indx": "x", "zpy": "y", "absy": "y", "indy": "y"}


class UnionFind:
    """Union-find with offsets: ``find(v)`` is ``(root, d)`` with ``v`` at offset ``d`` of the
    root's region; ``union(a, b, d)`` places ``b`` at offset ``d`` of ``a``."""

    def __init__(self):
        self.parent = {}

    def find(self, v):
        p, d = self.parent.get(v, (v, 0))
        if p == v:
            return v, 0
        r, e = self.find(p)
        self.parent[v] = (r, d + e)
        return r, d + e

    def union(self, a, b, d=0):
        """Merge; False when ``b`` is already in ``a``'s region at another offset."""
        (ra, da), (rb, db) = self.find(a), self.find(b)
        if ra == rb:
            return db - da == d
        if da + d - db >= 0:
            self.parent[rb] = (ra, da + d - db)
        else:
            self.parent[ra] = (rb, db - da - d)
        return True

    def groups(self, items):
        """``{root: {item: offset}}`` over ``items``."""
        out = defaultdict(dict)
        for v in items:
            r, d = self.find(v)
            out[r][v] = d
        return dict(out)


class _Values:
    """Hash-consed expressions: an id per distinct expression tuple."""

    def __init__(self):
        self.ids, self.nodes = {}, []

    def __call__(self, t):
        i = self.ids.get(t)
        if i is None:
            i = self.ids[t] = len(self.nodes)
            self.nodes.append(t)
        return i

    def const(self, i):
        t = self.nodes[i]
        return t[1] if t[0] == "c" else None

    def ptr16(self, i):
        """``(lo, hi)`` byte ids of ``OR(LEFT(ZEXT(hi), 8), ZEXT(lo))``, or None."""
        t = self.nodes[i]
        if t[0] != "INT_OR":
            return None
        for a, b in ((t[1], t[2]), (t[2], t[1])):
            ta, tb = self.nodes[a], self.nodes[b]
            if ta[0] == "INT_LEFT" and self.const(ta[2]) == 8 and tb[0] == "INT_ZEXT":
                th = self.nodes[ta[1]]
                if th[0] == "INT_ZEXT":
                    return tb[1], th[1]
        return None

    def word_bytes(self, i):
        """``(lo, hi)`` byte-load ids of the bytes a 2-byte ``LOAD`` reads, where both were
        loaded as bytes, or None."""
        t = self.nodes[i]
        if t[0] != "LOAD" or t[2] != 2:
            return None
        c, rest = self.terms(t[1])
        if len(rest) > 1:
            return None
        k = self.ids.get(("c", (c + 1) & 0xFFFF))
        if rest:
            hi = [self.ids.get(("INT_ADD", *o, 2)) for o in ((rest[0], k), (k, rest[0]))]
            hi = next((h for h in hi if h is not None), None)
        else:
            hi = k
        lo, hl = self.ids.get(("LOAD", t[1], 1)), self.ids.get(("LOAD", hi, 1))
        return (lo, hl) if None not in (k, hi, lo, hl) else None

    def terms(self, i):
        """``(constant, [other terms])`` of a sum."""
        t = self.nodes[i]
        if t[0] == "c":
            return t[1], []
        if t[0] != "INT_ADD":
            return 0, [i]
        c1, a = self.terms(t[1])
        c2, b = self.terms(t[2])
        return c1 + c2, a + b


def _next(p):
    return (p + 1) & 0xFF if p < 0x100 else (p + 1) & 0xFFFF


def _block_pointers(vals, uniq, stored, blk, found):
    """Scan one block's ops (see :func:`pointer_uses`): unique varnodes are the function's,
    registers the block's (from a symbol per block entry); ``stored`` maps a value to the
    ``(address, size)`` stores of it."""
    env, fed, pc = {}, {}, blk["pc"]
    pointers, feeds = found

    def val(vn):
        sp, off, _ = vn
        if sp == "c":
            return vals(("c", off))
        if sp == "u":
            return uniq.get(off, vals(("u", off)))
        return env.get((sp, off), vals((sp, off, pc)))

    def at(i):
        t = vals.nodes[i]
        own = {(vals.const(t[1]), t[2])} if t[0] == "LOAD" else set()
        return own | stored[i]

    def address(i):
        for t in vals.terms(i)[1]:
            for p in (p for p, n in at(t) if n == 2 and p is not None):
                pointers[p].add(pc)
            pair = vals.ptr16(t) or vals.word_bytes(t)
            for p, n in at(pair[0]) if pair else ():
                if n == 1 and p is not None and (_next(p), 1) in at(pair[1]):
                    pointers[p].add(pc)

    for mn, out, ins, *_ in blk["ops"]:
        args = [None if v is None else val(v) for v in ins]
        if mn in ("LOAD", "STORE", "BRANCHIND"):
            address(args[0])
        if mn == "STORE":
            p, src = vals.const(args[0]), vals.nodes[args[1]]
            if p is not None:
                stored[args[1]].add((p, ins[1][2]))
                c, terms = vals.terms(src[1]) if src[0] == "LOAD" and src[2] == 1 else (0, ())
                if len(terms) == 1:
                    fed[p] = (c, terms[0])
        if out is not None:
            e = args[0] if mn == "COPY" else vals((mn, *args, out[2]))
            (uniq if out[0] == "u" else env)[out[1] if out[0] == "u" else (out[0], out[1])] = e
    for p, (lo, idx) in fed.items():
        q = fed.get(_next(p))
        if q is not None and q[1] == idx:
            feeds[(p, lo, q[0])].add(pc)


def pointer_uses(prog):
    """``(pointers, feeds)`` of a lifted program's ops: ``{p: {block pcs}}`` for byte pairs
    ``(p, p+1)`` an access or indirect branch uses as an address, and ``{(p, lo, hi): {block
    pcs}}`` for pairs a block stores from ``LOAD lo+I`` and ``LOAD hi+I`` with the same ``I``."""
    pointers, feeds = defaultdict(set), defaultdict(set)
    for fn in prog.functions:
        vals, uniq, stored = _Values(), {}, defaultdict(set)
        for blk in fn["blocks"]:
            _block_pointers(vals, uniq, stored, blk, (pointers, feeds))
    return dict(pointers), dict(feeds)


def _hx(v):
    return "%04x" % v


def _indexed(ea):
    """``[(base, reg, index, width, evidence)]`` of indexed and ``(zp,X)`` accesses, and
    ``{p: [targets, Y index, evidence]}`` of ``(zp),Y`` pointers (intervals as ``Strided``)."""
    pats, ptrs = [], {}
    for pc, ops in ea.items():
        for r in ops.values():
            md = r["mode"]
            if md not in _REG:
                continue
            idx, base, ev = EA.strided(r, "index"), EA.strided(r, "base"), "ea@" + _hx(pc)
            for b in base.values() or ():
                if md != "indy":
                    pats.append((b, _REG[md], idx, 2 if md == "indx" else 1, ev))
                    continue
                ptr = EA.strided(r, "ptr")
                if b in ptrs:
                    t = ptrs[b]
                    ptrs[b] = [t[0].join(ptr), t[1].join(idx), t[2] | {ev}]
                else:
                    ptrs[b] = [ptr, idx, {ev}]
    return pats, ptrs


def _region(kind, base, **kw):
    return {"kind": kind, "base": base} | {k: v for k, v in kw.items() if v is not None}


def _spans(spans):
    """Maximal ``(offset, width)`` spans, contained ones absorbed; partial overlaps (merged
    to their hull) are returned as conflicts."""
    out, bad = [], []
    for a, w in sorted(set(spans), key=lambda t: (t[0], -t[1])):
        if out and a < sum(out[-1]):
            pa, pw = out[-1]
            if a + w > pa + pw:
                bad.append([[pa, pw], [a, w]])
                out[-1] = (pa, a + w - pa)
            continue
        out.append((a, w))
    return out, bad


def _fields(spans):
    return [{"offset": o, "width": w} for o, w in spans]


def _aggregate(nodes, info, conflicts):
    """The record or array region of union-find ``nodes`` ``(base, stride)``: the strides'
    gcd as its stride, fields modulo it, elements from each node's index values."""
    s, m = gcd(*(t for _, t in nodes)), min(b for b, _ in nodes)
    fields, bad = _spans(((b - m) % s, w) for v in nodes for b in v[:1] for w in info[v]["w"])
    lo = min(b + info[b, t]["idx"].lo for b, t in nodes)
    hi = max(b + info[b, t]["idx"].hi + w - 1 for b, t in nodes for w in info[b, t]["w"])
    first = min((b - m + info[b, t]["idx"].lo) // s for b, t in nodes)
    last = max((b - m + info[b, t]["idx"].hi) // s for b, t in nodes)
    ev = sorted({e for v in nodes for e in info[v]["ev"]})
    conflicts += [{"kind": "fields", "base": m, "spans": c, "evidence": ev} for c in bad]
    return _region(
        "record" if len(fields) > 1 else "array",
        m,
        stride=s,
        count=last - first + 1,
        extent=[lo, hi],
        size=hi - lo + 1,
        fields=_fields(fields),
        index="".join(sorted({r for v in nodes for r in info[v]["reg"]})),
        evidence=ev,
    )


def _span(r):
    return r["extent"] if "extent" in r else [r["base"], r["base"] + r.get("size", 1) - 1]


def _arrays(pats, widths, words, io):
    """Aggregates of indexed accesses as ``(regions by root, parallel groups, conflicts, node
    info)``. Per index sequence ``(register, Strided)`` (memory and devices apart) bases closer
    than its span join one ``(base, stride)`` aggregate, farther ones are parallel; aggregates
    of one stride whose bytes overlap merge."""
    groups = defaultdict(set)
    info = defaultdict(lambda: {"idx": None, "w": set(), "ev": set(), "reg": set()})
    for b, reg, idx, w, ev in pats:
        if idx.stride:
            groups[reg, idx, io(b)].add(b)
            n = info[b, idx.stride]
            n["idx"] = idx if n["idx"] is None else n["idx"].join(idx)
            n["w"].add(max(w, widths.get(b, 1)))
            n["ev"].add(ev)
            n["reg"].add(reg)
    uf, par = UnionFind(), []
    for (_, idx, _), bases in groups.items():
        s, bases = idx.stride, sorted(bases)
        for a, b in zip(bases, bases[1:]):
            if b - a < idx.points * s:
                uf.union((a, s), (b, s), b - a)
        par.append([(b, s) for b in bases])
    while True:
        bad = []
        regs = {r: _aggregate(sorted(ns), info, bad) for r, ns in uf.groups(info).items()}
        live, merged = [], False
        for r in sorted(regs, key=lambda r: regs[r]["extent"]):
            lo, sr = regs[r]["extent"][0], regs[r]["stride"]
            live = [q for q in live if regs[q]["extent"][1] >= lo]
            for q in live:
                sq = regs[q]["stride"]
                if io(q[0]) == io(r[0]) and (sr % sq == 0 or sq % sr == 0):
                    merged |= uf.union(q, r, r[0] - q[0])
            live.append(r)
        if not merged:
            break
    puf = UnionFind()
    for nodes in par:
        rs = sorted({uf.find(v)[0] for v in nodes})
        for a, b in zip(rs, rs[1:]):
            puf.union(a, b)
    groups = [sorted(g, key=lambda r: regs[r]["base"]) for g in puf.groups(regs).values()]
    return regs, [g for g in groups if len(g) > 1], bad, info


def _parallel(ms, words):
    """The parallel-array region of member regions ``ms`` (lowest base first)."""
    m = ms[0]["base"]
    off = {r["base"]: r["base"] - m for r in ms}
    ws = sorted({tuple(off[b] for b in wd) for wd in words if set(wd) <= off.keys()})
    return _region(
        "parallel_array",
        m,
        stride=ms[0]["stride"],
        count=max(r["count"] for r in ms),
        extent=[ms[0]["extent"][0], max(r["extent"][1] for r in ms)],
        fields=[{"offset": off[r["base"]], "width": r["fields"][0]["width"]} for r in ms],
        words=[list(w) for w in ws] or None,
        index=ms[0]["index"],
        evidence=sorted({e for r in ms for e in r["evidence"]}),
    )


def _tables(tables, regs, info, image, written):
    """Address-table regions of the split or interleaved ``tables`` ``{(lo, hi): (pointers,
    evidence)}``: the entries at the index values seen at the low half, the image's ``targets``
    when no entry byte is ``written``."""
    out = []
    for (lo, hi), (ps, ev) in sorted(tables.items()):
        b, d = min(lo, hi), abs(hi - lo)
        k = next((k for k, r in regs.items() if r["base"] == lo and d == 1), None)
        if k is not None and regs[k]["fields"] == [{"offset": 0, "width": 2}]:
            r = regs.pop(k)
        else:
            r = _region("address_table", b, fields=_fields([(0, 1), (d, 1)]), evidence=[])
        s = next((s for (a, s) in info if a == lo and (hi, s) in info), r.get("stride"))
        idx = info[lo, s]["idx"] if (lo, s) in info else None
        if idx is not None:
            r |= {"stride": s, "count": idx.points, "index": "".join(sorted(info[lo, s]["reg"]))}
            r["extent"] = [b + idx.lo, b + d + idx.hi]
            ent = [(lo + i, hi + i) for i in idx.values()]
            if written.isdisjoint(a for e in ent for a in e):
                r["targets"] = [image[a] | image[b] << 8 for a, b in ent]
            else:
                r["written"] = True
        r |= {"kind": "address_table", "pointers": sorted(ps), "words": [[lo - b, hi - b]]}
        r["evidence"] = sorted(set(r["evidence"]) | ev)
        out.append(r)
    return out


def _atoms(atoms):
    """Word and pointer regions of scalars ``(base, size, kind, evidence)``: atoms sharing a
    byte are one region; partially overlapping spans are conflicts."""
    uf = UnionFind()
    for a, n, _, _ in atoms:
        for k in range(1, n):
            uf.union(a, a + k, k)
    by = defaultdict(list)
    for t in atoms:
        by[uf.find(t[0])[0]].append(t)
    regions, conflicts = [], []
    for root, ts in sorted(by.items()):
        spans, bad = _spans((a - root, n) for a, n, _, _ in ts)
        ev = sorted({e for _, _, _, es in ts for e in es})
        conflicts += [{"kind": "atoms", "base": root, "spans": c, "evidence": ev} for c in bad]
        regions.append(
            _region(
                "pointer" if any(k == "pointer" for _, _, k, _ in ts) else "word",
                root,
                size=max(a + n for a, n in spans),
                fields=_fields(spans),
                evidence=ev,
            )
        )
    return regions, conflicts


def _overlaps(regions):
    """Conflicts between array and record regions of different strides sharing bytes."""
    arr = sorted(
        (r["extent"][0], r["extent"][1], r["stride"], i)
        for i, r in enumerate(regions)
        if r["kind"] in ("array", "record")
    )
    out, live = [], []
    for lo, hi, s, i in arr:
        live = [o for o in live if o[1] >= lo]
        out += [
            {"kind": "strides", "regions": [j, i], "strides": [s2, s]}
            for _, _, s2, j in live
            if s2 != s
        ]
        live.append((lo, hi, s, i))
    return out


def _chains(chains):
    """Scalar word atoms, indexed field widths ``{base: width}`` and parallel words (tuples of
    bases) of carry chains: operand bytes indexed by one register or one computed value."""
    atoms, widths, words = [], {}, set()
    for c in chains:
        for places in (c["x"], c["y"], c["r"]):
            if len(places) < 2 or None in places:
                continue
            keys, addrs = {(p[0], *p[2:]) for p in places}, [p[1] for p in places]
            run = addrs == list(range(addrs[0], addrs[0] + len(addrs)))
            if keys == {("abs",)} and run:
                atoms.append((addrs[0], len(addrs), "word", {"chain@" + _hx(c["pc"])}))
            elif len(keys) == 1 and ("abs",) not in keys:
                if run:
                    widths[addrs[0]] = max(widths.get(addrs[0], 1), len(addrs))
                else:
                    words.add(tuple(addrs))
    return atoms, widths, words


def build(prog, facts, image, volatile=()):
    """The layout map of ``prog`` lifted from ``image`` and the ``facts`` (with ``ea``
    evidence) of a run from it: ``{"regions", "conflicts"}``, JSON-serializable."""
    if not isinstance(facts, Facts):
        facts = Facts.from_dict(facts)
    atoms, widths, words = _chains(prog.chains)
    pats, ptrs = _indexed(facts.ea)
    pointers, feeds = pointer_uses(prog)
    atoms += [(p, 2, "pointer", {"ops@" + _hx(b) for b in bs}) for p, bs in pointers.items()]
    atoms += [(p, 2, "pointer", ev) for p, (_, _, ev) in ptrs.items()]
    regions, conflicts = _atoms(atoms)
    for r in regions:
        if r["kind"] == "pointer" and r["base"] in ptrs:
            t, y, _ = ptrs[r["base"]]
            vals = t.values()
            r |= {"targets": vals} if vals is not None else {"target_range": [t.lo, t.hi]}
            r["index"] = y.to_list()
    tables = defaultdict(lambda: (set(), set()))
    ptr = {r["base"] for r in regions if r["kind"] == "pointer"}
    for (p, lo, hi), blocks in sorted(feeds.items()):
        if p in ptr:
            tables[lo, hi][0].add(p)
            tables[lo, hi][1].update("ops@" + _hx(b) for b in blocks)
        if hi == lo + 1:
            widths[lo] = max(widths.get(lo, 1), 2)
        else:
            words.add((lo, hi))

    def io(a):
        return any(lo <= a < hi for lo, hi in volatile)

    regs, par, bad, info = _arrays(pats, widths, words, io)
    pairs = {tuple(sorted(t)) for t in tables}
    regions += _tables(tables, regs, info, image, set(facts.writes))
    for g in par:
        ms = [regs[r] for r in g if r in regs]
        if len(ms) > 1 and not (len(ms) == 2 and (ms[0]["base"], ms[1]["base"]) in pairs):
            regions.append(_parallel(ms, words))
    regions += sorted(regs.values(), key=lambda r: (r["base"], r["stride"]))
    conflicts += bad + _overlaps(regions)
    for r in regions:
        if io(_span(r)[0]):
            r["io"] = True
    return {"regions": regions, "conflicts": conflicts}


def table_entries(r):
    """``[(lo, hi)]`` byte addresses of an address table's entries at the index values its
    extent spans; empty without index evidence."""
    if "extent" not in r:
        return []
    (lo, hi), a, s = r["words"][0], r["extent"][0], r["stride"]
    return [(a + lo + k * s, a + hi + k * s) for k in range(r["count"])]


def elements(r):
    """Start addresses of a record's or array's elements (``count`` of them, ``stride`` apart,
    aligned to ``base``)."""
    b, s = r["base"], r["stride"]
    a = b + (r["extent"][0] - b) // s * s
    return [a + k * s for k in range(r["count"])]


def address_bytes(regions):
    """``{address: "lo"|"hi"}`` of the address bytes a map implies: table entries and the
    bytes of pointers."""
    out = {}
    for r in regions:
        pairs = table_entries(r) if r["kind"] == "address_table" else []
        pairs += [(r["base"], r["base"] + 1)] if r["kind"] == "pointer" else []
        for lo, hi in pairs:
            out[lo], out[hi] = "lo", "hi"
    return out
