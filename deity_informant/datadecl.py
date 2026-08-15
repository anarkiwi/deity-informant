"""Typed song-data declarations and state-role aliases (the data/symbols sections).

``declarations(model)`` carves disjoint byte-carried data regions out of the
post-init image, plus state-cell role aliases. Extents are honest: a proven index
domain sizes exactly, else observation floors and the sound ceiling bounds."""

from __future__ import annotations

import bisect

from . import expr as E
from . import streams as ST
from .lifter import MODE_LEN, OPS

_LOW = 0x200  # zero page and stack always stay in image {}
_LANES = 64  # lane-word candidates one pair's own definitions may name


def _code_bytes(model):
    """Sorted executed-instruction byte addresses (never carved as data)."""
    out = set()
    for pc, ops in getattr(model, "pcs", {}).items():
        for op in ops:
            out.update((pc + i) & 0xFFFF for i in range(MODE_LEN[OPS[op][1]]))
    return sorted(out)


def _read_pcs(model):
    """Read-site pc attribution: ``("t", base)`` static indexed reads,
    ``("p", zp)`` pointer derefs, ``("o", cell)`` SMC-operand instructions."""
    out = {}
    mem0 = model.mem0
    written = model.written
    for pc, ops in getattr(model, "pcs", {}).items():
        for op in ops:
            mode = OPS[op][1]
            if MODE_LEN[mode] == 3:
                out.setdefault(("o", (pc + 1) & 0xFFFF), set()).add(pc)
            if mode in ("absx", "absy"):
                if (pc + 1) & 0xFFFF in written or (pc + 2) & 0xFFFF in written:
                    continue
                base = mem0[(pc + 1) & 0xFFFF] | (mem0[(pc + 2) & 0xFFFF] << 8)
                out.setdefault(("t", base), set()).add(pc)
            elif mode in ("indy", "indx"):
                if (pc + 1) & 0xFFFF not in written:
                    out.setdefault(("p", mem0[(pc + 1) & 0xFFFF]), set()).add(pc)
    return out


def _idx_sites(model, f):
    """``base -> [idx expr]`` over every idx-shaped image read in the model."""
    out = {}

    def add(sp):
        if sp[0] == "idx" and sp[1] >= _LOW and not 0xD000 <= sp[1] <= 0xDFFF:
            out.setdefault(sp[1], []).append(sp[2])

    def walk(n):
        if n[0] == "mem":
            add(ST._split(n[1]))
            walk(n[1])
        elif n[0] == "op":
            for c in n[2]:
                walk(c)

    for _key, ax, sp in f.loads:
        add(sp)
        walk(ax)
    for stores, flags, dyns in f.by_key.values():
        for ax, vx, _sp in stores:
            walk(ax)
            walk(vx)
        for x in flags:
            walk(x)
        for x in dyns:
            walk(x)
    for regs in f.regs.values():
        for i, r in enumerate(regs):
            if r != E.reg(i):
                walk(r)
    return out


def _shift(idx):
    """Total constant left-shift of an index expression (record-stride log2)."""
    n = ST._strip_zext(idx)
    s = 0
    while n[0] == "op" and n[1] == "INT_LEFT" and E.is_const(n[2][1]):
        s += n[2][1][1]
        n = ST._strip_zext(n[2][0])
    return s


def _bound(idx):
    """``(hi, proven)``: sound index bound; proven iff below the width mask."""
    hi = ST._idx_hi(idx)
    return hi, hi < E.mask(E.width(ST._strip_zext(idx)))


