"""framefuse: rung (d) of the lift ladder, 16-bit fusion (docs/frameprog.md 4).

A pair fuses on evidence the model already carries: ``datadecl``'s pointer pairs,
the paired-index zip closure of a dispatch word, or the SID lo/hi registers the
canonical section emits adjacent. One lone-half access refuses that pair alone.
"""

from __future__ import annotations

from . import expr as E
from . import frameproc
from . import streams as ST
from .structured import Proof

_SID_LO = 0xD400
_CUTOFF = 0x15  # the filter cutoff lo/hi pair; the voice pairs are freq and pulse


def _half(cell):
    return ("mem", ("const", cell, 2), 1)


def _word(cell):
    return ("mem", ("const", cell, 2), 2)


_w = frameproc.loc_width  # value width of a frameprog node, loc leaves included


def _zext2(n):
    return n if _w(n) == 2 else ("op", "INT_ZEXT", (n,), 2)


def _word_shape(n, lo, hi):
    """``n`` is exactly ``hi<<8 | lo`` over the pair's two byte cells."""
    if n[0] != "op" or n[1] != "INT_OR" or len(n[2]) != 2 or n[3] != 2:
        return False
    for a, b in (n[2], n[2][::-1]):
        if a[0] == "op" and a[1] == "INT_LEFT" and E.is_const(a[2][1]) and a[2][1][1] == 8:
            if ST._strip_zext(a[2][0]) == _half(hi) and ST._strip_zext(b) == _half(lo):
                return True
    return False


def _pack(vlo, vhi, hi_first=True):
    """``hi<<8 | lo``, the half written first left of the bar: evaluation order."""
    shl = ("op", "INT_LEFT", (_zext2(vhi), ("const", 8, 1)), 2)
    kids = (shl, _zext2(vlo)) if hi_first else (_zext2(vlo), shl)
    return ("op", "INT_OR", kids, 2)


def unpack(val):
    """``(lo value, hi value)`` of a packed word value, else None."""
    if val[0] != "op" or val[1] != "INT_OR" or len(val[2]) != 2 or val[3] != 2:
        return None
    for a, b in (val[2], val[2][::-1]):
        if a[0] == "op" and a[1] == "INT_LEFT" and E.is_const(a[2][1]) and a[2][1][1] == 8:
            return ST._strip_zext(b), ST._strip_zext(a[2][0])
    return None


def _addr_split(addr):
    """``(const base, index expression)`` of an address, index None when plain."""
    if addr[0] == "const" and addr[2] == 2:
        return addr[1], None
    got = frameproc._index_of(addr)
    return got if got is not None else (None, None)


def _may_read(n, cell):
    """True when evaluating ``n`` may load ``cell`` (the write-order hazard)."""
    stack = [n]
    while stack:
        x = stack.pop()
        if x[0] == "mem":
            base, idx = _addr_split(x[1])
            if base is None:
                return True
            span = 0 if idx is None else E.mask(_w(idx))
            if base <= cell <= base + span + x[2] - 1:
                return True
            stack.append(x[1])
        elif x[0] == "op":
            stack.extend(x[2])
    return False


def stmts_of(stmts):
    """Every statement of a statement list, nested bodies included."""
    stack = [list(stmts)]
    while stack:
        for s in stack.pop():
            yield s
            stack.extend(list(b) for b in frameproc._stmt_bodies(s))


