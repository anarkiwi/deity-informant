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
    """The slot's loads become the local, addresses included.

    A read nested in another access's address (``m_CA02[(m_01FA & $07)]``) is a
    read like any other: ``_count`` walks into ``mem`` addresses, so a rewrite
    that does not walk there deletes the store and leaves the load behind."""
    if n[0] == "mem":
        if n[2] == 1 and n[1] in names:
            return ("loc", names[n[1]])
        return ("mem", _rewrite_expr(n[1], names), n[2])
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


# ---- rung (d0s): the slot named through ``sp`` rather than through its cell ------
_STK_PAGE = ("const", 0x0100, 2)


def _sp_disp(addr, sp):
    """``k`` of a ``(zext2(sp [+ $k]) | $0100)`` address, else None.

    The 6510 pull address, ``or`` rather than add because the byte wraps inside
    page one. ``concretize_stack`` folds it to a cell only where one entry
    ``sp`` flowed there; two call depths join to bot and the spelling survives."""
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


def _touches_page(addr, width):
    """``(may reach page one, address unresolvable)`` of a ``width``-byte access."""
    base, span = _span(addr)
    if base is None:
        return False, True
    return base <= _PAGE[-1] and base + span + width - 1 >= _PAGE[0], False


class _Marks:
    """Per-statement ``(epoch, displacement, aliases)``: where ``sp`` stands.

    An epoch is a run control neither leaves nor enters, so inside one ``sp`` is
    the entry value plus the displacements written between and a slot is
    ``(epoch, displacement + k)``; two epochs name nothing in common."""

    def __init__(self):
        self.epoch = 0
        self.disp = 0
        self.alias = {}
        self.at = {}

    def bump(self):
        self.epoch += 1
        self.disp = 0
        self.alias = {}

    def state(self):
        return (self.epoch, self.disp, dict(self.alias))

    def restore(self, st):
        self.epoch, self.disp, self.alias = st[0], st[1], dict(st[2])

    def mark(self, stmts, i):
        self.at[(id(stmts), i)] = (self.epoch, self.disp, self.alias)


def _sp_scan(stmts, marks, sp):
    """Mark every statement of the list, bodies included, opening epochs as needed."""
    for i, s in enumerate(stmts):
        k = s[0]
        if k not in _STRAIGHT:
            marks.bump()
        marks.mark(stmts, i)
        if k == "if":
            here, arms = marks.state(), []
            for b in frameproc._stmt_bodies(s):
                marks.restore(here)
                _sp_scan(b, marks, sp)
                arms.append(marks.state())
            if len({(a[0], a[1]) for a in arms}) == 1:
                marks.restore(arms[0] if len(arms) == 1 else (arms[0][0], arms[0][1], {}))
            else:
                marks.bump()
        else:
            for b in frameproc._stmt_bodies(s):
                marks.bump()
                _sp_scan(b, marks, sp)
                marks.bump()
        if k in ("asg", "for"):
            marks.alias = {n: v for n, v in marks.alias.items() if n != s[1]}
        if k == "asg" and s[1] == sp:
            d = _sp_delta(s[2], sp)
            if d is None:
                marks.bump()
            else:
                marks.disp = (marks.disp + d) & 0xFF
        elif k == "asg":
            d = _sp_disp(s[2], sp)
            if d is not None:
                marks.alias = {**marks.alias, s[1]: (marks.disp + d) & 0xFF}


def _slot_at(addr, mark, sp):
    """``(epoch, cell)`` the address names at ``mark``, else None.

    A polished procedure spells the pull address once into a local, so a
    two-byte local the run defined from ``sp`` names the cell it was made at."""
    d = _sp_disp(addr, sp)
    if d is not None:
        return (mark[0], (mark[1] + d) & 0xFF)
    return (mark[0], mark[2][addr[1]]) if addr[0] == "loc" and addr[1] in mark[2] else None


