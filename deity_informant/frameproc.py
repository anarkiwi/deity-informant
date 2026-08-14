"""frameproc: procedural serialization for the frameprog dialect.

Pass 1 lifts registers/temporaries to named locals, pass 2 infers procedure
parameters/returns from register liveness, pass 3 renders counter loops as
for-ranges; ``ProcWebs`` is the def-use web, which is the unit a name is not.
"""

from __future__ import annotations

import re

from bisect import bisect_left
from types import MappingProxyType

from . import expr as E
from . import grammar as G
from . import sidprog
from . import structured as C

_REG_LOCAL = {
    0: "a",
    1: "x",
    2: "y",
    3: "sp",
    8: "cflag",
    9: "zflag",
    10: "iflag",
    11: "dflag",
    12: "bflag",
    13: "vflag",
    14: "nflag",
}


def _reg_local(i):
    return _REG_LOCAL.get(i) or "g%d" % i


_ALL_REG_LOCALS = frozenset(_reg_local(i) for i in range(16))
_SP = _REG_LOCAL[3]  # machine state: call/ret move it and stack bytes land through it
_LOCAL_ORDER = {_reg_local(i): i for i in range(16)}


def _by_reg(names):
    return sorted(names, key=_LOCAL_ORDER.get)


# ---- expression helpers (sidprog nodes plus ("loc", name[, width]) leaves) -------
def loc_width(n):
    """Value width of a frameprog expression node: the ONE local-width rule.

    ``("loc", name)`` is one byte and ``("loc", name, w)`` is ``w``; every other
    node states its own width. ``expr.width`` cannot decide a bare local (it reads
    the name), so a loc width is decided here and nowhere else."""
    if n[0] == "loc":
        return n[2] if len(n) > 2 else 1
    return E.width(n)


SHIFT8 = ("const", 8, 1)  # a byte column's own distance: the 6502 shifts no wider


def is_op(n, mn, width=None, arity=None):
    """``n`` is that operation, of that width and operand count where given.

    Every rule below opens by naming the shape it rewrites; spelling that naming
    once means a rule reads as the shape it matches and not as the tuple indices
    the shape happens to occupy."""
    if n[0] != "op" or n[1] != mn:
        return False
    return (width is None or n[3] == width) and (arity is None or len(n[2]) == arity)


def commuted(kids):
    """Both readings of a two-operand node: the tree spells no order, so try each."""
    return (kids, kids[::-1])


def strip_zext(n):
    """``n`` without the reader's own widening -- one zext, which hides nothing.

    The widening is the reader's, not the datum's (grammar ``_index_addr``), so
    exactly one comes off; a second would be a value the program really widened."""
    return n[2][0] if is_op(n, "INT_ZEXT") else n


def shifted_hi(n):
    """The word whose high byte ``n`` shifts down (``v >> 8``), else None."""
    return n[2][0] if is_op(n, "INT_RIGHT") and n[2][1] == SHIFT8 else None


def trunc_lo(v):
    """The low byte of ``v``, spelled as the value graph spells it."""
    return ("op", "COPY", (v,), 1)


def trunc_hi(v):
    """The high byte of ``v``, spelled as the value graph spells it."""
    return ("op", "COPY", (("op", "INT_RIGHT", (v, SHIFT8), 2),), 1)


def trunc_pair(lo, hi):
    """The one word ``lo`` and ``hi`` are the two truncs of, else None.

    ``hi:lo`` of ``V`` is ``V`` only where both halves name the same ``V``; two
    truncs of different words wear the shape and mean nothing."""
    if not is_op(lo, "COPY") or not is_op(hi, "COPY"):
        return None
    v = lo[2][0]
    return v if shifted_hi(hi[2][0]) == v else None


_LEAF = ("mem", "loc")  # a pack side carries a cell, a local, or a trunc of a word


def _le_bytes(n):
    """``(lo side, hi side)`` where ``n`` packs two byte-wide sides into one word.

    The 6502 spells a word this way and only this way, so the shape is read once
    here; every rule that rewrites a pack opens by asking this, then asks only
    what it needs of the two sides it gets back."""
    if not is_op(n, "INT_OR", 2, 2):
        return None
    for hi, lo in commuted(n[2]):
        if not is_op(hi, "INT_LEFT", 2) or hi[2][1][0] != "const" or hi[2][1][1] != 8:
            continue
        mh, ml = strip_zext(hi[2][0]), strip_zext(lo)
        if (mh[0] in _LEAF or mh[1] == "COPY") and (ml[0] in _LEAF or ml[1] == "COPY"):
            return ml, mh
    return None


def le_pack(lo, hi):
    """``hi<<8 | lo`` over two byte values: the word ``_le_bytes`` reads back out."""
    shl = ("op", "INT_LEFT", (("op", "INT_ZEXT", (hi,), 2), SHIFT8), 2)
    return ("op", "INT_OR", (shl, ("op", "INT_ZEXT", (lo,), 2)), 2)


def pair_site(pairs, base, idx):
    """``(hi cell, lo base, index)`` for the pair half at ``base``/``idx``, else None.

    ``pairs`` is the ONE registry, lo base to ``(hi base, size)`` off the decl
    roles. An indexed half names its table's base outright; a scalar half's own
    offset into the columns IS the index the pair rendering reads."""
    if idx is not None:
        got = pairs.get(base)
        return None if got is None else (got[0], base, idx)
    for lo, (hi, size) in pairs.items():
        o = base - lo
        if 0 <= o < size:
            return hi + o, lo, ("const", o, 1)
    return None


def _kids(n):
    if n[0] == "mem":
        return (n[1],)
    if n[0] == "op":
        return n[2]
    return ()


def pure(n):
    """True if the expression reads no memory: consts, locals and ops only.

    A pure address re-evaluates with no side effect and consumes no volatile
    input, so it can be probed again within the statement that used it."""
    k = n[0]
    if k in ("const", "loc"):
        return True
    return k == "op" and all(pure(c) for c in n[2])


def _rebuild(n, kids):
    if n[0] == "mem":
        return ("mem", kids[0], n[2])
    if n[0] == "op":
        return ("op", n[1], tuple(kids), n[3])
    return n


def _locset(n):
    out = set()
    stack = [n]
    while stack:
        x = stack.pop()
        if x[0] == "loc":
            out.add(x[1])
        else:
            stack.extend(_kids(x))
    return out


def _reads_mem(n):
    stack = [n]
    while stack:
        x = stack.pop()
        if x[0] == "mem":
            return True
        stack.extend(_kids(x))
    return False


def _reads_vol(n):
    """True when evaluating ``n`` may read a volatile cell (input order)."""
    stack = [n]
    while stack:
        x = stack.pop()
        if x[0] == "mem" and not sidprog._ld_safe(x[1]):
            return True
        stack.extend(_kids(x))
    return False


def _subst_loc(n, name, repl):
    if n[0] == "loc":
        return repl if n[1] == name else n
    kids = _kids(n)
    if not kids:
        return n
    return _rebuild(n, [_subst_loc(c, name, repl) for c in kids])


_WILD = frozenset(("call", "dcall", "swc", "dbr", "dgoto", "igoto", "label"))
_CYCLIC = frozenset(("loop", "for"))  # a back edge re-reads what the body wrote
_EXITS = frozenset(("cont", "brk"))


def rereads(s):
    """Whether ``s``'s body may run a second time over what the first left.

    A ``loop`` the passes placed as a scope leaves by its own end and continues
    nowhere, so it has no back edge and a definition before it stands inside it
    (the reading ``eqlift_mem`` already takes of one)."""
    return s[0] in _CYCLIC and not (s[0] == "loop" and single_trip(s[1]))


NOIDX = object()  # "the caller is not a store, so no index is shared"
UNRES = object()  # "the base is not named, so only the bits the address sets bound it"


# ---- addresses: the base an access names, and the bytes it may reach -------------
def addr_split(addr):
    """``(const base, index expression)`` of an address, index None when plain.

    The naming form: a modular address is no ``base + index`` the emitter may write
    or a table row be read off, since the row it lands on wraps."""
    if addr[0] == "const" and addr[2] == 2:
        return addr[1], None
    got = _index_of(addr)
    return (got[0], got[1]) if got is not None and got[2] == 0 else (None, None)


def addr_range(addr, width=1):
    """``(base, index, modulus)`` an access of ``width`` bytes makes, else None.

    The straddle guard sits here because only the access knows its width: a wider
    access at a modular address is refused, since a word read at ``$FF`` takes
    ``$0100`` and the ``zp,X`` wrap does not reach it."""
    if addr[0] == "const" and addr[2] == 2:
        return (addr[1], None, 0)
    got = _index_of(addr)
    return None if got is None or (got[2] and width > 1) else got


def packed_cells(n):
    """``(lo base, hi base, shared index)`` of the two cells ``n`` packs, else None.

    Both sides must be byte cells -- a wider side is a value the program really
    widened, not a column -- and both must ride one index, since two columns of
    one datum are read at one row or at no row at all."""
    got = _le_bytes(n)
    if got is None or got[0][0] != "mem" or got[0][2] != 1:
        return None
    if got[1][0] != "mem" or got[1][2] != 1:
        return None
    (bl, il), (bh, ih) = addr_split(got[0][1]), addr_split(got[1][1])
    return None if bl is None or bh is None or il != ih else (bl, bh, il)


class DefsAt:
    """The definitions in force at one statement: a ``Defs`` and a position.

    Read-only by construction, so a predicate may resolve a local without the
    emitted program learning of it (docs/frameprog.md 7.10.3)."""

    __slots__ = ("defs", "bound")

    def __init__(self, defs, bound):
        self.defs, self.bound = defs, bound

    def defn(self, n):
        """What the local names where one definition reaches it, else None.

        ``Defs.resolve`` hands back the local itself at every wall -- a cyclic body
        that may rebind, a ``pcall``-bound name, a definition with no readable
        value -- so a local returning is exactly ⊤."""
        got = self.defs.resolve(n, self.bound)
        return None if got[0] == "loc" else got


def addr_bits(n, env=None):
    """Bits an address may set: every address the expression names is a subset.

    A value fits the width it is read at, which bounds the ``zp,X`` wrap and the
    stack push ``zext2(sp) | $0100`` without either being named as a shape. Given a
    ``DefsAt``, a local is the address its reaching definition spells."""
    m = E.mask(loc_width(n))
    if n[0] == "const":
        return n[1] & m
    if n[0] == "loc":
        got = None if env is None else env.defn(n)
        return m if got is None else addr_bits(got) & m  # the definition's own: width alone
    if n[0] == "op" and n[1] in ("INT_ZEXT", "COPY"):
        return addr_bits(n[2][0], env) & m
    if n[0] == "op" and n[1] in ("INT_OR", "INT_AND") and len(n[2]) == 2:
        a, b = (addr_bits(c, env) for c in n[2])
        return (a | b if n[1] == "INT_OR" else a & b) & m
    return m


def addr_floor(n, env=None):
    """Bits an address must set: every address the expression names is a superset.

    ``addr_bits``' mirror, and the lower bound it cannot give: the stack push
    ``zext2(sp) | $0100`` is guaranteed bit 8, so its reach starts at ``$0100``
    rather than at zero (docs/register-model-lift-impl.md, stage 3 intervals)."""
    m = E.mask(loc_width(n))
    if n[0] == "const":
        return n[1] & m
    if n[0] == "loc":
        got = None if env is None else env.defn(n)
        return 0 if got is None else addr_floor(got) & m
    if n[0] == "op" and n[1] in ("INT_ZEXT", "COPY"):
        return addr_floor(n[2][0], env) & m
    if n[0] == "op" and n[1] in ("INT_OR", "INT_AND") and len(n[2]) == 2:
        a, b = (addr_floor(c, env) for c in n[2])
        return (a | b if n[1] == "INT_OR" else a & b) & m
    return 0


def lattice(n):
    """The interval the ``mem_rules`` rewrites derive for ``n`` itself, else None.

    A magnitude bound where a rule states one, wrap guard included: an address the bits
    cannot bound (``zext2(y) + $A5`` sets every bit its width has) has a bound here."""
    if n[0] == "const":
        v = n[1] & E.mask(n[2])
        return v, v
    if n[0] != "op":
        return None
    mn, kids, w = n[1], n[2], n[3]
    if mn == "INT_ZEXT":
        return 0, 255
    if mn == "INT_AND" and len(kids) == 2 and kids[1][0] == "const":
        return 0, kids[1][1] & E.mask(w)
    if mn == "INT_ADD" and len(kids) == 2:
        got = [lattice(k) for k in kids]
        if all(got) and got[0][1] + got[1][1] <= E.mask(w):
            return got[0][0] + got[1][0], got[0][1] + got[1][1]
    if mn == "INT_LEFT" and len(kids) == 2 and kids[1][0] == "const":
        got, k = lattice(kids[0]), kids[1][1]
        if got and (got[1] << k) <= E.mask(w):
            return got[0] << k, got[1] << k
    return None


def addr_reach(n, env=None):
    """The interval an address expression may reach: the tighter of the two readings.

    ``addr_floor``/``addr_bits`` bound the bits an address sets, ``lattice`` its
    magnitude. Each is sound alone, so the tighter of the two is -- which
    ``addr_bits`` may not do itself, its ``INT_OR`` recursion needing masks."""
    lo, hi = addr_floor(n, env), addr_bits(n, env)
    got = lattice(n)
    return (lo, hi) if got is None else (max(lo, got[0]), min(hi, got[1]))


def span(base, idx, regions, mod=0):
    """Tightest sound span for an indexed address at ``base``: the ONE span rule.

    An index reaches no further than the declaration holding its base, since the
    lifted program indexes that datum; a modular index is the evidence the access
    leaves its datum, so no declaration bounds one."""
    if idx is None:
        return 0
    full = E.mask(loc_width(idx))
    avail = 0 if mod or base is None or regions is None else regions.avail(base)
    return min(full, avail - 1) if avail > 0 else full


def _flat(base, sp, width, mod):
    """A modular range as the plain interval it cannot leave."""
    if not mod or base + sp + width - 1 < mod:
        return base, sp, width
    return 0, mod - 1, width


def overlaps(a, b):
    """Whether two ranges may intersect; each is ``(base, index, span, width, mod)``.

    The ONE aliasing rule: two ranges carrying one index and one modulus name one
    row apiece, so their spans drop out and the bases decide, ``$14,X``/``$15,X``
    modulo the wrap. Short of that a modular range reaches its whole wrap."""
    (ba, ia, sa, wa, ma), (bb, ib, sb, wb, mb) = a, b
    if isinstance(ia, tuple) and ia == ib and ma == mb:
        d = (ba - bb) % ma if ma else ba - bb
        return -wa < d < wb
    ba, sa, wa = _flat(ba, sa, wa, ma)
    bb, sb, wb = _flat(bb, sb, wb, mb)
    return not (ba + sa + wa - 1 < bb or bb + sb + wb - 1 < ba)


def store_ref(stmt, regions):
    """The range a store writes, else None where its address does not resolve."""
    width = G.store_width(stmt[2])
    got = addr_range(stmt[1], width)
    if got is None:
        return None
    base, idx, mod = got
    return (base, idx, span(base, idx, regions, mod), width, mod)


def store_reach(stmt, regions, env=None):
    """The range a store writes: ``store_ref``, or the interval its address reaches.

    An address the base/index form does not name still bounds the bytes it may
    reach, which holds a zero-page store to ``$00FF`` however unresolvable its index
    is; the floor is the join's own reading, so the range here starts at zero."""
    got = store_ref(stmt, regions)
    if got is not None:
        return got
    return (0, UNRES, addr_reach(stmt[1], env)[1], G.store_width(stmt[2]), 0)


def accesses(s):
    """``(address node, byte width)`` of every load a statement makes, plus its store."""
    out, stack = [], list(_stmt_exprs(s))
    while stack:
        x = stack.pop()
        if x[0] == "mem":
            out.append((x[1], x[2]))
            stack.append(x[1])
        elif x[0] == "op":
            stack.extend(x[2])
    if s[0] == "st":
        out.append((s[1], G.store_width(s[2])))
    return out


_STK_PAGE = ("const", 0x0100, 2)


def sp_addr(k=0, sp=None):
    """``(zext2(sp [+ $k]) | $0100)``: the page-one address the machine reaches at.

    ``sp_disp``'s inverse and the one spelling of it -- the lift writes exactly this
    for ``PHA``/``PLA``, so an address stated here is bounded the way that one is."""
    sp = _SP if sp is None else sp
    inner = ("loc", sp) if not k else ("op", "INT_ADD", (("loc", sp), ("const", k, 1)), 1)
    return ("op", "INT_OR", (("op", "INT_ZEXT", (inner,), 2), _STK_PAGE), 2)


def machine_reads(s, sp=_SP):
    """``(address, width)`` a statement reads that none of its expressions names.

    A ``ret``/``brk`` reads page one through ``sp``, a static ``igoto`` the vector word,
    an ``opsw`` its operand cell; omitting one calls a transfer's own definition unread.
    ``sp`` None is rung (d0')'s drop: a ret then reads the machine's push, no store."""
    k = s[0]
    if k in ("ret", "brk"):
        return () if sp is None else ((sp_addr(1, sp), 2),)
    if k == "igoto":
        return () if s[2] is not None else ((("const", s[1], 2), 2),)
    return ((("const", s[1], 2), 1),) if k == "opsw" else ()


def sp_kept(procs):
    """Whether the program still carries ``sp``: rung (d0') would not drop it.

    A parameter, a return, an update or a read -- the four places ``_strip_sp``
    empties -- so this is the drop's verdict read back off the program."""
    for _e, params, rets, stmts in procs:
        if _SP in set(params) | set(rets):
            return True
        for _env, _i, s in envs(stmts):
            if s[0] == "asg" and s[1] == _SP:
                return True
            if any(_SP in _locset(x) for x in _stmt_exprs(s)):
                return True
    return False