class _Pair:
    """One candidate lo/hi pair and the evidence gathered against it."""

    __slots__ = ("lo", "hi", "kind", "evidence", "words", "lone", "stores", "unpaired", "hazard")

    def __init__(self, lo, hi, kind, evidence):
        self.lo = lo
        self.hi = hi
        self.kind = kind
        self.evidence = evidence
        self.words = self.lone = self.stores = self.unpaired = self.hazard = 0

    def refusal(self):
        """The premise's refusal diagnostic, or None where the pair fuses.

        A state pair is one tune-wide declaration, so any lone half refuses it; a
        SID pair declares nothing beyond the two statements it rewrites, so its
        premise is per site and only a pair with no fusable site refuses."""
        if self.hi != self.lo + 1:
            return "halves are not adjacent"
        if self.kind != "sid":
            if self.hazard:
                return "%d store pair(s) whose second value may read the first cell" % self.hazard
            if self.lone:
                return "%d lone-half read(s)" % self.lone
            if self.unpaired:
                return "%d unpaired half store(s)" % self.unpaired
        if not (self.words or self.stores):
            return "no word access in the play code"
        return None

    def proof(self):
        """The rung-(d) proof record: evidence, premise counts, refusal."""
        why = self.refusal()
        body = "16-bit fusion: cells $%04X/$%04X; %s; %d word read(s), %d word store(s)" % (
            self.lo,
            self.hi,
            self.evidence,
            self.words,
            self.stores,
        )
        rest = "%d lone-half read(s), %d lone-half store(s), %d hazard(s)" % (
            self.lone,
            self.unpaired,
            self.hazard,
        )
        status = "refused" if why else ("fused" if not (self.lone or self.unpaired) else "partial")
        return Proof(self.lo, self.kind, status, (self.lo, self.hi), "%s; %s" % (body, why or rest))


# ---- candidate pairs: the evidence the spec names --------------------------------
def _pointer_pairs(model, decls):
    """State pairs the pointer classifier proves, named by their partner tables."""
    lanes = {d["base"] for d in decls if (d.get("role") or (None,))[0] == "lo"}
    out = {}
    for _cell, rec in sorted(ST.classify(model).items()):
        if rec["class"] != "pointer" or rec.get("role") != "lo":
            continue
        lts = [t for t in rec.get("reload_tables", ()) if t in lanes]
        note = " (datadecl lo/hi partner table %s)" % " ".join("$%04X" % t for t in lts)
        out[tuple(rec["pair"])] = ("pointer", "pointer pair" + (note if lts else ""))
    return out


def _dispatch_pairs(model):
    """Dispatch operand words the paired-index zip closure proved (study 4)."""
    ana = getattr(model, "analysis", None)
    out = {}
    for site, cells in sorted((getattr(ana, "derivations", None) or {}).items()):
        for (lo, hi), text in sorted(cells.items()):
            out[(lo, hi)] = ("dispatch", "%s (site $%04X)" % (text, site))
    return out


def _sid_base(base):
    """The lo register of the SID lo/hi pair ``base`` belongs to, else None."""
    reg = base - _SID_LO
    if not 0 <= reg <= 0x18:
        return None
    if reg > 0x14:
        return _SID_LO + _CUTOFF if reg in (_CUTOFF, _CUTOFF + 1) else None
    r = reg % 7
    return base - r if r <= 1 else base - (r - 2) if r <= 3 else None


def _sid_pairs(procs):
    """SID lo/hi register pairs a store site addresses (freq, pulse, cutoff)."""
    bases = set()
    for _e, _p, _r, stmts in procs:
        for s in stmts_of(stmts):
            if s[0] == "st":
                b = _sid_base(_addr_split(s[1])[0] or 0)
                if b is not None:
                    bases.add(b)
    note = "SID register pair (the canonical section emits lo,hi adjacent)"
    return {(b, b + 1): ("sid", note) for b in sorted(bases)}


def candidates(model, decls, procs):
    """``{(lo, hi): (kind, evidence)}`` over every pair the model attests."""
    out = dict(_pointer_pairs(model, decls))
    out.update(_dispatch_pairs(model))
    out.update(_sid_pairs(procs))
    return out


# ---- the pass ---------------------------------------------------------------------
def _rewrite(n, p, count):
    """Fold word shapes to a word load; count lone-half reads on the way."""
    if _word_shape(n, p.lo, p.hi):
        p.words += count
        return _word(p.lo)
    k = n[0]
    if k == "mem":
        if n[1] in (("const", p.lo, 2), ("const", p.hi, 2)):
            p.lone += count
            return n
        return ("mem", _rewrite(n[1], p, count), n[2])
    if k == "op":
        return ("op", n[1], tuple(_rewrite(c, p, count) for c in n[2]), n[3])
    return n


