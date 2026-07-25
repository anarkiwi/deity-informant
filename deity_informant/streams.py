"""Sequencer-state slicing of a decompiled playroutine model (M4.1).

Read-only over :class:`deity_informant.structured.Model`: back-slices SID
stores to the state cells and image reads feeding them, classifies state
cells from update code, and derives image-read stream descriptors.
"""

from __future__ import annotations

import weakref

from . import expr as E
from .render import fmt, name_addr
from .structured import SID_HI, SID_LO

_GROUP_CAP = 32
_CELL_CAP = 1024
_REG_DEPTH = 4
_FLOW_DEPTH = 3
_FANIN_CAP = 12
_FACTS = weakref.WeakKeyDictionary()


def _is_state(model, a):
    """Whether ``a`` is a mutable state cell (written, not stack, not IO)."""
    return a in model.written and not 0x100 <= a <= 0x1FF and not 0xD000 <= a <= 0xDFFF


def _inline(n, smap):
    k = n[0]
    if k == "uni":
        return smap.get(n[1], n)
    if k == "mem":
        return ("mem", _inline(n[1], smap), n[2])
    if k == "op":
        return ("op", n[1], tuple(_inline(c, smap) for c in n[2]), n[3])
    return n


def _contains(n, sub):
    if n == sub:
        return True
    if not isinstance(n, tuple):
        return False
    if n[0] == "mem":
        return _contains(n[1], sub)
    if n[0] == "op":
        return any(_contains(c, sub) for c in n[2])
    return False


def _idx_hi(n):
    """Sound upper bound of an index expression's value."""
    if E.is_const(n):
        return n[1]
    if n[0] == "op":
        mn, kids = n[1], n[2]
        if mn == "INT_ZEXT":
            return _idx_hi(kids[0])
        if mn == "INT_AND":
            consts = [c[1] for c in kids if E.is_const(c)]
            if consts:
                return min(consts)
        if mn == "INT_ADD":
            return min(sum(_idx_hi(c) for c in kids), E.mask(n[3]))
        if mn == "INT_LEFT" and E.is_const(kids[1]):
            return min(_idx_hi(kids[0]) << kids[1][1], E.mask(n[3]))
    return E.mask(E.width(n))


def _strip_zext(n):
    while n[0] == "op" and n[1] == "INT_ZEXT":
        n = n[2][0]
    return n


def _mem_leg(n):
    """``(base, indexed)`` for a byte-wide mem leaf with resolvable base."""
    n = _strip_zext(n)
    if n[0] != "mem":
        return None
    a = n[1]
    if E.is_const(a):
        return (a[1], False)
    if a[0] == "op" and a[1] == "INT_ADD":
        consts = [c[1] for c in a[2] if E.is_const(c)]
        if len(consts) == 1:
            return (consts[0], True)
    return None


def _word_pair(n):
    """``(lo_leg, hi_leg)`` for a ``hi<<8|lo`` pointer word, else None."""
    if n[0] != "op" or n[1] != "INT_OR" or len(n[2]) != 2:
        return None
    for a, b in (n[2], n[2][::-1]):
        if a[0] == "op" and a[1] == "INT_LEFT" and E.is_const(a[2][1]) and a[2][1][1] == 8:
            lo, hi = _mem_leg(b), _mem_leg(a[2][0])
            if lo is not None and hi is not None:
                return (lo, hi)
    return None


def _split(aexpr):
    """Address form: ``("const", a, None)`` | ``("idx", base, idx)`` |
    ``("pair", (lo_leg, hi_leg), idx)`` | ``("top", None, None)``."""
    if E.is_const(aexpr):
        return ("const", aexpr[1], None)
    pw = _word_pair(aexpr)
    if pw is not None:
        return ("pair", pw, E.konst(0, 1))
    if aexpr[0] == "op" and aexpr[1] == "INT_ADD":
        consts = [c for c in aexpr[2] if E.is_const(c)]
        rest = [c for c in aexpr[2] if not E.is_const(c)]
        for r in rest:
            pw = _word_pair(r)
            if pw is not None:
                others = [c for c in aexpr[2] if c is not r]
                idx = others[0] if len(others) == 1 else E.op("INT_ADD", others, aexpr[3])
                return ("pair", pw, idx)
        if len(consts) == 1 and rest:
            idx = rest[0] if len(rest) == 1 else E.op("INT_ADD", rest, aexpr[3])
            return ("idx", consts[0][1], idx)
    return ("top", None, None)