def sp_disp(addr, sp=None):
    """``k`` of a ``(zext2(sp [+ $k]) | $0100)`` address, else None.

    The 6510 pull address, ``or`` rather than add because the byte wraps inside
    page one. ``concretize_stack`` folds it to a cell only where one entry
    ``sp`` flowed there; two call depths join to bot and the spelling survives."""
    sp = _SP if sp is None else sp
    if addr[0] != "op" or addr[1] != "INT_OR" or len(addr[2]) != 2:
        return None
    for a, b in (addr[2], addr[2][::-1]):
        if b != _STK_PAGE or a[0] != "op" or a[1] != "INT_ZEXT":
            continue
        inner = a[2][0]
        if inner == ("loc", sp):
            return 0
        if inner[0] == "op" and inner[1] == "INT_ADD" and len(inner[2]) == 2:
            p, q = inner[2]
            if p == ("loc", sp) and q[0] == "const":
                return q[1] & 0xFF
            if q == ("loc", sp) and p[0] == "const":
                return p[1] & 0xFF
    return None


def sp_delta(v, sp=None):
    """``+/-k`` of an ``sp = (sp +/- $k)`` update, else None."""
    sp = _SP if sp is None else sp
    if v[0] != "op" or len(v[2]) != 2:
        return None
    a, b = v[2]
    if v[1] == "INT_ADD" and b[0] == "const" and a == ("loc", sp):
        return b[1]
    if v[1] == "INT_ADD" and a[0] == "const" and b == ("loc", sp):
        return a[1]
    if v[1] == "INT_SUB" and a == ("loc", sp) and b[0] == "const":
        return -b[1]
    return None


def mem_refs(n):
    """``((base, index, modulus), width)`` of every memory reference under ``n``."""
    out, stack = [], [n]
    while stack:
        x = stack.pop()
        if x[0] == "mem":
            out.append((addr_range(x[1], x[2]), x[2]))
            stack.append(x[1])
        elif x[0] == "op":
            stack.extend(x[2])
    return out


def lane(base, idx, sp, mod=0):
    """The one-byte range a lane occupies."""
    return (base, idx, sp, 1, mod)


def reads(exprs, at, regions):
    """True where evaluating ``exprs`` may load a cell of the range ``at``."""
    for ref, rw in (r for x in exprs for r in mem_refs(x)):
        if ref is None:
            return True
        rb, ri, rm = ref
        if overlaps(at, (rb, ri, span(rb, ri, regions, rm), rw, rm)):
            return True
    return False


def clobbers(stmt, exprs, regions):
    """True where ``stmt`` may change the value of any of ``exprs``."""
    if stmt[0] == "asg":
        return any(stmt[1] in _locset(x) for x in exprs)
    return True if stmt[0] != "st" else disturbs(stmt, exprs, regions)


def disturbs(stmt, exprs, regions):
    """The store may change a value ``exprs`` read.

    Index expressions may only be compared when the read is a direct load here;
    one captured earlier can be structurally equal yet hold a stale index."""
    return False if stmt[0] != "st" else reads(exprs, store_reach(stmt, regions), regions)


def exit_level(s):
    """How many enclosing regions a ``cont``/``brk`` leaves; 1 is the bare form."""
    return s[1] if len(s) > 1 and s[1] is not None else 1


TERMS = frozenset(("cont", "brk", "ret", "goto", "unobs", "dgoto", "igoto"))
_OWN = frozenset(("if", "loop", "for", "opsw", "swg"))  # bodies whose ``ret`` is this list's
_ARMED = frozenset(("swg", "swc"))  # an arm table its computed transfer must stay beside
_ANYCALL = frozenset(("call", "callb", "pcall", "dcall", "swc"))
_PC_ARMS = {"swg": 1, "opsw": 2, "swc": 2}  # where a form carries its arm labels


def bound_pcs(stmts, out=None):
    """The pcs a statement list *defines* as a transfer target, arms included.

    A label is a pc in the grammar, so a list binding one may neither be copied
    (two copies bind it twice) nor spliced (a transfer to it would run past the
    ``ret`` the splice removes)."""
    out = [] if out is None else out
    for s in stmts:
        if s[0] == "label":
            out.append(s[1])
        at = _PC_ARMS.get(s[0])
        if at is not None:
            out += [b[0] for b in s[at] if isinstance(b[0], int)]
        if s[0] == "swc":
            out += [lbl for lbl in s[1] if isinstance(lbl, int)]
        for b in _stmt_bodies(s):
            bound_pcs(b, out)
    return sorted(set(out))


def calls_in(stmts):
    """Whether any statement in ``stmts``, bodies included, is a call."""
    for s in stmts:
        if s[0] in _ANYCALL or any(calls_in(b) for b in _stmt_bodies(s)):
            return True
    return False


def _sp_walk(stmts, at):
    """``(displacement the list leaves, every ``ret`` in it stood at the entry)``."""
    ok = True
    for s in stmts:
        k = s[0]
        if k == "asg" and s[1] == _SP:
            d = sp_delta(s[2], _SP)
            at = None if d is None or at is None else (at + d) & 0xFF
            continue
        if k == "ret":
            ok = ok and at == 0
            continue
        outs = []
        for b in _stmt_bodies(s):
            end, good = _sp_walk(b, at)
            ok = ok and good
            outs.append(end)
        if k in _CYCLIC:
            at = at if outs and outs[0] == at else None
        elif outs:
            at = outs[0] if all(o == outs[0] for o in outs) else None
    return at, ok


def sp_balanced(stmts):
    """Whether every ``ret`` in ``stmts`` stands where the list was entered.

    The build-time reading of framestack's displacement walk: a body returning from
    a displacement it moved returns through a word it moved, so its text spliced at
    the call site takes that ``ret`` for the caller's own (720_Degrees $C31D)."""
    return _sp_walk(stmts, 0)[1]


def _read_locals(stmts, out=None):
    """Every local any statement in ``stmts`` writes or reads, bodies included."""
    out = set() if out is None else out
    for s in stmts:
        if s[0] in ("asg", "for"):
            out.add(s[1])
        elif s[0] == "pcall":
            out.update(s[3])
        for x in _stmt_exprs(s):
            out |= _locset(x)
        for b in _stmt_bodies(s):
            _read_locals(b, out)
    return out


def escapes(stmts):
    """Whether the list may leave by a transfer rather than by its own ``ret``.

    A callee that tail-transfers out returns through the pushed word from wherever it
    landed. A computed transfer its own arm table or static vector answers lands in
    the list itself, so that one leaves nothing behind."""
    for i, s in enumerate(stmts):
        k = s[0]
        nxt = stmts[i + 1][0] if i + 1 < len(stmts) else None
        if k in ("goto", "dbr"):
            return True
        if k in ("dgoto", "igoto") and nxt != "swg" and not (k == "igoto" and s[2] is None):
            return True
        if any(escapes(b) for b in _stmt_bodies(s)):
            return True
    return False


def _brk(level):
    """A ``break`` leaving ``level`` enclosing regions, in the canonical spelling."""
    return ("brk",) if level == 1 else ("brk", level)


def _rets_in(stmts, depth=0, out=None):
    """``(list, index, loops it stands in)`` of every ``ret`` this list returns by.

    A ``swc`` arm and a ``callb`` body are another procedure's text: their ``ret``
    is that callee's edge, not this list's, so the walk does not enter them."""
    out = [] if out is None else out
    for i, s in enumerate(stmts):
        if s[0] == "ret":
            out.append((stmts, i, depth))
        elif s[0] in _OWN:
            for b in _stmt_bodies(s):
                _rets_in(b, depth + (s[0] in _CYCLIC), out)
    return out


def leaves(stmts):
    """Whether control cannot run off the end of ``stmts``.

    A terminator ends it, and so does the arm table of one: every arm of a
    computed transfer leaves by its own end, so the table is that transfer's own
    exits laid out."""
    if not stmts:
        return False
    if stmts[-1][0] in TERMS:
        return True
    return stmts[-1][0] == "swg" and len(stmts) > 1 and stmts[-2][0] in ("dgoto", "igoto")


def scope(stmts):
    """``stmts`` as one single-trip scope every ``ret`` in it leaves by.

    A scope is a ``loop`` whose body always breaks (34e9d96), so several returns
    become one exit with no region production and no label."""
    for lst, i, depth in _rets_in(stmts):
        lst[i] = _brk(depth + 1)
    if not leaves(stmts):
        stmts.append(("brk",))
    return [("loop", stmts)]


def one_exit(procs):
    """Each procedure's several returns as its one own last (8.4).

    The scope opens at the first statement any return stands in. A procedure that
    still calls or still names ``sp`` keeps its returns: one may be a call's edge
    back or a page-one word the machine returns through, not this procedure's exit."""
    for k, (e, params, rets, stmts) in enumerate(procs):
        if calls_in(stmts) or _SP in set(params) | _read_locals(stmts):
            continue
        at = next((i for i, s in enumerate(stmts) if _rets_in([s])), None)
        if at is None or (at == len(stmts) - 1 and stmts[at][0] == "ret"):
            continue
        if stmts[at][0] in _ARMED:  # the computed transfer that arms it stands before it
            at -= 1
        stmts[at:] = scope(stmts[at:]) + [("ret", False)]
        procs[k] = (e, params, rets, stmts)


def hi_first(s):
    """True where a word store emits its high byte before its low byte.

    A store's write order rides as an optional fourth element, set only by rung
    (d) where the pair it merged was written hi then lo. It is a fact about the
    store and not about its index: ``frameval``'s ``stw`` emits ascending unless
    told otherwise, so a store carrying its own order reproduces the frame log
    wherever the index lands, and owes nothing about where that is (§7.10.4).
    Every rebuilder of a store carries ``s[3:]`` through for that reason."""
    return len(s) > 3 and s[3]


def as_written(env, lst, k):
    """``lst[k]`` with its address spelled as the definition in force spells it.

    A local naming an address names the same address at ``k`` only where every
    local that naming reads still holds there what it held where it was written --
    the staleness ``disturbs`` warns of, discharged rather than assumed."""
    s = lst[k]
    if s[0] != "st" or env is None:
        return s
    n, bound = s[1], k
    while n[0] == "loc":
        got = env.value(n[1], bound)
        if got is None or any(env.at(m, got[0]) != env.at(m, k) for m in _locset(got[1])):
            break
        n, bound = got[1], got[0]
    return ("st", n, s[2]) + s[3:]


def hoist(lst, i, j, exprs, regions, env=None):
    """``exprs``, the interval's definitions inlined, and the statement blocking it.

    A read is carried up to ``i`` from where the interval made it, so a statement
    is held only against what is hoisted *past* it: one writing a cell later than
    the read it would spoil is no hazard, and its own definition is not one."""
    at = None
    for k in range(j - 1, i, -1):
        s = lst[k]
        if s[0] == "asg" and any(s[1] in _locset(x) for x in exprs):
            exprs = tuple(_subst_loc(x, s[1], s[2]) for x in exprs)
        elif clobbers(as_written(env, lst, k), exprs, regions):
            at = k
    return exprs, at


def hidden_defs(s):
    """Names ``s`` may define other than as this list's own ``asg``; None means any.

    A nested body's definition, a call's writes and a label control may enter at
    are invisible to a scan of one list, yet each ends the reign of the definition
    before it."""
    if s[0] in _WILD:
        return None
    out = {s[1]} if s[0] in ("asg", "for") else set(s[3]) if s[0] == "pcall" else set()
    for b in _stmt_bodies(s):
        for s2 in b:
            got = hidden_defs(s2)
            if got is None:
                return None
            out |= got
    return out


_COMPUTED = frozenset(("dgoto", "dbr", "igoto", "swg"))  # a jump no list of gotos names
ENTRY = object()  # "nothing is in force, so the name still holds its value at entry"


def _never_binds(stmts, name):
    """No statement of the forest may bind ``name``: it holds its entry value.

    An ``asg``/``for``/``pcall`` return binds it by name; a ``call``, ``dcall``
    or ``swc`` clobbers registers unseen; a jump or a label binds nothing."""
    for s in stmts:
        k = s[0]
        if k in ("call", "dcall", "swc"):
            return False
        if k in ("asg", "for") and s[1] == name:
            return False
        if k == "pcall" and name in s[3]:
            return False
        if any(not _never_binds(b, name) for b in _stmt_bodies(s)):
            return False
    return True


def _label_defs(s):
    """``(names, label pcs)`` that ``s`` may define, or None where a call binds unseen.

    ``hidden_defs`` with the labels held out: a label binds nothing itself, and
    the join (``Defs._verified``) proves what its entries carry."""
    if s[0] == "label":
        return frozenset(), frozenset((s[1],))
    if s[0] in _WILD:
        return None
    names = {s[1]} if s[0] in ("asg", "for") else set(s[3]) if s[0] == "pcall" else set()
    labels = set()
    for b in _stmt_bodies(s):
        for s2 in b:
            got = _label_defs(s2)
            if got is None:
                return None
            names |= got[0]
            labels |= got[1]
    return names, labels


class _Jumps:
    """The procedure's label entries: ``pc -> [(env, position)]`` over its gotos.

    ``computed`` is any jump whose targets no list of gotos names -- an indirect
    goto, a computed branch, a switch goto -- and it refuses every label join."""

    __slots__ = ("computed", "srcs", "labels")

    def __init__(self, root):
        self.computed = False
        self.srcs = {}
        self.labels = set()
        self._scan(root)

    def _scan(self, env):
        for k, s in enumerate(env.lst):
            if s[0] == "goto":
                self.srcs.setdefault(s[1], []).append((env, k))
            elif s[0] == "label":
                self.labels.add(s[1])
            elif s[0] in _COMPUTED:
                self.computed = True
            for b in _stmt_bodies(s):
                self._scan(Defs(b, (env, k), rereads(s)))


