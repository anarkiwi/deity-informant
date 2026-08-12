"""roles: the update-shape reading of a persistent cell (stage 2's precondition).

The stage-1 catalog accounted *dataflow* shapes; a role is read off a cell's own
*update* term plus where the program reads the cell back. Recognition licenses
nothing, so an un-roled field stays a legal ``u8``/``u16``.
"""

from __future__ import annotations

from collections import defaultdict, namedtuple

from . import framefuse as FF
from . import frameproc as P
from . import idioms

ROLES = ("cursor", "accumulator", "counter", "flags", "parameter", "vm")

STEP_UP, STEP_DOWN, DEC, FIELD, SET = "step-up", "step-down", "dec", "field", "set"
SHAPES = (STEP_UP, STEP_DOWN, DEC, FIELD, SET)

_BITOPS = frozenset(("INT_AND", "INT_OR", "INT_XOR"))


def _signed(k, w):
    """``k`` read as a signed constant at ``w`` bytes: the machine's own -1 is ``$FF``.

    A 6502 ``DEC`` lifts to ``x + $FF``, so a countdown is a modular step up until
    the delta is read at the width the step is taken at."""
    top = 1 << (8 * w)
    k &= top - 1
    return k - top if k >= top >> 1 else k


def _self(n, cells):
    """``n`` reads the field itself: a byte/word of it, or its two columns packed."""
    if n[0] == "mem":
        got = P.addr_range(n[1], n[2])
        return got is not None and got[0] in cells
    got = P.packed_cells(n)
    return got is not None and (got[0] in cells or got[1] in cells)


def reads_self(n, cells):
    """Some subterm of ``n`` reads the field's *value*, addresses excluded.

    A cell read inside an address is the program using it as a pointer or an
    index, which is a cursor's evidence and not an arithmetic self-reference:
    ``s' = mem[s + y]`` walks a block, it does not step ``s``."""
    stack = [n]
    while stack:
        x = stack.pop()
        if _self(x, cells):
            return True
        stack.extend(P._kids(x) if x[0] != "mem" else ())
    return False


def _peel(n, cells):
    """The update term under the coercions and the read-back lane wrapper.

    A width coercion is no update; ``(w & K) | v`` writes one lane of a word it
    reads back whole, so the lane's value is the shape; and a byte store of
    ``v >> 8`` is the high lane of a wide update, which is that update's shape."""
    while True:
        if n[0] == "op" and n[1] in ("COPY", "INT_ZEXT") and len(n[2]) == 1:
            n = n[2][0]
            continue
        got = idioms._lane_insert(n)
        if got is not None and _self(got[0], cells):
            n = got[1]
            continue
        got = P.shifted_hi(n)
        if got is not None and reads_self(got, cells):
            n = got
            continue
        return n


def _step(n, cells):
    """``(sign, delta)`` where the update adds to or subtracts from the field."""
    got = idioms._adc_chain(n) or (n[2] if P.is_op(n, "INT_ADD") else None)
    if got is not None:
        rest = [x for x in got if not _self(x, cells)]
        return (1, rest[0]) if len(rest) == len(got) - 1 else None
    got = idioms._sbc_chain(n) or (n[2] if P.is_op(n, "INT_SUB", arity=2) else None)
    if got is not None and _self(got[0], cells):
        return -1, got[1]
    return None


def _mask_bound(n):
    """``(x, K)`` where ``n`` is ``x & K``: an accumulator's bound is spelled as a mask."""
    if not P.is_op(n, "INT_AND", arity=2):
        return None
    for x, k in P.commuted(n[2]):
        if k[0] == "const":
            return x, k[1]
    return None


def _carries(n, cells):
    """The cell reaches the top of ``n`` through bitwise operators alone."""
    if _self(n, cells):
        return True
    if n[0] != "op" or n[1] not in _BITOPS:
        return False
    kids = [(_carries(c, cells), reads_self(c, cells)) for c in n[2]]
    return any(b for b, _r in kids) and all(b or not r for b, r in kids)


def _bitwise(n, cells):
    """The update recombines the cell by bitwise operators: a bit-field write.

    The top must be one of those operators -- a bare read of the cell writes it
    back unchanged -- so the bits the update does not write are the preserved."""
    return n[0] == "op" and n[1] in _BITOPS and _carries(n, cells)