def _self_delta(aexpr, vexpr):
    """``(signed_delta, mask)`` if ``vexpr`` is ``(mem[aexpr] + d) [& m]``."""
    m = None
    if vexpr[0] == "op" and vexpr[1] == "INT_AND" and len(vexpr[2]) == 2:
        consts = [c for c in vexpr[2] if E.is_const(c)]
        rest = [c for c in vexpr[2] if not E.is_const(c)]
        if len(consts) == 1 and len(rest) == 1:
            m, vexpr = consts[0][1], rest[0]
    if vexpr[0] != "op" or vexpr[1] != "INT_ADD":
        return None
    d, memkid = 0, None
    for c in vexpr[2]:
        if E.is_const(c):
            d += c[1]
        elif c[0] == "mem" and c[1] == aexpr:
            if memkid is not None:
                return None
            memkid = c
        else:
            return None
    if memkid is None:
        return None
    d &= E.mask(vexpr[3])
    if E.width(vexpr) == 1 and d >= 0x80:
        d -= 0x100
    return None if d == 0 else (d, m)


def _self_advance(aexpr, vexpr):
    """Whether ``vexpr`` adds a non-constant step to ``mem[aexpr]``."""
    if vexpr[0] != "op" or vexpr[1] != "INT_ADD":
        return False
    return any(c[0] == "mem" and c[1] == aexpr for c in vexpr[2])


def _read_of(vexpr):
    """The address split of ``vexpr`` when it is (a zext/mask of) one read."""
    v = _strip_zext(vexpr)
    if v[0] == "op" and v[1] == "INT_AND" and len(v[2]) == 2:
        rest = [c for c in v[2] if not E.is_const(c)]
        if len(rest) == 1:
            v = _strip_zext(rest[0])
    if v[0] == "mem":
        return _split(v[1])
    return None


def _eq_const(flag):
    """``(subject, const)`` for an equality flag, normalising CMP's ``x-k == 0``."""
    if flag[0] != "op" or flag[1] not in ("INT_EQUAL", "INT_NOTEQUAL"):
        return None
    a, b = flag[2]
    if not E.is_const(b):
        a, b = b, a
    if not E.is_const(b) or E.is_const(a):
        return None
    subj, const = a, b[1]
    if const == 0 and subj[0] == "op" and subj[1] == "INT_ADD":
        consts = [c for c in subj[2] if E.is_const(c)]
        rest = [c for c in subj[2] if not E.is_const(c)]
        if len(consts) == 1 and len(rest) == 1:
            return rest[0], (-consts[0][1]) & E.mask(subj[3])
    return subj, const


def _term_exprs(term):
    kind = term[0]
    if kind == "br":
        yield ("flag", term[4])
        if term[5] is not None:
            yield ("dyn", term[5])
    elif kind == "jmpd":
        yield ("dyn", term[1])
    elif kind == "jmpind" and term[2] is not None:
        yield ("dyn", term[2])
    elif kind == "jsr" and term[3] is not None:
        yield ("dyn", term[3])