class Defs:
    """The local definitions of one statement list, read at the point of the read.

    A statement list is not SSA: ``x0 = x`` captures what ``x`` held there, so
    every lookup carries the reader's position and answers with the definer's. A
    definition the list does not make itself is recorded valueless (``hidden_defs``)."""

    __slots__ = ("defs", "wild", "outer", "cyclic", "lst", "jumps", "foreign")

    def __init__(self, lst, outer=None, cyclic=False, foreign=None):
        self.defs, self.wild, self.jumps = {}, [], None
        self.outer, self.cyclic, self.lst, self.foreign = outer, cyclic, lst, foreign
        for k, s in enumerate(lst):
            if s[0] == "asg":
                self.defs.setdefault(s[1], []).append((k, s[2]))
                continue
            killed = hidden_defs(s)
            if killed is None:
                self.wild.append(k)
                continue
            for name in killed:
                self.defs.setdefault(name, []).append((k, None))

    def at(self, name, bound):
        """``(index, value)`` of the definition in force at ``bound``, else None.

        This list only: the index is a position in it, so a caller stepping to that
        index must not be handed one from an enclosing list. A None ``value`` marks
        a definition whose value cannot be read off, and still names which it is."""
        made = self.defs.get(name)
        k = 0 if made is None else bisect_left(made, (bound,))
        got = made[k - 1] if k else None
        w = bisect_left(self.wild, bound)
        if w and (got is None or self.wild[w - 1] > got[0]):
            return (self.wild[w - 1], None)
        return got

    def _lookup(self, name, bound):
        """``(env, index, value)`` of the definition in force, enclosing lists included.

        Reaching here at all means this list defines nothing for ``name`` before
        ``bound`` and lets control in nowhere before it either -- ``at`` answers
        both. Only a back edge can still overwrite it, from a definition or a label
        *after* the read, so a cyclic body must bind neither.

        A ``for`` header binds its counter over its own body, and is the one
        statement that does: pass 3 lifts the counter's init and step *out* of the
        list, so the body defines nothing for it, and an escape asks the enclosing
        list at the ``for``'s own index -- which ``at`` excludes. Without this the
        counter reads as whatever constant was in force before the loop, which it
        is at no iteration."""
        got = self.at(name, bound)
        if got is not None:
            return (self, got[0], got[1])
        if self.outer is None or (self.cyclic and (self.wild or name in self.defs)):
            return None
        outer, at = self.outer
        if outer.lst[at][:2] == ("for", name):
            return None
        return outer._lookup(name, at)

    def resolve(self, n, bound):
        """The value ``n`` names, following definitions out through enclosing lists."""
        env = self
        while n[0] == "loc":
            got = env._lookup(n[1], bound)
            if got is None or got[2] is None:
                return n
            env, bound, n = got
        return n

    def value(self, name, bound):
        """``at``, but None wherever the definition in force has no readable value."""
        got = self.at(name, bound)
        return None if got is None or got[1] is None else got

    def _hits(self, k, cell, regions):
        """True where the store at ``k`` may reach ``cell``.

        An address the base/index form does not name still bounds the bytes it can
        reach by the bits it can set (``addr_bits``)."""
        s = self.lst[k]
        got = store_ref(s, regions)
        if got is not None:
            return overlaps((cell, NOIDX, 0, 1, 0), got)
        m = addr_bits(self.resolve(s[1], k))
        return any((cell - d) & ~m == 0 for d in range(G.store_width(s[2])))

    def _writes(self, k, cell, regions):
        """True where statement ``k`` may store into ``cell``, nested bodies included.

        A callee's stores and a nested body's are invisible to a scan of one list,
        and control may enter at a label having made neither, exactly as
        ``hidden_defs`` records for names."""
        s = self.lst[k]
        if s[0] in _WILD or s[0] == "pcall":
            return True
        if s[0] == "st" and self._hits(k, cell, regions):
            return True
        return any(
            Defs(b, (self, k), rereads(s))._writes(j, cell, regions)
            for b in _stmt_bodies(s)
            for j in range(len(b))
        )

    def _crossable(self, k, cell, regions, labels, writes=None):
        """True where statement ``k`` cannot write ``cell``; label entries collected.

        ``_writes`` with labels held out for the caller: a label writes nothing,
        but control may enter there, so ``cell`` owes a proof that every
        enumerable entry reaches the same store (docs/frameprog.md 7.7 (3)). A
        ``pcall`` crosses only where the callee summary rules the cell out."""
        s = self.lst[k]
        if s[0] == "label":
            labels.add(s[1])
            return True
        if s[0] == "pcall":
            return writes is not None and not writes.may_write(s[1], cell)
        if s[0] in _WILD:
            return False
        if s[0] == "st" and self._hits(k, cell, regions):
            return writes is not None and writes.store_misses(self, k, s, cell)
        return all(
            Defs(b, (self, k), rereads(s))._crossable(j, cell, regions, labels, writes)
            for b in _stmt_bodies(s)
            for j in range(len(b))
        )

    def _cell_walk(self, cell, bound, regions, labels, writes=None):
        """One backward walk for the store in force, labels transparent-and-collected."""
        for k in range(bound - 1, -1, -1):
            s = self.lst[k]
            if s[0] == "st" and s[1] == ("const", cell, 2) and G.store_width(s[2]) == 1:
                return (self, k, s[2])
            if not self._crossable(k, cell, regions, labels, writes):
                return None
        if self.outer is None or (
            self.cyclic
            and any(
                not self._crossable(k, cell, regions, labels, writes) for k in range(len(self.lst))
            )
        ):
            return None
        return self.outer[0]._cell_walk(cell, self.outer[1], regions, labels, writes)

    def _name_dirty(self, name, labels):
        """The cyclic body may rebind ``name``; its labels join like any crossed."""
        for s in self.lst:
            got = _label_defs(s)
            if got is None or name in got[0]:
                return True
            labels |= got[1]
        return False

    def _name_walk(self, name, bound, labels):
        """``_lookup`` with labels transparent-and-collected; None where truly wild."""
        for k in range(bound - 1, -1, -1):
            s = self.lst[k]
            if s[0] == "asg" and s[1] == name:
                return (self, k, s[2])
            got = _label_defs(s)
            if got is None:
                return None
            if name in got[0]:
                return (self, k, None)
            labels |= got[1]
        if self.cyclic and self._name_dirty(name, labels):
            return None
        if self.outer is None:
            return ENTRY
        oenv, obound = self.outer
        if oenv.lst[obound][0] == "for" and oenv.lst[obound][1] == name:
            return (oenv, obound, None)  # the counter binds the body from its own seat
        return oenv._name_walk(name, obound, labels)

    def _root(self):
        root = self
        while root.outer is not None:
            root = root.outer[0]
        return root

    def _entries(self):
        """The root's label-entry index, rebuilt from current contents when dropped."""
        root = self._root()
        if root.jumps is None:
            root.jumps = _Jumps(root)
        return root.jumps

    def rewritten(self):
        """A list under this root changed shape: its cached label entries are void.

        ``_Jumps`` names each goto by ``(env, position)`` over the whole root
        environment, so a rewrite that shrinks any list in it -- a pair fold merging
        two statements into one -- leaves every cached position at or past the change
        pointing at a different statement. Dropping the index makes ``_entries``
        rebuild it, reproducing the lookups a from-scratch analysis would make; a
        clamped stale position would be a different lookup, not a safer one
        (docs/frameprog.md 7.10.6)."""
        self._root().jumps = None

    def _verified(self, got, labels, walk):
        """``got`` where every enumerable entry into each crossed label agrees.

        The join law (docs/frameprog.md 7.7 (3)): a definition survives a label
        only where each ``goto`` entering it arrives with the same definition in
        force, to a fixpoint over the entry graph. A computed jump refuses all,
        and so does a label another procedure's goto may target -- ``foreign``
        is that set, and an unstamped root (None) trusts no label at all."""
        if got is None or not labels:
            return got
        foreign = self._root().foreign
        jumps = self._entries()
        if jumps.computed or foreign is None:
            return None
        want = got if got is ENTRY else (id(got[0].lst), got[1])
        seen = set()
        while labels:
            pc = labels.pop()
            seen.add(pc)
            if pc in foreign:
                return None
            for env, k in jumps.srcs.get(pc, ()):
                more = set()
                sgot = walk(env, k, more)
                if sgot is None or (sgot if sgot is ENTRY else (id(sgot[0].lst), sgot[1])) != want:
                    return None
                labels |= more - seen
        return got

    def lookup_joined(self, name, bound):
        """``_lookup`` surviving labels every goto entry is proven to agree with.

        A name the whole procedure cannot bind holds its entry value at every
        point: jumps bind nothing, so no label, goto or computed dispatch can
        carry another value in -- unless a foreign goto enters the procedure
        itself, whose caller's value of the name arrives unseen."""
        labels = set()
        got = self._name_walk(name, bound, labels)
        out = self._verified(got, labels, lambda e, k, o: e._name_walk(name, k, o))
        if out is not None:
            return out
        root = self._root()
        if root.foreign is None or not self._entries().labels.isdisjoint(root.foreign):
            return None
        return ENTRY if _never_binds(root.lst, name) else None

    def cell(self, cell, bound, regions, writes=None):
        """``(env, index, value)`` of the byte store to ``cell`` in force at ``bound``.

        ``_lookup`` over memory rather than names: the store in force is the last
        statement before the read that may write the cell, labels joined as
        ``_verified`` requires."""
        labels = set()
        got = self._cell_walk(cell, bound, regions, labels, writes)
        return self._verified(
            got, labels, lambda e, k, out: e._cell_walk(cell, k, regions, out, writes)
        )


def envs(stmts, outer=None, cyclic=False):
    """``(env, index, statement)`` over a list and its nested bodies, scopes linked."""
    env = Defs(stmts, outer, cyclic)
    for i, s in enumerate(stmts):
        yield env, i, s
        for b in _stmt_bodies(s):
            yield from envs(b, (env, i), rereads(s))


def _transfers(s, nxt):
    """True where ``s`` may enter a procedure the enclosing list does not name.

    A ``dgoto``/``igoto`` the next statement's ``swg`` enumerates lands in an arm of
    this same procedure, and a ``dcall`` an ``swc`` enumerates lands on one of the
    labels ``swc`` names; anything else computed may go anywhere."""
    k = s[0]
    if k in ("dgoto", "igoto"):
        return nxt != "swg"
    if k == "dcall":
        return nxt != "swc"
    return k == "dbr"


class Calls:
    """Who calls each procedure and with what: the entry side of a bare local.

    A parameter holds what a call site passed, so the value is known only where
    the call graph is closed -- an indirect transfer, a caller the model does not
    name (an RTS-trick landing among them), or no caller at all leaves it open."""

    __slots__ = ("params", "sites", "opaque", "open_flow", "play")

    def __init__(self, procs, play, landings=()):
        self.play, self.sites, self.open_flow = play, {}, False
        self.opaque = set(landings)
        self.params = {e: list(pa) for e, pa, _r, _s in procs}
        for e, _pa, _r, stmts in procs:
            for env, i, s in envs(stmts):
                k = s[0]
                if k == "pcall":
                    self.sites.setdefault(s[1], []).append((e, env, i, tuple(s[2])))
                elif k in ("call", "callb"):
                    self.opaque.add(s[1])
                elif k == "swc":
                    self.opaque |= {int(lbl[1:], 16) for lbl in s[1]}
                elif _transfers(s, env.lst[i + 1][0] if i + 1 < len(env.lst) else None):
                    self.open_flow = True

    def args(self, entry, name):
        """``(caller, env, index, argument)`` per call site, None where the graph is open."""
        params = self.params.get(entry) or []
        if self.open_flow or entry == self.play or entry in self.opaque:
            return None
        if name not in params or not self.sites.get(entry):
            return None
        k = params.index(name)
        sites = self.sites[entry]
        if any(k >= len(args) for _c, _e, _i, args in sites):
            return None
        return [(c, env, i, args[k]) for c, env, i, args in sites]


def _stmt_exprs(s):
    """Statement forms: label/asg/st/if/loop/for/opsw/swg/swc/callb/call/
    pcall/dcall/dbr/dgoto/igoto/goto/unobs/cont/brk/ret; this returns the
    expression operands, ``_stmt_bodies`` the nested statement lists."""
    k = s[0]
    if k == "asg":
        return (s[2],)
    if k == "dgoto":
        return (s[1],)
    if k == "st":
        return (s[1], s[2])
    if k == "if":
        return (s[2],)
    if k == "pcall":
        return tuple(s[2])
    if k == "dcall":
        return (s[1],)
    if k == "dbr":
        return (s[2], s[3])
    if k == "igoto":
        return () if s[2] is None else (s[2],)
    return ()


def _stmt_bodies(s):
    k = s[0]
    if k == "if":
        return (s[3], s[4])
    if k == "loop":
        return (s[1],)
    if k == "for":
        return (s[4],)
    if k == "opsw":
        return tuple(b for _l, b in s[2])
    if k == "swg":
        return tuple(b for _l, b in s[1])
    if k == "swc":
        return tuple(b for _l, b in s[2])
    if k == "callb":
        return (s[3],)
    return ()


def _map_exprs(s, f):
    k = s[0]
    if k == "asg":
        return ("asg", s[1], f(s[2]))
    if k == "st":
        return ("st", f(s[1]), f(s[2])) + s[3:]
    if k == "if":
        return ("if", s[1], f(s[2]), s[3], s[4])
    if k == "pcall":
        return ("pcall", s[1], [f(a) for a in s[2]], s[3])
    if k == "dcall":
        return ("dcall", f(s[1]), s[2])
    if k == "dbr":
        return ("dbr", s[1], f(s[2]), f(s[3]), s[4])
    if k == "dgoto":
        return ("dgoto", f(s[1]))
    if k == "igoto" and s[2] is not None:
        return ("igoto", s[1], f(s[2]))
    return s


def _const_of(n, env, k):
    """The constant ``n`` evaluates to at ``k``, else None.

    A local is read as the definition in force leaves it, and that definition's own
    locals at the point it was written. A memory read is never constant, so a value
    reached through a cell is refused here and left to rung (d)'s spill rule."""
    if n[0] == "const":
        return n
    if n[0] == "loc":
        got = env._lookup(n[1], k)
        return None if got is None or got[2] is None else _const_of(got[2], got[0], got[1])
    if n[0] != "op":
        return None
    kids = [_const_of(c, env, k) for c in n[2]]
    if any(c is None for c in kids):
        return None
    return E.simplify(("op", n[1], tuple(kids), n[3]))


def _one_addr(addr, env, k):
    """``addr`` as the constant it is, else as written: the ONE address-naming rule.

    The converter spells a constant address as its constant only inside the block
    that sets the index, and binds it to a temporary wherever it takes two
    operations, as ``zp,X`` does -- so one cell arrives under two names."""
    got = _const_of(addr, env, k)
    return addr if got is None else ("const", got[1] & 0xFFFF, 2)


def _addr_exprs(n, env, k):
    """``n`` with the address of every memory reference under it spelled the one way."""
    kids = _kids(n)
    out = n if not kids else _rebuild(n, [_addr_exprs(c, env, k) for c in kids])
    return ("mem", _one_addr(out[1], env, k), out[2]) if out[0] == "mem" else out


def canon_addrs(stmts, outer=None, cyclic=False):
    """Spell every address ``stmts`` names one way, nested bodies included."""
    env = Defs(stmts, outer, cyclic)
    for k, s in enumerate(stmts):
        for b in _stmt_bodies(s):
            canon_addrs(b, (env, k), rereads(s))
        if s[0] == "st":
            s = ("st", _one_addr(s[1], env, k), s[2]) + s[3:]
        stmts[k] = _map_exprs(s, lambda n, e=env, i=k: _addr_exprs(n, e, i))


# ---- block -> sequential local statements (pass 1 groundwork) -------------------
class _Stale(Exception):
    def __init__(self, reg):
        super().__init__(reg)
        self.reg = reg


class _Names:
    """Per-procedure local-name allocator (deterministic counters)."""

    def __init__(self, aliases):
        self.aliases = aliases
        self.counts = {}

    def fresh(self, prefix):
        k = self.counts.get(prefix, 0)
        self.counts[prefix] = k + 1
        return "%s%d" % (prefix, k)

    def role(self, addr):
        """Role prefix from a const cell address's symbol alias, else None."""
        if addr[0] != "const":
            return None
        alias = self.aliases.get(addr[1])
        if alias is None:
            return None
        head = alias.split("_", 1)[0].lower()
        return head if head.isalpha() else None


def _sugar(n):
    """Subtrees that print as name sugar (array index shapes) stay inline."""
    if is_op(n, "INT_ZEXT") and n[2][0][0] in ("reg", "slot", "loc"):
        return True
    if is_op(n, "INT_ADD", 2, 2):
        idx, base = n[2]
        if base[0] == "const" and base[2] == 2 and base[1] >= 0x100:
            return is_op(idx, "INT_ZEXT")
    return False


class _Conv:
    """One block to statements: loads and shared values become named locals,
    register out-exprs become sequenced assignments (pre-copies on conflict).

    Name-keyed, not web-keyed (A1): this builds the trees the webs are computed
    over, so there is no forest here to reach a definition through."""

    def __init__(self, names):
        self.names = names

    def run(self, blk):
        items, kouts, term = self._keyform(blk)
        bound, first, order, impure = self._bindings(items, kouts, term)
        precopy = set()
        while True:
            snap = dict(self.names.counts)
            try:
                return self._emit(items, kouts, term, bound, first, order, impure, precopy)
            except _Stale as e:
                self.names.counts = snap
                if e.reg in precopy:
                    raise ValueError("unresolvable register dependency") from e
                precopy.add(e.reg)

    @staticmethod
    def _keyform(blk):
        memo = {}

        def key(n):
            r = memo.get(id(n))
            if r is None:
                k = n[0]
                if k == "uni":
                    r = ("slot", n[1])
                elif k == "mem":
                    r = ("mem", key(n[1]), n[2])
                elif k == "op":
                    r = ("op", n[1], tuple(key(c) for c in n[2]), n[3])
                else:
                    r = n
                memo[id(n)] = r
            return r

        items = []
        for ev in blk.events:
            if ev[0] == "ld":
                items.append(("ld", ev[1], key(ev[2])))
            else:
                items.append(("st", key(ev[1]), key(ev[2])) + ev[3:])
        kouts = {i: key(x) for i, x in enumerate(blk.regs) if x != ("reg", i)}
        t = blk.term
        k = t[0]
        if k == "br":
            term = ("br", t[1], t[2], t[3], key(t[4]), None if t[5] is None else key(t[5]))
        elif k == "jmpd":
            term = ("jmpd", key(t[1]))
        elif k == "jmpind":
            term = ("jmpind", t[1], None if t[2] is None else key(t[2]))
        elif k == "jsr":
            term = ("jsr", t[1], t[2], None if t[3] is None else key(t[3]))
        else:
            term = t
        return items, kouts, term

    @staticmethod
    def _roots(items, kouts, term):
        roots = []
        for item in items:
            roots.extend(item[2:] if item[0] == "ld" else item[1:])
        roots.extend(kouts[i] for i in sorted(kouts))
        k = term[0]
        if k == "br":
            roots.append(term[4])
            if term[5] is not None:
                roots.append(term[5])
        elif k == "jmpd":
            roots.append(term[1])
        elif k in ("jmpind", "jsr") and term[-1] is not None:
            roots.append(term[-1])
        return roots

    def _bindings(self, items, kouts, term):
        """(bound set, first-use position, postorder, impure set) for the
        pure multi-use subtrees worth naming (the _Cse economics)."""
        refs = {}
        order = []
        stack = [(r, False) for r in reversed(self._roots(items, kouts, term))]
        while stack:
            n, done = stack.pop()
            if done:
                order.append(n)
                continue
            if n in refs:
                refs[n] += 1
                continue
            refs[n] = 1
            stack.append((n, True))
            stack.extend((c, False) for c in _kids(n))
        plen = {}
        impure = set()
        bound = set()
        for n in order:
            kids = _kids(n)
            if n[0] == "mem" or any(c in impure for c in kids):
                impure.add(n)
            if not kids:
                plen[n] = 2 * n[2] + 1 if n[0] == "const" else 3
                continue
            if n[0] == "mem":
                base = 5
            else:
                base = {"INT_ZEXT": 7, "INT_CARRY": 9}.get(n[1], 3 * len(kids) + 1)
            plen[n] = base + sum(3 if c in bound else plen[c] for c in kids)
            if n[0] != "op" or n in impure or _sugar(n):
                continue
            if refs[n] > 1 and (refs[n] - 1) * plen[n] > 12 + 3 * refs[n]:
                bound.add(n)
        first = {}
        roots = [(p, r) for p, item in enumerate(items) for r in item[1:] if isinstance(r, tuple)]
        roots += [(len(items), r) for r in self._roots([], kouts, term)]
        for pos, root in roots:
            stack = [root]
            while stack:
                x = stack.pop()
                if x in bound and x not in first:
                    first[x] = pos
                stack.extend(_kids(x))
        return bound, first, order, impure

    @staticmethod
    def _reg_last(items, kouts, term):
        last = {}
        roots = [(p, r) for p, item in enumerate(items) for r in item[1:] if isinstance(r, tuple)]
        roots += [(len(items), r) for r in _Conv._roots([], kouts, term)]
        for pos, root in roots:
            stack = [root]
            while stack:
                x = stack.pop()
                if x[0] == "reg":
                    last[x[1]] = max(last.get(x[1], -1), pos)
                stack.extend(_kids(x))
        return last

    def _emit(self, items, kouts, term, bound, first, order, impure, precopy):
        avail = {("reg", i): _reg_local(i) for i in range(16)}
        held = {}
        for i in range(16):
            held.setdefault(_reg_local(i), set()).add(("reg", i))
        out = []
        pend = dict(kouts)
        reg_last = self._reg_last(items, kouts, term)
        slot_regs = {}
        for i in sorted(kouts):
            stack = [kouts[i]]
            while stack:
                x = stack.pop()
                if x[0] == "slot":
                    slot_regs.setdefault(x[1], []).append(i)
                stack.extend(_kids(x))

        def render(n):
            name = avail.get(n)
            if name is not None:
                return ("loc", name)
            k = n[0]
            if k == "const":
                return n
            if k in ("reg", "slot"):
                raise _Stale(n[1])
            return _rebuild(n, [render(c) for c in _kids(n)])

        def bind(name, node):
            avail[node] = name
            held.setdefault(name, set()).add(node)

        def overwrite(name, i, rendered, node):
            if i in precopy and avail.get(("reg", i)) == name:
                tmp = self.names.fresh(name)
                out.append(("asg", tmp, ("loc", name)))
                bind(tmp, ("reg", i))
            out.append(("asg", name, rendered))
            for n2 in held.pop(name, ()):
                if avail.get(n2) == name:
                    del avail[n2]
            if node is not None and node not in impure and node[0] != "const":
                bind(name, node)

        matq = [n for n in order if n in bound]
        end = len(items)

        def mat(pos):
            for n in matq:
                if first.get(n) != pos or n in avail:
                    continue
                if pos == end and n in pend.values():
                    continue  # the register assignment itself binds the value
                di = None
                if pos < end:
                    for i in sorted(pend):
                        if pend[i] == n and reg_last.get(i, -1) < pos:
                            di = i
                            break
                if di is not None:
                    overwrite(_reg_local(di), di, render(n), pend[di])
                    del pend[di]
                else:
                    rendered = render(n)
                    name = self.names.fresh("t")
                    out.append(("asg", name, rendered))
                    bind(name, n)

        for pos, item in enumerate(items):
            mat(pos)
            if item[0] == "ld":
                _l, s, addr = item
                rendered = ("mem", render(addr), 1)
                di = None
                for i in sorted(pend):
                    if pend[i] == ("slot", s) and reg_last.get(i, -1) <= pos:
                        di = i
                        break
                if di is not None:
                    overwrite(_reg_local(di), di, rendered, None)
                    bind(_reg_local(di), ("slot", s))
                    del pend[di]
                else:
                    vregs = [i for i in slot_regs.get(s, ()) if i < 4]
                    prefix = self.names.role(addr) or (_reg_local(min(vregs)) if vregs else "w")
                    name = self.names.fresh(prefix)
                    out.append(("asg", name, rendered))
                    bind(name, ("slot", s))
            else:
                out.append(("st", render(item[1]), render(item[2])) + item[3:])
        mat(len(items))
        while pend:
            done = None
            for i in sorted(pend):
                try:
                    rendered = render(pend[i])
                except _Stale:
                    continue
                overwrite(_reg_local(i), i, rendered, pend[i])
                done = i
                break
            if done is None:
                render(pend[sorted(pend)[0]])
                raise AssertionError("stale register not raised")
            del pend[done]
        k = term[0]
        if k == "br":
            term = term[:4] + (render(term[4]), None if term[5] is None else render(term[5]))
        elif k == "jmpd":
            term = ("jmpd", render(term[1]))
        elif k == "jmpind" and term[2] is not None:
            term = ("jmpind", term[1], render(term[2]))
        elif k == "jsr" and term[3] is not None:
            term = ("jsr", term[1], term[2], render(term[3]))
        return out, term