def reading(value, cells):
    """``(shape, mask)``: the update's form, and the constant its step is taken under.

    The mask is the bound the program itself spells (stage 2's accumulator), kept
    here because the declaration carries it as evidence; it is None where the step
    is unmasked or the update has no step at all."""
    n = _peel(value, cells)
    if not reads_self(n, cells) or _self(n, cells):
        return SET, None  # a copy of the cell is an assignment, not a shape of its own
    got, mask = _step(n, cells), None
    if got is None:
        inner = _mask_bound(n)
        got = None if inner is None else _step(inner[0], cells)
        if got is not None:
            n, mask = inner[0], inner[1]
    if got is None:
        return (FIELD if _bitwise(n, cells) else None), None
    sign, delta = got
    if delta[0] != "const":
        return (STEP_UP if sign > 0 else STEP_DOWN), mask
    k = sign * _signed(delta[1], P.loc_width(n))
    return (DEC if k == -1 else (STEP_UP if k >= 0 else STEP_DOWN)), mask


def shape(value, cells):
    """The update's form relative to the cell it lands on, or None for residue."""
    return reading(value, cells)[0]


def _bases(n, out):
    """Const bases of every memory read inside ``n``, at any width."""
    stack = [n]
    while stack:
        x = stack.pop()
        if x[0] == "mem":
            base, _idx = P.addr_split(x[1])
            if base is not None:
                out.add(base)
        stack.extend(P._kids(x))


def _stmts(prog):
    stack = [s for _e, _p, _r, stmts in prog.procs for s in stmts]
    while stack:
        s = stack.pop()
        yield s
        stack.extend(x for body in P._stmt_bodies(s) for x in body)


def read_sites(prog):
    """``(address cells, dispatch subjects)``: the two read-backs a role turns on.

    A cell an address computes on is an index into a block, which is what makes a
    stepped cell a cursor; the cell an operator dispatch switches on is a VM
    register whatever wrote it."""
    addr, sw = set(), set()
    for s in _stmts(prog):
        if s[0] == "opsw":
            sw.add(s[1])
        for n in P._stmt_exprs(s):
            stack = [n]
            while stack:
                x = stack.pop()
                if x[0] == "mem":
                    _bases(x[1], addr)
                stack.extend(P._kids(x))
        if s[0] == "st":
            _bases(s[1], addr)  # a store's address is an address position with no mem node over it
    return addr, sw


def role(shapes, base, addr_cells, switch_cells):
    """The role a cell's update shapes name, or None where a shape has no form.

    Order is by strength of evidence: an unshaped update is residue whatever else
    the cell does, then the dispatch subject, then the cell an address reads
    (advanced or rewritten, both cursors), then the update shape alone."""
    if None in shapes:
        return None
    if base in switch_cells:
        return "vm"
    if base in addr_cells:
        return "cursor"
    if DEC in shapes:
        return "counter"
    if shapes & {STEP_UP, STEP_DOWN}:
        return "accumulator"
    return "flags" if FIELD in shapes else "parameter"


Update = namedtuple("Update", "base field shape mask value site entry")


def updates(prog):
    """One ``Update`` per witnessed cell-update slice, its shape read off the term.

    A fused pair's lane reads are the word's truncs (rung (d)), so the term is read
    back through ``framefuse.unlane`` and a lane step is the step it always was."""
    cells = idioms.state_cells(prog)
    byfield = defaultdict(set)
    for a, f in cells.items():
        byfield[f].add(a)
    _sid, upd = idioms.obligations(prog)
    out = []
    for ob in upd:
        if ob.base is not None:
            got, mask = reading(FF.unlane(ob.value, ob.base), byfield[ob.field])
            out.append(Update(ob.base, ob.field, got, mask, ob.value, ob.site, ob.entry))
    return out


def census(prog):
    """``(cell -> role, cell -> shapes, residue updates, cell -> mask)``: one reading.

    A cell carrying an unshaped update is left un-roled; the residue is the
    unshaped updates themselves, so a common residual shape names a missing role.
    A cell's mask is evidence only where its masked steps agree on one constant."""
    addr_cells, switch_cells = read_sites(prog)
    got = updates(prog)
    shapes, masks = defaultdict(set), defaultdict(set)
    for u in got:
        shapes[u.base].add(u.shape)
        if u.mask is not None:
            masks[u.base].add(u.mask)
    roles = {b: role(s, b, addr_cells, switch_cells) for b, s in shapes.items()}
    bounds = {b: k.pop() for b, k in masks.items() if len(k) == 1}
    return roles, dict(shapes), [u for u in got if u.shape is None], bounds