def _groups(sites):
    """Cluster table bases into record regions at stride distance, absorbing
    byte fields just before a record start and contiguous equal-stride runs."""
    stride = {b: max((1 << _shift(ix) for ix in sites[b]), default=1) for b in sites}
    raw = []
    for b in sorted(sites):
        if raw and b < raw[-1]["base"] + raw[-1]["stride"]:
            raw[-1]["fields"].append(b)
            raw[-1]["stride"] = max(raw[-1]["stride"], stride[b])
        else:
            raw.append({"base": b, "fields": [b], "stride": stride[b]})
    out = []
    for g in raw:
        while (
            out
            and g["stride"] > 1
            and out[-1]["stride"] == 1
            and g["base"] - out[-1]["base"] < g["stride"]
        ):
            p = out.pop()  # byte field(s) immediately before the record start
            g["fields"] = p["fields"] + g["fields"]
            g["base"] = p["base"]
        if (
            out
            and out[-1]["stride"] == g["stride"] > 1
            and g["base"] == out[-1]["base"] + g["stride"]
        ):
            out[-1]["fields"] += g["fields"]  # interleaved continuation of one array
        else:
            out.append(g)
    return out


def _next_bound(bounds, code, start):
    """First boundary strictly above ``start`` (region starts, code, top)."""
    lim = 0x10000
    j = bisect.bisect_right(bounds, start)
    if j < len(bounds):
        lim = min(lim, bounds[j])
    j = bisect.bisect_right(code, start)
    if j < len(code):
        lim = min(lim, code[j])
    return lim


def _obs_hi(by_pc, pcs, lo, hi):
    """Highest read address in [lo, hi) observed at the given site pcs."""
    best = -1
    for pc in pcs:
        for a in by_pc.get(pc, ()):
            if lo <= a < hi and a > best:
                best = a
    return best


def _run_reads(g, rd_pcs, by_pc):
    """``(addresses observed, cells one access read contiguously from the base)``.

    A contiguous run is a traversal of the datum the base names, so the declaration
    holds every cell of it; a sparse read map is an index leaving its declaration and
    evidences no extent (a byte index spans 256 cells it never touches)."""
    pcs = set()
    for b in g["fields"]:
        pcs |= rd_pcs.get(("t", b), set())
    seen = [set(by_pc.get(pc, ())) for pc in pcs]
    run = 0
    for s in seen:
        n = 0
        while g["base"] + n in s:
            n += 1
        run = max(run, n)
    return sorted(set().union(*seen, set())), run


def _alias(p, g):
    """True if ``g`` is ``p``'s region read at a shifted base (``tab,x``/``tab+k,x``).

    Same index domain (parallel observed runs) and the runs overlap in more than
    half their extent, so ``g`` is a field of ``p`` rather than the next block."""
    d = g["base"] - p["base"]
    return 0 < 2 * d < p["top"] - p["base"] and g["top"] - p["top"] == d


def _covered(p, g):
    """True where ``p``'s traversal reads every cell ``g`` was seen to name.

    A base the run passes over is a field of the datum traversed; one whose own reads
    leave the run is the next datum, and absorbing it would declare it short."""
    return p["base"] <= g["base"] <= g["top"] < p["base"] + p["run"]


def _witnessed(g, sites):
    """A read was observed in the group, or its index domain is proven.

    An unwitnessed base declares nothing, so it must not bound its neighbour."""
    if g["top"] >= g["base"]:
        return True
    return all(_bound(ix)[1] for b in g["fields"] for ix in sites[b])


def _regions(groups, sites, rd_pcs, by_pc, code):
    """Witnessed regions off the code image; alias and interior bases absorbed.

    A base on an executed instruction byte would carve code into ``data { }``, and
    the next code byte bounds a region anyway; a base whose whole read map lies
    inside a neighbour's traversed run is a field of it rather than the next block."""
    out = []
    oncode = set(code)
    for g in groups:
        if g["base"] in oncode:
            continue
        reads, run = _run_reads(g, rd_pcs, by_pc)
        g = dict(g, reads=reads, top=reads[-1] if reads else -1, run=run)
        if not _witnessed(g, sites):
            continue
        alias = bool(out) and _alias(out[-1], g)
        inside = bool(out) and not alias and _covered(out[-1], g)
        if (alias or inside) and _next_bound((), code, out[-1]["base"]) > g["base"]:
            p = out[-1]
            p["fields"] = p["fields"] + g["fields"]
            p["stride"] = max(p["stride"], g["stride"])
            p["reads"] = sorted(set(p["reads"]) | set(g["reads"]))
            p["top"] = max(p["top"], g["top"])
        else:
            out.append(g)
    return out