class _Facts:
    """Inlined per-block expressions plus model-wide store/read/flow indexes."""

    def __init__(self, model):
        self.model = model
        self.stores = []
        self.loads = []
        self.by_key = {}
        self.flags = []
        self.dyns = []
        self.regs = {}
        self.cont_pcs = set()
        self._scan()
        self._groups()
        self._update_index()
        self._flow()

    def _scan(self):
        for key, blk in self.model.blocks.items():
            smap, loads, stores = {}, [], []
            for ev in blk.events:
                if ev[0] == "ld":
                    ax = _inline(ev[2], smap)
                    smap[ev[1]] = ("mem", ax, 1)
                    loads.append((ax, _split(ax)))
                elif ev[0] == "st":
                    ax, vx = _inline(ev[1], smap), _inline(ev[2], smap)
                    stores.append((ax, vx, _split(ax)))
            flags, dyns = [], []
            for kind, ex in _term_exprs(blk.term):
                ex = _inline(ex, smap)
                (flags if kind == "flag" else dyns).append(ex)
            self.by_key[key] = (stores, flags, dyns)
            self.stores += [(key, ax, vx, sp) for ax, vx, sp in stores]
            self.loads += [(key, ax, sp) for ax, sp in loads]
            self.flags += [(key, ex) for ex in flags]
            self.dyns += [(key, ex) for ex in dyns]
            self.regs[key] = [_inline(r, smap) for r in blk.regs]

    def _groups(self):
        """Partition indexed mutable arrays at the program's own access bases."""
        model = self.model
        hi = {}

        def anchor(base, idx):
            if base is not None and any(_is_state(model, base + k) for k in range(_GROUP_CAP)):
                hi[base] = max(hi.get(base, 0), _idx_hi(idx))

        for _key, _ax, sp in self.loads:
            if sp[0] == "idx":
                anchor(sp[1], sp[2])
            elif sp[0] == "pair":
                for base, indexed in sp[1]:
                    if indexed:
                        anchor(base, E.konst(0xFF, 1))
        for _key, _ax, _vx, sp in self.stores:
            if sp[0] == "idx":
                anchor(sp[1], sp[2])
        anchors = sorted(hi)
        self.group = {}
        for j, b in enumerate(anchors):
            nxt = anchors[j + 1] if j + 1 < len(anchors) else b + _GROUP_CAP
            cap = min(b + hi[b], b + _GROUP_CAP - 1, nxt - 1)
            cells, a = [], b
            while a <= cap and a in model.written:
                if _is_state(model, a):
                    cells.append(a)
                a += 1
            self.group[b] = tuple(cells) or (b,)

    def _update_index(self):
        """``cell -> [(key, aexpr, vexpr, idx)]`` plus SID and top store lists."""
        model = self.model
        self.updates = {}
        self.sid_stores = []
        self.top_stores = []
        for key, ax, vx, sp in self.stores:
            kind, base, idx = sp
            if kind == "const":
                if SID_LO <= base <= SID_HI:
                    self.sid_stores.append((key, base, vx, False))
                elif _is_state(model, base):
                    self.updates.setdefault(base, []).append((key, ax, vx, None))
            elif kind == "idx":
                if SID_LO <= base <= SID_HI:
                    self.sid_stores.append((key, base, vx, True))
                elif base in self.group:
                    for c in self.group[base]:
                        self.updates.setdefault(c, []).append((key, ax, vx, idx))
                else:
                    self.top_stores.append((key, ax))
            else:
                self.top_stores.append((key, ax))

    def _flow(self):
        """Static predecessor map and dispatch-fed cells (vector/target reads)."""
        model = self.model
        self.preds = {}
        self.dispatch_cells = set()
        for key, blk in model.blocks.items():
            if blk.term[0] == "jsr":
                self.cont_pcs.add((blk.term[2] + 1) & 0xFFFF)
            if blk.term[0] == "jmpind" and blk.term[1] is not None:
                ptr = blk.term[1]
                for a in (ptr, (ptr & 0xFF00) | ((ptr + 1) & 0xFF)):
                    if a in model.written:
                        self.dispatch_cells.add(a)
            for skey in self.succs(key):
                self.preds.setdefault(skey, []).append(key)
        for key, ex in self.dyns:
            sl = _Slice()
            _expr_deps(self, key, ex, sl, 0, set())
            self.dispatch_cells |= sl.cells

    def succs(self, key):
        """Successor block keys along static and resolved dynamic edges."""
        blk = self.model.blocks[key]
        term = blk.term
        kind = term[0]
        dyn = self.model.dyn_targets.get(blk.pcs[-1], ())
        if kind in ("goto", "jmp"):
            pcs = [term[1]]
        elif kind == "br":
            pcs = ([term[2]] if term[2] is not None else list(dyn)) + [term[3]]
        elif kind == "jmpd":
            pcs = list(dyn)
        elif kind == "jmpind":
            pcs = list(dyn)
            if not pcs and term[1] is not None and term[1] not in self.model.written:
                m, ptr = self.model.mem0, term[1]
                pcs = [m[ptr] | (m[(ptr & 0xFF00) | ((ptr + 1) & 0xFF)] << 8)]
        elif kind == "jsr":
            pcs = ([term[1]] if term[1] is not None else list(dyn)) + [(term[2] + 1) & 0xFFFF]
        else:
            pcs = []
        return [k for pc in pcs for k in self.model.variants(pc)]


def _facts(model):
    f = _FACTS.get(model)
    if f is None:
        f = _Facts(model)
        _FACTS[model] = f
    return f