# ---- region trees -> statement trees --------------------------------------------
def _view_block(blk):
    events = [ev for ev in blk.events if ev[0] in ("ld", "st")]
    if len(events) != len(blk.events):
        blk = C.Block(blk.pc, blk.op0, blk.pcs, events, blk.term, blk.regs)
    return sidprog._stmt_view(blk)


class _Builder:
    """Serializes region trees into statement lists (frameprog dialect)."""

    def __init__(self, labels, dispatch, view, conv):
        self.labels = labels
        self.dispatch = dispatch
        self.view = view
        self.conv = conv
        self.arm = 0

    def proc(self, root):
        return self.capture(sidprog._items(root))

    def capture(self, items):
        out = []
        self.seq(items, out)
        return out

    def seq(self, items, out):
        i = 0
        while i < len(items):
            r = items[i]
            k = r.kind
            if k == "block":
                i += self.block(r, items[i + 1] if i + 1 < len(items) else None, out)
                continue
            if k == "seq":
                self.seq(r.a, out)
            elif k == "loop":
                out.append(("loop", self.capture(sidprog._items(r.a))))
            elif k == "switch":
                if r.b and r.b[0] in self.labels:
                    out.append(("label", r.b[0]))
                cases = [(lbl, self.capture(sidprog._items(body))) for lbl, body in r.a[1]]
                out.append(("opsw", r.b[0], cases))
            elif k == "goto":
                out.append(("goto", r.a))
            elif k == "frontier":
                out.append(("unobs", r.a))
            elif k in _EXITS:
                out.append((k,) if (r.a or 1) == 1 else (k, r.a))
            else:
                raise ValueError("unexpected %s region in sequence" % k)
            i += 1

    def block(self, r, nxt, out):
        blk = r.a
        if self.view is not None and r.b is not None and blk.pc in self.dispatch:
            if r.b in self.labels:
                out.append(("label", r.b))
            inner = []
            consumed = self._leaf(r, nxt, inner, False)
            out.append(("opsw", blk.pc, [("$%02X" % blk.op0, inner)]))
            return consumed
        return self._leaf(r, nxt, out, True)

    def _leaf(self, r, nxt, out, labelled):
        blk = _view_block(r.a)
        stmts, term = self.conv.run(blk)
        if labelled and r.b is not None and r.b in self.labels:
            out.append(("label", r.b))
        out.extend(stmts)
        return self._flow(term, blk.pcs[-1], nxt, out)

    def splice(self, target, ret, body):
        """An inlined callee's statements as the caller's own (8.4).

        The RTS is an edge to the call's continuation: a body leaving by one
        trailing ``ret`` splices bare, one leaving by several through a scope. A
        body binding a pc, leaving by a transfer or returning from a displacement
        it moved keeps its wrapper."""
        if bound_pcs(body) or escapes(body) or not sp_balanced(body):
            return [("callb", target, ret, body)]
        rets = _rets_in(body)
        if len(rets) == 1 and body and body[-1][0] == "ret":
            return body[:-1]
        return scope(body) if rets else body

    def callsw(self, term, cases, out):
        """A computed call's arm table as a structured dispatch (8.4).

        Every arm is already the caller's own text and every arm's ``ret`` is the
        call's edge back, so the site is a ``switch goto`` inside a scope the arms
        break out of. ``splice``'s guards, read at an arm: one the plan left a
        procedure, one binding a pc some other site's transfer lands on (whose
        ``ret`` answers *that* site), one leaving by a transfer, or one returning
        from a displacement it moved keeps the call form."""
        bare = [lbl for lbl, arm in cases if arm is None or arm.b is None]
        bodied = []
        for lbl, arm in cases:
            if arm is not None and arm.b is not None:
                self.arm += 1
                bodied.append((lbl, self.capture(sidprog._items(arm.b))))
                self.arm -= 1
        blocked = (bound_pcs(b) or escapes(b) or not sp_balanced(b) for _l, b in bodied)
        if bare or any(blocked):
            out.append(("dcall", term[3], term[2]))
            out.append(("swc", bare, bodied))
            return
        out.extend(scope([("dgoto", term[3]), ("swg", bodied)]))

    def _flow(self, term, site, nxt, out):
        k = term[0]
        if nxt is not None and nxt.kind == "if":
            if k != "br" or term[5] is not None:
                raise ValueError("if region without a static branch terminator")
            word = "if" if term[1] else "ifnot"
            then = self.capture(sidprog._items(nxt.b))
            els = self.capture(sidprog._items(nxt.c)) if nxt.c is not None else []
            out.append(("if", word, term[4], then, els))
            return 2
        if nxt is not None and nxt.kind == "call":
            if k != "jsr" or term[3] is not None:
                raise ValueError("call body without a static call terminator")
            self.arm += 1
            body = self.capture(sidprog._items(nxt.b))
            self.arm -= 1
            out.extend(self.splice(term[1], term[2], body))
            return 2
        if nxt is not None and nxt.kind == "switch" and not nxt.b:
            sel, cases = nxt.a
            if sel == "call":
                self.callsw(term, cases, out)
                return 2
            self._termlines(term, out)
            vector = k == "jmpind" and term[1] is not None
            if vector and self.view is not None and not self.view.dyn_targets.get(site):
                for _lbl, arm in cases:
                    self.seq(sidprog._items(arm), out)
            else:
                arms = [(lbl, self.capture(sidprog._items(arm))) for lbl, arm in cases]
                out.append(("swg", arms))
            return 2
        if k == "br" and term[5] is None:
            raise ValueError("static branch terminator without an if region")
        self._termlines(term, out)
        return 2 if nxt is not None and nxt.kind == "exit" else 1

    def _termlines(self, term, out):
        k = term[0]
        if k in ("goto", "jmp"):
            return
        if k == "br":
            out.append(("dbr", "if" if term[1] else "ifnot", term[4], term[5], term[3]))
        elif k == "jmpd":
            out.append(("dgoto", term[1]))
        elif k == "jmpind":
            out.append(("igoto", term[1], term[2]))
        elif k == "jsr":
            if term[3] is not None:
                out.append(("dcall", term[3], term[2]))
            else:
                out.append(("call", term[1], term[2]))
        else:
            out.append(("ret", self.arm > 0))


def _signed(k):
    return k - 256 if k > 127 else k


def slot_reader(stmts):
    """The procedure consumes the return slot its caller pushed (docs/frameprog.md 5).

    ``sp`` walks entry-relative over the straight-line prefix, where a callee must take
    its own return address before a transfer can lose it: a stack pointer above entry,
    or an access at displacement ``+1``, is what the caller pushed and a ``pcall`` drops."""
    disp, env = 0, {}
    for s in stmts:
        for addr, width in accesses(s):
            for _hop in range(4):
                if addr[0] != "loc" or addr[1] not in env:
                    break
                addr = env[addr[1]]
            k = sp_disp(addr)
            if k is not None and disp + _signed(k) + width - 1 >= 1:
                return True
        if s[0] == "asg" and s[1] == _SP:
            d = sp_delta(s[2])
            if d is None:
                return False
            disp += _signed(d & 0xFF)
            if disp >= 1:
                return True
        elif s[0] == "asg":
            env[s[1]] = s[2]
        elif s[0] != "st":
            return False
    return False


# ---- pass 2: interprocedural register liveness -----------------------------------
class _Info:
    """Program-wide summaries: live-in, may/must-define, returns, callability.

    Name-keyed, not web-keyed (A1): live-in and returns become the procedure
    header's own registers, so a per-web signature spells a header the printer
    does not have; and this runs inside ``repolish``, over unsettled trees."""

    def __init__(self, procs, play):
        self.procs = dict(procs)
        self.order = [e for e, _s in procs]
        self.play = play
        self.livein = {e: set() for e in self.order}
        self.returns = {e: set() for e in self.order}
        self.may = {e: set() for e in self.order}
        self.must = {e: set() for e in self.order}
        self.labmap = {e: {} for e in self.order}
        self.own_labels = {e: set() for e in self.order}
        self.call_labels = {e: set() for e in self.order}
        self.G = set()
        self.open_flow = False
        self.callable_ = {}
        self.params = {}
        self.rets = {}
        self._scan()

    def _scan(self):
        blocked = set()
        for e in self.order:
            gotos, calls = set(), set()
            self._scan_list(self.procs[e], self.own_labels[e], gotos, blocked, calls)
            blocked |= {pc for pc in gotos if pc == e or pc not in self.own_labels[e]}
            self.call_labels[e] = calls & self.own_labels[e]
            if slot_reader(self.procs[e]):
                blocked.add(e)
        for e in self.order:
            self.callable_[e] = not self.open_flow and e not in blocked

    def _scan_list(self, stmts, labels, gotos, blocked, calls):
        for j, s in enumerate(stmts):
            k = s[0]
            nxt = stmts[j + 1][0] if j + 1 < len(stmts) else None
            if k == "dbr":
                self.open_flow = True
            elif k == "dgoto" and nxt != "swg":
                self.open_flow = True
            elif k == "igoto" and s[2] is not None and nxt != "swg":
                self.open_flow = True
            elif k == "dcall" and nxt != "swc":
                self.open_flow = True
            elif k == "swc":
                blocked |= {int(lbl[1:], 16) for lbl in s[1]}
            elif k == "label":
                labels.add(s[1])
            elif k == "goto":
                gotos.add(s[1])
            elif k in ("call", "callb"):
                calls.add(s[1])
            for x in _stmt_exprs(s):
                self.G |= _locset(x) & _ALL_REG_LOCALS
            for b in _stmt_bodies(s):
                self._scan_list(b, labels, gotos, blocked, calls)

    def ret_live(self, e):
        out = set(self.returns[e])
        if e == self.play:
            out |= self.livein[e]
        if not self.callable_[e]:
            out |= self.G
        return out

    def summarize(self):
        for _round in range(24):
            changed = False
            for e in self.order:
                may = self._may(self.procs[e])
                if may != self.may[e]:
                    self.may[e] = may
                    changed = True
                must = self._must(self.procs[e])[0]
                if must != self.must[e]:
                    self.must[e] = must
                    changed = True
                before = (len(self.livein[e]), len(self.returns[e]))
                self.livein[e] |= _Flow(self, e).run() & _ALL_REG_LOCALS
                if self.callable_[e]:
                    self.livein[e] |= self.returns[e] - self.must[e]
                if (len(self.livein[e]), len(self.returns[e])) != before:
                    changed = True
            if not changed:
                break
        for e in self.order:
            self.params[e] = _by_reg(self.livein[e])
            self.rets[e] = _by_reg(self.returns[e]) if self.callable_[e] else []

    def _may(self, stmts):
        out = set()
        for s in stmts:
            k = s[0]
            if k in ("asg", "for"):
                out.add(s[1])
            elif k == "pcall":
                out |= set(s[3]) | self.may.get(s[1], set())
            elif k == "call":
                out |= self.may.get(s[1], self.G)
            elif k in ("dcall", "dbr", "dgoto"):
                out |= self.G
            elif k == "swc":
                for lbl in s[1]:
                    out |= self.may.get(int(lbl[1:], 16), self.G)
            for b in _stmt_bodies(s):
                out |= self._may(b)
        return out

    def _must(self, stmts):
        """(surely-defined names, fell-through) under conservative branching."""
        out = set()
        for s in stmts:
            k = s[0]
            if k in ("ret", "goto", "cont", "brk", "unobs", "dbr", "dgoto", "igoto"):
                return out, False
            if k == "asg":
                out.add(s[1])
            elif k == "pcall":
                out |= set(s[3]) | self.must.get(s[1], set())
            elif k == "call":
                out |= self.must.get(s[1], set())
            elif k == "callb":
                out |= self._must(s[3])[0]
            elif k in ("if", "opsw", "swg"):
                arms = [self._arm_must(b) for b in _stmt_bodies(s)]
                live_arms = [d for d in arms if d is not None]
                if not live_arms:
                    return out, False
                if len(live_arms) == len(arms):
                    out |= set.intersection(*live_arms)
                elif k == "if" and len(live_arms) == 1:
                    out |= live_arms[0]
                if k == "swg":
                    return out, False
        return out, True

    def _arm_must(self, body):
        if body and all(x[0] == "unobs" for x in body):
            return None
        return self._must(body)[0]


class _Flow:
    """One backward register-liveness sweep over a procedure's statements."""

    def __init__(self, info, entry):
        self.info = info
        self.entry = entry
        self.labmap = info.labmap[entry]
        self.brk = []
        self.cont = []
        self.armret = []
        self.loop_after = None
        self.loop_head = None
        self.liveout = None

    def run(self):
        """Backward sweep to a fixpoint over the procedure's label live-sets.

        One pass is unsound: a ``goto`` reaching a label the sweep has not yet
        visited (a forward branch into a later arm) reads an empty live-set and
        makes live locals look dead, so seed each round with the last."""
        seed = self.info.labmap[self.entry]
        out = set()
        for _ in range(24):
            self.labmap = {k: set(v) for k, v in seed.items()}
            out = self.seq(self.info.procs[self.entry], set())
            grew = any(not v <= seed.get(k, set()) for k, v in self.labmap.items())
            for k, v in self.labmap.items():
                seed[k] = seed.get(k, set()) | v
            if not grew:
                break
        self.labmap = seed
        return out

    def _call_body(self, stmts):
        """First index of an own-label some ``call`` enters, else None.

        Such a body returns to its call sites and may be entered again, so its
        exit carries the machine set: textual fall-through under-approximates."""
        labs = self.info.call_labels.get(self.entry)
        if not labs:
            return None
        for i, s in enumerate(stmts):
            if s[0] == "label" and s[1] in labs:
                return i
        return None

    def seq(self, stmts, live):
        nxt = None
        cb = self._call_body(stmts)
        for j in range(len(stmts) - 1, -1, -1):
            s = stmts[j]
            if cb is not None and j >= cb:
                live = live | self.info.G
            if self.liveout is not None:
                self.liveout[id(s)] = set(live)
            live = self.stmt(s, live, nxt)
            nxt = s[0]
        return live

    def _uses(self, s):
        out = set()
        for x in _stmt_exprs(s):
            out |= _locset(x)
        return out

    def _loop_head(self, s, live):
        body = s[1] if s[0] == "loop" else s[4]
        head = set()
        if s[0] == "loop" and not _own_conts(body) and not self.info._must(body)[1]:
            return head  # single-trip scope: no back edge reaches the head

        for _i in range(24):
            f = _Flow(self.info, self.entry)
            f.armret = list(self.armret)
            f.brk = self.brk + [set(live)]
            f.cont = self.cont + [head]
            h = f.seq(body, set(head))
            if s[0] == "for":
                h.discard(s[1])
            if h <= head:
                break
            head |= h
        return head

    def _call_live(self, tgt, live, kills, extra):
        info = self.info
        if tgt in info.procs:
            if info.callable_[tgt]:
                info.returns[tgt] |= live & info.may[tgt]
            return (live - kills) | info.livein[tgt] | extra
        return live | info.G | extra

    def stmt(self, s, live, nxt):
        k = s[0]
        info = self.info
        if k == "asg":
            if s[1] != _SP and s[1] not in live and not _reads_vol(s[2]):
                return live  # faint: a dead target's operand reads are not uses
            live = set(live)
            live.discard(s[1])
            return live | _locset(s[2])
        if k == "st":
            return live | self._uses(s)
        if k == "if":
            return self.seq(s[3], set(live)) | self.seq(s[4], set(live)) | _locset(s[2])
        if k in ("loop", "for"):
            head = self._loop_head(s, live)
            if k == "loop" and self.loop_after is not None:
                self.loop_after[id(s)] = set(live)
            if self.loop_head is not None:
                self.loop_head[id(s)] = set(head)
            self.brk.append(set(live))
            self.cont.append(head)
            out = self.seq(s[1] if k == "loop" else s[4], set(head))
            self.brk.pop()
            self.cont.pop()
            if k == "for":
                out |= live  # a for leaves by its own bottom: a loop leaves only by brk
                out.discard(s[1])  # the counter the for defines on entry is dead above
            return out | head
        if k == "label":
            self.labmap[s[1]] = self.labmap.get(s[1], set()) | live
            return live
        if k == "goto":
            pc = s[1]
            if pc in info.own_labels[self.entry]:
                return set(self.labmap.get(pc, set()))
            if pc in info.procs:
                return set(info.livein[pc])
            return set(info.G)
        if k in _EXITS:
            stack = self.cont if k == "cont" else self.brk
            n = exit_level(s)
            return set(stack[-n]) if len(stack) >= n else set(info.G)
        if k == "unobs":
            return set()
        if k == "ret":
            if s[1]:
                return set(self.armret[-1]) if self.armret else set(info.G)
            return set(info.ret_live(self.entry))
        if k == "call":
            return self._call_live(s[1], live, info.must.get(s[1], set()), set())
        if k == "pcall":
            kills = set(s[3]) | info.must.get(s[1], set())
            return self._call_live(s[1], live, kills, self._uses(s))
        if k == "dcall":
            if nxt == "swc":
                return live | _locset(s[1])
            return set(info.G) | _locset(s[1])
        if k == "swc":
            out = set()
            for _lbl, body in s[2]:
                self.armret.append(set(live))
                out |= self.seq(body, set())
                self.armret.pop()
            for lbl in s[1]:
                out |= self._call_live(int(lbl[1:], 16), set(live), set(), set())
            return out
        if k in ("opsw", "swg"):
            out = set()
            for body in _stmt_bodies(s):
                out |= self.seq(body, set(live))
            return out
        if k == "callb":
            self.armret.append(set(live))
            out = self.seq(s[3], set(live))
            self.armret.pop()
            return out
        if k == "dbr":
            return set(info.G) | self._uses(s)
        if k == "dgoto":
            if nxt == "swg":
                return live | _locset(s[1])
            return set(info.G) | _locset(s[1])
        if k == "igoto":
            used = _locset(s[2]) if s[2] is not None else set()
            if nxt == "swg" or s[2] is None:
                return live | used
            return set(info.G) | used
        raise ValueError("unexpected statement %r" % (k,))


