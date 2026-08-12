"""frameptr: rung (f)'s pointer resolution, the residue rung (d) left (spec 4.4).

``mem[P + i]`` resolves when every definition of the pointer state field loads its
halves from a declared ``lo``/``hi`` partner table ``T``: the address is ``T[k] + i``,
row ``i`` of the block ``k`` names, the target set read from the declared extent. Where
that set is one block the address is also a store's source cell (spec 4.6).
"""

from __future__ import annotations

from . import datadecl
from . import expr as E
from . import framefuse as FU
from . import frameproc
from . import grammar as G
from . import streams as ST
from .structured import Proof

_ROW = 0xFF  # a resolved deref reads one byte row of its target block
_WEB = 16  # save cells one pointer web closes over


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
    if frameproc.is_op(addr, "INT_ADD", 2, 2):
        for a, b in frameproc.commuted(addr[2]):
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


def const_word(v):
    """The constant value ``v`` names, else None; packed byte lanes included.

    A pointer set to a literal block arrives as ``zext2($60) | zext2($15) << 8``
    once the lanes fold, so the row is constant even where no ``const`` node is."""
    if v[0] == "const":
        return v[1]
    if v[0] != "op":
        return None
    m = E.mask(frameproc.loc_width(v))
    if v[1] in ("INT_ZEXT", "COPY"):
        got = const_word(v[2][0])
        return None if got is None else got & m
    kids = [const_word(c) for c in v[2]]
    if any(c is None for c in kids):
        return None
    if v[1] == "INT_OR":
        out = 0
        for c in kids:
            out |= c
    elif v[1] == "INT_ADD":
        out = sum(kids)
    elif v[1] == "INT_AND":
        out = kids[0]
        for c in kids[1:]:
            out &= c
    elif v[1] == "INT_LEFT" and len(kids) == 2:
        out = kids[0] << kids[1]
    else:
        return None
    return out & m


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
def _span(addr, wide, vals, words, chase=True):
    """``(lo, hi)`` a store at ``addr`` may reach, else None (unprovable).

    A deref of a pointer whose word set the registry closes reaches that set plus
    the row; ``base + i`` is the declared-index form; ``x | K`` is the stack; a
    local address is the union over its assignments; a modular one keeps its page."""
    if addr[0] == "const" and addr[2] == 2:
        return addr[1], addr[1]
    got = deref(addr)
    if got is not None:
        ws = words.get(got[0])
        if ws:
            return min(ws), max(ws) + (0 if got[1] is None else _bound(got[1], wide))
        return None
    got = frameproc._index_of(addr)
    if got is not None:
        return (0, got[2] - 1) if got[2] else (got[0], got[0] + _bound(got[1], wide))
    if frameproc.is_op(addr, "INT_OR", arity=2):
        ks = [c for c in addr[2] if c[0] == "const"]
        rest = [c for c in addr[2] if c[0] != "const"]
        if len(ks) == 1:
            return ks[0][1], ks[0][1] + _bound(rest[0], wide)
    if addr[0] == "loc" and chase:
        got = [_span(v, wide, vals, words, False) for v in vals.get(addr[1], ())]
        if got and None not in got:
            return min(s[0] for s in got), max(s[1] for s in got)
    return None


def _word_defs(procs):
    """The word values stored per cell, keyed by the cell each word store names."""
    defs = {}
    for _e, _p, _r, stmts in procs:
        for s in FU.stmts_of(stmts):
            if s[0] == "st" and s[1][0] == "const" and s[1][2] == 2 and G.store_width(s[2]) == 2:
                defs.setdefault(s[1][1], []).append(s[2])
    return defs


def _closed_words(defs, mem0, tabs, wide):
    """``{cell: every word it may hold}`` where the registry closes the set.

    A declared const lo/hi row and a constant are the two closed shapes; the image
    word joins them, since a deref before the first definition reads ``mem0``. This
    is what bounds a deref store off the registry -- computed, never observed."""
    out = {}
    for cell, vals in defs.items():
        words = {mem0[cell] | (mem0[(cell + 1) & 0xFFFF] << 8)}
        for v in vals:
            got, tab, _why = _entry_words(mem0, tabs, v, wide)
            if tab is None:
                got = const_word(v)
                if got is None:
                    break
                words.add(got & 0xFFFF)
            else:
                words |= got
        else:
            out[cell] = words
    return out


def _writers(procs, wide, vals, words):
    """``(spans a store may reach, wild store)`` over every store of ``procs``."""
    spans, wild_store = [], False
    for _e, _p, _r, stmts in procs:
        for s in FU.stmts_of(stmts):
            if s[0] != "st":
                continue
            got = _span(s[1], wide, vals, words)
            if got is None:
                wild_store = True
            else:
                spans.append((got[0], got[1] + G.store_width(s[2]) - 1))
    return spans, wild_store


def _leg(half):
    """``(base address, index)`` of one byte-wide table read, else None.

    A modular read is no row of the table at its base -- the wrap picks another --
    so it names no leg."""
    if half[0] != "mem" or half[2] != 1:
        return None
    got = frameproc._index_of(half[1])
    if got is not None and got[2] == 0:
        return got[:2]
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