class _Slice:
    """Accumulated slice: state cells, image-read sites, unresolved notes."""

    def __init__(self):
        self.cells = set()
        self.sites = set()
        self.notes = set()

    def site(self, sp):
        kind, base, _idx = sp
        if kind == "idx":
            self.sites.add(("table", base))
        elif kind == "pair":
            self.sites.add(("pair", sp[1]))
        else:
            self.sites.add(("byte", base))


def _pair_cells(f, legs):
    """State cells behind a pointer pair's lo/hi legs."""
    out = []
    for base, indexed in legs:
        if indexed:
            out += list(f.group.get(base, ()))
        elif _is_state(f.model, base):
            out.append(base)
    return out


def _expr_deps(f, key, n, sl, regdepth, seen):
    """Collect mutable-cell and image-read dependencies of an expression."""
    k = n[0]
    if k == "mem":
        _read_deps(f, key, _split(n[1]), sl, regdepth, seen)
    elif k == "reg":
        _reg_deps(f, key, n[1], sl, regdepth, seen)
    elif k == "op":
        for c in n[2]:
            _expr_deps(f, key, c, sl, regdepth, seen)
    elif k == "uni":
        sl.notes.add("unmapped load slot at $%04X" % key[0])


def _read_deps(f, key, sp, sl, regdepth, seen):
    kind, base, idx = sp
    model = f.model
    if kind == "const":
        if _is_state(model, base):
            sl.cells.add(base)
        elif base in model.written:
            sl.notes.add("read of non-state mutable %s" % name_addr(base))
        else:
            sl.site(sp)
        return
    if kind == "top":
        sl.notes.add("computed read at $%04X" % key[0])
        return
    if kind == "idx":
        if base in f.group:
            sl.cells.update(f.group[base])
        elif base in model.written:
            sl.notes.add("read of non-state mutable %s" % name_addr(base))
        else:
            sl.site(sp)
    else:
        sl.site(sp)
        sl.cells.update(_pair_cells(f, base))
    _expr_deps(f, key, idx, sl, regdepth, seen)


def _reg_deps(f, key, i, sl, regdepth, seen):
    """Bounded def-chain walk of a block-entry register through predecessors."""
    if (key, i) in seen:
        return
    seen.add((key, i))
    if regdepth <= 0:
        sl.notes.add("reg r%d at $%04X beyond depth bound" % (i, key[0]))
        return
    ps = f.preds.get(key, ())
    if not ps or key[0] in f.cont_pcs:
        sl.notes.add("reg r%d at $%04X has unresolved predecessors" % (i, key[0]))
        if not ps:
            return
    for p in sorted(ps)[:_FANIN_CAP]:
        ex = f.regs[p][i]
        if ex == E.reg(i):
            _reg_deps(f, p, i, sl, regdepth, seen)
        else:
            _expr_deps(f, p, ex, sl, regdepth - 1, seen)
    if len(ps) > _FANIN_CAP:
        sl.notes.add("reg r%d at $%04X fan-in truncated" % (i, key[0]))


# ---- 1. slice_sid ---------------------------------------------------------------
def slice_sid(model):
    """Per SID store site: the transitive mutable-cell/image-read closure of
    its value expression, keyed by ``(block_pc, sid_addr)``."""
    f = _facts(model)
    out = {}
    for key, base, vx, indexed in f.sid_stores:
        sl = out.setdefault((key[0], base), _Slice())
        if indexed:
            sl.notes.add("indexed SID store (base register)")
        _expr_deps(f, key, vx, sl, _REG_DEPTH, set())
        _close_cells(f, sl)
    return {
        k: {
            "cells": sorted(sl.cells),
            "sites": sorted(sl.sites, key=repr),
            "notes": sorted(sl.notes),
        }
        for k, sl in out.items()
    }


def _close_cells(f, sl):
    """Transitive closure over cell update expressions (value + index)."""
    done = set()
    work = sorted(sl.cells)
    while work:
        c = work.pop()
        if c in done:
            continue
        done.add(c)
        if len(sl.cells) > _CELL_CAP:
            sl.notes.add("cell closure truncated at %d" % _CELL_CAP)
            return
        for key, _ax, vx, idx in f.updates.get(c, ()):
            _expr_deps(f, key, vx, sl, _REG_DEPTH, set())
            if idx is not None:
                _expr_deps(f, key, idx, sl, _REG_DEPTH, set())
        for key, ax in f.top_stores:
            lo, hi = _top_range(ax)
            if lo <= c <= hi:
                sl.notes.add("computed store at $%04X may write %s" % (key[0], name_addr(c)))
        work.extend(a for a in sl.cells if a not in done)


