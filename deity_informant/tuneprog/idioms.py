"""S4 peepholes on the SSA form: 6510 idioms become ordinary expressions.

Two rewrites, both semantics-preserving and both re-verified against
:class:`~.ir.Interp`:

* :func:`inline` folds a pure single-use value into its use *inside one block*,
  which is what puts a flag's defining expression under the branch that reads it
  (after :func:`~.ssa.merge_chains` a compare and its branch share a block);
* :func:`fold` then simplifies algebraically -- constants, masks
  (``(x & $7F) & $80`` -> 0, so ``ANC #$7F``'s carry disappears), boolean
  comparisons (``(x == y) == 1`` -> ``x == y``, ``(x == y) == 0`` -> ``x != y``)
  and the 6510's compare shape ``((x - y) & $FF) == 0`` -> ``x == y``, which
  turns compare-then-branch, ``DEC``/``INC``-then-branch and the ``ASL``/``BIT``
  bit tests into relational tests over the values themselves.

``LAX``/``SAX``/``SBX``/``ALR``/``ANC`` need no rule of their own: the lifter
already expands them into the same value algebra, and the two rewrites above
reduce them with everything else.

:func:`compound_hints` reports the load-modify-store shapes on a single scalar
region (``x = x + 1`` on one address) for the printer; it does not change the IR.

Public API: :func:`rewrite` (the pass :func:`~.ssa.simplify` calls),
:func:`inline`, :func:`fold`, :func:`compound_hints`.
"""

from __future__ import annotations

from .ir import Bin, Const, Let, Load, MASK, Phi, Store, Var, evalbin
from .ssa import apply_stmt, apply_term, pure, sub_expr, use_counts

CMP = ("==", "!=", "<", "<=")
_ID0 = ("+", "-", "|", "^", "<<", ">>")


def width(e):
    """Byte width of an expression's value (a comparison is one byte, 0 or 1)."""
    t = type(e)
    if t is Bin:
        return 1 if e.op in CMP or e.op == "carry" else e.w
    return e.w


def _isbool(e):
    return type(e) is Bin and e.op in CMP


def _neg(e):
    if e.op == "==":
        return Bin("!=", e.a, e.b, e.w)
    if e.op == "!=":
        return Bin("==", e.a, e.b, e.w)
    if e.op == "<":
        return Bin("<=", e.b, e.a, e.w)
    return Bin("<", e.b, e.a, e.w)


def _noload(e):
    t = type(e)
    if t is Load:
        return False
    return _noload(e.a) and _noload(e.b) if t is Bin else True


def fold(e):
    """Algebraic simplification of one expression node (children already folded)."""
    if type(e) is not Bin:
        return e
    a, b, op, w = e.a, e.b, e.op, e.w
    if type(a) is Const and type(b) is Const:
        return Const(evalbin(op, a.v, b.v, w), width(e))
    if type(b) is not Const:
        return e
    v = b.v
    if v == 0 and op in _ID0:
        return a
    if op == "&":
        if v == 0:
            return Const(0, w)
        if v == MASK[w] and width(a) <= w:
            return a
        if type(a) is Bin and a.op == "&" and type(a.b) is Const:
            return Bin("&", a.a, Const(a.b.v & v, w), w)
    if op in ("|", "^") and type(a) is Bin and a.op == op and type(a.b) is Const:
        return Bin(op, a.a, Const(evalbin(op, a.b.v, v, w), w), w)
    if op in ("+", "-") and type(a) is Bin and a.op in ("+", "-") and a.w == w:
        if type(a.b) is Const:
            k = (a.b.v if a.op == "+" else -a.b.v) + (v if op == "+" else -v)
            return Bin("+", a.a, Const(k & MASK[w], w), w)
    if op in ("==", "!=") and _isbool(a) and v in (0, 1):
        return a if (v == 1) == (op == "==") else _neg(a)
    if op in ("==", "!=") and v == 0 and type(a) is Bin and a.op == "-" and a.w == 1:
        if width(a.a) == 1 and width(a.b) == 1:
            return Bin(op, a.a, a.b, 1)
    if op in ("==", "!=") and v == 0 and type(a) is Bin and a.op == "|" and type(a.b) is Const:
        if a.b.v:  # ORA #imm with a bit set is never zero: a known-flag branch
            return Const(0 if op == "==" else 1, 1)
    return e


def foldall(proc):
    """Fold every expression in ``proc``; returns the number of nodes changed."""
    n = [0]

    def fn(e):
        r = fold(e)
        n[0] += r is not e
        return r

    for b in proc.blocks.values():
        for s in b.stmts:
            apply_stmt(s, fn)
        apply_term(b.term, fn)
    return n[0]


def _size(e):
    """Operator count of an expression (leaves are free)."""
    return 1 + _size(e.a) + _size(e.b) if type(e) is Bin else 0


def _consumer(avail, hits):
    """Substitutes an available value into every use of it in this block."""

    def fn(e):
        hit = avail.get(e.n) if type(e) is Var else None
        if hit is None:
            return e
        hits[0] += 1
        return hit

    return fn


def inline(proc, limit=3):
    """Fold a pure, load-free value into its uses in the same block.

    A single use always moves; a small expression (at most ``limit`` operators --
    the shape a flag definition has) also moves into several uses, which is what
    puts a compare under both the branch that reads it and the value it returns.
    Definitions left without uses are removed by :func:`~.ssa.dce`.
    """
    uses = use_counts(proc)
    hits = [0]
    for b in proc.blocks.values():
        avail = {}
        fn = _consumer(avail, hits)
        for s in b.stmts:
            if type(s) is not Phi:
                apply_stmt(s, fn)
            if type(s) is Let and pure(s.e) and _noload(s.e):
                if uses[s.n] == 1 or _size(s.e) <= limit:
                    avail[s.n] = s.e
        apply_term(b.term, fn)
    return hits[0]


def rewrite(proc):
    """The S4 peephole pass: inline single uses, then simplify."""
    return inline(proc) + foldall(proc)


def compound_hints(proc):
    """``[(block, index, region)]`` load-modify-store on one scalar (printer hint)."""
    out = []
    for lbl, b in proc.blocks.items():
        vals = {s.n: s.e for s in b.stmts if type(s) is Let}
        for i, s in enumerate(b.stmts):
            if type(s) is not Store or type(s.a) is not Const:
                continue
            seen = set()
            _expand(s.v, vals, seen)
            if any(
                type(e) is Load and type(e.a) is Const and e.a.v == s.a.v and e.r == s.r
                for e in seen
            ):
                out.append((lbl, i, s.r))
    return out


def _expand(e, vals, seen):
    def fn(x):
        seen.add(x)
        if type(x) is Var and x.n in vals:
            return _expand(vals[x.n], vals, seen)
        return x

    return sub_expr(e, fn)