def _below_sp(k):
    """The displacement claims free stack space: at or below the live top.

    A store at ``k > 0`` writes the caller's live stack -- the return address an
    RTS trick rewrites -- and is no spill of this procedure's own."""
    return k == 0 or k >= 0x80


def _count_sp(n, key, mark, sp):
    """Occurrences of a one-byte load of slot ``key`` in expression ``n``."""
    out, stack = 0, [n]
    while stack:
        x = stack.pop()
        if x[0] == "mem":
            if x[2] == 1 and _slot_at(x[1], mark, sp) == key:
                out += 1
            else:
                stack.append(x[1])
        elif x[0] == "op":
            stack.extend(x[2])
    return out


class _SpSlot:
    """One slot named through ``sp``: the must-def walk over a procedure.

    Rung (d0)'s premises re-asked against a relative cell, plus two of its own:
    the store claims free stack space, and the procedure balances so no ``ret``
    reads page one for the return address the slot would sit in."""

    __slots__ = ("key", "stores", "reads", "why")

    def __init__(self, key):
        self.key = key
        self.stores = self.reads = 0
        self.why = None

    def _refuse(self, why):
        self.why = self.why or why

    def run(self, stmts, marks, sp, balanced):
        if not balanced:
            self._refuse("the procedure's stack effect is not zero")
        self._walk(stmts, marks, sp, False)
        if not (self.stores and self.reads):
            self._refuse("the slot is not both stored and read in the procedure")
        return self

    def _probe(self, addr, width, mark, sp, defd):
        """One access against the slot: refuse where it may touch what is live."""
        got = _slot_at(addr, mark, sp)
        if got is None:
            near, blind = _touches_page(addr, width)
            if near:
                self._refuse("another resolvable access may touch the slot")
            elif blind and defd:
                self._refuse("an unresolvable address may alias the live slot")
            return
        if got[0] != self.key[0]:
            if defd:
                self._refuse("an unresolvable address may alias the live slot")
            return
        if width != 1 and any(((got[1] + j) & 0xFF) == self.key[1] for j in range(width)):
            self._refuse("another resolvable access may touch the slot")

    def _walk(self, stmts, marks, sp, defd):
        """Must-def over one statement list; returns the state it leaves behind."""
        for i, s in enumerate(stmts):
            k = s[0]
            defd = defd and k in _STRAIGHT
            mark = marks.at[(id(stmts), i)]
            for addr, width in _accesses(s):
                self._probe(addr, width, mark, sp, defd)
            for x in frameproc._stmt_exprs(s):
                got = _count_sp(x, self.key, mark, sp)
                self.reads += got
                if got and not defd:
                    self._refuse("a read is not dominated by a store of the slot")
            if k == "st" and _slot_at(s[1], mark, sp) == self.key and G.store_width(s[2]) == 1:
                self.stores += 1
                defd = True
            if k == "if":
                arms = [self._walk(b, marks, sp, defd) for b in frameproc._stmt_bodies(s)]
                defd = all(arms)
            else:
                for b in frameproc._stmt_bodies(s):
                    self._walk(b, marks, sp, False)
        return defd

    def proof(self, name):
        """The rung-(d0s) record: the premise counts, and the refusal or the local."""
        return Proof(
            self.key[1],
            "spslot",
            "refused" if self.why else "named",
            (),
            "sp slot [%d]$%02X: %d store(s), %d read(s); %s"
            % (
                self.key[0],
                self.key[1],
                self.stores,
                self.reads,
                self.why or "data temporary, local %s" % name,
            ),
        )


def _sp_candidates(stmts, marks, sp):
    """Slots some store in the procedure claims below the live stack top."""
    out = set()

    def walk(lst):
        for i, s in enumerate(lst):
            if s[0] == "st" and G.store_width(s[2]) == 1:
                mark = marks.at[(id(lst), i)]
                got = _slot_at(s[1], mark, sp)
                if got is not None and _below_sp((got[1] - mark[1]) & 0xFF):
                    out.add(got)
            for b in frameproc._stmt_bodies(s):
                walk(b)

    walk(stmts)
    return out


