"""Section 5's expressions, guards and cells, compiled to closures.

The object is fixed for a render, so every node of it is spent once here and
called thereafter: a closure per expression, a predicate per guard list, a
reader and a writer per named cell.  A node is bound to its children's closures
where it is compiled, and to the numbers an arm or a command states beside it,
so nothing here is looked up again while a tick runs.  It is the compile half
of :class:`~deity_informant.trackerprog.universal.Player` and reads its state.
"""

from __future__ import annotations

import operator
from functools import partial

# the compiler's dispatch, spent once per node instead of once per evaluation, and
# the same comparison with its operands the other way about, for a folded constant
_CMP = {">=": operator.ge, "<": operator.lt, "!=": operator.ne, "==": operator.eq, ">": operator.gt}
_MIRROR = {
    ">=": operator.le,
    "<": operator.gt,
    "!=": operator.ne,
    "==": operator.eq,
    ">": operator.lt,
}
_BINOP = {
    "and": operator.and_,
    "or": operator.or_,
    "xor": operator.xor,
    "add": operator.add,
    "sub": operator.sub,
    "shr": operator.rshift,
}


def _always(ov=None):
    """A guard list with no terms: what it guards runs (§3.3)."""
    return True


def _both(f, g):
    """Two guard lists as one predicate: a record's own, and its arm's (§5)."""
    if f is _always:
        return g
    if g is _always:
        return f
    return lambda ov: f(ov) and g(ov)


_UNARY = {  # a node whose argument is a name, not an expression
    "cell": lambda p, a, pay: p.cellcode(a),
    "global": lambda p, a, pay: (lambda ov, d=p.gl: d[a] & 0xFFFF),
    "flag": lambda p, a, pay: (lambda ov, d=p.flags: d.get(a, 0)),
    "payload": lambda p, a, pay: (lambda ov: ov[a]),
    "ins": lambda p, a, pay: (lambda ov: p.column(p.instr(), a)),
    "insrec": lambda p, a, pay: (lambda ov: p.column(p.ins[str(p.cell(a[0]))], a[1])),
    "bug": lambda p, a, pay: p.bugcode(a),
    "notefreq": lambda p, a, pay: (lambda ov: p.pitchof()),
    "tuned": lambda p, a, pay: (lambda ov, f=p.code_of(a, pay): p.tuned(f(ov))),
    "transpose": lambda p, a, pay: (lambda ov, f=p.code_of(a, pay): p.transpose(f(ov))),
}


class Plan:
    """What the compiler bound for one record of the object: fields, not behaviour."""

    def __init__(self, **kw):
        self.__dict__.update(kw)