def _store_half(s, p):
    """``(cell, index expression)`` when ``s`` stores one half, else None."""
    if s[0] != "st":
        return None
    base, idx = _addr_split(s[1])
    return (base, idx) if base in (p.lo, p.hi) else None


def _pair_at(stmts, i, p):
    """``(first cell, second cell)`` of a half-store pair at ``stmts[i:i+2]``.

    A last-write-wins SID pair may be written hi first: the canonical section
    emits lo,hi whatever the order, so the record cannot move."""
    if i + 1 >= len(stmts):
        return None
    ha, hb = _store_half(stmts[i], p), _store_half(stmts[i + 1], p)
    if ha is None or hb is None or ha[0] == hb[0] or ha[1] != hb[1]:
        return None
    return None if hb[0] != p.hi and p.kind != "sid" else (ha[0], hb[0])


def _visit(stmts, p, mutate):
    """One statement list: fuse paired stores, fold word reads, count refusals."""
    count = 0 if mutate else 1
    i = 0
    while i < len(stmts):
        s = stmts[i]
        for body in frameproc._stmt_bodies(s):
            _visit(body, p, mutate)
        at = _pair_at(stmts, i, p)
        if at is not None:
            if _may_read(stmts[i + 1][2], at[0]):
                p.hazard += count
                i += 2
                continue
            lo, hi = (s, stmts[i + 1]) if at[1] == p.hi else (stmts[i + 1], s)
            p.stores += count
            if mutate:
                stmts[i] = ("st", lo[1], _pack(lo[2], hi[2], at[1] == p.lo))
                del stmts[i + 1]
            i += 1 if mutate else 2
            continue
        if _store_half(s, p) is not None:
            p.unpaired += count
        new = frameproc._map_exprs(s, lambda x: _rewrite(x, p, count))
        if mutate:
            stmts[i] = new
        i += 1


def _merge_alias(symbols, p):
    """Name the fused word after the pair, dropping the hi half's alias."""
    alias = symbols.get(p.lo)
    if alias is not None and alias.endswith("_lo"):
        merged = alias[:-3]
        if merged not in set(symbols.values()):
            symbols[p.lo] = merged
    symbols.pop(p.hi, None)


def _fuse_state(state, symbols, pairs, name_of):
    """Drop each fused pair's hi field and widen its lo field to ``u16``."""
    drop, rename = set(), {}
    for p in pairs:
        drop.add(symbols.get(p.hi) or name_of(p.hi))
        old = symbols.get(p.lo) or name_of(p.lo)
        _merge_alias(symbols, p)
        rename[old] = symbols.get(p.lo) or name_of(p.lo)
    out = []
    for name, width, array, obs in state:
        if name in rename:
            out.append((rename[name], 2, array, obs))
        elif name not in drop:
            out.append((name, width, array, obs))
    return out


def apply_rung(model, decls, procs, state, symbols, name_of):
    """Rung (d) in place over ``procs``; returns ``(state fields, proofs)``.

    Per pair, never per tune: a pair whose premise fails keeps its two byte
    halves and every other pair still fuses. The SID register pairs — freq,
    pulse and cutoff — fuse on the same footing, per store site (spec 4d)."""
    proofs, fused = [], []
    for (lo, hi), (kind, evidence) in sorted(candidates(model, decls, procs).items()):
        p = _Pair(lo, hi, kind, evidence)
        if hi == lo + 1:
            for _e, _pa, _r, stmts in procs:
                _visit(stmts, p, False)
        proofs.append(p.proof())
        if p.refusal() is not None:
            continue
        fused.append(p)
        for _e, _pa, _r, stmts in procs:
            _visit(stmts, p, True)
    state = _fuse_state(state, symbols, [p for p in fused if p.kind != "sid"], name_of)
    return state, proofs
