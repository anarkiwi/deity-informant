"""framestack: rung (d0), the data stack destacked (docs/frameprog.md 5).

``PHA``/``PLA`` at a statically known ``sp`` lower to a store and a load at a
constant stack-page cell, so a register spill reads back as tune state. A slot
every read of which a store dominates, none of the procedure's own accesses
between, is a local: the domination is asked over the structured region tree.
"""

from __future__ import annotations

from . import expr as E
from . import framefuse as FF
from . import frameproc
from . import grammar as G
from .structured import Proof

_PAGE = range(0x0100, 0x0200)
_STRAIGHT = ("asg", "st", "if")  # the only forms that transfer no control
_OPAQUE = frameproc._ANYCALL | frozenset(("label",))  # a machine push, and an open join
_EXITS = frameproc._EXITS  # ``cont``/``brk``: the one edge whose target the text names
_CYCLIC = frameproc._CYCLIC  # the regions a levelled exit counts out through


def _addr(cell):
    return ("const", cell, 2)


def _span(addr):
    """``(const base, cells the index spans)``; base None is an unresolvable address."""
    base, idx = FF._addr_split(addr)
    return base, 0 if idx is None else E.mask(FF._w(idx))


def _hit(addr, width, cell):
    """``(may touch ``cell``, address unresolvable)`` of a ``width``-byte access."""
    base, span = _span(addr)
    if base is None:
        return False, True
    return base <= cell <= base + span + width - 1, False


_accesses = frameproc.accesses  # the ONE reading of a statement's memory operands


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


def _has_read(stmts, reads, has):
    """``has[(list, i)]``: statement ``i``, bodies included, reads the slot."""
    out = False
    for i, s in enumerate(stmts):
        got = bool(reads(stmts, i, s))
        for b in frameproc._stmt_bodies(s):
            got = _has_read(b, reads, has) or got
        has[(id(stmts), i)] = got
        out = out or got
    return out


def _may_read(stmts, reads, tail=False, out=None, has=None):
    """``out[(list, i)]``: a read of the slot may still be evaluated at statement ``i``.

    The interval the locality premise is about: an access matters where it stands
    between a store and a read, and a loop's back edge re-reaches its body's own
    reads, so inside one every position sees them."""
    if has is None:
        has, out = {}, {}
        _has_read(stmts, reads, has)
    total = tail
    for i in range(len(stmts) - 1, -1, -1):
        s = stmts[i]
        after = total or has[(id(stmts), i)]
        out[(id(stmts), i)] = after
        inner = after if s[0] in _CYCLIC else total
        for b in frameproc._stmt_bodies(s):
            _may_read(b, reads, inner, out, has)
        total = after
    return out


class _Slot:
    """One const stack cell: the must-def walk over a procedure and its refusal."""

    __slots__ = ("cell", "stores", "reads", "why", "live")

    def __init__(self, cell):
        self.cell = cell
        self.stores = self.reads = 0
        self.why = None
        self.live = {}

    def _refuse(self, why):
        self.why = self.why or why

    def run(self, stmts, shared):
        """The walk over a whole procedure, plus the both-ends and privacy premises."""
        want = ("mem", _addr(self.cell), 1)
        self.live = _may_read(
            stmts, lambda l, i, s: sum(_count(x, want) for x in frameproc._stmt_exprs(s))
        )
        self._walk(stmts, False)
        if not (self.stores and self.reads):
            self._refuse("the slot is not both stored and read in the procedure")
        if self.cell in shared:
            self._refuse("another procedure may touch the slot")
        return self

    def _walk(self, stmts, defd):
        """Must-def over one statement list; returns the state it leaves behind.

        A definition is monotone: only a call's own push and a join no list enumerates
        unmake it, so a region is entered with what stands before it and hands back
        what holds on its far side. The arms of an ``if`` intersect, so a slot written
        in both and read in the shared tail is a local with two definitions."""
        want = ("mem", _addr(self.cell), 1)
        for i, s in enumerate(stmts):
            k = s[0]
            live = defd and self.live[(id(stmts), i)]
            acc = _accesses(s)
            wrote = len(acc) - 1 if k == "st" else -1  # the store's own address is the last
            for j, (addr, width) in enumerate(acc):
                near, blind = (
                    (False, False) if (addr, width) == want[1:] else _hit(addr, width, self.cell)
                )
                if near:
                    self._refuse("another resolvable access may touch the slot")
                elif blind and live and j == wrote:
                    self._refuse("an unresolvable store may alias the live slot")
            for x in frameproc._stmt_exprs(s):
                got = _count(x, want)
                self.reads += got
                if got and not defd:
                    self._refuse("a read is not dominated by a store of the slot")
            if k == "st" and (s[1], G.store_width(s[2])) == want[1:]:
                self.stores += 1
                defd = True
            opaque = k in _OPAQUE  # a callee's body is entered past the machine's push
            arms = [self._walk(b, not opaque and defd) for b in frameproc._stmt_bodies(s)]
            defd = all(arms) if k == "if" else (defd and all(arms) and not opaque)
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
_sp_disp = frameproc.sp_disp  # the ONE reading of a stack-page address


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
    ``(epoch, displacement + k)``; two epochs name nothing in common. A call ends
    one, which is how the machine's own return-address push is priced."""

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