def _sound_hi(base, ceil, mut):
    """First play-written cell in [base, ceil), else ceil: snapshot soundness.

    ``mem0`` holds a written cell's pre-play value only, so a const declaration
    must stop there."""
    return next((a for a in range(base, ceil) if a in mut), ceil)


def _record(size, stride):
    """Record length a declaration's ``mut`` offsets are taken modulo."""
    return stride if stride > 1 else size or 1


def _mut_offs(base, size, stride, mut):
    """Play-written offsets in a region's record: a lane if strided, else the cell.

    Snapshot soundness is per record offset, so a stride-``s`` block keeps the lanes
    the play phase never writes; a flat region is one record, so an offset is a
    cell. The const claim is the region minus these offsets."""
    rec = _record(size, stride)
    return sorted({(a - base) % rec for a in range(base, base + size) if a in mut})


def _extent(g, sites, bounds, code, mut=frozenset(), pairtabs=frozenset()):
    """``(size, mutable offsets, observed)``; size 0 when nothing is known.

    An observed run only *floors* the extent, so the region runs on to its ceiling
    (index cap, next boundary, first written cell above the run); a pointer reload
    table stops at the floor. ``_mut_offs`` carries the writes inside the run."""
    base = g["base"]
    lim = _next_bound(bounds, code, base)
    cap = 0
    proven = True
    for b in g["fields"]:
        for ix in sites[b]:
            hi, ok = _bound(ix)
            cap = max(cap, b - base + hi)
            proven = proven and ok
    if proven and base + cap < lim:
        return cap + 1, _mut_offs(base, cap + 1, g["stride"], mut), False
    ceil = min(base + cap + 1, lim)
    j = bisect.bisect_left(g["reads"], ceil)
    if j == 0 or g["reads"][j - 1] < base:
        return 0, [], True
    floor = g["reads"][j - 1] + 1
    size = (floor if base in pairtabs else _sound_hi(floor, ceil, mut)) - base
    return size, _mut_offs(base, size, g["stride"], mut), True


def _co_indexed(lrows, hrows):
    """``[(lo table, hi table)]`` for the columns one index reads together.

    The lo/hi partnership is a *co-index* claim -- two columns of one datum are read
    at one row or at no row at all -- so it is asserted off the index expression the
    two reload reads share, never off the order the two base addresses sort in."""
    his = {}
    for base, idx in hrows:
        his.setdefault(repr(idx), []).append(base)
    out, taken = [], set()
    for base, idx in lrows:
        for other in sorted(his.get(repr(idx), ())):
            if other not in taken and other != base:
                out.append((base, other))
                taken.add(other)
                break
    return out


def _lane_words(lo, hi):
    """The words a pair's own lane definitions name: a reset value, or its floor.

    A row the play code computes is in no reload table, so ``via:`` reads it off the
    lanes themselves -- the constant a lane is reset to, or the bits an ``INT_OR``
    row must set (``expr.floor``), which is the block the walk starts in."""
    sides = [set(r.get("resets", ())) | set(r.get("reset_floors", ())) for r in (lo, hi)]
    if not sides[0] or not sides[1] or len(sides[0]) * len(sides[1]) > _LANES:
        return ()
    return sorted({(h << 8) | l for l in sides[0] for h in sides[1]})


def _pair_recs(cls):
    """``[(lo, hi, co-indexed row pairs, lane words)]`` from lo-role pointer records."""
    out = []
    for cell in sorted(cls):
        rec = cls[cell]
        if rec["class"] == "pointer" and rec.get("role") == "lo":
            hi = rec["pair"][1]
            hrec = cls.get(hi, {})
            rows = _co_indexed(rec.get("reload_rows", []), hrec.get("reload_rows", []))
            out.append((cell, hi, rows, _lane_words(rec, hrec)))
    return out


