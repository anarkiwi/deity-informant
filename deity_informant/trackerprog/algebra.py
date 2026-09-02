"""B7 -- the small algebra the recognition pass reasons in.

The lowered expression language evaluated over a valuation of its cells, the
shapes a section 5 record is read out of, and the substitution one rewrite is.
A leaf the arithmetic does not reach says so rather than guessing.
"""

from __future__ import annotations

from itertools import product

MASK8 = 0xFF
LIMIT = 2  # free cells a constant-under-guard check enumerates the byte over

_OPS = {
    "and": lambda a, b: a & b,
    "or": lambda a, b: a | b,
    "xor": lambda a, b: a ^ b,
    "add": lambda a, b: a + b,
    "sub": lambda a, b: a - b,
    "shr": lambda a, b: a >> b,
    "shl": lambda a, b: a << b,
}
_CMP = {
    "==": lambda a, b: a == b,
    "!=": lambda a, b: a != b,
    "<": lambda a, b: a < b,
    ">=": lambda a, b: a >= b,
    ">": lambda a, b: a > b,
}


class Opaque(Exception):
    """A node the join's own evaluator has no arithmetic for."""


def ev(e, env):
    """One lowered expression under a valuation of the cells it reads."""
    if isinstance(e, int):
        return e
    if not isinstance(e, dict) or len(e) != 1:
        raise Opaque(repr(e))
    k, a = next(iter(e.items()))
    if k == "cell":
        return env[a if isinstance(a, str) else a[0]]
    if k == "global":
        return env["#" + a]
    if k in _OPS:
        return _OPS[k](ev(a[0], env), ev(a[1], env))
    if k in ("bit", "carry_out"):
        return (ev(a[0], env) >> a[1]) & 1
    if k == "borrow_out":
        return 1 - ((ev(a[0], env) >> a[1]) & 1)
    if k == "field":
        return ev(a[0], env) & a[1]
    if k == "u16":
        return (ev(a[0], env) & MASK8) | (ev(a[1], env) & MASK8) << 8
    raise Opaque(k)


def free(e, out=None):
    """Every cell an expression reads, a global's own ``#`` on it."""
    out = set() if out is None else out
    if isinstance(e, dict):
        for k, v in e.items():
            if k == "cell":
                out.add(v if isinstance(v, str) else v[0])
            elif k == "global":
                out.add("#" + v)
            else:
                free(v, out)
    elif isinstance(e, (list, tuple)):
        for x in e:
            free(x, out)
    return out


def evaluable(x):
    """Whether the join's own arithmetic reaches every leaf of an expression."""
    try:
        ev(x, {n: 0 for n in free(x)})
    except (Opaque, KeyError):
        return False
    return True


def constant_under(e, guards):
    """The one value an expression has wherever a guard list holds, else ``None``.

    Machine-checked and not argued: the cells both read are enumerated over the
    byte.  A term the arithmetic does not reach is dropped, which only widens
    the set the value must be constant on, and so is a term over cells the value
    does not reach -- which is every guard of the row that is not about it.
    """
    try:
        guards = [t for t in guards if all(evaluable(x) for x in (t[0], t[2]))]
        guards, names = _about(e, guards)
        names = sorted(names)
        if len(names) > LIMIT:
            return None
        got = set()
        for vals in product(range(256), repeat=len(names)):
            env = dict(zip(names, vals))
            if all(_CMP[op](ev(x, env), ev(y, env)) for x, op, y in guards):
                got.add(ev(e, env))
                if len(got) > 1:
                    return None
    except (Opaque, KeyError):
        return None
    return got.pop() if len(got) == 1 else None


def _about(e, guards):
    """The guard terms that can constrain a value: the ones its own cells reach."""
    names = free(e)
    while True:
        got = [t for t in guards if free([t[0], t[2]]) & names]
        more = names | free([[t[0], t[2]] for t in got])
        if more == names:
            return got, names
        names = more


def target_of(name):
    """The ``sets`` target one cell name is written under."""
    return name if name.startswith("#") else "@" + name


def read_of(name):
    """The expression one cell name is read as."""
    return {"global": name[1:]} if name.startswith("#") else {"cell": name}


def prefix(lists):
    """The longest guard list every row of a set begins with."""
    out = []
    for terms in zip(*list(lists)):
        if any(t != terms[0] for t in terms[1:]):
            break
        out.append(terms[0])
    return out


def extends(when, pre):
    return len(when) >= len(pre) and when[: len(pre)] == pre


def peel(expr, cell, copies):
    """``M8(M8(cell + x) + y)`` as the delta an 8-bit store applies to its own cell."""
    e = expr
    while isinstance(e, dict) and "and" in e and e["and"][1] == MASK8:
        e = e["and"][0]
    if not (isinstance(e, dict) and len(e) == 1):
        return None
    k, a = next(iter(e.items()))
    if k != "add":
        return None
    if follow(a[0], copies) == cell:
        return a[1]
    inner = peel(a[0], cell, copies)
    return None if inner is None else {"add": [inner, a[1]]}


def follow(node, copies):
    """One read through the copies the lowering left: the cell it is a copy of."""
    seen = 0
    while isinstance(node, dict) and node.get("cell") in copies and seen < 8:
        node, seen = copies[node["cell"]], seen + 1
    return node


def unsplit(lo, hi):
    """``(M8(E), M8(E >> 8))`` as ``E``: one word stated once and not twice."""
    if not (isinstance(lo, dict) and "and" in lo and lo["and"][1] == MASK8):
        return None
    if not (isinstance(hi, dict) and "and" in hi and hi["and"][1] == MASK8):
        return None
    a, b = lo["and"][0], hi["and"][0]
    return a if isinstance(b, dict) and b.get("shr") == [a, 8] else None


def rewrite(node, sub):
    """One expression with the reads ``sub`` maps replaced."""
    if isinstance(node, dict):
        got = sub.get(_key(node))
        return got if got is not None else {k: rewrite(v, sub) for k, v in node.items()}
    return [rewrite(x, sub) for x in node] if isinstance(node, list) else node


def _key(node):
    if len(node) != 1:
        return None
    k, v = next(iter(node.items()))
    return (k, v) if k in ("cell", "global") and isinstance(v, str) else None
