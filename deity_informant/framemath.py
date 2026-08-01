"""framemath: rung (d2), 16-bit arithmetic lifting (docs/frameprog.md 4).

An 8-bit add/sub on the lo lane plus the carry (borrow) it propagates into the hi
lane is one 16-bit add/sub. The carry link is the evidence, and it is local: it
exists only where the two lanes are halves of one quantity.
"""

from __future__ import annotations

from . import expr as E
from . import framefuse as FF
from . import frameproc
from . import streams as ST
from .structured import Proof

_ADD, _SUB = "INT_ADD", "INT_SUB"
_VOL = frozenset((0xD41B, 0xD41C, 0xD012, 0xDC04, 0xDC05, 0xDD04, 0xDD05))


def _loc(name):
    return ("loc", name, 2)


def _trunc(n):
    return ("op", "COPY", (n,), 1)


def _hi_byte(n):
    return _trunc(("op", "INT_RIGHT", (n, ("const", 8, 1)), 2))


def _resolve(n, env, seen=None):
    """Follow ``loc`` definitions to the first non-``loc`` expression."""
    seen = seen or set()
    while n[0] == "loc" and n[1] in env and n[1] not in seen:
        seen.add(n[1])
        n = env[n[1]]
    return n


def _same(a, b, env):
    return a == b or _resolve(a, env) == _resolve(b, env)


def _update(val, env):
    """``(lane value, step, op)`` when ``val`` is a byte add/sub, else None."""
    r = _resolve(val, env)
    if r[0] != "op" or r[3] != 1:
        return None
    if r[1] == _ADD and len(r[2]) == 2:
        return (r[2][0], r[2][1], _ADD)
    if r[1] == _SUB:
        return (r[2][0], r[2][1], _SUB)
    return None


def _carry_over(n, env):
    """``(a, b)`` a carry term is over; ``carry(x, $00)`` is 0 and drops out."""
    r = _resolve(n, env)
    if r[0] != "op":
        return None
    if r[1] == "INT_CARRY":
        return None if E.is_const(r[2][1]) and r[2][1][1] == 0 else (r[2][0], r[2][1])
    if r[1] == "INT_OR":
        live = [g for g in (_carry_over(c, env) for c in r[2]) if g is not None]
        return live[0] if len(live) == 1 else None
    return None


def _borrow_over(n, env):
    """``(a, b)`` a borrow term is over: ``$01 - (zext(b) <= zext(a))`` or ``a < b``."""
    r = _resolve(n, env)
    if r[0] != "op":
        return None
    if r[1] == "INT_LESS":
        return (r[2][0], r[2][1])
    if r[1] == _SUB and r[2][0] == ("const", 1, 1):
        c = _resolve(r[2][1], env)
        if c[0] == "op" and c[1] == "INT_LESSEQUAL":
            return (ST._strip_zext(c[2][1]), ST._strip_zext(c[2][0]))
    return None


def _reads(n):
    """Every ``mem`` node under ``n``."""
    out, stack = [], [n]
    while stack:
        x = stack.pop()
        if x[0] == "mem":
            out.append(x)
            stack.append(x[1])
        elif x[0] == "op":
            stack.extend(x[2])
    return out


def _volatile(n):
    return any(E.is_const(m[1]) and m[1][1] in _VOL for m in _reads(n))


def _clobbers(stmt, exprs):
    """True where ``stmt`` may change the value of any of ``exprs``."""
    if stmt[0] == "asg":
        return any(stmt[1] in frameproc._locset(x) for x in exprs)
    if stmt[0] != "st":
        return True
    base, idx = FF._addr_split(stmt[1])
    if base is None:
        return True
    span = 0 if idx is None else E.mask(FF._w(idx))
    return any(any(FF._may_read(x, c) for c in range(base, base + span + 1)) for x in exprs)


def _hits(stmt, base, span):
    """True where ``stmt`` may read or write a cell of the lane at ``base``."""
    cells = range(base, base + span + 1)
    if stmt[0] not in ("asg", "st"):
        return True
    if stmt[0] == "st":
        b, idx = FF._addr_split(stmt[1])
        if b is None:
            return True
        s = 0 if idx is None else E.mask(FF._w(idx))
        if not (b + s < base or b > base + span):
            return True
    return any(any(FF._may_read(x, c) for c in cells) for x in frameproc._stmt_exprs(stmt))


class _Site:
    """One byte-wise 16-bit update, its premise counts and its refusal."""

    __slots__ = ("lo", "hi", "op", "adjacent", "why", "sid")

    def __init__(self, lo, hi, op, adjacent):
        self.lo, self.hi, self.op, self.adjacent = lo, hi, op, adjacent
        self.why = None
        self.sid = None

    def proof(self):
        body = "16-bit %s: lanes $%04X/$%04X, %s%s" % (
            "add" if self.op == _ADD else "sub",
            self.lo,
            self.hi,
            "adjacent cells" if self.adjacent else "split tables",
            "" if self.sid is None else ", SID pair $%04X" % self.sid,
        )
        return Proof(
            self.lo,
            "math",
            "refused" if self.why else "lifted",
            (self.lo, self.hi),
            "%s; %s" % (body, self.why or "carry chain"),
        )


def _match(lst, i, env):
    """``(j, site, parts)`` for the update the store at ``lst[i]`` opens, else None."""
    s = lst[i]
    lo = _update(s[2], env)
    if lo is None:
        return None
    lo_base, lo_idx = FF._addr_split(s[1])
    if lo_base is None or not _same(lo[0], ("mem", s[1], 1), env):
        return None
    for j in range(i + 1, len(lst)):
        t = lst[j]
        if t[0] == "asg":
            continue
        if t[0] != "st":
            return None
        hi = _update(t[2], env)
        if hi is None or hi[2] != lo[2]:
            continue
        over = (_carry_over if lo[2] == _ADD else _borrow_over)(hi[1], env)
        if over is None or not _same(over[0], lo[0], env) or not _same(over[1], lo[1], env):
            continue
        if not _same(hi[0], ("mem", t[1], 1), env):
            continue
        hi_base, hi_idx = FF._addr_split(t[1])
        if hi_base is None or hi_idx != lo_idx:
            continue
        site = _Site(lo_base, hi_base, lo[2], hi_base == lo_base + 1)
        return j, site, (lo[0], hi[0], lo[1], lo_idx)
    return None
