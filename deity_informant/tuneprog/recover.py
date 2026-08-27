"""S6 -- presentation-level recovery: stride views, roles, names.

Reads the S4 IR only: loads flowing into a SID store are that register's image, a
load-modify-store by one is a counter or timer, a value that indexes a region is a
cursor, a zero-page pair used as an address is a pointer, equal strides one view.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from .facts import (
    Facts,
    elem_count,
    MAXPAIRS,
    MAXROLE,
    image_copy,
    sid_image,
    update_role,
    scales,
    voice_maps,
)
from .halves import register
from .ir import Bin, Const, Let, Load, R16, Store, Var
from .irwalk import forwarder, unique_name, walk
from .structure import phase as _phase

try:
    from pysidtracker.notefreq import is_octave_ramp
except ImportError:  # pragma: no cover - only without the optional survey package

    def is_octave_ramp(values, min_steps=6):
        """Fallback for :func:`pysidtracker.notefreq.is_octave_ramp`."""
        if not values or not 0 < values[0] <= 4 or values[-1] < 0x20:
            return False
        if any(b < a for a, b in zip(values, values[1:])):
            return False
        return sum(b > a for a, b in zip(values, values[1:])) >= min_steps


@dataclass
class Names:
    """The recovered presentation: a name and a role per region, plus struct views."""

    region: dict = field(default_factory=dict)
    role: dict = field(default_factory=dict)
    image: dict = field(default_factory=dict)
    scale: dict = field(default_factory=dict)
    view: dict = field(default_factory=dict)
    groups: dict = field(default_factory=dict)
    procs: dict = field(default_factory=dict)
    elem: dict = field(default_factory=dict)
    phase: tuple = None
    notes: dict = field(default_factory=dict)
    u16: dict = field(default_factory=dict)
    u16group: dict = field(default_factory=dict)
    slots: dict = field(default_factory=dict)
    column: dict = field(default_factory=dict)
    split: dict = field(default_factory=dict)
    voicemap: set = field(default_factory=set)
    freq: dict = field(default_factory=dict)
    sidwrite: tuple = None
    copies: dict = None
    index: list = field(default_factory=list)

    def of(self, rid):
        return self.region.get(rid, "r%d" % rid)

    def to_dict(self):
        """The serialised naming plane; :meth:`from_dict` is its inverse.

        ``index`` is the index relation, one plain record per cell and target:
        ``region``/``addr`` the index cell (``addr`` null when the whole region
        is the index), ``target`` the region it indexes, ``base`` how the
        address reaches that region -- ``const`` a constant, ``ptr`` a 16-bit
        pair (``pair`` its name, ``tables`` the regions the pair's low byte is
        loaded from, themselves ``target`` s of this relation where a cell
        indexes them), ``other`` neither.
        """
        return {
            "regions": [
                {
                    "id": k,
                    "name": v,
                    "role": self.role.get(k, ""),
                    "view": list(self.view[k]) if k in self.view else None,
                    "note": self.notes.get(k, ""),
                }
                for k, v in sorted(self.region.items())
            ],
            "image": [{"region": k, "delta": v} for k, v in sorted(self.image.items())],
            "groups": {g: dict(v, members=sorted(v["members"])) for g, v in self.groups.items()},
            "copies": self.copies,
            "u16": [{"lo": lo, "hi": hi, "name": n} for (lo, hi), n in sorted(self.u16.items())],
            "procs": self.procs,
            "phase": None if self.phase is None else {"region": self.phase[0]},
            "index": self.index,
        }

    @classmethod
    def from_dict(cls, doc):
        """A :class:`Names` from :meth:`to_dict`: the serialised parts, unchanged."""
        n = cls(copies=doc["copies"], procs=doc["procs"], index=doc.get("index") or [])
        for r in doc["regions"]:
            n.region[r["id"]] = r["name"]
            for dst, key in ((n.role, "role"), (n.notes, "note")):
                if r[key]:
                    dst[r["id"]] = r[key]
            if r["view"]:
                n.view[r["id"]] = tuple(r["view"])
        n.image = {x["region"]: x["delta"] for x in doc["image"]}
        n.groups = {g: dict(v) for g, v in doc["groups"].items()}
        n.u16 = {(tuple(x["lo"]), tuple(x["hi"])): x["name"] for x in doc["u16"]}
        n.phase = None if doc["phase"] is None else (doc["phase"]["region"],)
        return n


def index_relation(facts, names):
    """The index relation of :meth:`Names.to_dict`, as plain records.

    One record per index cell and target region: what the cell indexes, and the
    base its index is added to. A pointer base carries the pair's name and the
    regions the pair's low byte is loaded from, so a table reached through a
    pointer table is two joined records and not one opaque access.
    """
    out = []
    for cell, uses in sorted(facts.idxbase.items(), key=lambda kv: _cellkey(kv[0])):
        for target, kind, lo, hi in sorted(uses, key=lambda u: (u[0], u[1], str(u[2]))):
            rec = {"region": cell[0], "addr": cell[1], "target": target, "base": kind}
            if kind == "ptr":
                rec["pair"] = _pairname(names, lo, hi)
                rec["tables"] = sorted(facts.cellsrc.get(lo, ()))
            out.append(rec)
    return out


def _cellkey(cell):
    """Sort order for an index cell: by region, the whole-region index last."""
    return cell[0], cell[1] is None, cell[1] or 0


def _pairname(names, lo, hi):
    """What the print spells a 16-bit base: the named pair, or the region holding both."""
    return names.u16.get((lo, hi)) or (names.region.get(lo[0], "") if lo[0] == hi[0] else "")


# ---- roles -------------------------------------------------------------------
def _groups(prog, names):
    """Struct views: regions of equal stride and element count are one view."""
    groups = {}
    for r in prog.storage:
        n = (r.size + r.stride - 1) // r.stride if r.stride else 1
        if r.id < 0 or r.stride < 1 or r.kind == "io" or r.size != (n - 1) * r.stride + 1:
            continue
        if n < 3 or (r.stride == 1 and n != 3):
            continue
        groups.setdefault((r.stride, n), []).append(r.id)
    out = {}
    three = {k: v for k, v in groups.items() if k[1] == 3 and len(v) > 1}
    best = max(sorted(three), key=lambda k: len(three[k]), default=None)  # the widest is voice
    for i, ((stride, n), members) in enumerate(sorted(groups.items())):
        if len(members) < 2:
            continue
        g = unique_name("voice" if (stride, n) == best else "rec%d" % (i + 1), out)
        out[g] = {"stride": stride, "n": n, "members": members}
        for rid in members:
            names.view[rid] = (g, "")
    return out


def _layouts(data, n):
    """The (name, lo, hi) column pairs a table of ``n`` entries could be stored as."""
    return (
        ("lo|hi", data[:n], data[n : 2 * n]),
        ("hi|lo", data[n : 2 * n], data[:n]),
        ("u16le", data[0 : 2 * n : 2], data[1 : 2 * n : 2]),
    )


def _freq_layout(data, least=48, most=256):
    """A note-frequency layout ``(columns, entries, entries below an octave)`` of ``data``."""
    for n in range(min(len(data) // 2, most), least - 1, -1):
        for name, lo, hi in _layouts(data, n):
            cut = next((i for i, v in enumerate(hi) if v), n)
            if n - cut < least or not is_octave_ramp(list(hi[cut:])):
                continue
            v = [(h << 8) | l for l, h in zip(lo[cut:], hi[cut:])]
            if _semitones(v) >= least:
                return name, n, cut
    return None


def _semitones(v, lo=1.04, hi=1.08):
    """The longest run of consecutive entries one 12-TET semitone apart."""
    best = run = 0
    for a, b in zip(v, v[1:]):
        run = run + 1 if a and lo < b / a < hi else 0
        best = max(best, run)
    return best


def _tables(prog, facts, names):
    """Read-only regions: an array read through an index is a table; the rest keep an address."""
    indexed = {t for s in facts.index.values() for t in s}
    for r in prog.storage:
        if r.id < 0 or r.id in names.region or r.kind not in ("const", "image", "init_constant"):
            continue
        names.role[r.id] = names.role.get(r.id) or ("table" if r.id in indexed else "")
        if r.zero < r.base:
            names.notes[r.id] = "%d-based, read at $%04X,i" % (r.base - r.zero, r.zero)
        _uniq(names, r.id, "b%04X" % r.base if r.id in names.view else "T%04X" % r.base)


def _uniq(names, rid, want):
    """Give region ``rid`` the name ``want``, made unique against the ones taken."""
    names.region[rid] = n = unique_name(want, set(names.region.values()))
    return n


def _update_role(facts, rid):
    """The role a region's own updates give it."""
    return update_role(facts.updates.get(rid, ()), rid in facts.plain, rid)