def _top_range(aexpr):
    """Conservative [lo, hi] byte range a computed store address may take."""
    sp = _split(aexpr)
    if sp[0] == "idx":
        return sp[1], min(sp[1] + _idx_hi(sp[2]), 0xFFFF)
    if sp[0] == "const":
        return sp[1], sp[1]
    return 0, 0xFFFF


# ---- 2. classify ----------------------------------------------------------------
def _pairs(f):
    """Pointer-pair usage from deref sites: legs -> sites + position slice."""
    pairs = {}
    for key, _ax, sp in f.loads:
        if sp[0] != "pair":
            continue
        rec = pairs.setdefault(sp[1], {"sites": set(), "pos": _Slice()})
        rec["sites"].add(key[0])
        _expr_deps(f, key, sp[2], rec["pos"], _REG_DEPTH, set())
    return pairs


def _index_use(f):
    """``cell -> {table_base}`` for cells indexing const-based image reads."""
    use = {}
    for key, _ax, sp in f.loads:
        if sp[0] != "idx" or sp[1] in f.model.written:
            continue
        sl = _Slice()
        _expr_deps(f, key, sp[2], sl, _REG_DEPTH, set())
        for c in sl.cells:
            use.setdefault(c, set()).add(sp[1])
    return use


def _update_summary(f, cell):
    """Update-shape summary of one cell's stores across all blocks."""
    s = {
        "deltas": set(),
        "masks": set(),
        "resets": set(),
        "reset_tables": set(),
        "reset_pairs": set(),
        "inputs": set(),
        "advance": False,
        "notes": set(),
    }
    for key, ax, vx, _idx in f.updates.get(cell, ()):
        sd = _self_delta(ax, vx)
        if sd is not None:
            s["deltas"].add(sd[0])
            if sd[1] is not None:
                s["masks"].add(sd[1])
            continue
        if _self_advance(ax, vx):
            s["advance"] = True
            continue
        if E.is_const(vx):
            s["resets"].add(vx[1])
            continue
        rd = _read_of(vx)
        if rd is not None and rd[0] == "idx" and rd[1] not in f.model.written:
            s["reset_tables"].add(rd[1])
        elif rd is not None and rd[0] == "pair":
            s["reset_pairs"].add(rd[1])
        sl = _Slice()
        _expr_deps(f, key, vx, sl, 0, set())
        s["inputs"] |= sl.cells - {cell}
        s["notes"] |= sl.notes
    return s


def _compares(f):
    """``cell -> {const}`` from equality branch flags over that cell's value."""
    out = {}
    for key, flag in f.flags:
        ec = _eq_const(flag)
        if ec is None:
            continue
        sl = _Slice()
        _expr_deps(f, key, ec[0], sl, 0, set())
        for c in sl.cells:
            out.setdefault(c, set()).add(ec[1])
    return out


def _pair_members(f, pairs):
    """``cell -> [(role, partner, pair_rec)]`` over deref'd pointer pairs."""
    out = {}
    for legs, rec in pairs.items():
        (lo, lo_ix), (hi, hi_ix) = legs
        for role, base, indexed, other, other_ix in (
            ("lo", lo, lo_ix, hi, hi_ix),
            ("hi", hi, hi_ix, lo, lo_ix),
        ):
            span = f.group.get(base, (base,)) if indexed else (base,)
            for off, c in enumerate(span):
                partner = other + off if other_ix else other
                if _is_state(f.model, c):
                    out.setdefault(c, []).append((role, partner, rec))
    return out