def _rewrite_sp_expr(n, names, mark, sp):
    if n[0] == "mem":
        got = _slot_at(n[1], mark, sp)
        if n[2] == 1 and got in names:
            return ("loc", names[got])
        return ("mem", _rewrite_sp_expr(n[1], names, mark, sp), n[2])
    if n[0] == "op":
        return ("op", n[1], tuple(_rewrite_sp_expr(c, names, mark, sp) for c in n[2]), n[3])
    return n


def _rewrite_sp(stmts, marks, names, sp):
    """Slot stores become assignments, slot reads locals; no store is dropped."""
    for i, s in enumerate(stmts):
        mark = marks.at[(id(stmts), i)]
        for b in frameproc._stmt_bodies(s):
            _rewrite_sp(b, marks, names, sp)
        was = _slot_at(s[1], mark, sp) if s[0] == "st" else None
        s = frameproc._map_exprs(s, lambda x, _m=mark: _rewrite_sp_expr(x, names, _m, sp))
        stmts[i] = ("asg", names[was], s[2]) if was in names else s


def apply_rung(procs):
    """Rungs (d0) and (d0s) in place over ``procs``; returns the per-slot proofs."""
    used, proofs, n = _used_names(procs), [], 0
    prints = [_footprint(p[3]) for p in procs]
    sp = frameproc._SP
    for k, (_e, _params, _rets, stmts) in enumerate(procs):
        shared = set().union(*(f for j, f in enumerate(prints) if j != k), set())
        names, spnames = {}, {}
        marks = _Marks()
        _sp_scan(stmts, marks, sp)
        for cell in sorted(_candidates(stmts)):
            slot = _Slot(cell).run(stmts, shared)
            name = None
            if slot.why is None:
                name, n = _fresh(used, n)
                used.add(name)
                names[_addr(cell)] = name
            proofs.append(slot.proof(name))
        bal = _sp_state(stmts, ("entry", 0), sp, _saves(stmts, sp), {}) == ("entry", 0)
        for key in sorted(_sp_candidates(stmts, marks, sp)):
            slot = _SpSlot(key).run(stmts, marks, sp, bal)
            name = None
            if slot.why is None:
                name, n = _fresh(used, n)
                used.add(name)
                spnames[key] = name
            proofs.append(slot.proof(name))
        if names:
            _rewrite(stmts, names)
        if spnames:
            _rewrite_sp(stmts, marks, spnames, sp)
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


def _sp_uses(stmts, calls, sp, saves):
    """The class naming why a statement needs ``sp``, else None.

    A raw call keeps the machine stack alive; a pcall's plain ``sp`` argument is
    the threading the caller may drop with the callee, recorded in ``calls``; a
    save-bracket store of ``sp`` to a private cell is fabric, not a consumer."""
    for s in stmts:
        k = s[0]
        if k in _RAW_CALLS:
            return "sp_linked"
        if k == "asg" and s[1] == sp:
            continue
        if k == "st" and s[1][0] == "const" and s[1][1] in saves and s[2] == ("loc", sp):
            continue
        if k == "pcall":
            if sp in s[3]:
                return "sp_read"
            for a in s[2]:
                if a == ("loc", sp):
                    calls.append(s[1])
                elif sp in frameproc._locset(a):
                    return "sp_read"
            continue
        for x in frameproc._stmt_exprs(s):
            if sp in frameproc._locset(x):
                return "sp_read"
        for b in frameproc._stmt_bodies(s):
            got = _sp_uses(b, calls, sp, saves)
            if got is not None:
                return got
    return None


def _strip_sp(stmts, spat, sp, saves=frozenset()):
    """Remove the ``sp`` updates, saves and threading argument, bodies included."""
    out = []
    for s in stmts:
        if s[0] == "asg" and s[1] == sp:
            continue
        if s[0] == "st" and s[1][0] == "const" and s[1][1] in saves and s[2] == ("loc", sp):
            continue
        if s[0] == "pcall":
            k = spat.get(s[1])
            args = [a for i, a in enumerate(s[2]) if i != k]
            s = ("pcall", s[1], args, [r for r in s[3] if r != sp])
        for b in frameproc._stmt_bodies(s):
            b[:] = _strip_sp(b, spat, sp, saves)
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


