"""frameptr: rung (f)'s pointer resolution, the residue rung (d) left (spec 4.4).

``mem[P + i]`` resolves when every definition of the pointer state field loads its
halves from a declared ``lo``/``hi`` partner table ``T``: the address is ``T[k] + i``,
row ``i`` of the block ``k`` names, the target set read from the declared extent. Where
that set is one block the address is also a store's source cell (spec 4.6).
"""

from __future__ import annotations

import bisect

from . import framefuse as FU
from . import frameproc
from . import grammar as G
from . import streams as ST
from .structured import Proof

_ROW = 0xFF  # a resolved deref reads one byte row of its target block


# ---- the deref shape ---------------------------------------------------------------
def _fused_cell(n):
    """The state cell of a fused pointer word load, else None."""
    if n[0] == "mem" and n[2] == 2 and n[1][0] == "const" and n[1][2] == 2:
        return n[1][1]
    return None


def _split_cell(n):
    """The lo cell of an unfused ``hi<<8 | lo`` pointer word, else None."""
    legs = ST._word_pair(n)
    if legs is None:
        return None
    (lo, lo_ix), (hi, hi_ix) = legs
    return lo if not lo_ix and not hi_ix and hi == lo + 1 else None


def _cell_of(n):
    """``(cell, fused)`` when ``n`` is a pointer word, else None."""
    for read, fused in ((_fused_cell, True), (_split_cell, False)):
        cell = read(n)
        if cell is not None:
            return cell, fused
    return None


def deref(addr):
    """``(cell, index or None, fused)`` of a base-less deref address, else None."""
    got = _cell_of(addr)
    if got is not None:
        return got[0], None, got[1]
    if addr[0] == "op" and addr[1] == "INT_ADD" and addr[3] == 2 and len(addr[2]) == 2:
        for a, b in (addr[2], addr[2][::-1]):
            got = _cell_of(a)
            if got is not None:
                return got[0], ST._strip_zext(b), got[1]
    return None


def _sub(addr, base):
    """``addr`` with the block the proof pins substituted for the pointer word it reads."""
    blk = ("const", base, 2)
    if _cell_of(addr) is not None:
        return blk
    i = next(j for j, c in enumerate(addr[2]) if _cell_of(c) is not None)
    return ("op", "INT_ADD", tuple(blk if j == i else c for j, c in enumerate(addr[2])), addr[3])


def _addrs(stmts):
    """Every memory address expression under a statement list, with repeats."""
    for s in FU.stmts_of(stmts):
        stack = list(frameproc._stmt_exprs(s))
        if s[0] == "st":
            yield s[1]
        while stack:
            x = stack.pop()
            if x[0] == "mem":
                yield x[1]
            stack.extend(frameproc._kids(x))


# ---- index bounds over the frameprog local alphabet ---------------------------------
def _widen(n, wide):
    """A local replaced by a const at its sound bound, so ``_idx_hi`` applies."""
    if n[0] == "loc":
        return ("const", 0xFFFF, 2) if n[1] in wide else ("const", _ROW, 1)
    kids = frameproc._kids(n)
    return frameproc._rebuild(n, [_widen(c, wide) for c in kids]) if kids else n


def _bound(idx, wide):
    """Sound upper bound of an index expression under the local width map."""
    return ST._idx_hi(_widen(idx, wide))


def _assigns(procs):
    """``(wide local names, local name -> assigned values)``; a local is a byte
    unless some assignment gives it a 16-bit value."""
    wide, vals = set(), {}
    for _e, _p, _r, stmts in procs:
        for s in FU.stmts_of(stmts):
            if s[0] != "asg":
                continue
            vals.setdefault(s[1], []).append(s[2])
            if G.store_width(s[2]) != 1:
                wide.add(s[1])
    return wide, vals


# ---- the writers of a pointer field ------------------------------------------------
def _span(addr, wide, vals, chase=True):
    """``(lo, hi)`` a store at ``addr`` may reach, else None (unprovable).

    ``base + i`` is the declared-index form; ``x | K`` is the stack (``x | K`` lies
    in ``[K, K + hi(x)]``); a local address is the union over its assignments."""
    if addr[0] == "const" and addr[2] == 2:
        return addr[1], addr[1]
    got = frameproc._index_of(addr)
    if got is not None:
        return got[0], got[0] + _bound(got[1], wide)
    if addr[0] == "op" and addr[1] == "INT_OR" and len(addr[2]) == 2:
        ks = [c for c in addr[2] if c[0] == "const"]
        rest = [c for c in addr[2] if c[0] != "const"]
        if len(ks) == 1:
            return ks[0][1], ks[0][1] + _bound(rest[0], wide)
    if addr[0] == "loc" and chase:
        got = [_span(v, wide, vals, False) for v in vals.get(addr[1], ())]
        if got and None not in got:
            return min(s[0] for s in got), max(s[1] for s in got)
    return None


