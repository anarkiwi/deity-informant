"""idioms: the stage-1 catalog -- one recognizer per idiom, and what it reduces to.

A recognizer consumes its node plus the glue its idiom absorbs (zext, shift and
mask scaffolding) and returns the real operands, so a walk recursing into what a
row returns accounts every node of an obligation or names the gap (stage 1).
"""

from __future__ import annotations

from collections import Counter, namedtuple

from . import frameproc as P
from . import grammar as G

SID_LO, SID_HI = 0xD400, 0xD41C  # the boundary byte fidelity is owed at
UNKNOWN = "named-unknown"  # a row that names its shape and reduces it to nothing yet
UNRESOLVED = "unresolved"  # a store the base/index form does not name: no field, still owed
DEPTH = 8  # substitutions per slice: a body that rebinds must still terminate
STACK_LO, STACK_HI = 0x100, 0x1FF

_ALU = frozenset(("INT_ADD", "INT_SUB", "INT_AND", "INT_OR", "INT_XOR", "INT_LEFT", "INT_RIGHT"))
_CMP = frozenset(
    (
        "INT_LESS",
        "INT_SLESS",
        "INT_LESSEQUAL",
        "INT_SLESSEQUAL",
        "INT_EQUAL",
        "INT_NOTEQUAL",
        "INT_CARRY",
        "INT_SCARRY",
    )
)
_CARRY = frozenset(("INT_CARRY", "INT_SCARRY"))
_BITS = frozenset((0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80))
_SHIFTS = frozenset(("INT_LEFT", "INT_RIGHT"))


# ---- the shapes the exemplars spell: each returns its operands, or None ----------
def pack(n):
    """``hi << 8 | lo`` over two byte values: the 6502's only spelling of a word.

    ``frameproc._le_bytes`` is this shape restricted to the leaf sides rung (d)
    fuses; a slice packs arbitrary byte terms, so the sides are read by width."""
    if not P.is_op(n, "INT_OR", 2, 2):
        return None
    for hi, lo in P.commuted(n[2]):
        k = hi[2][1] if P.is_op(hi, "INT_LEFT", 2) else None
        if k is None or k[0] != "const" or k[1] != 8:
            continue
        h, l = P.strip_zext(hi[2][0]), P.strip_zext(lo)
        if P.loc_width(h) == 1 and P.loc_width(l) == 1:
            return l, h
    return None


def shift_pair(n):
    """``(lo >> 1) | ((hi & 1) << 7)`` and its mirror: one word shifted as two bytes."""
    if not P.is_op(n, "INT_OR", arity=2):
        return None
    for a, b in P.commuted(n[2]):
        if not P.is_op(a, "INT_RIGHT") and not P.is_op(a, "INT_LEFT"):
            continue
        if not P.is_op(b, "INT_LEFT") and not P.is_op(b, "INT_RIGHT"):
            continue
        inner = b[2][0]
        if P.is_op(inner, "INT_AND") and inner[2][1][0] == "const":
            return (a[2][0], inner[2][0]) if inner[2][1][1] in (0x01, 0x80) else None
    return None


def flag_bit(n):
    """``(OP(..) & BIT) != 0``: a status flag left as a mask-and-compare.

    The masked value must be an operation's result, since that is what the 6502
    sets N and Z from; the same shape over a plain load is the driver testing a
    bit of its own state byte."""
    if n[0] != "op" or n[1] not in ("INT_EQUAL", "INT_NOTEQUAL"):
        return None
    for a, b in P.commuted(n[2]):
        if b[0] != "const" or b[1] != 0 or not P.is_op(a, "INT_AND", arity=2):
            continue
        k, v = a[2][1], P.strip_zext(a[2][0])
        ok = k[0] == "const" and k[1] in _BITS and v[0] == "op" and v[1] not in _CMP
        return (v,) if ok else None
    return None


