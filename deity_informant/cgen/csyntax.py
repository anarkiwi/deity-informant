"""C statement trees: declarations at the innermost scope holding every use, then text.

Statements: ``("a", name, text, refs)`` an assignment, ``("s", text, refs)``, ``("j", text,
refs)`` a jump, ``("l", text, refs)`` a label, ``("if", cond, refs, then, else)``,
``("loop", kind, cond, refs, body)`` (kind ``for``, ``while`` or ``do``) and ``("switch", sel,
refs, [(labels, body)])``.
"""

from __future__ import annotations

from .marks import CLOSE, OPEN

ZERO = {"struct": "{0}"}


def _own(st):
    """The variables statement ``st`` reads or writes outside its nested statements."""
    if st[0] == "a":
        return st[3] | {st[1]}
    return st[2] if st[0] in ("s", "j", "l", "if", "switch") else st[3]


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
    """``(definitely assigned after, no read before assignment)`` over ``items``; control
    reaching a label may come from anywhere, so nothing is assigned there."""
    gone = False
    for st in items:
        kind = st[0]
        if kind == "l":
            done, gone = False, False
            continue
        if gone:
            continue
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
                done, gone = True, True
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


def _lead(text):
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
            m, text = _lead(st[2])
            out.append(f"{m}{st[1]} = {text};")
        elif kind == "d":
            first = next((i for i, (_, e) in enumerate(st[2]) if e), None)
            m = "" if first is None else _lead(st[2][first][1])[0]
            parts = [
                n if e is None else f"{n} = {_lead(e)[1] if i == first else e}"
                for i, (n, e) in enumerate(st[2])
            ]
            out.append(f"{m}{st[1]} {', '.join(parts)};")
        elif kind in ("s", "j", "l"):
            out.append(st[1])
        elif kind == "if":
            out += _render_if(st)
        elif kind == "loop":
            body = render(st[4])
            m, cond = _lead(st[2])
            if st[1] == "do":
                out += ["do {", *body, f"{m}}} while ({cond});"]
            elif st[1] == "while":
                out += [f"{m}while ({cond}) {{", *body, "}"]
            else:
                out += ["for (;;) {", *body, "}"]
        else:
            m, sel = _lead(st[1])
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
    m, cond = _lead(st[1])
    out = [f"{m}if ({cond}) {{", *render(st[3])]
    els = st[4]
    while len(els) == 1 and els[0][0] == "if":
        m, cond = _lead(els[0][1])
        out += [f"{m}}} else if ({cond}) {{", *render(els[0][3])]
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


RETURN = ("j", "return;", frozenset())


def _targets(items):
    """The labels the gotos of ``items`` jump to, nested ones included."""
    return {st[1][5:-1] for st in walk(items) if st[0] == "j" and st[1].startswith("goto ")}


def _labels(items):
    """The labels of ``items``, nested ones included."""
    return {st[1][:-2] for st in walk(items) if st[0] == "l"}


def sections(items, local):
    """``[(label, items)]`` of a function body cut at each top-level label no variable of
    ``local`` is used across and no goto crosses but into it from before: what follows
    the label runs the same whatever ran before it, so each section is a function of its
    own (the label ending the section before it, none after the last)."""
    out, start = [], 0
    for i, st in enumerate(items):
        if st[0] != "l":
            continue
        before, after, lab = items[start:i], items[i + 1 :], st[1][:-2]
        shared = names_in(before) & names_in(after) & local
        if shared or _targets(before) & (_labels(after) - {lab}):
            continue
        if _targets(after) & (_labels(before) | {lab}):
            continue
        out.append((lab, before))
        start = i + 1
    return out + [(None, items[start:])]


def tails(items, local, call):
    """``(head, [(label, items, params)])`` of a function body: from its last top-level
    label back, each label nothing after it jumps back before is the start of a tail, the
    statements after it to the next one, ``params`` the variables of ``local`` the tail
    reads before it sets them (:func:`assigned_first`), the gotos to later tails and the
    fall into the next read as those tails' calls (``call(label, params)``)."""
    out, refs = [], {}
    while True:
        i = next((i for i in range(len(items) - 1, -1, -1) if items[i][0] == "l"), None)
        if i is None:
            return items, out
        before, after, lab = items[:i], items[i + 1 :], items[i][1][:-2]
        if _targets(after) & (_labels(before) | {lab}) or _targets(before) & _labels(after):
            return items, out
        body = calling(after, {t: call(t, ps) for t, ps in refs.items()})
        if out:
            body = [*body, call(out[0][0], refs[out[0][0]])]
        live = names_in(body) & names_in(before) & local
        params = sorted((v for v in live if not assigned_first(body, v)), key=_order)
        refs[lab] = params
        out.insert(0, (lab, after, params))
        items = before


def _splice(items, f):
    """``items`` with each statement, nested ones included, replaced by the list ``f``
    gives for it."""
    out = []
    for st in items:
        if st[0] == "if":
            st = (*st[:3], _splice(st[3], f), _splice(st[4], f), *st[5:])
        elif st[0] == "loop":
            st = (*st[:4], _splice(st[4], f))
        elif st[0] == "switch":
            st = (*st[:3], [(lab, _splice(b, f)) for lab, b in st[3]])
        out += f(st)
    return out


