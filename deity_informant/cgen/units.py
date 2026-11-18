"""The ``.c`` as translation units by reachability in the call graph: the state's definitions,
each entry's own functions (``static``), and those several entries reach (external, their
prototypes in the ``.h``)."""

from __future__ import annotations

import copy
from typing import Callable, NamedTuple

from pycparser import c_ast

from . import cnodes as cn

#: Where a part goes: every unit, the state's, the ``.h``, by reachability, with its function.
EVERY, STATE, HEADER, CODE, PROTO = "every", "state", "header", "code", "proto"


class Part(NamedTuple):
    """A piece of the ``.c``: ``node`` a ``c_ast`` declaration or definition, or ``(record,
    text)`` verbatim C, or ``(record, text(external))`` verbatim C whose definitions are
    external when it is shared; ``where`` it goes; the names it ``provides`` and ``calls``;
    ``header`` the declarations the ``.h`` needs when other units use it."""

    node: object
    where: str = CODE
    provides: frozenset = frozenset()
    calls: frozenset = frozenset()
    header: tuple = ()


def part(node, where=CODE):
    """The part of ``c_ast`` node ``node``: a definition provides its name and calls those of
    its calls; a data declaration goes to the state's unit, its ``extern`` to the ``.h``."""
    if isinstance(node, c_ast.FuncDef):
        calls = {n.name.name for n in cn.walk(node.body) if isinstance(n, c_ast.FuncCall)}
        calls |= {n.name for n in cn.walk(node.body) if isinstance(n, c_ast.ID)}
        return Part(node, where, frozenset({node.decl.name}), frozenset(calls))
    if isinstance(node, c_ast.Decl) and isinstance(node.type, c_ast.FuncDecl):
        return Part(node, PROTO, frozenset({node.name}))
    if isinstance(node, c_ast.Decl) and node.name and where == CODE:
        return Part(node, where, frozenset({node.name}))
    return Part(node, where)


def single(parts):
    """The ``.c`` of ``parts`` as one unit, in their order, verbatim C as internal."""
    return [_body(p.node, False) for p in parts]


def _body(node, external):
    """``node`` as :func:`cnodes.render` takes it, verbatim C made for ``external``."""
    if isinstance(node, tuple) and isinstance(node[1], Callable):
        return node[0], node[1](external)
    return node


def owners(parts, entries):
    """``{name: frozenset(entries reaching it)}`` over the call graph of ``parts``."""
    calls = {}
    for p in parts:
        for n in p.provides:
            calls.setdefault(n, set()).update(p.calls)
    out = {}
    for e in entries:
        seen, work = {e}, [e]
        while work:
            for t in calls.get(work.pop(), ()):
                if t in calls and t not in seen:
                    seen.add(t)
                    work.append(t)
        for n in seen:
            out[n] = out.get(n, frozenset()) | {e}
    return out


def _external(node):
    """A copy of ``c_ast`` function definition or declaration ``node`` without ``static``."""
    out = copy.copy(node)
    decl = copy.copy(node.decl if isinstance(node, c_ast.FuncDef) else node)
    decl.storage = [s for s in decl.storage if s != "static"]
    if isinstance(node, c_ast.FuncDef):
        out.decl = decl
        return out
    return decl


def _extern(node):
    """The ``extern`` declaration of data definition ``node``, its ``static`` dropped."""
    d = copy.copy(node)
    d.storage, d.init = ["extern"], None
    return d


def _defined(node):
    """Data definition ``node`` without ``static``."""
    d = copy.copy(node)
    d.storage = [s for s in d.storage if s != "static"]
    return d


def split(parts, entries, unit_of):
    """``({unit: [render parts]}, [header c_ast declarations])``: ``parts`` placed by
    reachability from ``entries`` (``unit_of(entry)`` its unit, ``unit_of(None)`` the shared
    one's, ``unit_of(STATE)`` the state's); a shared unit only when some part is."""
    reach = owners(parts, entries)
    place, units, head = {}, {}, []
    for p in parts:
        if p.where != CODE or not p.provides:
            continue
        who = frozenset().union(*(reach.get(n, frozenset()) for n in p.provides))
        u = unit_of(next(iter(who))) if len(who) == 1 else unit_of(None)
        place.update(dict.fromkeys(p.provides, u))
    shared = unit_of(None)
    for p in parts:
        if p.where == EVERY:
            for u in {*place.values(), unit_of(STATE)}:
                units.setdefault(u, []).append(_body(p.node, False))
        elif p.where == STATE:
            node = p.node
            data = isinstance(node, c_ast.Decl) and node.name is not None
            units.setdefault(unit_of(STATE), []).append(
                _defined(node) if data else _body(node, True)
            )
            head += [_extern(node)] if data else list(p.header)
        elif p.where == HEADER:
            head.append(p.node)
        elif p.where == PROTO:
            u = place.get(next(iter(p.provides)), shared)
            if u == shared:
                head.append(_external(p.node))
            else:
                units.setdefault(u, []).append(p.node)
        else:
            u = place.get(next(iter(p.provides), None), shared)
            ext = u == shared
            node = _external(p.node) if ext and isinstance(p.node, c_ast.FuncDef) else p.node
            units.setdefault(u, []).append(_body(node, ext))
            if ext:
                head += (
                    [_external(p.node).decl]
                    if isinstance(p.node, c_ast.FuncDef)
                    else list(p.header)
                )
    return units, head
