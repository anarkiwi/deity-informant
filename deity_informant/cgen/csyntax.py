"""C statement trees (wayward-eagle's): declarations at the innermost scope holding every
use, unread assignments dropped, then text."""

from __future__ import annotations

from wayward_eagle import assigned_first, children, order, own

from .marks import CLOSE, OPEN

ZERO = {"struct": "{0}"}


def _locations(items, path, out, lists):
    lists[id(items)] = items
    for i, st in enumerate(items):
        here = path + ((id(items), i),)
        for v in own(st):
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


def drop_unread(items, dead):
    """``items`` without the assignments ``dead(st, read)`` holds of, ``read`` the variables
    they read, repeated until none is left."""
    while True:
        read = _reads(items)
        out = _drop(items, lambda st: dead(st, read))
        if out == items:
            return out
        items = out


def _drop(items, dead):
    out = []
    for st in items:
        if st[0] == "a" and dead(st):
            continue
        if st[0] == "if":
            then, els = _drop(st[3], dead), _drop(st[4], dead)
            if not then and not els:
                continue
            st = (*st[:3], then, els, *st[5:])
        elif st[0] == "loop":
            st = (*st[:4], _drop(st[4], dead))
        elif st[0] == "switch":
            st = (*st[:3], [(lab, _drop(b, dead)) for lab, b in st[3]])
        out.append(st)
    return out


def _reads(items):
    """Every variable ``items`` read."""
    out = set()
    for st in items:
        out |= st[3] if st[0] == "a" else own(st)
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
            for v, init in sorted(before[idx], key=lambda x: order(x[0])):
                group.setdefault((types[v], init is None), []).append((v, init))
            items[idx:idx] = [("d", t, vs, frozenset()) for (t, _), vs in group.items()]
    return body


def lead(text):
    """``(marker, rest)`` of ``text``: its leading marker (:mod:`.marks`), moved to the start
    of the line printing it, and the rest."""
    if not text or not text.startswith(OPEN):
        return "", text or ""
    end = text.index(CLOSE) + 1
    return text[:end], text[end:]


def render(items):
    """The lines of ``items``."""
    out = []
    for st in items:
        kind = st[0]
        if kind == "a":
            m, text = lead(st[2])
            out.append(f"{m}{st[1]} = {text};")
        elif kind == "d":
            first = next((i for i, (_, e) in enumerate(st[2]) if e), None)
            m = "" if first is None else lead(st[2][first][1])[0]
            parts = [
                n if e is None else f"{n} = {lead(e)[1] if i == first else e}"
                for i, (n, e) in enumerate(st[2])
            ]
            out.append(f"{m}{st[1]} {', '.join(parts)};")
        elif kind in ("s", "j", "l"):
            out.append(st[1])
        elif kind == "if":
            out += _render_if(st)
        elif kind == "loop":
            body = render(st[4])
            m, cond = lead(st[2])
            if st[1] == "do":
                out += ["do {", *body, f"{m}}} while ({cond});"]
            elif st[1] == "while":
                out += [f"{m}while ({cond}) {{", *body, "}"]
            else:
                out += ["for (;;) {", *body, "}"]
        else:
            m, sel = lead(st[1])
            out.append(f"{m}switch ({sel}) {{")
            for labels, body in st[3]:
                out += [f"case {x}:" if x is not None else "default:" for x in labels]
                lines = render(body)
                tail = [] if body and body[-1][0] == "j" else ["break;"]
                out += ["{", *lines, *tail, "}"]
            out.append("}")
    return out


def _render_if(st):
    """The lines of an ``if`` statement, an ``else`` holding one ``if`` as ``else if``."""
    m, cond = lead(st[1])
    out = [f"{m}if ({cond}) {{", *render(st[3])]
    els = st[4]
    while len(els) == 1 and els[0][0] == "if":
        m, cond = lead(els[0][1])
        out += [f"{m}}} else if ({cond}) {{", *render(els[0][3])]
        els = els[0][4]
    if els:
        out += ["} else {", *render(els)]
    out.append("}")
    return out


def hexes(vals, width=2):
    """``vals`` as a C initializer list of hex literals, ``width`` digits at least."""
    return ", ".join(f"0x{v:0{width}X}" for v in vals)
