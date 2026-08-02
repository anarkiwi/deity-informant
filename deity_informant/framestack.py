"""framestack: rung (d0), the data stack destacked (docs/frameprog.md 5).

``PHA``/``PLA`` at a statically known ``sp`` lower to a store and a load at a
constant stack-page cell, so a register spill reads back as tune state. A slot
every read of which a store dominates, no control transfer between, is a local.
"""

from __future__ import annotations

from . import expr as E
from . import framefuse as FF
from . import frameproc
from . import grammar as G
from .structured import Proof

_PAGE = range(0x0100, 0x0200)
_STRAIGHT = ("asg", "st", "if")  # the only forms that transfer no control


def _addr(cell):
    return ("const", cell, 2)


def _span(addr):
    """``(const base, cells the index spans)``; base None is an unresolvable address."""
    base, idx = FF._addr_split(addr)
    return base, 0 if idx is None else E.mask(FF._w(idx))


def _mems(s):
    """Every ``mem`` node of a statement's expression operands."""
    stack = list(frameproc._stmt_exprs(s))
    while stack:
        x = stack.pop()
        if x[0] == "mem":
            yield x
            stack.append(x[1])
        elif x[0] == "op":
            stack.extend(x[2])


def _hit(addr, width, cell):
    """``(may touch ``cell``, address unresolvable)`` of a ``width``-byte access."""
    base, span = _span(addr)
    if base is None:
        return False, True
    return base <= cell <= base + span + width - 1, False


def _accesses(s):
    """``(address, byte width)`` of every load and store the statement performs."""
    out = [(x[1], x[2]) for x in _mems(s)]
    return out + [(s[1], G.store_width(s[2]))] if s[0] == "st" else out


def _footprint(stmts):
    """Stack-page cells the procedure's resolvable addresses may touch."""
    out = set()
    for s in FF.stmts_of(stmts):
        for addr, width in _accesses(s):
            base, span = _span(addr)
            if base is not None:
                out.update(range(max(base, _PAGE[0]), min(base + span + width, _PAGE[-1] + 1)))
    return out


def _candidates(stmts):
    """Const stack-page cells some store in the procedure addresses."""
    return {
        s[1][1]
        for s in FF.stmts_of(stmts)
        if s[0] == "st" and s[1][0] == "const" and s[1][2] == 2 and s[1][1] in _PAGE
    }


class _Slot:
    """One const stack cell: the must-def walk over a procedure and its refusal."""

    __slots__ = ("cell", "stores", "reads", "why")

    def __init__(self, cell):
        self.cell = cell
        self.stores = self.reads = 0
        self.why = None

    def _refuse(self, why):
        self.why = self.why or why

    def run(self, stmts, shared):
        """The walk over a whole procedure, plus the both-ends and privacy premises."""
        self._walk(stmts, False)
        if not (self.stores and self.reads):
            self._refuse("the slot is not both stored and read in the procedure")
        if self.cell in shared:
            self._refuse("another procedure may touch the slot")
        return self

    def _walk(self, stmts, defd):
        """Must-def over one statement list; returns the state it leaves behind.

        A control transfer kills the definition, so a read reached through one is
        undominated. The arms of an ``if`` intersect: a slot written in both arms
        and read in the shared tail is a local with two definitions."""
        want = ("mem", _addr(self.cell), 1)
        for s in stmts:
            k = s[0]
            defd = defd and k in _STRAIGHT
            for addr, width in _accesses(s):
                near, blind = (
                    (False, False) if (addr, width) == want[1:] else _hit(addr, width, self.cell)
                )
                if near:
                    self._refuse("another resolvable access may touch the slot")
                elif blind and defd:
                    self._refuse("an unresolvable address may alias the live slot")
            for x in frameproc._stmt_exprs(s):
                got = _count(x, want)
                self.reads += got
                if got and not defd:
                    self._refuse("a read is not dominated by a store of the slot")
            if k == "st" and (s[1], G.store_width(s[2])) == want[1:]:
                self.stores += 1
                defd = True
            if k == "if":
                arms = [self._walk(b, defd) for b in frameproc._stmt_bodies(s)]
                defd = all(arms)  # both arms walk, whatever the first leaves behind
            else:
                for b in frameproc._stmt_bodies(s):
                    self._walk(b, False)
        return defd

    def proof(self, name):
        """The rung-(d0) record: the premise counts, and the refusal or the local."""
        return Proof(
            self.cell,
            "stack",
            "refused" if self.why else "named",
            (self.cell,),
            "stack slot $%04X: %d store(s), %d read(s); %s"
            % (self.cell, self.stores, self.reads, self.why or "data temporary, local %s" % name),
        )


def _count(n, want):
    """Occurrences of the exact byte load ``want`` in expression ``n``."""
    out, stack = 0, [n]
    while stack:
        x = stack.pop()
        if x == want:
            out += 1
        elif x[0] == "mem":
            stack.append(x[1])
        elif x[0] == "op":
            stack.extend(x[2])
    return out


def _used_names(procs):
    """Every local name any procedure binds or reads, registers included."""
    used = set(frameproc._ALL_REG_LOCALS)
    for _e, params, rets, stmts in procs:
        used.update(params, rets)
        for s in FF.stmts_of(stmts):
            if s[0] in ("asg", "for"):
                used.add(s[1])
            elif s[0] == "pcall":
                used.update(s[3])
            for x in frameproc._stmt_exprs(s):
                used |= frameproc._locset(x)
    return used


def _fresh(used, n):
    """``(next unused s<k>, the counter past it)``.

    ``s<k>`` names no cell (``grammar.name_addr`` is None), no register and no
    ``[utr]<k>`` slot, so only the procedures' own names can collide."""
    while "s%d" % n in used:
        n += 1
    return "s%d" % n, n + 1


def _rewrite_expr(n, names):
    if n[0] == "mem":
        return ("loc", names[n[1]]) if n[2] == 1 and n[1] in names else ("mem", n[1], n[2])
    if n[0] == "op":
        return ("op", n[1], tuple(_rewrite_expr(c, names) for c in n[2]), n[3])
    return n


def _rewrite(stmts, names):
    """Slot stores become assignments, slot reads locals; no store is dropped."""
    for i, s in enumerate(stmts):
        for b in frameproc._stmt_bodies(s):
            _rewrite(b, names)
        s = frameproc._map_exprs(s, lambda x: _rewrite_expr(x, names))
        stmts[i] = ("asg", names[s[1]], s[2]) if s[0] == "st" and s[1] in names else s


def apply_rung(procs):
    """Rung (d0) in place over ``procs``; returns the per-slot proofs."""
    used, proofs, n = _used_names(procs), [], 0
    prints = [_footprint(p[3]) for p in procs]
    for k, (_e, _params, _rets, stmts) in enumerate(procs):
        shared = set().union(*(f for j, f in enumerate(prints) if j != k), set())
        names = {}
        for cell in sorted(_candidates(stmts)):
            slot = _Slot(cell).run(stmts, shared)
            name = None
            if slot.why is None:
                name, n = _fresh(used, n)
                used.add(name)
                names[_addr(cell)] = name
            proofs.append(slot.proof(name))
        if names:
            _rewrite(stmts, names)
    return proofs


def drop_state(state, proofs, symbols, name_of):
    """Drop the state field of every slot named in every procedure that uses it."""
    named, kept = set(), set()
    for p in proofs:
        if p.kind == "stack":
            (named if p.status == "named" else kept).update(p.targets)
    gone = {symbols.pop(c, None) or name_of(c) for c in named - kept}
    return [f for f in state if f[0] not in gone]
