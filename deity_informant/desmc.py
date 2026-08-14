"""Self-modification removed: a patched code byte becomes an ordinary state cell.

frameprog has no SMC concept (docs/frameprog.md 2), so the code bytes an emitted access
names are relocated by one displacement onto free pages, seeded from the image. No
statement then stores into executable memory; ``frameval`` faults on one that does.
"""

from __future__ import annotations

from . import datadecl
from . import expr as E
from . import frameproc
from . import grammar as G
from . import streams as ST
from .lifter import MODE_LEN, OPS

_PAGE = 0x100
_IO = range(0xD0, 0xE0)  # the IO window a relocated cell may never land in
# A destination is preferred low: an over-approximated index span reaches upward, so
# a cell under every table base collides with none of them (framefuse._may_read).
_ORDER = tuple(list(range(0x02, 0xD0)) + list(range(0xE0, 0x100)))


def code_bytes(model):
    """Every byte of every executed instruction: the artifact's executable memory."""
    return set(datadecl._code_bytes(model))


def executable(prog):
    """The same set off an emitted program: ``evidence { code }`` against the image.

    A parsed artifact carries no model, so the invariant is checkable from the text
    alone: a dispatch cell's opcodes are its declared set, any other pc's the image."""
    mem0, out = prog.mem0, set()
    for pc in prog.evidence.get("code", ()) or ():
        for op in prog.dispatch.get(pc) or (mem0[pc],):
            out.update((pc + i) & 0xFFFF for i in range(MODE_LEN[OPS[op][1]]))
    return out


def _sub_const(n, old, new):
    """``n`` with the width-2 const leaf ``old`` replaced by ``new`` (the base)."""
    if n[0] == "const":
        return ("const", new, n[2]) if n[1] == old and n[2] == 2 else n
    if n[0] == "op":
        return (n[0], n[1], tuple(_sub_const(c, old, new) for c in n[2]), n[3])
    if n[0] == "mem":
        return ("mem", _sub_const(n[1], old, new), n[2])
    return n


def _deloc(n):
    """``n`` with each local leaf at the largest value its own width can hold.

    ``streams._idx_hi`` bounds a model expression, where no local occurs; a frameprog
    index carries them, and a local's width is the only bound it has."""
    if n[0] == "loc":
        w = frameproc.loc_width(n)
        return ("const", E.mask(w), w)
    if n[0] == "op":
        return ("op", n[1], tuple(_deloc(c) for c in n[2]), n[3])
    return n


def _named(name, symbols):
    """The address a state field name stands for, alias or canonical spelling."""
    for a, n in symbols.items():
        if n == name:
            return a
    return G.name_addr(name)


def _vacant(mem0, src, dst):
    """Whether the destination page holds nothing but what this relocation puts there.

    The image is read, not just the evidence: a page the evidence never names may
    still carry bytes some path loads. A re-emission finds its own copy already in
    place, which is why the test admits exactly that and the choice stays stable."""
    lo, hi = dst << 8, (dst << 8) + _PAGE
    return mem0[lo:hi] == bytes(_PAGE) or mem0[lo:hi] == mem0[src << 8 : (src << 8) + _PAGE]


def _run(free, mem0, lo, n):
    """The preferred run of ``n`` vacant free pages for source pages ``lo..lo+n``."""
    for p in _ORDER:
        want = range(p, p + n)
        if all(q in free for q in want) and all(
            _vacant(mem0, lo + i, q) for i, q in enumerate(want)
        ):
            free -= set(want)
            return p
    return None


def _clusters(spans):
    """``spans`` as maximal runs of consecutive pages: one displacement each.

    A span is contiguous and no wider than a page plus one, so it lies inside one
    run; separate runs then need no free pages for the gap between them."""
    pages = sorted({p for lo, hi in spans for p in range((lo >> 8), (hi >> 8) + 1)})
    out = []
    for p in pages:
        if out and p == out[-1][1] + 1:
            out[-1] = (out[-1][0], p)
        else:
            out.append((p, p))
    return out