def calling(items, calls):
    """``items`` with each goto to a label of ``calls`` (``{label: call statement}``) the
    call and a return."""

    def f(st):
        if st[0] == "j" and st[1].startswith("goto ") and st[1][5:-1] in calls:
            return [calls[st[1][5:-1]], RETURN]
        return [st]

    return _splice(items, f)


def _map(items, f):
    """``items`` with each statement, nested ones included, replaced by ``f`` of it."""
    out = []
    for st in items:
        st = f(st)
        if st[0] == "if":
            st = (*st[:3], _map(st[3], f), _map(st[4], f), *st[5:])
        elif st[0] == "loop":
            st = (*st[:4], _map(st[4], f))
        elif st[0] == "switch":
            st = (*st[:3], [(lab, _map(b, f)) for lab, b in st[3]])
        out.append(st)
    return out


def returning(items, label):
    """``items`` of a section, each goto to the ``label`` ending it a return."""
    jump = ("j", f"goto {label};")
    return _map(items, lambda st: RETURN if st[:2] == jump else st)


def cost(items, depth=0):
    """The cognitive complexity of ``items`` at nesting ``depth`` (SonarSource's rules on
    the statements, clang-tidy's ``readability-function-cognitive-complexity``): an ``if``,
    loop or ``switch`` one plus its depth, an ``else if`` and an ``else`` one, a ``goto``
    one; a statement's arms one level deeper. Conditions count the same either way."""
    out = 0
    for st in items:
        if st[0] == "j" and st[1].startswith("goto "):
            out += 1
        elif st[0] == "if":
            out += 1 + depth + _arms(st, depth + 1)
        elif st[0] in ("loop", "switch"):
            out += 1 + depth + sum(cost(sub, depth + 1) for sub in children(st))
    return out


def _arms(st, depth):
    """The cost of an ``if``'s arms, an ``else`` holding one ``if`` an ``else if``."""
    out, els = cost(st[3], depth), st[4]
    while len(els) == 1 and els[0][0] == "if":
        out += 1 + cost(els[0][3], depth)
        els = els[0][4]
    return out + (1 + cost(els, depth) if els else 0)


def _exits(items, i, jump):
    """The rewrites of ``if`` statement ``i`` of ``items`` that leave by ``jump`` first."""
    st, last = items[i], i == len(items) - 1
    if last:
        pairs = [(st[4], st[3], st[5], st[1]), (st[3], st[4], st[1], st[5])]
    elif not st[4] and ends_in_jump(st[3]):
        pairs = [(items[i + 1 :], st[3], st[5], st[1])]
    else:
        return []
    out = []
    for first, rest, cond, neg in pairs:
        first = first if ends_in_jump(first) else [*first, jump]
        out.append(([*items[:i], ("if", cond, st[2], first, [], neg)], rest))
    return out


def ending(items, jump, depth=0):
    """``items`` ending a function or loop body at nesting ``depth`` (``jump`` its ``return``
    or ``continue``), each ``if`` that ends this level either way leaving first by one arm,
    ending in ``jump``, and the other after it at this level, wherever that lowers the
    cognitive complexity (:func:`cost`) once the statements moved up end the level in turn: a
    last ``if`` either way round, one whose arm leaves before the rest with the rest as the
    arm leaving first. Each rewrite lowers the cost and recurs only into the statements of
    one ``if``, so the rewrites end."""
    while True:
        low, best = cost(items, depth), None
        for i in range(len(items) - 1, -1, -1):
            for head, rest in _exits(items, i, jump) if items[i][0] == "if" else ():
                got = [*head, *ending(rest, jump, depth)]
                c = cost(got, depth)
                if c < low:
                    low, best = c, got
        if best is None:
            return items
        items = best


def guarded(items):
    """``items`` ending a function that returns nothing (:func:`ending` with ``return``)."""
    return ending(items, RETURN)


CONTINUE = ("j", "continue;", frozenset())


def flattened(items, depth=0):
    """``items`` at nesting ``depth`` with each ``if`` whose ``else`` ends in a jump and whose
    ``then`` does not tested the other way, its ``else`` the jumping arm and its ``then``
    after it, wherever that lowers the cognitive complexity (:func:`cost`), and each loop
    body ending in an ``if`` rewritten to continue first (:func:`ending`)."""
    out = []
    for st in items:
        if st[0] == "if":
            st = (*st[:3], flattened(st[3], depth + 1), flattened(st[4], depth + 1), *st[5:])
            if st[4] and ends_in_jump(st[4]) and not ends_in_jump(st[3]):
                alt = [("if", st[5], st[2], st[4], [], st[1]), *st[3]]
                if cost(alt, depth) < cost([st], depth):
                    out += alt
                    continue
        elif st[0] == "loop":
            st = (*st[:4], ending(flattened(st[4], depth + 1), CONTINUE, depth + 1))
        elif st[0] == "switch":
            st = (*st[:3], [(lab, flattened(b, depth + 1)) for lab, b in st[3]])
        out.append(st)
    return out


def walk(items):
    """Every statement of ``items``, nested ones included, in order."""
    for st in items:
        yield st
        for sub in children(st):
            yield from walk(sub)


def names_in(items):
    """Every variable ``items`` read or write."""
    out = set()
    for st in items:
        out |= _own(st)
        for sub in children(st):
            out |= names_in(sub)
    return out


def hexes(vals, width=2):
    """``vals`` as a C initializer list of hex literals, ``width`` digits at least."""
    return ", ".join(f"0x{v:0{width}X}" for v in vals)
