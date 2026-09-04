"""The region tree the levels from L2 on carry, and the player that renders one.

A statement list is ordered, so a guard over a value a statement just left is a
second statement of the same list; ``loop``, ``region``, ``take`` and ``trap``
stand beside section 3.3's row, whose leaves stay the player's own.
"""

from __future__ import annotations

from .. import universal

W = 16  # the width a comparison in a value position is taken at
OPS = {"borrow_out": "==", "carry_out": "!="}
SCRATCH = "$"  # a cell an expansion declares for itself
FORMS = ("loop", "region", "take", "trap")


def isrow(s):
    """Whether one statement is section 3.3's own row, which every level admits."""
    return not any(k in s for k in FORMS)


def read(cell):
    """One cell in a value position: the global channel's, or the voice's own."""
    return {"global": cell[1:]} if cell.startswith("#") else {"cell": cell}


def put(cell):
    """One cell as a ``sets`` target."""
    return cell if cell.startswith(("#", "!", "*")) else "@" + cell


def truth(term):
    """One guard term in a value position: worth 1 where it holds and 0 where not.

    The chip's own comparisons, as :mod:`..read` writes them where an
    if-conversion needs the decision a block made and not the branch it took.
    """
    x, op, y = term
    if op in ("==", "!="):
        d = {"sub": [0, {"and": [{"sub": [x, y]}, 0xFFFF]}]}
        return {"borrow_out" if op == "==" else "carry_out": [d, W]}
    if op == "<":
        return {"carry_out": [{"sub": [x, y]}, W]}
    if op == ">=":
        return {"borrow_out": [{"sub": [x, y]}, W]}
    if op == ">":
        return {"carry_out": [{"sub": [y, x]}, W]}
    raise KeyError("no value for the guard term %r" % (op,))


def truth_of(when):
    """A whole guard list in a value position: its terms, and nothing between."""
    if not when:
        return 1
    got = truth(when[0])
    for t in when[1:]:
        got = {"and": [got, truth(t)]}
    return got


def common(rows):
    """The guard every statement of a run carries: the record's own ``when``."""
    if not rows:
        return []
    return [t for t in (rows[0].get("when") or []) if all(t in (r.get("when") or []) for r in rows)]


def sets1(s, target, val=None):
    """Whether one statement is a single assignment to a target, and to a value."""
    got = s.get("sets") or []
    if len(got) != 1 or got[0][0] != target:
        return False
    return val is None or got[0][1] == val


def cellof(target):
    return target[1:] if target[:1] == "@" else target


def selfstep(s):
    """``(cell, op, delta, width)`` where a statement moves its own target, else ``None``."""
    got = s.get("sets") or []
    if len(got) != 1:
        return None
    t, v = got[0]
    if not isinstance(v, dict) or "and" not in v:
        return None
    m, inner = v["and"][1], v["and"][0]
    if not isinstance(m, int) or not isinstance(inner, dict) or len(inner) != 1:
        return None
    op = next(iter(inner))
    if op not in ("add", "sub") or inner[op][0] != read(cellof(t)):
        return None
    return (cellof(t), op, inner[op][1], m.bit_length())


def isreg(r):
    """Whether every target of a statement is a register the chip has."""
    got = r.get("sets") or []
    return bool(got) and all(not t.startswith(("@", "#", "!", "*")) for t, _v in got)


def untruth(node):
    """One guard list read back out of the value position it was written in."""
    if isinstance(node, dict) and "and" in node and isinstance(node["and"][0], dict):
        got = untruth(node["and"][0])
        rest = untruth(node["and"][1]) if got is not None else None
        return None if rest is None else got + rest
    if not isinstance(node, dict) or len(node) != 1:
        return None
    k, a = next(iter(node.items()))
    if k not in OPS or not isinstance(a[0], dict) or "sub" not in a[0]:
        return None
    d = a[0]["sub"]
    if d[0] == 0 and isinstance(d[1], dict) and "and" in d[1]:
        x, y = d[1]["and"][0]["sub"]
        return [[x, OPS[k], y]]
    return [[d[0], "<" if k == "carry_out" else ">=", d[1]]]


