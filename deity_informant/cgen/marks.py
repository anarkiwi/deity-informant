"""Markers: what each printed construct of the ``.c`` comes from (*Translation cost*).

Each printed statement and check carries an invisible marker naming a :class:`Marks` record;
what survives printing is read back in text order and becomes ``#line`` directives.
"""

from __future__ import annotations

import re
from collections import defaultdict

OPEN, CLOSE, END = "\x1e", "\x1f", "\x1d"
_MARK = re.compile("\x1e(\\d+)\x1f|\x1d")
_BLANK = re.compile(r"\s")
#: ``#line`` files: an instruction's and the artifacts' (docs/cgen.md, *Translation cost*).
FILES = ("6502", "check", "trap", "access", "copy", "frame", "runtime")
_LINE = re.compile(r'(?m)^#line \d+ "(?:' + "|".join(FILES) + r')"\n')
_INLINE = re.compile(r'\n# line \d+ "(?:' + "|".join(FILES) + r')"\n')


def strip(text):
    """``text`` without markers."""
    return _MARK.sub("", text)


def strip_lines(c):
    """The ``.c`` ``c`` without the ``#line`` directives :func:`directives` inserted."""
    return _LINE.sub("", _INLINE.sub("", c))


def bare(st):
    """Statement tree ``st`` (a statement or a list of them) without markers, for comparing."""
    if isinstance(st, str):
        return strip(st)
    if isinstance(st, (list, tuple)):
        return type(st)(bare(x) for x in st)
    return st


class Marks:
    """The records markers name: ``("pc", fn, block, visit, ops)`` a statement printing ``ops``
    in visit ``visit`` of block ``block`` of MLIR function ``fn``, ``("check", kind, op, fn,
    trap)``, ``("trap", k)`` and ``("tag", file)``."""

    def __init__(self):
        self.recs, self.visits = [], 0

    def put(self, rec):
        """The marker of a new record ``rec``."""
        self.recs.append(rec)
        return f"{OPEN}{len(self.recs) - 1}{CLOSE}"

    def leading(self, text, kind):
        """Whether ``text`` starts with the marker of a record of ``kind``."""
        m = _MARK.match(text)
        return bool(m and m.group(1) and self.recs[int(m.group(1))][0] == kind)

    def visit(self):
        """A new visit: one printing of a block's statements."""
        self.visits += 1
        return self.visits

    def wrap(self, rec, text):
        """``text`` marked by ``rec``, the enclosing marker resuming after it."""
        return f"{self.put(rec)}{text}{END}"

    @staticmethod
    def finish(raw):
        """``(C, [(offset, record)], [record])``: joined text ``raw`` without markers, where
        each marked construct starts in non-blank characters (an enclosing one resuming after a
        wrapped one), and the records in text order."""
        out, places, order, stack, at, nonblank = [], [], [], [None], 0, 0
        for m in _MARK.finditer(raw):
            piece = raw[at : m.start()]
            out.append(piece)
            nonblank += len(piece) - len(_BLANK.findall(piece))
            at = m.end()
            if m.group(1) is None:
                stack.pop()
            else:
                order.append(int(m.group(1)))
                stack.append(order[-1])
            if stack[-1] is not None:
                places.append((nonblank, stack[-1]))
        out.append(raw[at:])
        return "".join(out), places, order


def directives(c, places, line):
    """Formatted C ``c`` with ``#line`` directives, ``line(record)`` a marker's ``(number,
    file)``: each line holding code names the marker standing before it (the last at an offset
    of ``places`` wins), and a marker inside a line breaks it (``# line``)."""
    want = dict(places)
    out, nonblank, active = [], 0, None

    def say(form):
        n, f = line(active)
        return form.format(f'{n} "{f}"')

    for text in c.splitlines(keepends=True):
        body = [i for i, ch in enumerate(text) if not ch.isspace()]
        if body and nonblank in want:
            active = want[nonblank]
        if body and active is not None:
            out.append(say("#line {}\n"))
        at = 0
        for k, i in enumerate(body):
            if k and nonblank + k in want:
                active = want[nonblank + k]
                out += [text[at:i], say("\n# line {}\n")]
                at = i
        out.append(text[at:])
        nonblank += len(body)
    return "".join(out)


def copies(recs, order, pcs_of):
    """``{pc: n}``: the times each original instruction is printed, summed over its lift nodes
    and inline call chains (:func:`_group`) of the most visits printing one of its operations
    in one block. ``pcs_of(fn, pc)``: each copy's instruction an operation stands for."""
    seen = defaultdict(set)
    for rec in (recs[j] for j in order):
        if rec[0] != "pc":
            continue
        _, fn, block, visit, ops = rec
        for o in (o for o in ops if o.pc):
            for pc in pcs_of(fn, o.pc):
                seen[(pc, _group(o), id(o), block)].add(visit)
    most = defaultdict(int)
    for (pc, group, _, _), visits in seen.items():
        most[(pc, group)] = max(most[(pc, group)], len(visits))
    out = defaultdict(int)
    for (pc, _), n in most.items():
        out[pc] += n
    return dict(sorted(out.items()))


def _group(o):
    """The lift node and call sites of operation ``o``'s location; one the printer made takes
    that of an operation of its instruction in its block."""
    loc = o.loc
    if not loc and o.parent is not None:
        loc = next((x.loc for x in o.parent.ops if x.loc and x.pc == o.pc), ())
    return (loc[0][1], loc[1:]) if loc else (None, id(o.parent))