def _pair_row(n):
    """Two declared byte columns read at one index: rung (d)'s own pack."""
    got = P.packed_cells(n)
    return None if got is None else (() if got[2] is None else (got[2],))


def _lane_insert(n):
    """``(w & K) | v``: one byte lane written into a word, the rest read back."""
    if not P.is_op(n, "INT_OR", 2, 2):
        return None
    for a, v in P.commuted(n[2]):
        if P.is_op(a, "INT_AND", 2, 2) and a[2][1][0] == "const":
            return a[2][0], P.strip_zext(v[2][0] if P.is_op(v, "INT_LEFT", 2) else v)
    return None


def _hi_byte(n):
    """``COPY(v >> 8)``: a word read as its high byte."""
    if not P.is_op(n, "COPY", arity=1):
        return None
    got = P.shifted_hi(n[2][0])
    return None if got is None else (got,)


def _lo_byte(n):
    """``COPY(v)`` over a word: a word read as its low byte."""
    if not P.is_op(n, "COPY", arity=1) or P.loc_width(n[2][0]) != 2:
        return None
    return (n[2][0],)


def _carry_glue(x):
    """``carry(a,b) | carry(a+b,c)``: the carry-out an ADC chain threads on."""
    if x[0] == "op" and x[1] in _CARRY:
        return True
    return P.is_op(x, "INT_OR", arity=2) and all(_carry_glue(c) for c in x[2])


def _adc_chain(n):
    """``a + b + carry``: the ADC the machine has no wide add for."""
    if not P.is_op(n, "INT_ADD", arity=3):
        return None
    a, b, c = n[2]
    return (a, b) if _carry_glue(c) else (a, b, c)


def _sbc_chain(n):
    """``a - (b + (1 - borrow))``: SBC, the borrow spelled as a comparison."""
    if not P.is_op(n, "INT_SUB", arity=2):
        return None
    a, rhs = n[2]
    if not P.is_op(rhs, "INT_ADD", arity=2):
        return None
    for b, borrow in P.commuted(rhs[2]):
        if P.is_op(borrow, "INT_SUB", arity=2) and borrow[2][0][0] == "const":
            return (a, b)
    return None


def _shift_chain(n):
    """Constant shifts nested one on another: the 6502 shifts one place at a time."""
    if n[0] != "op" or n[1] not in _SHIFTS or n[2][1][0] != "const":
        return None
    inner, depth = n[2][0], 1
    while inner[0] == "op" and inner[1] == n[1] and inner[2][1][0] == "const":
        inner, depth = inner[2][0], depth + 1
    return (inner,) if depth > 1 else None


def _table_row(n):
    """``mem[base + i]``: a row of the datum the base declares."""
    if n[0] != "mem":
        return None
    got = P.addr_range(n[1], n[2])
    return None if got is None or got[1] is None or got[2] else (got[1],)


def _zp_row(n):
    """``mem[zext(b)]``: a page-zero row whose index arithmetic wrapped in 8 bits.

    ``LDA $zz,X`` adds in the byte domain, so the address is a widened byte term and
    no width-2 base+index form names it; the const side is the base the row is off."""
    if n[0] != "mem" or not P.is_op(n[1], "INT_ZEXT", arity=1):
        return None
    b = n[1][2][0]
    if P.loc_width(b) != 1:
        return None
    if P.is_op(b, "INT_ADD", arity=2) or P.is_op(b, "INT_SUB", arity=2):
        rest = tuple(x for x in b[2] if x[0] != "const")
        return rest if len(rest) < 2 else (b,)
    return (b,)


def _cell_read(n):
    """``mem[$ADDR]``: one named cell, byte or word."""
    return () if n[0] == "mem" and n[1][0] == "const" and n[1][2] == 2 else None


def _stack_slot(n):
    """A byte of the stack page: the address must set bit 8 and can set no more."""
    if n[0] != "mem" or P.addr_floor(n[1]) < STACK_LO or P.addr_bits(n[1]) > STACK_HI:
        return None
    return (n[1],)