def _basename(r, role, facts, names):
    """The name a region gets from its role, its target, or its address."""
    if role == "cursor":
        tgt = sorted(facts.index.get(r.id, ()))
        name = names.region.get(tgt[0]) if tgt else None
        return "%s_idx" % name.lower() if name else "cursor_%04X" % r.base
    if role in ("timer", "counter", "acc", "ptr"):
        return role
    return "b%04X" % r.base


def _unique_proc(names, want):
    return unique_name(want, set(names.procs.values()), sep="")


def _tail_target(prog, name):
    """The procedure ``name`` exists only to call, or ``name`` itself."""
    tgt = forwarder(prog.procs[name])
    return _tail_target(prog, tgt) if tgt is not None else name


def _proc_names(prog, facts, names, structured):
    """Procedure names: the phase arms are the two rates, a record decoder is row_apply."""
    for name in prog.procs:
        names.procs[name] = name
    if names.phase is not None:
        for arm, tag in ((names.phase[3], "main"), (names.phase[2], "sub")):
            for callee in arm[:1]:
                names.procs[_tail_target(prog, callee)] = _unique_proc(names, tag)
    ptr = {r for r, k in names.role.items() if k == "ptr"}
    for name, p in prog.procs.items():
        wr = {r for r in facts.writes[name] if facts.rgn.get(r) and facts.rgn[r].kind == "state"}
        if names.procs[name] != name or p.kind != "sub":
            continue
        if len(wr) >= 4 and facts.reads[name] & ptr:
            names.procs[name] = _unique_proc(names, "row_apply")
    for name in prog.procs:
        tgt = _tail_target(prog, name)
        if tgt != name:
            names.procs[name] = names.procs[tgt]