def scratch(stmts, out=None):
    """The cells an expansion declares for itself: the ones it names with ``$``."""
    out = set() if out is None else out
    for s in stmts or ():
        for t, _v in s.get("sets", ()) or ():
            name = t.lstrip("@#!*")
            if name.startswith(SCRATCH):
                out.add(name)
        scratch(s.get("region") or (s.get("loop") or {}).get("body"), out)
    return out


def flatten(stmts, when=()):
    """The rows a region tree is, where every statement has one, else ``None``.

    A region is its own list under the guard it stands on, a loop whose trip the
    object states outright is that many turns of its body, and a ``take`` or a
    ``trap`` is a form no row states.
    """
    out = []
    for s in stmts:
        w = list(when) + [t for t in (s.get("when") or []) if t not in when]
        if "trap" in s or "take" in s:
            return None
        if "region" in s:
            got = flatten(s["region"], w)
        elif "loop" in s:
            n = s["loop"]["trip"]
            got = None if not isinstance(n, int) else flatten(list(s["loop"]["body"]) * n, w)
        else:
            got = [{**({"when": w} if w else {}), **{k: v for k, v in s.items() if k != "when"}}]
        if got is None:
            return None
        out += got
    return out


class Player(universal.Player):
    """The unchanged player, with the region tree's control and none of its leaves.

    ``rowplan`` is the one place section 3.3's guarded rows compile, so the region
    tree is admitted there and nowhere else.
    """

    def rowplan(self, rows, pay=None):
        return [self.stmtcode(s, pay) for s in rows]

    def putcode(self, t):
        """A ``sets`` target: any cell of section 5, written where the player writes it."""
        if t[:1] == "@" and self.split_cell(t[1:])[0] not in self.c:
            write = self.cellput(t[1:])
            return lambda val, prod, edge: write(val)
        return super().putcode(t)

    def runstream(self, sr, prod, edge, ov=None):
        self.beyond = sr.beyond
        for f in sr.plan:
            f(prod, edge, ov)

    def stmtcode(self, s, pay=None, act=True):
        """One statement of the region tree, compiled to a closure over the payload."""
        when = self.guardcode(s.get("when"), pay)
        if "trap" in s:
            why = s["trap"] if isinstance(s["trap"], str) else "a statement that never runs"
            return lambda prod, edge, ov: self.sprung(why) if when(ov) else None
        if "take" in s:
            f = self.code_of(s["take"], pay)
            return lambda prod, edge, ov: self.take(f(ov), prod) if when(ov) else None
        if "loop" in s:
            trip = self.code_of(s["loop"]["trip"], pay)
            return _loop(when, trip, [self.stmtcode(x, pay, act) for x in s["loop"]["body"]])
        if "region" in s:
            body = [self.stmtcode(x, pay, act and not s.get("one")) for x in s["region"]]
            return _region(self, when, body, s.get("beyond"), bool(s.get("one")) and act)
        sets = self.setcode(s.get("sets", ()), pay)
        return _row(self, when, sets, self.pointcode(s.get("point", ()), pay), act)


def _row(p, when, sets, pts, act):
    def row(prod, edge, ov):
        if not when(ov):
            return
        if act:
            p.act += 1
        for setter, f in sets:
            setter(f(ov), prod, edge)
        if pts:
            p.points(pts, ov)

    return row


def _loop(when, trip, body):
    def loop(prod, edge, ov):
        if not when(ov):
            return
        for _ in range(trip(ov)):
            for f in body:
                f(prod, edge, ov)

    return loop


def _region(p, when, body, beyond, one):
    def region(prod, edge, ov):
        if not when(ov):
            return
        p.cur = beyond
        if one:
            p.act += 1
        for f in body:
            f(prod, edge, ov)

    return region


def render(obj, ticks):
    """The whole horizon of an object whose streams are region trees."""
    p = Player(obj)
    return [p.tick() for _ in range(ticks)]