def _ptrish(x):
    """A word an address may root on: not a constant, not the reader's own zext."""
    return P.loc_width(x) == 2 and x[0] != "const" and not P.is_op(x, "INT_ZEXT")


def _deref_row(n):
    """``mem[ptr + i]``: a row off a pointer the address computes rather than names."""
    if n[0] != "mem":
        return None
    a = n[1]
    if P.is_op(a, "INT_ADD", 2, 2):
        for p, i in P.commuted(a[2]):
            if _ptrish(p):
                return p, P.strip_zext(i)
        return None
    return (a,) if _ptrish(a) else None


def _mask_const(n):
    """``v & K``: a bit field selected out of a byte."""
    if not P.is_op(n, "INT_AND", arity=2):
        return None
    for v, k in P.commuted(n[2]):
        if k[0] == "const":
            return (v,)
    return None


def _set_const(n):
    """``v | K``: control bits set into a byte."""
    if not P.is_op(n, "INT_OR", arity=2):
        return None
    for v, k in P.commuted(n[2]):
        if k[0] == "const":
            return (v,)
    return None


def _alu(n):
    """The dialect's own arithmetic and logic, over operands it does not absorb."""
    return n[2] if n[0] == "op" and n[1] in _ALU else None


def _widen(n):
    return n[2] if P.is_op(n, "INT_ZEXT", arity=1) else None


def _carry_value(n):
    """``carry(..)`` as a value: the flag outlived the operation that set it."""
    return n[2] if n[0] == "op" and n[1] in _CARRY else None


def _compare_value(n):
    """A comparison used as a value: a flag no use has folded it into."""
    return n[2] if n[0] == "op" and n[1] in _CMP else None


def _const(n):
    return () if n[0] == "const" else None


def _local(n):
    return () if n[0] == "loc" else None


Row = namedtuple("Row", "id form match")

ROWS = (
    Row("pair-row", "u16 cell/table row", _pair_row),
    Row("word-pack", "u16 value", pack),
    Row("lane-insert", "u16 lane update", _lane_insert),
    Row("hi-byte", "u16 high byte", _hi_byte),
    Row("lo-byte", "u16 low byte", _lo_byte),
    Row("shift-pair", "u16 shift", shift_pair),
    Row("adc-chain", "wide add", _adc_chain),
    Row("sbc-chain", "wide subtract", _sbc_chain),
    Row("shift-chain", "one shift", _shift_chain),
    Row("flag-bit", "flag", flag_bit),
    Row("table-row", "table[i]", _table_row),
    Row("zp-row", "zp table[i]", _zp_row),
    Row("cell-read", "cell", _cell_read),
    Row("stack-slot", UNKNOWN, _stack_slot),
    Row("deref-row", "*ptr[i]", _deref_row),
    Row("mask-const", "field select", _mask_const),
    Row("set-const", "field set", _set_const),
    Row("alu-op", "wide operator", _alu),
    Row("widen", "width coercion", _widen),
    Row("carry-value", UNKNOWN, _carry_value),
    Row("compare-value", UNKNOWN, _compare_value),
    Row("const-literal", "const", _const),
    Row("local-read", "local", _local),
)

FORMS = {r.id: r.form for r in ROWS}


def size(n):
    """Nodes in ``n``: what ``cover`` accounts plus the glue its rows absorb."""
    return 1 + sum(size(c) for c in P._kids(n))


def cover(node):
    """``(row counts, unaccounted nodes)`` over every node of ``node``.

    Rows are tried in order and the walk recurses into the operands the matching
    row hands back, so a row's glue is consumed with it; a node no row matches is
    recorded and its children walked anyway, so one gap cannot mask the rest."""
    counts, gaps, stack = Counter(), [], [node]
    while stack:
        n = stack.pop()
        for row in ROWS:
            got = row.match(n)
            if got is not None:
                counts[row.id] += 1
                stack.extend(got)
                break
        else:
            gaps.append(n)
            stack.extend(P._kids(n))
    return counts, gaps


