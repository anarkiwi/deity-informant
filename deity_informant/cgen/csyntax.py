"""C statement trees: declarations at the innermost scope holding every use, then text.

Statements: ``("a", name, text, refs)`` an assignment, ``("s", text, refs)``, ``("j", text,
refs)`` a jump, ``("if", cond, refs, then, else)``, ``("loop", kind, cond, refs, body)`` (kind
``for``, ``while`` or ``do``) and ``("switch", sel, refs, [(labels, body)])``.
"""

from __future__ import annotations

ZERO = {"struct": "{0}"}


def _own(st):
    """The variables statement ``st`` reads or writes outside its nested statements."""
    if st[0] == "a":
        return st[3] | {st[1]}
    return st[2] if st[0] in ("s", "j") else st[2] if st[0] in ("if", "switch") else st[3]


def children(st):
    """The statement lists nested in ``st``."""
    if st[0] == "if":
        return [st[3], st[4]]
    if st[0] == "loop":
        return [st[4]]
    if st[0] == "switch":
        return [b for _, b in st[3]]
    return []


def _locations(items, path, out, lists):
    lists[id(items)] = items
    for i, st in enumerate(items):
        here = path + ((id(items), i),)
        for v in _own(st):
            out.setdefault(v, []).append(here)
        for sub in children(st):
            _locations(sub, here, out, lists)


def _scope(locs):
    """``(list id, index)`` of the declaration of a variable used at ``locs``."""
    k = 0
    while all(len(p) > k for p in locs) and len({p[k] for p in locs}) == 1:
        k += 1
    if all(len(p) > k for p in locs) and len({p[k][0] for p in locs}) == 1:
        return locs[0][k][0], min(p[k][1] for p in locs)
    return locs[0][k - 1]


def assigned_first(items, v):
    """Whether every read of ``v`` in ``items`` follows an assignment on every path."""
    return _da(items, v, False)[1]


def _da(items, v, done):  # pylint: disable=too-many-return-statements,too-many-branches
    """``(definitely assigned after, no read before assignment)`` over ``items``."""
    for st in items:
        kind = st[0]
        if kind == "a":
            if v in st[3] and not done:
                return done, False
            done = done or st[1] == v
        elif kind == "d":
            if v in st[3] and not done:
                return done, False
            done = done or any(n == v and e is not None for n, e in st[2])
        elif kind in ("s", "j"):
            if v in st[2] and not done:
                return done, False
            if kind == "j":
                return True, True
        elif kind == "if":
            if v in st[2] and not done:
                return done, False
            a, ok = _da(st[3], v, done)
            b, ok2 = _da(st[4], v, done)
            if not (ok and ok2):
                return done, False
            done = a and b
        elif kind == "loop":
            if v in st[3] and not done and st[1] == "while":
                return done, False
            after, ok = _da(st[4], v, done)
            if not ok or (v in st[3] and not after and st[1] == "do"):
                return done, False
        else:
            if v in st[2] and not done:
                return done, False
            arms = [_da(b, v, done) for _, b in st[3]]
            if not all(ok for _, ok in arms):
                return done, False
            dflt = any(None in labels for labels, _ in st[3])
            done = done or (dflt and all(a for a, _ in arms))
    return done, True


def drop_unread(items, names, read=None):
    """``items`` without the constant assignments to ``names`` that nothing reads."""
    read = _reads(items) if read is None else read
    out = []
    for st in items:
        if st[0] == "a" and st[1] in names and st[1] not in read and st[2].isdigit():
            continue
        if st[0] == "if":
            then, els = drop_unread(st[3], names, read), drop_unread(st[4], names, read)
            if not then and not els:
                continue
            st = (*st[:3], then, els, *st[5:])
        elif st[0] == "loop":
            st = (*st[:4], drop_unread(st[4], names, read))
        elif st[0] == "switch":
            st = (*st[:3], [(lab, drop_unread(b, names, read)) for lab, b in st[3]])
        out.append(st)
    return out


