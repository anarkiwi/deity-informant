"""S4 peepholes on the SSA form: 6510 idioms become ordinary expressions.

Two rewrites, both semantics-preserving and both re-verified against
:class:`~.interp.Interp`:

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

from .ir import Bin, Call, Const, Let, Load, MASK, Phi, Store, Var, evalbin
from .irwalk import apply_stmt, apply_term, loadfree, node_exprs, pure, sub_expr, use_counts

CMP = ("==", "!=", "<", "<=")
BITDEPTH = 8
FLAGS = ("C", "Z", "I", "D", "V", "N")
_ID0 = ("+", "-", "|", "^", "<<", ">>")


def width(e):
    """Byte width of an expression's value (a comparison is one byte, 0 or 1)."""
    t = type(e)
    if t is Bin:
        return 1 if e.op in CMP or e.op == "carry" else e.w
    return e.w


def _isbool(e):
    return type(e) is Bin and e.op in CMP


def negated(e):
    if e.op == "==":
        return Bin("!=", e.a, e.b, e.w)
    if e.op == "!=":
        return Bin("==", e.a, e.b, e.w)
    if e.op == "<":
        return Bin("<=", e.b, e.a, e.w)
    return Bin("<", e.b, e.a, e.w)


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
    if op in ("<<", ">>") and type(a) is Bin and a.op == op and type(a.b) is Const:
        return Bin(op, a.a, Const(a.b.v + v, w), w)  # a shift chain is one shift
    if op in ("|", "^") and type(a) is Bin and a.op == op and type(a.b) is Const:
        return Bin(op, a.a, Const(evalbin(op, a.b.v, v, w), w), w)
    if op in ("+", "-") and type(a) is Bin and a.op in ("+", "-") and a.w == w:
        if type(a.b) is Const:
            k = (a.b.v if a.op == "+" else -a.b.v) + (v if op == "+" else -v)
            return Bin("+", a.a, Const(k & MASK[w], w), w)
    if op in ("==", "!=") and _isbool(a) and v in (0, 1):
        return a if (v == 1) == (op == "==") else negated(a)
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
            if type(s) is Let and pure(s.e) and loadfree(s.e):
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


def _alldefs(proc):
    """``{name: [expressions]}`` over every ``Let``, and the names a call defines."""
    out, calls = {}, set()
    for b in proc.blocks.values():
        for x in b.stmts:
            if type(x) is Let:
                out.setdefault(x.n, []).append(x.e)
            elif type(x) is Call:
                calls.update(x.rets)
    return out, calls


def _one(e):
    return type(e) is Const and e.v == 1


def _onebit(e, defs, depth):
    """True when a value can only be 0 or 1 (a flag register is one by construction)."""
    t = type(e)
    if t is Const:
        return e.v in (0, 1)
    if t is Bin:
        return e.op in CMP or e.op == "carry" or (e.op == "&" and _one(e.b))
    if t is not Var:
        return False
    if e.n.split("#")[0] in FLAGS:
        return True
    return bool(depth and e.n in defs) and all(_onebit(d, defs, depth - 1) for d in defs[e.n])


def bit(e, k, defs, depth=BITDEPTH, need=None):
    """Bit ``k`` of ``e`` as an expression, or ``None`` when it is not decidable.

    A name whose definitions disagree needs a bit of its own; ``need`` collects
    those requests, so the caller can define them where the value is.
    """
    t = type(e)
    if _onebit(e, defs, depth):
        return e if k == 0 else Const(0, 1)
    if t is Const:
        return Const((e.v >> k) & 1, 1)
    if t is Var:
        return _bitvar(e, k, defs, depth, need)
    if t is not Bin or (e.op != "|" and type(e.b) is not Const):
        return None
    if e.op == "|":
        hits = [bit(e.a, k, defs, depth, need), bit(e.b, k, defs, depth, need)]
        rest = [x for x in hits if x is not None and not (type(x) is Const and not x.v)]
        if any(x is None for x in hits) or len(rest) > 1:
            return None
        return rest[0] if rest else Const(0, 1)
    if e.op == "&":
        return Const(0, 1) if not (e.b.v >> k) & 1 else bit(e.a, k, defs, depth, need)
    if e.op == "<<":
        return Const(0, 1) if k < e.b.v else bit(e.a, k - e.b.v, defs, depth, need)
    return bit(e.a, k + e.b.v, defs, depth, need) if e.op == ">>" else None