def _writers(procs, wide, vals):
    """``(word values stored per cell, spans a store may reach, wild store)``."""
    defs, spans, wild_store = {}, [], False
    for _e, _p, _r, stmts in procs:
        for s in FU.stmts_of(stmts):
            if s[0] != "st":
                continue
            width = G.store_width(s[2])
            if s[1][0] == "const" and s[1][2] == 2 and width == 2:
                defs.setdefault(s[1][1], []).append(s[2])
            got = _span(s[1], wide, vals)
            if got is None:
                wild_store = True
            else:
                spans.append((got[0], got[1] + width - 1))
    return defs, spans, wild_store


def _leg(half):
    """``(base address, index)`` of one byte-wide table read, else None."""
    if half[0] != "mem" or half[2] != 1:
        return None
    got = frameproc._index_of(half[1])
    if got is not None:
        return got
    if half[1][0] == "const" and half[1][2] == 2:
        return half[1][1], ("const", 0, 1)
    return None


def _entry(val):
    """``(lo address, hi address, entry index)`` when ``val`` reads one pair entry."""
    pair = FU.unpack(val)
    if pair is None:
        return None
    lo, hi = _leg(pair[0]), _leg(pair[1])
    if lo is None or hi is None or lo[1] != hi[1]:
        return None
    return lo[0], hi[0], lo[1]


class _Tables:
    """The declarations, addressable by containment (a lane is an offset in one)."""

    __slots__ = ("decls", "bases")

    def __init__(self, decls):
        self.decls = sorted(decls, key=lambda d: d["base"])
        self.bases = [d["base"] for d in self.decls]

    def at(self, addr):
        """``(declaration, offset)`` of the region containing ``addr``, else None."""
        j = bisect.bisect_right(self.bases, addr) - 1
        if j < 0:
            return None
        d = self.decls[j]
        return (d, addr - d["base"]) if addr < d["base"] + d["size"] else None


class _Ptr:
    """One pointer state field: the definitions reaching it and what they prove."""

    __slots__ = ("cell", "fused", "tables", "targets", "init", "why")

    def __init__(self, cell, fused):
        self.cell = cell
        self.fused = fused
        self.tables = []
        self.targets = set()
        self.init = 0
        self.why = None

    def resolve(self, mem0, tabs, w):
        """Discharge the premise over every definition; sets ``why`` on refusal."""
        self.init = mem0[self.cell] | (mem0[(self.cell + 1) & 0xFFFF] << 8)
        if not self.fused:
            self.why = "the lo/hi pair did not fuse (rung d)"
        elif w.wild:
            self.why = "a store at an unproven address may write the pointer"
        elif any(self._hit(lo, hi) for lo, hi in w.spans):
            self.why = "another store may write the pointer"
        else:
            for val in w.defs.get(self.cell, ()):
                self.why = self._entry_targets(mem0, tabs, val, w.wide)
                if self.why is not None:
                    break
        return self

    def _hit(self, lo, hi):
        """Whether a store span reaches the pair, its own word store excepted."""
        if lo == self.cell and hi == self.cell + 1:
            return False
        return lo <= self.cell + 1 and self.cell <= hi

    def _entry_targets(self, mem0, tabs, val, wide):
        """Absorb one definition's target words, or return its refusal."""
        ent = _entry(val)
        if ent is None:
            return "a definition is not a lo/hi partner-table entry read"
        lob, hib, idx = ent
        glo, ghi = tabs.at(lob), tabs.at(hib)
        if glo is None or ghi is None:
            return "reload table %s is not declared" % G.addr_name(lob)
        (dlo, off), (dhi, ohi) = glo, ghi
        if dlo["role"] != ("lo", dhi["base"]) or dhi["role"] != ("hi", dlo["base"]) or off != ohi:
            return "%s/%s is not a declared lo/hi partner pair" % (
                G.addr_name(lob),
                G.addr_name(hib),
            )
        if dlo["mut"] or dhi["mut"]:
            return "reload table %s has play-written offsets" % G.addr_name(dlo["base"])
        end = min(dlo["size"], dhi["size"], off + _bound(idx, wide) + 1)
        self.tables.append((lob, hib, end - off, frameproc._fmt(idx)))
        lb, hb = dlo["base"], dhi["base"]
        self.targets.update(mem0[lb + j] | (mem0[hb + j] << 8) for j in range(off, end))
        return None