def _freq(prog, names):
    """Name every note-frequency table: one region, or two adjacent parallel columns."""
    rs = [r for r in prog.storage if r.id >= 0 and 8 <= r.size <= 4096]
    for r in rs:
        note = _freq_layout(r.init)
        if note:
            _name_freq(names, [r], note, ["FREQ"])
    sized = {}
    for r in rs:
        sized.setdefault((r.size, r.kind), []).append(r)
    for group in sorted(sized.values(), key=lambda g: -g[0].size):
        for a, q in [(a, q) for a in group for q in group if a.base < q.base][:MAXPAIRS]:
            if {a.id, q.id} & set(names.region):
                continue
            note = _freq_layout(a.init + q.init)
            if note:
                cols = ["FREQ_LO", "FREQ_HI"] if note[0] == "lo|hi" else ["FREQ_HI", "FREQ_LO"]
                _name_freq(names, [a, q], note, cols)


def _name_freq(names, regions, lay, cols):
    names.freq[tuple(r.id for r in regions)] = lay
    for r, col in zip(regions, cols):
        names.role[r.id] = "freq_table"
        names.notes[r.id] = "12-TET %s, %d entries (%d below one octave)" % lay
        _uniq(names, r.id, col)


def _unrolled(prog, facts, names):
    """Unrolled copies: one variable per voice, written by the same code ``d`` bytes on.

    Three regions of equal shape whose writer pcs are the same set shifted by ``d``
    are the same variable in three unrolled blocks, so they are one struct field.
    """
    left = [r for r in prog.storage if r.id >= 0 and r.kind == "state" and r.id not in names.view]
    by, tri = {}, {}
    for r in left:
        if facts.wpc.get(r.id):
            by.setdefault((r.size, r.stride, names.role.get(r.id, "")), {})[r.base] = r
    for bases in by.values():
        for b in sorted(bases):
            for d in sorted(x - b for x in bases if x > b):
                rs = [bases.get(b + i * d) for i in range(3)]
                if all(rs) and _shifted(facts, rs, d) and not any(r.id in names.view for r in rs):
                    tri.setdefault(d, []).append(rs)
                    for i, r in enumerate(rs):
                        names.view[r.id] = (None, r.base)
                        names.elem[r.id] = i
                    break
    g = "voice" if names.groups.get("voice", {}).get("n") == 3 else None
    d = max(tri, key=lambda k: len(tri[k]), default=None)
    for k, chains in tri.items():
        for rs in chains:
            for r in rs:
                if k != d or g is None:
                    del names.view[r.id], names.elem[r.id]
                else:
                    names.view[r.id] = (g, names.region.get(rs[0].id, "b%04X" % rs[0].base))
                    names.groups[g]["members"].append(r.id)