def _bitvar(e, k, defs, depth, need):
    """Bit ``k`` of a name: its definitions' bit, or a bit of its own."""
    if not depth or e.n not in defs:
        return None
    hits = [bit(d, k, defs, depth - 1, need) for d in defs[e.n]]
    if any(h is None for h in hits):
        return None
    if all(h == hits[0] for h in hits):
        return hits[0]
    if need is None:
        return None
    need.add((e.n, k))
    return Var(_bitname(e.n, k))


def _extract(e):
    """``(name, bit)`` of a ``(x >> k) & 1`` read of one value, or ``None``."""
    if type(e) is not Bin or e.op != "&" or not _one(e.b):
        return None
    x, k = e.a, 0
    while type(x) is Bin and x.op == ">>" and type(x.b) is Const:
        x, k = x.a, k + x.b.v
    return (x.n, k) if type(x) is Var else None


def _scan(e, bits):
    """Record every bit a value is read by."""
    hit = _extract(e)
    if hit is not None:
        bits.setdefault(hit[0], set()).add(hit[1])
        return
    t = type(e)
    if t is Bin:
        _scan(e.a, bits)
        _scan(e.b, bits)
    elif t is Load:
        _scan(e.a, bits)


def _bituses(proc):
    """``{name: the bits something reads of it}``."""
    bits = {}
    for b in proc.blocks.values():
        for s in b.stmts:
            for e in node_exprs(s):
                _scan(e, bits)
        for e in node_exprs(b.term):
            _scan(e, bits)
    return bits


def bitfields(proc):
    """A value read one bit at a time gains that bit where it is defined.

    ``PHP`` packs the flags into a byte and ``PLP`` unpacks them, so defining each
    bit at every push gives the flags back the values that push held; the packed
    byte is then read by nothing and goes with the rest of the dead values.
    """
    defs, calls = _alldefs(proc)
    plan, work = {}, [(n, k) for n, ks in _bituses(proc).items() for k in sorted(ks)]
    while work:
        n, k = work.pop()
        if (n, k) in plan or n in calls or n not in defs:
            continue
        need = set()
        hits = [bit(e, k, defs, need=need) for e in defs[n]]
        if any(h is None for h in hits):
            continue
        plan[(n, k)] = (hits, need)
        work += sorted(need)
    _settle(plan)
    return _emit_bits(proc, defs, plan) if plan else 0


def _settle(plan):
    """Drop the bits that ask for a bit no definition could give."""
    while True:
        gone = [key for key, (_h, need) in plan.items() if not need <= set(plan)]
        if not gone:
            return plan
        for key in gone:
            del plan[key]


def _emit_bits(proc, defs, plan):
    """Define every planned bit at each definition of its value, then read it there."""
    sub = {key: Var(_bitname(*key)) for key in plan}
    for b in proc.blocks.values():
        out = []
        for s in b.stmts:
            ks = [k for (n, k) in plan if type(s) is Let and n == s.n] if type(s) is Let else []
            for k in sorted(ks):
                out.append(Let(_bitname(s.n, k), plan[(s.n, k)][0][defs[s.n].index(s.e)]))
            out.append(s)
        b.stmts = out
        for s in b.stmts:
            apply_stmt(s, _bitreader(sub))
        apply_term(b.term, _bitreader(sub))
    return len(plan)


def _bitreader(sub):
    def fn(e):
        hit = _extract(e)
        return sub.get(hit, e) if hit is not None else e

    return fn


def _bitname(n, k):
    """The name bit ``k`` of a value takes."""
    return "$%s_b%d" % (n.lstrip("$").split("#")[0], k)