# ---- pass 1 cleanup: dead-definition pruning + single-use inlining ---------------
class _Prune(_Flow):
    """Liveness sweep that drops assignments to locals dead at their site."""

    changed = False

    def seq(self, stmts, live):
        keep = []
        nxt = None
        cb = self._call_body(stmts)
        for j in range(len(stmts) - 1, -1, -1):
            s = stmts[j]
            if cb is not None and j >= cb:
                live = live | self.info.G
            if s[0] == "asg" and s[1] != _SP and s[1] not in live and not _reads_vol(s[2]):
                self.changed = True
                continue
            live = self.stmt(s, live, nxt)
            keep.append(s)
            nxt = s[0]
        keep.reverse()
        stmts[:] = keep
        return live


def _prune(info):
    changed = False
    for e in info.order:
        p = _Prune(info, e)
        p.run()
        changed |= p.changed
    return changed


def _defs_in(s, info):
    """Textual-semantics definitions of ``s`` (a pcall writes only its rets)."""
    k = s[0]
    out = set()
    if k in ("asg", "for"):
        out.add(s[1])
    elif k == "pcall":
        out |= set(s[3])
    elif k == "call":
        out |= info.may.get(s[1], info.G)
    elif k in ("dcall", "dbr", "dgoto", "igoto", "swc"):
        out |= info.G
    for b in _stmt_bodies(s):
        for s2 in b:
            out |= _defs_in(s2, info)
    return out


def _invis_name(s, name, info, entry):
    """Whether ``s`` may consume ``name`` through a non-textual use."""
    k = s[0]
    if k == "call":
        hit = name in info.livein.get(s[1], info.G)
    elif k in ("dcall", "dbr", "dgoto", "igoto", "swc"):
        hit = name in info.G
    elif k == "ret":
        hit = True if s[1] else name in info.ret_live(entry)
    elif k == "goto":
        if s[1] in info.own_labels[entry]:
            hit = name in info.labmap[entry].get(s[1], info.G)  # the target consumes it
        else:
            hit = name in info.livein.get(s[1], info.G)
    else:
        hit = False
    return hit or any(_invis_name(s2, name, info, entry) for b in _stmt_bodies(s) for s2 in b)


def _labels_in(s):
    if s[0] == "label":
        return True
    return any(_labels_in(s2) for b in _stmt_bodies(s) for s2 in b)


def _use_count_expr(x, name):
    """How many times ``name`` is read in ``x``, at any width.

    A 16-bit local reads as ``("loc", name, 2)``, so matching the bare 2-tuple
    misses exactly the words rung (d) mints: ``_find_use`` then walks past a real
    use and ``_inline_list`` deletes a definition still being read (§7.10.14)."""
    n = 0
    stack = [x]
    while stack:
        y = stack.pop()
        if y[0] == "loc" and y[1] == name:
            n += 1
        stack.extend(_kids(y))
    return n


def _use_count(s, name):
    n = sum(_use_count_expr(x, name) for x in _stmt_exprs(s))
    for b in _stmt_bodies(s):
        for s2 in b:
            n += _use_count(s2, name)
    return n


def _escapes(s, out, depth=0):
    """Collect cont/brk statements of ``s`` bound to the enclosing loop.

    A levelled exit inside a nested region is bound here when its level counts
    out exactly as far as this loop, so nested bodies are walked too."""
    k = s[0]
    if k in _EXITS:
        if exit_level(s) == depth + 1:
            out.add(k)
        return out
    inner = depth + 1 if k in _CYCLIC else depth
    for b in _stmt_bodies(s):
        for s2 in b:
            _escapes(s2, out, inner)
    return out


def _esc_hit(s, name, head, after):
    esc = _escapes(s, set())
    if "cont" in esc and (head is None or name in head):
        return True
    return "brk" in esc and (after is None or name in after)


class _InlineCtx:
    """Per-list inlining context: liveness annotations plus loop scope."""

    __slots__ = ("info", "entry", "liveout", "loop_head", "head", "after")

    def __init__(self, info, entry, liveout, loop_head, head=None, after=None):
        self.info = info
        self.entry = entry
        self.liveout = liveout
        self.loop_head = loop_head
        self.head = head
        self.after = after

    def enter(self, s):
        if s[0] in ("loop", "for"):
            return _InlineCtx(
                self.info,
                self.entry,
                self.liveout,
                self.loop_head,
                self.loop_head.get(id(s)),
                self.liveout.get(id(s)),
            )
        return self


def _find_use(items, start, name, e, ctx):
    """Index of the sole safe use site of ``name = e`` in ``items``, else None.

    Sound for multi-def names: the def dominates the same-list use (no label
    in between), so every path to the use passes the def."""
    info, entry, liveout = ctx.info, ctx.entry, ctx.liveout
    head, after = ctx.head, ctx.after
    elocs = _locset(e)
    has_mem = _reads_mem(e)
    for j in range(start, len(items)):
        s = items[j]
        if _labels_in(s):
            return None
        cnt = _use_count(s, name)
        dd = _defs_in(s, info)
        if cnt:
            targets = {s[1]} if s[0] == "asg" else set(s[3]) if s[0] == "pcall" else set()
            if cnt > 1 or (dd - targets) & (elocs | {name}) or _invis_name(s, name, info, entry):
                return None  # targets assign after operand evaluation: not a conflict
            top = sum(_use_count_expr(x, name) for x in _stmt_exprs(s))
            if has_mem and top != cnt:
                return None
            if _esc_hit(s, name, head, after):
                return None
            if name not in targets and name in liveout.get(id(s), (name,)):
                return None  # a later (possibly non-textual) consumer remains
            return j
        if name in dd or dd & elocs or _invis_name(s, name, info, entry):
            return None
        if _esc_hit(s, name, head, after):
            return None
        if has_mem and s[0] != "asg":
            return None
    return None


def _subst_stmt(s, name, e):
    s = _map_exprs(s, lambda x: _subst_loc(x, name, e))
    for b in _stmt_bodies(s):
        for i, s2 in enumerate(b):
            if _use_count(s2, name):
                b[i] = _subst_stmt(s2, name, e)
    return s


def _inline_list(items, ctx):
    changed = False
    i = 0
    while i < len(items):
        s = items[i]
        sub = ctx.enter(s)
        for b in _stmt_bodies(s):
            changed |= _inline_list(b, sub)
        if s[0] == "asg" and s[1] != _SP and not _reads_vol(s[2]):
            j = _find_use(items, i + 1, s[1], s[2], ctx)
            if j is not None:
                items[j] = _subst_stmt(items[j], s[1], s[2])
                del items[i]
                changed = True
                continue
        i += 1
    return changed


def _pin_stmts(stmts, out):
    for s in stmts:
        out.append(s)
        for b in _stmt_bodies(s):
            _pin_stmts(b, out)


def _inline(info):
    changed = False
    for e in info.order:
        flow = _Flow(info, e)
        flow.liveout = {}
        flow.loop_head = {}
        flow.run()
        pins = []
        _pin_stmts(info.procs[e], pins)  # freed ids reused by new tuples are stale liveness hits
        changed |= _inline_list(info.procs[e], _InlineCtx(info, e, flow.liveout, flow.loop_head))
        del pins
    return changed


def _rewrite_calls(info):
    def walk(stmts):
        for i, s in enumerate(stmts):
            if s[0] == "call" and info.callable_.get(s[1]):
                args = [("loc", p) for p in info.params[s[1]]]
                stmts[i] = ("pcall", s[1], args, list(info.rets[s[1]]))
            for b in _stmt_bodies(stmts[i]):
                walk(b)

    for e in info.order:
        walk(info.procs[e])


# ---- pass 3: counter loops as for-ranges -----------------------------------------
def _mentions(s, name):
    if s[0] in ("asg", "for") and s[1] == name:
        return True
    if s[0] == "pcall" and name in s[3]:
        return True
    if any(_use_count_expr(x, name) for x in _stmt_exprs(s)):
        return True
    return any(_mentions(s2, name) for b in _stmt_bodies(s) for s2 in b)


def _own_conts(stmts, depth=0):
    n = 0
    for s in stmts:
        if s[0] in _EXITS:
            n += s[0] == "cont" and exit_level(s) == depth + 1
        else:
            inner = depth + 1 if s[0] in _CYCLIC else depth
            for b in _stmt_bodies(s):
                n += _own_conts(b, inner)
    return n


def single_trip(body):
    """Whether a loop body's back edge is unreachable: no own ``cont``, no fall-through.

    A scope the structurer, ``splice`` or ``one_exit`` placed is exactly this, and
    nothing that follows it needs the join a back edge would force."""
    return leaves(body) and not _own_conts(body)


def _defs_name(s, name):
    if s[0] in ("asg", "for") and s[1] == name:
        return True
    if s[0] == "pcall" and name in s[3]:
        return True
    if s[0] in ("call", "dcall", "swc"):
        return True
    return any(_defs_name(s2, name) for b in _stmt_bodies(s) for s2 in b)


def _step_of(upd, name):
    if upd == ("op", "INT_ADD", (("loc", name), ("const", 1, 1)), 1):
        return 1
    if upd == ("op", "INT_ADD", (("loc", name), ("const", 0xFF, 1)), 1):
        return -1
    if upd == ("op", "INT_SUB", (("loc", name), ("const", 1, 1)), 1):
        return -1
    return None


def _cond_of(cond, name):
    """("sign", None) or ("eq"/"ne", K) when ``cond`` tests the counter."""
    if cond[0] != "op":
        return None
    if cond[1] == "INT_NOTEQUAL" and cond[3] == 1:
        lhs, rhs = cond[2]
        sign = (
            rhs == ("const", 0, 1)
            and lhs[0] == "op"
            and lhs[1] == "INT_AND"
            and set(lhs[2]) == {("loc", name), ("const", 0x80, 1)}
        )
        if sign:
            return ("sign", None)
    if cond[1] in ("INT_EQUAL", "INT_NOTEQUAL"):
        lhs, rhs = cond[2]
        if lhs == ("loc", name) and rhs[0] == "const" and rhs[2] == 1:
            return ("eq" if cond[1] == "INT_EQUAL" else "ne", rhs[1])
    return None


def _match_for(body):
    """(counter, step, test shape, continue-if-true) from an update/test tail."""
    if len(body) < 3 or body[-1] != ("brk",):
        return None
    upd, test = body[-3], body[-2]
    if upd[0] != "asg" or test[0] != "if":
        return None
    name = upd[1]
    step = _step_of(upd[2], name)
    if step is None:
        return None
    _k, word, cond, then, els = test
    if then == [("cont",)] and els == []:
        cont_if_true = word == "if"
    elif then == [] and els == [("cont",)]:
        cont_if_true = word == "ifnot"
    else:
        return None
    shape = _cond_of(cond, name)
    if shape is None:
        return None
    return name, step, shape, cont_if_true


def _for_last(step, shape, cont_if_true, init):
    kind, kval = shape
    if kind == "sign" and step == -1 and not cont_if_true and init < 0x80:
        return 0
    cont_ne = (kind == "ne" and cont_if_true) or (kind == "eq" and not cont_if_true)
    if cont_ne and step == 1 and init < kval:
        return kval - 1
    if cont_ne and step == -1 and init > kval:
        return kval + 1
    return None


def _for_lists(items, after):
    for s in items:
        for b in _stmt_bodies(s):
            _for_lists(b, after)
    idx = 0
    while idx < len(items):
        s = items[idx]
        match = _match_for(s[1]) if s[0] == "loop" and id(s) in after else None
        if match is None:
            idx += 1
            continue
        name, step, shape, cont_if_true = match
        body = s[1][:-3]
        bad = (
            name in after[id(s)]
            or any(_labels_in(s2) for s2 in s[1])
            or _own_conts(body)
            or any(_defs_name(s2, name) for s2 in body)
        )
        j = idx - 1
        while j >= 0 and items[j][0] != "label" and not _mentions(items[j], name):
            j -= 1
        init_ok = j >= 0 and items[j][:2] == ("asg", name) and items[j][2][0] == "const"
        if bad or not init_ok:
            idx += 1
            continue
        init = items[j][2][1]
        last = _for_last(step, shape, cont_if_true, init)
        if last is None:
            idx += 1
            continue
        items[idx] = ("for", name, init, last, body)
        del items[j]
    return items


def _forloops(info):
    for e in info.order:
        f = _Flow(info, e)
        f.loop_after = {}
        f.run()
        _for_lists(info.procs[e], f.loop_after)


# ---- printing --------------------------------------------------------------------
def _base_add(addr, w, least):
    """``(const base, index)`` of a two-operand ``INT_ADD`` at width ``w``, else None."""
    if not is_op(addr, "INT_ADD", w, 2):
        return None
    at = [i for i, c in enumerate(addr[2]) if c[0] == "const" and c[2] == w and c[1] >= least]
    if len(at) != 1:
        return None
    base, idx = addr[2][at[0]], addr[2][1 - at[0]]
    if idx[0] == "const":
        return None
    if is_op(idx, "INT_ZEXT", 2):
        idx = idx[2][0]
    return base[1], idx


def _index_of(addr):
    """``(base, index expression, modulus)`` for a ``const + index`` address, else None.

    The index is whatever the address adds to the base; ``zext2`` is the reader's
    own widening (grammar ``_index_addr``) and is stripped so the text round trips.
    The ``zp,X`` form adds inside the byte, so it names the zero page modulo 256."""
    if is_op(addr, "INT_ZEXT", 2):
        got = _base_add(addr[2][0], 1, 0)
        return None if got is None else (got[0], got[1], 0x100)
    got = _base_add(addr, 2, 0x100)
    return None if got is None else (got[0], got[1], 0)


_NORES = MappingProxyType({})  # rung (f): deref address -> (pointer cell, index or None)


def _membody(addr, res=_NORES, sz=1):
    if addr[0] == "const" and addr[2] == 2:
        return sidprog._addr_name(addr[1])
    got = res.get(addr)
    if got is not None:
        name = "*" + sidprog._addr_name(got[0])
        return name if got[1] is None else "%s[%s]" % (name, _fmt(got[1], res))
    got = _index_of(addr)
    if got is not None and got[2] == 0:
        base, idx = got[0], got[1]
        if sz == 1 and G.sid_base(base) is not None:
            off = base - 0xD400  # rung (d)'s residue: assert the byte, not the register
            if off:
                wide = idx if loc_width(idx) == 2 else ("op", "INT_ZEXT", (idx,), 2)
                idx = ("op", "INT_ADD", (wide, ("const", off, 2)), 2)
            return "%s[%s]" % (G.VIEW, _fmt(idx, res))
        return "%s[%s]" % (sidprog._addr_name(base), _fmt(idx, res))
    return None


def _memref(addr, sz=1, res=_NORES):
    body = _membody(addr, res, sz)
    if body is None:
        body = "mem[%s]" % _fmt(addr, res)
    return body + sidprog._wsuf(sz)


_PAIRS = {}  # render-scoped: lo table base -> hi table base, from the decl roles


def _pair_columns(bl, bh, idx):
    """``(lo base, index)`` where two cells are a declared pair's two columns.

    The hi cell the registry names for the lo half must be the very cell the hi
    address reads; anything else is two cells that merely sit near each other."""
    got = pair_site(_PAIRS, bl, idx)
    return None if got is None or got[0] != bh else got[1:]