def _pair_streams(strs):
    """``(lo, hi) -> {pcs, cmp, dispatch}`` merged over pointer deref streams."""
    out = {}
    for rec in strs:
        if rec["kind"] != "pointer" or len(rec["pair_cells"]) != 2:
            continue
        key = tuple(rec["pair_cells"])
        agg = out.setdefault(key, {"cmp": set(), "dispatch": set()})
        agg["cmp"].update(rec["consumers"]["compare"])
        agg["dispatch"].update(rec["consumers"]["dispatch"])
    return out


def decl_pairs(decls):
    """``{lo base: (hi base, size)}`` off the declared roles, the ONE pair registry."""
    return {
        d["base"]: (d["role"][1], d["size"])
        for d in decls
        if d.get("role") and d["role"][0] == "lo"
    }


class Regions:
    """The declarations indexed by containment: which declared datum holds a byte.

    Declarations are disjoint and base-sorted, so one bisect answers it. ``const_at``
    adds the #61 const claim, which excludes the record offsets ``mut`` names."""

    __slots__ = ("decls", "bases", "recs")

    def __init__(self, decls):
        self.decls = sorted(decls, key=lambda d: d["base"])
        self.bases = [d["base"] for d in self.decls]
        self.recs = [
            (_record(d["size"], max(1, d.get("stride") or 1)), frozenset(d.get("mut") or ()))
            for d in self.decls
        ]

    def _index(self, addr):
        j = bisect.bisect_right(self.bases, addr) - 1
        if j < 0 or addr >= self.bases[j] + self.decls[j]["size"]:
            return None
        return j

    def at(self, addr):
        """``(declaration, offset)`` of the region containing ``addr``, else None."""
        j = self._index(addr)
        return None if j is None else (self.decls[j], addr - self.bases[j])

    def const_at(self, addr):
        """True where ``addr`` is a declared byte at an offset ``mut`` does not name."""
        j = self._index(addr)
        if j is None:
            return False
        rec, mut = self.recs[j]
        return (addr - self.bases[j]) % rec not in mut

    def avail(self, addr):
        """Bytes from ``addr`` to the end of the region containing it, else 0."""
        j = self._index(addr)
        return 0 if j is None else self.bases[j] + self.decls[j]["size"] - addr


def _anchors(model, pairs, tables):
    """``anchor -> (lo, hi)``: pair initial words + reload-table entry words."""
    mem0 = model.mem0
    regions = Regions(tables.values())
    out = {}
    for lo, hi, rows, lanes in pairs:
        words = [mem0[lo] | (mem0[hi] << 8), *lanes]
        for lt, ht in rows:
            n = min(regions.avail(lt), regions.avail(ht))
            words += [mem0[lt + i] | (mem0[ht + i] << 8) for i in range(n)]
        for w in words:
            if w >= _LOW:
                out.setdefault(w, (lo, hi))
    return out


def _table_decls(sites, groups, bounds, code, mut=frozenset(), pairtabs=frozenset()):
    """Base-keyed table declarations with extents against the given bounds."""
    out = {}
    for g in groups:
        size, moffs, observed = _extent(g, sites, bounds, code, mut, pairtabs)
        if size <= 0 or g["base"] + size > 0x10000:
            continue
        out[g["base"]] = {
            "kind": "table",
            "base": g["base"],
            "size": size,
            "stride": g["stride"],
            "mut": moffs,
            "cobases": [b for b in g["fields"] if b != g["base"]],
            "role": None,
            "via": None,
            "targets": None,
            "cmp": [],
            "dispatch": [],
            "observed": observed,
        }
    return out