def _reads(items):
    """Every variable ``items`` read."""
    out = set()
    for st in items:
        out |= st[3] if st[0] == "a" else _own(st)
        for sub in children(st):
            out |= _reads(sub)
    return out


def declare(body, types):
    """``body`` with each variable of ``types`` (``{name: ctype}``) declared at the innermost
    scope holding its uses: its first assignment there when that is a statement of the scope,
    else a declaration before, zero-initialized unless assigned before every read."""
    locs, lists = {}, {}
    _locations(body, (), locs, lists)
    at = {}
    for v in types:
        if v in locs:
            lid, idx = _scope(locs[v])
            at.setdefault(lid, []).append((idx, v))
    for lid, decls in at.items():
        items = lists[lid]
        before = {}
        for idx, v in decls:
            st = items[idx]
            if st[0] == "a" and st[1] == v and v not in st[3]:
                items[idx] = ("d", types[v], [(v, st[2])], st[3])
                continue
            init = None if assigned_first(items[idx:], v) else ZERO.get(types[v][:6], "0")
            before.setdefault(idx, []).append((v, init))
        for idx in sorted(before, reverse=True):
            group = {}
            for v, init in sorted(before[idx], key=lambda x: _order(x[0])):
                group.setdefault((types[v], init is None), []).append((v, init))
            items[idx:idx] = [("d", t, vs, frozenset()) for (t, _), vs in group.items()]
    return body


def _order(name):
    """A variable's sort key: its number after the prefix letter."""
    return (len(name), name)


def render(items):
    """The lines of ``items``."""
    out = []
    for st in items:
        kind = st[0]
        if kind == "a":
            out.append(f"{st[1]} = {st[2]};")
        elif kind == "d":
            names = ", ".join(n if e is None else f"{n} = {e}" for n, e in st[2])
            out.append(f"{st[1]} {names};")
        elif kind in ("s", "j"):
            out.append(st[1])
        elif kind == "if":
            out += _render_if(st)
        elif kind == "loop":
            body = render(st[4])
            if st[1] == "do":
                out += ["do {", *body, f"}} while ({st[2]});"]
            elif st[1] == "while":
                out += [f"while ({st[2]}) {{", *body, "}"]
            else:
                out += ["for (;;) {", *body, "}"]
        else:
            out.append(f"switch ({st[1]}) {{")
            for labels, body in st[3]:
                out += [f"case {x}:" if x is not None else "default:" for x in labels]
                lines = render(body)
                tail = [] if body and body[-1][0] == "j" else ["break;"]
                out += ["{", *lines, *tail, "}"]
            out.append("}")
    return out


def _render_if(st):
    """The lines of an ``if`` statement, an ``else`` holding one ``if`` as ``else if``."""
    out = [f"if ({st[1]}) {{", *render(st[3])]
    els = st[4]
    while len(els) == 1 and els[0][0] == "if":
        out += [f"}} else if ({els[0][1]}) {{", *render(els[0][3])]
        els = els[0][4]
    if els:
        out += ["} else {", *render(els)]
    out.append("}")
    return out


def ends_in_jump(items):
    """Whether control never falls off the end of ``items``."""
    if not items:
        return False
    last = items[-1]
    if last[0] == "j":
        return True
    if last[0] == "if":
        return bool(last[4]) and ends_in_jump(last[3]) and ends_in_jump(last[4])
    return last[0] == "loop" and last[1] == "for" and not _breaks(last[4])


def _breaks(items):
    """Whether ``items`` break out of their loop."""
    for st in items:
        if st[0] == "j" and st[1] == "break;":
            return True
        if st[0] == "if" and (_breaks(st[3]) or _breaks(st[4])):
            return True
        if st[0] == "switch" and any(_breaks(b) for _, b in st[3]):
            return True
    return False


def names_in(items):
    """Every variable ``items`` read or write."""
    out = set()
    for st in items:
        out |= _own(st)
        for sub in children(st):
            out |= names_in(sub)
    return out