class _Site:
    """One deref address: the pointer's premise plus this site's row bound."""

    __slots__ = ("ptr", "addr", "idx", "count", "bound", "why")

    def __init__(self, ptr, addr, idx, wide):
        self.ptr = ptr
        self.addr = addr
        self.idx = idx
        self.count = 1
        self.bound = 0 if idx is None else _bound(idx, wide)
        self.why = ptr.why
        if self.why is None and self.bound > _ROW:
            self.why = "row index bound $%04X exceeds one row" % self.bound

    def key(self):
        """Deterministic ordering key: the pointer cell and the index text."""
        return (self.ptr.cell, self.text())

    def text(self):
        """The row index as the emitter writes it, empty for a bare deref."""
        return "" if self.idx is None else frameproc._fmt(self.idx)

    def blocks(self):
        """The words the pointer may hold: every declared entry, and the image's own."""
        return sorted(set(self.ptr.targets) | {self.ptr.init})

    def source(self):
        """``(the address the proof supplies, refusal)`` for the provenance rule (spec 4.6).

        One target block is one address, so the base is the proof's constant and only
        the row evaluates; two or more is an address space and refuses."""
        if self.why is not None:
            return None, self.why
        blocks = self.blocks()
        if len(blocks) != 1:
            return None, "the proof names %d target blocks, not one address" % len(blocks)
        expr = _sub(self.addr, blocks[0])
        if not frameproc.pure(expr):
            return None, "the row index reads memory"
        return expr, None

    def src_proof(self):
        """The provenance record: the proven address, or why the proof supplies none."""
        body = "deref source *%s[%s] at %d site(s)" % (
            G.addr_name(self.ptr.cell),
            self.text(),
            self.count,
        )
        expr, why = self.source()
        if expr is None:
            return Proof(self.ptr.cell, "deref-src", "refused", (), "%s; %s" % (body, why))
        base = self.blocks()[0]
        return Proof(
            self.ptr.cell,
            "deref-src",
            "resolved",
            (base,),
            "%s; block $%04X, address $%04X..$%04X"
            % (body, base, base, (base + self.bound) & 0xFFFF),
        )

    def proof(self):
        """The rung-(f) proof record: table, definitions, target set, row bound."""
        ptr = self.ptr
        tabs = " ".join(
            "%s/%s[%d]@%s" % (G.addr_name(lo), G.addr_name(hi), n, ix)
            for lo, hi, n, ix in ptr.tables
        )
        body = "pointer deref *%s[%s] at %d site(s); %d definition(s)%s" % (
            G.addr_name(ptr.cell),
            self.text(),
            self.count,
            len(ptr.tables),
            " from " + tabs if tabs else "",
        )
        if self.why is not None:
            return Proof(ptr.cell, "deref", "refused", (), "%s; %s" % (body, self.why))
        blocks = sorted(ptr.targets)
        lemma = "%s; %d table block(s) %s, init $%04X; row index bound $%02X" % (
            body,
            len(blocks),
            "$%04X..$%04X" % (blocks[0], blocks[-1]) if blocks else "-",
            ptr.init,
            self.bound,
        )
        return Proof(ptr.cell, "deref", "resolved", tuple(self.blocks()), lemma)


class _Writes:
    """What the play code may write: word definitions, spans, wild stores."""

    __slots__ = ("defs", "spans", "wild", "wide")

    def __init__(self, procs):
        self.wide, vals = _assigns(procs)
        self.defs, self.spans, self.wild = _writers(procs, self.wide, vals)


def analyse(mem0, decls, procs):
    """Every base-less pointer deref site of ``procs``, premise discharged."""
    tabs = _Tables(decls)
    w = _Writes(procs)
    ptrs, seen = {}, {}
    for _e, _p, _r, stmts in procs:
        for addr in _addrs(stmts):
            got = deref(addr)
            if got is None:
                continue
            cell, idx, fused = got
            ptr = ptrs.get(cell)
            if ptr is None:
                ptr = ptrs[cell] = _Ptr(cell, fused).resolve(mem0, tabs, w)
            site = seen.get(addr)
            if site is None:
                seen[addr] = _Site(ptr, addr, idx, w.wide)
            else:
                site.count += 1
    return sorted(seen.values(), key=_Site.key)


def apply_rung(mem0, decls, procs):
    """Rung (f) over ``procs``: ``(resolved addresses, proven addresses, proofs)``.

    Naming only, exactly as the indexed form of spec 4.2: the trees and every value
    are untouched, so Gate FP cannot move. The second map is the provenance rule of
    spec 4.6 — the sites whose proof names one block, so it supplies an address."""
    sites = analyse(mem0, decls, procs)
    resolved = {s.addr: (s.ptr.cell, s.idx) for s in sites if s.why is None}
    srcs = {s.addr: s.source()[0] for s in sites}
    pinned = {a: e for a, e in srcs.items() if e is not None}
    return resolved, pinned, [s.proof() for s in sites] + [s.src_proof() for s in sites]