def classify(model):
    """Update-code classification of every state cell; dict keyed by address."""
    f = _facts(model)
    pair_of = _pair_members(f, _pairs(f))
    index_use = _index_use(f)
    compares = _compares(f)
    out = {}
    for cell in sorted(set(f.updates) | set(pair_of) | set(index_use)):
        s = _update_summary(f, cell)
        rec = {
            "name": name_addr(cell),
            "class": "other",
            "deltas": sorted(s["deltas"]),
            "masks": sorted(s["masks"]),
            "resets": sorted(s["resets"]),
            "reset_tables": sorted(s["reset_tables"]),
            "reset_pairs": sorted(s["reset_pairs"]),
            "inputs": sorted(s["inputs"]),
            "compares": sorted(compares.get(cell, ())),
            "index_bases": sorted(index_use.get(cell, ())),
            "notes": sorted(s["notes"]),
        }
        if cell in pair_of:
            role, partner, _prec = pair_of[cell][0]
            rec["class"] = "pointer"
            rec["role"] = role
            rec["pair"] = (cell, partner) if role == "lo" else (partner, cell)
            rec["reload_tables"] = sorted(s["reset_tables"])
            rec["advance"] = s["advance"] or bool(s["deltas"])
            pos, sites = _Slice(), set()
            for _r, _p, prec in pair_of[cell]:
                pos.cells |= prec["pos"].cells
                pos.notes |= prec["pos"].notes
                sites |= prec["sites"]
            rec["deref_sites"] = sorted(sites)
            rec["position_cells"] = sorted(pos.cells)
            rec["notes"] = sorted(set(rec["notes"]) | pos.notes)
        elif s["deltas"]:
            rec["class"] = "counter"
        elif rec["index_bases"]:
            rec["class"] = "index"
        elif s["reset_tables"] or s["reset_pairs"] or s["inputs"]:
            rec["class"] = "derived"
        out[cell] = rec
    return out


# ---- 3. streams -------------------------------------------------------------------
def _consumers(f, key, node, res, depth, seen):
    """SID/state/dispatch/compare consumers of value ``node`` from block ``key``."""
    if (key, node) in seen or depth <= 0:
        return
    seen.add((key, node))
    stores, flags, dyns = f.by_key[key]
    for _ax, vx, sp in stores:
        if not _contains(vx, node):
            continue
        kind, base, _i = sp
        if kind in ("const", "idx") and SID_LO <= (base or 0) <= SID_HI:
            res["sid"].add(base)
        elif kind == "const" and _is_state(f.model, base):
            res["cells"].add(base)
        elif kind == "idx" and base in f.group:
            res["cells"].update(f.group[base])
    for flag in flags:
        if _contains(flag, node):
            ec = _eq_const(flag)
            if ec is not None and _contains(ec[0], node):
                res["compare"].add(ec[1])
            else:
                res["branch"].add(key[0])
    for ex in dyns:
        if _contains(ex, node):
            res["dispatch"].add(f.model.blocks[key].pcs[-1])
    for i, rx in enumerate(f.regs[key]):
        if rx != E.reg(i) and _contains(rx, node):
            for skey in f.succs(key):
                _consumers(f, skey, E.reg(i), res, depth - 1, seen)


def streams(model):
    """Descriptors for image-read sites addressed by pointer/index state."""
    f = _facts(model)
    out = {}
    for key, ax, sp in f.loads:
        kind, base, idx = sp
        if kind == "idx":
            if base in model.written or 0xD000 <= base <= 0xDFFF:
                continue
            skind = "table"
        elif kind == "pair":
            skind = "pointer"
        else:
            continue
        skey = (key[0], skind, base)
        rec = out.get(skey)
        if rec is None:
            rec = out[skey] = {
                "pc": key[0],
                "kind": skind,
                "base": base,
                "addr": fmt(ax),
                "position": _Slice(),
                "consumers": {
                    "sid": set(),
                    "cells": set(),
                    "dispatch": set(),
                    "compare": set(),
                    "branch": set(),
                },
            }
        _expr_deps(f, key, idx, rec["position"], _REG_DEPTH, set())
        _consumers(f, key, ("mem", ax, 1), rec["consumers"], _FLOW_DEPTH, set())
    result = []
    for rec in out.values():
        cons = rec["consumers"]
        command = bool(cons["dispatch"]) or len(cons["compare"]) >= 2
        command = command or bool(cons["cells"] & f.dispatch_cells)
        result.append(
            {
                "pc": rec["pc"],
                "kind": rec["kind"],
                "base": rec["base"],
                "addr": rec["addr"],
                "pair_cells": _pair_cells(f, rec["base"]) if rec["kind"] == "pointer" else [],
                "position_cells": sorted(rec["position"].cells),
                "position_notes": sorted(rec["position"].notes),
                "consumers": {k: sorted(v) for k, v in cons.items()},
                "command": command,
            }
        )
    result.sort(key=lambda r: (r["pc"], repr(r["base"])))
    return result


# ---- 4. report --------------------------------------------------------------------
def _fmt_base(base):
    if isinstance(base, int):
        return name_addr(base)
    (lo, lo_ix), (hi, hi_ix) = base
    return "(%s%s,%s%s)" % (
        name_addr(lo),
        "[]" if lo_ix else "",
        name_addr(hi),
        "[]" if hi_ix else "",
    )