def _saves(stmts, sp):
    """Capture cells eligible as sp brackets: saved once, read only to restore.

    A save is ``st CELL = sp``; its restore is ``sp = mem[CELL]``. Any other
    access that may touch the cell -- resolvable or not -- disqualifies it, so
    the bracket pair is provably private before the walk trusts it."""
    cells, dirty = {}, set()
    all_stmts = list(FF.stmts_of(stmts))
    for s in all_stmts:
        if s[0] == "st" and s[1][0] == "const" and s[2] == ("loc", sp):
            cells[s[1][1]] = cells.get(s[1][1], 0) + 1
    for s in all_stmts:
        if s[0] == "st" and s[1][0] == "const" and s[2] == ("loc", sp) and s[1][1] in cells:
            continue
        if s[0] == "asg" and s[1] == sp and s[2][0] == "mem" and s[2][1][0] == "const":
            continue  # the restore is the bracket's own reader
        for addr, width in _accesses(s):
            base, span = _span(addr)
            if base is None:
                dirty.update(cells)
                continue
            for c in cells:
                if base <= c <= base + span + width - 1:
                    dirty.add(c)
    return frozenset(c for c, n in cells.items() if c not in dirty and n == 1)


def _sp_state(stmts, st, sp, saves, caps):
    """Symbolic ``sp`` state through the list, None where dropping cannot hold.

    States are ``(base, offset)``: displacement moves the offset (mod 256), a
    save records the state under its cell, a restore returns to it whatever ran
    between, and a constant load opens a new base (the TXS stack switch). Every
    point control may leave or enter must sit at the entry state."""
    for s in stmts:
        k = s[0]
        if k == "st" and s[1][0] == "const" and s[2] == ("loc", sp) and s[1][1] in saves:
            caps[s[1][1]] = st
        elif k == "asg" and s[1] == sp:
            d = _sp_delta(s[2], sp)
            if d is not None:
                st = (st[0], (st[1] + d) & 0xFF)
            elif s[2][0] == "mem" and s[2][1][0] == "const" and s[2][1][1] in caps:
                st = caps[s[2][1][1]]
            elif s[2][0] == "const":
                st = ("abs", s[2][1] & 0xFF)
            else:
                return None
        elif k == "pcall" and sp in s[3]:
            return None  # the callee hands sp back: this walk cannot say where it stands
        elif k in ("ret", "label", "goto", "cont", "brk", "unobs", "dgoto", "igoto", "dbr"):
            if st != ("entry", 0):
                return None
        elif k == "if":
            a = _sp_state(s[3], st, sp, saves, caps)
            if a is None or a != _sp_state(s[4], st, sp, saves, caps):
                return None
            st = a
        elif k in ("loop", "for", "opsw", "swg", "callb"):
            for b in frameproc._stmt_bodies(s):
                if _sp_state(b, st, sp, saves, caps) != st:
                    return None
    return st


_PAGE1 = range(0x0100, 0x0200)


def _push_val(s):
    """``(cell, value expression)`` of a pure byte store to a stack-page cell."""
    if s[0] != "st" or s[1][0] != "const" or G.store_width(s[2]) != 1:
        return None
    if s[2][0] not in ("const", "loc", "mem"):
        return None
    return (s[1][1], s[2]) if s[1][1] in _PAGE1 else None


def _disturbs_vals(s, names, reads):
    """The interval statement may change what the pushed values read."""
    if s[0] == "asg":
        return s[1] in names
    reach = frameproc.store_reach(s, None)
    for (rb, ri, rm), rw in reads:
        if frameproc.overlaps(reach, (rb, ri, frameproc.span(rb, ri, None, rm), rw, rm)):
            return True
    return False