class Relocation:
    """The de-SMC rewrite of one model: what the code bytes move by, and what refuses.

    One displacement moves every named code byte, so every index offset an access uses
    survives it; the bytes between them move too, so a span reads the image's own. The
    free pages come off the evidence and never off the image, so a rebuild picks them."""

    def __init__(self, model, decls=()):
        self.code = code_bytes(model)
        self.data = set(getattr(model, "written", None) or ()) - self.code
        used = {a >> 8 for a in self.code | self.data}
        used |= {a >> 8 for _pc, a in getattr(model, "reads", None) or ()}
        for d in decls:
            used |= {a >> 8 for a in range(d["base"], d["base"] + d["size"])}
        self.free = {p for p in _ORDER if p not in used and p not in _IO}
        self.spans = []  # (first, last) code byte each emitted access may reach
        self.disp = {}  # source page -> the displacement its cluster moves by
        self.refusals = {}  # cause -> the bases left in place
        self.moved = False

    def refuse(self, cause, base):
        self.refusals.setdefault(cause, set()).add(base)

    def reach(self, n, width):
        """``(base, last)`` code bytes an access at ``n`` may reach, else None.

        The bound is the index's own width, so the span over-approximates; what an
        access may really reach is the guard's premise (``frameval.Relocated``), which
        faults at the copy of any play-written cell the instruction stream did not hold."""
        base, idx = frameproc.addr_split(n)
        if base is None or base not in self.code:
            return None
        hi = 0 if idx is None else ST._idx_hi(_deloc(idx))
        last = min(base + hi + width - 1, 0xFFFF)
        return base, last

    def addr(self, n, width):
        """Record ``n``'s reach while planning; substitute its base while rewriting."""
        got = self.reach(n, width)
        if got is None:
            return n
        if not self.moved:
            self.spans.append(got)
            return n
        return _sub_const(n, got[0], self.cell(got[0]))

    def expr(self, n):
        """``n`` with every memory read at a code base carried by the displacement."""
        if n[0] == "mem":
            return ("mem", self.addr(self.expr(n[1]), n[2]), n[2])
        if n[0] == "op":
            return (n[0], n[1], tuple(self.expr(c) for c in n[2]), n[3])
        return n

    def cell(self, a):
        """The address ``a`` moved to, or ``a`` itself where it is not a moved byte."""
        return a + self.disp.get(a >> 8, 0) if self.moved and a in self.code else a

    def stmt(self, s):
        """One statement relocated; ``opsw``/``igoto`` name their cell positionally."""
        k = s[0]
        if k == "st":
            return ("st", self.addr(self.expr(s[1]), G.store_width(s[2])), self.expr(s[2])) + s[3:]
        if k == "opsw" and s[1] in self.code:
            return ("opsw", self.addr(("const", s[1], 2), 1)[1], s[2])
        if k == "igoto" and s[2] is None and s[1] in self.code:
            return ("igoto", self.addr(("const", s[1], 2), 2)[1], s[2])
        return frameproc._map_exprs(s, self.expr)

    def seq(self, stmts):
        """Rewrite a statement list in place, labelling every relocated switch site."""
        i = 0
        while i < len(stmts):
            s = stmts[i]
            got = self.stmt(s)
            if got[0] == "opsw" and got[1] != s[1] and not (i and stmts[i - 1] == ("label", s[1])):
                stmts.insert(i, ("label", s[1]))  # the pc the switch stood at is still a target
                i += 1
            stmts[i] = got
            for body in frameproc._stmt_bodies(got):
                self.seq(body)
            i += 1

    def allocate(self, mem0):
        """Claim a free run per cluster of pages the plan named; False where none fit."""
        for lo, hi in _clusters(self.spans):
            page = _run(self.free, mem0, lo, hi - lo + 1)
            if page is None:
                self.refuse("no-free-run", lo << 8)
                continue
            for q in range(lo, hi + 1):
                self.disp[q] = (page - lo) << 8
        self.moved = bool(self.disp)
        return self.moved

    def blocks(self):
        """``[(first, last, destination)]``: the moved page runs, as the artifact says."""
        out = []
        for lo, hi in _clusters(self.spans):
            if lo in self.disp:
                out.append(((lo << 8), (hi << 8) + _PAGE - 1, (lo << 8) + self.disp[lo]))
        return out

    def seed(self, mem0):
        """``mem0`` with each moved page carrying the bytes the page it left holds.

        The whole page moves, so a span reads the image's own byte past the patched
        ones; a cell the play phase writes is the one exception, and the guard
        (``frameval.Relocated``) sends that access back to the cell itself."""
        if not self.moved:
            return mem0
        out = bytearray(mem0)
        for src, d in self.disp.items():
            out[(src << 8) + d : (src << 8) + d + _PAGE] = mem0[src << 8 : (src << 8) + _PAGE]
        return bytes(out)


def apply_rung(model, decls, procs, state, symbols):
    """De-SMC ``procs`` in place; returns ``(relocation, state fields, symbols)``."""
    rel = Relocation(model, decls)
    for _entry, _params, _rets, stmts in procs:
        rel.seq(stmts)
    if not rel.allocate(model.mem0):
        return rel, state, symbols
    for _entry, _params, _rets, stmts in procs:
        rel.seq(stmts)
    out = []
    for f in state:
        a = _named(f[0], symbols)
        moved = a is not None and rel.cell(a) != a and f[0] == G.addr_name(a)
        out.append((G.addr_name(rel.cell(a)),) + tuple(f[1:]) if moved else f)
    return rel, out, {rel.cell(a): n for a, n in symbols.items()}