def _entry_words(mem0, tabs, val, wide):
    """``(target words, table record, refusal)`` for one partner-table entry read.

    The words are read out of ``mem0`` at the *declared* extent -- the rows the
    index bound reaches, never the trace -- and a ``mut`` offset refuses, since
    ``mut`` is exactly the play-written lane the const claim excludes (#61)."""
    ent = _entry(val)
    if ent is None:
        return None, None, "a definition is not a lo/hi partner-table entry read"
    lob, hib, idx = ent
    glo, ghi = tabs.at(lob), tabs.at(hib)
    if glo is None or ghi is None:
        return None, None, "reload table %s is not declared" % G.addr_name(lob)
    (dlo, off), (dhi, ohi) = glo, ghi
    if dlo["role"] != ("lo", dhi["base"]) or dhi["role"] != ("hi", dlo["base"]) or off != ohi:
        why = "%s/%s is not a declared lo/hi partner pair" % (G.addr_name(lob), G.addr_name(hib))
        return None, None, why
    if dlo["mut"] or dhi["mut"]:
        return None, None, "reload table %s has play-written offsets" % G.addr_name(dlo["base"])
    end = min(dlo["size"], dhi["size"], off + _bound(idx, wide) + 1)
    lb, hb = dlo["base"], dhi["base"]
    words = {mem0[lb + j] | (mem0[hb + j] << 8) for j in range(off, end)}
    return words, (lob, hib, end - off, frameproc._fmt(idx)), None


def _row_declared(tabs, base):
    """True where ``base`` names a row of a declared datum with no ``mut`` offset.

    No bound is asked of the index: a lane replacement makes no target claim for one
    to hold up, so what the registry is asked is only that the row is const data."""
    at = tabs.at(base)
    return at is not None and not at[0]["mut"]


def _cell_reads(v):
    """Every plain const-address memory read under ``v``, as ``(base, width)``."""
    out, stack = [], [v]
    while stack:
        x = stack.pop()
        if x[0] == "mem":
            if x[1][0] == "const" and x[1][2] == 2:
                out.append((x[1][1], x[2]))
            stack.append(x[1])
        elif x[0] == "op":
            stack.extend(x[2])
    return out


def _web_value(v, cells):
    """True where ``v``'s every memory read is a plain read of a web cell.

    An advance, a lane-wise step and a restore all wear this shape: the value is a
    function of cells the web already owns, so the store making it is the web's own
    maintenance and not a third writer."""
    stack, read = [v], False
    while stack:
        x = stack.pop()
        if x[0] == "mem":
            base, idx = frameproc.addr_split(x[1])
            if base is None or idx is not None or any(base + o not in cells for o in range(x[2])):
                return False
            read = True
        elif x[0] == "op":
            stack.extend(x[2])
        elif x[0] not in ("const", "loc"):
            return False
    return read