def _trick_window(lst, i, sp):
    """``(positions, target expr)`` of an RTS trick led by the push at ``i``.

    Two pure pushes to adjacent stack cells, the -2 displacement, and the ret
    it flows into, nothing between touching the cells, the values, ``sp`` or
    control: the machine reads PCL at the lower cell, PCH above, and lands one
    past the word, so the ret is a goto on it (docs/frameprog.md 7.9)."""
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
            lo, hi = cells[lo_cell], cells[max(cells)]
            if lo[0] == "const" and hi[0] == "const":
                target = ("const", (((hi[1] << 8) | lo[1]) + 1) & 0xFFFF, 2)
            else:
                pack = frameproc.le_pack(lo, hi)
                target = ("op", "INT_ADD", (pack, ("const", 1, 2)), 2)
            return keep + [disp, j], target
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
        if s[0] == "st" and s[1][0] == "const" and s[1][1] in _PAGE1:
            return None
        names = set().union(*(frameproc._locset(v) for v in cells.values()))
        reads = [r for v in cells.values() for r in frameproc.mem_refs(v) if r[0] is not None]
        if any(r[0] is None for v in cells.values() for r in frameproc.mem_refs(v)):
            return None
        if _disturbs_vals(s, names, reads):
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
        stmts[positions[-1]] = ("dgoto", target)
        for j in sorted(positions[:-1], reverse=True):
            del stmts[j]
        where = "$%04X" % target[1] if target[0] == "const" else "the pushed word + 1"
        proofs.append(
            Proof(
                target[1] if target[0] == "const" else 0,
                "rts",
                "resolved",
                (target[1],) if target[0] == "const" else (),
                "rts trick: the push pair and the displacement are goto (%s)" % where,
            )
        )
    return proofs


SP_CLASSES = {
    "sp_unbalanced": "the procedure's stack effect is not zero",
    "sp_returned": "the procedure returns sp to its caller",
    "sp_linked": "a raw call keeps the machine stack alive",
    "sp_read": "an access rung (d0) could not destack reads sp",
    "sp_callee": "a callee keeps sp, so the threading argument stays",
}


def drop_sp(procs, play):
    """``sp`` leaves the program where nothing reads it; one proof per keeper.

    The record cannot see ``sp`` and its real consumers were the destacked slot
    addresses, so the updates, the parameter and every threading argument go; a
    refusal names its ``SP_CLASSES`` class, per procedure, in the ledger."""
    sp = frameproc._SP
    need, calls_of, saves_of = {}, {}, {}
    for e, _pa, rets, stmts in procs:
        calls = []
        saves = _saves(stmts, sp)
        balanced = _sp_state(stmts, ("entry", 0), sp, saves, {}) == ("entry", 0)
        why = None if balanced else "sp_unbalanced"
        why = why or ("sp_returned" if sp in rets else None)
        need[e] = why or _sp_uses(stmts, calls, sp, saves)
        calls_of[e] = calls
        saves_of[e] = saves
    changed = True
    while changed:
        changed = False
        for e, _pa, _r, _s in procs:
            if not need[e] and any(need.get(c, "sp_callee") for c in calls_of[e]):
                need[e] = "sp_callee"
                changed = True
    kept = sorted(e for e, n in need.items() if n)
    if kept:
        return [
            Proof(e, "sp", "refused", (e,), "%s: sub_$%04X keeps sp -- %s" % (c, e, SP_CLASSES[c]))
            for e, c in ((k, need[k]) for k in kept)
        ]
    spat = {e: (pa.index(sp) if sp in pa else None) for e, pa, _r, _s in procs}
    for k, (e, pa, rets, stmts) in enumerate(procs):
        stmts[:] = _strip_sp(stmts, spat, sp, saves_of[e])
        procs[k] = (e, [p for p in pa if p != sp], [r for r in rets if r != sp], stmts)
    why = "sp: no reader; the updates, the parameter and the threading dropped"
    return [Proof(play, "sp", "resolved", (), why)]
