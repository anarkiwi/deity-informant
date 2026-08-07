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


# ---- rung (d0'): the stack fabric leaves the frame program -----------------------
_RAW_CALLS = frozenset(("call", "callb", "dcall", "swc"))


def _sp_uses(stmts, calls, sp):
    """True where a statement needs ``sp`` beyond updating or passing it.

    A raw call keeps the machine stack alive; a pcall's plain ``sp`` argument is
    the threading the caller may drop with the callee, recorded in ``calls``."""
    for s in stmts:
        k = s[0]
        if k in _RAW_CALLS:
            return True
        if k == "asg" and s[1] == sp:
            continue
        if k == "pcall":
            if sp in s[3]:
                return True
            for a in s[2]:
                if a == ("loc", sp):
                    calls.append(s[1])
                elif sp in frameproc._locset(a):
                    return True
            continue
        for x in frameproc._stmt_exprs(s):
            if sp in frameproc._locset(x):
                return True
        for b in frameproc._stmt_bodies(s):
            if _sp_uses(b, calls, sp):
                return True
    return False


def _strip_sp(stmts, spat, sp):
    """Remove the ``sp`` updates and the threading argument, bodies included."""
    out = []
    for s in stmts:
        if s[0] == "asg" and s[1] == sp:
            continue
        if s[0] == "pcall":
            k = spat.get(s[1])
            args = [a for i, a in enumerate(s[2]) if i != k]
            s = ("pcall", s[1], args, [r for r in s[3] if r != sp])
        for b in frameproc._stmt_bodies(s):
            b[:] = _strip_sp(b, spat, sp)
        out.append(s)
    return out


def _sp_delta(v, sp):
    """``+/-k`` of an ``sp = (sp +/- $k)`` update, else None."""
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


def _sp_net(stmts, off, sp):
    """Net ``sp`` displacement through the list, None where dropping cannot hold.

    Every point control may leave or enter -- a ret, a label, a goto, a break
    or continue -- must sit at rest (offset zero), and joining arms must agree,
    so a dropped ``sp`` leaves the evaluator's frame matching untouched."""
    for s in stmts:
        k = s[0]
        if k == "asg" and s[1] == sp:
            d = _sp_delta(s[2], sp)
            if d is None:
                return None
            off = (off + d) & 0xFF  # sp walks its byte: -$02 is +$FE
        elif k in ("ret", "label", "goto", "cont", "brk", "unobs", "dgoto", "igoto", "dbr"):
            if off != 0:
                return None
        elif k == "if":
            a = _sp_net(s[3], off, sp)
            if a is None or a != _sp_net(s[4], off, sp):
                return None
            off = a
        elif k in ("loop", "for", "opsw", "swg", "callb"):
            for b in frameproc._stmt_bodies(s):
                if _sp_net(b, off, sp) != off:
                    return None
    return off


_PAGE1 = range(0x0100, 0x0200)


def _push_val(s):
    """``(cell, value)`` of a constant byte store to a stack-page cell."""
    if s[0] != "st" or s[1][0] != "const" or s[2][0] != "const" or s[2][2] != 1:
        return None
    return (s[1][1], s[2][1]) if s[1][1] in _PAGE1 else None


def _trick_window(lst, i, sp):
    """``(positions, target)`` of a constant RTS trick led by the push at ``i``.

    Two constant pushes to adjacent stack cells, the -2 displacement, and the
    ret it flows into, nothing between touching the cells, ``sp`` or control:
    the machine reads PCL at the lower cell and PCH above, and lands one past."""
    first = _push_val(lst[i])
    if first is None:
        return None
    cells = {first[0]: first[1]}
    keep = [i]
    disp = None
    for j in range(i + 1, min(i + 8, len(lst))):
        s = lst[j]
        if s[0] == "ret":
            if disp is None or len(cells) != 2:
                return None
            lo_cell = min(cells)
            if max(cells) != lo_cell + 1:
                return None
            target = ((cells[max(cells)] << 8) | cells[lo_cell]) + 1
            return keep + [disp, j], target & 0xFFFF
        if s[0] == "asg" and s[1] == sp:
            if disp is not None or _sp_delta(s[2], sp) != -2 % 256:
                return None
            disp = j
            continue
        got = _push_val(s)
        if got is not None and len(cells) < 2:
            cells[got[0]] = got[1]
            keep.append(j)
            continue
        if s[0] not in ("asg", "st"):
            return None
        if s[0] == "st" and _push_val(s) is None and s[1][0] == "const" and s[1][1] in _PAGE1:
            return None
        if s[0] == "asg" and s[1] == sp:
            return None
    return None


def lift_rts_trick(procs):
    """A constant RTS trick becomes the goto it is (docs/frameprog.md 7.9).

    The push pair, the displacement and the ret are one dispatch: control lands
    at the pushed word plus one, which the evaluator resolves through the same
    map the machine path read. The procedure then balances, and rung (d0')
    drops ``sp`` with no further rule; a ret carrying declared returns stays."""
    sp = frameproc._SP
    out = []
    for _e, _pa, rets, stmts in procs:
        if rets:
            continue
        out += _lift_tricks(stmts, sp)
    return out


def _lift_tricks(stmts, sp):
    proofs = []
    for s in stmts:
        for b in frameproc._stmt_bodies(s):
            proofs += _lift_tricks(b, sp)
    i = 0
    while i < len(stmts):
        got = _trick_window(stmts, i, sp)
        if got is None:
            i += 1
            continue
        positions, target = got
        stmts[positions[-1]] = ("dgoto", ("const", target, 2))
        for j in sorted(positions[:-1], reverse=True):
            del stmts[j]
        proofs.append(
            Proof(
                target,
                "rts",
                "resolved",
                (target,),
                "rts trick: the push pair and the displacement are goto ($%04X)" % target,
            )
        )
    return proofs


def drop_sp(procs, play):
    """``sp`` leaves the program where nothing reads it; the proof names why not.

    The record cannot see ``sp`` and its real consumers were the destacked slot
    addresses, so the updates, the parameter and every threading argument go.
    An unresolved stack access, a raw call or a computed ``sp`` keeps it -- the
    RTS-trick's pushed cells stay as the stores they are either way."""
    sp = frameproc._SP
    need, calls_of = {}, {}
    for e, _pa, rets, stmts in procs:
        calls = []
        need[e] = sp in rets or _sp_uses(stmts, calls, sp) or _sp_net(stmts, 0, sp) != 0
        calls_of[e] = calls
    changed = True
    while changed:
        changed = False
        for e, _pa, _r, _s in procs:
            if not need[e] and any(need.get(c, True) for c in calls_of[e]):
                need[e] = changed = True
    kept = sorted(e for e, n in need.items() if n)
    if kept:
        why = "sp kept: %d procedure(s) read it beyond updates" % len(kept)
        return Proof(play, "sp", "refused", tuple(kept), why)
    spat = {e: (pa.index(sp) if sp in pa else None) for e, pa, _r, _s in procs}
    for k, (e, pa, rets, stmts) in enumerate(procs):
        stmts[:] = _strip_sp(stmts, spat, sp)
        procs[k] = (e, [p for p in pa if p != sp], [r for r in rets if r != sp], stmts)
    why = "sp: no reader; the updates, the parameter and the threading dropped"
    return Proof(play, "sp", "resolved", (), why)