def _pair_pack(n):
    """``(lo base, index)`` where ``n`` packs a declared lo/hi table pair."""
    got = packed_cells(n) if _PAIRS else None
    return None if got is None else _pair_columns(*got)


def _pair_halves(a, b):
    """``(lo base, index, word value)`` where ``a``/``b`` store one pair datum."""
    if not _PAIRS or a[0] != "st" or b[0] != "st":
        return None
    if G.store_width(a[2]) != 1 or G.store_width(b[2]) != 1:
        return None
    (bl, il), (bh, ih) = addr_split(a[1]), addr_split(b[1])
    if bl is None or bh is None or il != ih:
        return None
    site = _pair_columns(bl, bh, il)
    v = trunc_pair(a[2], b[2])
    return None if site is None or v is None else (site[0], site[1], v)


def _fmt(n, res=_NORES):
    k = n[0]
    got = _pair_pack(n)
    if got is not None:
        return "%s[%s]:2" % (sidprog._addr_name(got[0]), _fmt(got[1], res))
    if k == "const":
        return sidprog._hex(n[1], n[2])
    if k == "loc":
        return n[1] + sidprog._wsuf(loc_width(n))
    if k == "mem":
        return _memref(n[1], n[2], res)
    mn, kids, sz = n[1], n[2], n[3]
    if mn == "INT_ZEXT":
        return "zext%d(%s)" % (sz, _fmt(kids[0], res))
    if mn == "COPY":
        return "trunc%d(%s)" % (sz, _fmt(kids[0], res))
    if mn == "INT_CARRY":
        return "carry(%s, %s)" % (_fmt(kids[0], res), _fmt(kids[1], res))
    if mn == "INT_ADD":
        half = 1 << (8 * sz - 1)
        parts = [_fmt(kids[0], res)]
        for c in kids[1:]:
            if c[0] == "const" and c[1] >= half:
                parts.append("- " + sidprog._hex((-c[1]) & ((1 << (8 * sz)) - 1), sz))
            else:
                parts.append("+ " + _fmt(c, res))
        return "(%s)%s" % (" ".join(parts), sidprog._wsuf(sz))
    if mn == "INT_SUB":
        return "(%s - %s)%s" % (_fmt(kids[0], res), _fmt(kids[1], res), sidprog._wsuf(sz))
    if mn in sidprog._CHAINS:
        body = (" %s " % sidprog._CHAINS[mn]).join(_fmt(c, res) for c in kids)
        return "(%s)%s" % (body, sidprog._wsuf(sz))
    if mn in sidprog._CMPS:
        return "(%s %s %s)" % (_fmt(kids[0], res), sidprog._CMPS[mn], _fmt(kids[1], res))
    body = "%s %s %s" % (_fmt(kids[0], res), sidprog._BINS[mn], _fmt(kids[1], res))
    return "(%s)%s" % (body, sidprog._wsuf(sz))


class _Printer:
    """Statement trees to frameprog text lines."""

    def __init__(self, res=_NORES):
        self.out = []
        self.rets = []
        self.res = res

    def e(self, n):
        """Expression text, resolved deref addresses named (rung (f))."""
        return _fmt(n, self.res)

    def line(self, text, d):
        self.out.append(" " * d + text)

    def proc(self, entry, stmts, params, rets):
        self.rets = rets
        sig = "sub_%04X(%s)" % (entry, ", ".join(params))
        if rets:
            sig += " -> %s" % ", ".join(rets)
        self.line(sig + " {", 0)
        self.seq(stmts, 1)
        self.line("}", 0)

    def seq(self, stmts, d):
        i = 0
        while i < len(stmts):
            got = None if i + 1 >= len(stmts) else _pair_halves(stmts[i], stmts[i + 1])
            if got is not None:
                lo, idx, v = got
                text = "%s[%s]:2 = %s" % (sidprog._addr_name(lo), self.e(idx), self.e(v))
                self.line(text, d + 1)
                i += 2
                continue
            self.stmt(stmts[i], d)
            i += 1

    def _cases(self, head, cases, d):
        self.line(head, d)
        for lbl, body in cases:
            self.line("case %s: {" % lbl, d + 1)
            self.seq(body, d + 2)
            self.line("}", d + 1)
        self.line("}", d)

    def _if(self, s, d):
        _k, word, cond, then, els = s
        if len(then) == 1 and then[0][0] == "unobs":
            self.line("%s %s unobserved $%04X" % (word, self.e(cond), then[0][1]), d)
            self.seq(els, d)
            return
        self.line("%s %s {" % (word, self.e(cond)), d)
        self.seq(then, d + 1)
        if len(els) == 1 and els[0][0] == "unobs":
            self.line("} else unobserved $%04X" % els[0][1], d)
            return
        if els:
            self.line("} else {", d)
            self.seq(els, d + 1)
        self.line("}", d)

    def _swc(self, s, d):
        if not s[2]:
            body = " ".join(s[1])
            self.line("switch call { %s }" % body if body else "switch call { }", d)
            return
        self.line("switch call {", d)
        if s[1]:
            self.line(" ".join(s[1]), d + 1)
        for lbl, body in s[2]:
            self.line("case %s: {" % lbl, d + 1)
            self.seq(body, d + 2)
            self.line("}", d + 1)
        self.line("}", d)

    def stmt(self, s, d):
        k = s[0]
        if k == "label":
            self.line("$%04X:" % s[1], d)
        elif k == "asg":
            lv = s[1] + sidprog._wsuf(G.store_width(s[2]))
            self.line("%s = %s" % (lv, self.e(s[2])), d + 1)
        elif k == "st":
            sz = G.store_width(s[2])
            self.line(
                "%s%s = %s"
                % ("hi-first " if hi_first(s) else "", _memref(s[1], sz, self.res), self.e(s[2])),
                d + 1,
            )
        elif k == "if":
            self._if(s, d)
        elif k == "loop":
            self.line("loop {", d)
            self.seq(s[1], d + 1)
            self.line("}", d)
        elif k == "for":
            self.line("for %s in $%02X..$%02X {" % (s[1], s[2], s[3]), d)
            self.seq(s[4], d + 1)
            self.line("}", d)
        elif k == "opsw":
            self._cases("switch %s {" % sidprog._addr_name(s[1]), s[2], d)
        elif k == "swg":
            self._cases("switch goto {", s[1], d)
        elif k == "swc":
            self._swc(s, d)
        elif k == "callb":
            self.line("call $%04X ret $%04X {" % (s[1], s[2]), d + 1)
            self.seq(s[3], d + 2)
            self.line("}", d + 1)
        elif k == "call":
            self.line("call $%04X ret $%04X" % (s[1], s[2]), d + 1)
        elif k == "pcall":
            text = "sub_%04X(%s)" % (s[1], ", ".join(self.e(a) for a in s[2]))
            if s[3]:
                text = "%s = %s" % (", ".join(s[3]), text)
            self.line(text, d + 1)
        elif k == "dcall":
            self.line("call (%s) ret $%04X" % (self.e(s[1]), s[2]), d + 1)
        elif k == "dbr":
            self.line(
                "%s %s goto (%s) else $%04X" % (s[1], self.e(s[2]), self.e(s[3]), s[4]), d + 1
            )
        elif k == "dgoto":
            self.line("goto (%s)" % self.e(s[1]), d + 1)
        elif k == "igoto":
            ptr = "(%s)" % self.e(s[2]) if s[2] is not None else "$%04X" % s[1]
            self.line("igoto %s" % ptr, d + 1)
        elif k == "goto":
            self.line("goto $%04X" % s[1], d)
        elif k == "unobs":
            self.line("unobserved $%04X" % s[1], d)
        elif k in _EXITS:
            n = exit_level(s)
            self.line(("continue" if k == "cont" else "break") + ("" if n == 1 else " %d" % n), d)
        elif k == "ret":
            if not s[1] and self.rets:
                self.line("ret %s" % ", ".join(self.rets), d + 1)
            else:
                self.line("ret", d + 1)
        else:
            raise ValueError("unexpected statement %r" % (k,))


# ---- driver ------------------------------------------------------------------------
def summaries(flat, play):
    """The program-wide summary of ``(entry, statements)`` pairs, at its fixpoint.

    A signature is a summary of the bodies, so it is read at the same fixpoint
    wherever it is read: the build takes one here and ``resign`` takes another."""
    info = _Info(flat, play)
    info.summarize()
    for _round in range(3):
        before = ({e: list(v) for e, v in info.params.items()}, dict(info.rets))
        info.summarize()
        if before == (info.params, info.rets):
            break
    return info


def procedures(trees, labels, view, dispatch, aliases, play):
    """``(entry, params, rets, statements)`` per procedure under passes 1-3."""
    cell_aliases = dict(aliases or {})
    procs = []
    for entry, root in trees:
        builder = _Builder(labels, dispatch, view, _Conv(_Names(cell_aliases)))
        procs.append((entry, builder.proc(root)))
    info = summaries(procs, play)
    _rewrite_calls(info)
    for _round in range(16):
        pruned = _prune(info)
        inlined = _inline(info)
        if not (pruned or inlined):
            break
    _forloops(info)
    for _e, stmts in procs:
        canon_addrs(stmts)
    return [(e, info.params[e], info.rets[e], stmts) for e, stmts in procs]


def _clear_path(uenv, at, denv, k, rd, regions):
    """No statement between the definition and the use may write ``rd``.

    The use's scope chain climbs to the definition's list; every top-level
    statement crossed must be an ``asg`` or a store proven off the range, and a
    compound crossed is a wall (its bodies may store anywhere)."""
    env, pos = uenv, at
    while True:
        for j in range(k + 1 if env is denv else 0, pos):
            s = env.lst[j]
            if s[0] == "asg":
                continue
            if s[0] != "st" or overlaps(rd, store_reach(s, regions)):
                return False
        if env is denv:
            return True
        if env.outer is None:
            return False
        env, pos = env.outer


def _same_defs(names, uenv, at, denv, k):
    """Every name resolves to one definition from both points, ENTRY included."""
    for m in sorted(names):
        a = uenv.lookup_joined(m, at)
        b = denv.lookup_joined(m, k)
        if a is ENTRY or b is ENTRY:
            if a is not b:
                return False
            continue
        if a is None or b is None or (id(a[0].lst), a[1]) != (id(b[0].lst), b[1]):
            return False
    return True


def _stable_row(addr, uenv, at, denv, k, regions):
    """``addr`` where the byte it read at ``k`` still reads the same at ``at``.

    The row is wholly const (a store cannot move it) or no statement between
    wrote it, and the index's locals resolve to the same definitions from both
    points, so the traced value may stand in for the spill."""
    vb, vi = addr_split(addr)
    if vb is None:
        return None
    sp = span(vb, vi, regions)
    if not all(regions.const_at(a) for a in range(vb, vb + sp + 1)):
        if not _clear_path(uenv, at, denv, k, (vb, vi, sp, 2, 0), regions):
            return None
    if vi is not None and not _same_defs(_locset(vi), uenv, at, denv, k):
        return None
    return addr


def _side_val(node, env, at):
    """The value a pack side carries, traced through one local definition."""
    if node[0] != "loc" or env is None:
        return node, env, at
    got = env.lookup_joined(node[1], at)
    if got is None or got is ENTRY or got[2] is None:
        return node, env, at
    return got[2], got[0], got[1]


def _untrunced(lo, hi, env, at):
    """The word a pack of its own two truncs rebuilds: ``hi:lo`` of ``V`` is ``V``.

    Both sides trace through one local each; the word's locals must resolve to
    the same definitions from the pack as from both truncs, or the value moved."""
    vl, le, lk = _side_val(lo, env, at)
    vh, he, hk = _side_val(hi, env, at)
    v = trunc_pair(vl, vh)
    if v is None or loc_width(v) != 2 or _reads_mem(v) or _reads_vol(v):
        return None
    names = _locset(v)
    if not _same_defs(names, env, at, le, lk) or not _same_defs(names, env, at, he, hk):
        return None
    return v


def _side_addr(node, env, at, regions):
    """The byte address a pack side reads, traced through one spill or local."""
    if node[0] == "mem" and node[2] == 1:
        base, idx = addr_split(node[1])
        if base is None:
            return None
        if idx is not None or env is None or regions is None:
            return node[1]
        got = env.cell(base, at, regions)
        if got is None or got[0] is not env or got[2][0] != "mem" or got[2][2] != 1:
            return node[1]
        return _stable_row(got[2][1], env, at, got[0], got[1], regions) or node[1]
    if node[0] != "loc" or env is None or regions is None:
        return None
    got = env.lookup_joined(node[1], at)
    if got is None or got is ENTRY or got[2] is None:
        return None
    if got[2][0] != "mem" or got[2][2] != 1:
        return None
    return _stable_row(got[2][1], env, at, got[0], got[1], regions)


def _fold_word(n, env, at, regions, in_addr=False):
    """One little-endian word read for its two byte reads, the 6502's only spelling.

    ``hi<<8 | lo`` over ``mem[b+1+i]``/``mem[b+i]`` is ``mem[b+i]:2`` -- the same
    two cells, loaded pure -- for any base and one shared index, a volatile source
    excluded since ``iota`` pins each of its reads (docs/frameprog.md 7.9). An
    address position folds only the plain adjacent shape, whose spelling rung (f)
    already reads; a value position also traces spills and normalizes a
    non-adjacent pack onto its columns for the pair rendering."""
    got = _le_bytes(n)
    if got is None:
        return n
    if not in_addr:
        whole = _untrunced(got[0], got[1], env, at)
        if whole is not None:
            return whole
    if in_addr:
        if got[0][0] != "mem" or got[1][0] != "mem":
            return n
        la, ha = got[0][1], got[1][1]
    else:
        la = _side_addr(got[0], env, at, regions)
        ha = _side_addr(got[1], env, at, regions)
    if la is None or ha is None:
        return n
    (bl, il), (bh, ih) = addr_split(la), addr_split(ha)
    if bl is None or bh is None or il != ih:
        return n
    reach = 1 + (0 if il is None else E.mask(loc_width(il)))
    if any(min(bl, bh) <= v <= max(bl, bh) + reach for v in sidprog._VOLS):
        return n
    if bh == bl + 1:
        return ("mem", la, 2)
    if in_addr:
        return n
    return le_pack(("mem", la, 1), ("mem", ha, 1))


def _carry_out(n, carry_in):
    """``(the two addends, what came in below)`` where ``n`` is a stage's carry-out.

    A carry out of ``A + B + cin`` is spelled ``carry(A, B) | carry(A + B, cin)``;
    ``carry_in`` reads the stage below out of ``cin``, and neither bar nor plus
    spells an order, so every side is tried both ways."""
    if not is_op(n, "INT_OR", 1, 2):
        return None
    for x, y in commuted(n[2]):
        if not is_op(x, "INT_CARRY") or not is_op(y, "INT_CARRY"):
            continue
        for add, cin in commuted(y[2]):
            got = carry_in(cin)
            if got is None or not is_op(add, "INT_ADD", 1, 2):
                continue
            if set(add[2]) == set(x[2]):
                return x[2], got
    return None


def _no_carry_in(cin):
    """The bottom stage carries nothing in: the literal zero byte, and only that."""
    return () if cin == ("const", 0, 1) else None


def _chain_lo(n):
    """``(l1, l2)`` where ``n`` is a byte add's carry-out spelled as its chain."""
    got = _carry_out(n, _no_carry_in)
    return None if got is None else got[0]


def _chain_full(n):
    """``(h1, h2, l1, l2)`` where ``n`` is a word add's carry-out chain."""
    got = _carry_out(n, _chain_lo)
    return None if got is None else (got[0][0], got[0][1], got[1][0], got[1][1])


def _word_from(lo, hi, env, at, regions):
    """The word two byte operands are the halves of, else None."""
    v = _untrunced(lo, hi, env, at)
    if v is not None:
        return v
    la = _side_addr(lo, env, at, regions)
    ha = _side_addr(hi, env, at, regions)
    if la is None or ha is None:
        return None
    (bl, il), (bh, ih) = addr_split(la), addr_split(ha)
    if bl is None or bh != bl + 1 or il != ih:
        return None
    reach = 1 + (0 if il is None else E.mask(loc_width(il)))
    if any(bl <= v2 <= bl + reach for v2 in sidprog._VOLS):
        return None
    return ("mem", la, 2)


def _fold_chain(n, env, at, regions):
    """A byte carry chain over word halves is the word operation (7.9).

    The full chain is the word add's carry-out, ``carry(A:2, B:2)``; a hi half
    plus a lo chain is the word sum's high byte. Halves pair by provenance --
    truncs of one word, or reads of adjacent cells -- and each pairing is tried
    both ways since the chain spells no order."""
    got = _chain_full(n)
    if got is not None:
        h1, h2, l1, l2 = got
        for (la, ha), (lb, hb) in (((l1, h1), (l2, h2)), ((l2, h2), (l1, h1))):
            a2 = _word_from(la, ha, env, at, regions)
            b2 = _word_from(lb, hb, env, at, regions)
            if a2 is not None and b2 is not None:
                return ("op", "INT_CARRY", (a2, b2), 1)
        return n
    if is_op(n, "INT_ADD", 1, 2):
        for h1, chain in commuted(n[2]):
            lo = _chain_lo(chain)
            if lo is None:
                continue
            for l1, l2 in commuted(lo):
                a2 = _word_from(l1, h1, env, at, regions)
                if a2 is None:
                    continue
                b2 = ("op", "INT_ZEXT", (l2,), 2)
                add = ("op", "INT_ADD", (a2, b2), 2)
                shr = ("op", "INT_RIGHT", (add, ("const", 8, 1)), 2)
                return ("op", "COPY", (shr,), 1)
    return n


def const_value(n):
    """The one value the expression denotes, else None: a fold over constant leaves."""
    if n[0] == "const":
        return n[1] & E.mask(n[2])
    if n[0] != "op":
        return None
    vals = [const_value(c) for c in n[2]]
    if any(v is None for v in vals):
        return None
    return E._apply(n[1], vals, [E.width(c) for c in n[2]], n[3])