class PlayerMixin:
    """The object compiled: §5's forms, each spent once and called thereafter.

    The compile half of ``universal.Player``, whose state it reads: ``o`` the
    object, ``c`` the voice cells, ``v`` the voice being committed, ``ins`` the
    instrument records, ``gl`` the global channel, ``flags``, ``pw``, ``acc``,
    and the memos ``columns`` and ``spaces`` a reading fills.
    """

    def ev(self, e, ov=None):
        """Evaluate one §5 node the object reaches through a payload it does not fix."""
        return self.build(e, None)(ov)

    def code_of(self, e, pay=None):
        """The closure a sub-expression evaluates through, bound to its children's."""
        return self.build(e, pay)

    def fold(self, e, pay=None):
        """What a node is worth where the object states it outright, or ``None``.

        A constant, and a payload name a fixed payload binds to one: an arm and a
        command are records of the object, so what they bind is spent here and
        read by nothing per tick.
        """
        if isinstance(e, int):
            return (e,)
        name = e
        if isinstance(e, dict) and len(e) == 1:
            k, a = next(iter(e.items()))
            if k == "const" and isinstance(a, int):
                return (a,)
            name = a if k in ("const", "payload") else None
        x = pay.get(name) if pay is not None and isinstance(name, str) else None
        return (x,) if isinstance(x, int) else None

    def build(self, e, pay=None):  # noqa: C901 - one clause per section 5 expression form
        """Compile one section 5 expression node to a closure over the payload."""
        c = self.fold(e, pay)
        if c is not None:
            return lambda ov, x=c[0]: x
        if isinstance(e, str):
            return lambda ov: (ov or {})[e]
        k, a = next(iter(e.items()))
        if k in _BINOP:
            op, x = _BINOP[k], self.code_of(a[0], pay)
            c = self.fold(a[1], pay)
            if c is not None:
                return lambda ov, m=c[0]: op(x(ov), m)
            y = self.code_of(a[1], pay)
            return lambda ov: op(x(ov), y(ov))
        if k in _UNARY:
            return _UNARY[k](self, a, pay)
        if k == "const":
            x = pay.get(a) if pay is not None else None
            if x is not None:  # a name the object's own record binds to an expression
                return self.code_of(x, pay)
            return lambda ov: self.const(a, ov)
        if k == "u16":
            x, y = self.code_of(a[0], pay), self.code_of(a[1], pay)
            return lambda ov: (x(ov) & 0xFF) | (y(ov) & 0xFF) << 8
        if k == "interval":
            if a is None:
                return lambda ov: self.interval(None)
            x = self.code_of(a, pay)
            return lambda ov: self.interval(x(ov))
        if k == "field":
            x, m = self.code_of(a[0], pay), a[1]
            return lambda ov: x(ov) & m
        if k in ("bit", "carry_out"):
            x, n = self.code_of(a[0], pay), a[1]
            return lambda ov: (x(ov) >> n) & 1
        if k == "borrow_out":  # a subtraction's own: the 6502's C, 1 where it did not borrow
            x, n = self.code_of(a[0], pay), a[1]
            return lambda ov: 1 - ((x(ov) >> n) & 1)
        if k == "fold":  # the triangle a free counter's low bits already are
            x, m = self.code_of(a[0], pay), a[1]
            return lambda ov: self.folded(x(ov) & m, m)
        if k == "trap":
            return lambda ov: self.sprung(a)
        if k == "tabcell":  # a named column of a stream row, selected by a live cell
            name, y, col = a[0], self.code_of(a[1], pay), a[2]
            cols = self.column_of(name, col)

            def tabcell(ov):
                i = y(ov)
                return (cols[i] or self.missing(name, col, i))(ov)

            return tabcell
        raise KeyError("expression form %r" % (k,))

    def column_of(self, name, col):
        """One named column of a stream's rows, compiled: a closure per row.

        A row the column is not in, and a row the object marks as no row at all,
        are both ``None`` here and both answered by ``missing`` at the read --
        the second by ``srow``'s own refusal, which is where it belongs.
        """
        out = self.columns.get((name, col))
        if out is None:
            rows = self.o["streams"][name]["rows"]
            # the list goes into the memo before it is filled: a column may read
            # its own stream at another row, and that is the object's, not a loop
            out = self.columns[(name, col)] = [None] * len(rows)
            for i, r in enumerate(rows):
                if isinstance(r, dict) and col in r and "trap" not in r:
                    out[i] = self.code_of(r[col])
        return out

    def missing(self, name, col, i):
        """A column a compiled row does not carry: read it out, or refuse the row."""
        return self.code_of(self.srow(name, i)[col])

    def setcode(self, sets, pay=None):
        """One ``sets`` list, compiled: each target's own setter and its value."""
        return [(self.put_to(t), self.code_of(e, pay)) for t, e in sets]

    def const(self, a, ov):
        """A named constant an arm or a command binds: a number, or an expression."""
        x = (ov or {})[a]
        return x if isinstance(x, int) else self.ev(x, ov)

    @staticmethod
    def folded(x, m):
        return x ^ m if x > m >> 1 else x

    @staticmethod
    def sprung(why):
        raise AssertionError(why)

    def cellcode(self, name):
        """A cell read, compiled: the voice being committed, or the one it names.

        One vocabulary either way -- a name, its space, its half (§5).  A word
        about another voice's state states that voice, and reads the same cell
        the voice itself would: ``{"cell": [name, v]}`` beside ``{"cell": name}``.
        """
        if isinstance(name, list):
            name, u = name
            return lambda ov: self.on_voice(name, u)
        if name in self.c and name not in ("freq_hi", "freq_lo"):
            d = self.c[name]
            return lambda ov: d[self.v] & 0xFFFF
        return lambda ov: self.cell(name)

    def dividercode(self, r, pay=None):
        """§3.3's divider, compiled: one procedure wherever a ``rate`` is one.

        A counter cell the run steps down by one, firing where it passes zero and
        reloading from the object's own expression -- a stream's ``rate`` and an
        accumulator's are the same form and the same counter.  ``rate`` absent, or
        the degenerate ``1``, is no divider at all and compiles to ``None``; the
        counter is where a divider lives, so a bare ``k`` names none and is refused.
        """
        if r is None or r == 1:
            return None
        if not isinstance(r, dict):
            raise AssertionError("a divider is a counter cell and its reload, not %r" % (r,))
        d, f = self.c[r["cell"]], self.code_of(r["reload"], pay)

        def due(ov):
            v = self.v
            d[v] = c = (d[v] - 1) & 0xFF
            if not c & 0x80:
                return False
            d[v] = f(ov) & 0xFF
            return True

        return due

    def cmpcode(self, x, op, y, pay):
        """One comparison of a guard list: a read, and the compare the chip's own.

        An operand the object states outright is spent here and the comparison
        keeps it, so a term costs one read; a term of two reads keeps its own.
        """
        c = self.fold(y, pay)
        if c is not None:
            return partial(_MIRROR[op], c[0]), self.code_of(x, pay)
        c = self.fold(x, pay)
        if c is not None:
            return partial(_CMP[op], c[0]), self.code_of(y, pay)
        f, g, h = _CMP[op], self.code_of(x, pay), self.code_of(y, pay)
        return bool, lambda ov: f(g(ov), h(ov))

    def guardcode(self, gs, pay=None):
        """One guard list, compiled to a predicate: its terms, and nothing between."""
        if not gs:
            return _always
        t = [self.cmpcode(x, op, y, pay) for x, op, y in gs]
        if len(t) == 1:
            ((p, g),) = t
            return lambda ov: p(g(ov))
        if len(t) == 2:
            (p, g), (q, h) = t
            return lambda ov: p(g(ov)) and q(h(ov))
        if len(t) == 3:  # three terms to a frame, so a long list costs its terms
            (p, g), (q, h), (r, k) = t
            return lambda ov: p(g(ov)) and q(h(ov)) and r(k(ov))
        head, tail = self.guardcode(gs[:3], pay), self.guardcode(gs[3:], pay)
        return lambda ov: head(ov) and tail(ov)

    @staticmethod
    def split_cell(s):
        """A cell name and the half of it a ``.hi`` or ``.lo`` picks, where it does."""
        return (s[:-3], s[-2:]) if s.endswith((".hi", ".lo")) else (s, None)

    def space(self, s):
        """One named cell's own space, resolved once: how it is read and written.

        The tick's scratch, an instrument's pulse width, the global channel's, the
        image's register pair, or a cell of the voice being committed (§5).
        """
        gp = self.spaces.get(s)
        if gp is None:
            gp = self.spaces[s] = self.spacecode(s)
        return gp

    def spacecode(self, s):
        """How one space is read and written, dispatched on the name and not per read."""
        if s == "tick":
            d = self.acc
            return (lambda: d[0]), (lambda val: d.__setitem__(0, val))
        if s == "ins.pw":
            pw, ins = self.pw, self.c["ins"]
            return (
                lambda: pw[str(ins[self.v])],
                lambda val: pw.__setitem__(str(ins[self.v]), val),
            )
        if s[:1] == "#":
            k, d = s[1:], self.gl
            return (lambda: d[k]), (lambda val: d.__setitem__(k, val))
        if s[:7] == "shadow.":
            k = s[7:]
            return (lambda: self.shadow_pair(k)), (lambda val: self.shadow_store(k, val))
        d = self.c[s]
        return (lambda: d[self.v]), (lambda val: d.__setitem__(self.v, val))

    def cellget(self, name):
        """One named cell read, compiled: its space, and the half a ``.hi``/``.lo`` picks."""
        s, part = self.split_cell(name)
        get = self.space(s)[0]
        if part is None:
            return get
        return (lambda: get() & 0xFF) if part == "lo" else (lambda: (get() >> 8) & 0xFF)

    def cellput(self, name):
        """One named cell written; a half of it leaves the other half the one it had."""
        s, part = self.split_cell(name)
        get, put = self.space(s)
        if part is None:
            return put
        if part == "lo":
            return lambda val: put(get() & 0xFF00 | val & 0xFF)
        return lambda val: put(get() & 0xFF | (val & 0xFF) << 8)