class _Ptr:
    """One pointer state field: the definitions reaching it and what they prove."""

    __slots__ = (
        "cell",
        "fused",
        "tables",
        "targets",
        "init",
        "why",
        "roots",
        "cells",
        "maint",
        "ndefs",
    )

    def __init__(self, cell, fused):
        self.cell = cell
        self.fused = fused
        self.tables = []
        self.targets = set()
        self.init = 0
        self.why = None
        self.roots = (cell,)
        self.cells = {cell, cell + 1}
        self.maint = 0
        self.ndefs = 0

    def resolve(self, mem0, tabs, w):
        """Discharge the premise over every definition; sets ``why`` on refusal."""
        self.init = mem0[self.cell] | (mem0[(self.cell + 1) & 0xFFFF] << 8)
        if not self.fused:
            self.why = "the lo/hi pair did not fuse (rung d)"
            return self
        self.roots, self.cells = self._close(w.defs)
        self.ndefs = sum(len(w.defs.get(r, ())) for r in self.roots)
        if w.wild:
            self.why = "a store at an unproven address may write the pointer"
        elif any(self._hit(lo, hi) for lo, hi in w.spans):
            self.why = "another store may write the pointer"
        else:
            for root in self.roots:
                for val in w.defs.get(root, ()):
                    self.why = self._absorb(mem0, tabs, val, w.wide, root == self.cell)
                    if self.why is not None:
                        return self
        return self

    def _close(self, defs):
        """``(web roots, web cells)``: the pair, plus the save cells it restores from.

        A definition the pair's own cells do not explain may still be the web's, one
        hop out: the cell it reads joins the web and answers for its own definitions,
        which is the held-value closure 2a runs, re-asked over the word stores here."""
        roots, cells = [self.cell], {self.cell, self.cell + 1}
        for _round in range(_WEB):
            held = {
                b
                for r in roots
                for v in defs.get(r, ())
                if not _web_value(v, cells) and const_word(v) is None and _entry(v) is None
                for b, _w in _cell_reads(v)
                if b in defs and b not in cells
            }
            if not held or len(roots) + len(held) > _WEB:
                return tuple(roots), cells
            roots += sorted(held)
            cells |= {b + o for b in held for o in (0, 1)}
        return tuple(roots), cells

    def _hit(self, lo, hi):
        """Whether a store span reaches the web, each root's own word store excepted."""
        if any(lo == c and hi == c + 1 for c in self.roots):
            return False
        return any(lo <= c + 1 and c <= hi for c in self.roots)

    def _absorb(self, mem0, tabs, val, wide, root):
        """One definition: a declared row, a constant, or the web's own maintenance."""
        words, tab, why = _entry_words(mem0, tabs, val, wide)
        if tab is not None:
            self.tables.append(tab)
            if root:
                self.targets |= words
            return None
        k = const_word(val)
        if k is not None:
            if root:
                self.targets.add(k & 0xFFFF)
            return None
        if _web_value(FU.unlane(val, self.cell), self.cells) or self._lane(tabs, val):
            self.maint += 1
            return None
        return why

    def _lane(self, tabs, val):
        """Whether ``val`` replaces one lane of the web's word with the web's own row.

        Rung (d) spells a lone half store as the lane update ``(w & $FF00) | zext2(v)``
        (``framefuse.lane_of``), so a pair whose lanes are reloaded apart never packs one
        entry: the surviving lane is the web's, and the replacement must be its own row."""
        got = FU.lane_of(val, self.cell)
        if got is None:
            return False
        half = FU.unlane(got[1], self.cell)
        if const_word(half) is not None or _web_value(half, self.cells):
            return True
        leg = _leg(half)
        return leg is not None and _row_declared(tabs, leg[0])

    @property
    def open(self):
        """Whether the web's own maintenance leaves the target set unclosed."""
        return bool(self.maint)


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
        the row evaluates; two or more is an address space and refuses, and so does an
        open set -- the web's own maintenance names no block at all."""
        if self.why is not None:
            return None, self.why
        if self.ptr.open:
            return None, "the web's own maintenance leaves the target set open"
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
        body = "pointer deref *%s[%s] at %d site(s); %d definition(s), %d table row(s)%s" % (
            G.addr_name(ptr.cell),
            self.text(),
            self.count,
            ptr.ndefs,
            len(ptr.tables),
            " from " + tabs if tabs else "",
        )
        if self.why is not None:
            return Proof(ptr.cell, "deref", "refused", (), "%s; %s" % (body, self.why))
        blocks = sorted(ptr.targets)
        lemma = "%s; %d table block(s) %s, init $%04X%s; row index bound $%02X" % (
            body,
            len(blocks),
            "$%04X..$%04X" % (blocks[0], blocks[-1]) if blocks else "-",
            ptr.init,
            "; %d maintenance definition(s), target set open" % ptr.maint if ptr.open else "",
            self.bound,
        )
        sites = () if ptr.open else tuple(self.blocks())
        return Proof(ptr.cell, "deref", "resolved", sites, lemma)


class _Writes:
    """What the play code may write: word definitions, spans, wild stores.

    The closed word sets are assumed and then checked: a set bounds the deref stores
    through its cell, the bound is read back against the cell, and a cell some other
    store may still reach loses its set and every bound that rested on it."""

    __slots__ = ("defs", "spans", "wild", "wide", "words")

    def __init__(self, procs, mem0, tabs):
        self.wide, vals = _assigns(procs)
        self.defs = _word_defs(procs)
        self.words = _closed_words(self.defs, mem0, tabs, self.wide)
        for _round in range(len(self.words) + 1):
            self.spans, self.wild = _writers(procs, self.wide, vals, self.words)
            bad = [c for c in self.words if self._clobbered(c)]
            if not bad:
                return
            for cell in bad:
                del self.words[cell]
        self.spans, self.wild = _writers(procs, self.wide, vals, self.words)

    def _clobbered(self, cell):
        """Whether a store other than the cell's own word store may reach the cell."""
        if self.wild:
            return True
        return any(
            not (lo == cell and hi == cell + 1) and lo <= cell + 1 and cell <= hi
            for lo, hi in self.spans
        )


def analyse(mem0, decls, procs):
    """Every base-less pointer deref site of ``procs``, premise discharged."""
    tabs = datadecl.Regions(decls)
    w = _Writes(procs, mem0, tabs)
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
    """Rung (f) over ``procs``: ``(resolved, block-proved, proven addresses, proofs)``.

    Naming only (spec 4.2's indexed form), so Gate FP cannot move. A web named on its
    own maintenance proves no block set and stays 2b's ⊤ access, its extent the
    observed one; the third map is spec 4.6's, the sites whose proof names one address."""
    sites = analyse(mem0, decls, procs)
    lifted = [s for s in sites if s.why is None]
    resolved = {s.addr: (s.ptr.cell, s.idx) for s in lifted}
    blocked = {s.addr: (s.ptr.cell, s.idx) for s in lifted if not s.ptr.open}
    srcs = {s.addr: s.source()[0] for s in sites}
    pinned = {a: e for a, e in srcs.items() if e is not None}
    return resolved, blocked, pinned, [s.proof() for s in sites] + [s.src_proof() for s in sites]