def _pin_targets(stmts):
    """A computed goto whose operand denotes one value transfers to that pc.

    Every definition reaching the transfer was a constant, so the value question
    answered it outright and the text says so rather than restating the arithmetic."""
    for i, s in enumerate(stmts):
        for b in _stmt_bodies(s):
            _pin_targets(b)
        if s[0] == "dgoto" and s[1][0] != "const":
            got = const_value(s[1])
            if got is not None:
                stmts[i] = ("dgoto", ("const", got & 0xFFFF, 2))


def _fold_expr(n, env, at, regions, in_addr=False):
    if n[0] == "mem":
        n = ("mem", _fold_expr(n[1], env, at, regions, True), n[2])
    elif n[0] == "op":
        n = ("op", n[1], tuple(_fold_expr(c, env, at, regions, in_addr) for c in n[2]), n[3])
    n = _fold_word(n, env, at, regions, in_addr)
    return n if in_addr else _fold_chain(n, env, at, regions)


def _word_of(vl, vh):
    """The word value two half stores spell, where the halves say so themselves."""
    if vl[0] == "mem" and vl[2] == 1 and vh[0] == "mem" and vh[2] == 1:
        (cl, jl), (ch, jh) = addr_split(vl[1]), addr_split(vh[1])
        if cl is not None and ch == cl + 1 and jl == jh:
            return ("mem", vl[1], 2)
    v = trunc_pair(vl, vh)
    return v if v is not None and loc_width(v) == 2 else None


def _fold_store_pair(a, b, regions):
    """One word store for two adjacent half stores of one datum, RAM only.

    The SID pairs stay rung (d)'s, where the record law lives; here the two
    cells are plain memory, the writes adjacent, and the second half's reads
    must not overlap the first store, so one word store is the same program."""
    if a[0] != "st" or b[0] != "st":
        return None
    if G.store_width(a[2]) != 1 or G.store_width(b[2]) != 1:
        return None
    (ba, ia), (bb, ib) = addr_split(a[1]), addr_split(b[1])
    if ba is None or bb is None or ia != ib:
        return None
    lo, hi = (a, b) if bb == ba + 1 else (b, a) if ba == bb + 1 else (None, None)
    if lo is None:
        return None
    base = min(ba, bb)
    if 0xD000 <= base <= 0xDFFF:
        return None
    val = _word_of(lo[2], hi[2])
    if val is None:
        return None
    st_at = (base, ia, span(base, ia, regions), 2, 0)
    for ref, rw in (r for x in (hi[2], hi[1]) for r in mem_refs(x)):
        if ref is None:
            return None
        rb, ri, rm = ref
        if overlaps(st_at, (rb, ri, span(rb, ri, regions, rm), rw, rm)):
            return None
    return ("st", lo[1], val)


def _fold_pair_at(stmts, i, regions):
    """Fold the half-store pair led at ``i``, safe asgs between hoisted above.

    An ``asg`` between the halves moves above the pair only where it reads
    neither target cell, so it still sees the memory the lo store had not yet
    changed; anything else between the halves keeps them apart."""
    a = stmts[i]
    if a[0] != "st" or G.store_width(a[2]) != 1:
        return False
    if addr_split(a[1])[0] is None:
        return False
    lead = store_reach(a, regions)  # what a crossed statement steps above
    moved = []
    for j in range(i + 1, min(i + 5, len(stmts))):
        s = stmts[j]
        if s[0] == "st":
            got = _fold_store_pair(a, s, regions)
            if got is not None:
                stmts[i : j + 1] = moved + [got]
                return True
            if not _steps_over(a, s, lead, regions):
                return False
            moved.append(s)
            continue
        if s[0] != "asg":
            return False
        for ref, rw in mem_refs(s[2]):
            if ref is None:
                return False
            rb, ri, rm = ref
            if overlaps(lead, (rb, ri, span(rb, ri, regions, rm), rw, rm)):
                return False
        moved.append(s)
    return False


def _steps_over(a, s, lead, regions):
    """The store ``s`` may move above the half store ``a``: neither sees the other.

    ``s`` must read nothing ``a`` writes, and ``a`` must read nothing ``s``
    writes, so the two commute and the pair may meet below."""
    sreach = store_reach(s, regions)
    for x, at2 in ((s, lead), (a, sreach)):
        for ref, rw in (r for e in (x[1], x[2]) for r in mem_refs(e)):
            if ref is None:
                return False
            rb, ri, rm = ref
            if overlaps(at2, (rb, ri, span(rb, ri, regions, rm), rw, rm)):
                return False
    return not overlaps(lead, sreach)


def _fold_stmt(s, env, at, regions):
    """``_map_exprs`` with a store's destination folded in the address position.

    A destination is an address, so it folds the plain adjacent shape and traces no
    spill: a trace replaces the pointer word a deref reads with the columns that
    word was loaded from, which is the spelling rung (f) reads (frameprog.md 4.4)."""
    if s[0] != "st":
        return _map_exprs(s, lambda x: _fold_expr(x, env, at, regions))
    return ("st", _fold_expr(s[1], env, at, regions, True), _fold_expr(s[2], env, at, regions)) + s[
        3:
    ]


def _fold_words(stmts, regions, outer=None, cyclic=False, foreign=None):
    env = Defs(stmts, outer, cyclic, foreign if outer is None else None)
    for i, s in enumerate(stmts):
        stmts[i] = _fold_stmt(s, env, i, regions)
        for b in _stmt_bodies(stmts[i]):
            _fold_words(b, regions, (env, i), rereads(stmts[i]))
    if regions is None:
        return
    i = 0
    while i + 1 < len(stmts):
        if _fold_pair_at(stmts, i, regions):
            env.rewritten()  # the fold shrank this list; the root's goto index named it
            continue
        i += 1


# ---- def-use webs: the analysis unit a machine name is not ------------------------
WEB_ENTRY = "entry"  # the web is live where the procedure begins
WEB_OPAQUE = "opaque"  # an opaque call, or a transfer this module cannot enumerate, defines it
WEB_WIDTH = "width"  # the web's sites do not agree on a width
WEB_REFUSALS = (WEB_ENTRY, WEB_OPAQUE, WEB_WIDTH)

_AT_ENTRY = -1  # the pseudo-definition every name carries where the procedure begins
_NEXT = object()  # a successor placeholder: whichever node is emitted after this one
_EMPTY = frozenset()


class Web:
    """One def-use web: the definitions that reach a common read, closed.

    ``def_widths`` is what ``_loc_widths`` records at the web's definitions (a
    ``pcall`` return is 0, unknown); ``widths`` is the width its sites actually
    spell, which is what ``WEB_WIDTH`` reads."""

    __slots__ = ("name", "root", "defs", "uses", "def_widths", "widths", "refusals")

    def __init__(self, name, root):
        self.name = name
        self.root = root
        self.defs = []
        self.uses = []
        self.def_widths = set()
        self.widths = set()
        self.refusals = set()

    def __repr__(self):
        return "Web(%s@%d, %d defs, %d uses, %s)" % (
            self.name,
            self.root[0],
            len(self.defs),
            len(self.uses),
            "|".join(sorted(self.refusals)) or "typed",
        )


class _Union:
    """Union-find over definition keys: one web is one named quantity."""

    __slots__ = ("up",)

    def __init__(self):
        self.up = {}

    def find(self, k):
        self.up.setdefault(k, k)
        while self.up[k] != k:
            self.up[k] = k = self.up[self.up[k]]
        return k

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        self.up[max(ra, rb)] = min(ra, rb)


def _own_labels(stmts, out):
    for s in stmts:
        if s[0] == "label":
            out.add(s[1])
        for b in _stmt_bodies(s):
            _own_labels(b, out)
    return out


def _uses_of(n, out):
    """Every local ``n`` reads, each carrying the width its own site spells."""
    stack = [n]
    while stack:
        x = stack.pop()
        if x[0] == "loc":
            out.setdefault(x[1], set()).add(loc_width(x))
        else:
            stack.extend(_kids(x))
    return out


def _named_locals(stmts, out):
    """Every local the forest names, as a definition or as a read."""
    for s in stmts:
        for x in _stmt_exprs(s):
            _uses_of(x, out)
        if s[0] in ("asg", "for"):
            out.setdefault(s[1], set())
        elif s[0] == "pcall":
            for n in s[3]:
                out.setdefault(n, set())
        for b in _stmt_bodies(s):
            _named_locals(b, out)
    return out


def _site(s):
    """``(definitions -> width, reads -> widths)`` of one statement, its own only."""
    defs, uses = {}, {}
    if s is None:
        return defs, uses
    for x in _stmt_exprs(s):
        _uses_of(x, uses)
    k = s[0]
    if k == "asg":
        defs[s[1]] = G.store_width(s[2])
    elif k == "for":
        defs[s[1]] = 1
    elif k == "pcall":
        defs.update({n: 0 for n in s[3]})
    return defs, uses


class _Graph:
    """One procedure's statement forest as a flow graph, one node per statement.

    Join nodes stand where control merges and every ``goto``/``cont``/``brk``
    resolves to a node, so a transfer this module cannot enumerate becomes an
    opaque node -- a refused web -- rather than a missing edge and a split one."""

    def __init__(self, stmts, rets=(), regs=_EMPTY, calls=None):
        self.stmt, self.succ, self.path = [], [], []
        self.labels, self.opaque, self.escape = {}, set(), set()
        self.own = _own_labels(stmts, set())
        self.regs = regs
        self.rets = frozenset(rets) & regs
        self.calls = calls or {}  # callee entry -> (may-define, live-in), both register-only
        self.summed = {}  # node -> the callee summary bounding what it defines and reads
        self._tick = 0
        self._walk(stmts, [], ())
        self._emit(None, [], None)  # the sink control falls off the end into
        self.succ = [
            [i + 1 if t is _NEXT else self.labels[t] for t in ss] for i, ss in enumerate(self.succ)
        ]

    def _emit(self, s, succ, path):
        self.stmt.append(s)
        self.succ.append(list(succ))
        self.path.append(path)
        return len(self.stmt) - 1

    def _syn(self, tag):
        self._tick += 1
        return (tag, self._tick)

    def _join(self, key):
        self.labels[key] = self._emit(None, [_NEXT], None)

    def _summed(self, at, summary):
        """Bound a call node by its callee's summary, or refuse it as opaque.

        A raw ``call`` runs a body this program holds, so what it defines is that
        body's may-set and what it reads its live-in; a register in neither flows
        through untouched. Only a callee with no summary -- a target outside the
        program, or a transfer the graph cannot enumerate -- is the ABI-free case
        the ``opaque`` refusal was stated for."""
        if summary is None:
            self.opaque.add(at)
        else:
            self.summed[at] = (summary[0] & self.regs, summary[1] & self.regs)

    def _stop(self, s, path, escaping):
        at = self._emit(s, [], path)
        if escaping:
            self.escape.add(at)
        return at

    def _cases(self, s, bodies, loops, here, from_head):
        end = self._syn("end")
        heads = [self._syn("case") for _ in bodies]
        at = self._emit(s, heads + from_head(end), here)
        for bi, b in enumerate(bodies):
            self._join(heads[bi])
            self._walk(b, loops, here + (bi,))
            self._emit(None, [end], None)
        self._join(end)
        return at

    def _walk(self, stmts, loops, path):  # pylint: disable=too-many-branches
        for k, s in enumerate(stmts):
            here, op = path + (k,), s[0]
            nxt = stmts[k + 1][0] if k + 1 < len(stmts) else None
            if op == "label":
                self.labels[("lbl", s[1])] = self._emit(s, [_NEXT], here)
            elif op in ("asg", "st", "pcall"):
                self._emit(s, [_NEXT], here)
            elif op == "call":
                self._summed(self._emit(s, [_NEXT], here), self.calls.get(s[1]))
            elif op == "callb":
                self._emit(s, [_NEXT], here)  # the callee's own statements are right here
                self._walk(s[3], loops, here + (0,))
            elif op == "if":
                els, end = self._syn("else"), self._syn("end")
                self._emit(s, [_NEXT, els], here)
                self._walk(s[3], loops, here + (0,))
                self._emit(None, [end], None)
                self._join(els)
                self._walk(s[4], loops, here + (1,))
                self._join(end)
            elif op in ("loop", "for"):
                head, out = self._syn("head"), self._syn("break")
                if op == "for":
                    self.labels[head] = self._emit(s, [_NEXT, out], here)
                else:
                    self._join(head)
                self._walk(s[1] if op == "loop" else s[4], loops + [(head, out)], here + (0,))
                self._emit(None, [head], None)
                self._join(out)
            elif op == "goto":
                if s[1] in self.own:
                    self._emit(s, [("lbl", s[1])], here)
                else:
                    self._stop(s, here, True)
            elif op in _EXITS:
                n = exit_level(s)
                if len(loops) >= n:
                    self._emit(s, [loops[-n][0 if op == "cont" else 1]], here)
                else:
                    self._stop(s, here, True)
            elif op == "ret":
                self._stop(s, here, bool(s[1]))
            elif op == "unobs":
                self._stop(s, here, False)
            elif op in ("opsw", "swg"):
                self._cases(s, _stmt_bodies(s), loops, here, lambda _e: [])
            elif op == "swc":
                self.opaque.add(self._cases(s, _stmt_bodies(s), loops, here, lambda e: [e]))
            elif op == "dcall" and nxt == "swc":
                self._emit(s, [_NEXT], here)
            elif op == "dgoto" and nxt == "swg":
                self._emit(s, [_NEXT], here)
            elif op == "igoto" and (nxt == "swg" or s[2] is None):
                self._emit(s, [_NEXT], here)
            elif op in ("dcall", "dgoto", "igoto", "dbr"):
                at = self._stop(s, here, True)
                if op == "dcall":
                    self.opaque.add(at)
            else:
                raise ValueError("unexpected statement %r" % (op,))

    def sites(self):
        """``[(definitions, reads)]`` per node, the machine's own edges included.

        An opaque call defines and reads every register the procedure names (the
        ABI is not in the text) and a transfer that leaves carries them out to a
        reader the graph does not hold, so a web at either is refused. A call whose
        callee the program holds is bounded by that callee's summary instead, and
        is no refusal: the registers it names are the ones it touches."""
        out = []
        for i, s in enumerate(self.stmt):
            defs, uses = _site(s)
            if i in self.opaque:
                defs.update({r: None for r in self.regs})
                uses.update({r: {1} for r in self.regs})
            elif i in self.summed:
                may, livein = self.summed[i]
                defs.update({r: None for r in may})
                uses.update({r: {1} for r in livein})
            if i in self.escape:
                uses.update({r: {1} for r in self.regs})
            elif s is not None and s[0] == "ret":
                uses.update({r: {1} for r in self.rets})
            out.append((defs, uses))
        return out


def _reaching(graph, sites):
    """``(node, name) -> the definition keys reaching that read``; ``-1`` is entry.

    Two definitions that reach one read are one quantity, and this is the relation
    that says which. One name's definitions never reach another's, so each name is
    its own bit-vector sweep rather than a row of one joint map."""
    defs, uses, succ = {}, {}, graph.succ
    for i, (d, u) in enumerate(sites):
        for n in d:
            defs.setdefault(n, []).append(i)
        for n in u:
            uses.setdefault(n, []).append(i)
    out = {}
    for name, at in uses.items():
        made = defs.get(name, ())
        keys = [(i, name) for i in made] + [(_AT_ENTRY, name)]
        if not made:
            out.update({(i, name): frozenset(keys) for i in at})
            continue
        bit = {i: 1 << k for k, i in enumerate(made)}
        ins = [0] * len(succ)
        ins[0] = 1 << len(made)
        work = [0]
        while work:
            i = work.pop()
            cur = bit.get(i) or ins[i]
            for j in succ[i]:
                if ins[j] | cur != ins[j]:
                    ins[j] |= cur
                    work.append(j)
        for i in at:
            m = ins[i] or 1 << len(made)
            out[(i, name)] = frozenset(k for b, k in enumerate(keys) if m >> b & 1)
    return out


class ProcWebs:
    """One procedure's def-use webs, with an identity queryable per site.

    ``at_def``/``at_use`` answer with the web a statement's definition or read
    belongs to; ``of_name`` answers with every web one machine spelling carries,
    which is the denotation plan's Exhibit A measured rather than asserted."""

    __slots__ = ("entry", "webs", "_bydef", "_byuse", "_byname")

    def __init__(self, entry, stmts, rets=(), calls=None):
        graph = _Graph(stmts, rets, _ALL_REG_LOCALS & set(_named_locals(stmts, {})), calls)
        sites = graph.sites()
        reach = _reaching(graph, sites)
        union, at_use = _Union(), {}
        for i, (_d, use) in enumerate(sites):
            for n in use:
                keys = sorted(reach[(i, n)])
                for k in keys[1:]:
                    union.union(keys[0], k)
                at_use[(i, n)] = keys[0]
        body, self._bydef, self._byuse = {}, {}, {}
        for i, (defs, use) in enumerate(sites):
            for n, w in defs.items():
                web = self._web(body, union.find((i, n)), n)
                web.defs.append(graph.path[i])
                if w is not None:
                    web.def_widths.add(w)
                    if w:
                        web.widths.add(w)
                if i in graph.opaque:
                    web.refusals.add(WEB_OPAQUE)
                self._bydef[(graph.path[i], n)] = web
            for n, ws in use.items():
                web = self._web(body, union.find(at_use[(i, n)]), n)
                web.uses.append(graph.path[i])
                web.widths |= ws
                if i in graph.opaque or i in graph.escape:
                    web.refusals.add(WEB_OPAQUE)
                self._byuse[(graph.path[i], n)] = web
        self.entry = entry
        self.webs = [body[r] for r in sorted(body)]
        self._byname = {}
        for web in self.webs:
            if web.root[0] == _AT_ENTRY:
                web.refusals.add(WEB_ENTRY)
            if len(web.widths) > 1:
                web.refusals.add(WEB_WIDTH)
            self._byname.setdefault(web.name, []).append(web)

    @staticmethod
    def _web(body, root, name):
        got = body.get(root)
        if got is None:
            got = body[root] = Web(name, root)
        return got

    def at_def(self, path, name):
        """The web the definition of ``name`` at statement ``path`` belongs to."""
        return self._bydef.get((path, name))

    def at_use(self, path, name):
        """The web the read of ``name`` at statement ``path`` belongs to."""
        return self._byuse.get((path, name))

    def of_name(self, name):
        """Every web the machine spelling ``name`` carries, in definition order."""
        return tuple(self._byname.get(name, ()))

    def names(self):
        """Every local spelling the procedure carries, webs or not."""
        return sorted(self._byname)

    def counts(self):
        """``webs``, ``refused``, and one tally per refusal class."""
        out = {"webs": len(self.webs), "refused": sum(1 for w in self.webs if w.refusals)}
        for cls in WEB_REFUSALS:
            out[cls] = sum(1 for w in self.webs if cls in w.refusals)
        return out