def _shifted(facts, rs, d):
    """True when the regions are written by as many pcs, at least half of them ``d`` apart."""
    pcs = [facts.wpc.get(r.id, set()) for r in rs]
    if any(len(p) != len(pcs[0]) for p in pcs):
        return False
    return all(2 * len({x + i * d for x in pcs[0]} & p) >= len(p) for i, p in enumerate(pcs))


def recover(prog, structured=None, facts=None):
    """The :class:`Names` for ``prog``; pass :func:`~.structure.structure`'s result."""
    facts = facts or Facts(prog)
    names = Names()
    tick = prog.meta.get("tick_proc")
    if structured and tick in structured:
        names.phase = _phase(structured[tick], prog.storage)
    _freq(prog, names)
    names.groups = _groups(prog, names)
    names.scale = scales(facts)
    names.image = image_copy(facts)
    for rid, delta in sorted(names.image.items()):
        names.role[rid] = "sid_image"
        base = facts.rgn[rid].base
        names.notes[rid] = (
            "the RAM under $%04X.." % base if not delta else ("flushed to $%04X.." % (base + delta))
        )
        _uniq(names, rid, "ghost")
    for rid, (fname, elems) in sorted(sid_image(facts).items()):
        r = facts.rgn[rid]
        # a region is the SID image when its elements are, not when a few of a
        # hundred zero-page bytes reach a register; a name it already earned wins
        if rid in names.region or 2 * len(elems) < elem_count(r):
            continue
        names.role[rid] = "sid_image"
        _uniq(names, rid, fname)
    for r in prog.storage:
        if r.id < 0 or r.id in names.region or r.kind not in ("state", "init_constant"):
            continue
        ptr = r.id in facts.addr or (r.size == 2 and r.id in facts.index)
        role = names.role.get(r.id) or ("ptr" if ptr else "")
        # a role one accessor proves names a scalar or a small struct field, not a
        # block one init loop happened to make one region (Follin's zero page)
        if elem_count(r) <= MAXROLE:
            role = role or ("cursor" if r.id in facts.index else "") or _update_role(facts, r.id)
        names.role[r.id] = role
        _uniq(names, r.id, _basename(r, role, facts, names))
    if names.phase is not None:
        rid = names.phase[0]
        names.role[rid] = "phase"
        del names.region[rid]
        _uniq(names, rid, "call_counter" if _update_role(facts, rid) == "counter" else "phase")
    for r in prog.storage:
        if r.kind == "copymap":  # the per-copy columns the fold made: one name each
            names.region[r.id] = r.name
            names.role[r.id] = "per_copy"
    names.voicemap = voice_maps(prog)
    # the role wants the second source the printing spends per use: a SID index
    for rid in sorted(names.voicemap & facts.sididx):
        names.role[rid] = "voice_map"
        _uniq(names, rid, "voice_map")
    _tables(prog, facts, names)
    for rid, (g, _f) in list(names.view.items()):
        names.view[rid] = (g, names.region.get(rid, "b%04X" % facts.rgn[rid].base))
    _unrolled(prog, facts, names)
    _proc_names(prog, facts, names, structured)
    return names