def _neutral(s, sp, saves):
    """Whether a loop leaves ``sp`` at its head on the back edge and every exit.

    The displacement walk asked of the region alone: where it holds, ``sp`` at a
    point of the body is the head's plus what the text wrote between, in every
    iteration, so the head's epoch is the one the whole region stands in."""
    flow = _SpFlow(sp, saves, {})
    try:
        for b in frameproc._stmt_bodies(s):
            _join(flow.run(b, _ENTRY), _ENTRY)
    except _Unbalanced:
        return False
    return True


def _kept(alias, s):
    """The aliases that stand at a region's head: those its body never rebinds.

    A local the body assigns names no displacement on the next iteration, so it
    leaves; one nothing in the region touches spells the same cell throughout."""
    gone = set()
    for x in FF.stmts_of([s]):
        if x[0] in ("asg", "for"):
            gone.add(x[1])
        elif x[0] == "pcall":
            gone.update(x[3])
    return {n: v for n, v in alias.items() if n not in gone}


def _sp_scan(stmts, marks, sp, saves=frozenset()):
    """Mark every statement of the list, bodies included, opening epochs as needed."""
    for i, s in enumerate(stmts):
        k = s[0]
        marks.mark(stmts, i)  # a statement's operands are read before it transfers
        through = k in _CYCLIC and _neutral(s, sp, saves)
        head = (marks.epoch, marks.disp, _kept(marks.alias, s) if through else {})
        if k not in _STRAIGHT and not through:
            marks.bump()
        if k == "if":
            here, arms = marks.state(), []
            for b in frameproc._stmt_bodies(s):
                marks.restore(here)
                _sp_scan(b, marks, sp, saves)
                arms.append(marks.state())
            if len({(a[0], a[1]) for a in arms}) == 1:
                marks.restore(arms[0] if len(arms) == 1 else (arms[0][0], arms[0][1], {}))
            else:
                marks.bump()
        elif through:
            for b in frameproc._stmt_bodies(s):
                marks.restore(head)
                _sp_scan(b, marks, sp, saves)
            marks.restore(head)
        else:
            for b in frameproc._stmt_bodies(s):
                marks.bump()
                _sp_scan(b, marks, sp, saves)
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

    __slots__ = ("key", "stores", "reads", "why", "impure", "live")

    def __init__(self, key):
        self.key = key
        self.stores = self.reads = 0
        self.why = self.impure = None
        self.live = {}

    def _refuse(self, why):
        self.why = self.why or why

    def _hazard(self, why, writes):
        """A writer refuses the slot; a reader refuses only dropping its store.

        A read moves no value, so the slot's identity survives one; what does not
        survive is the removal of the store the reader may name."""
        if writes:
            self._refuse(why)
        else:
            self.impure = self.impure or why

    def run(self, stmts, marks, sp, balanced):
        if not balanced:
            self._refuse("the procedure's stack effect is not zero")

        def reads(lst, i, s):
            mark = marks.at[(id(lst), i)]
            return sum(_count_sp(x, self.key, mark, sp) for x in frameproc._stmt_exprs(s))

        self.live = _may_read(stmts, reads)
        self._walk(stmts, marks, sp, False)
        if not (self.stores and self.reads):
            self._refuse("the slot is not both stored and read in the procedure")
        return self

    def _probe(self, addr, width, mark, sp, live, writes):
        """One access against the slot: refuse where it may touch what is live."""
        got = _slot_at(addr, mark, sp)
        if got is None:
            near, blind = _touches_page(addr, width)
            if near:
                self._hazard("another resolvable access may touch the slot", writes)
            elif blind and live:
                self._hazard("an unresolvable address may alias the live slot", writes)
            return
        if got[0] != self.key[0]:
            if live:
                self._hazard("an unresolvable address may alias the live slot", writes)
            return
        if width != 1 and any(((got[1] + j) & 0xFF) == self.key[1] for j in range(width)):
            self._hazard("another resolvable access may touch the slot", writes)

    def _walk(self, stmts, marks, sp, defd):
        """Must-def over one statement list; returns the state it leaves behind.

        Rung (d0)'s reading of the region tree against a relative cell: a call's own
        return-address push and a label are what unmake a definition, so no slot is
        live across one and no push of its can reach one."""
        for i, s in enumerate(stmts):
            k = s[0]
            mark = marks.at[(id(stmts), i)]
            live = defd and self.live[(id(stmts), i)]
            acc = _accesses(s)
            wrote = len(acc) - 1 if k == "st" else -1
            for j, (addr, width) in enumerate(acc):
                self._probe(addr, width, mark, sp, live, j == wrote)
            for x in frameproc._stmt_exprs(s):
                got = _count_sp(x, self.key, mark, sp)
                self.reads += got
                if got and not defd:
                    self._refuse("a read is not dominated by a store of the slot")
            if k == "st" and _slot_at(s[1], mark, sp) == self.key and G.store_width(s[2]) == 1:
                self.stores += 1
                defd = True
            opaque = k in _OPAQUE  # a callee's body is entered past the machine's push
            arms = [
                self._walk(b, marks, sp, not opaque and defd) for b in frameproc._stmt_bodies(s)
            ]
            defd = all(arms) if k == "if" else (defd and all(arms) and not opaque)
        return defd

    def status(self):
        """``refused``, ``held`` where only the store must stay, else ``named``."""
        if self.why:
            return "refused"
        return "held" if self.impure else "named"

    def proof(self, name):
        """The rung-(d0s) record: the premise counts, and the refusal or the local."""
        held = "value held in %s, store kept -- %s" % (name, self.impure)
        return Proof(
            self.key[1],
            "spslot",
            self.status(),
            (),
            "sp slot [%d]$%02X: %d store(s), %d read(s); %s"
            % (
                self.key[0],
                self.key[1],
                self.stores,
                self.reads,
                self.why or (held if self.impure else "data temporary, local %s" % name),
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


def _rewrite_sp(stmts, marks, names, sp, held=frozenset()):
    """Slot stores become assignments, slot reads locals; a ``held`` store stays.

    A held slot keeps its cell -- some reader may name it -- so its store writes
    the local the pulls read, which is the identity the walk proved."""
    out = []
    for i, s in enumerate(stmts):
        mark = marks.at[(id(stmts), i)]
        for b in frameproc._stmt_bodies(s):
            _rewrite_sp(b, marks, names, sp, held)
        was = _slot_at(s[1], mark, sp) if s[0] == "st" else None
        s = frameproc._map_exprs(s, lambda x, _m=mark: _rewrite_sp_expr(x, names, _m, sp))
        if was in names:
            out.append(("asg", names[was], s[2]))
            s = ("st", s[1], ("loc", names[was])) if was in held else None
        if s is not None:
            out.append(s)
    stmts[:] = out


# ---- rung (d0r): the return slot's own value decides what a ``ret`` is -----------


def _page_cell(cell):
    """The stack-page cell ``cell`` names; the byte wraps inside page one."""
    return 0x0100 | (cell & 0xFF)


def _ret_word(cell):
    """``mem[cell]:2 + 1``: where the machine lands, spelled as the two bytes it reads.

    ``machine_reads`` names this operand and no expression did; spelling it as the
    byte pair is what puts it in reach of the slot walk, which reads bytes."""
    halves = tuple(("mem", _addr(_page_cell(c)), 1) for c in (cell, cell + 1))
    return ("op", "INT_ADD", (frameproc.le_pack(*halves), ("const", 1, 2)), 2)


def _ret_sites(stmts, marks, entry_sp, out):
    """``(list, index, ret, cell)`` of every ``ret`` whose slot the walk pins.

    The cell is the entry ``sp`` plus the displacement written since; an epoch the
    walk did not open at the entry names no displacement, so it is left alone."""
    for i, s in enumerate(stmts):
        for b in frameproc._stmt_bodies(s):
            _ret_sites(b, marks, entry_sp, out)
        if s[0] != "ret":
            continue
        epoch, disp, _alias = marks.at[(id(stmts), i)]
        if not epoch:
            out.append((stmts, i, s, 0x0100 | ((entry_sp + disp + 1) & 0xFF)))
    return out


def _pull(sp):
    """``sp = sp + 2``: the two bytes the ``RTS`` takes back off the stack."""
    return ("asg", sp, ("op", "INT_ADD", (("loc", sp), ("const", 2, 1)), 1))


def _propose(sites, sp):
    """Every site's ``ret`` rewritten as the pull it makes and the goto it takes."""
    for stmts, i, _was, cell in sorted(sites, key=lambda t: -t[1]):
        stmts[i] = ("dgoto", _ret_word(cell))
        stmts.insert(i, _pull(sp))


def _revert(sites, refused):
    """The proposal undone at every site whose slot the walk refused."""
    for stmts, i, was, cell in sorted(sites, key=lambda t: -t[1]):
        if refused.get(cell) or refused.get(_page_cell(cell + 1)):
            stmts[i : i + 2] = [was]


def _ret_proof(cell, why):
    """The rung-(d0r) record: the slot's value answered the ``ret``, or did not."""
    return Proof(
        cell,
        "rts",
        "refused" if why else "resolved",
        (cell,),
        "return slot $%04X: %s"
        % (cell, why or "the procedure's own stores reach it, so the ret is a goto"),
    )


def _lift_rets(procs, sp, spin, saves_of):
    """A ``ret`` whose slot the procedure itself defines is the computed goto it is.

    The value question rung (d0) already asks -- is every read of this cell dominated
    by a store of the program's own -- asked of the operand ``machine_reads`` names.
    Where the answer is no, the slot holds the caller's pushed word and the ret stays.
    """
    proofs = []
    prints = [_footprint(p[3]) for p in procs]
    for k, (e, _params, _rets, stmts) in enumerate(procs):
        entry_sp = spin.get(e)
        if not isinstance(entry_sp, int):
            continue
        marks = _Marks()
        _sp_scan(stmts, marks, sp, saves_of[e])
        sites = _ret_sites(stmts, marks, entry_sp, [])
        if not sites:
            continue
        _propose(sites, sp)
        shared = set().union(*(f for j, f in enumerate(prints) if j != k), set())
        want = sorted({_page_cell(c + j) for _l, _i, _w, c in sites for j in (0, 1)})
        walked = {c: _Slot(c).run(stmts, shared) for c in want}
        refused = {c: w.why for c, w in walked.items()}
        for _l, _i, _w, c in sites:
            hi = _page_cell(c + 1)
            if walked[c].stores or walked[hi].stores:  # an ordinary return states nothing
                proofs.append(_ret_proof(c, refused[c] or refused[hi]))
        _revert(sites, refused)
    return proofs


def apply_rung(procs, spin=None, exits=None):
    """Rungs (d0r), (d0) and (d0s) in place over ``procs``; the per-slot proofs."""
    used, proofs, n = _used_names(procs), [], 0
    sp = frameproc._SP
    saves_of = {e: _saves(stmts, sp) for e, _pa, _r, stmts in procs}
    proofs += _lift_rets(procs, sp, spin or {}, saves_of)
    prints = [_footprint(p[3]) for p in procs]
    bals, _at_entry = _balances(procs, sp, saves_of, exits)
    for k, (_e, _params, _rets, stmts) in enumerate(procs):
        shared = set().union(*(f for j, f in enumerate(prints) if j != k), set())
        names, spnames, held = {}, {}, set()
        marks = _Marks()
        _sp_scan(stmts, marks, sp, saves_of[_e])
        for cell in sorted(_candidates(stmts)):
            slot = _Slot(cell).run(stmts, shared)
            name = None
            if slot.why is None:
                name, n = _fresh(used, n)
                used.add(name)
                names[_addr(cell)] = name
            proofs.append(slot.proof(name))
        bal = bals.get(_e, False)
        for key in sorted(_sp_candidates(stmts, marks, sp)):
            slot = _SpSlot(key).run(stmts, marks, sp, bal)
            name = None
            if slot.why is None:
                name, n = _fresh(used, n)
                used.add(name)
                spnames[key] = name
                if slot.impure:
                    held.add(key)
            proofs.append(slot.proof(name))
        if names:
            _rewrite(stmts, names)
        if spnames:
            _rewrite_sp(stmts, marks, spnames, sp, held)
    return proofs


def entry_frame(play_frame):
    """The stack cells the invocation convention pushed below the frame's entry."""
    return range(0x0200 - play_frame, 0x0200)


def drop_entry_frame(state, procs, symbols, name_of, play_frame):
    """Drop state declaring a convention-frame cell no surviving statement names.

    The pushed return word, the P a handler's RTI pops and the KERNAL A/X/Y save
    are the caller's frame, not the frame program's state."""
    used = _used_names(procs)
    gone = {symbols.get(c) or name_of(c) for c in entry_frame(play_frame)} - used
    return [f for f in state if f[0] not in gone]


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


def _sp_uses(stmts, calls, sp, saves, linked=True):
    """The class naming why a statement needs ``sp``, else None.

    A raw call keeps the machine stack alive unless nothing reads its page; a
    pcall's plain ``sp`` argument or returned ``sp`` is the threading the caller
    drops with the callee, recorded in ``calls``; a save store is fabric."""
    for s in stmts:
        k = s[0]
        if k in _RAW_CALLS and linked:
            return "sp_linked"
        if k == "asg" and s[1] == sp:
            continue
        if k == "st" and s[1][0] == "const" and s[1][1] in saves and s[2] == ("loc", sp):
            continue
        if k == "pcall":
            if sp in s[3]:
                calls.append(s[1])  # a balanced callee hands back what it was given
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
            got = _sp_uses(b, calls, sp, saves, linked)
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


_sp_delta = frameproc.sp_delta  # the ONE reading of an ``sp`` update


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


class _Unbalanced(Exception):
    """The displacement walk cannot prove the procedure stands where it entered."""


_ENTRY = ("entry", 0)  # the displacement a procedure is entered and must return at
_INLINED = frozenset(("callb", "swc"))  # a callee's body: its ``ret`` is the call's edge
_EDGES = frozenset(("ret", "label", "goto", "cont", "brk", "dgoto", "igoto", "dbr"))
_FAULT = "unobs"  # reaching one faults, so no frame the gate accepts ever stands there


def _join(a, b):
    """The one displacement two edges into a point carry, else ``_Unbalanced``."""
    if a != b:
        raise _Unbalanced
    return a


class _SpFlow:
    """The displacement walk of one procedure, and where its calls stand.

    States are ``(base, offset)``: a displacement moves the offset (mod 256), a save
    records it under its cell, a restore returns to it, a constant load opens a new
    base (TXS). Every point control may leave or enter stands at the entry."""

    def __init__(self, sp, saves, bal, exit_disp=0):
        self.sp, self.saves, self.bal, self.caps = sp, saves, bal, {}
        self.entry = None
        self.at_entry = True
        self.exit_disp = exit_disp
        self.top = True
        self.heads = []

    def run(self, stmts, st):
        """The exit state where every edge holds, else ``_Unbalanced``."""
        self.entry, self.at_entry, self.caps = st, True, {}
        self.top, self.heads = True, []
        return self.walk(stmts, st)

    def returns(self, st):
        """The frame boundary: ``exit_disp`` is what the entry convention pushed and
        the terminator does not pop -- the KERNAL A/X/Y save a CINV handler restores."""
        if st != (self.entry[0], (self.entry[1] + self.exit_disp) & 0xFF):
            raise _Unbalanced
        return self.entry

    def walk(self, stmts, st):
        for s in stmts:
            st = self.step(s, st)
        return st

    def leaves(self, st):
        """A point control leaves or enters: only the entry displacement holds.

        A label may be the target of a ``goto`` this list does not carry, of a jump
        no list enumerates, or of a dispatch arm, so an interior displacement is
        not a state the walk may assume -- Phase 1's rule, and 2c re-measured it."""
        if st != self.entry:
            raise _Unbalanced
        return self.entry

    def exits(self, s, st):
        """A ``continue``/``break``: the head of the loop it names is where it lands.

        A structured exit's target is enumerated -- the level counts the enclosing
        loops -- so it holds at that head rather than at the procedure's entry, which
        is what lets a spill stand live across a loop."""
        n = frameproc.exit_level(s)
        return _join(st, self.heads[-n]) if len(self.heads) >= n else self.leaves(st)

    def update(self, v, st):
        """One ``sp = ..`` assignment against the state in force."""
        d = _sp_delta(v, self.sp)
        if d is not None:
            return (st[0], (st[1] + d) & 0xFF)
        if v[0] == "mem" and v[1][0] == "const" and v[1][1] in self.caps:
            return self.caps[v[1][1]]
        if v[0] == "const":
            return ("abs", v[1] & 0xFF)
        raise _Unbalanced

    def body(self, s, st):
        """A callee inlined at the call: its own ``ret`` edges stand where it began."""
        outer, self.entry = self.entry, st
        top, self.top = self.top, False
        heads, self.heads = self.heads, []
        try:
            for b in frameproc._stmt_bodies(s):
                _join(self.walk(b, st), st)
        finally:
            self.entry, self.top, self.heads = outer, top, heads
        return st

    def call(self, st):
        """A call: the machine pushes its return here, so the drop must not move it."""
        if st != _ENTRY:
            self.at_entry = False
        return st

    def step(self, s, st):
        k = s[0]
        if k == "st" and s[1][0] == "const" and s[2] == ("loc", self.sp) and s[1][1] in self.saves:
            self.caps[s[1][1]] = st
            return st
        if k == "asg" and s[1] == self.sp:
            return self.update(s[2], st)
        if k == "pcall":
            if self.sp in s[3] and not self.bal.get(s[1]):
                raise _Unbalanced  # the callee hands sp back from a place unproven
            return self.call(st)
        if k == "ret" and self.top and self.exit_disp:
            return self.returns(st)
        if k in _EXITS:
            return self.exits(s, st)
        if k in _EDGES:
            return self.leaves(st)
        if k == _FAULT:
            return st
        if k == "if":
            return _join(self.walk(s[3], st), self.walk(s[4], st))
        if k in _INLINED:
            return self.body(s, self.call(st))
        if k in ("loop", "for", "opsw", "swg"):
            if k in _CYCLIC:
                self.heads.append(st)
            try:
                for b in frameproc._stmt_bodies(s):
                    _join(self.walk(b, st), st)
            finally:
                if k in _CYCLIC:
                    self.heads.pop()
            return st
        if k in _RAW_CALLS:
            return self.call(st)
        return st


def _sp_state(stmts, st, sp, saves, bal=None):
    """The state the procedure leaves ``sp`` in, None where dropping cannot hold."""
    return _run_flow(stmts, sp, saves, bal or {}, st)[0]


def _run_flow(stmts, sp, saves, bal, st=_ENTRY, exit_disp=0):
    """``(exit state or None, every call stood at the entry displacement)``."""
    flow = _SpFlow(sp, saves, bal, exit_disp)
    try:
        return flow.run(stmts, st), flow.at_entry
    except _Unbalanced:
        return None, False


def _balances(procs, sp, saves_of, exits=None):
    """Per procedure: ``sp`` stands at its entry displacement on every edge.

    A ``pcall`` handing ``sp`` back preserves the caller's displacement exactly where
    its callee balances, so the verdict is a least fixpoint over the call graph:
    nothing is assumed, a cycle stays unproven, and only callers are re-asked."""
    bal, at_entry = {}, {}
    body = {e: stmts for e, _pa, _r, stmts in procs}
    deps = {
        e: {s[1] for s in FF.stmts_of(stmts) if s[0] == "pcall" and sp in s[3]}
        for e, stmts in body.items()
    }
    pending = ask = set(body)
    while ask:
        won = set()
        for e in sorted(ask):
            st, calls = _run_flow(body[e], sp, saves_of[e], bal, exit_disp=(exits or {}).get(e, 0))
            if st == _ENTRY:
                won.add(e)
                at_entry[e] = calls
        bal.update((e, True) for e in won)
        pending -= won
        ask = {e for e in pending if deps[e] & won}
    return bal, at_entry


SP_CLASSES = {
    "sp_unbalanced": "the procedure's stack effect is not zero",
    "sp_linked": "a raw call keeps the machine stack alive",
    "sp_read": "an access rung (d0) could not destack reads sp",
    "sp_callee": "a callee keeps sp, so the threading argument stays",
}

_PAGE_RANGE = (_PAGE[0], None, len(_PAGE) - 1, 1, 0)


def _off_page(addr, width, at, regions):
    """Every address the expression names lies outside the stack page.

    The resolvable form is bounded by the ONE span rule (a modular ``zp,X`` address
    never leaves the zero page), the rest by the bits the address may set and 2a's
    ``addr_floor``, the bits it must."""
    got = frameproc.addr_range(addr, width)
    if got is None:
        lo = frameproc.addr_floor(addr, at)
        hi = frameproc.addr_bits(addr, at)
        return hi + width - 1 < _PAGE[0] or lo > _PAGE[-1]
    base, idx, mod = got
    span = frameproc.span(base, idx, regions, mod)
    return not frameproc.overlaps((base, idx, span, width, mod), _PAGE_RANGE)


def _raw_called(procs):
    """Some procedure makes a call the machine, not the text, threads."""
    return any(s[0] in _RAW_CALLS for _e, _pa, _r, b in procs for s in FF.stmts_of(b))


def _page_one_free(procs, sp, regions):
    """No surviving access may reach the stack page other than through ``sp``.

    The second premise the raw call's linkage may rest on: the drop moves where the
    machine's pushed return bytes land, and where no surviving access can name page
    one, nothing in the program reads what moved."""
    for _e, _pa, _r, stmts in procs:
        for env, k, s in frameproc.envs(stmts):
            at = frameproc.DefsAt(env, k)
            for addr, width in _accesses(s) + list(frameproc.machine_reads(s)):
                if sp in frameproc._locset(addr):
                    continue
                if not _off_page(addr, width, at, regions):
                    return False
    return True


def drop_sp(procs, play, regions=None, exits=None):
    """``sp`` leaves the program where nothing reads it; one proof per keeper.

    The updates, the parameter and every threading argument go. A raw call's
    linkage goes with them where the drop cannot move the machine's pushed return
    bytes -- every call at the entry displacement -- or where nothing reads them."""
    sp = frameproc._SP
    need, calls_of = {}, {}
    saves_of = {e: _saves(stmts, sp) for e, _pa, _r, stmts in procs}
    bal, at_entry = _balances(procs, sp, saves_of, exits)
    linked = _raw_called(procs) and not (
        all(at_entry.values()) or _page_one_free(procs, sp, regions)
    )
    for e, _pa, _rets, stmts in procs:
        calls = []
        why = None if bal.get(e) else "sp_unbalanced"
        need[e] = why or _sp_uses(stmts, calls, sp, saves_of[e], linked)
        calls_of[e] = calls
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