def call_summaries(procs, play=None):
    """``entry -> (may-define, live-in)`` per procedure, registers only.

    What a raw ``call`` to that entry actually touches. The pair is the one
    ``_Info`` already computes for the header inference, read at its fixpoint, so
    the web graph and the printed signatures answer out of one summary."""
    info = _Info([(e, stmts) for e, _p, _r, stmts in procs], play)
    info.summarize()
    return {
        e: (info.may[e] & _ALL_REG_LOCALS, info.livein[e] & _ALL_REG_LOCALS) for e in info.order
    }


def webs(procs, play=None):
    """``entry -> ProcWebs`` over settled statement trees: the analysis unit.

    A procedure's declared returns are the registers its ``ret`` leaves live, so
    the 4-tuple list is the input; nothing here reads or writes a spelling, which
    is what lets a web be computed before it is nameable. The call summaries bound
    what a ``call`` node defines and reads, so a callee this program holds refuses
    nothing -- only a target outside it does."""
    calls = call_summaries(procs, play)
    return {e: ProcWebs(e, stmts, rets, calls) for e, _pa, rets, stmts in procs}


def web_counts(procs, play=None):
    """The plan's §5 web metric over one program: totals and refusals by class."""
    out = {"procs": 0, "webs": 0, "refused": 0}
    out.update({cls: 0 for cls in WEB_REFUSALS})
    for pw in webs(procs, play).values():
        out["procs"] += 1
        for k, v in pw.counts().items():
            out[k] += v
    return out


def _loc_widths(stmts, widths):
    """Definition widths per local over the forest; a pcall return is unknown."""
    for s in stmts:
        if s[0] == "asg":
            widths.setdefault(s[1], set()).add(G.store_width(s[2]))
        elif s[0] == "for":
            widths.setdefault(s[1], set()).add(1)
        elif s[0] == "pcall":
            for name in s[3]:
                widths.setdefault(name, set()).add(0)
        for b in _stmt_bodies(s):
            _loc_widths(b, widths)


def _width_webs(stmts, rets=()):
    """``name -> the definition widths of each web that name carries``."""
    out = {}
    for web in ProcWebs(None, stmts, rets).webs:
        out.setdefault(web.name, []).append(web.def_widths)
    return out


def _norm_widths(stmts, params, rets=()):
    """A local every definition of which is a word spells ``:2`` at every use.

    A width belongs to the site, so the widths are gathered per def-use web and a
    name promotes only where every web it carries is a word web: the printer
    spells names, so promoting one web of a name and not another moves the text."""
    words = {
        n
        for n, group in _width_webs(stmts, rets).items()
        if n not in params
        and any(ws == {2} for ws in group)
        and not any(ws and ws != {2} for ws in group)
    }

    def f(x):
        for n in sorted(words):
            x = _subst_loc(x, n, ("loc", n, 2))
        return x

    def apply(lst):
        for i, s in enumerate(lst):
            lst[i] = _map_exprs(s, f)
            for b in _stmt_bodies(lst[i]):
                apply(b)

    if words:
        apply(stmts)


def _def_sites(stmts, sites, banned, path=()):
    """Every asg per local over the forest; pcall returns and for counters ban."""
    for i, s in enumerate(stmts):
        if s[0] == "asg":
            sites.setdefault(s[1], []).append((stmts, i))
        elif s[0] == "for":
            banned.add(s[1])
        elif s[0] == "pcall":
            banned.update(s[3])
        for b in _stmt_bodies(s):
            _def_sites(b, sites, banned, path)


def _half_def(s):
    """``(kind, payload)`` where ``s`` defines one half of a word datum."""
    if s[0] != "asg":
        return None
    v = s[2]
    if v[0] == "mem" and v[2] == 1:
        base, idx = addr_split(v[1])
        if base is not None:
            return ("cell", base, idx)
    if is_op(v, "COPY"):
        inner = v[2][0]
        word = shifted_hi(inner)
        if word is not None and loc_width(word) == 2:
            return ("hi", word)
        if loc_width(inner) == 2:
            return ("lo", inner)
    return None


def _match_halves(a, b):
    """``(lo name, hi name, word value)`` where two adjacent asgs define one word."""
    da, db = _half_def(a), _half_def(b)
    if da is None or db is None:
        return None
    if da[0] == "cell" and db[0] == "cell" and da[2] == db[2]:
        if db[1] == da[1] + 1:
            return a[1], b[1], ("mem", a[2][1], 2)
        if da[1] == db[1] + 1:
            return b[1], a[1], ("mem", b[2][1], 2)
    if da[0] == "lo" and db[0] == "hi" and da[1] == db[1]:
        return a[1], b[1], da[1]
    if da[0] == "hi" and db[0] == "lo" and da[1] == db[1]:
        return b[1], a[1], db[1]
    return None


def _pair_window(lst, i):
    """``(j, lo, hi, word)`` pairing ``lst[i]`` with a nearby half asg, else None.

    Only asgs may stand between -- they write no memory, so the halves read at
    ``i`` what they read apart -- and none may rebind the word's locals or the
    first half; a volatile source anywhere refuses (``iota`` pins its reads)."""
    a = lst[i]
    if a[0] != "asg" or _half_def(a) is None:
        return None
    for j in range(i + 1, min(i + 4, len(lst))):
        b = lst[j]
        if b[0] != "asg":
            return None
        got = _match_halves(a, b)
        if got is None:
            names = {a[1]}
            if b[1] in names or _reads_vol(b[2]):
                return None
            continue
        lo, hi, word = got
        names = _locset(word) | {a[1]}
        for k in range(i + 1, j):
            if lst[k][1] in names or _reads_vol(lst[k][2]):
                return None
        if word[0] == "mem":
            base, idx = addr_split(word[1])
            reach = 1 + (0 if idx is None else E.mask(loc_width(idx)))
            if any(base <= v <= base + reach for v in sidprog._VOLS):
                return None
        return j, lo, hi, word
    return None


def _sub_halves(s, lo, hi, q):
    """Uses of the halves read the word local's truncs; nested bodies included."""
    word = ("loc", q, 2)
    reps = {lo: trunc_lo(word), hi: trunc_hi(word)}

    def f(x):
        for name, rep in reps.items():
            x = _subst_loc(x, name, rep)
        return x

    s = _map_exprs(s, f)
    for b in _stmt_bodies(s):
        b[:] = [_sub_halves(s2, lo, hi, q) for s2 in b]
    return s


def _pair_locals(stmts, params, rets, counter):
    """Two byte locals born as one word's halves become one word local (7.9).

    Every definition of both halves must pair in one window, neither name may be
    a parameter, return, pcall binding or counter, and every use then reads the
    word's truncs, which the pack folds take back to the word."""
    sites, banned = {}, set(params) | set(rets)
    _def_sites(stmts, sites, banned)
    pairs = {}
    for name, defs in sites.items():
        if name in banned:
            continue
        for lst, i in defs:
            got = _pair_window(lst, i)
            if got is None:
                continue
            j, lo, hi, word = got
            pairs.setdefault((lo, hi), []).append((lst, i, j, word))
    for (lo, hi), wins in sorted(pairs.items()):
        if lo in banned or hi in banned or lo == hi:
            continue
        if len(sites.get(lo, ())) != len(wins) or len(sites.get(hi, ())) != len(wins):
            continue
        q = "q%d" % counter[0]
        counter[0] += 1
        for lst, i, j, word in sorted(wins, key=lambda w: -w[1]):
            lst[i] = ("asg", q, word)
            del lst[j]
        stmts[:] = [_sub_halves(s2, lo, hi, q) for s2 in stmts]
        return True  # one pair per call: the next round re-reads the moved positions
    return False


_NEGATE = {"INT_EQUAL": "INT_NOTEQUAL", "INT_NOTEQUAL": "INT_EQUAL"}


def _tidy_ifs(stmts):
    """Empty then-arms flip to their negation, and ``ifnot (a != b)`` folds to ``if``."""
    for i, s in enumerate(stmts):
        for b in _stmt_bodies(s):
            _tidy_ifs(b)
        if s[0] != "if":
            continue
        if not s[3] and s[4]:
            s = ("if", "ifnot" if s[1] == "if" else "if", s[2], s[4], s[3])
        if s[1] == "ifnot" and s[2][0] == "op" and s[2][1] in _NEGATE:
            s = ("if", "if", ("op", _NEGATE[s[2][1]], s[2][2], s[2][3]), s[3], s[4])
        stmts[i] = s


def _arm_locals(stmts):
    """Locals the arm may define, None where a call binds unseen."""
    names = set()
    for s in stmts:
        got = _label_defs(s)
        if got is None:
            return None
        names |= got[0]
    return names


def _unify(a, b, ctx):
    """``a`` equals ``b`` modulo a bijection over arm-local names, grown in ``ctx``."""
    if a == b:
        return True
    if not (isinstance(a, tuple) and isinstance(b, tuple)) or len(a) != len(b):
        return False
    if a[0] == "loc" and b[0] == "loc":
        return _pair_names(a[1], b[1], ctx)
    return all(_unify(x, y, ctx) for x, y in zip(a, b))


def _pair_names(na, nb, ctx):
    """``nb`` may be read as ``na``: a fresh pairing, and ``na`` is free to take.

    ``na`` must bind nothing in the other arm -- renaming ``t1`` to a name that
    arm already assigns merges two locals into one, which is how a slot named in
    both arms (rung d0) turns a carry operand into the sum it fed."""
    mine, theirs, sigma, taken = ctx
    if nb in sigma:
        return sigma[nb] == na
    if na == nb:
        return True
    if na in mine and nb in theirs and na not in taken and na not in theirs:
        sigma[nb] = na
        taken.add(na)
        return True
    return False


def _unify_stmt(sa, sb, ctx):
    """The two arm statements are the same statement under the growing bijection."""
    if sa[0] != sb[0] or sa[0] not in ("asg", "st"):
        return False
    if sa[0] == "asg":
        return _pair_names(sa[1], sb[1], ctx) and _unify(sa[2], sb[2], ctx)
    return _unify(sa[1], sb[1], ctx) and _unify(sa[2], sb[2], ctx)


def _rename_stmt(s, sigma):
    if s[0] == "asg" and s[1] in sigma:
        s = ("asg", sigma[s[1]], s[2])
    elif s[0] == "for" and s[1] in sigma:
        s = ("for", sigma[s[1]], s[2], s[3], s[4])
    elif s[0] == "pcall":
        s = ("pcall", s[1], s[2], [sigma.get(n, n) for n in s[3]])
    for nb, na in sigma.items():
        s = _map_exprs(s, lambda x, _b=nb, _a=na: _subst_loc(x, _b, ("loc", _a)))
    for b in _stmt_bodies(s):
        b[:] = [_rename_stmt(s2, sigma) for s2 in b]
    return s


def _hoistable(s, cond):
    """Moving ``s`` above the pure ``cond`` changes neither: no write ``cond`` reads."""
    if s[0] == "asg":
        return s[1] not in _locset(cond)
    return not _reads_mem(cond)


def _factor_one(s, root):
    """``(head, if, tail)`` with the arms' shared statements moved out, else None."""
    a, b = list(s[3]), list(s[4])
    mine, theirs = _arm_locals(a), _arm_locals(b)
    if mine is None or theirs is None:
        return None
    ctx = (mine, theirs, {}, set())
    head = 0
    while (
        head < min(len(a), len(b))
        and _hoistable(a[head], s[2])
        and _unify_stmt(a[head], b[head], ctx)
    ):
        head += 1
    tail = 0
    while tail < min(len(a), len(b)) - head and _unify_stmt(a[-1 - tail], b[-1 - tail], ctx):
        tail += 1
    sigma = ctx[2]
    if (head == 0 and tail == 0) or set(sigma) & set(sigma.values()):
        return None
    inside = sum(_use_count(s, n) for n in sigma)
    total = sum(_use_count(x, n) for n in sigma for x in root)
    if total != inside:
        return None  # a renamed arm local leaks outside the if
    b = [_rename_stmt(x, sigma) for x in b]
    cut = len(a) - tail
    kept = ("if", s[1], s[2], a[head:cut], b[head : len(b) - tail])
    keep = [kept] if kept[3] or kept[4] or _reads_vol(s[2]) else []
    return a[:head], keep, a[cut:]


def _factor_ifs(stmts, root):
    changed = False
    i = 0
    while i < len(stmts):
        s = stmts[i]
        for b in _stmt_bodies(s):
            changed |= _factor_ifs(b, root)
        got = None
        if s[0] == "if" and s[3] and s[4]:
            got = _factor_one(s, root)
        if got is None:
            i += 1
            continue
        pre, kept, post = got
        stmts[i : i + 1] = pre + kept + post
        changed = True
        i += len(pre) + len(kept) + len(post)
    return changed


def must_defines(procs, play=None):
    """``entry -> the register locals its body surely defines``, callees included.

    What a raw ``call`` binds, as a reader of the text takes it: the callee runs, so
    every register on every path through it is defined after the statement. Name-keyed
    and not web-keyed (A1): that reader has the spelling and no web to ask about."""
    info = _Info([(e, stmts) for e, _p, _r, stmts in procs], play)
    info.summarize()
    return {e: info.must[e] & _ALL_REG_LOCALS for e in info.order}


def _respell_calls(stmts, info, old):
    """Move every ``pcall``'s arguments onto its callee's re-derived parameter list.

    A parameter that survives keeps the argument already spelled at it -- the passes
    may have folded a value in there -- so the refresh writes only what it adds, and
    a call whose callee's signature is unmoved is textually unmoved."""
    for i, s in enumerate(stmts):
        if s[0] == "pcall" and info.callable_.get(s[1]):
            was = dict(zip(old.get(s[1], ()), s[2]))
            args = [was[p] if p in was else ("loc", p) for p in info.params[s[1]]]
            stmts[i] = ("pcall", s[1], args, list(info.rets[s[1]]))
        for b in _stmt_bodies(stmts[i]):
            _respell_calls(b, info, old)


def resign(procs, play):
    """Re-derive every signature over the settled bodies, ``pcall`` arguments with them.

    ``repolish`` holds ``params``/``rets`` while its passes move the bodies, so a
    register they leave live-in is read under a header that never took it
    (`International_Karate`'s `sub_AE0C`). Nothing promotes: only spellings move."""
    old = {e: list(pa) for e, pa, _r, _s in procs}
    info = summaries([(e, stmts) for e, _p, _r, stmts in procs], play)
    for _e, _pa, _r, stmts in procs:
        _respell_calls(stmts, info, old)
    for k, (e, _pa, _r, stmts) in enumerate(procs):
        procs[k] = (e, list(info.params[e]), list(info.rets[e]), stmts)
    return info


def repolish(procs, play, regions=None):
    """A prune+inline fixpoint after the rungs: the 16-bit lift writes new temps.

    Signatures hold across it -- ``params``/``rets`` stay as the build fixed them
    and only the bodies move, ahead of rung (f) keying ``resolved`` by the final
    address expressions -- and ``resign`` re-derives them once the passes settle."""
    info = _Info([(e, stmts) for e, _p, _r, stmts in procs], play)
    info.summarize()
    info.params = {e: list(p) for e, p, _r, _s in procs}
    info.rets = {e: list(r) for e, _p, r, _s in procs}
    widths = {}
    for _e, _p, _r, st in procs:
        _loc_widths(st, widths)
    qs = [int(n[1:]) for n in widths if re.fullmatch(r"q\d+", n)]
    counter = [max(qs) + 1 if qs else 0]  # a pair's word name is born once, ever
    for _round in range(16):
        pruned = _prune(info)
        inlined = _inline(info)
        factored = False
        for e, stmts in info.procs.items():
            _norm_widths(stmts, info.params.get(e, ()), info.rets.get(e, ()))
            factored |= _pair_locals(stmts, info.params.get(e, ()), info.rets.get(e, ()), counter)
        gotos = {
            e: {s[1] for _v, _i, s in envs(st) if s[0] == "goto"} for e, st in info.procs.items()
        }
        for e, stmts in info.procs.items():
            foreign = frozenset(t for e2, ts in gotos.items() if e2 != e for t in ts)
            _fold_words(stmts, regions, foreign=foreign)
            _pin_targets(stmts)
            factored |= _factor_ifs(stmts, stmts)
        if not (pruned or inlined or factored):
            break
    for _e, _p, _r, stmts in procs:
        _tidy_ifs(stmts)


def render_lines(procs, resolved=_NORES, pairs=None):
    """frameprog text lines for analysed (or parsed) procedures.

    ``pairs`` (lo table base -> hi table base, off the decl roles) scopes the
    split-pair rendering: a pack over a declared pair reads ``lo[i]:2`` and its
    half-store pair writes it, the byte spelling staying the parsed form."""
    _PAIRS.clear()
    _PAIRS.update(pairs or {})
    try:
        printer = _Printer(resolved)
        for entry, params, rets, stmts in procs:
            printer.proc(entry, stmts, params, rets)
        return printer.out
    finally:
        _PAIRS.clear()