# ---- 16-bit pairs ------------------------------------------------------------
def _feeds(prog, want=range(0xD415, 0xD419)):
    """The regions whose value reaches one of the filter registers."""
    out = set()
    for proc in prog.procs.values():
        defs = {}
        for b in proc.blocks.values():
            for x in b.stmts:
                if type(x) is Let:
                    defs.setdefault(x.n, []).append(x.e)
        for b in proc.blocks.values():
            for x in b.stmts:
                if type(x) is Store and x.cls == "io" and type(x.a) is Const and x.a.v in want:
                    _sources(x.v, defs, out, set())
    return out


def _sources(e, defs, out, seen, depth=8):
    for x in walk(e):
        if type(x) is Load:
            out.add(x.r)
        elif type(x) is Var and x.n not in seen and depth:
            seen.add(x.n)
            for d in defs.get(x.n, ()):
                _sources(d, defs, out, seen, depth - 1)
    return out


def _r16s(e):
    if type(e) is R16:
        return [e]
    return _r16s(e.a) + _r16s(e.b) if type(e) is Bin else []


def name_u16(prog, names, words):
    """Name every folded pair, its halves, and the group its data flows to."""
    rgn = prog.by_id()
    filt, seen, kind = _feeds(prog), {}, {}
    for w in words:
        ops = [(x.lo, x.hi) for x in _r16s(w.e)]
        for p in [(w.lo, w.hi)] + ops:
            seen.setdefault(p, (w.lo, w.hi))
            kind.setdefault(p, "operand" if p != (w.lo, w.hi) else "")
        if (w.lo, w.hi) in ops:
            kind[(w.lo, w.hi)] = "acc"
    for p, dest in sorted(seen.items()):
        if p == dest and {c[0] for c in p} & filt and not {c[0] for c in p} & set(names.view):
            names.u16group[p] = "filter"
    for p, dest in sorted(seen.items()):
        if p in names.u16 or any(c[0] not in rgn for c in p) or register(p) is not None:
            continue  # the chip's own 16-bit register is named by the register file
        base = _u16name(names, rgn, p, kind.get(p, ""))
        group = names.u16group.get(dest, "")
        names.u16[p] = _uniqword(names, "%s.%s" % (group, base) if group else base, p)
        names.u16group[p] = group
        for (rid, _a), half in zip(p, ("lo", "hi")) if p[0][0] != p[1][0] else ():
            cur = names.region.get(rid, "")
            if rid not in names.view and cur[:-2].lower() != base.lower() + "_":
                names.region[rid] = "%s_%s" % (names.u16[p], half)


def _u16name(names, rgn, pair, kind):
    """``freq`` from ``freq_lo``/``freq_hi``, the region a whole word fills, else the role."""
    (lo, la), (hi, _ha) = pair
    a, b = names.region.get(lo, ""), names.region.get(hi, "")
    if lo == hi:
        return a or "w%04X" % la
    low = (a[:-2].lower(), b[:-2].lower(), a[-2:].lower(), b[-2:].lower())
    if a and b and low[0] == low[1] and low[2:] == ("lo", "hi"):
        return a[:-3] if a[-3] == "_" else a[:-2]
    if kind == "acc" or names.role.get(lo) == "acc":
        return "acc"
    if kind == "operand" and rgn[lo].kind == "const":
        return a or "T%04X" % rgn[lo].base
    if kind == "operand" and rgn[lo].kind == "init_constant":
        return "base"
    return "step" if kind == "operand" else "w%04X" % la


def _uniqword(names, want, own):
    taken = (set(names.region.values()) | set(names.u16.values())) - {
        names.region.get(r) for r, _a in own
    }
    return unique_name(want, taken)