Obligation = namedtuple("Obligation", "kind entry site at base field value addr")

_SITE = {"label": 1, "opsw": 1}  # statements carrying the pc a disassembly is read at


def resolve(env, n, at, depth=DEPTH):
    """``n`` with each local replaced by the definition in force where it was read.

    A statement list is not SSA, so a substituted body is read at the *definer's*
    position and not at the reader's; ``depth`` bounds the substitution so a list
    that rebinds a name still terminates."""
    if n[0] == "loc":
        got = None if depth <= 0 else env.value(n[1], at)
        return n if got is None else resolve(env, got[1], got[0], depth - 1)
    kids = P._kids(n)
    return n if not kids else P._rebuild(n, [resolve(env, c, at, depth) for c in kids])


def state_cells(prog):
    """``{cell address: field name}`` over the frame-surviving rows of ``prog.state``.

    A row names its cell through the symbol table where the tune aliases it and
    through the canonical name otherwise, which is how the row was spelled; an
    array names only its base, since its stores are rows off that base."""
    named = {v: k for k, v in prog.symbols.items()}
    out = {}
    for field in prog.state:
        name, width, array = field[0], field[1], field[2]
        base = named[name] if name in named else G.name_addr(name)
        if base is None:
            continue
        for a in range(base, base + (1 if array else width)):
            out[a] = name
    return out


def seats(prog):
    """Every labelled pc of ``prog``, sorted: a cite range runs from one seat to the next."""
    out, stack = set(), [s for _e, _p, _r, stmts in prog.procs for s in stmts]
    while stack:
        s = stack.pop()
        if s[0] in _SITE:
            out.add(s[_SITE[s[0]]])
        stack.extend(x for body in P._stmt_bodies(s) for x in body)
    return sorted(out)


def obligations(prog):
    """``(SID-store slices, cell updates)``: the two enumerations stage 1 must cover.

    A slice is the store's value expression with locals resolved, which is the
    dataflow the store depends on; a cell update is the same for a store landing
    on a frame-surviving cell's base."""
    cells = state_cells(prog)
    sid, upd = [], []
    for entry, _params, _rets, stmts in prog.procs:
        _slices(stmts, entry, entry, cells, sid, upd)
    return sid, upd


def _reaches(addr, width, targets):
    """The store may land on one of ``targets``: the bits its address can set bound it."""
    m = P.addr_bits(addr)
    return any((c - d) & ~m == 0 for c in targets for d in range(width))


def _target(addr, width, cells):
    """``(kind, base, field)`` the store lands on, else None: the ONE attribution rule.

    An address the base/index form does not name still bounds the bytes it may
    reach, so a store that may hit the boundary or a state cell is enumerated at
    an unknown base rather than dropped: completeness cannot rest on it resolving."""
    got = P.addr_range(addr, width)
    if got is not None:
        base = got[0]
        if SID_LO <= base <= SID_HI:
            return "sid", base, G.addr_name(base)
        return ("cell", base, cells[base]) if base in cells else None
    if _reaches(addr, width, range(SID_LO, SID_HI + 1)):
        return "sid", None, UNRESOLVED
    return ("cell", None, UNRESOLVED) if _reaches(addr, width, cells) else None


def _slices(stmts, entry, site, cells, sid, upd):
    """Both enumerations over one statement list, nested bodies included."""
    env = P.Defs(stmts)
    for k, s in enumerate(stmts):
        if s[0] in _SITE:
            site = s[_SITE[s[0]]]
        for body in P._stmt_bodies(s):
            _slices(body, entry, site, cells, sid, upd)
        if s[0] != "st":
            continue
        got = _target(s[1], G.store_width(s[2]), cells)
        if got is None:
            continue
        kind, base, field = got
        ob = Obligation(kind, entry, site, k, base, field, resolve(env, s[2], k), s[1])
        (sid if kind == "sid" else upd).append(ob)