def _names(addrs):
    return [name_addr(a) for a in addrs]


def _cell_line(rec):
    desc = "%-8s %s" % (rec["class"], rec["name"])
    if rec["class"] == "pointer":
        desc += " pair=%s reload=%s pos=%s" % (
            "/".join(_names(rec["pair"])),
            ",".join(_names(rec["reload_tables"])) or "-",
            ",".join(_names(rec["position_cells"])) or "-",
        )
    elif rec["class"] == "counter":
        resets = ["$%02X" % v for v in rec["resets"]] + _names(rec["reset_tables"])
        desc += " delta=%s resets=%s" % (
            ",".join("%+d" % d for d in rec["deltas"]),
            ",".join(resets) or "-",
        )
        if rec["compares"]:
            desc += " cmp=%s" % ",".join("$%02X" % v for v in rec["compares"])
    elif rec["class"] == "index":
        desc += " bases=%s" % ",".join(_names(rec["index_bases"]))
    else:
        srcs = _names(rec["inputs"]) + [_fmt_base(b) for b in rec["reset_pairs"]]
        srcs += _names(rec["reset_tables"])
        desc += " inputs=%s" % (",".join(srcs) or "-")
    return desc


def _cell_dict(rec):
    r = dict(rec)
    r["resets"] = ["$%02X" % v for v in r["resets"]]
    r["reset_pairs"] = [_fmt_base(b) for b in r["reset_pairs"]]
    for k in ("reset_tables", "inputs", "index_bases", "position_cells", "reload_tables"):
        if k in r:
            r[k] = _names(r[k])
    if "pair" in r:
        r["pair"] = _names(r["pair"])
    if "deref_sites" in r:
        r["deref_sites"] = ["$%04X" % p for p in r["deref_sites"]]
    return r


def _site_name(site):
    kind, base = site
    return "%s:%s" % (kind, _fmt_base(base) if not isinstance(base, int) else name_addr(base))


def report(model):
    """Serializable per-tune summary of cell classes, streams and SID slices;
    the compact reviewable text is under the ``"text"`` key."""
    cls = classify(model)
    strs = streams(model)
    slices = slice_sid(model)
    tally = {}
    for rec in cls.values():
        tally[rec["class"]] = tally.get(rec["class"], 0) + 1
    lines = ["streams report: " + ", ".join("%s=%d" % (k, tally[k]) for k in sorted(tally))]
    cells_out = {}
    for _cell, rec in sorted(cls.items()):
        cells_out[rec["name"]] = _cell_dict(rec)
        if rec["class"] != "other":
            lines.append("  " + _cell_line(rec))
    streams_out = []
    for s in strs:
        cons = s["consumers"]
        streams_out.append(
            {
                "pc": "$%04X" % s["pc"],
                "kind": s["kind"],
                "base": _fmt_base(s["base"]),
                "addr": s["addr"],
                "position_cells": _names(s["position_cells"]),
                "consumers": {
                    "sid": _names(cons["sid"]),
                    "cells": _names(cons["cells"]),
                    "dispatch": ["$%04X" % p for p in cons["dispatch"]],
                    "compare": ["$%02X" % v for v in cons["compare"]],
                },
                "command": s["command"],
            }
        )
        feeds = _names(cons["sid"]) + _names(cons["cells"])
        feeds += ["dispatch@$%04X" % p for p in cons["dispatch"]]
        feeds += ["==$%02X" % v for v in cons["compare"]]
        lines.append(
            "  %s%s $%04X %s pos=%s -> %s"
            % (
                "command " if s["command"] else "",
                s["kind"],
                s["pc"],
                _fmt_base(s["base"]),
                ",".join(_names(s["position_cells"])) or "-",
                ",".join(feeds) or "-",
            )
        )
    slices_out = {}
    for (pc, sid), rec in sorted(slices.items()):
        slices_out["%s@$%04X" % (name_addr(sid), pc)] = {
            "cells": _names(rec["cells"]),
            "sites": [_site_name(s) for s in rec["sites"]],
            "notes": rec["notes"],
        }
    return {
        "tally": tally,
        "cells": cells_out,
        "streams": streams_out,
        "slices": slices_out,
        "text": "\n".join(lines) + "\n",
    }