def _aliases(cls):
    """``cell -> role alias``; deterministic and collision-free by construction."""
    pos = set()
    for rec in cls.values():
        if rec["class"] == "pointer":
            pos.update(rec.get("position_cells", ()))
    out = {}
    used = set()
    for cell in sorted(cls):
        rec = cls[cell]
        if 0x100 <= cell < _LOW or cell >= 0xD000:
            continue
        kind = rec["class"]
        if kind == "pointer":
            name = "ptr_%04X_%s" % (rec["pair"][0], rec["role"])
            if name in used:
                name = "ptr_%04X_%s" % (cell, rec["role"])
        elif kind == "counter":
            name = "%s_%04X" % ("pos" if cell in pos else "ctr", cell)
        elif kind == "index":
            name = "idx_%04X" % cell
        else:
            continue
        used.add(name)
        out[cell] = name
    return out


def declarations(model):
    """``(decls, aliases)``: mutually disjoint data-region declarations carved
    from the image (bytes attached) and the state-cell alias table.

    A region is declared for its extent; ``mut`` alone carries constness, so a
    wholly play-written array declares its size with an empty const claim."""
    if not getattr(model, "blocks", None):
        return [], {}
    f = ST._facts(model)
    cls = ST.classify(model)
    sites = _idx_sites(model, f)
    code = _code_bytes(model)
    codeset = set(code)
    rd_pcs = _read_pcs(model)
    by_pc = {}
    for pc, a in getattr(model, "reads", ()):
        by_pc.setdefault(pc, []).append(a)
    groups = _regions(_groups(sites), sites, rd_pcs, by_pc, code)
    starts = [g["base"] for g in groups]
    pairs = _pair_recs(cls)
    mut = model.written
    pairtabs = {t for _l, _h, rows, _w in pairs for r in rows for t in r}
    tables = _table_decls(sites, groups, sorted(starts), code, mut, pairtabs)
    anchors = _anchors(model, pairs, tables)  # round 2: anchor-bounded extents
    bounds = sorted(set(starts) | set(anchors))
    tables = _table_decls(sites, groups, bounds, code, mut, pairtabs)
    anchors = _anchors(model, pairs, tables)
    bounds = sorted(set(starts) | set(anchors))
    for _lo, _hi, rows, _w in pairs:
        for lt, ht in rows:
            dl, dh = tables.get(lt), tables.get(ht)
            if dl is None or dh is None:
                continue
            dl["role"], dh["role"] = ("lo", ht), ("hi", lt)
            n = dl["size"] = dh["size"] = min(dl["size"], dh["size"])  # a pair is co-extensive
            dl["mut"] = _mut_offs(lt, n, dl["stride"], mut)
            dh["mut"] = _mut_offs(ht, n, dh["stride"], mut)
            words = [model.mem0[lt + i] | (model.mem0[ht + i] << 8) for i in range(n)]
            if words:
                dl["targets"] = dh["targets"] = (min(words), max(words))
    decls = sorted(tables.values(), key=lambda d: d["base"])
    spans = [(d["base"], d["base"] + d["size"]) for d in decls]
    pstr = _pair_streams(ST.streams(model))
    for a in sorted(anchors):
        pair = anchors[a]
        agg = pstr.get(pair)
        if agg is None or a in codeset or any(s <= a < e for s, e in spans):
            continue
        pcs = rd_pcs.get(("p", pair[0]), set()) | rd_pcs.get(("o", pair[0]), set())
        top = _obs_hi(by_pc, pcs, a, _next_bound(bounds, code, a))
        if top < a:
            continue
        decls.append(
            {
                "kind": "stream",
                "base": a,
                "size": top - a + 1,
                "stride": 1,
                "mut": _mut_offs(a, top - a + 1, 1, mut),
                "cobases": [],
                "role": None,
                "via": pair[0],
                "targets": None,
                "cmp": sorted(agg["cmp"]),
                "dispatch": sorted(agg["dispatch"]),
                "observed": True,
            }
        )
    decls.sort(key=lambda d: d["base"])
    end = 0
    for d in decls:
        assert end <= d["base"] and d["base"] + d["size"] <= 0x10000
        end = d["base"] + d["size"]
        d["data"] = bytes(model.mem0[d["base"] : end])
    return decls, _aliases(cls)
